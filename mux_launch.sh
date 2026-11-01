#!/bin/sh
# HELP: Use your RG35XX H as a USB or Bluetooth gamepad for PC, Mac, Switch, or Phone
# ICON: controllermode
# GRID: Controller Mode
# SPDX-License-Identifier: MIT

# Source muOS environment functions if present
if [ -f /opt/muos/script/var/func.sh ]; then
	. /opt/muos/script/var/func.sh
	APP_BIN="python3"
	SETUP_APP "$APP_BIN" ""
	SETUP_STAGE_OVERLAY
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR" || exit 1

mkdir -p "$APP_DIR/logs"

# Ensure SDL uses system libraries and background events
export PYSDL2_DLL_PATH="/usr/lib"
export SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1
export PYTHONDONTWRITEBYTECODE=1

# Restart loop for in-app updates (exit code 42 means an update finished and app needs restart)
while :; do
	python3 -u "$APP_DIR/app/main.py" >/tmp/controllermode.log 2>&1
	EXIT_STATUS=$?
	[ "$EXIT_STATUS" -eq 42 ] || break
done

cp /tmp/controllermode.log "$APP_DIR/logs/app.log" 2>/dev/null

# Clean up any residual USB gadget state if interrupted
if [ -d /sys/kernel/config/usb_gadget/controller-mode ]; then
	[ -e /sys/kernel/config/usb_gadget/controller-mode/UDC ] && printf '\n' >/sys/kernel/config/usb_gadget/controller-mode/UDC 2>/dev/null
	rm -f /sys/kernel/config/usb_gadget/controller-mode/configs/c.1/hid.usb0 2>/dev/null
	rmdir /sys/kernel/config/usb_gadget/controller-mode/configs/c.1/strings/0x409 2>/dev/null
	rmdir /sys/kernel/config/usb_gadget/controller-mode/configs/c.1 2>/dev/null
	rmdir /sys/kernel/config/usb_gadget/controller-mode/functions/hid.usb0 2>/dev/null
	rmdir /sys/kernel/config/usb_gadget/controller-mode/strings/0x409 2>/dev/null
	rmdir /sys/kernel/config/usb_gadget/controller-mode 2>/dev/null
fi

sync
