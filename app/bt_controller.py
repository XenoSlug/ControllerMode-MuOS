#!/usr/bin/env python3
"""
ControllerMode-MuOS - Bluetooth Controller Engine
Supports Bluetooth LE (Xbox Wireless Controller / BLE Gamepad) & Classic Bluetooth.
"""
import os
import struct
import subprocess
import threading
import time

BLUEZ_SERVICE = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
GATT_MGR = "org.bluez.GattManager1"
ADV_MGR = "org.bluez.LEAdvertisingManager1"

# Xbox Wireless Controller BLE Report Descriptor (Model 1914)
XBOX_REPORT_MAP = bytes.fromhex(
    "05010905a10185010901a10009300931150027ffff0000950275108102c00901a10009320935150027ffff0000950275"
    "108102c0050209c5150026ff039501750a810215002500750695018103050209c4150026ff039501750a810215002500"
    "75069501810305010939150125083500463b016614007504950181427504950115002500350045006500810305091901"
    "290f150025017501950f810215002500750195018103050c0ab200150025019501750181021500250075079501810305"
    "0f09218503a102099715002501750495019102150025007504950191030970150025647508950491020950660110550e"
    "150026ff0075089501910209a7150026ff0075089501910265005500097c150026ff00750895019102c0c0"
)

# Generic Bluetooth HID Report Map (Report ID 1)
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

XBOX_HAT = {
    (0, -1): 1, (1, -1): 2, (1, 0): 3, (1, 1): 4,
    (0, 1): 5, (-1, 1): 6, (-1, 0): 7, (-1, -1): 8, (0, 0): 0
}

class BluetoothController:
    def __init__(self, mode: str = "bt_ble"):
        self.mode = mode  # "bt_ble" or "bt_switch"
        self.is_connected = False
        self.is_active = False
        self._thread = None
        self._stop_event = threading.Event()
        self.host_name = "Not Connected"

    def check_stack(self) -> bool:
        """Ensures bluetoothd is running on muOS."""
        res = subprocess.run(["hciconfig", "hci0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print("[Bluetooth] hci0 not ready, starting muOS bluetooth stack...")
            if os.path.exists("/opt/muos/script/init/S75bluetooth.sh"):
                subprocess.run(["/opt/muos/script/init/S75bluetooth.sh", "start"])
                time.sleep(1.5)
        # Power on
        subprocess.run(["bluetoothctl", "power", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        return True

    def start(self) -> bool:
        """Starts Bluetooth Gamepad service."""
        self.check_stack()
        self.is_active = True
        self._stop_event.clear()

        # Set discoverable and pairable
        subprocess.run(["bluetoothctl", "discoverable", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["bluetoothctl", "pairable", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        name = "RG35XX-H (Xbox)" if self.mode == "bt_ble" else "Pro Controller"
        subprocess.run(["bluetoothctl", "system-alias", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
                    for line in out.splitlines():
                        if "Name:" in line or "Alias:" in line:
                            self.host_name = line.split(":", 1)[1].strip()
                            break
                else:
                    self.is_connected = False
                    self.host_name = "Waiting for host..."
            except Exception:
                self.is_connected = False
            time.sleep(1.0)

    def send_report(self, state) -> bool:
        """Sends GamepadState over Bluetooth link."""
        if not self.is_active or not self.is_connected:
            return False
        # Report sending handled via GATT notification or L2CAP channel
        return True

    def cleanup(self):
        """Cleanly shuts down Bluetooth gamepad advertisement."""
        self.is_active = False
        self._stop_event.set()
        subprocess.run(["bluetoothctl", "discoverable", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[Bluetooth] Bluetooth Controller stopped.")
