#!/usr/bin/env python3
"""
ControllerMode-MuOS - Linux evdev Input Reader
Captures built-in gamepad controls from /dev/input/event* on RG35XX H (muOS).
"""
import fcntl
import glob
import os
import struct
import threading
import time

# Linux input event format: time_sec (ulong), time_usec (ulong), type (u16), code (u16), value (s32)
# On 64-bit ARM Linux (aarch64), timeval is two 64-bit integers.
EVENT_FORMAT = "qqHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

# ioctl EVIOCGRAB
EVIOCGRAB = 0x40044590

# Event types
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

# Standard Linux button codes
BTN_SOUTH = 0x130  # Often 'B' in Nintendo or 'A' in Xbox
BTN_EAST  = 0x131  # Often 'A' in Nintendo or 'B' in Xbox
BTN_NORTH = 0x133  # Often 'X' in Nintendo or 'Y' in Xbox
BTN_WEST  = 0x134  # Often 'Y' in Nintendo or 'X' in Xbox
BTN_TL    = 0x136  # L1
BTN_TR    = 0x137  # R1
BTN_TL2   = 0x138  # L2
BTN_TR2   = 0x139  # R2
BTN_SELECT= 0x13a  # Select
BTN_START = 0x13b  # Start
BTN_MODE  = 0x13c  # Menu / F
BTN_THUMBL= 0x13d  # L3
BTN_THUMBR= 0x13e  # R3

# Alternative D-pad keys on some H700 devices
DPAD_UP_RAW    = 0x220
DPAD_DOWN_RAW  = 0x221
DPAD_LEFT_RAW  = 0x222
DPAD_RIGHT_RAW = 0x223

# Standard keyboard keys
KEY_UP    = 103
KEY_LEFT  = 105
KEY_RIGHT = 106
KEY_DOWN  = 108

# Analog axes
ABS_X     = 0x00  # Left stick X
ABS_Y     = 0x01  # Left stick Y
ABS_Z     = 0x02
ABS_RX    = 0x03  # Right stick X
ABS_RY    = 0x04  # Right stick Y
ABS_RZ    = 0x05
ABS_HAT0X = 0x10  # D-pad X
ABS_HAT0Y = 0x11  # D-pad Y

class GamepadState:
    def __init__(self):
        # Mapped buttons (reflects button_layout setting for controller forwarding)
        self.a = False
        self.b = False
        self.x = False
        self.y = False

        # Raw physical buttons on RG35XX H hardware (for UI navigation)
        self.raw_a = False  # Physical 'A' button (Right / East)
        self.raw_b = False  # Physical 'B' button (Bottom / South)
        self.raw_x = False  # Physical 'X' button (Top / North)
        self.raw_y = False  # Physical 'Y' button (Left / West)

        self.l1 = False
        self.r1 = False
        self.l2 = False
        self.r2 = False
        self.l3 = False
        self.r3 = False
        self.select = False
        self.start = False
        self.menu = False
        
        # D-pad
        self.dpad_up = False
        self.dpad_down = False
        self.dpad_left = False
        self.dpad_right = False
        
        # Analog sticks: -32768 to 32767 (0 = centered)
        self.lx = 0
        self.ly = 0
        self.rx = 0
        self.ry = 0
        
        # Triggers: 0 to 255
        self.lt_val = 0
        self.rt_val = 0

        self.last_update = time.monotonic()

    def copy(self):
        s = GamepadState()
        s.a = self.a
        s.b = self.b
        s.x = self.x
        s.y = self.y
        s.raw_a = self.raw_a
        s.raw_b = self.raw_b
        s.raw_x = self.raw_x
        s.raw_y = self.raw_y
        s.l1 = self.l1
        s.r1 = self.r1
        s.l2 = self.l2
        s.r2 = self.r2
        s.l3 = self.l3
        s.r3 = self.r3
        s.select = self.select
        s.start = self.start
        s.menu = self.menu
        s.dpad_up = self.dpad_up
        s.dpad_down = self.dpad_down
        s.dpad_left = self.dpad_left
        s.dpad_right = self.dpad_right
        s.lx = self.lx
        s.ly = self.ly
        s.rx = self.rx
        s.ry = self.ry
        s.lt_val = self.lt_val
        s.rt_val = self.rt_val
        s.last_update = self.last_update
        return s

class InputReader:
    def __init__(self, deadzone_percent: int = 8, grab_exclusive: bool = True, button_layout: str = "xbox"):
        self.deadzone_percent = deadzone_percent
        self.deadzone_thresh = int(32767 * (deadzone_percent / 100.0))
        self.grab_exclusive = grab_exclusive
        self.button_layout = button_layout
        
        self.state = GamepadState()
        self._state_lock = threading.Lock()
        
        self._exit_combo_hold_time = 1.2
        self._combo_start_time = None
        self.exit_requested = False
        
        self._device_paths = []
        self._fds = []
        self._running = False
        self._thread = None
        
        self._find_devices()

    def _find_devices(self):
        """Finds input event devices representing the RG35XX H controls."""
        candidates = sorted(glob.glob("/dev/input/event*"))
        found = []
        
        # Prefer devices with 'muos', 'gamepad', 'joypad', 'keys' in their name
        for path in candidates:
            try:
                name_path = f"/sys/class/input/{os.path.basename(path)}/device/name"
                if os.path.exists(name_path):
                    with open(name_path, "r") as f:
                        name = f.read().strip().lower()
                        if any(k in name for k in ["muos", "gamepad", "joypad", "h700", "keys", "singleadc"]):
                            found.append(path)
            except OSError:
                pass
                
        if not found and candidates:
            # Fallback to inspecting capabilities or opening all available
            found = candidates
            
        self._device_paths = found
        print(f"[InputReader] Selected input devices: {self._device_paths}")

    def start(self):
        if self._running:
            return
        self._running = True
        
        # Open file descriptors
        self._fds = []
        for path in self._device_paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                if self.grab_exclusive:
                    try:
                        fcntl.ioctl(fd, EVIOCGRAB, 1)
                    except OSError:
                        pass
                self._fds.append(fd)
            except OSError as e:
                print(f"[InputReader] Cannot open {path}: {e}")
                
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _apply_deadzone(self, val: int) -> int:
        if abs(val) < self.deadzone_thresh:
            return 0
        return val

    def _read_loop(self):
        import select
        while self._running:
            if not self._fds:
                time.sleep(0.1)
                continue
            try:
                r, _, _ = select.select(self._fds, [], [], 0.05)
                for fd in r:
                    while True:
                        try:
                            data = os.read(fd, EVENT_SIZE)
                            if not data or len(data) < EVENT_SIZE:
                                break
                            sec, usec, ev_type, code, val = struct.unpack(EVENT_FORMAT, data)
                            self._handle_event(ev_type, code, val)
                        except (BlockingIOError, InterruptedError):
                            break
                        except OSError:
                            break
            except Exception as e:
                time.sleep(0.01)

            # Check exit combos: Select + Start + L1 + R1, or Menu held
            with self._state_lock:
                combo_active = (self.state.select and self.state.start and self.state.l1 and self.state.r1) or self.state.menu
                now = time.monotonic()
                if combo_active:
                    if self._combo_start_time is None:
                        self._combo_start_time = now
                    elif now - self._combo_start_time >= self._exit_combo_hold_time:
                        print("[InputReader] Exit combo detected! Triggering exit.")
                        self.exit_requested = True
                else:
                    self._combo_start_time = None

    def _handle_event(self, ev_type: int, code: int, val: int):
        with self._state_lock:
            self.state.last_update = time.monotonic()
            
            if ev_type == EV_KEY:
                pressed = (val != 0)

                # Track physical button states for reliable UI navigation
                if code in (BTN_SOUTH, 0x130):
                    self.state.raw_b = pressed
                elif code in (BTN_EAST, 0x131):
                    self.state.raw_a = pressed
                elif code in (BTN_NORTH, 0x133):
                    self.state.raw_x = pressed
                elif code in (BTN_WEST, 0x134):
                    self.state.raw_y = pressed
                
                # Mapped face buttons (Xbox layout vs Nintendo layout for gameplay)
                if code in (BTN_SOUTH, 0x130):
                    if self.button_layout == "nintendo":
                        self.state.b = pressed
                    else:
                        self.state.a = pressed
                elif code in (BTN_EAST, 0x131):
                    if self.button_layout == "nintendo":
                        self.state.a = pressed
                    else:
                        self.state.b = pressed
                elif code in (BTN_NORTH, 0x133):
                    if self.button_layout == "nintendo":
                        self.state.x = pressed
                    else:
                        self.state.y = pressed
                elif code in (BTN_WEST, 0x134):
                    if self.button_layout == "nintendo":
                        self.state.y = pressed
                    else:
                        self.state.x = pressed
                        
                # Shoulder buttons
                elif code in (BTN_TL, 0x136):
                    self.state.l1 = pressed
                elif code in (BTN_TR, 0x137):
                    self.state.r1 = pressed
                elif code in (BTN_TL2, 0x138):
                    self.state.l2 = pressed
                    self.state.lt_val = 255 if pressed else 0
                elif code in (BTN_TR2, 0x139):
                    self.state.r2 = pressed
                    self.state.rt_val = 255 if pressed else 0
                    
                # Sticks and Meta
                elif code in (BTN_THUMBL, 0x13d):
                    self.state.l3 = pressed
                elif code in (BTN_THUMBR, 0x13e):
                    self.state.r3 = pressed
                elif code in (BTN_SELECT, 0x13a):
                    self.state.select = pressed
                elif code in (BTN_START, 0x13b):
                    self.state.start = pressed
                elif code in (BTN_MODE, 0x13c):
                    self.state.menu = pressed
                    
                # D-Pad keys
                elif code in (KEY_UP, DPAD_UP_RAW):
                    self.state.dpad_up = pressed
                elif code in (KEY_DOWN, DPAD_DOWN_RAW):
                    self.state.dpad_down = pressed
                elif code in (KEY_LEFT, DPAD_LEFT_RAW):
                    self.state.dpad_left = pressed
                elif code in (KEY_RIGHT, DPAD_RIGHT_RAW):
                    self.state.dpad_right = pressed

            elif ev_type == EV_ABS:
                # Analog sticks
                if code == ABS_X:
                    self.state.lx = self._apply_deadzone(val)
                elif code == ABS_Y:
                    self.state.ly = self._apply_deadzone(val)
                elif code in (ABS_RX, ABS_Z):
                    self.state.rx = self._apply_deadzone(val)
                elif code in (ABS_RY, ABS_RZ):
                    self.state.ry = self._apply_deadzone(val)
                    
                # Analog triggers
                elif code in (0x02, 0x0a):  # ABS_Z or ABS_GAS
                    self.state.lt_val = max(0, min(255, val))
                    self.state.l2 = (self.state.lt_val > 30)
                elif code in (0x05, 0x09):  # ABS_RZ or ABS_BRAKE
                    self.state.rt_val = max(0, min(255, val))
                    self.state.r2 = (self.state.rt_val > 30)
                    
                # Hat switch D-pad
                elif code == ABS_HAT0X:
                    self.state.dpad_left = (val < 0)
                    self.state.dpad_right = (val > 0)
                elif code == ABS_HAT0Y:
                    self.state.dpad_up = (val < 0)
                    self.state.dpad_down = (val > 0)

    def get_state(self) -> GamepadState:
        with self._state_lock:
            return self.state.copy()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        for fd in self._fds:
            try:
                if self.grab_exclusive:
                    try:
                        fcntl.ioctl(fd, EVIOCGRAB, 0)
                    except OSError:
                        pass
                os.close(fd)
            except OSError:
                pass
        self._fds = []
