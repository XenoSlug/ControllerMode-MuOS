#!/usr/bin/env python3
"""
ControllerMode-MuOS - Haptic & Rumble Motor Driver
Controls the RG35XX H internal vibration motor safely.
"""
import os
import threading
import time

MOTOR_PATHS = [
    "/sys/class/power_supply/axp2202-battery/moto",
    "/sys/devices/platform/soc@3000000/soc@3000000:axp2202-battery/moto",
    "/sys/class/pwm-vibrator/vibrator/enable",
]

class RumbleController:
    def __init__(self, enabled: bool = True, default_strength: int = 80):
        self.enabled = enabled
        self.default_strength = default_strength
        self.motor_path = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread = None
        self._current_pulse = 0.0
        self._last_trigger = 0.0

        for path in MOTOR_PATHS:
            if os.path.exists(path):
                self.motor_path = path
                break

    def _set_motor(self, active: bool):
        if not self.motor_path or not self.enabled:
            return
        try:
            with open(self.motor_path, "w") as f:
                f.write("1\n" if active else "0\n")
        except OSError:
            pass

    def pulse(self, duration_ms: int = 150):
        """Triggers a short pulse of vibration."""
        if not self.enabled or duration_ms <= 0:
            return
        # Rate limit to at most 10 pulses per second
        now = time.monotonic()
        if now - self._last_trigger < 0.08:
            return
        self._last_trigger = now

        def _run():
            with self._lock:
                self._set_motor(True)
                time.sleep(duration_ms / 1000.0)
                self._set_motor(False)

        threading.Thread(target=_run, daemon=True).start()

    def set_rumble(self, strong_magnitude: int, weak_magnitude: int):
        """
        Called when host sends rumble packet (values 0-255 or 0-65535).
        Maps to motor pulsing.
        """
        if not self.enabled:
            return
        val = max(strong_magnitude, weak_magnitude)
        if val > 1000:
            val = val // 256
        if val > 15:
            # Map intensity to pulse duration
            duration = min(250, max(30, int(val)))
            self.pulse(duration)
        else:
            self._set_motor(False)

    def close(self):
        self._set_motor(False)
