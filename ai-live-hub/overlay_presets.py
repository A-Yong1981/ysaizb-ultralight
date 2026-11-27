# -*- coding: utf-8 -*-
"""
overlay_presets.py —— 直播舞台叠层（右上角知识库章节字幕 + 下部口播字幕 + 免费商用字体与效果预设）
唯一真源：被 ai-live-hub/server.py 与 launcher/backend.py 共同引用。
所有内置/引用字体均为 SIL Open Font License 1.1（永久免费可商用、无需额外商业授权）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OVERLAY_FONT_PRESETS: List[Dict[str, str]] = [
    {
        "id": "noto_sans_sc",
        "label": "思源黑体 Noto Sans SC（SIL OFL 1.1 免费商用·推荐）",
        "css_family": '"Noto Sans SC", "Source Han Sans SC", "LXGW WenKai", sans-serif',
        "license": "SIL OFL 1.1（永久免费商用）",
        "local_file": r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
        # 02_download_models.bat 会把思源黑体下载到 static/fonts；没下载时退回随包内置的霞鹜文楷，绝不落到微软雅黑等商用字体
        "bundle_file": "NotoSansSC-VF.ttf",
        "fallback_bundle": "LXGWWenKaiLite-Regular.ttf",
    },
    {
        "id": "noto_serif_sc",
        "label": "思源宋体 Noto Serif SC（SIL OFL 1.1 免费商用·说书国学）",
        "css_family": '"Noto Serif SC", "Source Han Serif SC", "LXGW WenKai", serif',
        "license": "SIL OFL 1.1（永久免费商用）",
        "local_file": r"C:\Windows\Fonts\NotoSerifSC-VF.ttf",
        "fallback_bundle": "LXGWWenKaiLite-Regular.ttf",
    },
    {
        "id": "source_han_serif_heavy",
        "label": "思源宋特粗 Source Han Heavy（SIL OFL 1.1 免费商用·醒目大字）",
        "css_family": '"Source Han Serif SC Heavy", "Noto Serif SC", "LXGW WenKai", serif',
        "license": "SIL OFL 1.1（永久免费商用）",
        "local_file": r"C:\Windows\Fonts\Source Han Serif SC Heavy (TrueType).ttf",
        "fallback_bundle": "LXGWWenKaiLite-Regular.ttf",
    },
    {
        "id": "smiley_sans",
        "label": "得意黑 Smiley Sans（SIL OFL 1.1 免费商用·潮酷标题）",
        "css_family": '"Smiley Sans", "Noto Sans SC", sans-serif',
        "license": "SIL OFL 1.1（永久免费商用·随包离线内置）",
        "bundle_file": "SmileySans-Oblique.ttf",
    },
    {
        "id": "lxgw_wenkai",
        "label": "霞鹜文楷 LXGW WenKai（SIL OFL 1.1 免费商用·温润楷书）",
        "css_family": '"LXGW WenKai", "Noto Serif SC", serif',
        "license": "SIL OFL 1.1（永久免费商用·随包离线内置）",
        "bundle_file": "LXGWWenKaiLite-Regular.ttf",
    },
]

OVERLAY_FONT_BY_ID: Dict[str, Dict[str, str]] = {f["id"]: f for f in OVERLAY_FONT_PRESETS}

OVERLAY_EFFECT_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "classic_dark",
        "label": "经典黑透磨砂底（默认稳重）",
        "text_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 2,
        "bg_opacity": 78,
        "font_weight": 600,
    },
    {
        "id": "stroke_outline",
        "label": "透明背景 · 高清黑描边（无黑底框）",
        "text_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 4,
        "bg_opacity": 0,
        "font_weight": 800,
    },
    {
        "id": "gold_story",
        "label": "暖金评书描边（讲故事/国学推荐）",
        "text_color": "#FDE047",
        "stroke_color": "#451A03",
        "stroke_width": 3,
        "bg_opacity": 32,
        "font_weight": 700,
    },
    {
        "id": "neon_glow",
        "label": "青蓝霓虹流光（科技/硬件/游戏）",
        "text_color": "#E0F2FE",
        "stroke_color": "#0369A1",
        "stroke_width": 2,
        "bg_opacity": 72,
        "font_weight": 700,
    },
    {
        "id": "warm_card",
        "label": "暖白柔光卡片（浅底深字护眼）",
        "text_color": "#0F172A",
        "stroke_color": "#FFFFFF",
        "stroke_width": 0,
        "bg_opacity": 88,
        "font_weight": 700,
    },
    {
        "id": "none_plain",
        "label": "极简纯净透明字（轻投影无底）",
        "text_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 1,
        "bg_opacity": 0,
        "font_weight": 600,
    },
]

OVERLAY_EFFECT_BY_ID: Dict[str, Dict[str, Any]] = {e["id"]: e for e in OVERLAY_EFFECT_PRESETS}

OVERLAY_ANIMATION_OPTIONS: List[Tuple[str, str]] = [
    ("fade_up", "平滑淡入上浮"),
    ("pop_in", "轻弹放大强调"),
    ("none", "静态稳重无动效"),
]

DEFAULT_OVERLAY_CFG: Dict[str, Any] = {
    "show_kb_board": True,
    "board_position": "top-right",
    "show_info_card": True,
    "show_badge": True,
    "show_subtitle": True,
    "show_question_pill": True,
    "font_preset": "noto_sans_sc",
    "effect_preset": "classic_dark",
    "font_size": 24,
    "board_font_size": 14,
    "font_weight": 600,
    "text_color": "#FFFFFF",
    "stroke_color": "#000000",
    "stroke_width": 2,
    "bg_opacity": 78,
    "bottom_offset_pct": 4,
    "animation": "fade_up",
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def resolve_font_path(font_id: str, fonts_dir: Optional[Path] = None) -> Optional[Path]:
    """定位某款免费字体在硬盘上的真实 .ttf/.otf 文件路径（供桌面端实时预览渲染）。"""
    spec = OVERLAY_FONT_BY_ID.get(font_id)
    if not spec:
        return None
    if spec.get("bundle_file") and fonts_dir:
        cand = fonts_dir / spec["bundle_file"]
        if cand.is_file():
            return cand
    if spec.get("local_file"):
        p = Path(spec["local_file"])
        if p.is_file():
            return p
    # 本机没装该字体：退回随包内置的同协议免费字体（预览与直播画面都不会落到商用字体）
    if spec.get("fallback_bundle") and fonts_dir:
        cand = fonts_dir / spec["fallback_bundle"]
        if cand.is_file():
            return cand
    return None


def _clamp_int(val: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(val)))))
    except Exception:
        return fallback


def _safe_hex(val: Any, fallback: str) -> str:
    s = str(val or "").strip()
    return s.upper() if _HEX_RE.match(s) else fallback


def sanitize_overlay_cfg(raw: Optional[Dict[str, Any]], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """合并并校验直播画面章节卡、口播字幕与免费字体特效配置，同时附带字体 CSS 元信息。"""
    merged = {**DEFAULT_OVERLAY_CFG, **(base or {}), **(raw or {})}

    bpos = str(merged.get("board_position") or "top-right").strip().lower()
    if bpos not in ("top-right", "top-left"):
        bpos = "top-right"

    fp = str(merged.get("font_preset") or "noto_sans_sc").strip()
    if fp not in OVERLAY_FONT_BY_ID:
        fp = "noto_sans_sc"
    finfo = OVERLAY_FONT_BY_ID[fp]

    ep = str(merged.get("effect_preset") or "classic_dark").strip()
    if ep not in OVERLAY_EFFECT_BY_ID:
        ep = "classic_dark"

    anim = str(merged.get("animation") or "fade_up").strip()
    if anim not in {k for k, _ in OVERLAY_ANIMATION_OPTIONS}:
        anim = "fade_up"

    return {
        "show_kb_board": bool(merged.get("show_kb_board", True)),
        "board_position": bpos,
        "show_info_card": bool(merged.get("show_info_card", True)),
        "show_badge": bool(merged.get("show_badge", True)),
        "show_subtitle": bool(merged.get("show_subtitle", True)),
        "show_question_pill": bool(merged.get("show_question_pill", True)),
        "font_preset": fp,
        "font_css": finfo["css_family"],
        "font_license": finfo["license"],
        "effect_preset": ep,
        "font_size": _clamp_int(merged.get("font_size"), 14, 48, 24),
        "board_font_size": _clamp_int(merged.get("board_font_size"), 11, 26, 14),
        "font_weight": _clamp_int(merged.get("font_weight"), 400, 900, 600),
        "text_color": _safe_hex(merged.get("text_color"), "#FFFFFF"),
        "stroke_color": _safe_hex(merged.get("stroke_color"), "#000000"),
        "stroke_width": _clamp_int(merged.get("stroke_width"), 0, 6, 2),
        "bg_opacity": _clamp_int(merged.get("bg_opacity"), 0, 95, 78),
        "bottom_offset_pct": _clamp_int(merged.get("bottom_offset_pct"), 1, 40, 4),
        "animation": anim,
    }
