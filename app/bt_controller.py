#!/usr/bin/env python3
"""
ControllerMode-MuOS - Bluetooth Controller Engine
Supports:
  1. Bluetooth Classic HID (L2CAP PSM 17 Control & PSM 19 Interrupt) with SDP Gamepad record
  2. Bluetooth Low Energy (BLE HOGP - HID over GATT Profile 0x1812) via BlueZ D-Bus (when available)
  3. Continuous PTY pairing agent with ANSI escape stripping to accurately display PIN codes and auto-authorize
"""
import fcntl
import os
import pty
import re
import shutil
import socket
import struct
import subprocess
import threading
import time

# Check for L2CAP support in Python socket module (Linux standard)
HAS_L2CAP = hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_L2CAP")

# Check for D-Bus support in Python
try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

BLUEZ_SERVICE = "org.bluez"
AGENT_PATH = "/org/muos/controllermode/agent"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MGR = "org.bluez.AgentManager1"
PROFILE_PATH = "/org/muos/controllermode/profile"
HID_PROFILE_UUID = "00001124-0000-1000-8000-00805f9b34fb"
OM_IFACE = "org.freedesktop.DBus.ObjectManager"
PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_MGR = "org.bluez.GattManager1"
ADV_MGR = "org.bluez.LEAdvertisingManager1"
SVC_IFACE = "org.bluez.GattService1"
CHR_IFACE = "org.bluez.GattCharacteristic1"
DSC_IFACE = "org.bluez.GattDescriptor1"
ADV_IFACE = "org.bluez.LEAdvertisement1"

def safe_cmd(cmd_list, timeout=5, **kwargs):
    """Safely runs a system command, capturing errors without throwing FileNotFoundError."""
    if not cmd_list:
        return None
    try:
        bin_path = shutil.which(cmd_list[0])
        if not bin_path:
            for prefix in ["/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/muos/bin"]:
                cand = os.path.join(prefix, cmd_list[0])
                if os.path.isfile(cand) and os.access(cand, os.X_OK):
                    bin_path = cand
                    break
        if not bin_path:
            return None
        return subprocess.run(
            [bin_path] + cmd_list[1:],
            stdout=kwargs.pop("stdout", subprocess.DEVNULL),
            stderr=kwargs.pop("stderr", subprocess.DEVNULL),
            timeout=timeout,
            **kwargs
        )
    except Exception:
        return None

# Hat map: (dx, dy) -> hat value (0..7, 8 = centered)
HAT_MAP = {
    (0, -1): 0,   # Up
    (1, -1): 1,   # Up-Right
    (1, 0):  2,   # Right
    (1, 1):  3,   # Down-Right
    (0, 1):  4,   # Down
    (-1, 1): 5,   # Down-Left
    (-1, 0): 6,   # Left
    (-1, -1): 7,  # Up-Left
}

XBOX_HAT = {
    (0, -1): 1, (1, -1): 2, (1, 0): 3, (1, 1): 4,
    (0, 1): 5, (-1, 1): 6, (-1, 0): 7, (-1, -1): 8
}

# Xbox Wireless Controller BLE Report Descriptor (Model 1914)
XBOX_REPORT_MAP = bytes.fromhex(
    "05010905a10185010901a10009300931150027ffff0000950275108102c00901a10009320935150027ffff0000950275"
    "108102c0050209c5150026ff039501750a810215002500750695018103050209c4150026ff039501750a810215002500"
    "75069501810305010939150125083500463b016614007504950181427504950115002500350045006500810305091901"
    "290f150025017501950f810215002500750195018103050c0ab200150025019501750181021500250075079501810305"
    "0f09218503a102099715002501750495019102150025007504950191030970150025647508950491020950660110550e"
    "150026ff0075089501910209a7150026ff0075089501910265005500097c150026ff00750895019102c0c0"
)

# Generic HID Gamepad Descriptor (9 bytes report)
GENERIC_REPORT_MAP = bytes([
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,
    0x85, 0x01,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x10,
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02,
    0x05, 0x01, 0x09, 0x39,
    0x15, 0x00, 0x25, 0x07, 0x35, 0x00, 0x46, 0x3B, 0x01, 0x65, 0x14,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x42,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x03, 0x65, 0x00,
    0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
    0x09, 0x33, 0x09, 0x34,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x02, 0x81, 0x02,
    0xC0,
])

if HAS_DBUS:
    class BleApplication(dbus.service.Object):
        def __init__(self, bus, path="/org/muos/controllermode/gatt"):
            self.path = path
            self.services = []
            super().__init__(bus, self.path)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        @dbus.service.method(OM_IFACE, out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            res = {}
            for s in self.services:
                res[s.get_path()] = s.get_properties()
                for c in s.chars:
                    res[c.get_path()] = c.get_properties()
                    for d in c.descs:
                        res[d.get_path()] = d.get_properties()
            return res

    class BleService(dbus.service.Object):
        def __init__(self, bus, app, index, uuid):
            self.path = f"{app.path}/service{index}"
            self.uuid = uuid
            self.chars = []
            self.bus = bus
            super().__init__(bus, self.path)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def get_properties(self):
            return {
                SVC_IFACE: {
                    "UUID": self.uuid,
                    "Primary": dbus.Boolean(True),
                    "Characteristics": dbus.Array([c.get_path() for c in self.chars], signature="o")
                }
            }

        @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != SVC_IFACE:
                return {}
            return self.get_properties()[SVC_IFACE]

        @dbus.service.method(PROP_IFACE, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.GetAll(interface).get(prop)

    class BleCharacteristic(dbus.service.Object):
        def __init__(self, service, index, uuid, flags, value=b""):
            self.path = f"{service.path}/char{index}"
            self.service = service
            self.uuid = uuid
            self.flags = flags
            self.descs = []
            self.value = bytes(value)
            self.notifying = False
            super().__init__(service.bus, self.path)
            service.chars.append(self)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def get_properties(self):
            props = {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": dbus.Array(self.flags, signature="s"),
                "Descriptors": dbus.Array([d.get_path() for d in self.descs], signature="o"),
                "Value": dbus.ByteArray(self.value)
            }
            if "notify" in self.flags:
                props["Notifying"] = dbus.Boolean(self.notifying)
            return {CHR_IFACE: props}

        @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != CHR_IFACE:
                return {}
            return self.get_properties()[CHR_IFACE]

        @dbus.service.method(PROP_IFACE, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.GetAll(interface).get(prop)

        @dbus.service.method(CHR_IFACE, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            off = int(options.get("offset", 0))
            return dbus.ByteArray(self.value[off:])

        @dbus.service.method(CHR_IFACE, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            self.value = bytes(value)

        @dbus.service.method(CHR_IFACE)
        def StartNotify(self):
            print(f"[Bluetooth BLE] StartNotify on {self.uuid}")
            self.notifying = True

        @dbus.service.method(CHR_IFACE)
        def StopNotify(self):
            print(f"[Bluetooth BLE] StopNotify on {self.uuid}")
            self.notifying = False

        @dbus.service.signal(PROP_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        def notify(self, value):
            self.value = bytes(value)
            try:
                self.PropertiesChanged(
                    CHR_IFACE,
                    dbus.Dictionary({"Value": dbus.ByteArray(self.value)}, signature="sv"),
                    []
                )
            except Exception:
                pass

    class BleDescriptor(dbus.service.Object):
        def __init__(self, chrc, index, uuid, flags, value):
            self.path = f"{chrc.path}/desc{index}"
            self.chrc = chrc
            self.uuid = uuid
            self.flags = flags
            self.value = bytes(value)
            super().__init__(chrc.service.bus, self.path)
            chrc.descs.append(self)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def get_properties(self):
            return {
                DSC_IFACE: {
                    "Characteristic": self.chrc.get_path(),
                    "UUID": self.uuid,
                    "Flags": dbus.Array(self.flags, signature="s"),
                    "Value": dbus.ByteArray(self.value)
                }
            }

        @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != DSC_IFACE:
                return {}
            return self.get_properties()[DSC_IFACE]

        @dbus.service.method(PROP_IFACE, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.GetAll(interface).get(prop)

        @dbus.service.method(DSC_IFACE, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            return dbus.ByteArray(self.value)

    class BleAdvertisement(dbus.service.Object):
        def __init__(self, bus, name="RG35XX-H (Xbox)"):
            self.path = "/org/muos/controllermode/adv0"
            self.name = name
            super().__init__(bus, self.path)

        def get_properties(self):
            return {
                ADV_IFACE: {
                    "Type": "peripheral",
                    "ServiceUUIDs": dbus.Array(["1812", "180f", "180a"], signature="s"),
                    "Appearance": dbus.UInt16(0x03C4),  # Gamepad
                    "LocalName": self.name,
                    "Discoverable": dbus.Boolean(True)
                }
            }

        @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != ADV_IFACE:
                return {}
            return self.get_properties()[ADV_IFACE]

        @dbus.service.method(PROP_IFACE, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.GetAll(interface).get(prop)

        @dbus.service.method(ADV_IFACE)
        def Release(self):
            pass

    class ClassicHidProfile(dbus.service.Object):
        """BlueZ Profile1 implementation for Classic Bluetooth HID."""
        def __init__(self, bus, controller):
            self.bus = bus
            self.controller = controller
            self.sock = None
            super().__init__(bus, PROFILE_PATH)

        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self):
            print("[Bluetooth Profile] Released")
            if self.sock:
                try: self.sock.close()
                except Exception: pass
                self.sock = None
            self.controller._profile_sock = None

        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Cancel(self):
            print("[Bluetooth Profile] Cancelled")

        @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
        def NewConnection(self, path, fd, properties):
            raw_fd = fd.take()
            print(f"[Bluetooth Profile] NewConnection from {path}, fd={raw_fd}")
            try:
                self.sock = socket.socket(fileno=raw_fd)
                self.sock.setblocking(False)
                self.controller._profile_sock = self.sock
                self.controller.is_connected = True
                self.controller._conn_time = time.monotonic()
                self.controller.host_name = f"Connected ({path.split('/')[-1]})"
                print(f"[Bluetooth Profile] Socket initialized: fd={raw_fd}")
            except Exception as e:
                print(f"[Bluetooth Profile] Socket init error: {e}")

        @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
        def RequestDisconnection(self, path):
            print(f"[Bluetooth Profile] Disconnection requested: {path}")
            if self.sock:
                try: self.sock.close()
                except Exception: pass
                self.sock = None
            self.controller._profile_sock = None
            self.controller.is_connected = False
            self.controller.host_name = "Waiting for host..."

    class DbusPairingAgent(dbus.service.Object):
        """Auto-approving pairing agent for BlueZ."""
        def __init__(self, bus, controller):
            super().__init__(bus, AGENT_PATH)
            self._bus = bus
            self.controller = controller

        @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method(AGENT_IFACE, in_signature="s", out_signature="s")
        def RequestPinCode(self, device):
            print(f"[BluetoothAgent] RequestPinCode for {device} -> 0000")
            return "0000"

        @dbus.service.method(AGENT_IFACE, in_signature="s", out_signature="u")
        def RequestPasskey(self, device):
            return dbus.UInt32(0)

        @dbus.service.method(AGENT_IFACE, in_signature="ss", out_signature="")
        def DisplayPinCode(self, device, pincode):
            self.controller.current_passkey = str(pincode)
            self.controller._passkey_time = time.monotonic()
            print(f"[BluetoothAgent] DisplayPinCode: {pincode}")

        @dbus.service.method(AGENT_IFACE, in_signature="uuu", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            self.controller.current_passkey = f"{passkey:06d}"
            self.controller._passkey_time = time.monotonic()
            print(f"[BluetoothAgent] DisplayPasskey: {passkey:06d}")

        @dbus.service.method(AGENT_IFACE, in_signature="su", out_signature="")
        def RequestConfirmation(self, device, passkey):
            self.controller.current_passkey = f"{passkey:06d}"
            self.controller._passkey_time = time.monotonic()
            print(f"[BluetoothAgent] RequestConfirmation passkey={passkey:06d} -> Auto-Accepting")
            self._trust(device)
            return

        @dbus.service.method(AGENT_IFACE, in_signature="s", out_signature="")
        def RequestAuthorization(self, device):
            print(f"[BluetoothAgent] RequestAuthorization for {device} -> Auto-Accepting")
            self._trust(device)
            return

        @dbus.service.method(AGENT_IFACE, in_signature="ss", out_signature="")
        def AuthorizeService(self, device, uuid):
            self._trust(device)
            return

        @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Cancel(self):
            pass

        def _trust(self, device_path):
            try:
                props = dbus.Interface(
                    self._bus.get_object("org.bluez", device_path),
                    "org.freedesktop.DBus.Properties"
                )
                props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True, variant_level=1))
            except Exception:
                pass


class BluetoothController:
    def __init__(self, mode: str = "bt_ble"):
        self.mode = mode
        self.is_connected = False
        self.is_active = False
        self.host_name = "Waiting for host..."
        self.current_passkey = None
        self._passkey_time = 0.0
        self._conn_time = 0.0
        
        self._stop_event = threading.Event()
        self._monitor_thread = None

        # Classic L2CAP sockets
        self._ctrl_sock = None
        self._int_sock = None
        self._ctrl_client = None
        self._int_client = None

        # BLE D-Bus & Classic Profile objects
        self._bus = None
        self._agent = None
        self._ble_app = None
        self._ble_adv = None
        self._ble_report_chr = None
        self._classic_profile = None
        self._profile_sock = None
        self._dbus_loop = None

    def check_stack(self) -> bool:
        """Ensures bluetoothd is running on muOS, sets Gamepad Class of Device, SSP mode, and SDP profile."""
        try:
            res = safe_cmd(["hciconfig", "hci0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res is None or res.returncode != 0:
                print("[Bluetooth] hci0 not ready, starting muOS bluetooth stack...")
                if os.path.exists("/opt/muos/script/init/S75bluetooth.sh"):
                    safe_cmd(["/opt/muos/script/init/S75bluetooth.sh", "start"], timeout=5)
                    time.sleep(1.5)

            # Power on adapter
            safe_cmd(["bluetoothctl", "power", "on"])
            time.sleep(0.3)

            # Set Class of Device to Gamepad / Joystick (0x002508)
            safe_cmd(["hciconfig", "hci0", "up"])
            safe_cmd(["hciconfig", "hci0", "class", "0x002508"])

            # Enable Secure Simple Pairing (SSP) so Windows/Linux can pair with Just Works (no PIN)
            safe_cmd(["hciconfig", "hci0", "sspmode", "1"])

            # Enable Page Scan and Inquiry Scan so device is connectable and discoverable
            safe_cmd(["hciconfig", "hci0", "piscan"])

            # Register Classic Bluetooth HID profile in BlueZ SDP server if sdptool is available
            safe_cmd(["sdptool", "add", "HID"])
            safe_cmd(["sdptool", "add", "GG"])
        except Exception as e:
            print(f"[Bluetooth] check_stack exception: {e}")
        return True

    def _start_l2cap_server(self):
        """Listens on L2CAP PSM 17 (Control) and PSM 19 (Interrupt) for Classic Bluetooth Gamepad hosts."""
        if not HAS_L2CAP:
            print("[Bluetooth L2CAP] Note: AF_BLUETOOTH not available on this platform.")
            return

        def _l2cap_worker():
            HID_CONTROL_PSM = 17
            HID_INTERRUPT_PSM = 19

            try:
                self._ctrl_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
                self._ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._ctrl_sock.bind(("", HID_CONTROL_PSM))
                self._ctrl_sock.listen(1)

                self._int_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
                self._int_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._int_sock.bind(("", HID_INTERRUPT_PSM))
                self._int_sock.listen(1)
                print("[Bluetooth L2CAP] Server listening on PSM 17 (Control) & PSM 19 (Interrupt)...")
            except Exception as e:
                print(f"[Bluetooth L2CAP] Sockets bind note: {e}")
                return

            while not self._stop_event.is_set():
                try:
                    self._ctrl_sock.settimeout(1.0)
                    try:
                        c_client, c_addr = self._ctrl_sock.accept()
                        c_client.setblocking(False)
                        self._ctrl_client = c_client
                        print(f"[Bluetooth L2CAP] Control channel connected from {c_addr}")
                    except (socket.timeout, BlockingIOError, OSError):
                        continue

                    self._int_sock.settimeout(4.0)
                    try:
                        i_client, i_addr = self._int_sock.accept()
                        i_client.setblocking(False)
                        self._int_client = i_client
                        print(f"[Bluetooth L2CAP] Interrupt channel connected from {i_addr}")
                        self.is_connected = True
                        self._conn_time = time.monotonic()
                        self.host_name = f"Connected ({i_addr[0]})"
                    except Exception as e:
                        print(f"[Bluetooth L2CAP] Interrupt channel accept note: {e}")
                        if self._ctrl_client:
                            try: self._ctrl_client.close()
                            except Exception: pass
                            self._ctrl_client = None
                        continue

                    # Connection loop: handle handshakes on control socket
                    while not self._stop_event.is_set() and self.is_connected:
                        try:
                            data = self._ctrl_client.recv(64)
                            if not data:
                                break
                            # If host sends HID handshake request (0x00), reply 0x00 (SUCCESS)
                            if len(data) > 0 and (data[0] & 0xF0) == 0x00:
                                self._ctrl_client.send(b"\x00")
                        except (BlockingIOError, socket.timeout):
                            pass
                        except Exception:
                            break
                        time.sleep(0.04)

                    print("[Bluetooth L2CAP] Host disconnected.")
                    self.is_connected = False
                    if self._ctrl_client:
                        try: self._ctrl_client.close()
                        except Exception: pass
                        self._ctrl_client = None
                    if self._int_client:
                        try: self._int_client.close()
                        except Exception: pass
                        self._int_client = None

                except Exception:
                    if not self._stop_event.is_set():
                        time.sleep(0.5)

        threading.Thread(target=_l2cap_worker, daemon=True).start()

    def _setup_agent(self, name: str):
        """Registers the auto-pairing agent via bluetoothctl PTY and D-Bus."""
        # 1. PTY-based bluetoothctl automation with ANSI stripping and persistent agent
        def _cli_agent():
            master_fd, slave_fd = None, None
            p = None
            try:
                master_fd, slave_fd = pty.openpty()
                fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
                fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

                bin_btctl = shutil.which("bluetoothctl") or "/usr/bin/bluetoothctl"
                p = subprocess.Popen(
                    [bin_btctl],
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True
                )
                os.close(slave_fd)
                slave_fd = None

                def _send(cmd: str):
                    try:
                        os.write(master_fd, cmd.encode("utf-8") + b"\n")
                    except Exception:
                        pass

                time.sleep(0.2)
                _send("power on")
                time.sleep(0.1)
                _send("agent NoInputNoOutput")
                time.sleep(0.1)
                _send("default-agent")
                time.sleep(0.1)
                _send("discoverable on")
                time.sleep(0.1)
                _send("pairable on")
                time.sleep(0.1)

                buf = b""
                while not self._stop_event.is_set():
                    try:
                        chunk = os.read(master_fd, 1024)
                        if chunk:
                            buf += chunk
                            raw_text = buf.decode("utf-8", errors="ignore")
                            # Strip all ANSI terminal color and control codes
                            clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw_text)

                            # Parse 4 to 6 digit numeric passkey or PIN
                            m = re.search(r'(?:passkey|code|pin)\s*:?\s*(\d{4,6})', clean, re.I)
                            if not m:
                                m = re.search(r'\b(\d{6})\b', clean)
                            if m:
                                self.current_passkey = m.group(1)
                                self._passkey_time = time.monotonic()
                                print(f"[Bluetooth] Pairing passkey detected: {self.current_passkey}")

                            # Track connection events from bluetoothctl real-time stream
                            m_conn = re.search(r'Device\s+([0-9A-Fa-f:]{17})\s+Connected:\s+(yes|no)', clean)
                            if m_conn:
                                if m_conn.group(2).lower() == "yes":
                                    self.is_connected = True
                                    self._conn_time = time.monotonic()
                                    self.host_name = f"Connected ({m_conn.group(1)})"
                                    print(f"[Bluetooth] Host connected: {m_conn.group(1)}")
                                else:
                                    if self._int_client is None:
                                        self.is_connected = False
                                        self._conn_time = 0.0
                                        self.host_name = "Waiting for host..."
                                        print("[Bluetooth] Host disconnected")

                            m_name = re.search(r'Device\s+[0-9A-Fa-f:]{17}\s+(?:Name|Alias):\s+(.+)', clean)
                            if m_name:
                                self.host_name = m_name.group(1).strip()

                            # Auto-confirm passkey / pairing / service authorization requests
                            clean_lower = clean.lower()
                            if any(k in clean_lower for k in ["yes/no", "(yes/no)", "confirm", "authorize", "accept"]):
                                _send("yes")
                                print("[Bluetooth] Responded: yes")
                                buf = b""
                            elif "pin" in clean_lower and any(k in clean_lower for k in ["enter", "request", "?"]):
                                _send("0000")
                                print("[Bluetooth] Responded PIN: 0000")
                                buf = b""
                            elif "request passkey" in clean_lower:
                                _send("0")
                                print("[Bluetooth] Responded Passkey: 0")
                                buf = b""
                    except (BlockingIOError, InterruptedError):
                        pass
                    time.sleep(0.04)

                if p:
                    p.terminate()
            except Exception as e:
                print(f"[Bluetooth] CLI PTY agent error: {e}")
            finally:
                if slave_fd is not None:
                    try: os.close(slave_fd)
                    except Exception: pass
                if master_fd is not None:
                    try: os.close(master_fd)
                    except Exception: pass

        threading.Thread(target=_cli_agent, daemon=True).start()

        # 2. D-Bus GATT & Pairing Agent if available
        if HAS_DBUS:
            try:
                dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
                self._bus = dbus.SystemBus()

                # Register D-Bus pairing agent with NoInputNoOutput in its own try/except
                try:
                    self._agent = DbusPairingAgent(self._bus, self)
                    manager = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"), AGENT_MGR)
                    manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
                    manager.RequestDefaultAgent(AGENT_PATH)
                except Exception as e:
                    print(f"[Bluetooth] D-Bus Agent note: {e}")

                # Register Classic Bluetooth HID profile via ProfileManager1
                try:
                    self._classic_profile = ClassicHidProfile(self._bus, self)
                    pm = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"), "org.bluez.ProfileManager1")
                    pm.RegisterProfile(
                        PROFILE_PATH,
                        HID_PROFILE_UUID,
                        {
                            "Name": dbus.String(name),
                            "Role": dbus.String("server"),
                            "RequireAuthentication": dbus.Boolean(False),
                            "RequireAuthorization": dbus.Boolean(False),
                            "AutoConnect": dbus.Boolean(True),
                        }
                    )
                    print("[Bluetooth] Classic HID Profile registered with BlueZ")
                except Exception as e:
                    print(f"[Bluetooth] Classic Profile registration note: {e}")

                # Find Bluetooth adapter and register GATT Application
                try:
                    om = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/"), OM_IFACE)
                    adapter_path = next((p for p, ifs in om.GetManagedObjects().items() if GATT_MGR in ifs), None)
                    if not adapter_path:
                        adapter_path = "/org/bluez/hci0"

                    adapter = self._bus.get_object(BLUEZ_SERVICE, adapter_path)
                    props = dbus.Interface(adapter, PROP_IFACE)
                    props.Set("org.bluez.Adapter1", "Powered", True)
                    props.Set("org.bluez.Adapter1", "Pairable", True)
                    props.Set("org.bluez.Adapter1", "Discoverable", True)
                    props.Set("org.bluez.Adapter1", "Alias", name)

                    app = BleApplication(self._bus)

                    # Service 0: HID Service (1812)
                    hid = BleService(self._bus, app, 0, "1812")
                    app.services.append(hid)
                    BleCharacteristic(hid, 0, "2a4a", ["read"], bytes([0x11, 0x01, 0x00, 0x02]))
                    BleCharacteristic(hid, 1, "2a4b", ["read"], XBOX_REPORT_MAP if self.mode == "bt_ble" else GENERIC_REPORT_MAP)
                    BleCharacteristic(hid, 2, "2a4c", ["write-without-response"], b"\x00")
                    BleCharacteristic(hid, 3, "2a4e", ["read", "write-without-response"], b"\x01")
                    
                    self._ble_report_chr = BleCharacteristic(hid, 4, "2a4d", ["read", "notify"], bytes(16 if self.mode == "bt_ble" else 9))
                    BleDescriptor(self._ble_report_chr, 0, "2908", ["read"], bytes([0x01, 0x01]))

                    # Service 1: Battery Service (180f)
                    bat = BleService(self._bus, app, 1, "180f")
                    app.services.append(bat)
                    BleCharacteristic(bat, 0, "2a19", ["read", "notify"], bytes([100]))

                    # Service 2: Device Information Service (180a)
                    devinfo = BleService(self._bus, app, 2, "180a")
                    app.services.append(devinfo)
                    mfg = b"Microsoft Corporation" if self.mode == "bt_ble" else b"Nintendo"
                    mdl = b"Xbox Wireless Controller" if self.mode == "bt_ble" else b"Pro Controller"
                    pnp = bytes([0x01, 0x5e, 0x04, 0xfd, 0x02, 0x00, 0x01]) if self.mode == "bt_ble" else bytes([0x01, 0x7e, 0x05, 0x09, 0x20, 0x00, 0x01])
                    BleCharacteristic(devinfo, 0, "2a29", ["read"], mfg)
                    BleCharacteristic(devinfo, 1, "2a24", ["read"], mdl)
                    BleCharacteristic(devinfo, 2, "2a50", ["read"], pnp)

                    gatt = dbus.Interface(adapter, GATT_MGR)
                    gatt.RegisterApplication(app.path, {}, reply_handler=lambda: print("[Bluetooth BLE] GATT HID service registered"), error_handler=lambda e: print(f"[Bluetooth BLE] GATT reg failed: {e}"))
                    self._ble_app = app

                    self._ble_adv = BleAdvertisement(self._bus, name=name)
                    advm = dbus.Interface(adapter, ADV_MGR)
                    advm.RegisterAdvertisement(self._ble_adv.path, {}, reply_handler=lambda: print(f"[Bluetooth BLE] Advertising as '{name}'"), error_handler=lambda e: print(f"[Bluetooth BLE] Adv failed: {e}"))

                    def _glib_worker():
                        self._dbus_loop = GLib.MainLoop()
                        self._dbus_loop.run()

                    threading.Thread(target=_glib_worker, daemon=True).start()
                except Exception as e:
                    print(f"[Bluetooth] D-Bus GATT note: {e}")
            except Exception as e:
                print(f"[Bluetooth] D-Bus SystemBus note: {e}")

    def start(self) -> bool:
        """Starts Bluetooth Gamepad service."""
        try:
            self.check_stack()
            self.is_active = True
            self._stop_event.clear()
            self.current_passkey = None
            self._passkey_time = 0.0

            name = "RG35XX-H (Xbox)" if self.mode == "bt_ble" else "Pro Controller"
            safe_cmd(["bluetoothctl", "system-alias", name])

            self._start_l2cap_server()
            self._setup_agent(name)

            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
            print(f"[Bluetooth] Advertising as '{name}'...")
            return True
        except Exception as e:
            print(f"[Bluetooth] start failed: {e}")
            return False

    def _monitor_loop(self):
        """Monitors passkey expiry timer and connection state."""
        while not self._stop_event.is_set():
            now = time.monotonic()
            if self.current_passkey:
                # If connected, retain code on screen for 5 seconds after connection
                if self.is_connected and (now - self._conn_time > 5.0):
                    self.current_passkey = None
                # Or keep on screen for up to 25 seconds during pairing
                elif now - self._passkey_time > 25.0:
                    self.current_passkey = None
            time.sleep(0.5)

    def send_report(self, state) -> bool:
        """Sends GamepadState over Bluetooth link (L2CAP and/or BLE GATT)."""
        if not self.is_active:
            return False

        try:
            # Map buttons to 16-bit bitfield
            buttons = 0
            if state.a: buttons |= (1 << 0)
            if state.b: buttons |= (1 << 1)
            if state.x: buttons |= (1 << 2)
            if state.y: buttons |= (1 << 3)
            if state.l1: buttons |= (1 << 4)
            if state.r1: buttons |= (1 << 5)
            if state.l2: buttons |= (1 << 6)
            if state.r2: buttons |= (1 << 7)
            if state.select: buttons |= (1 << 8)
            if state.start:  buttons |= (1 << 9)
            if state.l3:     buttons |= (1 << 10)
            if state.r3:     buttons |= (1 << 11)
            if state.menu:   buttons |= (1 << 12)

            # D-pad hat
            dx = (1 if state.dpad_right else 0) - (1 if state.dpad_left else 0)
            dy = (1 if state.dpad_down else 0) - (1 if state.dpad_up else 0)
            hat = HAT_MAP.get((dx, dy), 8)

            # Analog sticks (0..255, center 128)
            lx = max(0, min(255, (state.lx + 32768) >> 8))
            ly = max(0, min(255, (state.ly + 32768) >> 8))
            rx = max(0, min(255, (state.rx + 32768) >> 8))
            ry = max(0, min(255, (state.ry + 32768) >> 8))

            # Triggers (0..255)
            lt = state.lt_val if state.lt_val > 0 else (255 if state.l2 else 0)
            rt = state.rt_val if state.rt_val > 0 else (255 if state.r2 else 0)

            # 9-byte standard HID report
            report_data = struct.pack("<HBBBBBBB", buttons, hat, lx, ly, rx, ry, lt, rt)

            sent = False

            # 1. Send over BlueZ Classic Profile socket if connected
            if self._profile_sock is not None:
                try:
                    # 0xA1 header = HID Input Report, 0x01 = Report ID 1
                    self._profile_sock.send(b"\xa1\x01" + report_data)
                    sent = True
                except (BlockingIOError, OSError):
                    pass
                except Exception:
                    self._profile_sock = None

            # 2. Send over raw L2CAP PSM 19 (HID Interrupt) if connected
            if self._int_client is not None:
                try:
                    # 0xA1 header = HID Input Report (Report ID 1)
                    self._int_client.send(b"\xa1\x01" + report_data)
                    sent = True
                except (BlockingIOError, OSError):
                    pass
                except Exception:
                    self._int_client = None

            # 3. Send over BLE GATT characteristic if active
            if self._ble_report_chr is not None:
                try:
                    if self.mode == "bt_ble":
                        # Xbox Wireless Controller BLE report format (16 bytes)
                        stick = lambda v: max(0, min(65535, v + 32768))
                        trig = lambda v: max(0, min(1023, v * 1023 // 255))
                        xb_hat = XBOX_HAT.get((dx, dy), 0)

                        # Dual-mapped Xbox button bitfield for max driver compatibility
                        xb_btn = 0
                        if state.a: xb_btn |= (1 << 0)
                        if state.b: xb_btn |= (1 << 1)
                        if state.x: xb_btn |= (1 << 2) | (1 << 3)
                        if state.y: xb_btn |= (1 << 4)
                        if state.l1: xb_btn |= (1 << 5) | (1 << 6)
                        if state.r1: xb_btn |= (1 << 7)
                        if state.select: xb_btn |= (1 << 8) | (1 << 10)
                        if state.start:  xb_btn |= (1 << 9)
                        if state.l3:     xb_btn |= (1 << 11) | (1 << 13)
                        if state.r3:     xb_btn |= (1 << 12) | (1 << 14)

                        xb_report = struct.pack("<HHHHHHBH",
                            stick(state.lx), stick(state.ly), stick(state.rx), stick(state.ry),
                            trig(lt), trig(rt), xb_hat, xb_btn & 0x7FFF) + b"\x00"
                        self._ble_report_chr.notify(xb_report)
                    else:
                        self._ble_report_chr.notify(report_data)
                    sent = True
                except Exception:
                    pass

            return sent
        except Exception:
            return False

    def cleanup(self):
        """Cleanly shuts down Bluetooth gamepad sockets and advertisement."""
        self.is_active = False
        self._stop_event.set()
        self.current_passkey = None

        # Close Profile socket
        if self._profile_sock is not None:
            try: self._profile_sock.close()
            except Exception: pass
            self._profile_sock = None

        # Close L2CAP sockets
        for s in [self._ctrl_client, self._int_client, self._ctrl_sock, self._int_sock]:
            if s is not None:
                try: s.close()
                except Exception: pass
        self._ctrl_client = None
        self._int_client = None
        self._ctrl_sock = None
        self._int_sock = None

        # Clean D-Bus objects
        if self._bus and self._classic_profile:
            try:
                pm = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"), "org.bluez.ProfileManager1")
                pm.UnregisterProfile(PROFILE_PATH)
            except Exception:
                pass
            self._classic_profile = None

        if self._dbus_loop:
            try: self._dbus_loop.quit()
            except Exception: pass
            self._dbus_loop = None

        if self._bus and self._agent:
            try:
                manager = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"), AGENT_MGR)
                manager.UnregisterAgent(AGENT_PATH)
            except Exception:
                pass

        safe_cmd(["bluetoothctl", "discoverable", "off"])
        print("[Bluetooth] Bluetooth Controller stopped.")
