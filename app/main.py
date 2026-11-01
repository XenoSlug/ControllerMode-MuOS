#!/usr/bin/env python3
"""
ControllerMode-MuOS - Main Application Entrypoint
Coordinates UI, Input Capture, USB Gadget, Bluetooth Controllers, and In-App Updater.
"""
import argparse
import glob
import os
import signal
import sys
import time

# Ensure vendored dependencies in deps/ are prioritized
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DEPS_DIR = os.path.join(APP_ROOT, "deps")
if os.path.isdir(DEPS_DIR) and DEPS_DIR not in sys.path:
    sys.path.insert(0, DEPS_DIR)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from config import load_config, save_config
from input_reader import InputReader
from rumble import RumbleController
from ui import UIRenderer
from updater import Updater, CURRENT_VERSION
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
        "id": "check_update",
        "title": "Check for Updates",
        "desc": f"Current version: v{CURRENT_VERSION}. Check GitHub for releases.",
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
        self.app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.updater = Updater(self.app_dir)
        self.rumble = RumbleController(
            enabled=self.config.get("rumble_enabled", True),
            default_strength=self.config.get("rumble_strength", 80)
        )
        self.input = InputReader(
            deadzone_percent=self.config.get("deadzone_percent", 8),
            grab_exclusive=False,
            button_layout=self.config.get("button_layout", "xbox")
        )
        self.ui = UIRenderer()
        self.running = True

        # Screen brightness preservation (prevents muOS idle dimming from sticking)
        self.bl_paths = glob.glob("/sys/class/backlight/*/brightness")
        self.initial_brightness = None
        if self.bl_paths:
            try:
                with open(self.bl_paths[0], "r") as f:
                    self.initial_brightness = int(f.read().strip())
            except Exception:
                pass

        signal.signal(signal.SIGINT, self._sig_handler)
        signal.signal(signal.SIGTERM, self._sig_handler)

    def _keep_screen_alive(self):
        """Restores screen brightness if muOS system dimmer dimmed it."""
        if self.bl_paths and self.initial_brightness:
            try:
                for p in self.bl_paths:
                    with open(p, "r") as f:
                        cur = int(f.read().strip())
                    if cur < self.initial_brightness:
                        with open(p, "w") as f:
                            f.write(f"{self.initial_brightness}\n")
            except Exception:
                pass

    def _sig_handler(self, sig, frame):
        self.running = False

    def run(self):
        self.input.start()
        # Check updates in background on startup
        self.updater.check_update_async()

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
            self._keep_screen_alive()

            # Navigation
            if state.dpad_up and not last_dpad_up:
                selected_index = (selected_index - 1) % len(MENU_ITEMS)
                self.rumble.pulse(30)
            elif state.dpad_down and not last_dpad_down:
                selected_index = (selected_index + 1) % len(MENU_ITEMS)
                self.rumble.pulse(30)

            last_dpad_up = state.dpad_up
            last_dpad_down = state.dpad_down

            # Confirm selection with layout-appropriate A button (Xbox: bottom / Nintendo: right) or Start
            confirm = (state.a and not last_a) or (state.start and not last_start)
            last_a = state.a
            last_start = state.start

            if confirm:
                self.rumble.pulse(60)
                item_id = MENU_ITEMS[selected_index]["id"]

                if item_id in ("usb_pc", "usb_switch", "bt_ble", "bt_switch"):
                    self.run_controller_session(item_id)
                    self.input.exit_requested = False
                    last_a = True
                    last_start = True

                elif item_id == "check_update":
                    self.handle_updater_menu()
                    last_a = True

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

            # Check update badge
            update_badge = ""
            if self.updater.status == "available" and self.updater.latest_info:
                update_badge = f"★ New update available: {self.updater.latest_info['version']}! Select 'Check for Updates'."

            self.ui.render_menu(
                MENU_ITEMS,
                selected_index,
                self.config.get("button_layout", "xbox"),
                self.config.get("battery_saver", True),
                update_status=update_badge
            )

            time.sleep(0.02)

    def handle_updater_menu(self):
        """Interactive in-app update checker and installer."""
        # Debounce: wait for release of physical buttons before starting
        for _ in range(50):
            st = self.input.get_state()
            if not st.raw_a and not st.raw_b and not st.start and not st.menu:
                break
            time.sleep(0.02)

        if self.updater.status in ("idle", "error"):
            self.ui.render_modal_message("Updater", "Checking GitHub for releases...", hint="")
            self.updater.check_update()

        last_a = False
        last_b = False
        start_time = time.monotonic()

        while self.running:
            state = self.input.get_state()
            self._keep_screen_alive()
            btn_a = state.a and not last_a
            btn_b = (state.b and not last_b) or state.menu
            last_a = state.a
            last_b = state.b

            elapsed = time.monotonic() - start_time

            if self.updater.status == "available":
                v = self.updater.latest_info['version']
                msg = f"New version {v} is available!\nInstall now?"
                self.ui.render_modal_message("Update Available", msg, hint="[A] Install Update    [B] Cancel")

                if btn_a:
                    self.rumble.pulse(100)
                    self.ui.render_modal_message("Downloading Update", "Fetching from GitHub...", progress=0, hint="")
                    
                    def _prog(pct):
                        self.ui.render_modal_message("Downloading Update", "Unpacking & Installing...", progress=pct, hint="")

                    self.updater.apply_update(progress_cb=_prog)
                    time.sleep(1.0)
                    break

                if btn_b:
                    self.rumble.pulse(30)
                    break

            elif self.updater.status == "up_to_date":
                msg = f"You are on the latest version (v{CURRENT_VERSION}).\nNo updates needed!"
                hint = "[B] Close" if elapsed >= 1.2 else ""
                self.ui.render_modal_message("Up to Date", msg, hint=hint)
                # Keep on screen so user can read; allow dismiss after 1.2s or auto close after 3.5s
                if (elapsed >= 1.2 and (btn_b or btn_a)) or (elapsed >= 3.5):
                    self.rumble.pulse(30)
                    break

            elif self.updater.status == "error":
                msg = f"{self.updater.error_message}\nPlease check Wi-Fi connection."
                hint = "[B] Close" if elapsed >= 1.0 else ""
                self.ui.render_modal_message("Update Check Failed", msg, hint=hint)
                if (elapsed >= 1.0 and (btn_b or btn_a)) or (elapsed >= 4.0):
                    self.rumble.pulse(30)
                    break

            time.sleep(0.03)

    def run_controller_session(self, mode: str):
        print(f"[ControllerApp] Starting session for mode: {mode}")
        backend = None
        mode_title = ""

        if mode.startswith("usb_"):
            backend = USBGadgetController(mode=mode)
            mode_title = "USB: PC / Mac / Android" if mode == "usb_pc" else "USB: Nintendo Switch"
            if not backend.start():
                err = getattr(backend, "error_message", "Could not initialize USB gadget.")
                self.ui.render_modal_message("USB Mode Failed", f"{err}\n\nPlug cable into right port (OTG2/DC).", hint="[B] Close")
                for _ in range(80):
                    st = self.input.get_state()
                    if st.a or st.b or st.menu or st.start:
                        break
                    time.sleep(0.05)
                return
        else:
            backend = BluetoothController(mode=mode)
            mode_title = "Bluetooth: Xbox Wireless" if mode == "bt_ble" else "Bluetooth: Switch Pro"
            if not backend.start():
                self.ui.render_modal_message("Bluetooth Failed", "Could not start Bluetooth stack.\nCheck Wi-Fi / BT toggle in muOS.", hint="[B] Close")
                for _ in range(80):
                    st = self.input.get_state()
                    if st.a or st.b or st.menu or st.start:
                        break
                    time.sleep(0.05)
                return

        self.rumble.pulse(150)
        report_count = 0
        last_sec = time.monotonic()
        reports_per_sec = 0

        saver_enabled = self.config.get("battery_saver", True)
        saver_timeout = self.config.get("saver_timeout_seconds", 30)
        last_activity = time.monotonic()
        is_dark = False

        try:
            while self.running and not self.input.exit_requested:
                now = time.monotonic()
                state = self.input.get_state()

                if backend.send_report(state):
                    report_count += 1

                if hasattr(backend, "read_rumble"):
                    rumble_data = backend.read_rumble()
                    if rumble_data:
                        self.rumble.set_rumble(rumble_data[0], rumble_data[1])

                if now - last_sec >= 1.0:
                    reports_per_sec = report_count
                    report_count = 0
                    last_sec = now

                if saver_enabled:
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

                passkey = getattr(backend, "current_passkey", None)
                host_name = getattr(backend, "host_name", None)

                if not is_dark or (report_count % 30 == 0):
                    self.ui.render_active_screen(
                        mode_title,
                        state,
                        reports_per_sec,
                        is_dark,
                        passkey=passkey,
                        host_name=host_name,
                        layout_name=self.config.get("button_layout", "xbox")
                    )

                time.sleep(0.004)

        finally:
            print("[ControllerApp] Exiting session, cleaning up backend...")
            self.rumble.pulse(100)
            if backend:
                backend.cleanup()

    def cleanup(self):
        print("[ControllerApp] Performing final shutdown...")
        if self.bl_paths and self.initial_brightness:
            try:
                for p in self.bl_paths:
                    with open(p, "w") as f:
                        f.write(f"{self.initial_brightness}\n")
            except Exception:
                pass
        self.input.stop()
        self.rumble.close()
        self.ui.close()
        USBGadgetController().cleanup()

def main():
    parser = argparse.ArgumentParser(description="ControllerMode-MuOS")
    parser.add_argument("--mode", choices=["usb_pc", "usb_switch", "bt_ble", "bt_switch"], help="Direct launch into mode")
    parser.add_argument("extra_args", nargs="*", help="muOS launch arguments")
    args, _ = parser.parse_known_args()

    app = ControllerApp(cli_mode=args.mode)
    app.run()

if __name__ == "__main__":
    main()
