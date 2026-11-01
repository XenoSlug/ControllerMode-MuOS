#!/usr/bin/env python3
"""
ControllerMode-MuOS - PySDL2 UI & Live Status Screen
Renders menu and real-time controller status with pixel art on 640x480 RG35XX H display.
"""
import ctypes
import os
import sys
import time

try:
    import sdl2
    import sdl2.ext
    import sdl2.sdlttf as ttf
    HAS_SDL2 = True
except ImportError:
    HAS_SDL2 = False

SCREEN_W = 640
SCREEN_H = 480

# Palette
C_BG        = (18, 20, 24)
C_PANEL     = (28, 32, 40)
C_ACCENT    = (0, 180, 160)
C_ACCENT2   = (255, 170, 0)
C_WHITE     = (240, 240, 245)
C_GRAY      = (120, 125, 135)
C_DARKGRAY  = (45, 50, 60)
C_ACTIVE    = (0, 230, 180)
C_ACTIVE_BTN= (255, 80, 80)
C_BORDER    = (60, 68, 82)

class UIRenderer:
    def __init__(self):
        self.window = None
        self.renderer = None
        self.font = None
        self.font_small = None
        self.initialized = False
        self._init_sdl()

    def _init_sdl(self):
        if not HAS_SDL2:
            print("[UI] PySDL2 not installed, running in headless mode.")
            return

        os.environ["PYSDL2_DLL_PATH"] = "/usr/lib"
        sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_EVENTS)
        ttf.TTF_Init()

        self.window = sdl2.SDL_CreateWindow(
            b"ControllerMode-MuOS",
            sdl2.SDL_WINDOWPOS_CENTERED,
            sdl2.SDL_WINDOWPOS_CENTERED,
            SCREEN_W,
            SCREEN_H,
            sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_FULLSCREEN
        )
        self.renderer = sdl2.SDL_CreateRenderer(
            self.window, -1, sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC
        )

        # Load system font
        font_paths = [
            "/opt/muos/share/theme/MustardOS/font/source.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        chosen_font = None
        for p in font_paths:
            if os.path.exists(p):
                chosen_font = p.encode("utf-8")
                break

        if chosen_font:
            self.font = ttf.TTF_OpenFont(chosen_font, 20)
            self.font_small = ttf.TTF_OpenFont(chosen_font, 14)
            self.font_large = ttf.TTF_OpenFont(chosen_font, 26)
        else:
            self.font = None
            self.font_small = None
            self.font_large = None

        self.initialized = True

    def set_color(self, color):
        sdl2.SDL_SetRenderDrawColor(self.renderer, color[0], color[1], color[2], 255)

    def draw_rect(self, x, y, w, h, color, fill=True):
        self.set_color(color)
        r = sdl2.SDL_Rect(int(x), int(y), int(w), int(h))
        if fill:
            sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(r))
        else:
            sdl2.SDL_RenderDrawRect(self.renderer, ctypes.byref(r))

    def draw_circle(self, cx, cy, radius, color, fill=True):
        self.set_color(color)
        r = int(radius)
        for y in range(-r, r + 1):
            for x in range(-r, r + 1):
                if x * x + y * y <= r * r:
                    if fill or (x * x + y * y >= (r - 2) * (r - 2)):
                        sdl2.SDL_RenderDrawPoint(self.renderer, int(cx + x), int(cy + y))

    def draw_text(self, text: str, x: int, y: int, color=C_WHITE, font_type="normal", center_x=False):
        if not self.initialized:
            return
        f = self.font
        if font_type == "small" and self.font_small:
            f = self.font_small
        elif font_type == "large" and self.font_large:
            f = self.font_large

        if not f:
            return

        c = sdl2.SDL_Color(color[0], color[1], color[2], 255)
        surf = ttf.TTF_RenderUTF8_Blended(f, text.encode("utf-8"), c)
        if not surf:
            return
        tex = sdl2.SDL_CreateTextureFromSurface(self.renderer, surf)
        w = surf.contents.w
        h = surf.contents.h
        sdl2.SDL_FreeSurface(surf)

        draw_x = x - (w // 2) if center_x else x
        rect = sdl2.SDL_Rect(int(draw_x), int(y), int(w), int(h))
        sdl2.SDL_RenderCopy(self.renderer, tex, None, ctypes.byref(rect))
        sdl2.SDL_DestroyTexture(tex)

    def render_menu(self, items: list, selected_index: int, layout_name: str, saver_on: bool):
        if not self.initialized:
            return

        self.draw_rect(0, 0, SCREEN_W, SCREEN_H, C_BG)

        # Header banner
        self.draw_rect(0, 0, SCREEN_W, 64, C_PANEL)
        self.draw_rect(0, 63, SCREEN_W, 2, C_ACCENT)
        self.draw_text("ControllerMode-MuOS", SCREEN_W // 2, 14, C_WHITE, "large", center_x=True)
        self.draw_text("Turn your RG35XX H into a USB & Bluetooth Gamepad", SCREEN_W // 2, 42, C_GRAY, "small", center_x=True)

        # Menu cards
        start_y = 85
        card_h = 56
        gap = 12

        for i, item in enumerate(items):
            y = start_y + i * (card_h + gap)
            is_sel = (i == selected_index)

            bg_col = (36, 44, 58) if is_sel else C_PANEL
            border_col = C_ACCENT if is_sel else C_BORDER

            self.draw_rect(40, y, SCREEN_W - 80, card_h, bg_col, fill=True)
            self.draw_rect(40, y, SCREEN_W - 80, card_h, border_col, fill=False)

            if is_sel:
                self.draw_rect(40, y, 6, card_h, C_ACCENT, fill=True)

            title_col = C_WHITE if is_sel else (200, 205, 215)
            self.draw_text(item["title"], 64, y + 10, title_col, "normal")
            self.draw_text(item["desc"], 64, y + 32, C_GRAY, "small")

            # Selection marker
            if is_sel:
                self.draw_text("►", SCREEN_W - 70, y + 18, C_ACCENT, "normal")

        # Bottom info bar
        self.draw_rect(0, SCREEN_H - 40, SCREEN_W, 40, C_PANEL)
        self.draw_rect(0, SCREEN_H - 41, SCREEN_W, 1, C_BORDER)
        help_text = f"Layout: {layout_name.upper()}  |  Battery Saver: {'ON' if saver_on else 'OFF'}  |  [A] Select  [START] Launch"
        self.draw_text(help_text, SCREEN_W // 2, SCREEN_H - 28, C_GRAY, "small", center_x=True)

        sdl2.SDL_RenderPresent(self.renderer)

    def render_active_screen(self, mode_title: str, state, reports_per_sec: int, is_dark: bool = False):
        if not self.initialized:
            return

        if is_dark:
            # Battery saver display blanking
            self.draw_rect(0, 0, SCREEN_W, SCREEN_H, (0, 0, 0), fill=True)
            sdl2.SDL_RenderPresent(self.renderer)
            return

        self.draw_rect(0, 0, SCREEN_W, SCREEN_H, C_BG)

        # Top Bar
        self.draw_rect(0, 0, SCREEN_W, 46, C_PANEL)
        self.draw_rect(0, 45, SCREEN_W, 1, C_BORDER)
        self.draw_text(mode_title, 20, 12, C_ACCENT, "large")
        status_txt = f"{reports_per_sec} r/s  |  Active"
        self.draw_text(status_txt, SCREEN_W - 130, 16, C_WHITE, "small")

        # Handheld Console Body (RG35XX H proportions)
        bx, by, bw, bh = 60, 68, 520, 310
        self.draw_rect(bx, by, bw, bh, (32, 36, 46), fill=True)
        self.draw_rect(bx, by, bw, bh, C_BORDER, fill=False)

        # Console Screen Frame
        sw, sh = 240, 180
        sx, sy = bx + (bw - sw) // 2, by + 30
        self.draw_rect(sx, sy, sw, sh, (14, 16, 20), fill=True)
        self.draw_rect(sx, sy, sw, sh, C_BORDER, fill=False)

        # Content on virtual screen
        self.draw_text("CONNECTED", sx + sw // 2, sy + 40, C_ACTIVE, "normal", center_x=True)
        self.draw_text("Controls forwarded to Host", sx + sw // 2, sy + 75, C_GRAY, "small", center_x=True)
        self.draw_text("OTG2/DC: Charging & Active", sx + sw // 2, sy + 105, (180, 200, 220), "small", center_x=True)

        # Shoulder Buttons L1/L2 and R1/R2
        col_l1 = C_ACTIVE if state.l1 else C_DARKGRAY
        col_l2 = C_ACTIVE if state.l2 else C_DARKGRAY
        col_r1 = C_ACTIVE if state.r1 else C_DARKGRAY
        col_r2 = C_ACTIVE if state.r2 else C_DARKGRAY
        self.draw_rect(bx + 40, by - 12, 60, 12, col_l2)
        self.draw_rect(bx + 110, by - 12, 60, 12, col_l1)
        self.draw_rect(bx + bw - 170, by - 12, 60, 12, col_r1)
        self.draw_rect(bx + bw - 100, by - 12, 60, 12, col_r2)

        # D-Pad (Left side)
        dx, dy = bx + 70, by + 100
        size = 28
        col_up = C_ACTIVE if state.dpad_up else C_DARKGRAY
        col_dn = C_ACTIVE if state.dpad_down else C_DARKGRAY
        col_lt = C_ACTIVE if state.dpad_left else C_DARKGRAY
        col_rt = C_ACTIVE if state.dpad_right else C_DARKGRAY

        self.draw_rect(dx, dy - size, size, size, col_up)
        self.draw_rect(dx, dy + size, size, size, col_dn)
        self.draw_rect(dx - size, dy, size, size, col_lt)
        self.draw_rect(dx + size, dy, size, size, col_rt)
        self.draw_rect(dx, dy, size, size, (50, 56, 70))

        # Face Buttons (Right side: X, Y, A, B)
        fx, fy = bx + bw - 85, by + 100
        btn_r = 14
        col_a = C_ACTIVE_BTN if state.a else C_DARKGRAY
        col_b = C_ACTIVE_BTN if state.b else C_DARKGRAY
        col_x = C_ACTIVE_BTN if state.x else C_DARKGRAY
        col_y = C_ACTIVE_BTN if state.y else C_DARKGRAY

        self.draw_circle(fx + 30, fy, btn_r, col_b)      # Right (B / A)
        self.draw_circle(fx - 30, fy, btn_r, col_x)      # Left (X / Y)
        self.draw_circle(fx, fy - 30, btn_r, col_y)      # Top (Y / X)
        self.draw_circle(fx, fy + 30, btn_r, col_a)      # Bottom (A / B)

        # Left Analog Stick
        lsx, lsy = bx + 84, by + 230
        self.draw_circle(lsx, lsy, 32, (20, 22, 28), fill=True)
        self.draw_circle(lsx, lsy, 32, C_BORDER, fill=False)
        off_lx = int((state.lx / 32767.0) * 16)
        off_ly = int((state.ly / 32767.0) * 16)
        col_l3 = C_ACTIVE if state.l3 else (80, 88, 105)
        self.draw_circle(lsx + off_lx, lsy + off_ly, 16, col_l3, fill=True)

        # Right Analog Stick
        rsx, rsy = bx + bw - 84, by + 230
        self.draw_circle(rsx, rsy, 32, (20, 22, 28), fill=True)
        self.draw_circle(rsx, rsy, 32, C_BORDER, fill=False)
        off_rx = int((state.rx / 32767.0) * 16)
        off_ry = int((state.ry / 32767.0) * 16)
        col_r3 = C_ACTIVE if state.r3 else (80, 88, 105)
        self.draw_circle(rsx + off_rx, rsy + off_ry, 16, col_r3, fill=True)

        # Select, Menu, Start
        col_sel = C_ACTIVE if state.select else C_DARKGRAY
        col_menu = C_ACTIVE if state.menu else C_DARKGRAY
        col_sta = C_ACTIVE if state.start else C_DARKGRAY
        self.draw_rect(sx + 30, by + 240, 36, 12, col_sel)
        self.draw_rect(sx + sw // 2 - 18, by + 240, 36, 12, col_menu)
        self.draw_rect(sx + sw - 66, by + 240, 36, 12, col_sta)

        # Bottom Exit Banner
        self.draw_rect(0, SCREEN_H - 44, SCREEN_W, 44, (12, 14, 18))
        self.draw_rect(0, SCREEN_H - 45, SCREEN_W, 1, C_BORDER)
        self.draw_text("Hold SELECT + START + L1 + R1 (or MENU) to Exit back to muOS", SCREEN_W // 2, SCREEN_H - 30, C_ACCENT2, "small", center_x=True)

        sdl2.SDL_RenderPresent(self.renderer)

    def close(self):
        if self.initialized:
            if self.font:
                ttf.TTF_CloseFont(self.font)
            if self.font_small:
                ttf.TTF_CloseFont(self.font_small)
            if self.font_large:
                ttf.TTF_CloseFont(self.font_large)
            ttf.TTF_Quit()
            if self.renderer:
                sdl2.SDL_DestroyRenderer(self.renderer)
            if self.window:
                sdl2.SDL_DestroyWindow(self.window)
            sdl2.SDL_Quit()
            self.initialized = False
