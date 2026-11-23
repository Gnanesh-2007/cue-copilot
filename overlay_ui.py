"""
Cue Copilot HUD - V6 "Pro"
  [ pill bar ]  status + waveform | Answer F7 | Screen F9 | Read F11 | Hide F8 | settings | collapse | close
  [ panel ]     tabs (Answer · Transcript · History) with model / latency / mode chips
                question card (who asked) -> answer card (toolbar: Shorter Deeper Example Simpler Copy)
                suggestion chips + Ask bar, footer with history navigation and hotkeys
- Fluent icons, Segoe UI Variable type, keycap badges, "thinking" timer, answer typography
  (lead sentence, headings, bullets, code blocks), 6 themes, teleprompter, region capture.
- Hotkeys (global): F6 Auto/Manual · F7 Answer · F8 Hide/Show · F9 Screen · F10 Click-through
                    F11 Teleprompter · F12 Region capture
- Hidden from screen share (WDA_EXCLUDEFROMCAPTURE), from Taskbar/Alt+Tab, never steals focus.
  Set env CUE_NOSTEALTH=1 while designing so screenshots can see it.
"""
import customtkinter as ctk
import ctypes
import json
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000   # clicking the HUD never steals focus from the meeting window

VK_F6, VK_F7, VK_F8, VK_F9, VK_F10, VK_F11, VK_F12 = 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x7B

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

KEY = "#010203"          # color-key: makes the gap between pill and panel see-through
C = {}
_DARK_TEXT = dict(text="#ECEFF6", dim="#9099AE", faint="#5E6780", code_fg="#D3E4FF", on_accent="#FFFFFF")
_STATE = dict(good="#4ECCA3", warn="#FBBF24", think="#A78BFA", info="#38BDF8", err="#FF5C6C")
THEMES = {
    "Glass":    dict(card="#0A0D14", panel="#121722", panel2="#1C2333", border="#3A4560",
                     accent="#8AB4FF", code_bg="#07090F", **_DARK_TEXT),
    "Midnight": dict(card="#0B0E14", panel="#131721", panel2="#1B202D", border="#262C3B",
                     accent="#7C9BFF", code_bg="#080A0F", **_DARK_TEXT),
    "Ocean":    dict(card="#081118", panel="#0E1B25", panel2="#152838", border="#1D3A50",
                     accent="#38BDF8", code_bg="#050C11", **_DARK_TEXT),
    "Emerald":  dict(card="#091311", panel="#0F1E18", panel2="#162C22", border="#1F3D30",
                     accent="#34D399", code_bg="#060F0B", **_DARK_TEXT),
    "Sunset":   dict(card="#140E11", panel="#1E1418", panel2="#2B1C22", border="#43262F",
                     accent="#FB7185", code_bg="#0D0709", **_DARK_TEXT),
    "Violet":   dict(card="#0F0C18", panel="#181325", panel2="#241B36", border="#392A55",
                     accent="#A78BFA", code_bg="#0A0711", **_DARK_TEXT),
    "Paper":    dict(card="#F3F5FA", panel="#FFFFFF", panel2="#E8ECF4", border="#D3D9E7",
                     accent="#4F6BED", code_bg="#EEF2FA", text="#171C2C", dim="#55607A",
                     faint="#8C95AB", code_fg="#223A6A", on_accent="#FFFFFF"),
}
THEME_ORDER = list(THEMES)


def _set_theme_colors(name):
    C.clear()
    C.update(_STATE)
    C.update(THEMES[name])


_set_theme_colors("Midnight")

HEADINGS = {"DIRECT ANSWER", "KEY POINTS", "APPROACH", "CODE", "WHAT THIS SHOWS", "FIX",
            "HR QUESTIONS", "TECHNICAL QUESTIONS", "PROJECT QUESTIONS", "THEORY QUESTIONS",
            "CORE QUESTIONS", "SCENARIO QUESTIONS"}
LEAD_HEADINGS = {"DIRECT ANSWER", "WHAT THIS SHOWS", "APPROACH"}
OPACITY_STEPS = [1.0, 0.90, 0.78, 0.62]
UI_FONT = "Segoe UI"            # upgraded at runtime to Segoe UI Variable when present
DISPLAY_FONT = "Segoe UI"
ICON_FONT = "Segoe MDL2 Assets"
MONO_FONT = "Cascadia Code"
PILL_H = 52
SUGGESTIONS = (
    ("What should I say?", "What should I say next? Give me the exact words."),
    ("Give an example", "Give one concrete real-world example for the last question."),
    ("Likely follow-up", "What follow-up question is the interviewer likely to ask next, and how should I answer it?"),
)
HINTS = (("F6", "Auto"), ("F10", "Click-through"), ("F12", "Region"))

# Segoe Fluent Icons / MDL2 code points
I = dict(bolt="", screen="", read="", hide="", gear="", down="",
         up="", close="", copy="", check="", send="", left="",
         right="", clock="", mic="", chat="", doc="", eye="",
         play="", pause="", restart="", history="", auto="", hand="")


def _pick_fonts():
    global UI_FONT, DISPLAY_FONT, ICON_FONT, MONO_FONT
    try:
        fam = set(tkfont.families())
    except Exception:
        return
    if "Segoe UI Variable Text" in fam:
        UI_FONT = "Segoe UI Variable Text"
    if "Segoe UI Variable Display" in fam:
        DISPLAY_FONT = "Segoe UI Variable Display"
    if "Segoe Fluent Icons" in fam:
        ICON_FONT = "Segoe Fluent Icons"
    if "Cascadia Code" not in fam:
        MONO_FONT = "Consolas"


def _get_top_hwnd(widget) -> int:
    user32 = ctypes.windll.user32
    hwnd = widget.winfo_id()
    parent = user32.GetParent(hwnd)
    return parent if parent else hwnd


def apply_full_stealth(hwnd: int, click_through: bool = False, no_activate: bool = True):
    try:
        user32 = ctypes.windll.user32
        if not os.environ.get("CUE_NOSTEALTH"):
            user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex_style |= WS_EX_TOOLWINDOW | WS_EX_LAYERED
        ex_style &= ~WS_EX_APPWINDOW
        if click_through:
            ex_style |= WS_EX_TRANSPARENT
        else:
            ex_style &= ~WS_EX_TRANSPARENT
        if no_activate:
            ex_style |= WS_EX_NOACTIVATE
        else:
            ex_style &= ~WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
    except Exception as e:
        print(f"[Stealth] Hook error: {e}")


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * t):02X}" for x, y in zip(a, b))


def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _bind_as_button(frame, widgets, enter, leave, click):
    """Make a frame + its child labels behave like one button (hover + click), without flicker."""
    def inside(e):
        try:
            w = frame.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return False
        while w is not None:
            if w is frame:
                return True
            w = getattr(w, "master", None)
        return False

    for w in [frame] + list(widgets):
        w.bind("<Enter>", lambda e: enter())
        w.bind("<Leave>", lambda e: None if inside(e) else leave())
        w.bind("<Button-1>", lambda e: click())
        try:
            w.configure(cursor="hand2")
        except Exception:
            pass


class PillButton:
    """Icon + label + keycap badge, e.g.  [⚡ Answer  F7]"""

    def __init__(self, hud, parent, icon, text, key, command):
        self.active = False
        self.hover = False
        self.f = ctk.CTkFrame(parent, corner_radius=17, height=34, fg_color=C["panel"])
        self.ic = ctk.CTkLabel(self.f, text=icon, font=hud._ifont(13), width=14, height=26)
        self.tx = ctk.CTkLabel(self.f, text=text, font=hud._font(12, "bold"), height=26)
        self.kc = ctk.CTkLabel(self.f, text=key, font=hud._font(9, "bold"), width=26, height=18,
                               corner_radius=5)
        self.ic.pack(side="left", padx=(11, 5), pady=4)
        self.tx.pack(side="left", pady=4)
        self.kc.pack(side="left", padx=(7, 7), pady=4)
        _bind_as_button(self.f, [self.ic, self.tx, self.kc], self._enter, self._leave, command)
        self.retheme()

    def _enter(self):
        self.hover = True
        self.retheme()

    def _leave(self):
        self.hover = False
        self.retheme()

    def set_active(self, on: bool):
        self.active = on
        self.retheme()

    def set_compact(self, on: bool):
        """Narrow window: show only icon + keycap."""
        if on:
            self.tx.pack_forget()
        elif not self.tx.winfo_manager():
            self.tx.pack(side="left", pady=4, before=self.kc)

    def retheme(self):
        if self.active:
            bg, fg, ic = C["accent"], C["on_accent"], C["on_accent"]
            kbg, kfg = _mix(C["accent"], "#000000", 0.22), C["on_accent"]
        else:
            bg = C["panel2"] if self.hover else C["panel"]
            fg, ic = C["text"], C["accent"]
            kbg, kfg = (C["border"] if self.hover else C["panel2"]), C["dim"]
        self.f.configure(fg_color=bg)
        for w in (self.ic, self.tx):
            w.configure(fg_color=bg)
        self.ic.configure(text_color=ic)
        self.tx.configure(text_color=fg)
        self.kc.configure(fg_color=kbg, text_color=kfg, bg_color=bg)


class Chip:
    """Small rounded status chip with an icon:  (⚡ qwen2.5:7b)"""

    def __init__(self, hud, parent, icon, text, color_key="dim", click=None):
        self.color_key = color_key
        self.f = ctk.CTkFrame(parent, corner_radius=12, height=24, fg_color=C["panel"])
        self.ic = ctk.CTkLabel(self.f, text=icon, font=hud._ifont(10), width=12, height=20)
        self.tx = ctk.CTkLabel(self.f, text=text, font=hud._font(10, "bold"), height=20)
        self.ic.pack(side="left", padx=(10, 5), pady=2)
        self.tx.pack(side="left", padx=(0, 10), pady=2)
        if click:
            _bind_as_button(self.f, [self.ic, self.tx], lambda: self._hover(True), lambda: self._hover(False), click)
        self.retheme()

    def _hover(self, on):
        bg = C["panel2"] if on else C["panel"]
        for w in (self.f, self.ic, self.tx):
            w.configure(fg_color=bg)

    def set(self, text=None, color_key=None, icon=None):
        if text is not None:
            self.tx.configure(text=text)
        if icon is not None:
            self.ic.configure(text=icon)
        if color_key is not None:
            self.color_key = color_key
        self.retheme()

    def retheme(self):
        col = C.get(self.color_key, self.color_key)
        for w in (self.f, self.ic, self.tx):
            w.configure(fg_color=C["panel"])
        self.ic.configure(text_color=col)
        self.tx.configure(text_color=col if self.color_key != "dim" else C["dim"])


class StealthHUD(ctk.CTk):
    def __init__(self, on_force_answer=None, on_screen_ocr=None, on_clear_memory=None,
                 on_refine=None, on_mode_change=None, on_region_capture=None, on_ask=None,
                 on_mic_toggle=None, on_profile_save=None, on_resume_pick=None, on_resume_clear=None,
                 on_predict=None):
        super().__init__()
        _pick_fonts()
        self.on_profile_save = on_profile_save
        self.on_resume_pick = on_resume_pick
        self.on_resume_clear = on_resume_clear
        self.on_predict = on_predict
        self._meta_text = "model"
        self._profile_sum = ""
        self.on_mic_toggle = on_mic_toggle
        self.on_force_answer = on_force_answer
        self.on_screen_ocr = on_screen_ocr
        self.on_clear_memory = on_clear_memory
        self.on_refine = on_refine
        self.on_mode_change = on_mode_change
        self.on_region_capture = on_region_capture
        self.on_ask = on_ask

        s = _load_settings()
        self._theme = s.get("theme") if s.get("theme") in THEMES else "Midnight"
        _set_theme_colors(self._theme)
        self._fsize = int(s.get("fsize", 13))
        self._opacity_idx = int(s.get("opacity_idx", 0)) % len(OPACITY_STEPS)
        self._wpm = int(s.get("wpm", 140))
        self._panel_open = bool(s.get("panel_open", True))
        self.mic_on = bool(s.get("mic_on", True))     # "You" microphone capture

        self.is_visible = True
        self.click_through = False
        self.auto_mode = True
        self._running = True
        self._level = 0.0
        self._level_smooth = 0.0
        self._status_color = C["good"]
        self._active_state = False
        self._phase = 0.0
        self._ticks = 0
        self._t0_session = time.time()
        self._t_think = None
        self._first_token_pending = False
        self._waiting = False          # answer card shows the animated "Thinking" placeholder
        self._wait_t0 = 0.0
        self._next_src = None
        self._history: list[dict] = []
        self._view_idx = -1
        self._restyle_job = None
        self._save_job = None
        self._toast_job = None
        self._saved_h = 540
        self._log_dir = None
        self._sel_win = None
        self._typing = False          # True only while the Ask box has keyboard focus
        self._prev_fg = None          # window to hand focus back to after typing
        self._user_sized = False      # user dragged the resize grip -> stop auto-sizing
        self._autosize_job = None
        self._as_again = False
        self._tab = "Answer"
        self._live_line_start = None
        self._themed = []             # (widget, {option: color key}) re-applied on theme change
        self._custom = []             # objects with .retheme()
        self.tele_mode = False
        self._tele_playing = True
        self._tele_idx = 0
        self._tele_job = None
        self._pre_tele_fsize = self._fsize

        self.title("")
        geom = s.get("geom") or "760x540+100+36"
        if not re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geom):
            geom = "760x540+100+36"
        self.geometry(geom)
        self.minsize(640, PILL_H + 8)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", OPACITY_STEPS[self._opacity_idx])
        self.configure(fg_color=KEY)
        try:
            self.attributes("-transparentcolor", KEY)
        except Exception:
            pass

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build()
        if not self._panel_open:
            self.after(60, lambda: self.toggle_panel(force=False))
        self.after(150, self.setup_stealth)
        self.after(50, self._tick)
        self._hotkey_thread = threading.Thread(target=self._global_hotkey_loop, daemon=True)
        self._hotkey_thread.start()

    # ================================================================== UI helpers
    def _font(self, size=11, weight="normal", slant="roman", family=None):
        return ctk.CTkFont(family=family or UI_FONT, size=size, weight=weight, slant=slant)

    def _ifont(self, size=12):
        return ctk.CTkFont(family=ICON_FONT, size=size)

    def _reg(self, widget, **opts):
        """Remember which theme colors a widget uses so set_theme() can repaint it."""
        self._themed.append((widget, opts))
        widget.configure(**{k: (C[v] if v in C else v) for k, v in opts.items()})
        return widget

    def _icon_btn(self, parent, icon, cmd, size=13, width=30, hover="panel2", color="dim"):
        b = ctk.CTkButton(parent, text=icon, width=width, height=30, corner_radius=15,
                          font=self._ifont(size), command=cmd, fg_color="transparent")
        return self._reg(b, hover_color=hover, text_color=color)

    def _ghost_btn(self, parent, text, cmd, width=10):
        b = ctk.CTkButton(parent, text=text, width=width, height=24, corner_radius=12,
                          font=self._font(10, "bold"), command=cmd)
        return self._reg(b, fg_color="transparent", hover_color="panel2", text_color="dim")

    def _keycap(self, parent, key, bg="panel"):
        k = ctk.CTkLabel(parent, text=key, font=self._font(8, "bold"), width=24, height=16, corner_radius=4)
        return self._reg(k, fg_color=bg, text_color="dim")

    # ====================================================================== build
    def _build(self):
        # ---------------------------------------------------------- PILL BAR
        self.pill = ctk.CTkFrame(self, corner_radius=PILL_H // 2, height=PILL_H, border_width=1)
        self._reg(self.pill, fg_color="card", border_color="border")
        self.pill.pack(fill="x", pady=(0, 8))
        self.pill.pack_propagate(False)

        left = ctk.CTkFrame(self.pill, fg_color="transparent")
        left.pack(side="left", padx=(16, 0), fill="y")
        self.dot = tk.Canvas(left, width=16, height=16, highlightthickness=0)
        self._reg(self.dot, bg="card")
        self.dot.pack(side="left", pady=17)
        self._dot_glow = self.dot.create_oval(1, 1, 15, 15, fill=_mix(C["card"], C["good"], .25), outline="")
        self._dot_core = self.dot.create_oval(4, 4, 12, 12, fill=C["good"], outline="")
        self.status_lbl = ctk.CTkLabel(left, text="Listening", text_color=C["good"],
                                       font=self._font(12, "bold"), width=80, anchor="w")
        self.status_lbl.pack(side="left", padx=(8, 0))
        self.wave = tk.Canvas(left, width=54, height=26, highlightthickness=0)
        self._reg(self.wave, bg="card")
        self.wave.pack(side="left", pady=13)
        self._bars = [self.wave.create_line(0, 0, 0, 0, fill=C["accent"], width=3, capstyle="round")
                      for _ in range(9)]

        right = ctk.CTkFrame(self.pill, fg_color="transparent")
        right.pack(side="right", padx=(4, 12), fill="y")
        self.close_btn = self._icon_btn(right, I["close"], self.quit_app, size=11, hover="err")
        self.close_btn.pack(side="right", pady=11)
        self.chev_btn = self._icon_btn(right, I["down"], lambda: self.toggle_panel(), size=11)
        self.chev_btn.pack(side="right", pady=11, padx=(0, 2))
        self.menu_btn = self._icon_btn(right, I["gear"], self.open_menu, size=13)
        self.menu_btn.pack(side="right", pady=11, padx=(0, 2))
        sep = ctk.CTkFrame(right, width=1, height=22)
        self._reg(sep, fg_color="border")
        sep.pack(side="right", padx=(6, 8), pady=15)

        mid = ctk.CTkFrame(self.pill, fg_color="transparent")
        mid.pack(side="left", expand=True, fill="both", padx=4)
        inner = ctk.CTkFrame(mid, fg_color="transparent")
        inner.pack(expand=True)
        self.btn_answer = PillButton(self, inner, I["bolt"], "Answer", "F7",
                                     lambda: self._thread(self.on_force_answer))
        self.btn_screen = PillButton(self, inner, I["screen"], "Screen", "F9",
                                     lambda: self._thread(self.on_screen_ocr))
        self.btn_read = PillButton(self, inner, I["read"], "Read", "F11", self.toggle_tele)
        self.btn_hide = PillButton(self, inner, I["hide"], "Hide", "F8", self.toggle_visibility)
        for b in (self.btn_answer, self.btn_screen, self.btn_read, self.btn_hide):
            b.f.pack(side="left", padx=2, pady=9)
            self._custom.append(b)

        self._pill_parts = (left, inner, right)
        self._pill_compact = False
        self._pill_full_w = None
        self.pill.bind("<Configure>", self._fit_pill, add="+")

        for w in (self.pill, left, self.status_lbl, mid, inner, self.dot, self.wave):
            w.bind("<Button-1>", self.start_move)
            w.bind("<B1-Motion>", self.do_move)
            w.bind("<ButtonRelease-1>", lambda e: self._save_settings_soon())

        # ------------------------------------------------------------ PANEL
        self.panel = ctk.CTkFrame(self, corner_radius=20, border_width=1)
        self._reg(self.panel, fg_color="card", border_color="border")
        self.panel.pack(fill="both", expand=True)

        # header: underline tabs + chips
        top = ctk.CTkFrame(self.panel, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 0))
        tabs = ctk.CTkFrame(top, fg_color="transparent")
        tabs.pack(side="left")
        self._tab_w = {}
        for name in ("Answer", "Transcript", "History", "Setup"):
            f = ctk.CTkFrame(tabs, fg_color="transparent")
            f.pack(side="left", padx=(0, 18))
            lbl = ctk.CTkLabel(f, text=name, font=self._font(12, "bold"), height=24, cursor="hand2")
            lbl.pack()
            bar = ctk.CTkFrame(f, height=3, width=24, corner_radius=2)
            bar.pack(fill="x", pady=(1, 0))
            lbl.bind("<Button-1>", lambda e, n=name: self._show_tab(n))
            self._tab_w[name] = (lbl, bar)

        self.mode_chip = Chip(self, top, I["auto"], "AUTO", "good", click=self.toggle_mode)
        self.mode_chip.f.pack(side="right")
        self.lat_chip = Chip(self, top, I["clock"], "—", "dim")
        self.lat_chip.f.pack(side="right", padx=6)
        self.model_chip = Chip(self, top, I["bolt"], "model", "accent")
        self.model_chip.f.pack(side="right")
        self._custom += [self.mode_chip, self.lat_chip, self.model_chip]
        for w in (top, tabs):
            w.bind("<Button-1>", self.start_move)
            w.bind("<B1-Motion>", self.do_move)
            w.bind("<ButtonRelease-1>", lambda e: self._save_settings_soon())

        hr = ctk.CTkFrame(self.panel, height=1)
        self._reg(hr, fg_color="border")
        hr.pack(fill="x", padx=16, pady=(0, 0))

        # body (stack of views)
        self.body = ctk.CTkFrame(self.panel, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=14, pady=(10, 0))

        # --- Answer view
        self.view_answer = ctk.CTkFrame(self.body, fg_color="transparent")
        self.q_frame = ctk.CTkFrame(self.view_answer, corner_radius=12)
        self._reg(self.q_frame, fg_color="panel")
        self.q_frame.pack(fill="x", pady=(0, 8))
        self.q_bar = ctk.CTkFrame(self.q_frame, width=3, height=10, corner_radius=2, fg_color=C["good"])
        self.q_bar.pack(side="left", fill="y", padx=(10, 0), pady=10)
        q_inner = ctk.CTkFrame(self.q_frame, fg_color="transparent")
        q_inner.pack(side="left", fill="x", expand=True, padx=(10, 12), pady=(7, 9))
        self.q_src = ctk.CTkLabel(q_inner, text="LISTENING", font=self._font(9, "bold"), height=14, anchor="w")
        self._reg(self.q_src, text_color="faint")
        self.q_src.pack(fill="x")
        self.question_box = ctk.CTkLabel(
            q_inner, text="Questions from the call show up here", font=self._font(13),
            wraplength=600, justify="left", anchor="w")
        self._reg(self.question_box, text_color="dim")
        self.question_box.pack(fill="x")

        # answer card = toolbar + text
        self.answer_wrap = ctk.CTkFrame(self.view_answer, corner_radius=14)
        self._reg(self.answer_wrap, fg_color="panel")
        self.answer_wrap.pack(fill="both", expand=True)
        tbar = ctk.CTkFrame(self.answer_wrap, fg_color="transparent", height=34)
        tbar.pack(fill="x", padx=(14, 8), pady=(8, 0))
        self.a_icon = ctk.CTkLabel(tbar, text=I["bolt"], font=self._ifont(11), width=14)
        self._reg(self.a_icon, text_color="accent")
        self.a_icon.pack(side="left")
        self.a_lbl = ctk.CTkLabel(tbar, text="SUGGESTED ANSWER", font=self._font(9, "bold"))
        self._reg(self.a_lbl, text_color="faint")
        self.a_lbl.pack(side="left", padx=(6, 0))
        self.copy_btn = self._icon_btn(tbar, I["copy"], self.copy_answer, size=12, width=28)
        self.copy_btn.pack(side="right")
        self._refine_btns = []
        for label, kind in (("Simpler", "simpler"), ("Example", "example"),
                            ("Deeper", "deeper"), ("Shorter", "shorter")):
            b = self._ghost_btn(tbar, label, lambda k=kind: self._refine(k))
            b.pack(side="right", padx=1)
            self._refine_btns.append(b)

        self.answer_box = ctk.CTkTextbox(self.answer_wrap, corner_radius=0, border_width=0,
                                         font=self._font(self._fsize), wrap="word")
        self._reg(self.answer_box, fg_color="panel", text_color="text",
                  scrollbar_button_color="panel2", scrollbar_button_hover_color="border")
        self.answer_box.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        self._setup_tags()
        self._show_welcome()
        self._lock_text(self.answer_box._textbox)

        # teleprompter controls (shown only in Read mode)
        self.tele_bar = ctk.CTkFrame(self.view_answer, fg_color="transparent")
        self.tele_play = ctk.CTkButton(self.tele_bar, text="Pause", height=28, width=84, corner_radius=14,
                                       font=self._font(11, "bold"), command=self.tele_toggle_play)
        self._reg(self.tele_play, fg_color="accent", hover_color="accent", text_color="on_accent")
        self.tele_play.pack(side="left", padx=(0, 8))
        self.tele_slower = self._ghost_btn(self.tele_bar, "−", lambda: self.tele_speed(-10), width=28)
        self.tele_slower.pack(side="left")
        self.tele_wpm_lbl = ctk.CTkLabel(self.tele_bar, text=f"{self._wpm} wpm", width=64,
                                         font=self._font(11, "bold"))
        self._reg(self.tele_wpm_lbl, text_color="dim")
        self.tele_wpm_lbl.pack(side="left")
        self.tele_faster = self._ghost_btn(self.tele_bar, "+", lambda: self.tele_speed(+10), width=28)
        self.tele_faster.pack(side="left")
        self.tele_restart = self._ghost_btn(self.tele_bar, "Restart", self.tele_restart_cb)
        self.tele_restart.pack(side="left", padx=(8, 0))
        self.tele_hint = ctk.CTkLabel(self.tele_bar, text="highlight = say this now", font=self._font(10))
        self._reg(self.tele_hint, text_color="faint")
        self.tele_hint.pack(side="right")

        # --- Transcript view
        self.view_tx = ctk.CTkFrame(self.body, fg_color="transparent")
        self.tx_box = ctk.CTkTextbox(self.view_tx, corner_radius=14, border_width=0,
                                     font=self._font(12), wrap="word")
        self._reg(self.tx_box, fg_color="panel", text_color="text",
                  scrollbar_button_color="panel2", scrollbar_button_hover_color="border")
        self.tx_box.pack(fill="both", expand=True)
        self._setup_tx_tags()
        self._tx_placeholder()
        self._lock_text(self.tx_box._textbox)

        # --- History view
        self.view_hist = ctk.CTkScrollableFrame(self.body, corner_radius=14)
        self._reg(self.view_hist, fg_color="panel", scrollbar_button_color="panel2")
        self._hist_widgets = []

        # --- Setup view (interview prep: company, role, round, resume, job description, notes)
        self._build_setup()

        # ---- suggestions + ask bar ----
        self.ask_wrap = ctk.CTkFrame(self.panel, fg_color="transparent")
        self.ask_wrap.pack(fill="x", padx=14, pady=(10, 0))
        sugg = ctk.CTkFrame(self.ask_wrap, fg_color="transparent")
        sugg.pack(fill="x", pady=(0, 7))
        self._sugg_btns = []
        for label, prompt in SUGGESTIONS:
            b = ctk.CTkButton(sugg, text=label, height=26, corner_radius=13, width=10, border_width=1,
                              font=self._font(10, "bold"), command=lambda p=prompt, l=label: self._ask(p, l))
            self._reg(b, fg_color="card", hover_color="panel2", text_color="dim", border_color="border")
            b.pack(side="left", padx=(0, 6))
            self._sugg_btns.append(b)

        row = ctk.CTkFrame(self.ask_wrap, corner_radius=23, height=46, border_width=1)
        self._reg(row, fg_color="panel", border_color="border")
        row.pack(fill="x")
        row.pack_propagate(False)
        self.ask_row = row
        ask_ic = ctk.CTkLabel(row, text=I["chat"], font=self._ifont(13), width=18)
        self._reg(ask_ic, text_color="faint")
        ask_ic.pack(side="left", padx=(16, 0))
        self.ask_entry = ctk.CTkEntry(
            row, placeholder_text="Ask anything about the call or your screen…", height=34,
            border_width=0, fg_color="transparent", font=self._font(12))
        self._reg(self.ask_entry, text_color="text", placeholder_text_color="faint")
        self.ask_entry.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=5)
        self.ask_entry.bind("<Return>", lambda e: self._ask_from_entry())
        self._make_typable(self.ask_entry)
        self.ask_entry.bind("<FocusOut>", lambda e: self.ask_row.configure(border_color=C["border"]), add="+")
        self.ask_btn = ctk.CTkButton(row, text=I["send"], width=36, height=36, corner_radius=18,
                                     font=self._ifont(13), command=self._ask_from_entry)
        self._reg(self.ask_btn, fg_color="accent", hover_color="accent", text_color="on_accent")
        self.ask_btn.pack(side="right", padx=(0, 5), pady=5)

        # ---- footer ----
        self.footer = ctk.CTkFrame(self.panel, fg_color="transparent", height=24)
        self.footer.pack(fill="x", padx=12, pady=(6, 9))
        self.prev_btn = self._icon_btn(self.footer, I["left"], lambda: self.nav_history(-1), size=10, width=24)
        self.prev_btn.configure(height=22)
        self.prev_btn.pack(side="left")
        self.hist_lbl = ctk.CTkLabel(self.footer, text="0 / 0", font=self._font(10, "bold"), width=44)
        self._reg(self.hist_lbl, text_color="faint")
        self.hist_lbl.pack(side="left")
        self.next_btn = self._icon_btn(self.footer, I["right"], lambda: self.nav_history(+1), size=10, width=24)
        self.next_btn.configure(height=22)
        self.next_btn.pack(side="left")

        self.hints = ctk.CTkFrame(self.footer, fg_color="transparent")
        self.hints.pack(side="left", padx=(14, 0))
        for k, d in HINTS:
            self._keycap(self.hints, k).pack(side="left")
            lbl = ctk.CTkLabel(self.hints, text=d, font=self._font(9))
            self._reg(lbl, text_color="faint")
            lbl.pack(side="left", padx=(5, 12))
        self.toast = ctk.CTkLabel(self.footer, text="", font=self._font(10, "bold"))   # transient messages

        self.grip = ctk.CTkLabel(self.footer, text="◢", font=self._font(11), cursor="size_nw_se")
        self._reg(self.grip, text_color="faint")
        self.grip.pack(side="right")
        self.grip.bind("<Button-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._do_resize)
        self.grip.bind("<ButtonRelease-1>", lambda e: self._save_settings_soon())
        self.panel.bind("<Configure>", self._on_resize)

        self._show_tab("Answer")

    # ================================================================ Setup tab
    SETUP_ROUNDS = ["Mixed", "Viva", "HR", "Technical", "Coding", "Managerial"]

    def _field_label(self, parent, text, hint=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        lbl = ctk.CTkLabel(row, text=text, font=self._font(9, "bold"), height=14, anchor="w")
        self._reg(lbl, text_color="faint")
        lbl.pack(side="left")
        if hint:
            h = ctk.CTkLabel(row, text=hint, font=self._font(9), height=14, anchor="w")
            self._reg(h, text_color="faint")
            h.pack(side="left", padx=(8, 0))
        return row

    def _setup_entry(self, parent, placeholder):
        e = ctk.CTkEntry(parent, placeholder_text=placeholder, height=34, corner_radius=10, border_width=1,
                         font=self._font(12))
        self._reg(e, fg_color="card", border_color="border", text_color="text", placeholder_text_color="faint")
        self._make_typable(e)
        return e

    def _setup_text(self, parent, height):
        t = ctk.CTkTextbox(parent, height=height, corner_radius=10, border_width=1, font=self._font(11),
                           wrap="word")
        self._reg(t, fg_color="card", border_color="border", text_color="text",
                  scrollbar_button_color="panel2", scrollbar_button_hover_color="border")
        self._make_typable(t)
        return t

    def _build_setup(self):
        v = ctk.CTkScrollableFrame(self.body, corner_radius=14)
        self._reg(v, fg_color="panel", scrollbar_button_color="panel2")
        self.view_setup = v

        head = ctk.CTkFrame(v, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 0))
        t = ctk.CTkLabel(head, text="Interview setup", font=self._font(15, "bold", family=DISPLAY_FONT),
                         anchor="w")
        self._reg(t, text_color="text")
        t.pack(anchor="w")
        s = ctk.CTkLabel(head, text="Fill this before the call. Every answer will use your real resume and the "
                                    "job, instead of [placeholders].", font=self._font(11), anchor="w",
                         justify="left", wraplength=560)
        self._reg(s, text_color="dim")
        s.pack(anchor="w", pady=(2, 0))
        self._setup_sub = s

        self._field_label(v, "COMPANY / COLLEGE   &   ROLE / SUBJECT")
        row = ctk.CTkFrame(v, fg_color="transparent")
        row.pack(fill="x", padx=14)
        self.su_company = self._setup_entry(row, "e.g. Infosys   or   VIT-AP")
        self.su_company.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.su_role = self._setup_entry(row, "e.g. SDE Trainee   or   Capstone project viva")
        self.su_role.pack(side="left", fill="x", expand=True)

        self._field_label(v, "ROUND")
        self.su_round = ctk.CTkSegmentedButton(v, values=self.SETUP_ROUNDS, height=30, corner_radius=10,
                                               font=self._font(11, "bold"))
        self._reg(self.su_round, fg_color="card", selected_color="accent", selected_hover_color="accent",
                  unselected_color="card", unselected_hover_color="panel2", text_color="text")
        self.su_round.set("Mixed")
        self.su_round.pack(anchor="w", padx=14)

        self._field_label(v, "RESUME", "PDF, DOCX or TXT")
        rrow = ctk.CTkFrame(v, fg_color="transparent")
        rrow.pack(fill="x", padx=14)
        self.su_pick = ctk.CTkButton(rrow, text="Choose file…", height=32, width=120, corner_radius=10,
                                     font=self._font(11, "bold"), command=self._pick_resume)
        self._reg(self.su_pick, fg_color="panel2", hover_color="border", text_color="text")
        self.su_pick.pack(side="left")
        self.su_resume_lbl = ctk.CTkLabel(rrow, text="No resume added", font=self._font(11), anchor="w")
        self._reg(self.su_resume_lbl, text_color="faint")
        self.su_resume_lbl.pack(side="left", padx=10)
        self.su_clear = self._ghost_btn(rrow, "Remove", self._clear_resume)

        self._field_label(v, "JOB DESCRIPTION / VIVA DETAILS", "paste the job post, or the course / project topic")
        self.su_jd = self._setup_text(v, 120)
        self.su_jd.pack(fill="x", padx=14)

        self._field_label(v, "NOTES", "anything else: CGPA, branch, key project, why this company")
        self.su_notes = self._setup_text(v, 70)
        self.su_notes.pack(fill="x", padx=14)

        brow = ctk.CTkFrame(v, fg_color="transparent")
        brow.pack(fill="x", padx=14, pady=(14, 14))
        self.su_save = ctk.CTkButton(brow, text="Save setup", height=34, width=120, corner_radius=17,
                                     font=self._font(12, "bold"), command=self._save_setup)
        self._reg(self.su_save, fg_color="accent", hover_color="accent", text_color="on_accent")
        self.su_save.pack(side="left")
        self.su_predict = ctk.CTkButton(brow, text="Predict likely questions", height=34, width=10,
                                        corner_radius=17, border_width=1, font=self._font(12, "bold"),
                                        command=self._predict)
        self._reg(self.su_predict, fg_color="card", hover_color="panel2", text_color="text",
                  border_color="border")
        self.su_predict.pack(side="left", padx=8)
        self.su_status = ctk.CTkLabel(brow, text="", font=self._font(10, "bold"))
        self.su_status.pack(side="left", padx=6)

    def _collect_setup(self) -> dict:
        return {"company": self.su_company.get().strip(), "role": self.su_role.get().strip(),
                "round": self.su_round.get() or "Mixed",
                "jd": self.su_jd.get("1.0", "end-1c").strip(),
                "notes": self.su_notes.get("1.0", "end-1c").strip()}

    def _save_setup(self):
        self._end_typing()
        if self.on_profile_save:
            threading.Thread(target=self.on_profile_save, args=(self._collect_setup(),), daemon=True).start()

    def _predict(self):
        self._end_typing()
        if self.on_profile_save:
            self.on_profile_save(self._collect_setup())       # use what is typed right now
        if self.on_predict:
            threading.Thread(target=self.on_predict, daemon=True).start()

    def _pick_resume(self):
        from tkinter import filedialog
        self._begin_typing(target=None)            # the file dialog needs the window to be focusable
        self.attributes("-topmost", False)
        try:
            path = filedialog.askopenfilename(
                parent=self, title="Choose your resume",
                filetypes=[("Resume", "*.pdf *.docx *.txt *.md"), ("All files", "*.*")])
        finally:
            self.attributes("-topmost", True)
            self._end_typing()
        if path and self.on_resume_pick:
            self.set_setup_status("Reading resume…", "info")
            threading.Thread(target=self.on_resume_pick, args=(path,), daemon=True).start()

    def _clear_resume(self):
        if self.on_resume_clear:
            threading.Thread(target=self.on_resume_clear, daemon=True).start()

    # thread-safe API for the Setup tab
    def set_profile(self, p: dict):
        def _apply():
            for entry, key in ((self.su_company, "company"), (self.su_role, "role")):
                entry.delete(0, "end")
                if p.get(key):
                    entry.insert(0, p[key])
            self.su_round.set(p.get("round") if p.get("round") in self.SETUP_ROUNDS else "Mixed")
            for box, key in ((self.su_jd, "jd"), (self.su_notes, "notes")):
                box.delete("1.0", "end")
                box.insert("1.0", p.get(key) or "")
            self._set_resume_label(p.get("resume_name") or "", len(p.get("resume_text") or ""))
            parts = [x for x in ((p.get("company") or "").strip(), (p.get("role") or "").strip()) if x]
            self._profile_sum = " · ".join(parts)
            self._paint_meta()
        self.after(0, _apply)

    def _set_resume_label(self, name, chars):
        if name:
            self.su_resume_lbl.configure(text=f"{name}  ·  {chars:,} characters read", text_color=C["good"])
            self.su_clear.pack(side="left")
        else:
            self.su_resume_lbl.configure(text="No resume added", text_color=C["faint"])
            self.su_clear.pack_forget()

    def set_setup_status(self, text: str, color_key: str = "good"):
        self.after(0, lambda: self.su_status.configure(text=text, text_color=C.get(color_key, color_key)))

    def _paint_meta(self):
        txt = self._meta_text + (f"  ·  {self._profile_sum}" if self._profile_sum else "")
        self.model_chip.set(txt)

    def _fit_pill(self, event=None):
        """Collapse pill buttons to icon + keycap when the window is too narrow for the labels."""
        try:
            avail = self.pill.winfo_width()
            if not self._pill_compact:
                need = sum(p.winfo_reqwidth() for p in self._pill_parts) + 40
                self._pill_full_w = need
                if need > avail:
                    self._pill_compact = True
            elif self._pill_full_w and self._pill_full_w + 10 < avail:
                self._pill_compact = False
            for b in (self.btn_answer, self.btn_screen, self.btn_read, self.btn_hide):
                b.set_compact(self._pill_compact and b is not self.btn_answer)
        except Exception:
            pass

    # ------------------------------------------------------------ toast (footer)
    def _toast(self, text: str, color_key: str = "accent", ms: int = 1600):
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self.hints.pack_forget()
        self.toast.configure(text=text, text_color=C.get(color_key, color_key))
        self.toast.pack(side="left", padx=(14, 0))

        def back():
            self._toast_job = None
            self.toast.pack_forget()
            if self.click_through:
                self._toast("Click-through ON · press F10 to turn off", "warn", 10 ** 8)
            else:
                self.hints.pack(side="left", padx=(14, 0))
        self._toast_job = self.after(ms, back)

    # ----------------------------------------------------------- tags/text
    def _setup_tags(self):
        tb = self.answer_box._textbox
        fs = self._fsize
        tb.configure(spacing1=2, spacing3=5, padx=16, pady=6, selectbackground=C["panel2"])
        tb.tag_config("h", foreground=C["accent"], font=(UI_FONT, max(fs - 4, 8), "bold"),
                      spacing1=8, spacing3=3)
        tb.tag_config("lead", font=(DISPLAY_FONT, fs + 2, "bold"), foreground=C["text"], spacing3=6)
        tb.tag_config("bul", foreground=C["accent"], font=(UI_FONT, fs, "bold"))
        tb.tag_config("li", lmargin1=4, lmargin2=20, spacing3=6)
        tb.tag_config("code", font=(MONO_FONT, max(fs - 2, 8)), background=C["code_bg"],
                      foreground=C["code_fg"], lmargin1=14, lmargin2=14, rmargin=14, spacing1=0, spacing3=0)
        tb.tag_config("code_top", spacing1=8)
        tb.tag_config("code_bot", spacing3=8)
        tb.tag_config("dim", foreground=C["dim"])
        tb.tag_config("faint", foreground=C["faint"])
        tb.tag_config("wait", foreground=C["dim"], font=(UI_FONT, fs, "italic"))
        tb.tag_config("big", font=(DISPLAY_FONT, fs + 5, "bold"), foreground=C["text"])
        tb.tag_config("key", foreground=C["accent"], font=(UI_FONT, max(fs - 2, 8), "bold"))
        tb.tag_config("tele_done", foreground=C["faint"])
        tb.tag_config("tele_cur", background=C["accent"], foreground=C["on_accent"])
        tb.tag_raise("tele_done")
        tb.tag_raise("tele_cur")

    def _setup_tx_tags(self):
        tb = self.tx_box._textbox
        tb.configure(spacing1=3, spacing3=8, padx=16, pady=14, selectbackground=C["panel2"])
        tb.tag_config("t_time", foreground=C["faint"], font=(UI_FONT, 9))
        tb.tag_config("t_who", foreground=C["accent"], font=(UI_FONT, 10, "bold"))
        tb.tag_config("t_you", foreground=C["good"], font=(UI_FONT, 10, "bold"))
        tb.tag_config("t_txt", foreground=C["text"], font=(UI_FONT, 12), lmargin1=0, lmargin2=0, spacing3=12)
        tb.tag_config("t_live", foreground=C["info"], font=(UI_FONT, 12, "italic"))
        tb.tag_config("t_dim", foreground=C["dim"], font=(UI_FONT, 12))

    def _tx_placeholder(self):
        tb = self.tx_box._textbox
        tb.delete("1.0", "end")
        tb.insert("end", "Everything said on the call appears here, live.\n", "t_dim")
        self._tx_empty = True

    def _show_welcome(self):
        tb = self.answer_box._textbox
        tb.delete("1.0", "end")
        tb.insert("end", "Ready when you are\n", "big")
        tb.insert("end", "I listen to the call and answer each question the moment it ends.\n", "dim")
        tb.insert("end", "\nSHORTCUTS\n", "h")
        for k, d in (("F7", "Answer the last ~12 seconds now"), ("F9", "Read the whole screen and solve it"),
                     ("F12", "Drag a box and read just that area"), ("F11", "Teleprompter: read answers aloud"),
                     ("F8", "Hide / show instantly"), ("F6", "Auto ↔ Manual answers")):
            tb.insert("end", f"{k:<5}", "key")
            tb.insert("end", f"{d}\n", "dim")

    # ============================================================ tabs/views
    def _show_tab(self, name):
        self._tab = name
        for v in (self.view_answer, self.view_tx, self.view_hist, self.view_setup):
            v.pack_forget()
        view = {"Answer": self.view_answer, "Transcript": self.view_tx, "History": self.view_hist,
                "Setup": self.view_setup}[name]
        view.pack(fill="both", expand=True)
        # the Setup form needs the room the suggestion chips + Ask bar use
        if name == "Setup":
            self.ask_wrap.pack_forget()
            if self._panel_open and not self._user_sized:
                w, h = self._cur_size()
                if h < 640:
                    self._set_size(w, 640)
        elif not self.ask_wrap.winfo_manager():
            self.ask_wrap.pack(fill="x", padx=14, pady=(10, 0), before=self.footer)
        if name == "History":
            self._rebuild_history()
        if name == "Transcript":
            self.tx_box._textbox.see("end")
        self._paint_tabs()

    def _paint_tabs(self):
        for n, (lbl, bar) in self._tab_w.items():
            on = (n == self._tab)
            lbl.configure(text_color=C["text"] if on else C["faint"])
            bar.configure(fg_color=C["accent"] if on else C["card"])

    def _rebuild_history(self):
        for w in self._hist_widgets:
            w.destroy()
        self._hist_widgets = []
        if not self._history:
            lbl = ctk.CTkLabel(self.view_hist, text="Past answers will be listed here.",
                               text_color=C["dim"], font=self._font(12))
            lbl.pack(pady=24)
            self._hist_widgets.append(lbl)
            return
        for i in range(len(self._history) - 1, -1, -1):
            e = self._history[i]
            q = self._clean_q(e["q"]) or "(no question)"
            q = q if len(q) < 84 else q[:81] + "…"
            a = re.sub(r"\s+", " ", re.sub("|".join(HEADINGS), "", e["a"] or "")).strip(" •-")
            a = a if len(a) < 110 else a[:107] + "…"
            card = ctk.CTkFrame(self.view_hist, corner_radius=10, fg_color=C["panel2"])
            card.pack(fill="x", padx=6, pady=4)
            num = ctk.CTkLabel(card, text=str(i + 1), width=22, height=22, corner_radius=11,
                               fg_color=C["border"], text_color=C["dim"], font=self._font(9, "bold"))
            num.pack(side="left", padx=(10, 8), pady=10, anchor="n")
            txt = ctk.CTkFrame(card, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True, pady=(7, 8), padx=(0, 10))
            ql = ctk.CTkLabel(txt, text=q, anchor="w", justify="left", font=self._font(11, "bold"),
                              text_color=C["text"])
            ql.pack(fill="x")
            al = ctk.CTkLabel(txt, text=a or "…", anchor="w", justify="left", font=self._font(10),
                              text_color=C["dim"])
            al.pack(fill="x")
            wrap = max(self.panel.winfo_width() / self._sc() - 120, 300)
            ql.configure(wraplength=wrap)
            al.configure(wraplength=wrap)

            def enter(c=card, n=num):
                c.configure(fg_color=C["border"]); n.configure(fg_color=C["accent"], text_color=C["on_accent"])

            def leave(c=card, n=num):
                c.configure(fg_color=C["panel2"]); n.configure(fg_color=C["border"], text_color=C["dim"])
            _bind_as_button(card, [num, txt, ql, al], enter, leave, lambda idx=i: self._open_history(idx))
            self._hist_widgets.append(card)

    def _open_history(self, idx):
        self._view_idx = idx if idx != len(self._history) - 1 else -1
        self._show_tab("Answer")
        self._render_entry(self._history[idx])
        self._update_hist_label(idx)

    # ---------------------------------------------------------- transcript
    def add_transcript(self, text: str, speaker: str = "Interviewer"):
        def _apply():
            tb = self.tx_box._textbox
            if self._tx_empty:
                tb.delete("1.0", "end")
                self._tx_empty = False
            self._clear_live_line()
            s = int(time.time() - self._t0_session)
            tb.insert("end", f"{speaker.upper()}   ", "t_you" if speaker == "You" else "t_who")
            tb.insert("end", f"{s // 60:02d}:{s % 60:02d}\n", "t_time")
            tb.insert("end", f"{text.strip()}\n", "t_txt")
            tb.see("end")
        self.after(0, _apply)

    def _clear_live_line(self):
        if self._live_line_start:
            tb = self.tx_box._textbox
            try:
                tb.delete(self._live_line_start, "end-1c")
            except Exception:
                pass
            self._live_line_start = None

    # -------------------------------------------------------------- ask box
    def _ask_from_entry(self):
        text = self.ask_entry.get().strip()
        if not text:
            return
        self.ask_entry.delete(0, "end")
        self._end_typing()
        self._ask(text, text)

    def _ask(self, prompt: str, label: str):
        if self.on_ask:
            if not self._panel_open:
                self.toggle_panel(force=True)
            self._show_tab("Answer")
            self._next_src = "YOU ASKED"
            threading.Thread(target=self.on_ask, args=(prompt, label), daemon=True).start()

    @staticmethod
    def _thread(fn):
        if fn:
            threading.Thread(target=fn, daemon=True).start()

    # ============================================================ settings
    def _save_settings_soon(self):
        if self._save_job is None:
            self._save_job = self.after(600, self._save_settings)

    def _save_settings(self):
        self._save_job = None
        try:
            data = {"theme": self._theme,
                    "fsize": self._pre_tele_fsize if self.tele_mode else self._fsize,
                    "opacity_idx": self._opacity_idx, "wpm": self._wpm,
                    "panel_open": self._panel_open, "mic_on": self.mic_on}
            if self._panel_open and self.is_visible:
                sc = self._get_window_scaling() or 1.0
                data["geom"] = (f"{round(self.winfo_width() / sc)}x{round(self.winfo_height() / sc)}"
                                f"+{self.winfo_x()}+{self.winfo_y()}")
            old = _load_settings()
            old.update(data)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(old, f, indent=2)
        except Exception as e:
            print(f"[Settings] save error: {e}")

    def open_menu(self):
        kw = dict(tearoff=0, bg=C["panel"], fg=C["text"], activebackground=C["accent"],
                  activeforeground=C["on_accent"], bd=0, relief="flat", font=(UI_FONT, 10),
                  disabledforeground=C["faint"])
        m = tk.Menu(self, **kw)
        tm = tk.Menu(m, **kw)
        for t in THEME_ORDER:
            tm.add_command(label=("●  " if t == self._theme else "    ") + t, command=lambda n=t: self.set_theme(n))
        m.add_command(label="APPEARANCE", state="disabled")
        m.add_cascade(label="    Theme", menu=tm)
        m.add_command(label=f"    Opacity  ·  {int(OPACITY_STEPS[self._opacity_idx] * 100)}%",
                      command=self.cycle_opacity)
        m.add_command(label=f"    Bigger text  ·  {self._fsize}px", command=lambda: self.change_font(+1))
        m.add_command(label="    Smaller text", command=lambda: self.change_font(-1))
        m.add_command(label="    Auto-size to answers",
                      command=lambda: (setattr(self, "_user_sized", False), self._autosize(True)))
        m.add_separator()
        m.add_command(label="BEHAVIOUR", state="disabled")
        m.add_command(label=f"    Answer mode  ·  {'Auto' if self.auto_mode else 'Manual'}      F6",
                      command=self.toggle_mode)
        m.add_command(label=f"    Click-through  ·  {'On' if self.click_through else 'Off'}      F10",
                      command=self.toggle_click_through)
        m.add_command(label=f"    Hear my mic as 'You'  ·  {'On' if self.mic_on else 'Off'}",
                      command=self.toggle_mic)
        m.add_command(label="    Capture a region      F12", command=self.start_region_select)
        m.add_separator()
        m.add_command(label="SESSION", state="disabled")
        m.add_command(label="    Open saved transcripts", command=self.open_log_folder)
        m.add_command(label="    Forget conversation memory", command=self._handle_clear_memory)
        x, y = self.menu_btn.winfo_rootx(), self.menu_btn.winfo_rooty() + 34
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def toggle_mic(self):
        self.mic_on = not self.mic_on
        if self.on_mic_toggle:
            self.on_mic_toggle(self.mic_on)
        self._save_settings_soon()
        self._toast(f"Mic ('You') {'on' if self.mic_on else 'off'}", "good" if self.mic_on else "warn")

    def cycle_theme(self):
        self.set_theme(THEME_ORDER[(THEME_ORDER.index(self._theme) + 1) % len(THEME_ORDER)])

    def set_theme(self, name: str):
        self._theme = name
        _set_theme_colors(name)
        self._apply_theme()
        self._save_settings_soon()
        self._toast(f"Theme · {name}", "accent")

    def _apply_theme(self):
        for w, opts in self._themed:
            try:
                w.configure(**{k: (C[v] if v in C else v) for k, v in opts.items()})
            except Exception:
                pass
        for o in self._custom:
            o.retheme()
        self.btn_read.set_active(self.tele_mode)
        self.mode_chip.set(color_key="good" if self.auto_mode else "warn")
        self._paint_tabs()
        self._set_dot(self._status_color)
        self._setup_tags()
        self._setup_tx_tags()
        self._restyle()
        self._tele_refresh_tags()
        if self._tab == "History":
            self._rebuild_history()

    # ============================================================== stealth
    def setup_stealth(self):
        apply_full_stealth(_get_top_hwnd(self), click_through=self.click_through,
                           no_activate=not self._typing)

    # ---------------------------------------------------- focus-safe typing
    def _make_typable(self, widget):
        """Text inputs (Ask box, Setup fields) may take keyboard focus while you type in them."""
        if not hasattr(self, "_typables"):
            self._typables = []
        self._typables.append(widget)
        widget.bind("<Button-1>", lambda e, w=widget: self._begin_typing(target=w), add="+")
        widget.bind("<FocusOut>", lambda e: self._maybe_end_typing(), add="+")
        widget.bind("<Escape>", lambda e: self._end_typing(), add="+")

    def _focus_is_typable(self) -> bool:
        try:
            f = self.focus_get()
        except Exception:
            return False
        while f is not None:
            if f in getattr(self, "_typables", []):
                return True
            f = getattr(f, "master", None)
        return False

    def _maybe_end_typing(self):
        # moving between Setup fields fires FocusOut too: only give focus back once it left our inputs
        self.after(120, lambda: None if self._focus_is_typable() else self._end_typing())

    def _begin_typing(self, event=None, target="ask"):
        """Only text inputs may take keyboard focus; remember which window to give it back to."""
        if target == "ask":
            target = self.ask_entry
        u = ctypes.windll.user32
        me = _get_top_hwnd(self)
        if not self._typing:
            fg = u.GetForegroundWindow()
            self._prev_fg = fg if fg and fg != me else None
            self._typing = True
            self.setup_stealth()                # drop NOACTIVATE so the entry can get keys
        if target is self.ask_entry:
            self.ask_row.configure(border_color=C["accent"])

        def _focus():
            try:
                u.SetForegroundWindow(me)
            except Exception:
                pass
            self.focus_force()
            if target is not None:
                target.focus_set()
        self.after(25, _focus)

    def _end_typing(self, event=None):
        if not self._typing:
            return
        self._typing = False
        self.ask_row.configure(border_color=C["border"])
        try:
            self.focus_set()                    # take the caret out of the field
        except Exception:
            pass
        self.setup_stealth()                    # back to never-steal-focus
        if self._prev_fg:
            try:
                ctypes.windll.user32.SetForegroundWindow(self._prev_fg)
            except Exception:
                pass
            self._prev_fg = None

    @staticmethod
    def _lock_text(tb):
        """Read-only display: no caret, no typing, no focus, no selection drag."""
        tb.configure(insertwidth=0, cursor="arrow", takefocus=0, exportselection=False)
        for ev in ("<Key>", "<<Paste>>", "<<Cut>>", "<Button-1>", "<B1-Motion>", "<Double-Button-1>",
                   "<Triple-Button-1>", "<Button-2>", "<Button-3>", "<ButtonRelease-1>"):
            tb.bind(ev, lambda e: "break")

    def _global_hotkey_loop(self):
        user32 = ctypes.windll.user32
        prev = {VK_F6: False, VK_F7: False, VK_F8: False, VK_F9: False, VK_F10: False,
                VK_F11: False, VK_F12: False}
        while self._running:
            for vk in prev:
                pressed = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if pressed and not prev[vk]:
                    if vk == VK_F6:
                        self.after(0, self.toggle_mode)
                    elif vk == VK_F7 and self.on_force_answer:
                        threading.Thread(target=self.on_force_answer, daemon=True).start()
                    elif vk == VK_F8:
                        self.after(0, self.toggle_visibility)
                    elif vk == VK_F9 and self.on_screen_ocr:
                        threading.Thread(target=self.on_screen_ocr, daemon=True).start()
                    elif vk == VK_F10:
                        self.after(0, self.toggle_click_through)
                    elif vk == VK_F11:
                        self.after(0, self.toggle_tele)
                    elif vk == VK_F12:
                        self.after(0, self.start_region_select)
                prev[vk] = pressed
            time.sleep(0.03)

    # ============================================================ window ops
    def start_move(self, event):
        self._drag_x, self._drag_y = event.x_root - self.winfo_x(), event.y_root - self.winfo_y()

    def do_move(self, event):
        self.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    # CTk multiplies geometry() sizes by the DPI scale but winfo_* returns real pixels, so every size we
    # set goes through these helpers (logical units) - mixing them made the window grow on each toggle.
    def _sc(self) -> float:
        return self._get_window_scaling() or 1.0

    def _cur_size(self):
        sc = self._sc()
        return round(self.winfo_width() / sc), round(self.winfo_height() / sc)

    def _set_size(self, w: int, h: int):
        self.geometry(f"{int(w)}x{int(h)}")

    def _start_resize(self, event):
        w, h = self._cur_size()
        self._rs = (event.x_root, event.y_root, w, h)

    def _do_resize(self, event):
        x0, y0, w, h = self._rs
        sc = self._sc()
        self._user_sized = True            # the user picked a size: stop auto-sizing
        self._set_size(max(640, w + (event.x_root - x0) / sc), max(300, h + (event.y_root - y0) / sc))

    COMPACT_H = 420

    def _autosize(self, allow_shrink: bool = False):
        self._autosize_job = None
        self._autosize_now(allow_shrink)
        if self._as_again:      # more text arrived while we were waiting: measure again
            self._as_again = False
            self._schedule_autosize(allow_shrink)

    def _autosize_now(self, allow_shrink: bool = False):
        """Grow the panel to fit the answer (like Cluely) instead of a fixed tall box."""
        if not self._panel_open or self._user_sized or self._tab != "Answer":
            return
        try:
            self.update_idletasks()
            tb = self.answer_box._textbox
            try:                                   # exact rendered height incl. tag fonts/spacing
                px = tb.count("1.0", "end", "update", "ypixels")
                px = px[0] if isinstance(px, tuple) else px
                text_h = int(px) + 2 * 6 + 24
            except Exception:
                n = tb.count("1.0", "end-1c", "displaylines")
                n = n[0] if n else 1
                lh = tkfont.Font(font=(UI_FONT, self._fsize)).metrics("linespace") + 8
                text_h = n * lh + 60
            overhead = self.winfo_height() - tb.winfo_height()
            sc = self._sc()
            top, bottom = self._work_area()
            max_h = max(bottom - self.winfo_y() - 12, 360 * sc)
            target = min(max(overhead + text_h, self.COMPACT_H * sc), max_h, (bottom - top) * 0.92)
            cur = self.winfo_height()
            if target > cur + 10 or (allow_shrink and target < cur - 10):
                self._set_size(self.winfo_width() / sc, target / sc)
        except Exception:
            pass

    def _work_area(self):
        """(top, bottom) of the current monitor's work area in real pixels (Tk's screen size is DPI-scaled)."""
        try:
            class RECT(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT),
                            ("dwFlags", ctypes.c_ulong)]
            u = ctypes.windll.user32
            u.MonitorFromWindow.restype = ctypes.c_void_p
            mon = u.MonitorFromWindow(ctypes.c_void_p(_get_top_hwnd(self)), 2)
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
                return mi.rcWork.t, mi.rcWork.b
        except Exception:
            pass
        return 0, int(self.winfo_screenheight() * self._sc())

    def collapsed_hidden(self) -> bool:
        return not self._panel_open

    def _schedule_autosize(self, allow_shrink=False):
        if self._autosize_job is None:
            self._autosize_job = self.after(140, lambda: self._autosize(allow_shrink))
        else:
            self._as_again = True

    def _on_resize(self, event):
        try:
            self.question_box.configure(wraplength=max(self.panel.winfo_width() - 120, 200))
        except Exception:
            pass

    def toggle_panel(self, force=None):
        want = (not self._panel_open) if force is None else force
        if want == self._panel_open and force is None:
            return
        w, h = self._cur_size()
        if want:
            self.panel.pack(fill="both", expand=True)
            self._set_size(w, self._saved_h)
            self.chev_btn.configure(text=I["down"])
        else:
            if self._panel_open:
                self._saved_h = max(h, 300)
            self.panel.pack_forget()
            self._set_size(w, PILL_H + 4)
            self.chev_btn.configure(text=I["up"])
        self._panel_open = want
        self._save_settings_soon()

    def toggle_visibility(self):
        if self.is_visible:
            self.withdraw()
            self.is_visible = False
        else:
            self.deiconify()
            self.is_visible = True
            self.after(50, self.setup_stealth)

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self.setup_stealth()
        if self.click_through:
            self._toast("Click-through ON · press F10 to turn off", "warn", 10 ** 8)
        else:
            self._toast("Click-through off", "good")
        self._apply_opacity()

    def _apply_opacity(self):
        self.attributes("-alpha", OPACITY_STEPS[self._opacity_idx] * (0.85 if self.click_through else 1.0))

    def cycle_opacity(self):
        self._opacity_idx = (self._opacity_idx + 1) % len(OPACITY_STEPS)
        self._apply_opacity()
        self._save_settings_soon()
        self._toast(f"Opacity · {int(OPACITY_STEPS[self._opacity_idx] * 100)}%", "accent")

    def change_font(self, delta: int):
        self._fsize = max(10, min(32, self._fsize + delta))
        if self.tele_mode:
            self._pre_tele_fsize = max(10, min(24, self._pre_tele_fsize + delta))
        else:
            self._pre_tele_fsize = self._fsize
        self.answer_box.configure(font=self._font(self._fsize))
        self._setup_tags()
        self._restyle()
        self._tele_refresh_tags()
        self._save_settings_soon()

    def toggle_mode(self):
        self.auto_mode = not self.auto_mode
        if self.auto_mode:
            self.mode_chip.set("AUTO", "good", I["auto"])
        else:
            self.mode_chip.set("MANUAL", "warn", I["hand"])
        self._toast("Auto answers on" if self.auto_mode else "Manual: press F7 to answer",
                    "good" if self.auto_mode else "warn")
        if self.on_mode_change:
            self.on_mode_change(self.auto_mode)

    def copy_answer(self):
        text = self.answer_box._textbox.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copy_btn.configure(text=I["check"], text_color=C["good"])
        self.after(1300, lambda: self.copy_btn.configure(text=I["copy"], text_color=C["dim"]))
        self._toast("Answer copied", "good", 1300)

    # ------------------------------------------------- region select (F12)
    def start_region_select(self):
        """Dim every monitor; drag a box to capture just that area. Esc / right-click cancels."""
        if self._sel_win is not None or not self.on_region_capture:
            return
        u = ctypes.windll.user32
        vx, vy, vw, vh = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        win = tk.Toplevel(self)
        self._sel_win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.35)
        win.configure(bg="#000000", cursor="crosshair")
        win.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
        cv = tk.Canvas(win, bg="#000000", highlightthickness=0, cursor="crosshair")
        cv.pack(fill="both", expand=True)
        cv.create_text(vw // 2, 60, text="Drag to select the area to read  ·  Esc or right-click to cancel",
                       fill="#FFFFFF", font=(UI_FONT, 16, "bold"))
        st = {"x": 0, "y": 0, "rect": None, "size": None}

        def close():
            try:
                win.destroy()
            finally:
                self._sel_win = None

        def down(e):
            st["x"], st["y"] = e.x, e.y
            st["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline=C["accent"], width=3)
            st["size"] = cv.create_text(e.x, e.y - 14, text="", fill="#FFFFFF", font=(UI_FONT, 11, "bold"),
                                        anchor="w")

        def move(e):
            if st["rect"]:
                cv.coords(st["rect"], st["x"], st["y"], e.x, e.y)
                cv.coords(st["size"], min(st["x"], e.x), min(st["y"], e.y) - 14)
                cv.itemconfig(st["size"], text=f"{abs(e.x - st['x'])} × {abs(e.y - st['y'])}")

        def up(e):
            x0, x1 = sorted((st["x"], e.x))
            y0, y1 = sorted((st["y"], e.y))
            close()
            if (x1 - x0) < 12 or (y1 - y0) < 12:
                return
            bbox = (vx + x0, vy + y0, vx + x1, vy + y1)
            self.after(180, lambda: threading.Thread(
                target=self.on_region_capture, args=(bbox,), daemon=True).start())

        cv.bind("<ButtonPress-1>", down)
        cv.bind("<B1-Motion>", move)
        cv.bind("<ButtonRelease-1>", up)
        cv.bind("<Button-3>", lambda e: close())
        win.bind("<Escape>", lambda e: close())
        win.update_idletasks()
        try:
            hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
            if not os.environ.get("CUE_NOSTEALTH"):
                u.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass
        win.focus_force()

    def set_log_dir(self, path: str):
        self._log_dir = path

    def open_log_folder(self):
        try:
            if self._log_dir:
                os.makedirs(self._log_dir, exist_ok=True)
                os.startfile(self._log_dir)
        except Exception as e:
            print(f"[HUD] open log folder error: {e}")

    def _handle_clear_memory(self):
        if self.on_clear_memory:
            self.on_clear_memory()
        self._history.clear()
        self._view_idx = -1
        self._update_hist_label()
        self._toast("Conversation memory cleared", "good")

    def _refine(self, kind: str):
        if self.on_refine:
            threading.Thread(target=self.on_refine, args=(kind,), daemon=True).start()

    # ========================================================== teleprompter
    def toggle_tele(self):
        self.tele_mode = not self.tele_mode
        self.btn_read.set_active(self.tele_mode)
        if self.tele_mode:
            if not self._panel_open:
                self.toggle_panel(force=True)
            self._show_tab("Answer")
            self._pre_tele_fsize = self._fsize
            self._fsize = max(self._fsize + 5, 19)
            self.tele_bar.pack(fill="x", pady=(8, 0))
            self._tele_idx = 0
            self._tele_playing = True
            self.tele_play.configure(text="Pause")
        else:
            self._fsize = self._pre_tele_fsize
            self.tele_bar.pack_forget()
            if self._tele_job:
                self.after_cancel(self._tele_job)
                self._tele_job = None
            tb = self.answer_box._textbox
            tb.tag_remove("tele_done", "1.0", "end")
            tb.tag_remove("tele_cur", "1.0", "end")
        self.answer_box.configure(font=self._font(self._fsize))
        self._setup_tags()
        self._restyle()
        if self.tele_mode:
            self._tele_loop()

    def tele_toggle_play(self):
        self._tele_playing = not self._tele_playing
        self.tele_play.configure(text="Pause" if self._tele_playing else "Play")

    def tele_speed(self, delta: int):
        self._wpm = max(80, min(220, self._wpm + delta))
        self.tele_wpm_lbl.configure(text=f"{self._wpm} wpm")
        self._save_settings_soon()

    def tele_restart_cb(self):
        self._tele_idx = 0
        self._tele_playing = True
        self.tele_play.configure(text="Pause")
        tb = self.answer_box._textbox
        tb.tag_remove("tele_done", "1.0", "end")
        tb.tag_remove("tele_cur", "1.0", "end")

    def _tele_words(self):
        return list(re.finditer(r"\S+", self.answer_box._textbox.get("1.0", "end-1c")))

    def _tele_refresh_tags(self):
        if self.tele_mode:
            self._tele_mark(max(self._tele_idx - 1, 0))

    def _tele_mark(self, idx: int):
        words = self._tele_words()
        if not words or idx >= len(words):
            return False
        tb = self.answer_box._textbox
        m = words[idx]
        start, end = f"1.0+{m.start()}c", f"1.0+{m.end()}c"
        tb.tag_remove("tele_done", "1.0", "end")
        tb.tag_remove("tele_cur", "1.0", "end")
        tb.tag_add("tele_done", "1.0", start)
        tb.tag_add("tele_cur", start, end)
        tb.tag_raise("tele_done")
        tb.tag_raise("tele_cur")
        tb.see(start)
        return True

    def _tele_loop(self):
        if not (self.tele_mode and self._running):
            return
        try:
            if self._tele_playing and self._panel_open and self._tab == "Answer" and not self._waiting:
                if self._tele_mark(self._tele_idx):
                    self._tele_idx += 1
        except Exception:
            pass
        self._tele_job = self.after(max(int(60000 / self._wpm), 120), self._tele_loop)

    # ============================================================== history
    def _update_hist_label(self, cur=None):
        n = len(self._history)
        c = (cur + 1) if cur is not None else n
        self.hist_lbl.configure(text=f"{c} / {n}")

    def nav_history(self, delta: int):
        if not self._history:
            return
        i = self._view_idx if self._view_idx >= 0 else len(self._history) - 1
        i = max(0, min(len(self._history) - 1, i + delta))
        self._view_idx = i if i != len(self._history) - 1 else -1
        self._show_tab("Answer")
        self._render_entry(self._history[i])
        self._update_hist_label(i)

    def _render_entry(self, entry):
        self._waiting = False
        self._set_question(entry["q"], entry.get("src"))
        tb = self.answer_box._textbox
        tb.delete("1.0", "end")
        tb.insert("end", entry["a"])
        self._restyle(final=True)
        tb.see("1.0")
        self._tele_idx = 0
        self._schedule_autosize(allow_shrink=True)

    # ------------------------------------------------------------ question card
    @staticmethod
    def _clean_q(q: str) -> str:
        q = (q or "").strip()
        q = re.sub(r"^(👁 Vision|📸 OCR|⚡ Last \d+s:)\s+", "", q).replace("\n", " ")
        return q[:1].upper() + q[1:]

    @staticmethod
    def _source_of(q: str) -> str:
        q = q or ""
        if q.startswith("⚡ Last"):
            return "F7 · FROM THE LAST MINUTE"
        if q.startswith("👁 Vision"):
            return "SCREEN · VISION"
        if q.startswith("📸 OCR"):
            return "SCREEN · TEXT"
        if "↻" in q:
            return "REFINED"
        if q.startswith("Likely questions"):
            return "INTERVIEW PREP"
        return "INTERVIEWER ASKED"

    def _set_question(self, q: str, src: str | None = None):
        self.q_src.configure(text=src or self._source_of(q), text_color=C["accent"])
        self.question_box.configure(text=self._clean_q(q) or "…", text_color=C["text"],
                                    font=self._font(13, "bold"))

    # --------------------------------------------------------------- rendering
    def _restyle(self, final: bool = False):
        self._restyle_job = None
        if self._waiting:
            return
        tb = self.answer_box._textbox
        # models sometimes emit markdown: hide ``` fence lines, **bold** markers and leading #'s
        lines = tb.get("1.0", "end-1c").split("\n")
        for i in range(len(lines), 0, -1):
            ln = lines[i - 1]
            if i == len(lines) and not final:   # last line may still be streaming in ("```py" -> "```python")
                continue
            if re.match(r"^\s*```[\w+#-]*\s*$", ln):
                tb.delete(f"{i}.0", f"{i + 1}.0")
                continue
            m = re.match(r"^\s*#{1,6}\s+", ln)
            if m:
                tb.delete(f"{i}.0", f"{i}.{m.end()}")
        while True:
            pos = tb.search("**", "1.0", "end")
            if not pos:
                break
            tb.delete(pos, f"{pos}+2c")
        for t in ("h", "lead", "bul", "li", "code", "code_top", "code_bot"):
            tb.tag_remove(t, "1.0", "end")
        in_code = False
        lead_pending = True
        code_start = None
        all_lines = tb.get("1.0", "end-1c").split("\n")
        for i, line in enumerate(all_lines, 1):
            s = line.strip()
            head = s.rstrip(":").upper()
            if head in HEADINGS:
                if in_code and code_start:
                    tb.tag_add("code_bot", f"{i - 1}.0", f"{i - 1}.end")
                tb.tag_add("h", f"{i}.0", f"{i}.end")
                in_code = head in ("CODE", "FIX")
                code_start = None
                lead_pending = head in LEAD_HEADINGS
                continue
            if in_code:
                if code_start is None:
                    if not s:
                        continue
                    code_start = i
                    tb.tag_add("code_top", f"{i}.0", f"{i}.end")
                tb.tag_add("code", f"{i}.0", f"{i}.end+1c")
                continue
            if s.startswith(("•", "- ", "* ")):
                tb.tag_add("li", f"{i}.0", f"{i}.end")
                b = line.index(s[0])
                tb.tag_add("bul", f"{i}.{b}", f"{i}.{b + 1}")
                lead_pending = False
            elif s and lead_pending:
                tb.tag_add("lead", f"{i}.0", f"{i}.end")
                lead_pending = False
        if in_code and code_start:
            last = len(all_lines)
            while last > code_start and not all_lines[last - 1].strip():
                last -= 1
            tb.tag_add("code_bot", f"{last}.0", f"{last}.end")

    def _schedule_restyle(self):
        if self._restyle_job is None:
            self._restyle_job = self.after(110, self._restyle)
        self._schedule_autosize()

    # --------------------------------------------------------- thinking state
    def _render_wait(self):
        tb = self.answer_box._textbox
        tb.delete("1.0", "end")
        n = (self._ticks // 6) % 4
        el = time.time() - self._wait_t0
        tb.insert("end", "Thinking" + "." * n + " " * (3 - n), "wait")
        tb.insert("end", f"   {el:.1f}s", "faint")

    # ============================================================ animation
    def _set_dot(self, color):
        self._status_color = color
        self.dot.itemconfig(self._dot_core, fill=color)
        self.q_bar.configure(fg_color=color)

    def _tick(self):
        if not self._running:
            return
        try:
            self._ticks += 1
            self._phase += 0.22
            target = min(self._level / 1800.0, 1.0)
            self._level_smooth += (target - self._level_smooth) * 0.35
            self._level *= 0.85
            h = 26
            for i, bar in enumerate(self._bars):
                idle = 0.08 + 0.05 * math.sin(self._phase + i * 0.6)
                wob = 0.5 + 0.5 * math.sin(self._phase * 1.7 + i * 1.1)
                env = 1.0 - abs(i - 4) / 6.0         # taller in the middle, like a voice meter
                v = max(idle, self._level_smooth * (0.35 + 0.65 * wob) * env)
                bh = max(1.5, v * (h - 6))
                x = i * 6 + 3
                self.wave.coords(bar, x, (h - bh) / 2, x, (h + bh) / 2)
                self.wave.itemconfig(bar, fill=_mix(C["faint"], self._status_color, min(1.0, 0.3 + v * 1.3)))
            glow = 0.15 + 0.25 * (0.5 + 0.5 * math.sin(self._phase * (1.6 if self._active_state else 0.6)))
            self.dot.itemconfig(self._dot_glow, fill=_mix(C["card"], self._status_color, glow))
            if self._waiting and self._ticks % 2 == 0 and self._view_idx == -1:
                self._render_wait()
        except Exception:
            pass
        self.after(50, self._tick)

    # =========================================== thread-safe public API
    def set_level(self, rms: float):
        if rms > self._level:
            self._level = rms

    def set_meta(self, model_name: str, doc_count: int, vision: str | None = None):
        def _apply():
            parts = [model_name]
            parts.append(vision.split(":")[0] if vision else "OCR")
            if doc_count:
                parts.append(f"{doc_count} docs")
            self._meta_text = "  ·  ".join(parts)
            self._paint_meta()
        self.after(0, _apply)

    def set_status(self, text: str, color: str = "#4ECCA3"):
        clean = re.sub(r"^[^\w]+", "", text).strip().rstrip(".…") or text
        short = clean.capitalize() if len(clean) < 14 else clean[:12].capitalize() + "…"

        def _apply():
            self.status_lbl.configure(text=short, text_color=color)
            self._set_dot(color)
            up = clean.upper()
            self._active_state = ("LISTENING" not in up)
            if any(k in up for k in ("ANALYZ", "FORCE", "READING", "SOLVING", "TRANSCRIB", "LOOKING", "REFIN")) \
                    and self._t_think is None:
                self._t_think = time.time()
                self._first_token_pending = True
            if "LISTENING" in up and not self._waiting and self._view_idx == -1:   # stream finished
                if self._restyle_job:
                    self.after_cancel(self._restyle_job)
                self._restyle(final=True)
                self._schedule_autosize()
            if "LISTENING" in up and self._waiting:      # nothing came back (skipped/cancelled)
                self._waiting = False
                if self._view_idx == -1:
                    tb = self.answer_box._textbox
                    tb.delete("1.0", "end")
                    tb.insert("end", "No answer needed for that one.", "dim")
        self.after(0, _apply)

    def update_live_caption(self, text: str):
        def _apply():
            preview = text[-200:] if len(text) > 200 else text
            if not self._waiting:
                self.q_src.configure(text="HEARING…", text_color=C["info"])
                self.question_box.configure(text=preview, text_color=C["info"],
                                            font=self._font(13, slant="italic"))
            tb = self.tx_box._textbox
            if self._tx_empty:
                tb.delete("1.0", "end")
                self._tx_empty = False
            self._clear_live_line()
            self._live_line_start = tb.index("end-1c")
            tb.insert("end", f"…{preview}", "t_live")
            tb.see("end")
        self.after(0, _apply)

    def start_stream(self, question_text: str):
        def _apply():
            self._clear_live_line()
            src = self._next_src or self._source_of(question_text)
            self._next_src = None
            self._history.append({"q": question_text, "a": "", "src": src})
            if len(self._history) > 30:
                self._history.pop(0)
            self._view_idx = -1
            self._update_hist_label()
            if not self._panel_open:
                self.toggle_panel(force=True)
            if self._tab != "Answer":
                self._show_tab("Answer")
            self._set_question(question_text, src)
            self._tele_idx = 0
            self._tele_playing = True
            if self.tele_mode:
                self.tele_play.configure(text="Pause")
            if self._panel_open and not self._user_sized:
                w, _ = self._cur_size()
                self._set_size(w, self.COMPACT_H)     # new answer starts compact, then grows as it streams
            if self._t_think is None:
                self._t_think = time.time()
            self._first_token_pending = True
            self._waiting = True
            self._wait_t0 = time.time()
            self._render_wait()
        self.after(0, _apply)

    def append_token(self, token: str):
        def _apply():
            if not self._history:
                self._history.append({"q": "", "a": "", "src": None})
            entry = self._history[-1]
            t = token
            if "•" in t:
                idx = t.index("•")
                prev = t[idx - 1] if idx > 0 else (entry["a"][-1:] or "\n")
                if prev != "\n":
                    t = t[:idx].rstrip(" ") + "\n" + t[idx:]
            if not entry["a"]:
                t = t.lstrip()
            entry["a"] += t
            if self._first_token_pending and self._t_think:
                self.lat_chip.set(f"{time.time() - self._t_think:.1f}s", "good")
                self._first_token_pending = False
                self._t_think = None
            if self._view_idx == -1:
                tb = self.answer_box._textbox
                if self._waiting:
                    self._waiting = False
                    tb.delete("1.0", "end")
                tb.insert("end", t)
                # stay at the top so the direct answer is always the first thing you read
                self._schedule_restyle()
        self.after(0, _apply)

    def update_cues(self, full_text: str):
        def _apply():
            self._waiting = False
            tb = self.answer_box._textbox
            tb.delete("1.0", "end")
            tb.insert("end", full_text, "dim")
        self.after(0, _apply)

    def quit_app(self):
        self._running = False
        try:
            self._save_settings()
        except Exception:
            pass
        self.destroy()
        sys.exit(0)


if __name__ == "__main__":
    # Design preview:  py overlay_ui.py [--shot out.png] [--theme Ocean] [--tab Transcript] [--tele] [--closed]
    #                                   [--thinking]
    app = StealthHUD()
    app.set_meta("qwen2.5:7b", 3, "qwen2.5vl:3b")
    arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
    shot = arg("--shot")
    if arg("--theme"):
        app.set_theme(arg("--theme"))
    demo = ("DIRECT ANSWER\nTCP is connection-oriented and reliable; UDP is connectionless and "
            "faster but gives no delivery guarantee.\n\nKEY POINTS\n"
            "• TCP: handshake, ordering, retransmission - used for web, email, file transfer.\n"
            "• UDP: no handshake, low latency - used for video calls, gaming, DNS.\n"
            "• Pick TCP when correctness matters, UDP when freshness beats completeness.\n\n"
            "CODE\n```python\nsock = socket.socket(AF_INET, SOCK_DGRAM)\nsock.sendto(b'ping', (host, 9999))\n```")

    def run_demo():
        app.add_transcript("Okay, so let's start. Can you explain the difference between TCP and UDP?")
        app.add_transcript("Sure, so TCP is connection oriented.", "You")
        app.set_status("🧠 ANALYZING...", C["think"])
        app.start_stream("Can you explain the difference between TCP and UDP?")
        if "--thinking" in sys.argv:
            return
        pos = {"i": 0}

        def pump():
            if pos["i"] < len(demo):
                app.append_token(demo[pos["i"]:pos["i"] + 3])
                app.set_level(900 + 700 * math.sin(pos["i"]))
                pos["i"] += 3
                app.after(10, pump)
            else:
                app.set_status("🟢 LISTENING", C["good"])
                if "--tele" in sys.argv:
                    app.toggle_tele()
                if arg("--tab"):
                    app._show_tab(arg("--tab"))
                if "--closed" in sys.argv:
                    app.toggle_panel(force=False)
        app.after(700, pump)

    app.after(800, run_demo)
    if shot:
        def grab():
            from PIL import ImageGrab
            x, y, w, h = app.winfo_x(), app.winfo_y(), app.winfo_width(), app.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(shot)
            app._running = False
            app.destroy()
        app.after(int(arg("--wait") or 5000), grab)
    app.mainloop()
