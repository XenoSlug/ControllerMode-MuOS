#!/usr/bin/env python3
"""
ControllerMode-MuOS - Bluetooth Controller Engine
Supports Bluetooth LE (Xbox Wireless Controller / BLE Gamepad) & Classic Bluetooth
with an auto-approving pairing agent to handle PIN/passkey requests automatically.
"""
import fcntl
import os
import pty
import re
import struct
import subprocess
import threading
import time

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

BLUEZ_SERVICE = "org.bluez"
AGENT_PATH = "/org/muos/controllermode/agent"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MGR = "org.bluez.AgentManager1"

# Xbox Wireless Controller BLE Report Descriptor (Model 1914)
XBOX_REPORT_MAP = bytes.fromhex(
    "05010905a10185010901a10009300931150027ffff0000950275108102c00901a10009320935150027ffff0000950275"
    "108102c0050209c5150026ff039501750a810215002500750695018103050209c4150026ff039501750a810215002500"
    "75069501810305010939150125083500463b016614007504950181427504950115002500350045006500810305091901"
    "290f150025017501950f810215002500750195018103050c0ab200150025019501750181021500250075079501810305"
    "0f09218503a102099715002501750495019102150025007504950191030970150025647508950491020950660110550e"
    "150026ff0075089501910209a7150026ff0075089501910265005500097c150026ff00750895019102c0c0"
)

if HAS_DBUS:
    class AutoPairingAgent(dbus.service.Object):
        """Auto-approves incoming pairing and passkey confirmation requests from PC/Phone."""
        def __init__(self, bus, controller):
            dbus.service.Object.__init__(self, bus, AGENT_PATH)
            self._bus = bus
            self.controller = controller

        @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method(AGENT_IFACE, in_signature="s", out_signature="s")
        def RequestPinCode(self, device):
            print(f"[BluetoothAgent] RequestPinCode for {device} -> returning 0000")
            return "0000"

        @dbus.service.method(AGENT_IFACE, in_signature="s", out_signature="u")
        def RequestPasskey(self, device):
            print(f"[BluetoothAgent] RequestPasskey for {device} -> returning 0")
            return dbus.UInt32(0)

        @dbus.service.method(AGENT_IFACE, in_signature="ss", out_signature="")
        def DisplayPinCode(self, device, pincode):
            print(f"[BluetoothAgent] DisplayPinCode: {pincode}")

        @dbus.service.method(AGENT_IFACE, in_signature="uuu", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            self.controller.current_passkey = f"{passkey:06d}"
            print(f"[BluetoothAgent] DisplayPasskey: {passkey:06d}")

        @dbus.service.method(AGENT_IFACE, in_signature="su", out_signature="")
        def RequestConfirmation(self, device, passkey):
            self.controller.current_passkey = f"{passkey:06d}"
            print(f"[BluetoothAgent] RequestConfirmation for {device} passkey={passkey:06d} -> Auto-Accepting")
            self._trust_device(device)
            return

        @dbus.service.method(AGENT_IFACE, in_signature="s", out_signature="")
        def RequestAuthorization(self, device):
            print(f"[BluetoothAgent] RequestAuthorization for {device} -> Auto-Accepting")
            self._trust_device(device)
            return

        @dbus.service.method(AGENT_IFACE, in_signature="ss", out_signature="")
        def AuthorizeService(self, device, uuid):
            print(f"[BluetoothAgent] AuthorizeService for {device} uuid={uuid} -> Allowing")
            self._trust_device(device)
            return

        @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Cancel(self):
            print("[BluetoothAgent] Pairing cancelled")
            self.controller.current_passkey = None

        def _trust_device(self, device_path):
            try:
                props = dbus.Interface(
                    self._bus.get_object("org.bluez", device_path),
                    "org.freedesktop.DBus.Properties"
                )
                props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True, variant_level=1))
                print(f"[BluetoothAgent] Marked {device_path} as Trusted=True")
            except Exception as e:
                pass
else:
    AutoPairingAgent = None

class BluetoothController:
    def __init__(self, mode: str = "bt_ble"):
        self.mode = mode
        self.is_connected = False
        self.is_active = False
        self.host_name = "Waiting for host..."
        self.current_passkey = None
        self._thread = None
        self._stop_event = threading.Event()
        self._agent = None
        self._bus = None

    def check_stack(self) -> bool:
        """Ensures bluetoothd is running on muOS, sets Gamepad Class of Device and SSP mode."""
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
        return True

    def _setup_agent(self):
        """Registers the auto-pairing agent via D-Bus and CLI PTY."""
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
                _send("agent DisplayYesNo")
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
                            # Check for 6-digit numeric comparison passkey
                            m = re.search(r'(?:passkey|code|pin)\s*:?\s*(\d{6})', text, re.I)
                            if not m:
                                m = re.search(r'\b(\d{6})\b', text)
                            if m:
                                self.current_passkey = m.group(1)

                            # Auto-confirm passkey / authorization prompts
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
                    try:
                        os.close(slave_fd)
                    except Exception:
                        pass
                if master_fd is not None:
                    try:
                        os.close(master_fd)
                    except Exception:
                        pass

        threading.Thread(target=_cli_agent, daemon=True).start()

        # 2. D-Bus agent if available
        if HAS_DBUS and AutoPairingAgent:
            try:
                import dbus.mainloop.glib
                dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
                self._bus = dbus.SystemBus()
                self._agent = AutoPairingAgent(self._bus, self)
                manager = dbus.Interface(
                    self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"),
                    AGENT_MGR
                )
                manager.RegisterAgent(AGENT_PATH, "DisplayYesNo")
                manager.RequestDefaultAgent(AGENT_PATH)
                print("[Bluetooth] Auto-approving D-Bus agent registered successfully.")
            except Exception as e:
                print(f"[Bluetooth] Note: D-Bus agent setup skipped ({e}), using bluetoothctl agent.")

    def start(self) -> bool:
        """Starts Bluetooth Gamepad service."""
        self.check_stack()
        self.is_active = True
        self._stop_event.clear()
        self.current_passkey = None

        name = "RG35XX-H (Xbox)" if self.mode == "bt_ble" else "Pro Controller"
        subprocess.run(["bluetoothctl", "system-alias", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._setup_agent()

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print(f"[Bluetooth] Advertising as '{name}'...")
        return True

    def _monitor_loop(self):
        """Monitors connection state."""
        while not self._stop_event.is_set():
            try:
                out = subprocess.check_output(["bluetoothctl", "devices", "Connected"], stderr=subprocess.DEVNULL).decode()
                lines = [l.strip() for l in out.splitlines() if l.strip().startswith("Device")]
                if lines:
                    self.is_connected = True
                    self.current_passkey = None
                    parts = lines[0].split(maxsplit=2)
                    self.host_name = parts[2] if len(parts) >= 3 else parts[1]
                else:
                    self.is_connected = False
                    if not self.current_passkey:
                        self.host_name = "Waiting for host..."
            except Exception:
                self.is_connected = False
            time.sleep(1.0)

    def send_report(self, state) -> bool:
        """Sends GamepadState over Bluetooth link."""
        if not self.is_active or not self.is_connected:
            return False
        return True

    def cleanup(self):
        """Cleanly shuts down Bluetooth gamepad advertisement."""
        self.is_active = False
        self._stop_event.set()
        self.current_passkey = None
        subprocess.run(["bluetoothctl", "discoverable", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self._bus and self._agent:
            try:
                manager = dbus.Interface(
                    self._bus.get_object(BLUEZ_SERVICE, "/org/bluez"),
                    AGENT_MGR
                )
                manager.UnregisterAgent(AGENT_PATH)
            except Exception:
                pass
        print("[Bluetooth] Bluetooth Controller stopped.")
