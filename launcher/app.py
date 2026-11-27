"""ysaizb AI 直播中控桌面管理面板（Tkinter GUI）。"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, scrolledtext, ttk
from typing import Any, Dict, List

import cv2
from PIL import Image, ImageTk

from launcher import backend as bk


UI_THEMES: Dict[str, Dict[str, str]] = {
    "crystal": {
        "label": "白昼冷晶",
        "win_bg": "#F4F7FB",
        "bar_bg": "#E8EEF6",
        "tonal_0": "#E6EDF7",
        "card_bg": "#FFFFFF",
        "tonal_3": "#EDF3FB",
        "pill_bg": "#EEF3FA",
        "bubble_bg": "#EAF1FB",
        "btn_bg": "#E9EFF8",
        "btn_hover": "#DCE6F5",
        "btn_press": "#CFDCEF",
        "card_border": "#CFD9E8",
        "border": "#DCE5F2",
        "specular": "#FFFFFF",
        "fg": "#0F172A",
        "fg_sec": "#334155",
        "fg_muted": "#4B5B73",
        "primary": "#2563EB",
        "primary_hover": "#1D4ED8",
        "primary_disabled": "#93C5FD",
        "primary_fg": "#FFFFFF",
        "rim_glow": "#38BDF8",
        "log_bg": "#F8FAFD",
        "log_fg": "#0F172A",
        "log_border": "#CFD9E8",
        "canvas_bg": "#EDF3FB",
        "stage_bg": "#111827",
        "stage_badge_bg": "#0F172A",
        "ok_bg": "#E6F6F0", "ok_fg": "#059669", "ok_bd": "#A7E3CA",
        "warn_bg": "#FEF3C7", "warn_fg": "#B45309", "warn_bd": "#FCD34D",
        "err_bg": "#FEE2E2", "err_fg": "#DC2626", "err_bd": "#FCA5A5",
        "info_bg": "#EAF1FB", "info_fg": "#2563EB", "info_bd": "#BFDBFE",
        "kb_card_bg": "#FFFFFF",
        "kb_card_well": "#EDF3FB",
        "kb_btn_bg": "#0F172A", "kb_btn_fg": "#FFFFFF",
        "popup_video_bg": "#0F172A", "popup_ctrl_bg": "#1E293B",
    },
    "warm": {
        "label": "暖炭玄岩",
        "win_bg": "#141413",
        "bar_bg": "#10100F",
        "tonal_0": "#0E0D0C",
        "card_bg": "#1B1A18",
        "tonal_3": "#23201D",
        "pill_bg": "#2B2723",
        "bubble_bg": "#26231F",
        "btn_bg": "#22201D",
        "btn_hover": "#2D2A26",
        "btn_press": "#36322D",
        "card_border": "#332F2A",
        "border": "#2A2622",
        "specular": "#3D3832",
        "fg": "#F2EFE9",
        "fg_sec": "#CEC8BC",
        "fg_muted": "#A39E93",
        "primary": "#D97757",
        "primary_hover": "#C25934",
        "primary_disabled": "#6E4A3E",
        "primary_fg": "#FFFFFF",
        "rim_glow": "#E59377",
        "log_bg": "#0E0D0C",
        "log_fg": "#F2EFE9",
        "log_border": "#2E2A26",
        "canvas_bg": "#10100F",
        "stage_bg": "#0E0D0C",
        "stage_badge_bg": "#1B1A18",
        "ok_bg": "#14261E", "ok_fg": "#56A87E", "ok_bd": "#244736",
        "warn_bg": "#2A2212", "warn_fg": "#E5A93B", "warn_bd": "#4B3B1B",
        "err_bg": "#2B1615", "err_fg": "#E5534B", "err_bd": "#4F2422",
        "info_bg": "#291D18", "info_fg": "#E59377", "info_bd": "#4D3226",
        "kb_card_bg": "#23201D",
        "kb_card_well": "#181614",
        "kb_btn_bg": "#2B2723", "kb_btn_fg": "#F2EFE9",
        "popup_video_bg": "#0E0D0C", "popup_ctrl_bg": "#1B1A18",
    },
}


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class LauncherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("泳昇 Ultralight 数字人直播平台")
        self.geometry("1160x850")
        self.minsize(1020, 740)
        self._apply_icon_to(self)

        self.wf = bk.WorkflowController()
        self.cfg: Dict[str, Any] = {}
        self.profs: Dict[str, Any] = {}
        self.lcfg: Dict[str, Any] = bk.load_json(bk.LAUNCHER_CFG_PATH, {})
        self.presets: List[Dict[str, Any]] = []
        self.hosts: List[Dict[str, Any]] = []
        self.kb_docs: List[Dict[str, Any]] = []

        saved_theme = str(self.lcfg.get("ui_theme", "crystal")).strip().lower()
        self._theme_key: str = saved_theme if saved_theme in UI_THEMES else "crystal"
        self._prism_cards: List[Dict[str, Any]] = []
        self._status_pills: Dict[str, tuple] = {}
        self._last_hub_states = (False, False, False)
        self._last_health_badge = ("warn", "诊断: 离线待机")
        self._last_hub_health: Dict[str, Any] | None = None
        self._last_hub_st: Dict[str, Any] | None = None
        self._last_kb_md_path: Path | None = None
        self._train_proc = None
        self._train_running = False

        self._init_styles()
        self._build_ui()
        self.apply_theme(self._theme_key, persist=False)
        self.reload_all_data()
        self._poll_status()

    def _theme(self) -> Dict[str, str]:
        return UI_THEMES.get(self._theme_key, UI_THEMES["crystal"])

    def _init_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        self._apply_ttk_theme(style, self._theme())

    def _apply_ttk_theme(self, style: ttk.Style, t: Dict[str, str]) -> None:
        self.configure(bg=t["win_bg"])
        self.option_add("*TCombobox*Listbox.background", t["card_bg"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["primary"])
        self.option_add("*TCombobox*Listbox.selectForeground", t["primary_fg"])

        style.configure("TFrame", background=t["win_bg"])
        style.configure("Bar.TFrame", background=t["bar_bg"])
        style.configure("Card.TFrame", background=t["card_bg"], relief="flat", borderwidth=0)

        style.configure("TLabel", background=t["win_bg"], foreground=t["fg"], font=("Microsoft YaHei UI", 10))
        style.configure("Bar.TLabel", background=t["bar_bg"], foreground=t["fg"], font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background=t["card_bg"], foreground=t["fg"], font=("Microsoft YaHei UI", 10))
        style.configure("CardHint.TLabel", background=t["card_bg"], foreground=t["fg_muted"], font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=t["bar_bg"], foreground=t["fg"], font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Hint.TLabel", background=t["win_bg"], foreground=t["fg_muted"], font=("Microsoft YaHei UI", 9))

        style.configure("TLabelframe", background=t["card_bg"], bordercolor=t["card_border"], borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=t["card_bg"], foreground=t["primary"], font=("Microsoft YaHei UI", 10, "bold"))

        style.configure(
            "TButton",
            background=t["btn_bg"],
            foreground=t["fg"],
            bordercolor=t["card_border"],
            lightcolor=t["specular"],
            darkcolor=t["border"],
            borderwidth=1,
            relief="solid",
            focusthickness=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(12, 6),
        )
        style.map(
            "TButton",
            background=[("active", t["btn_hover"]), ("pressed", t["btn_press"]), ("disabled", t["tonal_0"])],
            foreground=[("disabled", t["fg_muted"])],
            bordercolor=[("active", t["primary"])],
        )

        style.configure(
            "Primary.TButton",
            background=t["primary"],
            foreground=t["primary_fg"],
            bordercolor=t["primary"],
            lightcolor=t["rim_glow"],
            darkcolor=t["primary_hover"],
            borderwidth=1,
            relief="solid",
            focusthickness=0,
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(18, 9),
        )
        style.map(
            "Primary.TButton",
            background=[("active", t["primary_hover"]), ("pressed", t["primary_hover"]), ("disabled", t["primary_disabled"])],
            bordercolor=[("active", t["rim_glow"])],
        )

        style.configure(
            "CompactPrimary.TButton",
            background=t["primary"],
            foreground=t["primary_fg"],
            bordercolor=t["primary"],
            lightcolor=t["rim_glow"],
            darkcolor=t["primary_hover"],
            borderwidth=1,
            relief="solid",
            focusthickness=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(10, 5),
        )
        style.map(
            "CompactPrimary.TButton",
            background=[("active", t["primary_hover"]), ("pressed", t["primary_hover"]), ("disabled", t["primary_disabled"])],
            bordercolor=[("active", t["rim_glow"])],
        )

        style.configure(
            "Compact.TButton",
            background=t["btn_bg"],
            foreground=t["fg"],
            bordercolor=t["card_border"],
            lightcolor=t["specular"],
            darkcolor=t["border"],
            borderwidth=1,
            relief="solid",
            focusthickness=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(7, 5),
        )
        style.map(
            "Compact.TButton",
            background=[("active", t["btn_hover"]), ("pressed", t["btn_press"]), ("disabled", t["tonal_0"])],
            bordercolor=[("active", t["primary"])],
        )

        style.configure(
            "TCheckbutton",
            background=t["win_bg"],
            foreground=t["fg"],
            indicatorbackground=t["card_bg"],
            indicatorforeground=t["primary_fg"],
            upperbordercolor=t["card_border"],
            lowerbordercolor=t["card_border"],
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "TCheckbutton",
            background=[("active", t["win_bg"])],
            foreground=[("active", t["fg"])],
            indicatorbackground=[("selected", t["primary"]), ("!selected", t["card_bg"])],
            indicatorforeground=[("selected", t["primary_fg"])],
        )
        style.configure(
            "Card.TCheckbutton",
            background=t["card_bg"],
            foreground=t["fg"],
            indicatorbackground=t["tonal_3"],
            indicatorforeground=t["primary_fg"],
            upperbordercolor=t["card_border"],
            lowerbordercolor=t["card_border"],
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", t["card_bg"])],
            foreground=[("active", t["fg"])],
            indicatorbackground=[("selected", t["primary"]), ("!selected", t["tonal_3"])],
            indicatorforeground=[("selected", t["primary_fg"])],
        )

        style.configure(
            "TEntry",
            fieldbackground=t["card_bg"],
            foreground=t["fg"],
            insertcolor=t["primary"],
            bordercolor=t["card_border"],
            lightcolor=t["primary"],
            darkcolor=t["border"],
            borderwidth=1,
            padding=5,
        )
        style.map("TEntry", bordercolor=[("focus", t["primary"])])

        style.configure(
            "TCombobox",
            fieldbackground=t["card_bg"],
            background=t["btn_bg"],
            foreground=t["fg"],
            bordercolor=t["card_border"],
            lightcolor=t["primary"],
            darkcolor=t["border"],
            arrowcolor=t["primary"],
            borderwidth=1,
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", t["card_bg"]), ("disabled", t["tonal_0"])],
            foreground=[("readonly", t["fg"]), ("disabled", t["fg_muted"])],
            selectbackground=[("readonly", t["card_bg"])],
            selectforeground=[("readonly", t["fg"])],
            bordercolor=[("focus", t["primary"]), ("active", t["primary"])],
        )

        style.configure(
            "TScrollbar",
            background=t["card_border"],
            troughcolor=t["tonal_0"],
            bordercolor=t["tonal_0"],
            arrowcolor=t["fg_muted"],
            relief="flat",
        )
        style.map("TScrollbar", background=[("active", t["primary"])])

        style.configure("TNotebook", background=t["bar_bg"], bordercolor=t["border"], borderwidth=0, tabmargins=[4, 4, 4, 0])
        style.configure(
            "TNotebook.Tab",
            background=t["tonal_0"],
            foreground=t["fg_muted"],
            bordercolor=t["border"],
            lightcolor=t["specular"],
            borderwidth=0,
            focusthickness=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(15, 7),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", t["card_bg"]), ("active", t["tonal_3"])],
            foreground=[("selected", t["primary"]), ("active", t["fg"])],
        )

        style.configure(
            "Treeview",
            background=t["card_bg"],
            fieldbackground=t["card_bg"],
            foreground=t["fg"],
            bordercolor=t["card_border"],
            rowheight=26,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background=t["tonal_3"],
            foreground=t["primary"],
            bordercolor=t["border"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", t["primary"])],
            foreground=[("selected", t["primary_fg"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=t["primary"],
            troughcolor=t["tonal_0"],
            bordercolor=t["border"],
            thickness=12,
        )

    def _make_card(self, parent: tk.Misc, title: str, padding: int = 10, fill: str = "x", expand: bool = False, pady=(0, 8), side: str | None = None, padx=0) -> ttk.Frame:
        """创建具备 Obsidian Prism 镜面顶边高光与细线外框的冷晶/玄岩卡片容器。"""
        t = self._theme()
        outer = tk.Frame(parent, bg=t["card_border"], padx=1, pady=1)
        pack_kw: Dict[str, Any] = {"fill": fill, "expand": expand, "pady": pady, "padx": padx}
        if side:
            pack_kw["side"] = side
        outer.pack(**pack_kw)

        rim = tk.Frame(outer, bg=t["specular"], height=1)
        rim.pack(fill="x")

        hdr = tk.Frame(outer, bg=t["tonal_3"], padx=10, pady=5)
        hdr.pack(fill="x")
        lbl = tk.Label(hdr, text=title, bg=t["tonal_3"], fg=t["primary"], font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        lbl.pack(side="left", fill="x")

        hdr_sep = tk.Frame(outer, bg=t["border"], height=1)
        hdr_sep.pack(fill="x")

        body = ttk.Frame(outer, style="Card.TFrame", padding=padding)
        body.pack(fill="both", expand=True)

        self._prism_cards.append({"outer": outer, "rim": rim, "hdr": hdr, "sep": hdr_sep, "lbl": lbl})
        return body

    def _make_status_pill(self, parent: tk.Misc, key: str, text: str, command=None) -> None:
        t = self._theme()
        wrap = tk.Frame(parent, bg=t["pill_bg"], highlightbackground=t["border"], highlightthickness=1, padx=9, pady=4, cursor="hand2" if command else "")
        wrap.pack(side="left", padx=4)
        dot = tk.Label(wrap, text="●", bg=t["pill_bg"], fg=t["fg_muted"], font=("Microsoft YaHei UI", 8), cursor="hand2" if command else "")
        dot.pack(side="left", padx=(0, 5))
        lbl = tk.Label(wrap, text=text, bg=t["pill_bg"], fg=t["fg"], font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2" if command else "")
        lbl.pack(side="left")
        if command is not None:
            for w in (wrap, dot, lbl):
                w.bind("<Button-1>", lambda _e, cb=command: cb())
        self._status_pills[key] = (wrap, dot, lbl)

    def _paint_pill(self, key: str, tone: str, text: str | None = None) -> None:
        if key not in self._status_pills:
            return
        t = self._theme()
        wrap, dot, lbl = self._status_pills[key]
        bg = t.get(f"{tone}_bg", t["pill_bg"])
        fg = t.get(f"{tone}_fg", t["fg"])
        bd = t.get(f"{tone}_bd", t["border"])
        wrap.config(bg=bg, highlightbackground=bd)
        dot.config(bg=bg, fg=fg)
        lbl.config(bg=bg, fg=fg)
        if text is not None:
            lbl.config(text=text)

    def _style_scrolled_text(self, w: scrolledtext.ScrolledText) -> None:
        t = self._theme()
        w.configure(
            bg=t["log_bg"],
            fg=t["log_fg"],
            insertbackground=t["primary"],
            selectbackground=t["primary"],
            selectforeground="#FFFFFF",
            highlightthickness=1,
            highlightbackground=t["log_border"],
            highlightcolor=t["primary"],
            relief="flat",
            borderwidth=0,
        )

    def apply_theme(self, theme_key: str, persist: bool = True) -> None:
        if theme_key not in UI_THEMES:
            theme_key = "crystal"
        self._theme_key = theme_key
        t = self._theme()
        self._apply_ttk_theme(ttk.Style(self), t)

        # 顶栏与分隔线
        if hasattr(self, "top_bar"):
            self.top_bar.configure(bg=t["bar_bg"])
            self.top_sep.configure(bg=t["border"])
            self.lbl_app_title.configure(bg=t["bar_bg"], fg=t["fg"])
            self.right_top.configure(bg=t["bar_bg"])
            self.theme_switch_wrap.configure(bg=t["tonal_0"], highlightbackground=t["card_border"])
            is_cry = theme_key == "crystal"
            self.btn_theme_crystal.configure(
                bg=t["card_bg"] if is_cry else t["tonal_0"],
                fg=t["primary"] if is_cry else t["fg_muted"],
                activebackground=t["tonal_3"],
                activeforeground=t["fg"],
            )
            self.btn_theme_warm.configure(
                bg=t["pill_bg"] if not is_cry else t["tonal_0"],
                fg=t["primary"] if not is_cry else t["fg_muted"],
                activebackground=t["tonal_3"],
                activeforeground=t["fg"],
            )

        # 所有棱镜浮卡
        for c in self._prism_cards:
            c["outer"].configure(bg=t["card_border"])
            c["rim"].configure(bg=t["specular"])
            c["hdr"].configure(bg=t["tonal_3"])
            c["sep"].configure(bg=t["border"])
            c["lbl"].configure(bg=t["tonal_3"], fg=t["primary"])

        # 状态胶囊
        lt_ok, hub_ok, obs_ok = self._last_hub_states
        self._paint_pill("lt", "ok" if lt_ok else "err", "Ultralight: 运行中" if lt_ok else "Ultralight: 未启动")
        self._paint_pill("hub", "ok" if hub_ok else "err", "中控: 运行中" if hub_ok else "中控: 未启动")
        self._paint_pill("obs", "ok" if obs_ok else "warn", "OBS WS: 已连接" if obs_ok else "OBS WS: 未连接")
        h_tone, h_txt = self._last_health_badge
        self._paint_pill("health", h_tone, h_txt)
        self._paint_pill("mode", "info", f"{t['label']} · Ultralight")

        # 文本框与画布
        for wname in ("txt_log", "txt_prompt", "txt_live_log", "txt_plat_guide", "txt_train_log"):
            w = getattr(self, wname, None)
            if w is not None:
                self._style_scrolled_text(w)
        if hasattr(self, "tr_plan_box"):
            self.tr_plan_box.configure(bg=t["tonal_3"], highlightbackground=t["card_border"])
            self.lbl_tr_ai_title.configure(bg=t["tonal_3"], fg=t["primary"])
            self.lbl_tr_ai_plan.configure(bg=t["tonal_3"], fg=t["fg"])
            self.lbl_tr_ai_warn.configure(bg=t["tonal_3"], fg=t["warn_fg"])
        if hasattr(self, "kb_canvas"):
            self.kb_canvas.configure(bg=t["card_bg"])
        if hasattr(self, "live_canvas"):
            self.live_canvas.configure(bg=t["canvas_bg"])
            self._render_live_preview(self._last_hub_st)
        if hasattr(self, "kb_preview_inner"):
            self._render_kb_preview(self._last_kb_md_path)

        if persist:
            self.lcfg["ui_theme"] = theme_key
            bk.save_json(bk.LAUNCHER_CFG_PATH, self.lcfg)

    def _apply_icon_to(self, win: tk.Wm) -> None:
        cands = (
            [Path(sys._MEIPASS) / "app_icon.ico"] if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS") else []
        ) + [Path(__file__).resolve().parent / "app_icon.ico"]
        for p in cands:
            if p.is_file():
                try:
                    win.iconbitmap(str(p))
                    break
                except Exception:
                    pass

    def _theme_toplevel(self, win: tk.Toplevel, title: str, geometry: str) -> None:
        t = self._theme()
        win.title(title)
        win.geometry(geometry)
        win.configure(bg=t["win_bg"])
        self._apply_icon_to(win)

    def _build_ui(self) -> None:
        t = self._theme()
        # 顶部冰晶 / 玄岩标题栏
        self.top_bar = tk.Frame(self, bg=t["bar_bg"], padx=16, pady=10)
        self.top_bar.pack(fill="x")
        self.lbl_app_title = tk.Label(self.top_bar, text="泳昇 Ultralight 数字人直播平台", bg=t["bar_bg"], fg=t["fg"], font=("Microsoft YaHei UI", 15, "bold"))
        self.lbl_app_title.pack(side="left")

        self.right_top = tk.Frame(self.top_bar, bg=t["bar_bg"])
        self.right_top.pack(side="right")

        # 主题切换分段胶囊：白昼冷晶 / 暖炭玄岩
        self.theme_switch_wrap = tk.Frame(self.right_top, bg=t["tonal_0"], highlightbackground=t["card_border"], highlightthickness=1, padx=2, pady=2)
        self.theme_switch_wrap.pack(side="left", padx=(0, 8))
        self.btn_theme_crystal = tk.Button(
            self.theme_switch_wrap, text="☀ 白昼冷晶", relief="flat", bd=0, padx=8, pady=2,
            font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2",
            command=lambda: self.apply_theme("crystal"),
        )
        self.btn_theme_crystal.pack(side="left")
        self.btn_theme_warm = tk.Button(
            self.theme_switch_wrap, text="🌙 暖炭玄岩", relief="flat", bd=0, padx=8, pady=2,
            font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2",
            command=lambda: self.apply_theme("warm"),
        )
        self.btn_theme_warm.pack(side="left")

        self._make_status_pill(self.right_top, "lt", "Ultralight: 未启动")
        self._make_status_pill(self.right_top, "hub", "中控: 未启动")
        self._make_status_pill(self.right_top, "obs", "OBS WS: 未连接")
        self._make_status_pill(self.right_top, "health", "🩺 诊断/工具箱", command=self.on_open_health_dialog)
        self._make_status_pill(self.right_top, "guide", "🧸 傻瓜配置", command=self.on_open_config_wizard)
        self._make_status_pill(self.right_top, "mode", f"{t['label']} · Ultralight")

        self.top_sep = tk.Frame(self, bg=t["border"], height=1)
        self.top_sep.pack(fill="x")

        # 主选项卡
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=10)

        self.tab_wf = ttk.Frame(self.nb, padding=12)
        self.tab_ai = ttk.Frame(self.nb, padding=12)
        self.tab_host = ttk.Frame(self.nb, padding=12)
        self.tab_kb = ttk.Frame(self.nb, padding=12)
        self.tab_obs = ttk.Frame(self.nb, padding=12)
        self.tab_plat = ttk.Frame(self.nb, padding=12)
        self.tab_train = ttk.Frame(self.nb, padding=10)

        self.nb.add(self.tab_wf, text="🚀 开播预设与工作流")
        self.nb.add(self.tab_ai, text="1. AI 模型与声音")
        self.nb.add(self.tab_host, text="2. 主播与直播主题")
        self.nb.add(self.tab_kb, text="3. 知识库主题")
        self.nb.add(self.tab_obs, text="4. OBS 与直播控制")
        self.nb.add(self.tab_plat, text="5. 平台配置")
        self.nb.add(self.tab_train, text="🎓 主播训练与管理")

        self._build_tab_workflow()
        self._build_tab_ai()
        self._build_tab_host()
        self._build_tab_kb()
        self._build_tab_obs()
        self._build_tab_platform()
        self._build_tab_train()
        self.nb.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    # ------------------------------------------------------------
    # Tab 0: 开播预设与一键启动工作流
    # ------------------------------------------------------------
    def _build_tab_workflow(self) -> None:
        pbox = self._make_card(self.tab_wf, "常用开播预设方案（一键保存 / 读取）", padding=8)
        self.var_preset = tk.StringVar()
        self.cb_preset = ttk.Combobox(pbox, textvariable=self.var_preset, state="readonly", width=40)
        self.cb_preset.pack(side="left", padx=(0, 8))
        self.cb_preset.bind("<<ComboboxSelected>>", lambda _e: self.apply_selected_preset())
        ttk.Button(pbox, text="加载此预设", command=self.apply_selected_preset).pack(side="left", padx=4)
        ttk.Button(pbox, text="另存前三项为预设", command=self.save_current_as_preset).pack(side="left", padx=4)
        ttk.Button(pbox, text="删除预设", command=self.delete_selected_preset).pack(side="left", padx=4)

        gbox = self._make_card(self.tab_wf, f"全局配置专属目录（{bk.GLOBAL_CFG_DIR}）", padding=8)
        self.var_gcfg = tk.StringVar()
        self.cb_gcfg = ttk.Combobox(gbox, textvariable=self.var_gcfg, state="readonly", width=34)
        self.cb_gcfg.pack(side="left", padx=(0, 8))
        ttk.Button(gbox, text="读取加载全局配置", command=self.on_load_global_cfg).pack(side="left", padx=3)
        ttk.Button(gbox, text="保存为新全局配置", command=self.on_save_global_cfg).pack(side="left", padx=3)
        ttk.Button(gbox, text="手动浏览 .json 加载", command=self.on_browse_global_cfg).pack(side="left", padx=3)

        sbox = self._make_card(self.tab_wf, "当前开播配置总览（启动前自动同步写入）", padding=10)
        self.lbl_summary = ttk.Label(sbox, text="加载中...", justify="left", style="Card.TLabel")
        self.lbl_summary.pack(anchor="w")

        opt_row = ttk.Frame(self.tab_wf)
        opt_row.pack(fill="x", pady=(0, 8))
        self.var_auto_obs = tk.BooleanVar(value=True)
        self.var_open_web = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_row, text="启动时自动切换并拉起 OBS Studio（全 EXE 模式运行，OBS 直接拉取 /stage 舞台）", variable=self.var_auto_obs).pack(side="left")

        btn_row = ttk.Frame(self.tab_wf)
        btn_row.pack(fill="x", pady=(0, 8))
        self.btn_start = ttk.Button(btn_row, text="🚀 一键启动直播工作流", style="Primary.TButton", command=self.on_start_workflow)
        self.btn_start.pack(side="left", padx=(0, 10))
        ttk.Button(btn_row, text="⏹ 一键停止后台服务", command=self.on_stop_workflow).pack(side="left", padx=4)
        ttk.Button(btn_row, text="💾 仅保存全部配置", command=self.save_all_configs).pack(side="left", padx=4)

        lbox = self._make_card(self.tab_wf, "工作流运行日志", padding=8, fill="both", expand=True, pady=0)
        self.txt_log = scrolledtext.ScrolledText(lbox, height=11, font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True)

    # ------------------------------------------------------------
    # Tab 1: AI 模型与声音
    # ------------------------------------------------------------
    def _build_tab_ai(self) -> None:
        llm_frame = self._make_card(self.tab_ai, "大模型容灾池配置（按顺序调用，失败自动切换）", padding=10, pady=(0, 10))

        row1 = ttk.Frame(llm_frame, style="Card.TFrame")
        row1.pack(fill="x", pady=(0, 8))
        ttk.Label(row1, text="选择节点：", style="Card.TLabel").pack(side="left")
        self.var_llm_idx = tk.StringVar()
        self.cb_llm = ttk.Combobox(row1, textvariable=self.var_llm_idx, state="readonly", width=34)
        self.cb_llm.pack(side="left", padx=6)
        self.cb_llm.bind("<<ComboboxSelected>>", lambda _e: self._load_llm_fields())
        self.var_llm_en = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="启用该节点", variable=self.var_llm_en, style="Card.TCheckbutton").pack(side="left", padx=8)
        ttk.Button(row1, text="测试当前节点连通性", command=self.on_test_llm).pack(side="right")

        grid = ttk.Frame(llm_frame, style="Card.TFrame")
        grid.pack(fill="x")
        ttk.Label(grid, text="Base URL：", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.var_llm_url = tk.StringVar()
        ttk.Entry(grid, textvariable=self.var_llm_url, width=56).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(grid, text="模型名称：", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.var_llm_model = tk.StringVar()
        ttk.Entry(grid, textvariable=self.var_llm_model, width=56).grid(row=1, column=1, sticky="ew", padx=6)

        ttk.Label(grid, text="API Key：", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.var_llm_key = tk.StringVar()
        ttk.Entry(grid, textvariable=self.var_llm_key, width=56, show="*").grid(row=2, column=1, sticky="ew", padx=6)
        grid.columnconfigure(1, weight=1)

        llm_btns = ttk.Frame(llm_frame, style="Card.TFrame")
        llm_btns.pack(fill="x", pady=(8, 0))
        ttk.Button(llm_btns, text="🔑 一键全测全部 Key / 向量接口", command=self.on_test_all_keys_cli).pack(side="left")
        ttk.Button(llm_btns, text="保存大模型节点修改", command=self.on_save_llm).pack(side="right")

        tts_frame = self._make_card(self.tab_ai, "语音合成（TTS）设置", padding=10, pady=0)
        tgrid = ttk.Frame(tts_frame, style="Card.TFrame")
        tgrid.pack(fill="x")
        self._sf_custom_voices: List[Any] = []
        self.var_edge_voice = tk.StringVar()
        self.var_sf_voice = tk.StringVar()
        self.var_gsv_ref = tk.StringVar()
        self.var_gsv_prompt = tk.StringVar()
        self.var_active_voice = tk.StringVar()
        self.var_voice_extra = tk.StringVar()

        ttk.Label(tgrid, text="TTS 语音来源：", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        mode_row = ttk.Frame(tgrid, style="Card.TFrame")
        mode_row.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        self.var_tts_mode = tk.StringVar()
        self.cb_tts_mode = ttk.Combobox(
            mode_row,
            textvariable=self.var_tts_mode,
            values=[lbl for _, lbl in bk.TTS_MODE_OPTIONS],
            state="readonly",
            width=40,
        )
        self.cb_tts_mode.pack(side="left")
        self.cb_tts_mode.bind("<<ComboboxSelected>>", lambda _e: self._on_tts_mode_changed())
        self.lbl_tts_hint = ttk.Label(mode_row, text="", style="CardHint.TLabel")
        self.lbl_tts_hint.pack(side="left", padx=(10, 0))

        self.lbl_active_voice = ttk.Label(tgrid, text="当前可用声线：", style="Card.TLabel")
        self.lbl_active_voice.grid(row=1, column=0, sticky="w", pady=4)
        v_row = ttk.Frame(tgrid, style="Card.TFrame")
        v_row.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        self.cb_active_voice = ttk.Combobox(v_row, textvariable=self.var_active_voice, width=42)
        self.cb_active_voice.pack(side="left", fill="x", expand=True)
        self.cb_active_voice.bind("<<ComboboxSelected>>", lambda _e: self._on_active_voice_selected())
        self.btn_voice_action = ttk.Button(v_row, text="↻ 匹配主播声线", command=self.on_voice_action_click)
        self.btn_voice_action.pack(side="left", padx=(8, 0))

        self.lbl_voice_extra = ttk.Label(tgrid, text="当前声线 ID：", style="Card.TLabel")
        self.lbl_voice_extra.grid(row=2, column=0, sticky="w", pady=4)
        self.ent_voice_extra = ttk.Entry(tgrid, textvariable=self.var_voice_extra, width=52)
        self.ent_voice_extra.grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        self.ent_voice_extra.bind("<KeyRelease>", lambda _e: self._on_voice_extra_edited())

        ttk.Label(tgrid, text="说话语气前缀（硅基流动预设/自定义）：", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.var_sf_emo = tk.StringVar()
        ttk.Combobox(tgrid, textvariable=self.var_sf_emo, values=bk.TONE_PRESETS, width=50).grid(row=3, column=1, sticky="ew", padx=6, pady=4)
        tgrid.columnconfigure(1, weight=1)
        tts_btns = ttk.Frame(tts_frame, style="Card.TFrame")
        tts_btns.pack(fill="x", pady=(8, 0))
        ttk.Button(tts_btns, text="🎙 上传录音克隆硅基新音色", command=self.on_clone_voice_dialog).pack(side="left")
        ttk.Button(tts_btns, text="保存声音设置", command=self.on_save_tts).pack(side="right")

    # ------------------------------------------------------------
    # Tab 2: 主播与直播主题
    # ------------------------------------------------------------
    def _build_tab_host(self) -> None:
        hbox = self._make_card(self.tab_host, "选择开播主播 & 直播主题", padding=10, pady=(0, 10))
        hg = ttk.Frame(hbox, style="Card.TFrame")
        hg.pack(fill="x")

        ttk.Label(hg, text="开播主播形象：", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        av_row = ttk.Frame(hg, style="Card.TFrame")
        av_row.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        self.var_avatar = tk.StringVar()
        self.cb_avatar = ttk.Combobox(av_row, textvariable=self.var_avatar, state="readonly", width=34)
        self.cb_avatar.pack(side="left")
        self.cb_avatar.bind("<<ComboboxSelected>>", lambda _e: self._on_host_selected())
        self.lbl_host_voice = ttk.Label(av_row, text="", style="CardHint.TLabel")
        self.lbl_host_voice.pack(side="left", padx=(10, 0))

        ttk.Label(hg, text="主播称呼 ({name})：", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.var_host_name = tk.StringVar()
        ttk.Entry(hg, textvariable=self.var_host_name, width=36).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(hg, text="直播主题 / 风格：", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.var_live_style = tk.StringVar()
        ttk.Entry(hg, textvariable=self.var_live_style, width=62).grid(row=2, column=1, sticky="ew", padx=6)

        ttk.Label(hg, text="Ultralight 推理参数：", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ul_row = ttk.Frame(hg, style="Card.TFrame")
        ul_row.grid(row=3, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(ul_row, text="音频特征：", style="Card.TLabel").pack(side="left")
        self.var_ul_asr = tk.StringVar(value="hubert")
        ttk.Combobox(ul_row, textvariable=self.var_ul_asr, values=["hubert", "wenet"], state="readonly", width=10).pack(side="left", padx=(4, 12))
        ttk.Label(ul_row, text="推理后端：", style="Card.TLabel").pack(side="left")
        self.var_ul_backend = tk.StringVar(value="onnx")
        ttk.Combobox(ul_row, textvariable=self.var_ul_backend, values=["onnx", "torch"], state="readonly", width=10).pack(side="left", padx=(4, 12))
        ttk.Label(ul_row, text="(hubert=25fps高质量 / wenet=20fps轻量；onnx推荐)", style="CardHint.TLabel").pack(side="left")
        hg.columnconfigure(1, weight=1)

        prompt_hdr = ttk.Frame(hbox, style="Card.TFrame")
        prompt_hdr.pack(fill="x", pady=(6, 2))
        ttk.Label(prompt_hdr, text="人设系统提示词（支持 {name} 占位符）：", style="Card.TLabel").pack(side="left")
        ttk.Button(prompt_hdr, text="✨ AI 根据主题/风格生成人设", command=self.on_ai_gen_prompt).pack(side="right")
        self.txt_prompt = scrolledtext.ScrolledText(hbox, height=4, font=("Microsoft YaHei UI", 9))
        self.txt_prompt.pack(fill="x")
        ttk.Button(hbox, text="保存主播与直播主题", command=self.on_save_host).pack(anchor="e", pady=(6, 0))

        # 增加新主播 / 上传真实动作短片
        abox = self._make_card(self.tab_host, "增加新主播（中文名自动生成英文ID） / 上传真实动作短片", padding=10, pady=0)
        arow1 = ttk.Frame(abox, style="Card.TFrame")
        arow1.pack(fill="x", pady=4)
        ttk.Label(arow1, text="主播中文名：", style="Card.TLabel").pack(side="left")
        self.var_new_aname = tk.StringVar()
        ent_cn = ttk.Entry(arow1, textvariable=self.var_new_aname, width=16)
        ent_cn.pack(side="left", padx=4)
        ttk.Label(arow1, text="英文 ID（自动/自定义）：", style="Card.TLabel").pack(side="left")
        self.var_new_aid = tk.StringVar()
        self._aid_manual = False
        ent_en = ttk.Entry(arow1, textvariable=self.var_new_aid, width=18)
        ent_en.pack(side="left", padx=4)
        ent_en.bind("<KeyRelease>", lambda _e: setattr(self, "_aid_manual", bool(self.var_new_aid.get().strip())))
        ent_cn.bind("<KeyRelease>", lambda _e: self._auto_fill_english_id())
        ttk.Button(arow1, text="保存新主播配置", command=self.on_register_new_host).pack(side="left", padx=4)
        ttk.Button(arow1, text="🎓 前往训练/管理主播形象", command=self._goto_train_tab_prefilled).pack(side="left", padx=4)

        arow2 = ttk.Frame(abox, style="Card.TFrame")
        arow2.pack(fill="x", pady=(6, 0))
        ttk.Label(arow2, text="为当前主播上传真实动作 MP4：", style="Card.TLabel").pack(side="left")
        for act_id, act_lbl in (("sunglasses", "戴墨镜"), ("milktea", "拿奶茶"), ("heart", "比心"), ("wave", "挥手"), ("thumbsup", "点赞")):
            ttk.Button(arow2, text=act_lbl, command=lambda a=act_id, l=act_lbl: self.on_upload_action_clip(a, l)).pack(side="left", padx=3)

    # ------------------------------------------------------------
    # Tab 3: 知识库主题（按主播主目录 -> 分类目录 -> 讲稿+视频同目录预览 & 弹窗播放）
    # ------------------------------------------------------------
    def _build_tab_kb(self) -> None:
        top_kb = self._make_card(self.tab_kb, "主播知识库与讲稿联动筛选", padding=8, pady=(0, 8))

        r1 = ttk.Frame(top_kb, style="Card.TFrame")
        r1.pack(fill="x", pady=(0, 4))
        ttk.Label(r1, text="主播知识库：", style="Card.TLabel").pack(side="left")
        self.var_kb_host = tk.StringVar()
        self.cb_kb_host = ttk.Combobox(r1, textvariable=self.var_kb_host, state="readonly", width=28)
        self.cb_kb_host.pack(side="left", padx=6)
        self.cb_kb_host.bind("<<ComboboxSelected>>", lambda _e: self._on_kb_host_selected())
        ttk.Button(r1, text="📂 打开主播知识库", command=self.on_open_host_kb_dir).pack(side="right")

        r2 = ttk.Frame(top_kb, style="Card.TFrame")
        r2.pack(fill="x", pady=(0, 4))
        ttk.Label(r2, text="讲稿预设选择框：", style="Card.TLabel").pack(side="left")
        self.var_kb_doc = tk.StringVar()
        self.cb_kb_doc = ttk.Combobox(r2, textvariable=self.var_kb_doc, state="readonly", width=32)
        self.cb_kb_doc.pack(side="left", padx=6)
        self.cb_kb_doc.bind("<<ComboboxSelected>>", lambda _e: self._on_kb_doc_selected())
        self.var_kb_narration = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="章节连播", variable=self.var_kb_narration, style="Card.TCheckbutton").pack(side="left", padx=4)
        ttk.Button(r2, text="🔄 扫描刷新", command=self.on_scan_kb_and_bg).pack(side="left", padx=3)
        ttk.Button(r2, text="📂 打开知识库目录", command=self.on_open_kb_root_dir).pack(side="right")

        r3 = ttk.Frame(top_kb, style="Card.TFrame")
        r3.pack(fill="x")
        ttk.Button(r3, text="导入 .md/.txt 到知识库", command=self.on_import_kb).pack(side="left", padx=(0, 4))
        ttk.Button(r3, text="新建讲稿到知识库", command=self.on_new_kb_dialog).pack(side="left", padx=4)
        ttk.Button(r3, text="添加视频到当前讲稿目录", command=self.on_add_video_to_kb_folder).pack(side="left", padx=4)
        ttk.Button(r3, text="🔄 重建检索索引", command=self.on_kb_reindex).pack(side="left", padx=4)
        ttk.Button(r3, text="🧪 测试问法检索", command=self.on_kb_test_search_dialog).pack(side="left", padx=4)
        ttk.Button(r3, text="🎯 主题点播目录", command=self.on_open_topic_dialog).pack(side="left", padx=4)
        ttk.Button(r3, text="💾 保存知识库设置", style="Primary.TButton", command=self.on_save_kb).pack(side="right")
        self.var_kb_bg = tk.StringVar()
        self.var_kb_theme_dir = tk.StringVar(value="默认分类")

        # 讲稿文档与视频预览框（单节文本 + 单节视频缩略图，点击弹窗播放）
        pbox = self._make_card(self.tab_kb, "讲稿文档与联动视频预览（点击视频缩略图可弹窗播放）", padding=6, fill="both", expand=True, pady=0)
        self.kb_canvas = tk.Canvas(pbox, bg=self._theme()["card_bg"], highlightthickness=0)
        sb = ttk.Scrollbar(pbox, orient="vertical", command=self.kb_canvas.yview)
        self.kb_preview_inner = ttk.Frame(self.kb_canvas, style="Card.TFrame")
        self.kb_preview_inner.bind("<Configure>", lambda _e: self.kb_canvas.configure(scrollregion=self.kb_canvas.bbox("all")))
        self._kb_win_id = self.kb_canvas.create_window((0, 0), window=self.kb_preview_inner, anchor="nw")
        self.kb_canvas.bind("<Configure>", lambda e: self.kb_canvas.itemconfigure(self._kb_win_id, width=e.width))
        self.kb_canvas.configure(yscrollcommand=sb.set)
        self.kb_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._kb_thumbs: List[Any] = []
        self._host_kb_items: List[Dict[str, Any]] = []

    # ------------------------------------------------------------
    # Tab 4: OBS 多模式配置与拉起 + Root 安卓模拟摄像头 + 直播预览与直播记录
    # ------------------------------------------------------------
    def _build_tab_obs(self) -> None:
        # 1. OBS Studio 路径、多模式预设与 Root 安卓推流合并为紧凑单卡，为下方预览与互动区腾出充足高度
        obox = self._make_card(self.tab_obs, f"OBS Studio 多模式配置与 Root 安卓推流（配置目录：{bk.OBS_PRESETS_DIR}）", padding=5, pady=(0, 4))

        r_path = ttk.Frame(obox, style="Card.TFrame")
        r_path.pack(fill="x", pady=(0, 4))
        ttk.Label(r_path, text="OBS 路径：", style="Card.TLabel").pack(side="left")
        self.var_obs_path = tk.StringVar()
        ttk.Entry(r_path, textvariable=self.var_obs_path, width=36).pack(side="left", fill="x", expand=True, padx=(2, 6))
        ttk.Button(r_path, text="自动探寻", style="Compact.TButton", command=self.on_auto_find_obs).pack(side="left", padx=2)
        ttk.Button(r_path, text="浏览 obs64.exe", style="Compact.TButton", command=self.on_browse_obs).pack(side="left", padx=2)
        ttk.Button(r_path, text="📋 复制舞台URL", style="Compact.TButton", command=self.on_copy_stage_url).pack(side="left", padx=2)
        ttk.Button(r_path, text="🌐 网页控制台", style="Compact.TButton", command=lambda: webbrowser.open("http://127.0.0.1:28910/")).pack(side="left", padx=2)
        ttk.Button(r_path, text="▶ 立即拉起/同步 OBS", style="CompactPrimary.TButton", command=self.on_launch_obs_now).pack(side="right", padx=(4, 0))

        r_mode = ttk.Frame(obox, style="Card.TFrame")
        r_mode.pack(fill="x", pady=(0, 4))
        ttk.Label(r_mode, text="OBS 模式：", style="Card.TLabel").pack(side="left")
        self.var_obs_mode = tk.StringVar()
        self.cb_obs_mode = ttk.Combobox(r_mode, textvariable=self.var_obs_mode, state="readonly", width=21, values=[p["label"] for p in bk.OBS_PRESETS])
        self.cb_obs_mode.pack(side="left", padx=(2, 6))
        self.cb_obs_mode.bind("<<ComboboxSelected>>", lambda _e: self._on_obs_mode_changed())
        ttk.Button(r_mode, text="生成中文配置", style="Compact.TButton", command=self.on_generate_obs_presets).pack(side="left", padx=2)
        ttk.Button(r_mode, text="自动导入 OBS", style="Compact.TButton", command=self.on_import_obs_now).pack(side="left", padx=2)
        ttk.Button(r_mode, text="手动导入文件", style="Compact.TButton", command=self.on_manual_import_obs).pack(side="left", padx=2)
        self.lbl_obs_profiles = ttk.Label(r_mode, text="本机：--", style="CardHint.TLabel", justify="left")
        self.lbl_obs_profiles.pack(side="left", fill="x", expand=True, padx=(8, 0))

        r_adb = ttk.Frame(obox, style="Card.TFrame")
        r_adb.pack(fill="x")
        self.var_android_vcam = tk.BooleanVar(value=False)
        ttk.Checkbutton(r_adb, text="Root安卓虚拟摄像头", variable=self.var_android_vcam, style="Card.TCheckbutton").pack(side="left", padx=(0, 6))
        ttk.Label(r_adb, text="RTMP：", style="Card.TLabel").pack(side="left")
        self.var_android_rtmp = tk.StringVar(value="rtmp://127.0.0.1:1935/live")
        ttk.Entry(r_adb, textvariable=self.var_android_rtmp, width=22).pack(side="left", padx=(2, 6))
        ttk.Label(r_adb, text="密钥：", style="Card.TLabel").pack(side="left")
        self.var_android_key = tk.StringVar(value="vcam")
        ttk.Entry(r_adb, textvariable=self.var_android_key, width=8).pack(side="left", padx=(2, 6))
        ttk.Button(r_adb, text="检测手机", style="Compact.TButton", command=self.on_check_adb).pack(side="left", padx=2)
        ttk.Button(r_adb, text="ADB转发(1935)", style="Compact.TButton", command=self.on_adb_reverse).pack(side="left", padx=2)
        ttk.Button(r_adb, text="写入OBS推流", style="Compact.TButton", command=self.on_apply_android_obs).pack(side="right")

        # 2. 实时调度 · 讲解控制 · 绿幕背景抠像
        cbox = self._make_card(self.tab_obs, "🎛 直播控制中心（自动策略 · 讲解调度 · 绿幕抠像 · 章节与字幕免费字体）", padding=5, pady=(0, 4))
        crow1 = ttk.Frame(cbox, style="Card.TFrame")
        crow1.pack(fill="x", pady=(0, 4))
        self.var_auto_reply = tk.BooleanVar(value=True)
        self.var_gift_int = tk.BooleanVar(value=True)
        self.var_auto_warmup = tk.BooleanVar(value=True)
        self.var_warmup_gap = tk.IntVar(value=25)
        ttk.Checkbutton(crow1, text="自动答疑", variable=self.var_auto_reply, style="Card.TCheckbutton", command=self.on_save_sched_cfg).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(crow1, text="礼物打断连播", variable=self.var_gift_int, style="Card.TCheckbutton", command=self.on_save_sched_cfg).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(crow1, text="冷场暖场", variable=self.var_auto_warmup, style="Card.TCheckbutton", command=self.on_save_sched_cfg).pack(side="left", padx=(0, 4))
        ttk.Label(crow1, text="间隔(秒):", style="Card.TLabel").pack(side="left")
        ttk.Entry(crow1, textvariable=self.var_warmup_gap, width=4).pack(side="left", padx=(2, 4))
        ttk.Button(crow1, text="💾 应用策略", style="Compact.TButton", command=self.on_save_sched_cfg).pack(side="left", padx=2)
        ttk.Button(crow1, text="⏸ 暂停/继续", style="Compact.TButton", command=lambda: self.on_kb_ctrl("toggle_pause")).pack(side="left", padx=2)
        ttk.Button(crow1, text="📖 连播/答疑", style="Compact.TButton", command=lambda: self.on_kb_ctrl("toggle_mode")).pack(side="left", padx=2)
        ttk.Button(crow1, text="🧹 清队列", style="Compact.TButton", command=self.on_clear_queue).pack(side="left", padx=2)
        ttk.Button(crow1, text="⏹ 停收弹幕", style="Compact.TButton", command=self.on_stop_collector).pack(side="right", padx=2)

        crow2 = ttk.Frame(cbox, style="Card.TFrame")
        crow2.pack(fill="x")
        ttk.Label(crow2, text="背景模式：", style="Card.TLabel").pack(side="left")
        self.var_bg_mode = tk.StringVar(value="kb_auto")
        cb_bgm = ttk.Combobox(crow2, textvariable=self.var_bg_mode, values=["kb_auto", "greenscreen", "original"], state="readonly", width=11)
        cb_bgm.pack(side="left", padx=3)
        cb_bgm.bind("<<ComboboxSelected>>", lambda _e: self.on_save_bg_cfg())
        ttk.Label(crow2, text="背景视频：", style="Card.TLabel").pack(side="left")
        self.var_bg_video = tk.StringVar()
        self.cb_bg_video = ttk.Combobox(crow2, textvariable=self.var_bg_video, width=16)
        self.cb_bg_video.pack(side="left", padx=3)
        ttk.Button(crow2, text="+传视频", style="Compact.TButton", command=self.on_upload_bg_video).pack(side="left", padx=2)
        ttk.Button(crow2, text="绑定讲稿", style="Compact.TButton", command=self.on_bind_bg_doc).pack(side="left", padx=2)
        self.var_bg_sim = tk.StringVar(value="0.34")
        self.var_bg_smooth = tk.StringVar(value="0.08")
        self.var_bg_spill = tk.StringVar(value="0.16")
        ttk.Label(crow2, text="相似:", style="Card.TLabel").pack(side="left", padx=(6, 1))
        ttk.Entry(crow2, textvariable=self.var_bg_sim, width=4).pack(side="left")
        ttk.Label(crow2, text="柔化:", style="Card.TLabel").pack(side="left", padx=(4, 1))
        ttk.Entry(crow2, textvariable=self.var_bg_smooth, width=4).pack(side="left")
        ttk.Label(crow2, text="溢色:", style="Card.TLabel").pack(side="left", padx=(4, 1))
        ttk.Entry(crow2, textvariable=self.var_bg_spill, width=4).pack(side="left")
        ttk.Button(crow2, text="🎨 应用抠像", style="Compact.TButton", command=self.on_save_bg_cfg).pack(side="right", padx=2)

        crow3 = ttk.Frame(cbox, style="Card.TFrame")
        crow3.pack(fill="x", pady=(4, 0))
        self.var_ov_kb = tk.BooleanVar(value=True)
        self.var_ov_sub = tk.BooleanVar(value=True)
        self.var_ov_pos = tk.StringVar(value=bk.overlay_id_to_label("position", "top-right"))
        self.var_ov_font = tk.StringVar(value=bk.overlay_id_to_label("font", "noto_sans_sc"))
        self.var_ov_eff = tk.StringVar(value=bk.overlay_id_to_label("effect", "classic_dark"))

        ttk.Checkbutton(crow3, text="知识卡(章节)", variable=self.var_ov_kb, command=self.on_save_overlay_quick).pack(side="left", padx=(0, 4))
        cb_pos = ttk.Combobox(crow3, textvariable=self.var_ov_pos, values=bk.overlay_labels("position"), state="readonly", width=11)
        cb_pos.pack(side="left", padx=(0, 8))
        cb_pos.bind("<<ComboboxSelected>>", lambda _e: self.on_save_overlay_quick())
        ttk.Checkbutton(crow3, text="口播字幕", variable=self.var_ov_sub, command=self.on_save_overlay_quick).pack(side="left", padx=(0, 8))
        ttk.Label(crow3, text="字体:", style="Card.TLabel").pack(side="left", padx=(2, 2))
        cb_ovf = ttk.Combobox(crow3, textvariable=self.var_ov_font, values=bk.overlay_labels("font"), state="readonly", width=19)
        cb_ovf.pack(side="left", padx=(0, 6))
        cb_ovf.bind("<<ComboboxSelected>>", lambda _e: self.on_save_overlay_quick())
        ttk.Label(crow3, text="效果:", style="Card.TLabel").pack(side="left", padx=(2, 2))
        cb_ove = ttk.Combobox(crow3, textvariable=self.var_ov_eff, values=bk.overlay_labels("effect"), state="readonly", width=14)
        cb_ove.pack(side="left", padx=(0, 6))
        cb_ove.bind("<<ComboboxSelected>>", lambda _e: self.on_save_overlay_eff_preset())
        ttk.Button(crow3, text="🎨 字幕/字体详调", style="Compact.TButton", command=self.on_open_overlay_dialog).pack(side="right", padx=2)
        self.var_ov_fs = tk.StringVar(value=str(bk.get_overlay_cfg(self.cfg).get("font_size", 24)))
        ttk.Button(crow3, text="A＋", style="Compact.TButton", width=3, command=lambda: self.on_bump_sub_font(4)).pack(side="right", padx=1)
        ttk.Label(crow3, textvariable=self.var_ov_fs, style="Card.TLabel", width=3, anchor="center").pack(side="right")
        ttk.Button(crow3, text="A−", style="Compact.TButton", width=3, command=lambda: self.on_bump_sub_font(-4)).pack(side="right", padx=1)
        ttk.Label(crow3, text="字号:", style="Card.TLabel").pack(side="right", padx=(6, 2))

        # 3. 下半区：实时直播预览 + 直播记录与快捷互动（先 bottom 固定互动按钮区，再 expand 日志框，彻底杜绝按钮被截断）
        live_split = ttk.Frame(self.tab_obs)
        live_split.pack(fill="both", expand=True)

        prev_card = self._make_card(live_split, "📺 实时直播预览（人物 / 背景 / 字幕）", padding=8, fill="both", expand=False, pady=0, side="left", padx=(0, 8))
        pr_top = ttk.Frame(prev_card, style="Card.TFrame")
        pr_top.pack(fill="x", pady=(0, 4))
        self.lbl_prev_state = ttk.Label(pr_top, text="等待开播...", style="CardHint.TLabel")
        self.lbl_prev_state.pack(side="left")
        self.var_prev_ratio = tk.StringVar(value="auto")
        cb_pr = ttk.Combobox(pr_top, textvariable=self.var_prev_ratio, values=["auto", "portrait", "landscape"], state="readonly", width=10)
        cb_pr.pack(side="right")
        pv_row = ttk.Frame(prev_card, style="Card.TFrame")
        pv_row.pack(fill="x", pady=(0, 4))
        self.var_preview_on = tk.BooleanVar(value=bool(self.lcfg.get("preview_enabled", True)))
        ttk.Checkbutton(pv_row, text="开启预览", variable=self.var_preview_on, style="Card.TCheckbutton",
                        command=self.on_toggle_preview).pack(side="left")
        ttk.Button(pv_row, text="🖥 看真实画面", style="Compact.TButton", command=self.on_open_real_preview).pack(side="left", padx=4)
        cb_pr.bind("<<ComboboxSelected>>", lambda _e: self._render_live_preview(self._last_hub_st))

        self.live_canvas = tk.Canvas(prev_card, width=264, height=190, bg=self._theme()["canvas_bg"], highlightthickness=0)
        self.live_canvas.pack(fill="both", expand=True)
        self._live_preview_img = None
        self._last_live_canvas_size = (0, 0)
        self.live_canvas.bind("<Configure>", self._on_live_canvas_configure)

        rec_card = self._make_card(live_split, "📝 直播记录 & 快捷互动控制", padding=8, fill="both", expand=True, pady=0, side="right")

        ctrl_wrap = ttk.Frame(rec_card, style="Card.TFrame")
        ctrl_wrap.pack(side="bottom", fill="x")

        ask_row = ttk.Frame(ctrl_wrap, style="Card.TFrame")
        ask_row.pack(fill="x", pady=(0, 4))
        ttk.Label(ask_row, text="观众提问：", style="Card.TLabel").pack(side="left")
        self.var_live_ask = tk.StringVar()
        ent_ask = ttk.Entry(ask_row, textvariable=self.var_live_ask)
        ent_ask.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ent_ask.bind("<Return>", lambda _e: self.on_live_ask())
        ttk.Button(ask_row, text="💬 发送观众提问", style="CompactPrimary.TButton", command=self.on_live_ask).pack(side="left", padx=2)
        ttk.Button(ask_row, text="🎁 模拟礼物", style="Compact.TButton", command=self.on_live_gift).pack(side="left", padx=2)

        say_row = ttk.Frame(ctrl_wrap, style="Card.TFrame")
        say_row.pack(fill="x", pady=(0, 4))
        ttk.Label(say_row, text="主播插播：", style="Card.TLabel").pack(side="left")
        self.var_live_say = tk.StringVar()
        ent_say = ttk.Entry(say_row, textvariable=self.var_live_say)
        ent_say.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ent_say.bind("<Return>", lambda _e: self.on_live_say())
        ttk.Button(say_row, text="🗣 让主播说", style="Compact.TButton", command=self.on_live_say).pack(side="left", padx=2)
        ttk.Button(say_row, text="⏹ 立刻停下", style="Compact.TButton", command=self.on_live_interrupt).pack(side="left", padx=2)

        act_row1 = ttk.Frame(ctrl_wrap, style="Card.TFrame")
        act_row1.pack(fill="x", pady=(0, 4))
        ttk.Label(act_row1, text="舞台动作：", style="Card.TLabel").pack(side="left")
        for aid, albl in (("sunglasses", "🕶️ 戴墨镜"), ("milktea", "🧋 拿奶茶"), ("heart", "🫶 比心"), ("wave", "👋 挥手"), ("thumbsup", "👍 点赞")):
            ttk.Button(act_row1, text=albl, style="Compact.TButton", command=lambda a=aid: self.on_live_action(a)).pack(side="left", padx=2)

        act_row2 = ttk.Frame(ctrl_wrap, style="Card.TFrame")
        act_row2.pack(fill="x")
        ttk.Label(act_row2, text="微表情手势：", style="Card.TLabel").pack(side="left")
        for tid, tlbl in ((1, "😊 微笑"), (2, "💗 表情比心"), (3, "🙋 表情挥手")):
            ttk.Button(act_row2, text=tlbl, style="Compact.TButton", command=lambda i=tid: self.on_live_engine_action(i)).pack(side="left", padx=2)

        self.txt_live_log = scrolledtext.ScrolledText(rec_card, height=3, font=("Microsoft YaHei UI", 9))
        self.txt_live_log.pack(side="top", fill="both", expand=True, pady=(0, 6))

    # ------------------------------------------------------------
    # Tab 5: 平台选择配置 + 自动检测平台应用 + 配置指导教程
    # ------------------------------------------------------------
    def _build_tab_platform(self) -> None:
        f = self._make_card(self.tab_plat, "直播平台选择、应用检测与配置导入", padding=10, pady=(0, 8))
        g = ttk.Frame(f, style="Card.TFrame")
        g.pack(fill="x")

        ttk.Label(g, text="开播平台：", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.var_plat = tk.StringVar()
        self.cb_plat = ttk.Combobox(g, textvariable=self.var_plat, state="readonly", width=20)
        self.cb_plat.grid(row=0, column=1, sticky="w", padx=6)
        self.cb_plat.bind("<<ComboboxSelected>>", lambda _e: self._on_platform_selected())

        ttk.Label(g, text="画幅模式：", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 0), pady=4)
        self.var_plat_ori = tk.StringVar(value="横屏")
        self.cb_plat_ori = ttk.Combobox(g, textvariable=self.var_plat_ori, values=["横屏", "竖屏"], state="readonly", width=10)
        self.cb_plat_ori.grid(row=0, column=3, sticky="w", padx=6)
        self.cb_plat_ori.bind("<<ComboboxSelected>>", lambda _e: self._on_plat_ori_changed())

        ttk.Label(g, text="直播间号 / 用户名：", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.var_room_id = tk.StringVar()
        ttk.Entry(g, textvariable=self.var_room_id, width=32).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(g, text="采集代理（海外平台）：", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.var_proxy = tk.StringVar()
        ttk.Entry(g, textvariable=self.var_proxy, width=32).grid(row=2, column=1, sticky="w", padx=6)

        btn_p = ttk.Frame(f, style="Card.TFrame")
        btn_p.pack(fill="x", pady=(6, 2))
        ttk.Button(btn_p, text="🔍 检测对应平台应用状态", command=self.on_detect_platform).pack(side="left", padx=(0, 6))
        ttk.Button(btn_p, text="📦 生成并导入平台配置文件", command=self.on_export_platform_cfg).pack(side="left", padx=4)
        ttk.Button(btn_p, text="✓ 校验 OBS 对应并保存平台配置", style="Primary.TButton", command=self.on_validate_and_save_platform).pack(side="right")

        self.lbl_plat_obs_check = ttk.Label(f, text="OBS 对应校验：--", style="CardHint.TLabel")
        self.lbl_plat_obs_check.pack(anchor="w", pady=(4, 0))
        self.lbl_plat_status = ttk.Label(f, text="平台应用状态：点击检测或切换平台自动检测", style="CardHint.TLabel")
        self.lbl_plat_status.pack(anchor="w", pady=(2, 0))

        gbox = self._make_card(self.tab_plat, "当前平台接入配置指导教程", padding=10, fill="both", expand=True, pady=0)
        ghdr = ttk.Frame(gbox, style="Card.TFrame")
        ghdr.pack(fill="x", pady=(0, 4))
        ttk.Label(ghdr, text="未检测到平台端或初次接入时，请按下方步骤完成配置：", style="CardHint.TLabel").pack(side="left")
        ttk.Button(ghdr, text="打开完整图文教程（离线可用）", command=self.on_open_access_guide).pack(side="right")
        self.txt_plat_guide = scrolledtext.ScrolledText(gbox, height=9, font=("Microsoft YaHei UI", 10))
        self.txt_plat_guide.pack(fill="both", expand=True)

    # ------------------------------------------------------------
    # Tab 6: Ultralight 主播训练与形象管理中心
    # ------------------------------------------------------------
    def _build_tab_train(self) -> None:
        # 1. 上半区：主播资产管理表格
        mbox = self._make_card(self.tab_train, "📋 Ultralight 数字人主播形象管理（支持查看 / 热切 / 重导 ONNX / 导入 / 删除）", padding=8, pady=(0, 8))
        # 先建容器 tv_wrap，再在其内部创建 Treeview 与滚动条（避免后建 Frame 遮挡同级 Treeview 导致表格空白）
        tv_wrap = ttk.Frame(mbox, style="Card.TFrame")
        tv_wrap.pack(fill="x", pady=(0, 6))
        cols = ("id", "name", "asr", "frames", "models", "status", "actions")
        self.tv_avatars = ttk.Treeview(tv_wrap, columns=cols, show="headings", height=5, selectmode="browse")
        headers = [
            ("id", "英文 ID", 130),
            ("name", "中文名", 110),
            ("asr", "ASR 特征", 100),
            ("frames", "帧数 / 分辨率", 130),
            ("models", "权重文件", 130),
            ("status", "状态", 90),
            ("actions", "真实动作短片", 100),
        ]
        for cid, htxt, wid in headers:
            self.tv_avatars.heading(cid, text=htxt)
            self.tv_avatars.column(cid, width=wid, anchor="center" if cid not in ("id", "name") else "w")
        tv_sb = ttk.Scrollbar(tv_wrap, orient="vertical", command=self.tv_avatars.yview)
        self.tv_avatars.configure(yscrollcommand=tv_sb.set)
        self.tv_avatars.pack(side="left", fill="x", expand=True)
        tv_sb.pack(side="right", fill="y")

        tbar = ttk.Frame(mbox, style="Card.TFrame")
        tbar.pack(fill="x")
        ttk.Button(tbar, text="👗 为选中主播换新衣服(零训练)", style="Primary.TButton", command=self.on_train_clone_outfit).pack(side="left", padx=(0, 6))
        ttk.Button(tbar, text="✓ 设为开播主播", style="Compact.TButton", command=self.on_train_set_active_host).pack(side="left", padx=2)
        ttk.Button(tbar, text="⚡ 直播中热切换", style="Compact.TButton", command=self.on_train_hot_switch_host).pack(side="left", padx=2)
        ttk.Button(tbar, text="🔄 PTH→重导 ONNX", style="Compact.TButton", command=self.on_train_export_onnx).pack(side="left", padx=2)
        ttk.Button(tbar, text="👀 首帧/目录", style="Compact.TButton", command=self.on_train_preview_host).pack(side="left", padx=2)
        ttk.Button(tbar, text="📦 导入目录", style="Compact.TButton", command=self.on_train_import_dir).pack(side="left", padx=2)
        ttk.Button(tbar, text="🗑 删除", style="Compact.TButton", command=self.on_train_delete_host).pack(side="left", padx=2)
        ttk.Button(tbar, text="🔄 刷新", style="Compact.TButton", command=self._refresh_avatar_table).pack(side="right", padx=2)

        # 2. 下半区：傻瓜式 AI 配参 + 一键构建流水线（默认隐藏底层训练超参）
        tbox = self._make_card(self.tab_train, "🎓 傻瓜式主播构建中心（只选参考视频 + 一句话提要求 → AI 自动配参并生成）", padding=8, fill="both", expand=True, pady=0)

        # 步骤 1：只提供参考视频（自动体检 + 自动命名）
        r_vid = ttk.Frame(tbox, style="Card.TFrame")
        r_vid.pack(fill="x", pady=(0, 4))
        ttk.Label(r_vid, text="① 参考视频：", style="Card.TLabel").pack(side="left")
        self.var_tr_video = tk.StringVar()
        ttk.Entry(r_vid, textvariable=self.var_tr_video).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r_vid, text="📁 选择参考视频", style="Primary.TButton", command=self.on_train_pick_video).pack(side="left", padx=(0, 8))
        ttk.Label(r_vid, text="主播叫啥：", style="Card.TLabel").pack(side="left")
        self.var_tr_name = tk.StringVar()
        ent_tr_cn = ttk.Entry(r_vid, textvariable=self.var_tr_name, width=12)
        ent_tr_cn.pack(side="left", padx=(2, 6))
        self.var_tr_aid = tk.StringVar()
        ent_tr_cn.bind("<KeyRelease>", lambda _e: self._sync_tr_aid_from_name())

        self.lbl_tr_probe = ttk.Label(
            tbox,
            text="请先选择一段正面真人说话参考视频（建议 1~5 分钟含人声；系统会自动检测音轨并填好默认主播名）",
            style="CardHint.TLabel",
        )
        self.lbl_tr_probe.pack(anchor="w", pady=(0, 6))

        # 步骤 2：快捷大白话场景 + 自然语言需求 + AI 分析配置
        r_chip = ttk.Frame(tbox, style="Card.TFrame")
        r_chip.pack(fill="x", pady=(0, 4))
        ttk.Label(r_chip, text="② 想要什么效果：", style="Card.TLabel").pack(side="left", padx=(0, 4))
        quick_presets = [
            ("🎯 正式开播推荐", "我要用于正式长期开播，嘴型最自然清晰且不要爆显存"),
            ("⚡ 极速测试看看", "我想先极速测试看看效果，最快几分钟出片验证"),
            ("🪶 极省显存轻量", "我需要同时开游戏或其他软件，尽量省显存轻量运行"),
            ("📦 已有权重直封", "我已有外部训好的 .pth 或 .onnx 权重，跳过训练直接封装打包"),
        ]
        for q_lbl, q_txt in quick_presets:
            ttk.Button(r_chip, text=q_lbl, style="Compact.TButton", command=lambda s=q_txt: self.on_train_quick_req(s)).pack(side="left", padx=2)

        r_ai = ttk.Frame(tbox, style="Card.TFrame")
        r_ai.pack(fill="x", pady=(0, 6))
        ttk.Label(r_ai, text="自由描述要求：", style="Card.TLabel").pack(side="left")
        self.var_tr_req = tk.StringVar(value="我要用于正式长期开播，嘴型自然清晰且显存安全")
        ent_req = ttk.Entry(r_ai, textvariable=self.var_tr_req)
        ent_req.pack(side="left", fill="x", expand=True, padx=6)
        ent_req.bind("<Return>", lambda _e: self.on_train_ai_recommend())
        self.btn_tr_ai = ttk.Button(r_ai, text="🤖 AI 分析配置", command=self.on_train_ai_recommend)
        self.btn_tr_ai.pack(side="right")

        # 步骤 3：AI 方案解读展示卡
        t = self._theme()
        self.tr_plan_box = tk.Frame(tbox, bg=t["tonal_3"], highlightbackground=t["card_border"], highlightthickness=1, padx=10, pady=6)
        self.tr_plan_box.pack(fill="x", pady=(0, 6))
        self.lbl_tr_ai_title = tk.Label(
            self.tr_plan_box,
            text="智能配置就绪：标准高画质开播推荐方案（默认 · HuBERT 25fps · 100轮 · Batch=16）",
            bg=t["tonal_3"], fg=t["primary"], font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        )
        self.lbl_tr_ai_title.pack(fill="x")
        self.lbl_tr_ai_plan = tk.Label(
            self.tr_plan_box,
            text="说明：选好参考视频并点「🤖 AI 分析配置」后，大模型会结合配置知识库与视频体检数据为你匹配最佳参数；不懂参数也完全可直接点击下方开始。",
            bg=t["tonal_3"], fg=t["fg"], font=("Microsoft YaHei UI", 9), anchor="w", wraplength=1040, justify="left",
        )
        self.lbl_tr_ai_plan.pack(fill="x", pady=(2, 0))
        self.lbl_tr_ai_warn = tk.Label(
            self.tr_plan_box,
            text="",
            bg=t["tonal_3"], fg=t["warn_fg"], font=("Microsoft YaHei UI", 9, "bold"), anchor="w", wraplength=1040, justify="left",
        )

        # 高级参数变量（AI 自动维护，默认折叠隐藏）
        self.var_tr_mode = tk.StringVar(value="从头本地训练 + 导出ONNX")
        self.var_tr_asr = tk.StringVar(value="hubert")
        self.var_tr_epochs = tk.IntVar(value=100)
        self.var_tr_batch = tk.IntVar(value=16)
        self.var_tr_lr = tk.StringVar(value="0.001")
        self.var_tr_idle = tk.StringVar()
        self.var_tr_ckpt = tk.StringVar()
        self.var_tr_set_default = tk.BooleanVar(value=True)
        self._last_vid_probe: Dict[str, Any] = {}
        self._tr_adv_visible = False

        # 可折叠高级参数区（默认不展示，只有想手工微调才点开）
        self.frm_tr_adv = ttk.Frame(tbox, style="Card.TFrame")
        ag1 = ttk.Frame(self.frm_tr_adv, style="Card.TFrame")
        ag1.pack(fill="x", pady=2)
        ttk.Label(ag1, text="构建模式：", style="Card.TLabel").pack(side="left")
        ttk.Combobox(ag1, textvariable=self.var_tr_mode, values=["从头本地训练 + 导出ONNX", "断点续训 (.pth)", "封装外部权重 (.pth/.onnx)"], state="readonly", width=24).pack(side="left", padx=(2, 8))
        ttk.Label(ag1, text="英文ID：", style="Card.TLabel").pack(side="left")
        ttk.Entry(ag1, textvariable=self.var_tr_aid, width=14).pack(side="left", padx=(2, 8))
        ttk.Label(ag1, text="ASR：", style="Card.TLabel").pack(side="left")
        ttk.Combobox(ag1, textvariable=self.var_tr_asr, values=["hubert", "wenet"], state="readonly", width=8).pack(side="left", padx=(2, 8))
        ttk.Label(ag1, text="Epochs：", style="Card.TLabel").pack(side="left")
        ttk.Entry(ag1, textvariable=self.var_tr_epochs, width=5).pack(side="left", padx=(2, 8))
        ttk.Label(ag1, text="Batch：", style="Card.TLabel").pack(side="left")
        ttk.Entry(ag1, textvariable=self.var_tr_batch, width=4).pack(side="left", padx=(2, 8))
        ttk.Label(ag1, text="LR：", style="Card.TLabel").pack(side="left")
        ttk.Entry(ag1, textvariable=self.var_tr_lr, width=6).pack(side="left")

        ag2 = ttk.Frame(self.frm_tr_adv, style="Card.TFrame")
        ag2.pack(fill="x", pady=2)
        ttk.Label(ag2, text="待机视频(可选)：", style="Card.TLabel").pack(side="left")
        ttk.Entry(ag2, textvariable=self.var_tr_idle, width=26).pack(side="left", padx=2)
        ttk.Button(ag2, text="选待机", style="Compact.TButton", command=lambda: self.var_tr_idle.set(filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mov")]) or self.var_tr_idle.get())).pack(side="left", padx=(2, 8))
        ttk.Label(ag2, text="外部权重(.pth/.onnx)：", style="Card.TLabel").pack(side="left")
        ttk.Entry(ag2, textvariable=self.var_tr_ckpt, width=28).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(ag2, text="选权重", style="Compact.TButton", command=self._pick_tr_ckpt).pack(side="left", padx=(2, 6))
        ttk.Checkbutton(ag2, text="完成后自动设为开播主播", variable=self.var_tr_set_default, style="Card.TCheckbutton").pack(side="left")

        # 底部一键启动操作行 + 进度条 + 日志
        r_act = ttk.Frame(tbox, style="Card.TFrame")
        r_act.pack(fill="x", pady=(2, 4))
        self.btn_tr_adv_toggle = ttk.Button(r_act, text="⚙ 高级参数 (AI已自动配好，点此查看)", style="Compact.TButton", command=self._toggle_tr_advanced)
        self.btn_tr_adv_toggle.pack(side="left")
        self.btn_tr_stop = ttk.Button(r_act, text="⏹ 终止任务", style="Compact.TButton", command=self.on_train_stop, state="disabled")
        self.btn_tr_stop.pack(side="right", padx=3)
        self.btn_tr_start = ttk.Button(r_act, text="🚀 一键开始生成数字人", style="Primary.TButton", command=self.on_train_start)
        self.btn_tr_start.pack(side="right", padx=4)

        pbar_row = ttk.Frame(tbox, style="Card.TFrame")
        pbar_row.pack(fill="x", pady=(2, 4))
        self.var_tr_pct = tk.DoubleVar(value=0.0)
        self.pb_train = ttk.Progressbar(pbar_row, variable=self.var_tr_pct, maximum=100.0)
        self.pb_train.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.lbl_tr_stage = ttk.Label(pbar_row, text="空闲", style="CardHint.TLabel", width=34)
        self.lbl_tr_stage.pack(side="right")

        self.txt_train_log = scrolledtext.ScrolledText(tbox, height=5, font=("Consolas", 9))
        self.txt_train_log.pack(fill="both", expand=True)

    # ============================================================
    # 数据加载与交互逻辑
    # ============================================================
    def log(self, msg: str) -> None:
        def _append() -> None:
            self.txt_log.insert("end", msg + "\n")
            self.txt_log.see("end")
        self.after(0, _append)

    def reload_all_data(self) -> None:
        bk.ensure_project_folders()
        self.cfg = bk.load_json(bk.CONFIG_PATH, {})
        self.profs = bk.load_json(bk.PROFILES_PATH, {})
        self.lcfg = bk.load_json(bk.LAUNCHER_CFG_PATH, {})
        cfg_theme = str(self.lcfg.get("ui_theme", "")).strip().lower()
        if cfg_theme in UI_THEMES and cfg_theme != self._theme_key:
            self.apply_theme(cfg_theme, persist=False)
        self.presets = bk.load_presets()
        self.hosts = bk.list_hosts()
        self.kb_docs = bk.list_kb_docs()

        gcfgs = bk.list_global_configs()
        self.cb_gcfg["values"] = gcfgs
        last_gc = str(self.lcfg.get("last_global_config") or "").strip()
        if last_gc and last_gc in gcfgs:
            self.var_gcfg.set(last_gc)
        elif gcfgs and self.var_gcfg.get() not in gcfgs:
            self.var_gcfg.set(gcfgs[0])

        preset_names = [p["name"] for p in self.presets]
        self.cb_preset["values"] = preset_names
        saved_preset = str(self.lcfg.get("preset") or "").strip()
        if saved_preset and saved_preset in preset_names:
            self.var_preset.set(saved_preset)
        elif preset_names and self.var_preset.get() not in preset_names:
            self.var_preset.set(preset_names[0])

        # AI 模型
        providers = self.cfg.get("llm_providers", [])
        self.cb_llm["values"] = [f"{i}: {p.get('name', 'node')}" for i, p in enumerate(providers)]
        if providers:
            self.var_llm_idx.set(self.cb_llm["values"][0])
            self._load_llm_fields()

        tts = self.cfg.get("tts", {})
        self.var_tts_mode.set(bk.tts_mode_label_for_key(tts.get("mode", "edgetts")))
        raw_voice = self.cfg.get("livetalking", {}).get("tts_voice", "zh-CN-XiaoxiaoNeural")
        self.var_edge_voice.set(bk.voice_label_for_id(raw_voice))
        self.var_sf_voice.set(tts.get("siliconflow", {}).get("voice", "FunAudioLLM/CosyVoice2-0.5B:benjamin"))
        self.var_sf_emo.set(tts.get("siliconflow", {}).get("emotion_prefix", ""))
        self.var_gsv_ref.set(tts.get("gptsovits", {}).get("ref_audio_path", ""))
        self.var_gsv_prompt.set(tts.get("gptsovits", {}).get("prompt_text", ""))

        # 主播与主题（仅加载主播属性，绝不绑定知识库）
        self.cb_avatar["values"] = [f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts]
        cur_aid = self.cfg.get("livetalking", {}).get("avatar_id", "demo_ul_host")
        matched_h = next((f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts if h["id"] == cur_aid), cur_aid)
        self.var_avatar.set(matched_h)
        ul_cfg = self.cfg.get("ultralight", {})
        if hasattr(self, "var_ul_asr"):
            self.var_ul_asr.set(str(ul_cfg.get("asr", "hubert") or "hubert"))
        if hasattr(self, "var_ul_backend"):
            self.var_ul_backend.set(str(ul_cfg.get("backend", "onnx") or "onnx"))
        persona = self.cfg.get("persona", {})
        self.var_host_name.set(persona.get("name", "主播"))
        self.var_live_style.set(persona.get("style", ""))
        self.txt_prompt.delete("1.0", "end")
        self.txt_prompt.insert("1.0", persona.get("system_prompt", ""))
        self._refresh_active_voice_selector()

        # 知识库（主播与知识库彻底解耦：默认展示全部讲稿，不再跟随当前主播头像强制切换）
        kb_hosts = bk.list_kb_hosts()
        self.cb_kb_host["values"] = kb_hosts
        saved_kh = str(self.lcfg.get("kb_host") or "").strip()
        if saved_kh in kb_hosts:
            self.var_kb_host.set(saved_kh)
        elif bk.KB_ALL_HOSTS_LABEL in kb_hosts:
            self.var_kb_host.set(bk.KB_ALL_HOSTS_LABEL)
        elif kb_hosts:
            self.var_kb_host.set(kb_hosts[0])
        self.var_kb_narration.set(bool(self.cfg.get("knowledge_base", {}).get("narration_mode", True)))
        target_kb_doc = str(self.lcfg.get("kb_doc") or self.cfg.get("knowledge_base", {}).get("active_doc", "")).strip()
        self._on_kb_host_selected(target_doc=target_kb_doc)

        # OBS 与开关（精确还原横竖屏模式）
        self.var_obs_path.set(self.lcfg.get("obs_path") or bk.find_obs_executable())
        if "auto_obs" in self.lcfg:
            self.var_auto_obs.set(bool(self.lcfg["auto_obs"]))
        act_plat = self.profs.get("active_profile", "douyin")
        p_act = self.profs.get("profiles", {}).get(act_plat, {})
        saved_prof = str(p_act.get("obs_profile") or "").strip()
        saved_ori = str(p_act.get("orientation") or "").strip()
        obs_match = next(
            (p["label"] for p in bk.OBS_PRESETS if p["key"] == act_plat and saved_prof and p["profile"] == saved_prof),
            None,
        ) or next(
            (p["label"] for p in bk.OBS_PRESETS if p["key"] == act_plat and saved_ori and p["orientation"] == saved_ori),
            None,
        ) or next(
            (p["label"] for p in bk.OBS_PRESETS if p["key"] == act_plat),
            bk.OBS_PRESETS[0]["label"],
        )
        self.var_obs_mode.set(obs_match)
        self._update_obs_profiles_hint()

        # 平台（保留已选知识库讲稿，不再反向覆盖）
        plat_keys = list(self.profs.get("profiles", {}).keys())
        self.cb_plat["values"] = plat_keys
        self.var_plat.set(act_plat)
        self._on_platform_selected(refresh_summary=False)
        # 同步实时调度开关与绿幕背景参数及主播资产表
        sched = self.cfg.get("scheduler", {})
        if hasattr(self, "var_auto_reply"):
            self.var_auto_reply.set(bool(sched.get("auto_reply", True)))
            self.var_gift_int.set(bool(sched.get("gift_interrupt_narration", True)))
            self.var_auto_warmup.set(bool(sched.get("auto_warmup", True)))
            self.var_warmup_gap.set(int(sched.get("warmup_interval_sec", 25)))
        bg = self.cfg.get("background", {})
        if hasattr(self, "var_bg_mode"):
            self.var_bg_mode.set(str(bg.get("mode", "kb_auto")))
            self.var_bg_sim.set(str(bg.get("similarity", 0.34)))
            self.var_bg_smooth.set(str(bg.get("smoothness", 0.08)))
            self.var_bg_spill.set(str(bg.get("spill", 0.16)))
            bg_files = bk.list_bg_videos()
            self.cb_bg_video["values"] = bg_files
            dv = str(bg.get("default_video", ""))
            self.var_bg_video.set(dv if dv in bg_files else (bg_files[0] if bg_files else ""))
        ov = bk.get_overlay_cfg(self.cfg)
        self._sync_overlay_vars(ov)
        if hasattr(self, "tv_avatars"):
            self._refresh_avatar_table()
        self._step_done: Dict[str, bool] = {"tts": False, "host": False, "kb": False, "preset": False, "plat": False, "global": False}
        self._voice_locked_by_step1: bool = False
        self._sync_preset_display()
        self._update_plat_obs_label()
        self._update_summary()

    def _active_tts_mode(self) -> str:
        return bk.tts_mode_key_from_label(self.var_tts_mode.get())

    def _refresh_active_voice_selector(self) -> None:
        mode = self._active_tts_mode()
        if mode == "siliconflow":
            opts = bk.sf_voice_options_all(self.cfg, self._sf_custom_voices)
            self.lbl_active_voice.config(text="硅基流动 CosyVoice2 声线：")
            self.lbl_tts_hint.config(text="0 显存云端高拟真 / 克隆音色")
            self.cb_active_voice.config(values=[lbl for _, lbl in opts])
            cur_uri = self.var_sf_voice.get().strip()
            if not cur_uri:
                cur_uri = bk.suggest_sf_voice_for_host(self._selected_avatar_id(), bk.voice_id_from_label(self.var_edge_voice.get()))
                self.var_sf_voice.set(cur_uri)
            self.var_active_voice.set(bk.sf_voice_label_for_id(cur_uri, opts))
            self.lbl_voice_extra.config(text="硅基音色 URI / 预设 ID：")
            self.var_voice_extra.set(cur_uri)
            self.btn_voice_action.config(text="🔄 刷新云端克隆音色")
        elif mode == "gptsovits":
            opts = bk.list_gptsovits_ref_options()
            self.lbl_active_voice.config(text="GPT-SoVITS 本地参考音频：")
            self.lbl_tts_hint.config(text="本地 9880 推理，显存占用较高")
            self.cb_active_voice.config(values=[lbl for _, lbl in opts])
            cur_ref = self.var_gsv_ref.get().strip() or opts[0][0]
            self.var_gsv_ref.set(cur_ref)
            self.var_active_voice.set(bk.gsv_voice_label_for_path(cur_ref, opts))
            self.lbl_voice_extra.config(text="参考音频原话文本：")
            self.var_voice_extra.set(self.var_gsv_prompt.get())
            self.btn_voice_action.config(text="📂 浏览本地音频")
        else:
            self.lbl_active_voice.config(text="微软 Edge-TTS 免费声线：")
            self.lbl_tts_hint.config(text="0 显存微软云端免费声线")
            self.cb_active_voice.config(values=[lbl for _, lbl in bk.EDGE_VOICE_OPTIONS])
            ev = bk.voice_id_from_label(self.var_edge_voice.get()) or "zh-CN-XiaoxiaoNeural"
            lbl = bk.voice_label_for_id(ev)
            self.var_edge_voice.set(lbl)
            self.var_active_voice.set(lbl)
            self.lbl_voice_extra.config(text="当前微软声线 ID：")
            self.var_voice_extra.set(ev)
            self.btn_voice_action.config(text="↻ 匹配当前主播声线")
        if hasattr(self, "lbl_host_voice"):
            self.lbl_host_voice.config(text=f"当前音色({mode})：{self.var_active_voice.get()}")

    def _on_tts_mode_changed(self) -> None:
        self._refresh_active_voice_selector()
        if self._active_tts_mode() == "siliconflow" and not self._sf_custom_voices:
            def _fetch() -> None:
                custom = bk.list_siliconflow_custom_voices(self.cfg)
                if custom:
                    self._sf_custom_voices = custom
                    self.after(0, self._refresh_active_voice_selector)
            threading.Thread(target=_fetch, daemon=True).start()
        self._update_summary()

    def _on_active_voice_selected(self) -> None:
        mode = self._active_tts_mode()
        sel = self.var_active_voice.get().strip()
        if mode == "siliconflow":
            opts = bk.sf_voice_options_all(self.cfg, self._sf_custom_voices)
            uri = bk.sf_voice_id_from_label(sel, opts)
            self.var_sf_voice.set(uri)
            self.var_voice_extra.set(uri)
        elif mode == "gptsovits":
            opts = bk.list_gptsovits_ref_options()
            path = bk.gsv_voice_path_from_label(sel, opts)
            self.var_gsv_ref.set(path)
        else:
            vid = bk.voice_id_from_label(sel)
            lbl = bk.voice_label_for_id(vid)
            self.var_edge_voice.set(lbl)
            self.var_voice_extra.set(vid)
        self._update_summary()

    def _on_voice_extra_edited(self) -> None:
        mode = self._active_tts_mode()
        txt = self.var_voice_extra.get().strip()
        if mode == "siliconflow":
            self.var_sf_voice.set(txt)
        elif mode == "gptsovits":
            self.var_gsv_prompt.set(txt)
        else:
            if txt:
                self.var_edge_voice.set(bk.voice_label_for_id(txt))

    def on_voice_action_click(self) -> None:
        mode = self._active_tts_mode()
        if mode == "siliconflow":
            def _run() -> None:
                custom = bk.list_siliconflow_custom_voices(self.cfg)
                self._sf_custom_voices = custom
                self.after(0, lambda: (
                    self._refresh_active_voice_selector(),
                    self.log(f"已刷新硅基流动云端克隆音色：发现 {len(custom)} 个自定义音色"),
                ))
            threading.Thread(target=_run, daemon=True).start()
        elif mode == "gptsovits":
            f = filedialog.askopenfilename(title="选择 5~10 秒人声参考音频", filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a")])
            if f:
                self.var_gsv_ref.set(f.replace("\\", "/"))
                self._refresh_active_voice_selector()
                self._update_summary()
        else:
            aid = self._selected_avatar_id()
            h = next((x for x in self.hosts if x["id"] == aid), None)
            if h and h.get("tts_voice"):
                self.var_edge_voice.set(bk.voice_label_for_id(h["tts_voice"]))
                self._refresh_active_voice_selector()
                self._update_summary()

    def _sync_preset_display(self) -> None:
        """确保预设栏绝不残留错位的固定知识库名，实时跟随当前所选平台/主播/知识库显示。"""
        plat = self.var_plat.get() or "douyin"
        host = self.var_host_name.get().strip() or "主播"
        item = self._selected_kb_item()
        kb_doc = item.get("hub_doc") or self.var_kb_doc.get().strip()
        kb_t = bk.kb_short_title(kb_doc, item.get("full_path", "")) if kb_doc else "自由口播"
        cur = self.var_preset.get().strip()
        saved = next((x for x in self.presets if x.get("name") == cur), None)
        if saved and saved.get("kb_doc") == kb_doc and saved.get("avatar_id") == self._selected_avatar_id() and (
            saved.get("preset_scope") == "content3" or saved.get("platform") == plat
        ):
            return
        self.var_preset.set(f"当前：{bk.plat_cn_label(plat)} · {host} · {kb_t}")

    def _selected_avatar_id(self) -> str:
        raw = self.var_avatar.get().strip()
        return raw.split(" ")[0] if raw else ""

    def _selected_obs_preset(self) -> Dict[str, Any]:
        lbl = self.var_obs_mode.get()
        return next((p for p in bk.OBS_PRESETS if p["label"] == lbl), bk.OBS_PRESETS[0])

    def _update_plat_obs_label(self) -> Tuple[bool, str]:
        plat = self.var_plat.get() or "douyin"
        ori = "portrait" if self.var_plat_ori.get() == "竖屏" else "landscape"
        obs_p = self._selected_obs_preset()
        ok = (obs_p["key"] == plat and obs_p["orientation"] == ori)
        if hasattr(self, "lbl_plat_obs_check"):
            if ok:
                txt = f"OBS 对应校验：✅ 平台 [{bk.plat_cn_label(plat)}/{self.var_plat_ori.get()}] ⇄ OBS [{obs_p['profile']} · 场景「{obs_p['scene']}」 · {obs_p['width']}×{obs_p['height']}]"
            else:
                txt = f"OBS 对应校验：⚠ 当前 OBS [{obs_p['label']}] 与平台 [{plat}/{self.var_plat_ori.get()}] 不一致（点击右侧校验保存将自动对齐）"
            self.lbl_plat_obs_check.config(text=txt)
        return ok, f"{obs_p['profile']} / {obs_p['scene']}"

    def _update_summary(self) -> None:
        obs_p = self._selected_obs_preset()
        mode = self._active_tts_mode()
        active_voice = self.var_active_voice.get().strip() or self.var_edge_voice.get().strip()
        done = getattr(self, "_step_done", {})
        mark = lambda k: "✓" if done.get(k) else "·"
        chain = f"[1.声音{mark('tts')}] → [2.主播{mark('host')}] → [3.知识库{mark('kb')}] → [4.前三预设{mark('preset')}] → [5.平台×OBS{mark('plat')}] → [6.全局存档{mark('global')}]"
        txt = (
            f"• 开播平台：{self.var_plat.get()}   |   房间号：{self.var_room_id.get() or '(未填)'}\n"
            f"• 开播主播：{self.var_host_name.get()} ({self._selected_avatar_id()})   |   声音来源：{mode} / {active_voice}\n"
            f"• 直播主题：{self.var_live_style.get()}\n"
            f"• 知识主题：{self.var_kb_doc.get()}（{'章节连播' if self.var_kb_narration.get() else '仅答疑'}）\n"
            f"• OBS 模式：{obs_p['profile']} / 场景「{obs_p['scene']}」\n"
            f"• 关联确认：{chain}"
        )
        self.lbl_summary.config(text=txt)

    def _load_llm_fields(self) -> None:
        sel = self.var_llm_idx.get()
        if not sel:
            return
        idx = int(sel.split(":")[0])
        p = self.cfg.get("llm_providers", [])[idx]
        self.var_llm_en.set(bool(p.get("enabled", True)))
        self.var_llm_url.set(p.get("base_url", ""))
        self.var_llm_model.set(p.get("model", ""))
        self.var_llm_key.set(p.get("api_key", ""))

    def on_save_llm(self) -> None:
        sel = self.var_llm_idx.get()
        if not sel:
            return
        idx = int(sel.split(":")[0])
        p = self.cfg.get("llm_providers", [])[idx]
        p["enabled"] = self.var_llm_en.get()
        p["base_url"] = self.var_llm_url.get().strip()
        p["model"] = self.var_llm_model.get().strip()
        p["api_key"] = self.var_llm_key.get().strip()
        bk.save_json(bk.CONFIG_PATH, self.cfg)
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/reload", "POST", timeout=4.0), daemon=True).start()
        self.log(f"已保存大模型节点 [{p.get('name')}]")
        messagebox.showinfo("完成", "大模型节点配置已保存")

    def on_test_llm(self) -> None:
        sel = self.var_llm_idx.get()
        if not sel:
            return
        idx = int(sel.split(":")[0])
        p = dict(self.cfg.get("llm_providers", [])[idx])
        p["base_url"] = self.var_llm_url.get().strip()
        p["model"] = self.var_llm_model.get().strip()
        p["api_key"] = self.var_llm_key.get().strip()

        def _run() -> None:
            ok, msg = bk.test_llm_provider(p)
            self.log(f"[LLM 测试] {p.get('name')}: {msg}")
            self.after(0, lambda: messagebox.showinfo("连通性测试", msg) if ok else messagebox.showwarning("连通性测试", msg))

        threading.Thread(target=_run, daemon=True).start()

    def on_save_tts(self) -> None:
        self._voice_locked_by_step1 = True
        self._step_done["tts"] = True
        self.save_all_configs()
        self.log(f"[步骤1已确认] 声音来源={self._active_tts_mode()} / {self.var_active_voice.get()}")
        messagebox.showinfo("第 1 步已确认", f"声音配置已确定并保存（后续切换主播将保留您当前选定的声线）：\n{self._active_tts_mode()} / {self.var_active_voice.get()}")

    def _on_host_selected(self) -> None:
        aid = self._selected_avatar_id()
        h = next((x for x in self.hosts if x["id"] == aid), None)
        if h:
            if h.get("persona_name"):
                self.var_host_name.set(h["persona_name"])
            # 若第1步已点保存确认声线，切换主播时保留第1步选定声线不覆写
            if not getattr(self, "_voice_locked_by_step1", False):
                if h.get("tts_voice"):
                    self.var_edge_voice.set(bk.voice_label_for_id(h["tts_voice"]))
                sf_v = h.get("sf_voice") or bk.suggest_sf_voice_for_host(aid, h.get("tts_voice", ""))
                if sf_v:
                    self.var_sf_voice.set(sf_v)
            if h.get("style"):
                self.var_live_style.set(h["style"])
            if h.get("system_prompt"):
                self.txt_prompt.delete("1.0", "end")
                self.txt_prompt.insert("1.0", h["system_prompt"])
            if h.get("asr") in ("hubert", "wenet") and hasattr(self, "var_ul_asr"):
                self.var_ul_asr.set(h["asr"])
        self._refresh_active_voice_selector()
        self._sync_preset_display()
        self._update_plat_obs_label()
        self._update_summary()

    def _auto_fill_english_id(self) -> None:
        if getattr(self, "_aid_manual", False):
            return
        cn = self.var_new_aname.get().strip()
        self.var_new_aid.set(bk.suggest_english_id(cn))

    def on_ai_gen_prompt(self) -> None:
        host_name = self.var_host_name.get().strip() or "主播"
        style = self.var_live_style.get().strip() or "成人实用科普"
        self.log(f"正在调用 AI 生成 [{host_name}] 的人设系统提示词...")

        def _run() -> None:
            ok, res = bk.generate_persona_prompt(host_name, style, self.cfg)
            def _apply() -> None:
                if ok:
                    self.txt_prompt.delete("1.0", "end")
                    self.txt_prompt.insert("1.0", res)
                    self.log("✨ AI 人设系统提示词已生成！")
                else:
                    messagebox.showwarning("AI 生成失败", res)
            self.after(0, _apply)

        threading.Thread(target=_run, daemon=True).start()

    def on_register_new_host(self) -> None:
        cn = self.var_new_aname.get().strip()
        aid = self.var_new_aid.get().strip() or bk.suggest_english_id(cn)
        self.var_new_aid.set(aid)
        ok, msg = bk.register_host_profile(
            avatar_id=aid,
            display_name=cn or aid,
            persona_name=cn or aid,
            tts_voice=bk.voice_id_from_label(self.var_edge_voice.get()),
            style=self.var_live_style.get().strip(),
            system_prompt=self.txt_prompt.get("1.0", "end").strip(),
        )
        if ok:
            self.hosts = bk.list_hosts()
            self.cb_avatar["values"] = [f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts]
            self.var_avatar.set(f"{aid} ({cn or aid})")
            self._update_summary()
            self.log(msg)
            messagebox.showinfo("完成", msg)
        else:
            messagebox.showwarning("失败", msg)

    def on_save_host(self) -> None:
        self._step_done["host"] = True
        self.save_all_configs()
        self.hosts = bk.list_hosts()
        self.log(f"[步骤2已确认] 主播={self.var_host_name.get()} ({self._selected_avatar_id()})")
        messagebox.showinfo("第 2 步已确认", "主播形象、人设提示词与直播主题已确定并保存")

    def on_save_kb(self) -> None:
        item = self._selected_kb_item()
        if item.get("full_path"):
            bk.sync_kb_doc_to_hub(Path(item["full_path"]))
        self._step_done["kb"] = True
        self.save_all_configs()
        self.log(f"[步骤3已确认] 知识库={self.var_kb_doc.get()}（{'章节连播' if self.var_kb_narration.get() else '仅答疑'}）")
        messagebox.showinfo("第 3 步已确认", f"知识库主题设置已确定并保存：\n{self.var_kb_doc.get()}")

    def on_create_avatar(self) -> None:
        aid = self.var_new_aid.get().strip() or self._selected_avatar_id()
        aname = self.var_new_aname.get().strip() or aid
        if not aid:
            messagebox.showwarning("提示", "请先填写新主播英文 ID 或选择一个主播")
            return
        win = tk.Toplevel(self)
        self._theme_toplevel(win, f"Ultralight 训练 / 封装形象：{aid}", "600x360")
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)

        v_asr = tk.StringVar(value=self.var_ul_asr.get() if hasattr(self, "var_ul_asr") else "hubert")
        v_vid = tk.StringVar()
        v_idle = tk.StringVar()
        v_ckpt = tk.StringVar()
        v_epochs = tk.IntVar(value=100)
        v_batch = tk.IntVar(value=16)

        ttk.Label(frm, text="注意：本地训练需使用含正常说话音轨的真人正面视频（3~5分钟）；若已有外部训练好的 .pth/.onnx，可直接封装。", wraplength=560).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="说话素材视频 (.mp4)：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_vid, width=46).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="选择视频", command=lambda: v_vid.set(filedialog.askopenfilename(title="选择说话素材 MP4", filetypes=[("Video", "*.mp4 *.mov")]) or v_vid.get())).grid(row=1, column=2)

        ttk.Label(frm, text="静音待机视频 (可选)：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_idle, width=46).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="选择待机", command=lambda: v_idle.set(filedialog.askopenfilename(title="选择静音待机 MP4（可选）", filetypes=[("Video", "*.mp4 *.mov")]) or v_idle.get())).grid(row=2, column=2)

        ttk.Label(frm, text="外部权重 .pth/.onnx (可选)：").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_ckpt, width=46).grid(row=3, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="选择权重", command=lambda: v_ckpt.set(filedialog.askopenfilename(title="选择外部 .pth/.onnx 权重（填此项可跳过训练）", filetypes=[("Model", "*.pth *.onnx")]) or v_ckpt.get())).grid(row=3, column=2)

        opt = ttk.Frame(frm)
        opt.grid(row=4, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(opt, text="ASR 特征：").pack(side="left")
        ttk.Combobox(opt, textvariable=v_asr, values=["hubert", "wenet"], state="readonly", width=10).pack(side="left", padx=(4, 14))
        ttk.Label(opt, text="训练 Epochs：").pack(side="left")
        ttk.Entry(opt, textvariable=v_epochs, width=6).pack(side="left", padx=(4, 14))
        ttk.Label(opt, text="Batch Size：").pack(side="left")
        ttk.Entry(opt, textvariable=v_batch, width=6).pack(side="left", padx=4)

        lbl_st = ttk.Label(frm, text="准备就绪。点击开始后将在后台执行预处理、训练与 unet.onnx 导出，日志实时输出到主控日志区。", wraplength=560)
        lbl_st.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 10))
        frm.columnconfigure(1, weight=1)

        def _start() -> None:
            vp = v_vid.get().strip()
            if not vp:
                messagebox.showwarning("提示", "请先选择说话素材视频", parent=win)
                return
            lbl_st.config(text="正在后台执行 Ultralight 流水线，请勿关闭程序，进度请查看主日志...")
            win.update_idletasks()

            def _worker() -> None:
                ok, msg = bk.run_ultralight_train_cli(
                    avatar_id=aid,
                    name=aname,
                    asr=v_asr.get(),
                    video_path=vp,
                    idle_video=v_idle.get().strip(),
                    checkpoint=v_ckpt.get().strip(),
                    epochs=int(v_epochs.get() or 100),
                    batch_size=int(v_batch.get() or 16),
                    log_cb=self.log,
                )

                def _done() -> None:
                    if win.winfo_exists():
                        win.destroy()
                    self.reload_all_data()
                    if ok:
                        messagebox.showinfo("Ultralight 形象生成完成", msg)
                    else:
                        messagebox.showwarning("Ultralight 形象生成失败", msg)

                self.after(0, _done)

            threading.Thread(target=_worker, daemon=True).start()

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(btns, text="开始本地训练 / 封装导出 ONNX", command=_start).pack(side="right", padx=4)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="right", padx=4)

    def on_upload_action_clip(self, act_id: str, act_lbl: str) -> None:
        aid = self._selected_avatar_id()
        if not aid:
            messagebox.showwarning("提示", "请先选择一个主播")
            return
        f = filedialog.askopenfilename(title=f"选择 [{aid}] 的「{act_lbl}」真实动作 MP4", filetypes=[("MP4/WebM", "*.mp4 *.webm")])
        if not f:
            return
        dst_dir = bk.ACTION_DIR / aid
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"act_{act_id}.mp4"
        shutil.copy2(f, dst)
        bk.mark_custom_action_clip(aid, dst.name)
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/reload", "POST", timeout=3.0), daemon=True).start()
        self.log(f"已为 [{aid}] 绑定真实动作短片：{dst.name}")
        messagebox.showinfo("完成", f"已绑定 [{aid}] 的「{act_lbl}」真实动作短片：\n{dst}")

    def _selected_kb_item(self) -> Dict[str, Any]:
        disp = self.var_kb_doc.get().strip()
        return next(
            (
                x for x in self._host_kb_items
                if x["display"] == disp
                or x["hub_doc"] == disp
                or Path(x["hub_doc"]).name == Path(disp.replace("\\", "/")).name
                or Path(x["display"]).name == Path(disp.replace("\\", "/")).name
            ),
            {},
        )

    def _on_kb_host_selected(self, target_doc: str = "") -> None:
        kh = self.var_kb_host.get().strip()
        self._host_kb_items = bk.list_host_kb_docs(kh)
        if target_doc:
            t_name = Path(target_doc.replace("\\", "/")).name
            m = next(
                (
                    x["display"] for x in self._host_kb_items
                    if x["hub_doc"] == target_doc
                    or x["display"] == target_doc
                    or Path(x["hub_doc"]).name == t_name
                    or Path(x["display"]).name == t_name
                ),
                None,
            )
            if not m and kh != bk.KB_ALL_HOSTS_LABEL:
                self.var_kb_host.set(bk.KB_ALL_HOSTS_LABEL)
                self._host_kb_items = bk.list_host_kb_docs(bk.KB_ALL_HOSTS_LABEL)
                m = next(
                    (
                        x["display"] for x in self._host_kb_items
                        if x["hub_doc"] == target_doc
                        or x["display"] == target_doc
                        or Path(x["hub_doc"]).name == t_name
                        or Path(x["display"]).name == t_name
                    ),
                    None,
                )
            vals = [x["display"] for x in self._host_kb_items]
            self.cb_kb_doc["values"] = vals
            self.var_kb_doc.set(m or (vals[0] if vals else ""))
        else:
            vals = [x["display"] for x in self._host_kb_items]
            self.cb_kb_doc["values"] = vals
            if vals:
                self.var_kb_doc.set(vals[0])
            else:
                self.var_kb_doc.set("")
        self._on_kb_doc_selected()

    def _on_kb_doc_selected(self) -> None:
        item = self._selected_kb_item()
        self._render_kb_preview(Path(item["full_path"]) if item.get("full_path") else None)
        self._sync_preset_display()
        self._update_plat_obs_label()
        self._update_summary()

    def _extract_video_thumb(self, video_path: Path, size=(160, 96)):
        try:
            cap = cv2.VideoCapture(str(video_path))
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            im = Image.fromarray(rgb)
            im.thumbnail(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def _render_kb_preview(self, md_path: Path | None) -> None:
        self._last_kb_md_path = md_path
        t = self._theme()
        for w in self.kb_preview_inner.winfo_children():
            w.destroy()
        self._kb_thumbs.clear()
        if not md_path or not md_path.is_file():
            ttk.Label(self.kb_preview_inner, text="当前主播知识库暂无讲稿，可点击上方「新建讲稿」或「导入 .md/.txt」添加。", style="CardHint.TLabel").pack(padx=12, pady=12, anchor="w")
            return
        sections = bk.parse_kb_preview_sections(md_path)
        if not sections:
            ttk.Label(self.kb_preview_inner, text=md_path.read_text(encoding="utf-8", errors="ignore")[:600], style="Card.TLabel", wraplength=820, justify="left").pack(padx=12, pady=12, anchor="w")
            return

        # 每行并排展示最多 2 个「单节文字 + 单节视频缩略图」单元，按白昼冷晶 / 暖炭玄岩着色
        grid_wrap = ttk.Frame(self.kb_preview_inner, style="Card.TFrame")
        grid_wrap.pack(fill="both", expand=True, padx=6, pady=6)
        for idx, sec in enumerate(sections):
            r, c = divmod(idx, 2)
            cell = tk.Frame(grid_wrap, bg=t["kb_card_bg"], highlightbackground=t["card_border"], highlightthickness=1, padx=8, pady=8)
            cell.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)

            left = tk.Frame(cell, bg=t["kb_card_bg"])
            left.pack(side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(left, text=f"{idx + 1}. {sec['title']}", bg=t["kb_card_bg"], fg=t["fg"], font=("Microsoft YaHei UI", 10, "bold"), anchor="w", wraplength=240, justify="left").pack(anchor="w")
            tk.Label(left, text=sec["text"] or "(无正文)", bg=t["kb_card_bg"], fg=t["fg_sec"], font=("Microsoft YaHei UI", 9), anchor="nw", wraplength=240, justify="left").pack(anchor="w", pady=(4, 0))

            right = tk.Frame(cell, bg=t["kb_card_well"], highlightbackground=t["border"], highlightthickness=1, width=164, height=112)
            right.pack(side="right")
            right.pack_propagate(False)
            vp = Path(sec["video_path"]) if sec.get("video_path") else None
            if vp and vp.is_file():
                thumb = self._extract_video_thumb(vp)
                if thumb:
                    self._kb_thumbs.append(thumb)
                    btn = tk.Button(right, image=thumb, text=f"▶ {sec['video_name']}", compound="top", bg=t["kb_btn_bg"], fg=t["kb_btn_fg"], font=("Microsoft YaHei UI", 8), relief="flat", cursor="hand2", command=lambda p=vp: self.open_video_popup(p))
                else:
                    btn = tk.Button(right, text=f"▶ 点击播放\n{sec['video_name']}", bg=t["kb_btn_bg"], fg=t["kb_btn_fg"], font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2", command=lambda p=vp: self.open_video_popup(p))
                btn.pack(fill="both", expand=True)
            else:
                tk.Label(right, text="暂无同目录视频\n可点击上方添加", bg=t["kb_card_well"], fg=t["fg_muted"], font=("Microsoft YaHei UI", 8)).pack(expand=True)
        grid_wrap.columnconfigure(0, weight=1)
        grid_wrap.columnconfigure(1, weight=1)

    def open_video_popup(self, video_path: Path) -> None:
        if not video_path.is_file():
            messagebox.showwarning("提示", f"找不到视频：{video_path}")
            return
        t = self._theme()
        win = tk.Toplevel(self)
        self._theme_toplevel(win, f"视频预览 · {video_path.name}", "760x520")
        win.configure(bg=t["popup_video_bg"])

        lbl = tk.Label(win, bg=t["popup_video_bg"])
        lbl.pack(fill="both", expand=True, padx=10, pady=10)
        ctrl = tk.Frame(win, bg=t["popup_ctrl_bg"], pady=6)
        ctrl.pack(fill="x")
        state = {"playing": True, "closed": False}

        def _toggle() -> None:
            state["playing"] = not state["playing"]
            btn_pp.config(text="⏸ 暂停" if state["playing"] else "▶ 播放")

        btn_pp = ttk.Button(ctrl, text="⏸ 暂停", command=_toggle)
        btn_pp.pack(side="left", padx=10)
        ttk.Button(ctrl, text="用系统播放器打开", command=lambda: os.startfile(str(video_path))).pack(side="left", padx=4)
        ttk.Button(ctrl, text="关闭", command=win.destroy).pack(side="right", padx=10)

        cap = cv2.VideoCapture(str(video_path))

        def _on_close() -> None:
            state["closed"] = True
            try:
                cap.release()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

        def _tick() -> None:
            if state["closed"]:
                return
            if state["playing"]:
                ok, frame = cap.read()
                if not ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = cap.read()
                if ok and frame is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    im = Image.fromarray(rgb)
                    im.thumbnail((720, 420), Image.Resampling.LANCZOS)
                    tk_im = ImageTk.PhotoImage(im)
                    lbl.configure(image=tk_im)
                    lbl.image = tk_im
            win.after(40, _tick)

        _tick()

    def on_open_host_kb_dir(self) -> None:
        kh = self.var_kb_host.get().strip()
        target = (bk.KB_ROOT_DIR / kh) if (kh and kh != bk.KB_ALL_HOSTS_LABEL) else bk.KB_ROOT_DIR
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def on_open_kb_root_dir(self) -> None:
        bk.KB_ROOT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(bk.KB_ROOT_DIR))

    def on_scan_kb_and_bg(self) -> None:
        self.kb_docs = bk.list_kb_docs()
        kb_hosts = bk.list_kb_hosts()
        self.cb_kb_host["values"] = kb_hosts
        self._on_kb_host_selected(target_doc=self.var_kb_doc.get())
        self.log(f"🔄 扫描完成：主播知识库 {len(kb_hosts)} 个，讲稿 {len(self.kb_docs)} 份")

    def on_add_video_to_kb_folder(self) -> None:
        item = self._selected_kb_item()
        kh = self.var_kb_host.get().strip()
        if not kh or kh == bk.KB_ALL_HOSTS_LABEL:
            kh = "公共知识库"
        folder = Path(item["folder"]) if item.get("folder") else (bk.KB_ROOT_DIR / kh / "默认分类")
        folder.mkdir(parents=True, exist_ok=True)
        f = filedialog.askopenfilename(title="选择视频复制到当前讲稿目录", filetypes=[("Video", "*.mp4 *.webm")])
        if not f:
            return
        shutil.copy2(f, folder / Path(f).name)
        bk.add_bg_video_file(Path(f))
        self._on_kb_doc_selected()
        self.log(f"已添加视频 {Path(f).name} 到 {folder}")

    def on_import_kb(self) -> None:
        kh = self.var_kb_host.get().strip()
        if not kh or kh == bk.KB_ALL_HOSTS_LABEL:
            kh = "公共知识库"
        f = filedialog.askopenfilename(title="选择 Markdown 或 TXT 讲稿", filetypes=[("Markdown/Text", "*.md *.markdown *.txt")])
        if not f:
            return
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "导入到知识库目录", "380x140")
        ttk.Label(win, text=f"保存到「知识库/{kh}/」下的分类目录名：").pack(padx=14, pady=(12, 6), anchor="w")
        vcat = tk.StringVar(value="默认分类")
        ttk.Entry(win, textvariable=vcat, width=38).pack(padx=14, fill="x")

        def _ok() -> None:
            cat = vcat.get().strip() or "默认分类"
            ok, msg = bk.import_kb_file(Path(f), theme_dir=f"{kh}/{cat}")
            win.destroy()
            if ok:
                target_dir = bk.KB_ROOT_DIR / kh / cat
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bk.KB_DIR / kh / cat / (Path(f).stem + ".md"), target_dir / (Path(f).stem + ".md"))
                self.on_scan_kb_and_bg()
                messagebox.showinfo("导入成功", msg)
            else:
                messagebox.showerror("导入拦截", msg)

        ttk.Button(win, text="确定导入", command=_ok).pack(pady=10)

    def on_new_kb_dialog(self) -> None:
        kh = self.var_kb_host.get().strip()
        if not kh or kh == bk.KB_ALL_HOSTS_LABEL:
            kh = "公共知识库"
        win = tk.Toplevel(self)
        self._theme_toplevel(win, f"新建讲稿到 {kh}", "560x420")
        r = ttk.Frame(win, padding=10)
        r.pack(fill="x")
        ttk.Label(r, text="分类目录：").pack(side="left")
        vcat = tk.StringVar(value="新主题分类")
        ttk.Entry(r, textvariable=vcat, width=16).pack(side="left", padx=4)
        ttk.Label(r, text="讲稿文件名：").pack(side="left")
        vfn = tk.StringVar(value="new_topic.md")
        ttk.Entry(r, textvariable=vfn, width=20).pack(side="left", padx=4)
        txt = scrolledtext.ScrolledText(win, height=15, font=("Microsoft YaHei UI", 10))
        self._style_scrolled_text(txt)
        txt.pack(fill="both", expand=True, padx=10, pady=6)
        txt.insert("1.0", "# 第1章 主题标题\n> [板书] 核心要点写在这里\n口播正文段落，60到90字一段。\n")

        def _save() -> None:
            cat = vcat.get().strip() or "默认分类"
            fn = vfn.get().strip() or "new_topic.md"
            ok, msg = bk.save_new_kb_text(fn, txt.get("1.0", "end"), theme_dir=f"{kh}/{cat}")
            if ok:
                target_dir = bk.KB_ROOT_DIR / kh / cat
                target_dir.mkdir(parents=True, exist_ok=True)
                md_name = fn if fn.lower().endswith(".md") else fn + ".md"
                src_md = bk.KB_DIR / kh / cat / md_name
                if src_md.is_file():
                    shutil.copy2(src_md, target_dir / md_name)
                win.destroy()
                self.on_scan_kb_and_bg()
                messagebox.showinfo("保存成功", msg)
            else:
                messagebox.showerror("保存失败", msg)

        ttk.Button(win, text="保存并刷新预览", command=_save).pack(pady=8)

    def on_auto_find_obs(self) -> None:
        p = bk.find_obs_executable()
        if p:
            self.var_obs_path.set(p)
            self.lcfg["obs_path"] = p
            bk.save_json(bk.LAUNCHER_CFG_PATH, self.lcfg)
            messagebox.showinfo("已找到 OBS", p)
        else:
            messagebox.showwarning("未找到", "未能自动探寻到 obs64.exe，请点击右侧「浏览」手动选择")

    def on_browse_obs(self) -> None:
        f = filedialog.askopenfilename(title="选择 obs64.exe", filetypes=[("OBS Executable", "obs64.exe"), ("All EXE", "*.exe")])
        if f:
            self.var_obs_path.set(f)
            self.lcfg["obs_path"] = f
            bk.save_json(bk.LAUNCHER_CFG_PATH, self.lcfg)

    def on_generate_obs_presets(self) -> None:
        ok, msg = bk.generate_project_obs_presets()
        self.log(msg)
        messagebox.showinfo("生成项目 OBS 配置", msg)

    def _update_obs_profiles_hint(self) -> None:
        existing_obs = bk.list_existing_obs_profiles()
        if not existing_obs:
            self.lbl_obs_profiles.config(text="本机配置：暂无")
            return
        preview = ", ".join(existing_obs[:2]) + (f" 等{len(existing_obs)}项" if len(existing_obs) > 2 else "")
        self.lbl_obs_profiles.config(text=f"本机已配：{preview}")

    def on_import_obs_now(self) -> None:
        p = self._selected_obs_preset()
        ok, msg = bk.import_obs_preset_to_obs(p["profile"])
        self._update_obs_profiles_hint()
        self.log(msg)
        messagebox.showinfo("导入 OBS", msg)

    def on_manual_import_obs(self) -> None:
        p = self._selected_obs_preset()
        f = filedialog.askopenfilename(
            title="选择 OBS 配置文件 (.ini / .json)",
            initialdir=str(bk.OBS_PRESETS_DIR),
            filetypes=[("OBS Config", "*.ini *.json")],
        )
        if not f:
            return
        ok, msg = bk.manual_import_obs_file(Path(f), target_profile=p["profile"])
        self._update_obs_profiles_hint()
        self.log(msg)
        messagebox.showinfo("手动导入 OBS", msg) if ok else messagebox.showwarning("手动导入 OBS", msg)

    def on_open_access_guide(self) -> None:
        local_guide = bk.HUB_DIR / "static" / "access-guide.html"
        if local_guide.is_file():
            webbrowser.open(local_guide.as_uri())
        else:
            messagebox.showwarning("提示", f"未找到教程文件：{local_guide}")

    def on_save_global_cfg(self) -> None:
        # 保存全局配置前再次校验平台与 OBS 模式完全对应并落盘
        plat = self.var_plat.get() or "douyin"
        ori = "portrait" if self.var_plat_ori.get() == "竖屏" else "landscape"
        match = next((x for x in bk.OBS_PRESETS if x["key"] == plat and x["orientation"] == ori), None)
        if match and self.var_obs_mode.get() != match["label"]:
            self.var_obs_mode.set(match["label"])
        obs_p = self._selected_obs_preset()
        bk.import_obs_preset_to_obs(obs_p["profile"], scene_name=obs_p["scene"])
        self.save_all_configs()
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "保存全局配置", "400x145")
        ttk.Label(win, text="请输入全局配置文件名称：").pack(padx=14, pady=(14, 6), anchor="w")
        default_name = self.var_gcfg.get().strip() or f"全局配置_{self.var_plat.get()}_{self.var_host_name.get()}"
        v = tk.StringVar(value=default_name)
        ttk.Entry(win, textvariable=v, width=42).pack(padx=14, fill="x")

        def _ok() -> None:
            ok, msg = bk.save_global_config(v.get())
            if ok:
                clean = re.sub(r'[\\/:*?"<>|]', "_", v.get().strip())
                gcfgs = bk.list_global_configs()
                self.cb_gcfg["values"] = gcfgs
                matched = clean if clean in gcfgs else v.get().strip()
                self.var_gcfg.set(matched)
                self.lcfg["last_global_config"] = matched
                self._step_done["global"] = True
                self._update_summary()
                self.log(f"[步骤6已确认] {msg}")
                win.destroy()
                messagebox.showinfo("已保存", msg)
            else:
                messagebox.showwarning("失败", msg)

        ttk.Button(win, text="保存", command=_ok).pack(pady=10)

    def on_load_global_cfg(self) -> None:
        name = self.var_gcfg.get().strip()
        if not name:
            return
        ok, msg = bk.load_global_config(name)
        if ok:
            self.var_gcfg.set(name)
            self.reload_all_data()
            for k in self._step_done:
                self._step_done[k] = True
            self._voice_locked_by_step1 = True
            self._update_summary()
            obs_p = self._selected_obs_preset()
            bk.import_obs_preset_to_obs(obs_p["profile"], scene_name=obs_p["scene"])
            self.log(msg)
            messagebox.showinfo("已加载", msg)
        else:
            messagebox.showwarning("加载失败", msg)

    def on_browse_global_cfg(self) -> None:
        f = filedialog.askopenfilename(
            title="选择全局配置 JSON 文件",
            initialdir=str(bk.GLOBAL_CFG_DIR),
            filetypes=[("JSON", "*.json")],
        )
        if not f:
            return
        ok, msg = bk.load_global_config(f)
        if ok:
            self.var_gcfg.set(Path(f).stem)
            self.reload_all_data()
            for k in self._step_done:
                self._step_done[k] = True
            self._voice_locked_by_step1 = True
            self._update_summary()
            obs_p = self._selected_obs_preset()
            bk.import_obs_preset_to_obs(obs_p["profile"], scene_name=obs_p["scene"])
            self.log(msg)
            messagebox.showinfo("已加载", msg)
        else:
            messagebox.showwarning("加载失败", msg)

    def on_check_adb(self) -> None:
        ok, msg = bk.check_adb_devices()
        self.log(msg)
        messagebox.showinfo("ADB 检测", msg) if ok else messagebox.showwarning("ADB 检测", msg)

    def on_adb_reverse(self) -> None:
        ok, msg = bk.setup_android_adb_reverse(1935)
        self.log(msg)
        messagebox.showinfo("ADB 转发", msg) if ok else messagebox.showwarning("ADB 转发", msg)

    def on_apply_android_obs(self) -> None:
        p = self._selected_obs_preset()
        ok, msg = bk.apply_android_stream_to_obs(p["profile"], self.var_android_rtmp.get(), self.var_android_key.get())
        self.log(msg)
        messagebox.showinfo("Root 安卓推流配置", msg)

    def on_launch_obs_now(self) -> None:
        p = self._selected_obs_preset()
        bk.import_obs_preset_to_obs(p["profile"], scene_name=p["scene"])
        if self.var_android_vcam.get():
            bk.apply_android_stream_to_obs(p["profile"], self.var_android_rtmp.get(), self.var_android_key.get())
        if bk.is_port_open("127.0.0.1", 4455, 0.25):
            if bk.is_port_open("127.0.0.1", 28910, 0.2):
                code, res = bk.http_json(
                    "http://127.0.0.1:28910/api/obs/sync",
                    "POST",
                    {"obs_profile": p["profile"], "obs_scene": p["scene"], "orientation": p["orientation"]},
                    timeout=6.0,
                )
                msg = (res.get("obs_msg") if isinstance(res, dict) else str(res)) or f"HTTP {code}"
            else:
                msg = "检测到 OBS WebSocket (:4455) 已在运行，已刷新磁盘预设并在开播时自动同步场景"
            self.log(msg)
            messagebox.showinfo("OBS 同步", msg)
            return
        ok, msg = bk.launch_obs(self.var_obs_path.get(), profile=p["profile"], scene=p["scene"])
        self.log(msg)
        if not ok:
            messagebox.showwarning("拉起 OBS", msg)

    def on_detect_platform(self) -> None:
        plat = self.var_plat.get() or "douyin"
        ok, msg = bk.detect_platform_app(plat)
        self.lbl_plat_status.config(text=f"平台应用状态：{msg}")
        self.log(f"[平台检测 {plat}] {msg}")

    def on_export_platform_cfg(self) -> None:
        plat = self.var_plat.get() or "douyin"
        ok, msg = bk.export_platform_config(plat, self.var_room_id.get().strip(), self.var_proxy.get().strip())
        self.log(msg)
        messagebox.showinfo("平台配置导出", msg)

    def on_validate_and_save_platform(self) -> None:
        """第5步：选定平台后校验并自动对齐 OBS 多模式配置、预写目标场景并完整保存。"""
        plat = self.var_plat.get() or "douyin"
        ori = "portrait" if self.var_plat_ori.get() == "竖屏" else "landscape"
        match = next((x for x in bk.OBS_PRESETS if x["key"] == plat and x["orientation"] == ori), None)
        if not match:
            match = next((x for x in bk.OBS_PRESETS if x["key"] == plat), bk.OBS_PRESETS[0])
            self.var_plat_ori.set("竖屏" if match["orientation"] == "portrait" else "横屏")
        self.var_obs_mode.set(match["label"])
        bk.import_obs_preset_to_obs(match["profile"], scene_name=match["scene"])
        self._step_done["plat"] = True
        self._update_plat_obs_label()
        self.save_all_configs()
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(
                target=lambda: bk.http_json(
                    "http://127.0.0.1:28910/api/platform/switch",
                    "POST",
                    {
                        "platform": plat,
                        "room_id": self.var_room_id.get().strip(),
                        "sync_obs": True,
                        "orientation": match["orientation"],
                        "obs_profile": match["profile"],
                        "obs_scene": match["scene"],
                    },
                    timeout=6.0,
                ),
                daemon=True,
            ).start()
        msg = f"校验通过！平台 [{bk.plat_cn_label(plat)} / {self.var_plat_ori.get()}] ⇄ OBS [{match['profile']} / 场景「{match['scene']}」] 已对应锁定并保存。"
        self.log(f"[步骤5已确认] {msg}")
        messagebox.showinfo("第 5 步校验并保存成功", msg)

    def _on_obs_mode_changed(self) -> None:
        p = self._selected_obs_preset()
        self.var_plat.set(p["key"])
        self.var_plat_ori.set("竖屏" if p["orientation"] == "portrait" else "横屏")
        self._sync_preset_display()
        self._update_plat_obs_label()
        self._update_summary()
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(
                target=lambda: bk.http_json(
                    "http://127.0.0.1:28910/api/obs/sync",
                    "POST",
                    {"obs_profile": p["profile"], "obs_scene": p["scene"], "orientation": p["orientation"]},
                    timeout=5.0,
                ),
                daemon=True,
            ).start()
            self.log(f"已同步切换 OBS 场景与预览方向：{p['label']}")

    def _on_plat_ori_changed(self) -> None:
        plat = self.var_plat.get() or "douyin"
        ori = "portrait" if self.var_plat_ori.get() == "竖屏" else "landscape"
        match = next((x for x in bk.OBS_PRESETS if x["key"] == plat and x["orientation"] == ori), None)
        if match:
            self.var_obs_mode.set(match["label"])
            self._on_obs_mode_changed()

    def _on_platform_selected(self, refresh_summary: bool = True) -> None:
        plat = self.var_plat.get()
        p = self.profs.get("profiles", {}).get(plat, {})
        self.var_room_id.set(p.get("last_room_id", ""))
        self.var_proxy.set(p.get("proxy", ""))
        ori = p.get("orientation", "landscape")
        self.var_plat_ori.set("竖屏" if ori == "portrait" else "横屏")
        saved_prof = str(p.get("obs_profile") or "").strip()
        obs_match = next((x["label"] for x in bk.OBS_PRESETS if x["key"] == plat and saved_prof and x["profile"] == saved_prof), None)
        if not obs_match:
            obs_match = next((x["label"] for x in bk.OBS_PRESETS if x["key"] == plat and x["orientation"] == ori), None)
        if not obs_match:
            obs_match = next((x["label"] for x in bk.OBS_PRESETS if x["key"] == plat), None)
        if obs_match:
            self.var_obs_mode.set(obs_match)
        # 切换平台/横竖屏时绝不擅自覆盖用户在「3. 知识库主题」中选择的讲稿
        guide = bk.PLATFORM_GUIDES.get(plat, "请先选择平台并导入 OBS 配置文件。")
        self.txt_plat_guide.delete("1.0", "end")
        self.txt_plat_guide.insert("1.0", guide)
        self._update_plat_obs_label()
        self.on_detect_platform()
        if refresh_summary:
            self._sync_preset_display()
            self._update_summary()

    def _collect_workflow_state(self) -> Dict[str, Any]:
        obs_p = self._selected_obs_preset()
        kb_item = self._selected_kb_item()
        mode = self._active_tts_mode()
        opts_sf = bk.sf_voice_options_all(self.cfg, self._sf_custom_voices)
        return {
            "platform": self.var_plat.get() or "douyin",
            "room_id": self.var_room_id.get().strip(),
            "proxy": self.var_proxy.get().strip(),
            "avatar_id": self._selected_avatar_id(),
            "ul_asr": self.var_ul_asr.get() if hasattr(self, "var_ul_asr") else "hubert",
            "ul_backend": self.var_ul_backend.get() if hasattr(self, "var_ul_backend") else "onnx",
            "host_name": self.var_host_name.get().strip() or "主播",
            "live_style": self.var_live_style.get().strip(),
            "system_prompt": self.txt_prompt.get("1.0", "end").strip(),
            "tts_mode": mode,
            "tts_voice": bk.voice_id_from_label(self.var_edge_voice.get()) or "zh-CN-XiaoxiaoNeural",
            "sf_voice": bk.sf_voice_id_from_label(self.var_sf_voice.get(), opts_sf),
            "gsv_ref": self.var_gsv_ref.get().strip(),
            "gsv_prompt": self.var_gsv_prompt.get().strip(),
            "tts_emotion": self.var_sf_emo.get().strip(),
            "kb_host": self.var_kb_host.get().strip(),
            "kb_doc": kb_item.get("hub_doc") or self.var_kb_doc.get().strip(),
            "kb_narration_mode": self.var_kb_narration.get(),
            "obs_path": self.var_obs_path.get().strip(),
            "obs_profile": obs_p["profile"],
            "obs_scene": obs_p["scene"],
            "orientation": obs_p["orientation"],
            "auto_obs": self.var_auto_obs.get(),
            "open_webui": self.var_open_web.get(),
            "preset": self.var_preset.get().strip(),
            "last_global_config": self.var_gcfg.get().strip(),
        }

    def save_all_configs(self) -> None:
        st = self._collect_workflow_state()
        self.wf.apply_prelaunch_config(st)
        self.cfg = bk.load_json(bk.CONFIG_PATH, self.cfg)
        self.profs = bk.load_json(bk.PROFILES_PATH, self.profs)
        self.lcfg = bk.load_json(bk.LAUNCHER_CFG_PATH, self.lcfg)
        self._update_summary()
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(
                target=lambda: bk.http_json("http://127.0.0.1:28910/api/reload", "POST", timeout=4.0),
                daemon=True,
            ).start()
        self.log("已完整保存并同步全部开播配置。")

    def apply_selected_preset(self) -> None:
        name = self.var_preset.get()
        p = next((x for x in self.presets if x.get("name") == name), None)
        if not p:
            return
        is_content3 = (p.get("preset_scope") == "content3") or (not p.get("platform"))
        # 第4步保存的前三项预设（preset_scope="content3"）仅加载 声音+主播+知识库，绝不改动当前平台与 OBS 模式！
        if not is_content3:
            plat = p.get("platform", "douyin")
            self.var_plat.set(plat)
            self._on_platform_selected(refresh_summary=False)
            self.var_room_id.set(p.get("room_id", ""))
            self.var_proxy.set(p.get("proxy", ""))
            if p.get("obs_profile"):
                obs_m = next((x["label"] for x in bk.OBS_PRESETS if x["profile"] == p["obs_profile"]), None)
                if obs_m:
                    self.var_obs_mode.set(obs_m)
                    self.var_plat_ori.set("竖屏" if "720x1280" in p["obs_profile"] else "横屏")
        # 2. 同步主播形象与人设
        aid = p.get("avatar_id", "demo_ul_host")
        matched_h = next((f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts if h["id"] == aid), aid)
        self.var_avatar.set(matched_h)
        prev_lock = getattr(self, "_voice_locked_by_step1", False)
        self._voice_locked_by_step1 = False
        self._on_host_selected()
        if p.get("host_name"):
            self.var_host_name.set(p["host_name"])
        if p.get("live_style"):
            self.var_live_style.set(p["live_style"])
        if p.get("system_prompt"):
            self.txt_prompt.delete("1.0", "end")
            self.txt_prompt.insert("1.0", p["system_prompt"])
        # 3. 同步声音（TTS 来源 / 各引擎对应声线 / 语气前缀）
        if p.get("tts_mode"):
            self.var_tts_mode.set(bk.tts_mode_label_for_key(p["tts_mode"]))
        if p.get("tts_voice"):
            self.var_edge_voice.set(bk.voice_label_for_id(p["tts_voice"]))
        if p.get("sf_voice"):
            self.var_sf_voice.set(p["sf_voice"])
        if p.get("gsv_ref"):
            self.var_gsv_ref.set(p["gsv_ref"])
        if p.get("gsv_prompt"):
            self.var_gsv_prompt.set(p["gsv_prompt"])
        if p.get("tts_emotion"):
            self.var_sf_emo.set(p["tts_emotion"])
        self._refresh_active_voice_selector()
        self._voice_locked_by_step1 = True if is_content3 else prev_lock
        # 4. 仅当预设明确包含 kb_doc（如第4步存的前三项预设）时同步知识库；默认主播模板不覆写知识库
        if p.get("kb_doc"):
            if p.get("kb_host") and p["kb_host"] in list(self.cb_kb_host["values"]):
                self.var_kb_host.set(p["kb_host"])
            self._on_kb_host_selected(target_doc=p.get("kb_doc", ""))
        self.var_kb_narration.set(bool(p.get("kb_narration_mode", True)))
        self._step_done.update({"tts": True, "host": True, "kb": bool(p.get("kb_doc") or self.var_kb_doc.get()), "preset": True})
        self._sync_preset_display()
        self._update_plat_obs_label()
        self._update_summary()
        self.log(f"✅ 已加载{'前三项内容预设' if is_content3 else '开播模板'}：{name}")

    def save_current_as_preset(self) -> None:
        st = self._collect_workflow_state()
        kb_t = bk.kb_short_title(st.get("kb_doc", ""))
        # 第4步：只保存前 3 项（1.声音 + 2.主播与主题 + 3.知识库），不绑定平台/OBS！
        preset_obj = {
            "preset_scope": "content3",
            "avatar_id": st["avatar_id"],
            "host_name": st["host_name"],
            "live_style": st["live_style"],
            "system_prompt": st["system_prompt"],
            "tts_mode": st["tts_mode"],
            "tts_voice": st["tts_voice"],
            "sf_voice": st["sf_voice"],
            "gsv_ref": st["gsv_ref"],
            "gsv_prompt": st["gsv_prompt"],
            "tts_emotion": st["tts_emotion"],
            "kb_host": st["kb_host"],
            "kb_doc": st["kb_doc"],
            "kb_narration_mode": st["kb_narration_mode"],
        }
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "另存前三项为当前开播预设（声音+主播+知识库）", "440x145")
        ttk.Label(win, text="请输入前三项开播预设名称（不绑定平台）：").pack(padx=14, pady=(14, 6), anchor="w")
        v = tk.StringVar(value=f"{st['host_name']} · {kb_t}")
        ttk.Entry(win, textvariable=v, width=46).pack(padx=14, fill="x")

        def _ok() -> None:
            name = v.get().strip()
            if not name:
                return
            preset_obj["name"] = name
            self.presets = bk.save_preset(preset_obj)
            self.cb_preset["values"] = [x["name"] for x in self.presets]
            self.var_preset.set(name)
            self._step_done["preset"] = True
            self.save_all_configs()
            win.destroy()
            self.log(f"[步骤4已确认] 已将前 3 项（声音+主播+知识库）存为预设：{name}")
            messagebox.showinfo("第 4 步已确认", f"前三项（声音 + 主播 + 知识库）已存为预设：\n{name}")

        ttk.Button(win, text="保存预设", command=_ok).pack(pady=10)

    def delete_selected_preset(self) -> None:
        name = self.var_preset.get()
        if not name:
            return
        self.presets = bk.delete_preset(name)
        self.cb_preset["values"] = [x["name"] for x in self.presets]
        if self.lcfg.get("preset") == name:
            self.lcfg.pop("preset", None)
            bk.save_json(bk.LAUNCHER_CFG_PATH, self.lcfg)
        self.var_preset.set(self.presets[0]["name"] if self.presets else "")
        if not self.presets:
            self._sync_preset_display()
        self._update_summary()
        self.log(f"已删除预设：{name}")

    def on_start_workflow(self) -> None:
        st = self._collect_workflow_state()
        self.btn_start.config(state="disabled")

        def _run() -> None:
            try:
                ok, msg = self.wf.start_workflow(st, self.log)
            except Exception as exc:
                ok, msg = False, f"启动直播工作流发生未预期异常：{exc}"
                self.log("❌ " + msg)
            self.after(0, lambda: self.btn_start.config(state="normal"))
            if not ok:
                self.log("❌ 启动失败: " + msg)
                self.after(0, lambda m=msg: messagebox.showerror("启动失败", m))
            else:
                self.log("✅ " + msg)

        threading.Thread(target=_run, daemon=True).start()

    def on_stop_workflow(self) -> None:
        self.wf.stop_workflow(self.log)

    def on_live_ask(self) -> None:
        txt = self.var_live_ask.get().strip()
        if not txt:
            self._append_live_log("[提示] 请先在「观众提问」输入框输入内容再发送")
            return
        self.var_live_ask.set("")
        plat = self.var_plat.get().strip() or "mock"
        threading.Thread(
            target=lambda: bk.http_json(
                "http://127.0.0.1:28910/api/danmaku",
                "POST",
                {"type": "chat", "user": "测试观众", "text": txt, "platform": plat},
            ),
            daemon=True,
        ).start()
        self._append_live_log(f"[模拟观众提问] 测试观众：{txt}")

    def on_live_say(self) -> None:
        txt = self.var_live_say.get().strip()
        if not txt:
            return
        self.var_live_say.set("")
        threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/echo", "POST", {"text": txt, "interrupt": False}), daemon=True).start()
        self._append_live_log(f"[主播指令] {txt}")

    def on_live_interrupt(self) -> None:
        threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/interrupt", "POST"), daemon=True).start()
        self._append_live_log("[控制] 已发送打断停播指令")

    def on_live_action(self, action_id: str) -> None:
        threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/action/play", "POST", {"action_id": action_id, "user": "控制台", "speak": True}), daemon=True).start()
        self._append_live_log(f"[互动动作] 触发 {action_id}")

    def on_live_gift(self) -> None:
        threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/danmaku", "POST", {"type": "gift", "user": "测试观众", "gift_item": "墨镜", "gift_num": 1, "platform": "mock"}), daemon=True).start()
        self._append_live_log("[模拟礼物] 测试观众 送出 墨镜 x1")

    def _append_live_log(self, msg: str) -> None:
        self.txt_live_log.insert("end", msg + "\n")
        self.txt_live_log.see("end")

    def _on_live_canvas_configure(self, event=None) -> None:
        w = int(getattr(event, "width", 0) or self.live_canvas.winfo_width() or 0)
        h = int(getattr(event, "height", 0) or self.live_canvas.winfo_height() or 0)
        if w > 40 and h > 40 and (w, h) != getattr(self, "_last_live_canvas_size", (0, 0)):
            self._last_live_canvas_size = (w, h)
            self._render_live_preview(self._last_hub_st)

    def on_toggle_preview(self) -> None:
        """预览开关：桌面示意预览 + 中控/WebUI 真实画面预览一起开关（关掉后预览页自动断开，省 CPU）。"""
        on = bool(self.var_preview_on.get())
        self.lcfg["preview_enabled"] = on
        bk.save_json(bk.LAUNCHER_CFG_PATH, self.lcfg)
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/preview", "POST", {"enabled": on}, timeout=4.0),
                             daemon=True).start()
        self.log("已开启直播画面预览" if on else "已关闭直播画面预览（真实画面预览页会自动断开）")
        self._render_live_preview(self._last_hub_st)

    def on_open_real_preview(self) -> None:
        if not bk.is_port_open("127.0.0.1", 28910, 0.2):
            messagebox.showwarning("提示", "需要先开播（启动中控 :28910）才能看真实画面预览")
            return
        if not self.var_preview_on.get():
            self.var_preview_on.set(True)
            self.on_toggle_preview()
        else:
            bk.http_json("http://127.0.0.1:28910/api/preview", "POST", {"enabled": True}, timeout=4.0)
        # 预览页共享 OBS 同一路画面，不额外推理、不影响正式直播
        webbrowser.open("http://127.0.0.1:28910/stage?layer=all&muted=0&preview=1")

    def _render_live_preview(self, hub_st: Dict[str, Any] | None) -> None:
        self._last_hub_st = hub_st
        if hasattr(self, "var_preview_on"):
            pv = (hub_st or {}).get("preview") if isinstance(hub_st, dict) else None
            if isinstance(pv, dict) and "enabled" in pv and bool(pv["enabled"]) != bool(self.var_preview_on.get()):
                self.var_preview_on.set(bool(pv["enabled"]))   # 网页端改了开关，桌面端同步
            if not self.var_preview_on.get():
                t = self._theme()
                self.live_canvas.delete("all")
                cw = max(self.live_canvas.winfo_width(), 264)
                chh = max(self.live_canvas.winfo_height(), 190)
                self.live_canvas.create_text(cw // 2, chh // 2, text="预览已关闭\n勾选上方「开启预览」即可查看",
                                             fill=t.get("fg_muted", "#888888"), font=("Microsoft YaHei UI", 11, "bold"), justify="center")
                self.lbl_prev_state.config(text="预览已关闭（不影响直播）")
                return
        t = self._theme()
        aid = (hub_st or {}).get("avatar_id") or self._selected_avatar_id() or "demo_ul_host"
        host_name = (hub_st or {}).get("host_name") or self.var_host_name.get() or "主播"
        speaking = bool((hub_st or {}).get("is_speaking"))
        sub = (hub_st or {}).get("current_subtitle") or ""
        plat_ori = (hub_st or {}).get("orientation") or ("portrait" if self.var_plat_ori.get() == "竖屏" else "landscape")
        plat_lbl = (hub_st or {}).get("active_platform_label") or ""
        mode = self.var_prev_ratio.get()
        is_port = (plat_ori == "portrait") if mode == "auto" else (mode == "portrait")
        ori_txt = "竖屏 9:16" if is_port else "横屏 16:9"
        prefix = f"[{plat_lbl}] " if plat_lbl else ""
        self.lbl_prev_state.config(text=f"{prefix}{host_name} ({aid}) · {ori_txt} · {'说话中' if speaking else '待机'}")

        w_raw = self.live_canvas.winfo_width()
        h_raw = self.live_canvas.winfo_height()
        cw = w_raw if w_raw > 40 else 264
        ch = h_raw if h_raw > 40 else 280
        # 按 9:16 或 16:9 计算画幅框尺寸，始终适配真实画布高度避免底部裁剪
        max_w, max_h = max(80, cw - 16), max(80, ch - 14)
        ratio = (9.0 / 16.0) if is_port else (16.0 / 9.0)
        if max_w / max_h > ratio:
            fh = max_h
            fw = max(60, int(fh * ratio))
        else:
            fw = max_w
            fh = max(60, int(fw / ratio))
        x0 = (cw - fw) // 2
        y0 = (ch - fh) // 2
        x1 = x0 + fw
        y1 = y0 + fh

        av_dir = bk.get_avatars_dir()
        img_path = None
        if av_dir:
            for rel in ("full_body_img/0.jpg", "full_body_img/0.png", "full_imgs/00000000.png"):
                cand = av_dir / aid / rel
                if cand.is_file():
                    img_path = cand
                    break
        self.live_canvas.delete("all")
        self.live_canvas.create_rectangle(x0, y0, x1, y1, fill=t["stage_bg"], outline=t["rim_glow"], width=2)
        if img_path and img_path.is_file():
            try:
                bgr = cv2.imread(str(img_path))
                if bgr is not None:
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    im = Image.fromarray(rgb)
                    im.thumbnail((max(40, fw - 6), max(40, fh - 6)), Image.Resampling.LANCZOS)
                    tk_im = ImageTk.PhotoImage(im)
                    self._live_preview_img = tk_im
                    self.live_canvas.create_image(cw // 2, y1 - 3, image=tk_im, anchor="s")
            except Exception:
                pass
        else:
            self.live_canvas.create_text(cw // 2, ch // 2, text=f"{host_name}\n({ori_txt})", fill="#FFFFFF", font=("Microsoft YaHei UI", 11, "bold"), justify="center")

        ov_src = (hub_st or {}).get("overlay")
        ov = bk.sanitize_overlay_cfg(ov_src, base=bk.get_overlay_cfg(self.cfg)) if isinstance(ov_src, dict) else bk.get_overlay_cfg(self.cfg)

        if ov.get("show_badge", True):
            self.live_canvas.create_rectangle(x0 + 6, y0 + 6, x0 + 92, y0 + 24, fill=t["stage_badge_bg"], outline=t["rim_glow"])
            self.live_canvas.create_text(x0 + 49, y0 + 15, text=ori_txt, fill=t["rim_glow"], font=("Microsoft YaHei UI", 8, "bold"))

        if ov.get("show_kb_board", True):
            kb_st = (hub_st or {}).get("knowledge_base") or {}
            chap_title = str(kb_st.get("current_chapter_title") or "知识库章节字幕")
            kw = min(116, max(72, int(fw * 0.46)))
            kx0 = (x0 + 6) if ov.get("board_position") == "top-left" else (x1 - kw - 6)
            ky0 = y0 + (28 if (ov.get("board_position") == "top-left" and ov.get("show_badge", True)) else 6)
            self.live_canvas.create_rectangle(kx0, ky0, kx0 + kw, ky0 + 26, fill="#111827", outline=t["rim_glow"])
            self.live_canvas.create_text(
                kx0 + 6,
                ky0 + 13,
                anchor="w",
                text=f"章节:{chap_title[:9]}",
                fill=ov.get("text_color", "#FFFFFF"),
                font=("Microsoft YaHei UI", 8, "bold"),
            )

        if sub and ov.get("show_subtitle", True):
            bx0, bx1 = x0 + 8, x1 - 8
            bot_margin = max(6, int(fh * int(ov.get("bottom_offset_pct", 4)) / 100.0))
            by1 = y1 - bot_margin
            by0 = max(y0 + 30, by1 - 40)
            if int(ov.get("bg_opacity", 78)) > 5:
                self.live_canvas.create_rectangle(bx0, by0, bx1, by1, fill="#000000", outline="")
            self.live_canvas.create_text(
                cw // 2,
                (by0 + by1) // 2,
                text=sub[:34],
                fill=ov.get("text_color", "#FFFFFF"),
                font=("Microsoft YaHei UI", 9, "bold"),
                width=max(60, fw - 24),
                justify="center",
            )

    def _poll_status(self) -> None:
        def _check() -> None:
            lt_ok = bk.is_port_open("127.0.0.1", 8020, 0.25)
            hub_ok = bk.is_port_open("127.0.0.1", 28910, 0.25)
            obs_ok = bk.is_port_open("127.0.0.1", 4455, 0.25)
            hub_st = bk.fetch_hub_status() if hub_ok else None
            hub_hl = bk.fetch_hub_health() if hub_ok else None

            def _upd() -> None:
                self._last_hub_states = (lt_ok, hub_ok, obs_ok)
                self._last_hub_health = hub_hl
                self._paint_pill("lt", "ok" if lt_ok else "err", "Ultralight: 运行中" if lt_ok else "Ultralight: 未启动")
                self._paint_pill("hub", "ok" if hub_ok else "err", "中控: 运行中" if hub_ok else "中控: 未启动")
                self._paint_pill("obs", "ok" if obs_ok else "warn", "OBS WS: 已连接" if obs_ok else "OBS WS: 未连接")
                if hub_hl:
                    ov = hub_hl.get("overall", "warn")
                    tone = "ok" if ov == "ok" else ("err" if ov == "error" else "warn")
                    txt = f"🩺 诊断: {hub_hl.get('summary', '正常')}"
                    self._last_health_badge = (tone, txt)
                    self._paint_pill("health", tone, txt)
                else:
                    self._last_health_badge = ("warn", "🩺 诊断/工具箱")
                    self._paint_pill("health", "warn", "🩺 诊断/工具箱")
                if hub_st:
                    sig = (hub_st.get("active_platform"), hub_st.get("orientation"))
                    prev = getattr(self, "_last_hub_plat_sig", None)
                    self._last_hub_plat_sig = sig
                    # 仅在已建立基线且中控/OBS真实发生切换后才反向同步，避免首次轮询覆盖用户刚加载的离线配置
                    if prev is not None and sig != prev:
                        h_ori = hub_st.get("orientation", "landscape")
                        h_plat = hub_st.get("active_platform", "")
                        if h_plat:
                            self.var_plat.set(h_plat)
                        self.var_plat_ori.set("竖屏" if h_ori == "portrait" else "横屏")
                        obs_match = next(
                            (x["label"] for x in bk.OBS_PRESETS if x["key"] == h_plat and x["orientation"] == h_ori),
                            None,
                        )
                        if obs_match:
                            self.var_obs_mode.set(obs_match)
                        self._update_summary()
                self._render_live_preview(hub_st)
                if hub_st and hub_st.get("logs"):
                    seen_cnt = getattr(self, "_seen_hub_log_keys", set())
                    for entry in hub_st["logs"][-20:]:
                        key = f"{entry.get('ts')}|{entry.get('level')}|{entry.get('text')}"
                        if key not in seen_cnt:
                            seen_cnt.add(key)
                            self._append_live_log(f"[{entry.get('ts', '')}][{entry.get('level', '')}] {entry.get('text', '')}")
                    self._seen_hub_log_keys = seen_cnt

            try:
                self.after(0, _upd)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=_check, daemon=True).start()
        self.after(3000, self._poll_status)


    # ============================================================
    # 集成控制单元、系统诊断工具箱与主播训练/管理交互方法
    # ============================================================
    def _goto_train_tab_prefilled(self) -> None:
        aid = self.var_new_aid.get().strip() or self._selected_avatar_id()
        aname = self.var_new_aname.get().strip() or self.var_host_name.get().strip()
        if aid:
            self.var_tr_aid.set(aid)
        if aname:
            self.var_tr_name.set(aname)
        self.nb.select(self.tab_train)

    def on_copy_stage_url(self) -> None:
        url = "http://127.0.0.1:28910/stage?layer=all"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.log(f"已复制 OBS 舞台地址: {url}")
        messagebox.showinfo("已复制", f"已复制 OBS 浏览器源地址到剪贴板：\n{url}\n\n纯叠层地址：\nhttp://127.0.0.1:28910/stage?layer=overlay")

    def on_save_sched_cfg(self) -> None:
        patch = {
            "auto_reply": bool(self.var_auto_reply.get()),
            "gift_interrupt_narration": bool(self.var_gift_int.get()),
            "auto_warmup": bool(self.var_auto_warmup.get()),
            "warmup_interval_sec": max(5, int(self.var_warmup_gap.get() or 25)),
        }
        threading.Thread(target=lambda: bk.save_scheduler_and_bg_cfg(patch, {}), daemon=True).start()
        self.log(f"已应用实时调度策略: 答疑={patch['auto_reply']} 打断={patch['gift_interrupt_narration']} 暖场={patch['auto_warmup']}({patch['warmup_interval_sec']}s)")

    def on_kb_ctrl(self, action: str) -> None:
        if not bk.is_port_open("127.0.0.1", 28910, 0.2):
            messagebox.showwarning("提示", "请先启动中控 (:28910)")
            return
        threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/kb/control", "POST", {"action": action}, timeout=4.0), daemon=True).start()
        self._append_live_log(f"[讲稿控制] 发送指令: {action}")

    def on_clear_queue(self) -> None:
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/clear_queue", "POST", timeout=3.0), daemon=True).start()
        self._append_live_log("[弹幕控制] 已请求清空排队弹幕")

    def on_stop_collector(self) -> None:
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/platform/stop_collector", "POST", timeout=3.0), daemon=True).start()
        self._append_live_log("[弹幕控制] 已发送停止接收弹幕指令")

    def on_save_bg_cfg(self) -> None:
        try:
            sim = float(self.var_bg_sim.get())
            sm = float(self.var_bg_smooth.get())
            sp = float(self.var_bg_spill.get())
        except ValueError:
            messagebox.showwarning("参数错误", "相似度/柔化/溢色必须为小数，如 0.34 / 0.08 / 0.16")
            return
        bg_patch = {
            "mode": self.var_bg_mode.get(),
            "default_video": self.var_bg_video.get().strip(),
            "similarity": sim,
            "smoothness": sm,
            "spill": sp,
        }
        threading.Thread(target=lambda: bk.save_scheduler_and_bg_cfg({}, bg_patch), daemon=True).start()
        self.log(f"已应用背景与抠像参数: mode={bg_patch['mode']} video={bg_patch['default_video']} sim={sim}")

    def _sync_overlay_vars(self, ov: Dict[str, Any]) -> None:
        if not hasattr(self, "var_ov_kb"):
            return
        self.var_ov_kb.set(bool(ov.get("show_kb_board", True)))
        self.var_ov_sub.set(bool(ov.get("show_subtitle", True)))
        self.var_ov_pos.set(bk.overlay_id_to_label("position", str(ov.get("board_position", "top-right"))))
        self.var_ov_font.set(bk.overlay_id_to_label("font", str(ov.get("font_preset", "noto_sans_sc"))))
        self.var_ov_eff.set(bk.overlay_id_to_label("effect", str(ov.get("effect_preset", "classic_dark"))))
        if hasattr(self, "var_ov_fs"):
            self.var_ov_fs.set(str(ov.get("font_size", 24)))

    def _apply_overlay_patch_async(self, patch: Dict[str, Any], apply_effect_preset: bool = False, log_prefix: str = "[字幕/字体]") -> None:
        merged = bk.save_overlay_cfg(patch, apply_effect_preset=apply_effect_preset)
        self.cfg["overlay"] = merged
        self._sync_overlay_vars(merged)
        self._render_live_preview(self._last_hub_st)
        self.log(
            f"{log_prefix} 章节卡={'开' if merged['show_kb_board'] else '关'}({merged['board_position']}) "
            f"口播字幕={'开' if merged['show_subtitle'] else '关'} 字体={merged['font_preset']} 效果={merged['effect_preset']}"
        )

    def on_save_overlay_quick(self) -> None:
        patch = {
            "show_kb_board": bool(self.var_ov_kb.get()),
            "board_position": bk.overlay_label_to_id("position", self.var_ov_pos.get()),
            "show_subtitle": bool(self.var_ov_sub.get()),
            "font_preset": bk.overlay_label_to_id("font", self.var_ov_font.get()),
        }
        self._apply_overlay_patch_async(patch, apply_effect_preset=False)

    def on_bump_sub_font(self, delta: int) -> None:
        cur = int(bk.get_overlay_cfg(self.cfg).get("font_size", 24))
        new = max(14, min(80, cur + int(delta)))
        self.var_ov_fs.set(str(new))
        self._apply_overlay_patch_async({"font_size": new}, apply_effect_preset=False, log_prefix=f"[字幕字号 {new}px，超长自动换行]")

    def on_save_overlay_eff_preset(self) -> None:
        eff_id = bk.overlay_label_to_id("effect", self.var_ov_eff.get())
        self._apply_overlay_patch_async({"effect_preset": eff_id}, apply_effect_preset=True, log_prefix="[字幕效果预设]")

    def on_open_overlay_dialog(self) -> None:
        from PIL import ImageDraw, ImageFont

        ov = bk.get_overlay_cfg(self.cfg)
        t = self._theme()
        dlg = tk.Toplevel(self)
        dlg.title("直播画面：章节板书、口播字幕与免费商用字体设置（SIL OFL 1.1）")
        dlg.geometry("880x600")
        dlg.minsize(820, 540)
        dlg.configure(bg=t["win_bg"])
        dlg.transient(self)

        outer = ttk.Frame(dlg, padding=12)
        outer.pack(fill="both", expand=True)

        left = ttk.LabelFrame(outer, text="字幕开关 · 免费无授权字体 · 效果参数", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right = ttk.LabelFrame(outer, text="9:16 舞台实时效果预览（真字体渲染）", padding=8)
        right.pack(side="right", fill="y")

        v_kb = tk.BooleanVar(value=bool(ov["show_kb_board"]))
        v_info = tk.BooleanVar(value=bool(ov["show_info_card"]))
        v_badge = tk.BooleanVar(value=bool(ov["show_badge"]))
        v_sub = tk.BooleanVar(value=bool(ov["show_subtitle"]))
        v_pill = tk.BooleanVar(value=bool(ov["show_question_pill"]))
        v_pos = tk.StringVar(value=bk.overlay_id_to_label("position", ov["board_position"]))
        v_font = tk.StringVar(value=bk.overlay_id_to_label("font", ov["font_preset"]))
        v_eff = tk.StringVar(value=bk.overlay_id_to_label("effect", ov["effect_preset"]))
        v_fsize = tk.IntVar(value=int(ov["font_size"]))
        v_bsize = tk.IntVar(value=int(ov["board_font_size"]))
        v_fweight = tk.StringVar(value=str(ov["font_weight"]))
        v_tcol = tk.StringVar(value=str(ov["text_color"]))
        v_scol = tk.StringVar(value=str(ov["stroke_color"]))
        v_swidth = tk.IntVar(value=int(ov["stroke_width"]))
        v_bgop = tk.IntVar(value=int(ov["bg_opacity"]))
        v_boff = tk.IntVar(value=int(ov["bottom_offset_pct"]))
        v_anim = tk.StringVar(value=bk.overlay_id_to_label("animation", ov["animation"]))
        v_lines = tk.StringVar(value=str(ov.get("sub_max_lines", 3)))
        of_map = {"paginate": "保持字号，分几屏轮播（推荐）", "shrink": "自动缩小字号塞进去"}
        v_overflow = tk.StringVar(value=of_map.get(str(ov.get("sub_overflow", "paginate")), of_map["paginate"]))
        v_swpct = tk.IntVar(value=int(ov.get("sub_width_pct", 90)))
        v_lic = tk.StringVar(value="")

        def _safe_int(var: tk.Variable, fallback: int) -> int:
            try:
                return int(float(str(var.get()).strip()))
            except Exception:
                return fallback

        def collect_payload() -> Dict[str, Any]:
            return bk.sanitize_overlay_cfg(
                {
                    "show_kb_board": v_kb.get(),
                    "board_position": bk.overlay_label_to_id("position", v_pos.get()),
                    "show_info_card": v_info.get(),
                    "show_badge": v_badge.get(),
                    "show_subtitle": v_sub.get(),
                    "show_question_pill": v_pill.get(),
                    "font_preset": bk.overlay_label_to_id("font", v_font.get()),
                    "effect_preset": bk.overlay_label_to_id("effect", v_eff.get()),
                    "font_size": _safe_int(v_fsize, 24),
                    "board_font_size": _safe_int(v_bsize, 14),
                    "font_weight": _safe_int(v_fweight, 600),
                    "text_color": v_tcol.get(),
                    "stroke_color": v_scol.get(),
                    "stroke_width": _safe_int(v_swidth, 2),
                    "bg_opacity": _safe_int(v_bgop, 78),
                    "bottom_offset_pct": _safe_int(v_boff, 4),
                    "animation": bk.overlay_label_to_id("animation", v_anim.get()),
                    "sub_max_lines": _safe_int(v_lines, 3),
                    "sub_overflow": next((k for k, lbl in of_map.items() if lbl == v_overflow.get()), "paginate"),
                    "sub_width_pct": _safe_int(v_swpct, 90),
                }
            )

        def _hex_to_rgb(hx: str, fallback: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
            s = str(hx or "").strip().lstrip("#")
            if len(s) == 6:
                try:
                    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
                except ValueError:
                    pass
            return fallback

        def _load_pil_font(font_id: str, px: int):
            fpath = bk.resolve_overlay_font_file(font_id)
            if fpath and fpath.is_file():
                try:
                    return ImageFont.truetype(str(fpath), max(10, px))
                except Exception:
                    pass
            return ImageFont.load_default()

        prev_lbl = tk.Label(right, bg=t["canvas_bg"], bd=0)
        prev_lbl.pack(fill="both", expand=True)

        def refresh_preview(*_args) -> None:
            cfg_cur = collect_payload()
            fid = cfg_cur["font_preset"]
            finfo = bk.OVERLAY_FONT_BY_ID.get(fid, {})
            fpath = bk.resolve_overlay_font_file(fid)
            fstatus = "本地文件已就绪" if (fpath and fpath.is_file()) else "使用回退字体"
            v_lic.set(f"授权：{finfo.get('license', 'SIL OFL 1.1')} · {fstatus}")

            pw, ph = 300, 480
            img = Image.new("RGBA", (pw, ph), (22, 48, 36, 255))
            draw = ImageDraw.Draw(img, "RGBA")
            # 背景暗色舞台与人物剪影提示
            draw.ellipse((pw // 2 - 42, 125, pw // 2 + 42, 215), fill=(214, 174, 148, 255))
            draw.rounded_rectangle((pw // 2 - 68, 210, pw // 2 + 68, ph - 10), radius=26, fill=(41, 52, 74, 255))

            f_sub = _load_pil_font(fid, max(12, int(round(cfg_cur["font_size"] * 0.68))))
            f_brd = _load_pil_font(fid, max(10, int(round(cfg_cur["board_font_size"] * 0.85))))
            f_sm = _load_pil_font(fid, 10)

            tcol = _hex_to_rgb(cfg_cur["text_color"], (255, 255, 255)) + (255,)
            scol = _hex_to_rgb(cfg_cur["stroke_color"], (0, 0, 0)) + (255,)
            sw = int(cfg_cur["stroke_width"])
            op_alpha = int(round(255 * int(cfg_cur["bg_opacity"]) / 100.0))

            if cfg_cur["show_badge"]:
                draw.rounded_rectangle((10, 10, 86, 30), radius=10, fill=(10, 16, 28, 195), outline=(56, 189, 248, 180))
                draw.text((18, 14), "AI 讲解中", font=f_sm, fill=(224, 242, 254, 255))

            if cfg_cur["show_info_card"]:
                draw.rounded_rectangle((10, 36, 134, 72), radius=8, fill=(12, 18, 30, 185))
                draw.text((16, 40), "主题：说书与商品讲解", font=f_sm, fill=(226, 232, 240, 255))
                draw.text((16, 55), "阶段：正在按章节精讲", font=f_sm, fill=(148, 163, 184, 255))

            if cfg_cur["show_kb_board"]:
                bw = 148
                bx0 = 10 if cfg_cur["board_position"] == "top-left" else (pw - bw - 10)
                by0 = 78 if (cfg_cur["board_position"] == "top-left" and (cfg_cur["show_info_card"] or cfg_cur["show_badge"])) else 10
                card_fill = (255, 255, 255, 215) if tcol[0] < 60 and tcol[1] < 60 else (12, 18, 30, max(125, op_alpha))
                draw.rounded_rectangle((bx0, by0, bx0 + bw, by0 + 82), radius=10, fill=card_fill, outline=(96, 165, 250, 160))
                draw.text((bx0 + 8, by0 + 8), "章节大纲 · 第 1 章", font=f_brd, fill=tcol, stroke_width=min(1, sw), stroke_fill=scol)
                draw.text((bx0 + 8, by0 + 30), "• 第一节：核心卖点拆解", font=f_sm, fill=tcol)
                draw.text((bx0 + 8, by0 + 47), "• 第二节：高频问答要点", font=f_sm, fill=tcol)

            bot_y = ph - max(10, int(ph * int(cfg_cur["bottom_offset_pct"]) / 100.0))
            if cfg_cur["show_subtitle"]:
                # 按真实规则演示：舞台 720 宽 → 预览 300 宽，字号同比例缩放；按宽度自动换行，超行数时分屏/缩小
                scale = pw / 720.0
                demo = "欢迎来到直播间，今天咱们讲讲怎么识破AI换脸骗局，手机验证码绝不能告诉任何人，遇到借钱先挂断再回拨确认。"
                max_lines = int(cfg_cur.get("sub_max_lines", 3))
                box_w = int(pw * int(cfg_cur.get("sub_width_pct", 90)) / 100.0)
                fs_px = max(8, int(round(cfg_cur["font_size"] * 0.82 * scale)))

                def _wrap(txt: str, font) -> List[str]:
                    lines_out, cur = [], ""
                    for ch in txt:
                        if draw.textlength(cur + ch, font=font) <= box_w - 16:
                            cur += ch
                        else:
                            lines_out.append(cur)
                            cur = ch
                    if cur:
                        lines_out.append(cur)
                    return lines_out

                f_sub = _load_pil_font(fid, fs_px)
                lines = _wrap(demo, f_sub)
                if cfg_cur.get("sub_overflow") == "shrink":
                    while len(lines) > max_lines and fs_px > max(8, int(14 * scale)):
                        fs_px -= 1
                        f_sub = _load_pil_font(fid, fs_px)
                        lines = _wrap(demo, f_sub)
                page_note = ""
                if len(lines) > max_lines:
                    pages = (len(lines) + max_lines - 1) // max_lines
                    page_note = f"第 1/{pages} 屏"
                    lines = lines[:max_lines]
                line_h = int(fs_px * 1.45)
                sub_h = line_h * len(lines) + 14
                sy0 = max(90, bot_y - sub_h)
                bx0, bx1 = (pw - box_w) // 2, (pw + box_w) // 2
                if op_alpha > 10:
                    box_rgb = (248, 250, 252) if tcol[0] < 60 and tcol[1] < 60 else (8, 12, 20)
                    draw.rounded_rectangle((bx0, sy0, bx1, bot_y), radius=10, fill=box_rgb + (op_alpha,))
                for li, ln in enumerate(lines):
                    draw.text((pw // 2, sy0 + 7 + li * line_h + line_h // 2), ln, font=f_sub, anchor="mm",
                              fill=tcol, stroke_width=min(sw, 2), stroke_fill=scol)
                if page_note:
                    draw.text((bx1 - 4, sy0 - 4), page_note, font=f_sm, anchor="rb", fill=(250, 204, 21, 255))
                pill_anchor_y = sy0 - 8
            else:
                pill_anchor_y = bot_y - 8

            if cfg_cur["show_question_pill"]:
                py0 = max(60, pill_anchor_y - 24)
                draw.rounded_rectangle((36, py0, pw - 36, py0 + 22), radius=11, fill=(14, 165, 233, 190))
                draw.text((pw // 2, py0 + 11), "💬 观众提问：支持哪些免费字体？", font=f_sm, anchor="mm", fill=(255, 255, 255, 255))

            tk_im = ImageTk.PhotoImage(img.convert("RGB"))
            dlg._preview_photo = tk_im
            prev_lbl.configure(image=tk_im)

        # 左侧表单布局
        r0 = ttk.Frame(left, style="Card.TFrame")
        r0.pack(fill="x", pady=3)
        ttk.Checkbutton(r0, text="知识库章节板书卡片", variable=v_kb, command=refresh_preview).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(r0, text="顶部信息副卡", variable=v_info, command=refresh_preview).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(r0, text="状态角标", variable=v_badge, command=refresh_preview).pack(side="left")

        r1 = ttk.Frame(left, style="Card.TFrame")
        r1.pack(fill="x", pady=3)
        ttk.Checkbutton(r1, text="底部口播实时字幕", variable=v_sub, command=refresh_preview).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(r1, text="观众提问气泡", variable=v_pill, command=refresh_preview).pack(side="left", padx=(0, 12))
        ttk.Label(r1, text="章节卡位置:", style="Card.TLabel").pack(side="left", padx=(4, 4))
        cb_p = ttk.Combobox(r1, textvariable=v_pos, values=bk.overlay_labels("position"), state="readonly", width=18)
        cb_p.pack(side="left")
        cb_p.bind("<<ComboboxSelected>>", refresh_preview)

        r2 = ttk.Frame(left, style="Card.TFrame")
        r2.pack(fill="x", pady=(8, 2))
        ttk.Label(r2, text="免费商用字体:", width=14, style="Card.TLabel").pack(side="left")
        cb_f = ttk.Combobox(r2, textvariable=v_font, values=bk.overlay_labels("font"), state="readonly")
        cb_f.pack(side="left", fill="x", expand=True)
        cb_f.bind("<<ComboboxSelected>>", refresh_preview)
        ttk.Label(left, textvariable=v_lic, style="CardHint.TLabel").pack(anchor="w", pady=(0, 6))

        def on_pick_effect(_e=None) -> None:
            eid = bk.overlay_label_to_id("effect", v_eff.get())
            eff = bk.OVERLAY_EFFECT_BY_ID.get(eid)
            if eff:
                v_tcol.set(str(eff["text_color"]))
                v_scol.set(str(eff["stroke_color"]))
                v_swidth.set(int(eff["stroke_width"]))
                v_bgop.set(int(eff["bg_opacity"]))
                v_fweight.set(str(eff["font_weight"]))
            refresh_preview()

        r3 = ttk.Frame(left, style="Card.TFrame")
        r3.pack(fill="x", pady=4)
        ttk.Label(r3, text="效果预设:", width=14, style="Card.TLabel").pack(side="left")
        cb_e = ttk.Combobox(r3, textvariable=v_eff, values=bk.overlay_labels("effect"), state="readonly")
        cb_e.pack(side="left", fill="x", expand=True)
        cb_e.bind("<<ComboboxSelected>>", on_pick_effect)

        grid = ttk.Frame(left, style="Card.TFrame")
        grid.pack(fill="x", pady=6)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        ttk.Label(grid, text="口播字号(px):", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        sb_fs = ttk.Spinbox(grid, from_=14, to=80, increment=2, textvariable=v_fsize, width=7, command=refresh_preview)
        sb_fs.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(grid, text="章节字号(px):", style="Card.TLabel").grid(row=0, column=2, sticky="w", pady=4)
        sb_bs = ttk.Spinbox(grid, from_=11, to=26, textvariable=v_bsize, width=7, command=refresh_preview)
        sb_bs.grid(row=0, column=3, sticky="w", padx=6)

        ttk.Label(grid, text="字体粗细:", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        cb_w = ttk.Combobox(grid, textvariable=v_fweight, values=["400", "500", "600", "700", "800", "900"], state="readonly", width=6)
        cb_w.grid(row=1, column=1, sticky="w", padx=6)
        cb_w.bind("<<ComboboxSelected>>", refresh_preview)
        ttk.Label(grid, text="进入动效:", style="Card.TLabel").grid(row=1, column=2, sticky="w", pady=4)
        cb_a = ttk.Combobox(grid, textvariable=v_anim, values=bk.overlay_labels("animation"), state="readonly", width=15)
        cb_a.grid(row=1, column=3, sticky="w", padx=6)
        cb_a.bind("<<ComboboxSelected>>", refresh_preview)

        def pick_color(var: tk.StringVar, title: str) -> None:
            res = colorchooser.askcolor(color=var.get(), title=title, parent=dlg)
            if res and res[1]:
                var.set(str(res[1]).upper())
                refresh_preview()

        ttk.Label(grid, text="字幕颜色:", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        cf1 = ttk.Frame(grid, style="Card.TFrame")
        cf1.grid(row=2, column=1, sticky="w", padx=6)
        ttk.Entry(cf1, textvariable=v_tcol, width=9).pack(side="left")
        ttk.Button(cf1, text="选色", style="Compact.TButton", command=lambda: pick_color(v_tcol, "选择字幕文字颜色")).pack(side="left", padx=4)

        ttk.Label(grid, text="描边颜色:", style="Card.TLabel").grid(row=2, column=2, sticky="w", pady=4)
        cf2 = ttk.Frame(grid, style="Card.TFrame")
        cf2.grid(row=2, column=3, sticky="w", padx=6)
        ttk.Entry(cf2, textvariable=v_scol, width=9).pack(side="left")
        ttk.Button(cf2, text="选色", style="Compact.TButton", command=lambda: pick_color(v_scol, "选择字幕描边颜色")).pack(side="left", padx=4)

        ttk.Label(grid, text="描边宽度(0-6):", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Spinbox(grid, from_=0, to=6, textvariable=v_swidth, width=7, command=refresh_preview).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(grid, text="底框透明度(%):", style="Card.TLabel").grid(row=3, column=2, sticky="w", pady=4)
        ttk.Spinbox(grid, from_=0, to=95, increment=5, textvariable=v_bgop, width=7, command=refresh_preview).grid(row=3, column=3, sticky="w", padx=6)

        ttk.Label(grid, text="字幕距底边距(%):", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Spinbox(grid, from_=1, to=40, textvariable=v_boff, width=7, command=refresh_preview).grid(row=4, column=1, sticky="w", padx=6)
        ttk.Label(grid, text="字幕最宽(%):", style="Card.TLabel").grid(row=4, column=2, sticky="w", pady=4)
        ttk.Spinbox(grid, from_=60, to=98, increment=2, textvariable=v_swpct, width=7, command=refresh_preview).grid(row=4, column=3, sticky="w", padx=6)

        ttk.Label(grid, text="字幕最多几行:", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=4)
        cb_ln = ttk.Combobox(grid, textvariable=v_lines, values=["1", "2", "3", "4", "5"], state="readonly", width=6)
        cb_ln.grid(row=5, column=1, sticky="w", padx=6)
        cb_ln.bind("<<ComboboxSelected>>", refresh_preview)
        ttk.Label(grid, text="太长放不下时:", style="Card.TLabel").grid(row=5, column=2, sticky="w", pady=4)
        cb_of = ttk.Combobox(grid, textvariable=v_overflow, values=list(of_map.values()), state="readonly", width=24)
        cb_of.grid(row=5, column=3, sticky="w", padx=6)
        cb_of.bind("<<ComboboxSelected>>", refresh_preview)
        ttk.Label(grid, text="字号可调到 80px；调大后字幕会按屏幕宽度自动换行，超过设定行数时分屏轮播或自动缩小，不会溢出屏幕。",
                  style="CardHint.TLabel", wraplength=470).grid(row=6, column=0, columnspan=4, sticky="w", pady=(2, 0))

        for v in (v_tcol, v_scol, v_fsize, v_bsize, v_swidth, v_bgop, v_boff, v_swpct):
            v.trace_add("write", refresh_preview)

        btns = ttk.Frame(left, style="Card.TFrame")
        btns.pack(fill="x", side="bottom", pady=(12, 0))

        def on_apply() -> None:
            payload = collect_payload()
            self._apply_overlay_patch_async(payload, apply_effect_preset=False, log_prefix="[字幕详调]")
            messagebox.showinfo("已生效", "已保存并实时推送到直播画面，OBS 浏览器源无需手动刷新。", parent=dlg)

        def on_reset() -> None:
            d = dict(bk.DEFAULT_OVERLAY_CFG)
            v_kb.set(bool(d["show_kb_board"]))
            v_info.set(bool(d["show_info_card"]))
            v_badge.set(bool(d["show_badge"]))
            v_sub.set(bool(d["show_subtitle"]))
            v_pill.set(bool(d["show_question_pill"]))
            v_pos.set(bk.overlay_id_to_label("position", d["board_position"]))
            v_font.set(bk.overlay_id_to_label("font", d["font_preset"]))
            v_eff.set(bk.overlay_id_to_label("effect", d["effect_preset"]))
            v_fsize.set(int(d["font_size"]))
            v_bsize.set(int(d["board_font_size"]))
            v_fweight.set(str(d["font_weight"]))
            v_tcol.set(str(d["text_color"]))
            v_scol.set(str(d["stroke_color"]))
            v_swidth.set(int(d["stroke_width"]))
            v_bgop.set(int(d["bg_opacity"]))
            v_boff.set(int(d["bottom_offset_pct"]))
            v_anim.set(bk.overlay_id_to_label("animation", d["animation"]))
            v_lines.set(str(d.get("sub_max_lines", 3)))
            v_overflow.set(of_map.get(str(d.get("sub_overflow", "paginate")), of_map["paginate"]))
            v_swpct.set(int(d.get("sub_width_pct", 90)))
            refresh_preview()

        ttk.Button(btns, text="✅ 立即应用并实时生效", style="Primary.TButton", command=on_apply).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="恢复默认", command=on_reset).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="刷新预览", command=refresh_preview).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="关闭", command=dlg.destroy).pack(side="right")

        refresh_preview()

    def on_upload_bg_video(self) -> None:
        f = filedialog.askopenfilename(title="选择绿幕背景视频或图片", filetypes=[("Media", "*.mp4 *.webm *.jpg *.jpeg *.png *.webp")])
        if not f:
            return
        ok, name = bk.add_bg_video_file(Path(f))
        if ok:
            files = bk.list_bg_videos()
            self.cb_bg_video["values"] = files
            self.var_bg_video.set(name)
            self.on_save_bg_cfg()
            self.log(f"已添加背景素材: {name}")

    def on_bind_bg_doc(self) -> None:
        item = self._selected_kb_item()
        doc = item.get("hub_doc") or self.var_kb_doc.get().strip()
        vid = self.var_bg_video.get().strip()
        if not doc or not vid:
            messagebox.showwarning("提示", "请先选择知识库讲稿及背景视频")
            return
        bk.bind_kb_bg_video(doc, vid)
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/reload", "POST", timeout=3.0), daemon=True).start()
        self.log(f"已绑定讲稿 [{doc}] 的专属背景视频 -> {vid}")
        messagebox.showinfo("绑定成功", f"讲稿 [{doc}] 已绑定背景视频：{vid}")

    def on_live_engine_action(self, action_id: int) -> None:
        threading.Thread(target=lambda: bk.http_json(f"http://127.0.0.1:28910/api/action/{action_id}", "POST", timeout=4.0), daemon=True).start()
        self._append_live_log(f"[表情动作] 触发动作 ID={action_id}")

    def on_kb_reindex(self) -> None:
        if not bk.is_port_open("127.0.0.1", 28910, 0.2):
            messagebox.showwarning("提示", "请先启动中控 (:28910)")
            return
        threading.Thread(target=lambda: bk.http_json("http://127.0.0.1:28910/api/kb/control", "POST", {"action": "reindex"}, timeout=15.0), daemon=True).start()
        self.log("已发送知识库索引重建指令 (/api/kb/control reindex)")
        messagebox.showinfo("已触发", "已在后台重建当前讲稿向量与词法索引")

    def on_kb_test_search_dialog(self) -> None:
        if not bk.is_port_open("127.0.0.1", 28910, 0.2):
            messagebox.showwarning("提示", "测试问法检索需要先启动中控 (:28910)")
            return
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "🧪 测试观众问法检索命中结果", "560x320")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        vq = tk.StringVar(value="AI 会犯错吗")
        ttk.Entry(row, textvariable=vq, width=42).pack(side="left", fill="x", expand=True, padx=(0, 6))
        out_txt = scrolledtext.ScrolledText(frm, height=12, font=("Microsoft YaHei UI", 9))
        self._style_scrolled_text(out_txt)
        out_txt.pack(fill="both", expand=True)

        def _test() -> None:
            q = vq.get().strip()
            if not q:
                return
            code, res = bk.http_json("http://127.0.0.1:28910/api/kb/test_search", "POST", {"query": q}, timeout=8.0)
            out_txt.delete("1.0", "end")
            if code == 200 and isinstance(res, dict):
                out_txt.insert("1.0", json.dumps(res, ensure_ascii=False, indent=2))
            else:
                out_txt.insert("1.0", f"HTTP {code}: {res}")

        ttk.Button(row, text="开始检索", command=_test).pack(side="right")

    # ------------------------------------------------------------
    # 🧸 傻瓜配置向导（与 WebUI 共用 ai-live-hub/config_guide.py 唯一真源）
    # ------------------------------------------------------------
    def _guide_apply(self, patch: Dict[str, Any], label: str, on_done=None) -> None:
        import config_guide as cg

        try:
            res = cg.apply_patch(bk.HUB_DIR, bk.ROOT_DIR, patch)
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        if bk.is_port_open("127.0.0.1", 28910, 0.2):
            bk.http_json("http://127.0.0.1:28910/api/reload", method="POST", timeout=5.0)
        msg = f"{label}：{res['plain']}\n改动 {len(res['changed'])} 项：" + ("、".join(res["changed"][:12]) or "无变化")
        if res.get("restart_engine") and bk.is_port_open("127.0.0.1", 8020, 0.2):
            msg += "\n\n数字人引擎正在运行，请停止后重新开播，新的引擎设置才会生效。"
        self.log(msg.replace("\n", " "))
        messagebox.showinfo("已保存", msg)
        if on_done:
            on_done()

    def on_open_config_wizard(self) -> None:
        import config_guide as cg

        win = tk.Toplevel(self)
        self._theme_toplevel(win, "🧸 傻瓜配置向导 · 每项设置都有大白话解释", "920x680")
        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"按你的电脑推荐：{cg.HOST_PROFILE}", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)

        def _bundle() -> Dict[str, Any]:
            return cg.guide_bundle(bk.HUB_DIR, bk.ROOT_DIR)

        def _scroll_area(parent: tk.Misc) -> ttk.Frame:
            cv = tk.Canvas(parent, highlightthickness=0, bg=self._theme()["card_bg"])
            sb = ttk.Scrollbar(parent, orient="vertical", command=cv.yview)
            inner = ttk.Frame(cv)
            inner.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
            wid = cv.create_window((0, 0), window=inner, anchor="nw")
            cv.bind("<Configure>", lambda e: cv.itemconfigure(wid, width=e.width))
            cv.configure(yscrollcommand=sb.set)
            cv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            return inner

        # ① 三问向导
        tab1 = ttk.Frame(nb, padding=10)
        nb.add(tab1, text="① 回答 3 个问题")
        q_vars: Dict[str, tk.StringVar] = {}
        q_maps: Dict[str, Dict[str, str]] = {}
        for q in cg.WIZARD_QUESTIONS:
            ttk.Label(tab1, text=q["q"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
            labels = [o[1] for o in q["options"]]
            q_maps[q["id"]] = {o[1]: o[0] for o in q["options"]}
            v = tk.StringVar(value=labels[0])
            q_vars[q["id"]] = v
            ttk.Combobox(tab1, textvariable=v, values=labels, state="readonly", width=60).pack(anchor="w")
        out1 = scrolledtext.ScrolledText(tab1, height=9, font=("Microsoft YaHei UI", 9), wrap="word")
        self._style_scrolled_text(out1)

        def _answers() -> Dict[str, str]:
            return {k: q_maps[k].get(v.get(), "") for k, v in q_vars.items()}

        def _preview() -> None:
            res = cg.wizard(_answers())
            out1.delete("1.0", "end")
            out1.insert("1.0", "会这样配置：\n" + "\n".join(res["explain"]))

        btn1 = ttk.Frame(tab1)
        btn1.pack(fill="x", pady=8)
        ttk.Button(btn1, text="先看看会改什么", command=_preview).pack(side="left")
        ttk.Button(btn1, text="✅ 按我的回答一键配置", style="Primary.TButton",
                   command=lambda: self._guide_apply(cg.wizard(_answers())["patch"], "三问向导", _refresh_all)).pack(side="left", padx=8)
        out1.pack(fill="both", expand=True)

        # ② 一键预设
        tab2 = ttk.Frame(nb, padding=6)
        nb.add(tab2, text="② 一键预设")
        inner2 = _scroll_area(tab2)
        for p in cg.PRESETS:
            card = ttk.Frame(inner2, padding=8, style="Card.TFrame")
            card.pack(fill="x", pady=4, padx=4)
            ttk.Label(card, text=p["title"], font=("Microsoft YaHei UI", 10, "bold"), style="Card.TLabel").pack(anchor="w")
            ttk.Label(card, text=p["plain"], wraplength=760, style="Card.TLabel").pack(anchor="w", pady=2)
            ttk.Button(card, text="套用这个预设",
                       command=lambda pid=p["id"], title=p["title"]: (
                           messagebox.askyesno("确认", f"确定套用「{title}」吗？", parent=win)
                           and self._guide_apply(cg.preset_patch(pid), title, _refresh_all))).pack(anchor="e")

        # ③ 配置体检
        tab3 = ttk.Frame(nb, padding=6)
        nb.add(tab3, text="③ 配置体检")
        top3 = ttk.Frame(tab3)
        top3.pack(fill="x")
        ttk.Button(top3, text="🔄 重新体检", command=lambda: _refresh_doctor()).pack(side="left")
        ttk.Label(top3, text="按 12400F / 32GB / RTX 2080 的稳定红线检查当前配置").pack(side="left", padx=8)
        body3 = ttk.Frame(tab3)
        body3.pack(fill="both", expand=True, pady=6)
        inner3 = _scroll_area(body3)
        icons = {"error": "⛔", "warn": "⚠️", "info": "💡", "ok": "✅"}

        def _refresh_doctor() -> None:
            for w in inner3.winfo_children():
                w.destroy()
            for f in _bundle()["doctor"]:
                card = ttk.Frame(inner3, padding=8, style="Card.TFrame")
                card.pack(fill="x", pady=3, padx=4)
                ttk.Label(card, text=f"{icons.get(f['level'], '')} {f['title']}", font=("Microsoft YaHei UI", 10, "bold"),
                          style="Card.TLabel").pack(anchor="w")
                ttk.Label(card, text=f["plain"], wraplength=760, style="Card.TLabel").pack(anchor="w")
                if f.get("fix"):
                    ttk.Button(card, text="一键修复",
                               command=lambda fx=f["fix"], t=f["title"]: self._guide_apply(fx, f"修复：{t}", _refresh_all)).pack(anchor="e")

        # ④ 逐项微调
        tab4 = ttk.Frame(nb, padding=6)
        nb.add(tab4, text="④ 逐项微调（含白话说明）")
        top4 = ttk.Frame(tab4)
        top4.pack(fill="x")
        var_adv = tk.BooleanVar(value=False)
        var_pro = tk.BooleanVar(value=False)
        ttk.Checkbutton(top4, text="显示高级参数", variable=var_adv, command=lambda: _refresh_items()).pack(side="left")
        ttk.Checkbutton(top4, text="显示专业说明", variable=var_pro, command=lambda: _refresh_items()).pack(side="left", padx=8)
        body4 = ttk.Frame(tab4)
        body4.pack(fill="both", expand=True, pady=6)
        inner4 = _scroll_area(body4)
        item_vars: Dict[str, Any] = {}

        def _refresh_items() -> None:
            for w in inner4.winfo_children():
                w.destroy()
            item_vars.clear()
            last_group = ""
            for it in _bundle()["items"]:
                if it.get("level") != "basic" and not var_adv.get():
                    continue
                if it["group"] != last_group:
                    last_group = it["group"]
                    ttk.Label(inner4, text=last_group + ("（改完需重启数字人引擎）" if it.get("restart") else ""),
                              font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(8, 2), padx=4)
                card = ttk.Frame(inner4, padding=6, style="Card.TFrame")
                card.pack(fill="x", pady=2, padx=4)
                head = ttk.Frame(card, style="Card.TFrame")
                head.pack(fill="x")
                ttk.Label(head, text=it["label"], font=("Microsoft YaHei UI", 9, "bold"), style="Card.TLabel").pack(side="left")
                cur = it.get("current")
                if it["type"] == "bool":
                    v = tk.BooleanVar(value=str(cur).lower() in ("true", "1"))
                    ttk.Checkbutton(head, variable=v, style="Card.TCheckbutton").pack(side="right")
                elif it["type"] == "choice":
                    labels = [c[1] for c in it["choices"]]
                    cur_label = next((c[1] for c in it["choices"] if str(c[0]) == str(cur)), labels[0])
                    v = tk.StringVar(value=cur_label)
                    ttk.Combobox(head, textvariable=v, values=labels, state="readonly", width=34).pack(side="right")
                else:
                    v = tk.StringVar(value="" if cur is None else str(cur))
                    ttk.Spinbox(head, textvariable=v, from_=it.get("min", 0), to=it.get("max", 9999),
                                increment=0.05 if it["type"] == "float" else 1, width=10).pack(side="right")
                item_vars[it["key"]] = (it, v)
                ttk.Label(card, text=it["plain"], wraplength=780, style="Card.TLabel").pack(anchor="w")
                rec = it.get("recommend")
                if it["type"] == "choice":
                    rec = next((c[1] for c in it["choices"] if str(c[0]) == str(rec)), rec)
                elif it["type"] == "bool":
                    rec = "开" if rec else "关"
                ttk.Label(card, text=f"推荐：{rec}" + ("（当前就是推荐值）" if it.get("is_recommended") else ""),
                          style="Card.TLabel").pack(anchor="w")
                if var_pro.get() and it.get("pro"):
                    ttk.Label(card, text="专业说明：" + it["pro"], wraplength=780, style="Card.TLabel").pack(anchor="w")

        def _save_items() -> None:
            values: Dict[str, Any] = {}
            for key, (it, v) in item_vars.items():
                raw = v.get()
                if it["type"] == "choice":
                    raw = next((c[0] for c in it["choices"] if c[1] == raw), raw)
                if it["type"] in ("int", "float") and str(raw).strip() == "":
                    continue
                values[key] = raw
            try:
                patch = cg.items_to_patch(values)
            except ValueError as exc:
                messagebox.showerror("输入有误", str(exc), parent=win)
                return
            self._guide_apply(patch, "逐项微调", _refresh_all)

        ttk.Button(tab4, text="💾 保存修改", style="Primary.TButton", command=_save_items).pack(anchor="e")

        def _refresh_all() -> None:
            if win.winfo_exists():
                _refresh_doctor()
                _refresh_items()

        _refresh_all()

    # ------------------------------------------------------------
    # 🎯 主题点播目录：按主题索引查看知识库、测试观众说法、手动点播
    # ------------------------------------------------------------
    def on_open_topic_dialog(self) -> None:
        import asyncio as _aio

        import config_guide as cg
        from topic_router import TopicIndex

        win = tk.Toplevel(self)
        self._theme_toplevel(win, "🎯 观众点播主题目录（知识库按章节/小节自动整理）", "900x620")
        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        hub_on = bk.is_port_open("127.0.0.1", 28910, 0.2)
        ttk.Label(frm, text=("中控已运行：可以直接点“现在讲”插播主题。" if hub_on else
                             "中控未运行：可以浏览目录、测试观众说法（只用关键词规则）；开播后才能插播。"),
                  font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")

        cfg = json.loads((bk.HUB_DIR / "config.json").read_text(encoding="utf-8")) if (bk.HUB_DIR / "config.json").is_file() else {}
        idx = TopicIndex(bk.KB_DIR, cfg.get("knowledge_base", {}))

        top = ttk.Frame(frm)
        top.pack(fill="x", pady=6)
        v_filter = tk.StringVar()
        ttk.Label(top, text="搜主题：").pack(side="left")
        ent = ttk.Entry(top, textvariable=v_filter, width=30)
        ent.pack(side="left", padx=4)
        lbl_cnt = ttk.Label(top, text="")
        lbl_cnt.pack(side="left", padx=8)

        cols = ("doc", "beats", "kw")
        tv = ttk.Treeview(frm, columns=cols, show="tree headings", height=14)
        tv.heading("#0", text="主题")
        tv.heading("doc", text="讲稿")
        tv.heading("beats", text="段数")
        tv.heading("kw", text="观众可能这样叫")
        tv.column("#0", width=330)
        tv.column("doc", width=210)
        tv.column("beats", width=50, anchor="center")
        tv.column("kw", width=260)
        tv.pack(fill="both", expand=True)

        def _fill() -> None:
            idx.ensure_fresh(0)
            q = v_filter.get().strip().lower()
            tv.delete(*tv.get_children())
            parents: Dict[str, str] = {}
            shown = 0
            for t in idx.catalog():
                hay = (t["title"] + " " + " ".join(t["keywords"]) + " " + t["doc"]).lower()
                if q and q not in hay:
                    continue
                parent = ""
                if t["level"] == "section":
                    parent = parents.get(t["tid"].rsplit("s", 1)[0], "")
                iid = tv.insert(parent, "end", iid=t["tid"], text=t["title"], open=True,
                                values=(t["doc"], t["beats"], "、".join(t["keywords"][:4])))
                if t["level"] == "chapter":
                    parents[t["tid"]] = iid
                shown += 1
            lbl_cnt.config(text=f"显示 {shown} / 共 {len(idx.topics)} 个主题")

        v_filter.trace_add("write", lambda *_a: _fill())

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=6)

        def _play() -> None:
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("提示", "先在目录里选一个主题", parent=win)
                return
            if not bk.is_port_open("127.0.0.1", 28910, 0.2):
                messagebox.showwarning("提示", "需要先开播（启动中控 :28910）才能插播主题", parent=win)
                return
            code, res = bk.http_json("http://127.0.0.1:28910/api/topics/play", "POST", {"tid": sel[0], "user": "主播"}, timeout=8.0)
            messagebox.showinfo("点播", (res or {}).get("msg", str(res)) if isinstance(res, dict) else str(res), parent=win)

        def _stop() -> None:
            if bk.is_port_open("127.0.0.1", 28910, 0.2):
                bk.http_json("http://127.0.0.1:28910/api/topics/stop", "POST", {"clear_pending": True}, timeout=5.0)

        def _rebuild() -> None:
            idx.build()
            if bk.is_port_open("127.0.0.1", 28910, 0.2):
                bk.http_json("http://127.0.0.1:28910/api/topics/rebuild", "POST", {}, timeout=10.0)
            _fill()

        ttk.Button(btns, text="▶ 现在讲选中主题", style="Primary.TButton", command=_play).pack(side="left")
        ttk.Button(btns, text="■ 停止当前点播", command=_stop).pack(side="left", padx=6)
        ttk.Button(btns, text="🔄 重建目录", command=_rebuild).pack(side="left")

        test = ttk.Frame(frm)
        test.pack(fill="x", pady=(4, 2))
        v_q = tk.StringVar(value="主播先讲讲怎么防止被骗钱")
        ttk.Label(test, text="测试观众说法：").pack(side="left")
        ttk.Entry(test, textvariable=v_q, width=46).pack(side="left", padx=4, fill="x", expand=True)
        out = scrolledtext.ScrolledText(frm, height=7, font=("Microsoft YaHei UI", 9), wrap="word")
        self._style_scrolled_text(out)

        def _test() -> None:
            q = v_q.get().strip()
            if not q:
                return
            if bk.is_port_open("127.0.0.1", 28910, 0.2):
                _c, res = bk.http_json("http://127.0.0.1:28910/api/topics/test", "POST", {"text": q, "use_ai": True}, timeout=12.0)
                res = res if isinstance(res, dict) else {}
                topic = (res.get("topic") or {}).get("title", "")
            else:
                r = _aio.run(idx.route(None, q, [], allow_ai=False))
                res = {"is_request": r["is_request"], "method": r["method"], "reason": r["reason"], "candidates": r["candidates"]}
                topic = (r.get("topic") or {}).get("title", "")
            out.delete("1.0", "end")
            if res.get("is_request"):
                out.insert("1.0", f"✅ 会被当成点播（{res.get('method')}）→「{topic}」\n理由：{res.get('reason')}")
                if topic:
                    for iid in tv.get_children(""):
                        for cid in (iid,) + tuple(tv.get_children(iid)):
                            if tv.item(cid, "text") == topic:
                                tv.selection_set(cid)
                                tv.see(cid)
            else:
                cands = "、".join(f"「{c['title']}」{c['score']}" for c in (res.get("candidates") or [])[:3])
                out.insert("1.0", f"➖ 不会整段点播：{res.get('reason')}（会按普通提问回答）\n最接近的主题：{cands or '无'}")

        ttk.Button(test, text="测试会点到哪", command=_test).pack(side="right")
        out.pack(fill="both", expand=False)
        ttk.Label(frm, text=cg.KB_WRITING_TIPS.splitlines()[0] + "  加一行「> [主题词] 防骗|反诈」写上观众的叫法，点播更准。",
                  wraplength=860).pack(anchor="w", pady=(4, 0))
        _fill()

    def on_test_all_keys_cli(self) -> None:
        self.log("开始批量测试 config.json 全部 LLM / Embedding / TTS Key...")
        threading.Thread(
            target=lambda: bk.run_tool_script_cli("scripts/test_keys.py", log_cb=self.log),
            daemon=True,
        ).start()

    def on_clone_voice_dialog(self) -> None:
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "🎙 硅基流动 CosyVoice2 录音克隆新音色", "540x240")
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        vf = tk.StringVar()
        vn = tk.StringVar(value="host_v1")
        vt = tk.StringVar()
        ttk.Label(frm, text="8~10秒清晰人声 (.mp3/.wav)：").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=vf, width=36).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="选择音频", command=lambda: vf.set(filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav")]) or vf.get())).grid(row=0, column=2)
        ttk.Label(frm, text="音色英文标识：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=vn, width=36).grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)
        ttk.Label(frm, text="录音里说的原话：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=vt, width=36).grid(row=2, column=1, columnspan=2, sticky="ew", padx=4)
        frm.columnconfigure(1, weight=1)

        def _up() -> None:
            def _worker() -> None:
                ok, res = bk.upload_siliconflow_clone_voice(self.cfg, Path(vf.get().strip()), vn.get(), vt.get())
                def _done() -> None:
                    if ok:
                        self.var_tts_mode.set(bk.tts_mode_label_for_key("siliconflow"))
                        self.var_sf_voice.set(res)
                        self._sf_custom_voices = bk.list_siliconflow_custom_voices(self.cfg)
                        self._refresh_active_voice_selector()
                        self.save_all_configs()
                        win.destroy()
                        messagebox.showinfo("克隆成功", f"已上传并启用云端音色：\n{res}")
                    else:
                        messagebox.showwarning("克隆失败", res)
                self.after(0, _done)
            threading.Thread(target=_worker, daemon=True).start()

        ttk.Button(frm, text="上传并启用克隆音色", style="Primary.TButton", command=_up).grid(row=3, column=0, columnspan=3, pady=12)

    def on_open_health_dialog(self) -> None:
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "🩺 系统健康诊断与维护工具箱", "760x520")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 8))
        lbl_ov = ttk.Label(top, text="正在获取实时诊断信息...", font=("Microsoft YaHei UI", 10, "bold"))
        lbl_ov.pack(side="left")

        txt = scrolledtext.ScrolledText(frm, height=18, font=("Consolas", 9))
        self._style_scrolled_text(txt)
        txt.pack(fill="both", expand=True, pady=(0, 8))

        def _render_health() -> None:
            hl = bk.fetch_hub_health()
            txt.delete("1.0", "end")
            if not hl:
                lbl_ov.config(text="当前中控 (:28910) 未启动 —— 可运行下方「环境自检」或启动直播工作流。")
                lt_ok = bk.is_port_open("127.0.0.1", 8020, 0.2)
                obs_ok = bk.is_port_open("127.0.0.1", 4455, 0.2)
                assets = bk.list_ultralight_assets()
                txt.insert("1.0", (
                    f"[离线检查]\n"
                    f"- Ultralight 引擎 (:8020) : {'运行中' if lt_ok else '未启动'}\n"
                    f"- 中控网关 (:28910)       : 未启动\n"
                    f"- OBS WebSocket (:4455)   : {'已连接' if obs_ok else '未连接'}\n"
                    f"- 已就绪 Ultralight 形象  : {sum(1 for a in assets if a['ready'])} 个 / 共 {len(assets)} 条\n"
                ))
                return
            lbl_ov.config(text=f"总体状态: {hl.get('overall')} · {hl.get('summary')}  (检查于 {hl.get('checked_at')})")
            for m in hl.get("modules", []):
                txt.insert("end", f"[{m.get('level', '').upper():5s}] {m.get('name', m.get('id'))} | {m.get('metric', '')}\n")
                txt.insert("end", f"         说明: {m.get('message', '')}\n")
                if m.get("fix"):
                    txt.insert("end", f"         建议: {m.get('fix')}\n")
                txt.insert("end", "\n")

        def _run_tool(rel: str, args: list[str]) -> None:
            txt.delete("1.0", "end")
            lbl_ov.config(text=f"正在执行 {rel} ...")
            def _log_line(line: str) -> None:
                def _ins() -> None:
                    if win.winfo_exists():
                        txt.insert("end", line + "\n")
                        txt.see("end")
                self.after(0, _ins)
            def _worker() -> None:
                ok, _ = bk.run_tool_script_cli(rel, args, log_cb=_log_line)
                self.after(0, lambda: lbl_ov.config(text=f"执行完成 ({'成功' if ok else '有报错'})") if win.winfo_exists() else None)
            threading.Thread(target=_worker, daemon=True).start()

        def _unlock() -> None:
            if not bk.is_port_open("127.0.0.1", 28910, 0.2):
                messagebox.showwarning("提示", "中控 (:28910) 未启动", parent=win)
                return
            code, res = bk.http_json("http://127.0.0.1:28910/api/health/unlock", "POST", timeout=4.0)
            self.log(f"强制解锁结果: {code} {res}")
            _render_health()

        bar = ttk.Frame(frm)
        bar.pack(fill="x")
        ttk.Button(bar, text="🔄 立即复检", command=_render_health).pack(side="left", padx=2)
        ttk.Button(bar, text="🔓 强制解锁卡死状态", command=_unlock).pack(side="left", padx=2)
        ttk.Button(bar, text="⚙ 运行环境与测试自检", command=lambda: _run_tool("scripts/check_env.py", [])).pack(side="left", padx=2)
        ttk.Button(bar, text="📥 下载/校验基础模型", command=lambda: _run_tool("scripts/download_models.py", ["--mode", "all"])).pack(side="left", padx=2)
        ttk.Button(bar, text="🔑 测试 API Key", command=lambda: _run_tool("scripts/test_keys.py", [])).pack(side="left", padx=2)
        ttk.Button(bar, text="🌐 打开 WebUI", command=lambda: webbrowser.open("http://127.0.0.1:28910/")).pack(side="right", padx=2)
        _render_health()

    def _on_notebook_tab_changed(self, _event=None) -> None:
        try:
            if self.nb.select() == str(self.tab_train):
                self._refresh_avatar_table()
        except Exception:
            pass

    def _refresh_avatar_table(self, select_id: str = "") -> None:
        if not hasattr(self, "tv_avatars"):
            return
        prev_sel = select_id or self._selected_table_avatar() or self._selected_avatar_id()
        for row_id in self.tv_avatars.get_children():
            self.tv_avatars.delete(row_id)
        rows = bk.list_ultralight_assets()
        for a in rows:
            models_lbl = "+".join(x for x, ok in (("ONNX", a["has_onnx"]), ("PTH", a["has_pth"])) if ok) or "无权重"
            res_str = f"{a['frames']}帧" + (f" ({a['width']}×{a['height']})" if a["width"] else "")
            if a.get("base_avatar") and a["ready"]:
                st_lbl = f"👗 换装←{a['base_avatar'][:10]}"
            else:
                st_lbl = "✅ 已就绪" if a["ready"] else "⏳ 待训练"
            self.tv_avatars.insert(
                "",
                "end",
                iid=a["id"],
                values=(a["id"], a["name"], f"{a['asr']} ({a['fps']}fps)", res_str, models_lbl, st_lbl, f"{a['action_count']} 个"),
            )
        target_sel = prev_sel if self.tv_avatars.exists(prev_sel) else (rows[0]["id"] if rows else "")
        if target_sel and self.tv_avatars.exists(target_sel):
            self.tv_avatars.selection_set(target_sel)
            self.tv_avatars.see(target_sel)

    def _selected_table_avatar(self) -> str:
        sel = self.tv_avatars.selection() if hasattr(self, "tv_avatars") else ()
        return sel[0] if sel else ""

    def on_train_set_active_host(self) -> None:
        aid = self._selected_table_avatar()
        if not aid:
            messagebox.showwarning("提示", "请先在列表中选中一个主播")
            return
        assets = {x["id"]: x for x in bk.list_ultralight_assets()}
        info = assets.get(aid, {})
        self.hosts = bk.list_hosts()
        self.cb_avatar["values"] = [f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts]
        matched = next((f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts if h["id"] == aid), aid)
        self.var_avatar.set(matched)
        if info.get("asr") in ("hubert", "wenet") and hasattr(self, "var_ul_asr"):
            self.var_ul_asr.set(info["asr"])
        self._on_host_selected()
        self.save_all_configs()
        self.log(f"已将 [{aid}] 设为当前默认开播主播 ({info.get('asr', 'hubert')})")
        messagebox.showinfo("已设为默认主播", f"已将 [{aid}] 设为当前开播主播并同步保存！")

    def on_train_hot_switch_host(self) -> None:
        aid = self._selected_table_avatar()
        if not aid:
            messagebox.showwarning("提示", "请先在列表中选中一个已就绪的主播")
            return
        if not bk.is_port_open("127.0.0.1", 28910, 0.2):
            messagebox.showwarning("提示", "热切换需要先启动中控 (:28910) 与 Ultralight 引擎 (:8020)")
            return
        threading.Thread(
            target=lambda: bk.http_json("http://127.0.0.1:28910/api/avatar/switch", "POST", {"avatar_id": aid, "force": True}, timeout=8.0),
            daemon=True,
        ).start()
        self.log(f"⚡ 已发送直播中热切换主播请求: {aid}")

    def on_train_preview_host(self) -> None:
        aid = self._selected_table_avatar()
        if not aid:
            messagebox.showwarning("提示", "请先在列表中选中一个主播")
            return
        av_dir = bk.get_avatars_dir() / aid
        if av_dir.is_dir():
            os.startfile(str(av_dir))
        else:
            messagebox.showwarning("提示", f"尚无目录: {av_dir}")

    def on_train_import_dir(self) -> None:
        d = filedialog.askdirectory(title="选择包含 full_body_img / landmarks / unet.onnx 的完整形象文件夹")
        if not d:
            return
        src = Path(d)
        win = tk.Toplevel(self)
        self._theme_toplevel(win, "导入完整 Ultralight 形象目录", "420x190")
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        vid = tk.StringVar(value=re.sub(r"[^a-zA-Z0-9_\-]", "", src.name) or "imported_host")
        vname = tk.StringVar(value=src.name)
        vasr = tk.StringVar(value="hubert")
        ttk.Label(frm, text="英文 ID：").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=vid, width=28).grid(row=0, column=1, sticky="ew")
        ttk.Label(frm, text="中文名称：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=vname, width=28).grid(row=1, column=1, sticky="ew")
        ttk.Label(frm, text="ASR 路线：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(frm, textvariable=vasr, values=["hubert", "wenet"], state="readonly", width=12).grid(row=2, column=1, sticky="w")
        frm.columnconfigure(1, weight=1)

        def _ok() -> None:
            ok, msg = bk.import_external_avatar_dir(src, vid.get(), vname.get(), vasr.get())
            win.destroy()
            self.reload_all_data()
            if ok:
                messagebox.showinfo("导入成功", msg)
            else:
                messagebox.showwarning("导入失败", msg)

        ttk.Button(frm, text="确定导入", style="Primary.TButton", command=_ok).grid(row=3, column=0, columnspan=2, pady=10)

    def on_train_delete_host(self) -> None:
        aid = self._selected_table_avatar()
        if not aid:
            messagebox.showwarning("提示", "请先在列表中选中一个要删除的主播")
            return
        if not messagebox.askyesno("确认删除", f"确定删除形象 [{aid}] 的本地模型与权重文件吗？此操作不可撤销。"):
            return
        ok, msg = bk.delete_ultralight_avatar(aid)
        self.reload_all_data()
        self.log(msg)
        if ok:
            messagebox.showinfo("已删除", msg)
        else:
            messagebox.showwarning("删除失败", msg)

    def on_train_export_onnx(self) -> None:
        aid = self._selected_table_avatar()
        if not aid:
            messagebox.showwarning("提示", "请先选中包含 ultralight.pth 的主播")
            return
        assets = {x["id"]: x for x in bk.list_ultralight_assets()}
        info = assets.get(aid, {})
        if not info.get("has_pth"):
            messagebox.showwarning("提示", f"[{aid}] 缺少 ultralight.pth，无法导出 ONNX")
            return
        self._launch_train_worker(
            avatar_id=aid,
            name=info.get("name", aid),
            asr=info.get("asr", "hubert"),
            export_only=True,
        )

    def on_train_clone_outfit(self) -> None:
        """为已有并训好的主播快速更换一套新服饰造型（默认零重训复用 UNet 口型模型，10秒出新造型）。"""
        assets = {x["id"]: x for x in bk.list_ultralight_assets()}
        ready_hosts = [a for a in assets.values() if a.get("ready")]
        if not ready_hosts:
            messagebox.showwarning("提示", "请先训练并生成一个基础主播形象，之后即可随时给该主播无限添加新服饰造型！")
            return
        sel = self._selected_table_avatar()
        base_info = assets.get(sel) if (sel in assets and assets[sel].get("ready")) else ready_hosts[0]
        base_aid = base_info["id"]

        win = tk.Toplevel(self)
        self._theme_toplevel(win, f"👗 为主播 [{base_info.get('name', base_aid)}] 换穿新服饰", "580x320")
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)

        v_base = tk.StringVar(value=base_aid)
        v_vid = tk.StringVar()
        v_name = tk.StringVar()
        v_id = tk.StringVar()
        v_kw = tk.StringVar()
        v_tune = tk.BooleanVar(value=False)

        ttk.Label(
            frm,
            text="说明：Ultralight 模型只驱动嘴型，同一主播换新衣服拍 10~30 秒视频即可零重训秒出新造型（支持静音换装视频）。",
            wraplength=540,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="复用已训主播：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(frm, textvariable=v_base, values=[a["id"] for a in ready_hosts], state="readonly", width=26).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(frm, text="新服饰视频 (.mp4)：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_vid, width=42).grid(row=2, column=1, sticky="ew", padx=6)

        def _pick_v() -> None:
            f = filedialog.askopenfilename(title="选择穿着新衣服的真人视频（静音或说话均可）", filetypes=[("Video", "*.mp4 *.mov *.mkv")])
            if not f:
                return
            v_vid.set(f)
            if not v_name.get().strip():
                stem = Path(f).stem[:14]
                v_name.set(f"{base_info.get('name', base_aid)}·{stem}")
            if not v_id.get().strip():
                suf = re.sub(r"[^a-zA-Z0-9_]", "", bk.suggest_english_id(Path(f).stem)) or "outfit"
                v_id.set(f"{v_base.get()}_{suf}"[:28])

        ttk.Button(frm, text="选视频", command=_pick_v).grid(row=2, column=2)

        ttk.Label(frm, text="造型中文名：").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_name, width=32).grid(row=3, column=1, sticky="ew", padx=6)

        ttk.Label(frm, text="造型英文 ID：").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_id, width=32).grid(row=4, column=1, sticky="ew", padx=6)

        ttk.Label(frm, text="弹幕点换装关键词：").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=v_kw, width=32).grid(row=5, column=1, sticky="ew", padx=6)
        ttk.Label(frm, text="(可选,逗号隔开)").grid(row=5, column=2, sticky="w")

        ttk.Checkbutton(frm, text="新衣服光线/色调差异较大时追加 15 轮快速微调（需新视频含清晰说话音轨）", variable=v_tune).grid(row=6, column=0, columnspan=3, sticky="w", pady=8)
        frm.columnconfigure(1, weight=1)

        def _go() -> None:
            vp = v_vid.get().strip()
            ba = v_base.get().strip()
            nid = re.sub(r"[^a-zA-Z0-9_\-]", "", v_id.get().strip())
            nname = v_name.get().strip() or nid
            if not vp or not nid or nid == ba:
                messagebox.showwarning("提示", "请选择新服饰视频并填写不同于原主播的造型英文 ID", parent=win)
                return
            ep = 15 if v_tune.get() else 0
            if ep > 0:
                pinfo = bk.probe_video_info(vp)
                if pinfo.get("ok") and not pinfo.get("has_audio"):
                    messagebox.showwarning("提示", "该视频无音轨，无法做 15 轮微调；请取消勾选，直接零重训极速换装！", parent=win)
                    return
            kws = [k.strip() for k in v_kw.get().replace("，", ",").split(",") if k.strip()]
            b_asr = assets.get(ba, {}).get("asr", "hubert")
            win.destroy()
            self._launch_train_worker(
                avatar_id=nid,
                name=nname,
                asr=b_asr,
                video_path=vp,
                epochs=ep,
                batch_size=8,
                lr=0.0005 if ep > 0 else 0.001,
                base_avatar=ba,
                keywords=kws,
            )

        ttk.Button(frm, text="🚀 立即生成新服饰造型", style="Primary.TButton", command=_go).grid(row=7, column=0, columnspan=3, pady=6)

    def _sync_tr_aid_from_name(self) -> None:
        cn = self.var_tr_name.get().strip()
        if cn:
            self.var_tr_aid.set(bk.suggest_english_id(cn))

    def _toggle_tr_advanced(self) -> None:
        self._tr_adv_visible = not getattr(self, "_tr_adv_visible", False)
        if self._tr_adv_visible:
            # 插在操作行前方
            self.frm_tr_adv.pack(fill="x", pady=(0, 6), before=self.btn_tr_adv_toggle.master)
            self.btn_tr_adv_toggle.config(text="▴ 收起高级参数")
        else:
            self.frm_tr_adv.pack_forget()
            self.btn_tr_adv_toggle.config(text="⚙ 高级参数 (AI已自动配好，点此查看)")

    def _pick_tr_ckpt(self) -> None:
        f = filedialog.askopenfilename(title="选择已有 .pth 或 .onnx 模型权重", filetypes=[("Model", "*.pth *.onnx")])
        if not f:
            return
        self.var_tr_ckpt.set(f)
        self.on_train_ai_recommend()

    def _apply_tr_plan(self, plan: Dict[str, Any]) -> None:
        self.var_tr_mode.set(plan["mode"])
        self.var_tr_asr.set(plan["asr"])
        self.var_tr_epochs.set(int(plan["epochs"]))
        self.var_tr_batch.set(int(plan["batch_size"]))
        self.var_tr_lr.set(str(plan["lr"]))
        self.var_tr_set_default.set(bool(plan.get("set_default", True)))
        ep_txt = "跳过训练直接封装" if int(plan["epochs"]) == 0 else f"训练 {plan['epochs']} 轮 · Batch={plan['batch_size']}"
        asr_txt = "HuBERT 25fps 高清口型" if plan["asr"] == "hubert" else "WeNet 20fps 轻量省显存"
        self.lbl_tr_ai_title.config(
            text=f"🤖 {plan.get('summary_title', '推荐方案')}（来源：{plan.get('source', '智能规则')} | {plan['mode']} · {asr_txt} · {ep_txt}）"
        )
        self.lbl_tr_ai_plan.config(text=f"AI 解读：{plan.get('reason', '')}")
        warn = str(plan.get("warnings") or "").strip()
        if warn:
            self.lbl_tr_ai_warn.config(text=f"⚠ 注意：{warn}")
            if not self.lbl_tr_ai_warn.winfo_ismapped():
                self.lbl_tr_ai_warn.pack(fill="x", pady=(2, 0))
        else:
            self.lbl_tr_ai_warn.pack_forget()

    def on_train_quick_req(self, req_text: str) -> None:
        self.var_tr_req.set(req_text)
        if ("外部权重" in req_text or ".pth" in req_text) and not self.var_tr_ckpt.get().strip():
            f = filedialog.askopenfilename(title="选择要封装的 .pth 或 .onnx 权重文件", filetypes=[("Model", "*.pth *.onnx")])
            if f:
                self.var_tr_ckpt.set(f)
        self.on_train_ai_recommend()

    def on_train_ai_recommend(self) -> None:
        req_txt = self.var_tr_req.get().strip()
        vp = self.var_tr_video.get().strip()
        if vp and not getattr(self, "_last_vid_probe", None):
            self._last_vid_probe = bk.probe_video_info(vp)
        vinfo = dict(getattr(self, "_last_vid_probe", {}) or {"ok": False})
        has_ckpt = bool(self.var_tr_ckpt.get().strip())
        self.btn_tr_ai.config(state="disabled", text="🤖 AI 分析中...")

        def _worker() -> None:
            plan = bk.ai_recommend_ultralight_config(req_txt, vinfo, has_ckpt, self.cfg)
            def _done() -> None:
                self.btn_tr_ai.config(state="normal", text="🤖 AI 分析配置")
                self._apply_tr_plan(plan)
                self.log(f"[AI智能配参] {plan.get('summary_title')} -> mode={plan['mode']} asr={plan['asr']} epochs={plan['epochs']} batch={plan['batch_size']}")
            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def on_train_pick_video(self) -> None:
        f = filedialog.askopenfilename(title="选择真人正面参考视频", filetypes=[("Video", "*.mp4 *.mov *.mkv")])
        if not f:
            return
        self.var_tr_video.set(f)
        stem = Path(f).stem
        if not self.var_tr_name.get().strip():
            self.var_tr_name.set(stem[:16])
        if not self.var_tr_aid.get().strip():
            self.var_tr_aid.set(bk.suggest_english_id(stem))
        info = bk.probe_video_info(f)
        self._last_vid_probe = info
        if not info.get("ok"):
            self.lbl_tr_probe.config(text=f"⚠ 视频读取失败：{info.get('error')}")
            return
        aud_str = "✅ 含人声音轨（可直接本地训练）" if info["has_audio"] else "❌ 无音轨（纯静音底片，不能从头训练；只能搭配外部 .pth/.onnx 封装）"
        self.lbl_tr_probe.config(
            text=f"视频体检：{info['width']}×{info['height']} · {info['fps']}fps · {info['frames']}帧 ({info['duration_sec']}s) · {aud_str}"
        )
        # 选好视频后立即按知识库规则给一版基线推荐，若用户点了 AI 分析配置则进一步调用大模型细化
        base_plan = bk.rule_recommend_ultralight_config(self.var_tr_req.get(), info, bool(self.var_tr_ckpt.get().strip()))
        self._apply_tr_plan(base_plan)

    def _append_train_log(self, msg: str) -> None:
        def _do() -> None:
            self.txt_train_log.insert("end", msg + "\n")
            self.txt_train_log.see("end")
        self.after(0, _do)

    def on_train_start(self) -> None:
        vp = self.var_tr_video.get().strip()
        if not vp:
            messagebox.showwarning("提示", "请先点「📁 选择参考视频」选择要生成形象的真人视频")
            return
        if not self.var_tr_aid.get().strip():
            stem = Path(vp).stem
            if not self.var_tr_name.get().strip():
                self.var_tr_name.set(stem[:16])
            self.var_tr_aid.set(bk.suggest_english_id(self.var_tr_name.get() or stem))

        aid = re.sub(r"[^a-zA-Z0-9_\-]", "", self.var_tr_aid.get().strip()) or "ul_host"
        aname = self.var_tr_name.get().strip() or aid
        asr = self.var_tr_asr.get() or "hubert"
        mode = self.var_tr_mode.get()
        ckpt = self.var_tr_ckpt.get().strip()

        if "封装" in mode or "断点" in mode:
            if not ckpt:
                f = filedialog.askopenfilename(title="请选择 .pth 或 .onnx 权重文件", filetypes=[("Model", "*.pth *.onnx")])
                if not f:
                    return
                self.var_tr_ckpt.set(f)
                ckpt = f
        else:
            info = bk.probe_video_info(vp)
            if info.get("ok") and not info.get("has_audio"):
                messagebox.showerror("无法从头训练", "检测到所选参考视频没有音轨！\nUltralight 本地训练需要真人开口说话的带声视频。\n如果你有训好的 .pth/.onnx 模型，请点击「📦 已有权重直封」。")
                return

        live_n = bk.engine_live_sessions()
        if live_n > 0 and not messagebox.askyesno(
            "正在直播，确定要训练吗？",
            f"数字人引擎当前有 {live_n} 路直播画面在线。\n训练会占满 RTX 2080 显卡，可能让直播爆显存、画面卡死或断播。\n\n"
            "建议下播后再训练。确定已经下播、现在就要训练吗？",
        ):
            return

        self._launch_train_worker(
            avatar_id=aid,
            name=aname,
            asr=asr,
            video_path=vp,
            idle_video=self.var_tr_idle.get().strip(),
            checkpoint=ckpt if "封装" in mode else "",
            resume=ckpt if "断点" in mode else "",
            epochs=int(self.var_tr_epochs.get() or 100),
            batch_size=int(self.var_tr_batch.get() or 16),
            lr=float(self.var_tr_lr.get() or 0.001),
            export_only=False,
        )

    def _launch_train_worker(
        self,
        avatar_id: str,
        name: str,
        asr: str,
        video_path: str = "",
        idle_video: str = "",
        checkpoint: str = "",
        resume: str = "",
        epochs: int = 100,
        batch_size: int = 16,
        lr: float = 0.001,
        export_only: bool = False,
        base_avatar: str = "",
        keywords: List[str] | None = None,
    ) -> None:
        if getattr(self, "_train_running", False):
            messagebox.showwarning("提示", "当前已有训练/导出任务正在运行，请等待完成或先点击终止")
            return
        self._train_running = True
        self.btn_tr_start.config(state="disabled")
        self.btn_tr_stop.config(state="normal")
        self.var_tr_pct.set(2.0)
        self.lbl_tr_stage.config(text=f"正在启动 [{avatar_id}] ...")
        self.txt_train_log.delete("1.0", "end")

        def _worker() -> None:
            proc = bk.spawn_ultralight_pipeline_proc(
                avatar_id=avatar_id, name=name, asr=asr, video_path=video_path,
                idle_video=idle_video, checkpoint=checkpoint, resume=resume,
                epochs=epochs, batch_size=batch_size, lr=lr, export_only=export_only,
                base_avatar=base_avatar,
            )
            self._train_proc = proc
            last_lines: List[str] = []
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                ln = raw_line.rstrip("\r\n")
                last_lines.append(ln)
                if len(last_lines) > 40:
                    last_lines.pop(0)
                self._append_train_log(ln)
                m = re.match(r"\[PROGRESS\]\s*(\d+)%\s*\|\s*(.+)", ln)
                if m:
                    pct = float(m.group(1))
                    stg = m.group(2).strip()[:36]
                    self.after(0, lambda p=pct, s=stg: (self.var_tr_pct.set(p), self.lbl_tr_stage.config(text=f"{int(p)}% {s}")))
            rc = proc.wait()

            def _done() -> None:
                self._train_running = False
                self._train_proc = None
                self.btn_tr_start.config(state="normal")
                self.btn_tr_stop.config(state="disabled")
                if rc == 0:
                    self.var_tr_pct.set(100.0)
                    self.lbl_tr_stage.config(text="100% 完成！")
                    bk.register_host_profile(
                        avatar_id=avatar_id,
                        display_name=name or avatar_id,
                        persona_name=name or avatar_id,
                        tts_voice="" if base_avatar else (bk.voice_id_from_label(self.var_edge_voice.get()) or "zh-CN-XiaoxiaoNeural"),
                        style="" if base_avatar else self.var_live_style.get().strip(),
                        system_prompt="" if base_avatar else self.txt_prompt.get("1.0", "end").strip(),
                        source_video=video_path,
                        base_avatar=base_avatar,
                        keywords=keywords,
                    )
                    self.reload_all_data()
                    self._refresh_avatar_table(select_id=avatar_id)
                    if self.var_tr_set_default.get():
                        matched = next((f"{h['id']} ({h.get('name', h['id'])})" for h in self.hosts if h["id"] == avatar_id), avatar_id)
                        self.var_avatar.set(matched)
                        self._on_host_selected()
                        if hasattr(self, "var_ul_asr"):
                            self.var_ul_asr.set(asr)
                        self.save_all_configs()
                    self.log(f"✅ Ultralight 形象 [{avatar_id}] 构建完成并已入库！")
                    messagebox.showinfo("构建完成", f"Ultralight 形象 [{avatar_id}] ({asr}) 已就绪并已注册到主播列表！")
                else:
                    self.lbl_tr_stage.config(text=f"已中止 / 失败 (rc={rc})")
                    err_tail = "\n".join(last_lines[-8:])
                    messagebox.showwarning("构建未完成", f"任务退出码 {rc}：\n{err_tail}")

            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def on_train_stop(self) -> None:
        if self._train_proc:
            bk.stop_process_tree(self._train_proc)
            self._append_train_log("[用户控制] 已向训练子进程发送强制终止信号。")


def main() -> None:
    enable_dpi_awareness()
    app = LauncherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
