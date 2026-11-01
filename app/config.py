#!/usr/bin/env python3
"""
ControllerMode-MuOS - Configuration & Settings
"""
import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/controllermode")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULT_CONFIG = {
    "default_mode": "usb_pc",         # usb_pc, usb_switch, bt_ble, bt_switch
    "button_layout": "xbox",          # xbox, nintendo
    "battery_saver": True,            # dim/turn off display after timeout
    "saver_timeout_seconds": 30,      # seconds before screen dim/off
    "deadzone_percent": 8,            # analog stick deadzone %
    "rumble_enabled": True,           # enable rumble feedback
    "rumble_strength": 80,            # 0 - 100%
    "swap_triggers": False,           # swap L1/L2 and R1/R2
}

def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                config.update(data)
        except Exception as e:
            print(f"[Config] Error loading config: {e}")
    return config

def save_config(config: dict) -> bool:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"[Config] Error saving config: {e}")
        return False
