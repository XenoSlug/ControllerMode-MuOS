#!/usr/bin/env python3
"""
ControllerMode-MuOS - Bluetooth Controller Engine
Supports:
  1. Bluetooth Classic HID (L2CAP PSM 17 Control & PSM 19 Interrupt) with SDP Gamepad record
  2. Bluetooth Low Energy (BLE HOGP - HID over GATT Profile 0x1812) via BlueZ D-Bus (when available)
  3. Auto-approving pairing agent (NoInputNoOutput) to enforce Just Works pairing on PC / Steam / Windows / Linux
"""
import fcntl
import os
import pty
import re
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
OM_IFACE = "org.freedesktop.DBus.ObjectManager"
PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_MGR = "org.bluez.GattManager1"
ADV_MGR = "org.bluez.LEAdvertisingManager1"
SVC_IFACE = "org.bluez.GattService1"
CHR_IFACE = "org.bluez.GattCharacteristic1"
DSC_IFACE = "org.bluez.GattDescriptor1"
ADV_IFACE = "org.bluez.LEAdvertisement1"

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
                    "Primary": True,
                    "Characteristics": dbus.Array([c.get_path() for c in self.chars], signature="o")
                }
            }

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
            return {
                CHR_IFACE: {
                    "Service": self.service.get_path(),
                    "UUID": self.uuid,
                    "Flags": self.flags,
                    "Descriptors": dbus.Array([d.get_path() for d in self.descs], signature="o")
                }
            }

        @dbus.service.method(CHR_IFACE, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            off = int(options.get("offset", 0))
            return dbus.Array(self.value[off:], signature="y")

        @dbus.service.method(CHR_IFACE, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            self.value = bytes(value)

        @dbus.service.method(CHR_IFACE)
        def StartNotify(self):
            self.notifying = True

        @dbus.service.method(CHR_IFACE)
        def StopNotify(self):
            self.notifying = False

        @dbus.service.signal(PROP_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        def notify(self, value):
            self.value = bytes(value)
            self.PropertiesChanged(CHR_IFACE, {"Value": dbus.Array(self.value, signature="y")}, [])

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
                    "Flags": self.flags
                }
            }

        @dbus.service.method(DSC_IFACE, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            return dbus.Array(self.value, signature="y")

    class BleAdvertisement(dbus.service.Object):
        def __init__(self, bus, name="RG35XX-H (Xbox)"):
            self.path = "/org/muos/controllermode/adv0"
            self.name = name
            super().__init__(bus, self.path)

        @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return {
                "Type": "peripheral",
                "ServiceUUIDs": dbus.Array(["1812", "180f"], signature="s"),
                "Appearance": dbus.UInt16(0x03C4),  # Gamepad
                "LocalName": self.name,
                "Discoverable": True,
                "MinInterval": dbus.UInt32(30),
                "MaxInterval": dbus.UInt32(60)
            }

        @dbus.service.method(ADV_IFACE)
        def Release(self):
            pass

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

        # BLE D-Bus objects
        self._bus = None
        self._agent = None
        self._ble_app = None
        self._ble_adv = None
        self._ble_report_chr = None
        self._dbus_loop = None

    def check_stack(self) -> bool:
        """Ensures bluetoothd is running on muOS, sets Gamepad Class of Device, SSP mode, and SDP profile."""
        res = subprocess.run(["hciconfig", "hci0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print("[Bluetooth] hci0 not ready, starting muOS bluetooth stack...")
            if os.path.exists("/opt/muos/script/init/S75bluetooth.sh"):
                subprocess.run(["/opt/muos/script/init/S75bluetooth.sh", "start"])
                time.sleep(1.5)

        # Power on adapter
        subprocess.run(["bluetoothctl", "power", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)

        # Set Class of Device to Gamepad / Joystick (0x002508)
        subprocess.run(["hciconfig", "hci0", "up"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["hciconfig", "hci0", "class", "0x002508"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Enable Secure Simple Pairing (SSP) so Windows/Linux can pair with Just Works (no PIN)
        subprocess.run(["hciconfig", "hci0", "sspmode", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Enable Page Scan and Inquiry Scan so device is connectable and discoverable
        subprocess.run(["hciconfig", "hci0", "piscan"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Register Classic Bluetooth HID profile in BlueZ SDP server
        subprocess.run(["sdptool", "add", "HID"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sdptool", "add", "GG"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                print(f"[Bluetooth L2CAP] Sockets bind error: {e}")
                return

            while not self._stop_event.is_set():
                try:
                    self._ctrl_sock.settimeout(1.0)
                    try:
                        c_client, c_addr = self._ctrl_sock.accept()
                        c_client.setblocking(False)
                        self._ctrl_client = c_client
                        print(f"[Bluetooth L2CAP] Control channel connected from {c_addr}")
                    except (socket.timeout, BlockingIOError):
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
                        print(f"[Bluetooth L2CAP] Interrupt channel accept timed out or failed: {e}")
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

                except Exception as e:
                    if not self._stop_event.is_set():
                        time.sleep(0.5)

        threading.Thread(target=_l2cap_worker, daemon=True).start()

    def _setup_agent(self, name: str):
        """Registers the auto-pairing agent via bluetoothctl PTY and D-Bus."""
        # 1. PTY-based bluetoothctl automation with NoInputNoOutput capability
        def _cli_agent():
            master_fd, slave_fd = None, None
            p = None
            try:
                master_fd, slave_fd = pty.openpty()
                fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
                fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

                p = subprocess.Popen(
                    ["bluetoothctl"],
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

                _send("power on")
                # NoInputNoOutput tells host we are a pure gamepad with no display/keys (forces Just Works)
                _send("agent NoInputNoOutput")
                _send("default-agent")
                _send("discoverable on")
                _send("pairable on")

                buf = b""
                while not self._stop_event.is_set():
                    try:
                        chunk = os.read(master_fd, 1024)
                        if chunk:
                            buf += chunk
                            text = buf.decode("utf-8", errors="ignore")

                            # Parse 6-digit numeric passkey or PIN if presented by host
                            m = re.search(r'(?:passkey|code|pin)\s*:?\s*(\d{4,6})', text, re.I)
                            if not m:
                                m = re.search(r'\b(\d{6})\b', text)
                            if m:
                                self.current_passkey = m.group(1)
                                self._passkey_time = time.monotonic()

                            # Auto-confirm passkey / pairing / authorization requests
                            if any(k in text.lower() for k in ["(yes/no)", "confirm passkey", "authorize service", "authorize connection", "accept"]):
                                _send("yes")
                                buf = b""
                            elif "pin" in text.lower() and "?" in text:
                                _send("0000")
                                buf = b""
                    except (BlockingIOError, InterruptedError):
                        pass
                    time.sleep(0.05)

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

                # Register D-Bus pairing agent with NoInputNoOutput
                self._agent = DbusPairingAgent(self._bus, self)
                manager = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"), AGENT_MGR)
                manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
                manager.RequestDefaultAgent(AGENT_PATH)

                # Find Bluetooth adapter
                om = dbus.Interface(self._bus.get_object(BLUEZ_SERVICE, "/"), OM_IFACE)
                adapter_path = next((p for p, ifs in om.GetManagedObjects().items() if GATT_MGR in ifs), None)

                if adapter_path:
                    adapter = self._bus.get_object(BLUEZ_SERVICE, adapter_path)
                    props = dbus.Interface(adapter, PROP_IFACE)
                    props.Set("org.bluez.Adapter1", "Powered", True)
                    props.Set("org.bluez.Adapter1", "Pairable", True)
                    props.Set("org.bluez.Adapter1", "Alias", name)

                    # Build BLE GATT Application (HID Service 0x1812 & Battery 0x180f)
                    app = BleApplication(self._bus)
                    hid = BleService(self._bus, app, 0, "1812")
                    app.services.append(hid)
                    BleCharacteristic(hid, 0, "2a4a", ["read"], bytes([0x11, 0x01, 0x00, 0x02]))
                    BleCharacteristic(hid, 1, "2a4b", ["encrypt-read"], XBOX_REPORT_MAP if self.mode == "bt_ble" else GENERIC_REPORT_MAP)
                    BleCharacteristic(hid, 2, "2a4c", ["write-without-response"], b"\x00")
                    BleCharacteristic(hid, 3, "2a4e", ["read", "write-without-response"], b"\x01")
                    
                    self._ble_report_chr = BleCharacteristic(hid, 4, "2a4d", ["encrypt-read", "notify"], bytes(16 if self.mode == "bt_ble" else 9))
                    BleDescriptor(self._ble_report_chr, 0, "2908", ["read"], bytes([0x01, 0x01]))

                    bat = BleService(self._bus, app, 1, "180f")
                    app.services.append(bat)
                    BleCharacteristic(bat, 0, "2a19", ["read", "notify"], bytes([100]))

                    gatt = dbus.Interface(adapter, GATT_MGR)
                    gatt.RegisterApplication(app.path, {}, reply_handler=lambda: print("[Bluetooth BLE] GATT HID service registered"), error_handler=lambda e: print(f"[Bluetooth BLE] GATT reg failed: {e}"))
                    self._ble_app = app

                    # Register BLE Advertisement
                    self._ble_adv = BleAdvertisement(self._bus, name=name)
                    advm = dbus.Interface(adapter, ADV_MGR)
                    advm.RegisterAdvertisement(self._ble_adv.path, {}, reply_handler=lambda: print(f"[Bluetooth BLE] Advertising as '{name}'"), error_handler=lambda e: print(f"[Bluetooth BLE] Adv failed: {e}"))

                    # Run GLib mainloop in background thread for D-Bus signals
                    def _glib_worker():
                        self._dbus_loop = GLib.MainLoop()
                        self._dbus_loop.run()

                    threading.Thread(target=_glib_worker, daemon=True).start()
                    print("[Bluetooth] D-Bus BLE Gamepad and pairing agent registered.")
            except Exception as e:
                print(f"[Bluetooth] Note: D-Bus setup skipped ({e}). Relying on Classic L2CAP + bluetoothctl.")

    def start(self) -> bool:
        """Starts Bluetooth Gamepad service."""
        self.check_stack()
        self.is_active = True
        self._stop_event.clear()
        self.current_passkey = None
        self._passkey_time = 0.0

        name = "RG35XX-H (Xbox)" if self.mode == "bt_ble" else "Pro Controller"
        subprocess.run(["bluetoothctl", "system-alias", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._start_l2cap_server()
        self._setup_agent(name)

        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        print(f"[Bluetooth] Advertising as '{name}'...")
        return True

    def _monitor_loop(self):
        """Monitors connection state and connected host name."""
        while not self._stop_event.is_set():
            try:
                # Check connected devices via bluetoothctl
                out = subprocess.check_output(["bluetoothctl", "devices", "Connected"], stderr=subprocess.DEVNULL).decode()
                lines = [l.strip() for l in out.splitlines() if l.strip().startswith("Device")]
                if lines:
                    self.is_connected = True
                    parts = lines[0].split(maxsplit=2)
                    self.host_name = parts[2] if len(parts) >= 3 else parts[1]
                    if not self._conn_time:
                        self._conn_time = time.monotonic()
                elif self._int_client is None:
                    self.is_connected = False
                    self._conn_time = 0.0
                    if not self.current_passkey:
                        self.host_name = "Waiting for host..."

                # Retain current_passkey on screen for at least 15s so user can read it, or until 5s after connection
                now = time.monotonic()
                if self.current_passkey:
                    if self.is_connected and (now - self._conn_time > 5.0):
                        self.current_passkey = None
                    elif now - self._passkey_time > 18.0:
                        self.current_passkey = None

            except Exception:
                pass
            time.sleep(0.8)

    def send_report(self, state) -> bool:
        """Sends GamepadState over Bluetooth link (L2CAP and/or BLE GATT)."""
        if not self.is_active:
            return False

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
        # 1. Send over L2CAP PSM 19 (HID Interrupt)
        if self._int_client is not None:
            try:
                # 0xA1 header = HID Input Report (Report ID 1)
                self._int_client.send(b"\xa1" + report_data)
                sent = True
            except (BlockingIOError, OSError):
                pass

        # 2. Send over BLE GATT characteristic if active
        if self._ble_report_chr is not None:
            try:
                if self.mode == "bt_ble":
                    # Xbox Wireless Controller BLE report format (16 bytes)
                    stick = lambda v: max(0, min(65535, v + 32768))
                    trig = lambda v: max(0, min(1023, v * 1023 // 255))
                    xb_hat = XBOX_HAT.get((dx, dy), 0)
                    xb_report = struct.pack("<HHHHHHBH",
                        stick(state.lx), stick(state.ly), stick(state.rx), stick(state.ry),
                        trig(lt), trig(rt), xb_hat, buttons & 0x7FFF) + b"\x00"
                    self._ble_report_chr.notify(xb_report)
                else:
                    self._ble_report_chr.notify(report_data)
                sent = True
            except Exception:
                pass

        return sent or self.is_connected

    def cleanup(self):
        """Cleanly shuts down Bluetooth gamepad sockets and advertisement."""
        self.is_active = False
        self._stop_event.set()
        self.current_passkey = None

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

        subprocess.run(["bluetoothctl", "discoverable", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[Bluetooth] Bluetooth Controller stopped.")
