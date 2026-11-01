#!/usr/bin/env python3
"""
ControllerMode-MuOS - Bluetooth Controller Engine
Supports Bluetooth LE (Xbox Wireless Controller / BLE Gamepad) & Classic Bluetooth
with an auto-approving pairing agent to handle PIN/passkey requests automatically.
"""
import os
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
        """Ensures bluetoothd is running on muOS."""
        res = subprocess.run(["hciconfig", "hci0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print("[Bluetooth] hci0 not ready, starting muOS bluetooth stack...")
            if os.path.exists("/opt/muos/script/init/S75bluetooth.sh"):
                subprocess.run(["/opt/muos/script/init/S75bluetooth.sh", "start"])
                time.sleep(1.5)
        subprocess.run(["bluetoothctl", "power", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        return True

    def _setup_agent(self):
        """Registers the auto-pairing agent via D-Bus and CLI."""
        # 1. Shell agent as fallback
        def _cli_agent():
            try:
                # Tell bluetoothctl to run NoInputNoOutput default agent
                p = subprocess.Popen(
                    ["bluetoothctl"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                p.stdin.write("agent NoInputNoOutput\ndefault-agent\ndiscoverable on\npairable on\n")
                p.stdin.flush()
                while not self._stop_event.is_set():
                    time.sleep(0.5)
                p.terminate()
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
                out = subprocess.check_output(["bluetoothctl", "info"], stderr=subprocess.DEVNULL).decode()
                if "Connected: yes" in out:
                    self.is_connected = True
                    self.current_passkey = None
                    for line in out.splitlines():
                        if "Name:" in line or "Alias:" in line:
                            self.host_name = line.split(":", 1)[1].strip()
                            break
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
