#!/usr/bin/env python3
"""
ControllerMode-MuOS - Linux USB Gadget ConfigFS Engine
Emulates standard USB HID Gamepad and Nintendo Switch HORI Pad over USB-C (OTG2).
"""
import glob
import os
import struct
import subprocess
import time

GADGET_DIR = "/sys/kernel/config/usb_gadget/controller-mode"
CONFIG_DIR = f"{GADGET_DIR}/configs/c.1"
FUNC_DIR = f"{GADGET_DIR}/functions/hid.usb0"
DEV_HIDG = "/dev/hidg0"

# Nintendo Switch HORI Pokken Tournament DX Pro Pad HID Report Descriptor (86 bytes)
SWITCH_REPORT_DESC = bytes.fromhex(
    "05010905a101"                                      # Generic Desktop, Game Pad, Collection
    "150025013500450175019510"                          # 16 x 1 bit (Buttons 1..16)
    "0509190129108102"
    "05012507463b0175049501651409398142"               # Hat switch (0..7, 8=center)
    "650095018101"                                      # 4 bits padding
    "26ff0046ff000930093109320935750895048102"          # X, Y, Z, Rz (8-bit 0..255, 128 center)
    "0600ff092095018102"                                # 1 vendor byte
    "0a212695089102"                                    # 8-byte vendor output report
    "c0"
)

# Standard PC/Mac/Android USB Gamepad HID Report Descriptor (67 bytes)
PC_REPORT_DESC = bytes.fromhex(
    "05010905a101"                                      # Generic Desktop, Game Pad
    "05091901291015002501750195108102"                  # 16 buttons
    "05010939150025073500463b016514750495018142"       # Hat switch 0..7
    "6500750495018103"                                  # 4 bits padding
    "05010930093109320935"                              # X, Y (left stick), Z, Rz (right stick)
    "150026ff00750895048102"                            # 4 x 8-bit axes (0..255, 128 center)
    "09330934"                                          # Rx, Ry (triggers LT, RT)
    "150026ff00750895028102"                            # 2 x 8-bit axes (0..255)
    "c0"
)

# Hat mapping: (x, y) where x, y in {-1, 0, 1}
HAT_MAP_SWITCH = {
    (0, -1): 0,   # Up
    (1, -1): 1,   # Up-Right
    (1, 0): 2,    # Right
    (1, 1): 3,    # Down-Right
    (0, 1): 4,    # Down
    (-1, 1): 5,   # Down-Left
    (-1, 0): 6,   # Left
    (-1, -1): 7,  # Up-Left
    (0, 0): 8,    # Centered
}

HAT_MAP_PC = {
    (0, -1): 0,   # Up
    (1, -1): 1,   # Up-Right
    (1, 0): 2,    # Right
    (1, 1): 3,    # Down-Right
    (0, 1): 4,    # Down
    (-1, 1): 5,   # Down-Left
    (-1, 0): 6,   # Left
    (-1, -1): 7,  # Up-Left
    (0, 0): 8,    # Centered
}

def _write_file(path: str, content: str or bytes):
    if isinstance(content, str):
        content = content.encode("utf-8")
    with open(path, "wb") as f:
        f.write(content)

def find_udc() -> str:
    """Detects available USB Device Controller on RG35XX H."""
    # Check muOS configured board UDC
    muos_board_udc = "/opt/muos/device/config/board/udc"
    if os.path.isfile(muos_board_udc):
        try:
            with open(muos_board_udc, "r") as f:
                udc = f.read().strip()
                if udc and os.path.exists(f"/sys/class/udc/{udc}"):
                    return udc
        except OSError:
            pass

    # Check sysfs /sys/class/udc
    if os.path.exists("/sys/class/udc"):
        udcs = [u for u in os.listdir("/sys/class/udc") if not u.startswith(".")]
        if udcs:
            return udcs[0]

    # Check OTG controller file
    for p in ["/tmp/udc_name", "/run/udc_name"]:
        try:
            with open(p, "r") as f:
                udc = f.read().strip()
                if udc and os.path.exists(f"/sys/class/udc/{udc}"):
                    return udc
        except OSError:
            pass

    return "5100000.udc-controller"

class USBGadgetController:
    def __init__(self, mode: str = "usb_pc"):
        self.mode = mode  # "usb_pc" or "usb_switch"
        self.udc = find_udc()
        self.is_active = False
        self._dev_fd = None
        self.error_message = ""

    def start(self) -> bool:
        """Sets up the USB HID Gadget in ConfigFS and binds UDC."""
        self.cleanup()
        self.error_message = ""

        # 1. Ensure kernel module usb_f_hid is loaded on Linux/muOS
        is_hid_loaded = False
        try:
            with open("/proc/modules", "r") as f:
                if "usb_f_hid" in f.read():
                    is_hid_loaded = True
        except Exception:
            pass

        if not is_hid_loaded:
            # Try modprobe first
            subprocess.run(["modprobe", "libcomposite"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["modprobe", "usb_f_hid"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # If still not loaded, insmod our bundled kernel module built for H700 muOS
            app_dir = os.path.dirname(os.path.abspath(__file__))
            ko_path = os.path.join(app_dir, "kernel", "usb_f_hid.ko")
            if os.path.isfile(ko_path):
                subprocess.run(["insmod", ko_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.2)

        # 2. Ensure configfs is mounted
        if not os.path.ismount("/sys/kernel/config"):
            subprocess.run(["mount", "-t", "configfs", "none", "/sys/kernel/config"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.1)

        # 3. Detect UDC
        self.udc = find_udc()
        print(f"[USBGadget] Initializing gadget mode '{self.mode}' on UDC '{self.udc}'...")

        try:
            os.makedirs(f"{GADGET_DIR}/strings/0x409", exist_ok=True)
            os.makedirs(f"{CONFIG_DIR}/strings/0x409", exist_ok=True)
            os.makedirs(FUNC_DIR, exist_ok=True)

            if self.mode == "usb_switch":
                # HORI Pokken Tournament DX Pro Pad for Nintendo Switch
                _write_file(f"{GADGET_DIR}/idVendor", "0x0f0d\n")
                _write_file(f"{GADGET_DIR}/idProduct", "0x0092\n")
                _write_file(f"{GADGET_DIR}/bcdDevice", "0x0100\n")
                _write_file(f"{GADGET_DIR}/bcdUSB", "0x0200\n")
                _write_file(f"{GADGET_DIR}/strings/0x409/manufacturer", "HORI CO.,LTD.\n")
                _write_file(f"{GADGET_DIR}/strings/0x409/product", "POKKEN CONTROLLER\n")
                _write_file(f"{GADGET_DIR}/strings/0x409/serialnumber", "Switch-Pad-01\n")
                desc = SWITCH_REPORT_DESC
            else:
                # Standard PC/Mac/Android Gamepad
                _write_file(f"{GADGET_DIR}/idVendor", "0x1209\n")
                _write_file(f"{GADGET_DIR}/idProduct", "0x2882\n")
                _write_file(f"{GADGET_DIR}/bcdDevice", "0x0100\n")
                _write_file(f"{GADGET_DIR}/bcdUSB", "0x0200\n")
                _write_file(f"{GADGET_DIR}/strings/0x409/manufacturer", "MustardOS\n")
                _write_file(f"{GADGET_DIR}/strings/0x409/product", "RG35XX-H Gamepad\n")
                _write_file(f"{GADGET_DIR}/strings/0x409/serialnumber", "ControllerMode-01\n")
                desc = PC_REPORT_DESC

            # Write HID Function attributes
            _write_file(f"{FUNC_DIR}/protocol", "0\n")
            _write_file(f"{FUNC_DIR}/subclass", "0\n")
            _write_file(f"{FUNC_DIR}/report_length", f"{len(desc)}\n")
            _write_file(f"{FUNC_DIR}/report_desc", desc)

            # Link function to config
            link_target = f"{CONFIG_DIR}/hid.usb0"
            if not os.path.exists(link_target):
                os.symlink(FUNC_DIR, link_target)

            # Check if UDC is available
            if not os.path.exists(f"/sys/class/udc/{self.udc}"):
                available = [u for u in os.listdir("/sys/class/udc") if not u.startswith(".")] if os.path.exists("/sys/class/udc") else []
                if available:
                    self.udc = available[0]
                else:
                    self.error_message = "No UDC device found.\nPlug cable into right USB-C (OTG2/DC) port."
                    print(f"[USBGadget] {self.error_message}")
                    self.cleanup()
                    return False

            # Bind UDC
            _write_file(f"{GADGET_DIR}/UDC", f"{self.udc}\n")
            time.sleep(0.15)

            # Wait for /dev/hidg0 to appear
            for _ in range(40):
                if os.path.exists(DEV_HIDG):
                    break
                time.sleep(0.05)

            if not os.path.exists(DEV_HIDG):
                self.error_message = f"Gadget bound to {self.udc}\nbut {DEV_HIDG} node did not appear."
                print(f"[USBGadget] Error: {self.error_message}")
                self.cleanup()
                return False

            self._dev_fd = os.open(DEV_HIDG, os.O_RDWR | os.O_NONBLOCK)
            self.is_active = True
            print(f"[USBGadget] Successfully initialized {DEV_HIDG}")
            return True

        except Exception as e:
            self.error_message = f"USB Gadget error: {e}"
            print(f"[USBGadget] {self.error_message}")
            self.cleanup()
            return False

    def send_report(self, state) -> bool:
        """Encodes GamepadState and writes HID report to /dev/hidg0."""
        if not self.is_active or self._dev_fd is None:
            return False

        # Convert stick axes (-32768..32767) to 0..255 (128 center)
        # Note: Y axis is inverted on gamepad protocol (up is negative in evdev, but negative Y or standard)
        lx = max(0, min(255, int((state.lx / 32767.0) * 127 + 128)))
        ly = max(0, min(255, int((state.ly / 32767.0) * 127 + 128)))
        rx = max(0, min(255, int((state.rx / 32767.0) * 127 + 128)))
        ry = max(0, min(255, int((state.ry / 32767.0) * 127 + 128)))

        # D-pad Hat
        dx = (1 if state.dpad_right else 0) - (1 if state.dpad_left else 0)
        dy = (1 if state.dpad_down else 0) - (1 if state.dpad_up else 0)

        if self.mode == "usb_switch":
            # Switch format: 8 bytes
            # Low buttons: Y(0), B(1), A(2), X(3), L(4), R(5), ZL(6), ZR(7)
            # High buttons: Minus(0), Plus(1), LStick(2), RStick(3), Home(4), Capture(5)
            b_low = 0
            if state.x: b_low |= (1 << 0)  # Y button in Nintendo
            if state.a: b_low |= (1 << 1)  # B button in Nintendo
            if state.b: b_low |= (1 << 2)  # A button in Nintendo
            if state.y: b_low |= (1 << 3)  # X button in Nintendo
            if state.l1: b_low |= (1 << 4)
            if state.r1: b_low |= (1 << 5)
            if state.l2: b_low |= (1 << 6)
            if state.r2: b_low |= (1 << 7)

            b_high = 0
            if state.select: b_high |= (1 << 0)  # Minus
            if state.start:  b_high |= (1 << 1)  # Plus
            if state.l3:     b_high |= (1 << 2)
            if state.r3:     b_high |= (1 << 3)
            if state.menu:   b_high |= (1 << 4)  # Home / Menu

            hat = HAT_MAP_SWITCH.get((dx, dy), 8)
            report = struct.pack("<BBBBBBBB", b_low, b_high, hat, lx, ly, rx, ry, 0)
        else:
            # PC/Universal format:
            # 16 buttons (2 bytes), Hat (1 byte, 4 bits hat + 4 bits padding),
            # 4 axes (4 bytes: lx, ly, rx, ry), 2 triggers (2 bytes: lt, rt)
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

            hat = HAT_MAP_PC.get((dx, dy), 8)
            lt = state.lt_val if state.lt_val > 0 else (255 if state.l2 else 0)
            rt = state.rt_val if state.rt_val > 0 else (255 if state.r2 else 0)

            report = struct.pack("<HBBBBBB", buttons, hat, lx, ly, rx, ry, lt, rt)

        try:
            os.write(self._dev_fd, report)
            return True
        except (BlockingIOError, OSError):
            return False

    def read_rumble(self) -> tuple or None:
        """Reads host rumble report from /dev/hidg0 if available."""
        if not self.is_active or self._dev_fd is None:
            return None
        try:
            data = os.read(self._dev_fd, 64)
            if data and len(data) >= 2:
                # Returns (strong, weak)
                return (data[0], data[1])
        except (BlockingIOError, OSError):
            pass
        return None

    def cleanup(self):
        """Unbinds UDC and cleans ConfigFS gadget entries."""
        self.is_active = False
        if self._dev_fd is not None:
            try:
                os.close(self._dev_fd)
            except OSError:
                pass
            self._dev_fd = None

        if os.path.exists(f"{GADGET_DIR}/UDC"):
            try:
                _write_file(f"{GADGET_DIR}/UDC", "\n")
            except OSError:
                pass

        link_target = f"{CONFIG_DIR}/hid.usb0"
        if os.path.islink(link_target) or os.path.exists(link_target):
            try:
                os.unlink(link_target)
            except OSError:
                pass

        for p in [
            f"{CONFIG_DIR}/strings/0x409",
            CONFIG_DIR,
            FUNC_DIR,
            f"{GADGET_DIR}/strings/0x409",
            GADGET_DIR
        ]:
            if os.path.exists(p):
                try:
                    os.rmdir(p)
                except OSError:
                    pass
        print("[USBGadget] Gadget removed cleanly.")
