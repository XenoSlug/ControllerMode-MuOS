#!/usr/bin/env python3
"""
ControllerMode-MuOS - Main Application Entrypoint
Coordinates UI, Input Capture, USB Gadget, and Bluetooth Controllers.
"""
import argparse
import os
import signal
import sys
import time

from config import load_config, save_config
from input_reader import InputReader
from rumble import RumbleController
from ui import UIRenderer
from usb_gadget import USBGadgetController
from bt_controller import BluetoothController

MENU_ITEMS = [
    {
        "id": "usb_pc",
        "title": "USB: PC / Mac / Android / Linux",
        "desc": "Plug right USB-C port (OTG2/DC) into PC, Mac, Steam, Android.",
    },
    {
        "id": "usb_switch",
        "title": "USB: Nintendo Switch",
        "desc": "Plug into Switch dock or Switch USB-C. Emulates HORI Pro Pad.",
    },
    {
        "id": "bt_ble",
        "title": "Bluetooth: PC / Mac / iOS / Android",
        "desc": "Wireless BLE Gamepad. Pair as 'RG35XX-H (Xbox)' without pin.",
    },
    {
        "id": "bt_switch",
        "title": "Bluetooth: Nintendo Switch",
        "desc": "Wireless Switch Pro Controller mode. Use 'Change Grip/Order'.",
    },
    {
        "id": "toggle_layout",
        "title": "Toggle Button Layout",
        "desc": "Switch between Xbox (A bottom) and Nintendo (B bottom).",
    },
    {
        "id": "toggle_saver",
        "title": "Toggle Battery Saver",
        "desc": "Turns screen off while connected to save battery during play.",
    },
    {
        "id": "exit",
        "title": "Exit to muOS",
        "desc": "Return to the handheld launcher.",
    },
]

class ControllerApp:
    def __init__(self, cli_mode=None):
        self.cli_mode = cli_mode
        self.config = load_config()
        self.rumble = RumbleController(
            enabled=self.config.get("rumble_enabled", True),
            default_strength=self.config.get("rumble_strength", 80)
        )
        self.input = InputReader(
            deadzone_percent=self.config.get("deadzone_percent", 8),
            grab_exclusive=True,
            button_layout=self.config.get("button_layout", "xbox")
        )
        self.ui = UIRenderer()
        self.running = True

        # Signal handlers for clean termination
        signal.signal(signal.SIGINT, self._sig_handler)
        signal.signal(signal.SIGTERM, self._sig_handler)

    def _sig_handler(self, sig, frame):
        self.running = False

    def run(self):
        self.input.start()

        if self.cli_mode:
            self.run_controller_session(self.cli_mode)
        else:
            self.menu_loop()

        self.cleanup()

    def menu_loop(self):
        selected_index = 0
        last_dpad_up = False
        last_dpad_down = False
        last_a = False
        last_start = False

        while self.running:
            state = self.input.get_state()

            # Navigation
            if state.dpad_up and not last_dpad_up:
                selected_index = (selected_index - 1) % len(MENU_ITEMS)
                self.rumble.pulse(30)
            elif state.dpad_down and not last_dpad_down:
                selected_index = (selected_index + 1) % len(MENU_ITEMS)
                self.rumble.pulse(30)

            last_dpad_up = state.dpad_up
            last_dpad_down = state.dpad_down

            # Confirm selection
            confirm = (state.a and not last_a) or (state.start and not last_start)
            last_a = state.a
            last_start = state.start

            if confirm:
                self.rumble.pulse(60)
                item_id = MENU_ITEMS[selected_index]["id"]

                if item_id in ("usb_pc", "usb_switch", "bt_ble", "bt_switch"):
                    self.run_controller_session(item_id)
                    # Reset input triggers after session
                    self.input.exit_requested = False
                    last_a = True
                    last_start = True

                elif item_id == "toggle_layout":
                    cur = self.config.get("button_layout", "xbox")
                    self.config["button_layout"] = "nintendo" if cur == "xbox" else "xbox"
                    self.input.button_layout = self.config["button_layout"]
                    save_config(self.config)

                elif item_id == "toggle_saver":
                    cur = self.config.get("battery_saver", True)
                    self.config["battery_saver"] = not cur
                    save_config(self.config)

                elif item_id == "exit":
                    break

            # Render UI
            self.ui.render_menu(
                MENU_ITEMS,
                selected_index,
                self.config.get("button_layout", "xbox"),
                self.config.get("battery_saver", True)
            )

            time.sleep(0.02)

    def run_controller_session(self, mode: str):
        print(f"[ControllerApp] Starting session for mode: {mode}")
        backend = None
        mode_title = ""

        if mode.startswith("usb_"):
            backend = USBGadgetController(mode=mode)
            mode_title = "USB: PC / Mac / Android" if mode == "usb_pc" else "USB: Nintendo Switch"
            if not backend.start():
                print("[ControllerApp] Failed to start USB Gadget!")
                return
        else:
            backend = BluetoothController(mode=mode)
            mode_title = "Bluetooth: Xbox Wireless" if mode == "bt_ble" else "Bluetooth: Switch Pro"
            if not backend.start():
                print("[ControllerApp] Failed to start Bluetooth!")
                return

        self.rumble.pulse(150)
        report_count = 0
        last_sec = time.monotonic()
        reports_per_sec = 0

        # Battery saver tracking
        saver_enabled = self.config.get("battery_saver", True)
        saver_timeout = self.config.get("saver_timeout_seconds", 30)
        last_activity = time.monotonic()
        is_dark = False

        try:
            while self.running and not self.input.exit_requested:
                now = time.monotonic()
                state = self.input.get_state()

                # Send input to host
                if backend.send_report(state):
                    report_count += 1

                # Check host rumble
                if hasattr(backend, "read_rumble"):
                    rumble_data = backend.read_rumble()
                    if rumble_data:
                        self.rumble.set_rumble(rumble_data[0], rumble_data[1])

                # Calculate report rate
                if now - last_sec >= 1.0:
                    reports_per_sec = report_count
                    report_count = 0
                    last_sec = now

                # Battery saver check
                if saver_enabled:
                    # Detect activity on buttons
                    has_activity = (
                        state.a or state.b or state.x or state.y or
                        state.l1 or state.r1 or state.l2 or state.r2 or
                        state.l3 or state.r3 or state.dpad_up or state.dpad_down or
                        state.dpad_left or state.dpad_right or
                        abs(state.lx) > 3000 or abs(state.ly) > 3000 or
                        abs(state.rx) > 3000 or abs(state.ry) > 3000
                    )
                    if has_activity:
                        last_activity = now
                        if is_dark:
                            is_dark = False

                    elif now - last_activity > saver_timeout and not is_dark:
                        is_dark = True

                # Render active screen (throttled when dark to save CPU/battery)
                if not is_dark or (report_count % 30 == 0):
                    self.ui.render_active_screen(mode_title, state, reports_per_sec, is_dark)

                time.sleep(0.004)  # ~250 Hz loop

        finally:
            print("[ControllerApp] Exiting session, cleaning up backend...")
            self.rumble.pulse(100)
            if backend:
                backend.cleanup()

    def cleanup(self):
        print("[ControllerApp] Performing final shutdown...")
        self.input.stop()
        self.rumble.close()
        self.ui.close()
        # Ensure any leftover gadget is cleaned
        USBGadgetController().cleanup()

def main():
    parser = argparse.ArgumentParser(description="ControllerMode-MuOS")
    parser.add_argument("--mode", choices=["usb_pc", "usb_switch", "bt_ble", "bt_switch"], help="Direct launch into mode")
    args = parser.parse_args()

    app = ControllerApp(cli_mode=args.mode)
    app.run()

if __name__ == "__main__":
    main()
