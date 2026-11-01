# ControllerMode-MuOS 🎮

Turn your **Anbernic RG35XX H** running **muOS (MustardOS)** into a responsive wired USB and wireless Bluetooth game controller for PC, Mac, Nintendo Switch, Android, and iOS devices.

Inspired by and ported from the external controller features in [Handtop](https://github.com/junsulee119/handtop).

---

## ✨ Features

- **🔌 Wired USB Controller Modes (Right USB-C port: OTG2/DC)**:
  - **PC / Mac / Android / Linux**: Standard USB HID Gamepad (16 buttons, 8-way D-Pad, 4 analog axes, dual analog triggers). Works plug-and-play without special drivers. The handheld charges from the host while you play!
  - **Nintendo Switch (HORI Pokken DX)**: Emulates a licensed HORI Pro Controller. Plug straight into the Switch Dock or Switch USB-C port.
- **📶 Wireless Bluetooth Modes**:
  - **PC / Mac / iOS / Android**: Bluetooth LE Gamepad (Xbox Wireless Controller profile). Pairs instantly without entering PINs.
  - **Nintendo Switch**: Bluetooth classic Switch Pro Controller mode.
- **🎨 Live Visual Status Screen**:
  - Rendered natively in 640×480 on the RG35XX H display.
  - Features real-time pixel art of the console displaying every button press, D-pad tilt, and analog stick displacement as you play.
- **🔋 Battery Saver Mode**:
  - When enabled, turns the handheld's display off after 30 seconds of play to save battery and prevent screen burn.
  - Wakes up instantly upon pressing the Volume or Power button.
- **🔀 Layout Toggle**:
  - Switch freely between **Xbox layout** (A at bottom) and **Nintendo layout** (B at bottom).
- **📳 Safe Haptic Rumble**:
  - Translates host vibration commands to the RG35XX H internal vibration motor with rate-limiting to protect hardware.
- **🔄 In-App One-Click Updater**:
  - Automatically checks GitHub Releases for new updates in the background.
  - Shows an update notification banner in the menu when an update is available.
  - Downloads, verifies, installs the update, and restarts the app with zero manual intervention required.
- **🛑 Seamless Exit Combo**:
  - Hold **Select + Start + L1 + R1** (or the **Menu / F** button) for 1.2 seconds at any time to cleanly disconnect USB/BT and return to muOS.

---

## 🚀 Installation on muOS

### Option 1: Using the `.muxapp` Package (Recommended)

1. Download the latest `ControllerMode-MuOS.muxapp` from [Releases](https://github.com/XenoSlug/ControllerMode-MuOS/releases).
2. Insert your SD card into your computer and copy `ControllerMode-MuOS.muxapp` into the `ARCHIVE` folder (either on SD1 `mmc` or SD2 `sdcard`).
3. Boot your RG35XX H and navigate to **Applications → Archive Manager** to install.
4. Launch **Controller Mode** from the muOS Applications menu!

### Option 2: Manual Installation

1. Clone or download this repository.
2. Copy the folder to your SD card under:
   ```
   /mnt/mmc/MUOS/application/ControllerMode/
   ```
   (or `/mnt/sdcard/MUOS/application/ControllerMode/`)
3. Ensure `mux_launch.sh` has executable permissions:
   ```sh
   chmod +x /mnt/mmc/MUOS/application/ControllerMode/mux_launch.sh
   ```

---

## 🕹️ Controls & Navigation

### Inside the Menu:
| Button | Action |
|---|---|
| **D-Pad Up / Down** | Navigate menu options |
| **A / Start** | Select / Launch mode |
| **B** | Back / Exit |

### During Controller Play:
| Action | Description |
|---|---|
| **All buttons & sticks** | Forwarded to connected PC, Mac, Phone, or Switch |
| **Volume Up / Down / Power** | Adjust volume or wake display in Battery Saver mode |
| **Select + Start + L1 + R1 (Hold 1.2s)** | **Exit session** cleanly and return to muOS |
| **Menu / F Button (Hold 1.2s)** | Quick exit shortcut |

---

## 🛠️ Requirements & Architecture

- **Operating System**: muOS (MustardOS) Banana, Beans, Goose, or newer on Anbernic RG35XX H (Allwinner H700).
- **Display**: 640×480 framebuffer rendering via PySDL2.
- **Input**: Linux `evdev` interface capturing `/dev/input/event*` with exclusive grab (`EVIOCGRAB`).
- **USB Subsystem**: Linux kernel ConfigFS (`/sys/kernel/config/usb_gadget/`) utilizing the H700 MUSB controller on `5100000.udc-controller`.
- **Bluetooth Subsystem**: BlueZ D-Bus & L2CAP protocols.

---

## 📜 Credits & Disclaimer

- Based on external controller concepts, USB gadget configurations, and Bluetooth protocol research from [Handtop](https://github.com/junsulee119/handtop) by Junsu Lee.
- Built for the muOS retro handheld community.
- *Disclaimer: Device names and controller trademarks (Microsoft Xbox, Nintendo Switch, HORI) belong to their respective owners and are mentioned solely for compatibility identification.*

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
