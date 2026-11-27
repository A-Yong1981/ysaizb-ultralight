"""桌面管理面板后端逻辑：配置读写、OBS 多模式预设生成与拉起、工作流启停、预设方案管理。"""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import winreg
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def get_root_dir() -> Path:
    """定位 ysaizb 项目根目录（兼容源码运行与 PyInstaller 单文件 exe）。"""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "ai-live-hub").exists():
            return exe_dir
        if (exe_dir.parent / "ai-live-hub").exists():
            return exe_dir.parent
    here = Path(__file__).resolve().parent.parent
    if (here / "ai-live-hub").exists():
        return here
    return Path.cwd()


ROOT_DIR = get_root_dir()
HUB_DIR = ROOT_DIR / "ai-live-hub"
LOGS_DIR = ROOT_DIR / "logs"
CONFIG_PATH = HUB_DIR / "config.json"
FONTS_DIR = HUB_DIR / "static" / "fonts"

if str(HUB_DIR) not in sys.path:
    sys.path.insert(0, str(HUB_DIR))

from overlay_presets import (
    DEFAULT_OVERLAY_CFG,
    OVERLAY_ANIMATION_OPTIONS,
    OVERLAY_EFFECT_BY_ID,
    OVERLAY_EFFECT_PRESETS,
    OVERLAY_FONT_BY_ID,
    OVERLAY_FONT_PRESETS,
    resolve_font_path,
    sanitize_overlay_cfg,
)

BOARD_POSITION_OPTIONS: List[Tuple[str, str]] = [
    ("top-right", "右上角（默认推荐）"),
    ("top-left", "左上角（防遮挡左卡）"),
]
PROFILES_PATH = HUB_DIR / "platform_profiles.json"
PRESETS_PATH = HUB_DIR / "launcher_presets.json"
LAUNCHER_CFG_PATH = HUB_DIR / "launcher_settings.json"
ENV_BAT_PATH = ROOT_DIR / "env.bat"
KB_DIR = HUB_DIR / "kb_data"
KB_ROOT_DIR = ROOT_DIR / "知识库"
HOSTS_DIR = ROOT_DIR / "主播素材"
BG_DIR = HUB_DIR / "bg_media"
ACTION_DIR = HUB_DIR / "action_media"
OBS_PRESETS_DIR = ROOT_DIR / "OBS配置"
PLATFORM_CFG_DIR = ROOT_DIR / "platform_configs"
GLOBAL_CFG_DIR = ROOT_DIR / "全局配置"
KB_ALL_HOSTS_LABEL = "全部知识库（不限主播）"

# Verified free Microsoft Edge-TTS voice catalog with friendly Chinese display names
EDGE_VOICE_OPTIONS: List[Tuple[str, str]] = [
    ("zh-CN-YunjianNeural", "云健 · 稳重评书叙事男声 (zh-CN-YunjianNeural)"),
    ("zh-CN-YunxiNeural", "云希 · 温和青年男声 (zh-CN-YunxiNeural)"),
    ("zh-CN-YunyangNeural", "云扬 · 专业新闻播音男声 (zh-CN-YunyangNeural)"),
    ("zh-CN-YunxiaNeural", "云夏 · 阳光活力男声 (zh-CN-YunxiaNeural)"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 亲和甜美女声 (zh-CN-XiaoxiaoNeural)"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 元气活泼女声 (zh-CN-XiaoyiNeural)"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 东北幽默女声 (zh-CN-liaoning-XiaobeiNeural)"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 陕西特色女声 (zh-CN-shaanxi-XiaoniNeural)"),
    ("zh-HK-WanLungNeural", "云龙 · 粤语沉稳男声 (zh-HK-WanLungNeural)"),
    ("zh-HK-HiuMaanNeural", "晓曼 · 粤语亲切女声 (zh-HK-HiuMaanNeural)"),
    ("zh-HK-HiuGaaiNeural", "晓佳 · 粤语清新女声 (zh-HK-HiuGaaiNeural)"),
    ("zh-TW-YunJheNeural", "云哲 · 台湾温和男声 (zh-TW-YunJheNeural)"),
    ("zh-TW-HsiaoChenNeural", "晓臻 · 台湾知性女声 (zh-TW-HsiaoChenNeural)"),
    ("zh-TW-HsiaoYuNeural", "晓雨 · 台湾甜美女声 (zh-TW-HsiaoYuNeural)"),
    ("en-US-JennyNeural", "珍妮 · 美式亲和女声 (en-US-JennyNeural)"),
    ("en-US-AriaNeural", "艾丽娅 · 美式自然女声 (en-US-AriaNeural)"),
    ("en-US-AvaNeural", "艾娃 · 美式清晰女声 (en-US-AvaNeural)"),
    ("en-US-GuyNeural", "盖伊 · 美式沉稳男声 (en-US-GuyNeural)"),
    ("en-US-AndrewNeural", "安德鲁 · 美式温暖男声 (en-US-AndrewNeural)"),
    ("en-US-ChristopherNeural", "克里斯 · 美式播音男声 (en-US-ChristopherNeural)"),
]

_VOICE_LABEL_MAP: Dict[str, str] = {vid: lbl for vid, lbl in EDGE_VOICE_OPTIONS}
_VOICE_ID_BY_LABEL: Dict[str, str] = {lbl: vid for vid, lbl in EDGE_VOICE_OPTIONS}


def voice_label_for_id(voice_id: str) -> str:
    vid = str(voice_id or "").strip()
    if not vid:
        return _VOICE_LABEL_MAP.get("zh-CN-XiaoxiaoNeural", "zh-CN-XiaoxiaoNeural")
    if vid in _VOICE_ID_BY_LABEL:
        return vid
    return _VOICE_LABEL_MAP.get(vid, vid)


def voice_id_from_label(label_or_id: str) -> str:
    raw = str(label_or_id or "").strip()
    if not raw:
        return ""
    if raw in _VOICE_ID_BY_LABEL:
        return _VOICE_ID_BY_LABEL[raw]
    m = re.search(r"\(([a-z]{2}-[A-Z]{2}(?:-[a-z]+)?-[A-Za-z0-9]+Neural)\)\s*$", raw)
    if m:
        return m.group(1)
    return raw


# 硅基流动 CosyVoice2 官方内置声线与中文映射 + GPT-SoVITS / TTS 来源目录
TTS_MODE_OPTIONS: List[Tuple[str, str]] = [
    ("edgetts", "微软免费 · edgetts（0显存多声线）"),
    ("siliconflow", "硅基流动 CosyVoice2 · siliconflow（0显存高拟真/克隆）"),
    ("gptsovits", "本地 GPT-SoVITS · gptsovits（本地参考音频，占显存）"),
]
_TTS_MODE_LBL_BY_KEY = {k: v for k, v in TTS_MODE_OPTIONS}
_TTS_MODE_KEY_BY_LBL = {v: k for k, v in TTS_MODE_OPTIONS}


def tts_mode_label_for_key(mode: str) -> str:
    m = str(mode or "edgetts").strip().lower()
    return _TTS_MODE_LBL_BY_KEY.get(m, _TTS_MODE_LBL_BY_KEY["edgetts"])


def tts_mode_key_from_label(label_or_key: str) -> str:
    raw = str(label_or_key or "").strip()
    if raw in _TTS_MODE_KEY_BY_LBL:
        return _TTS_MODE_KEY_BY_LBL[raw]
    low = raw.lower()
    for k, _ in TTS_MODE_OPTIONS:
        if low == k or f"· {k}" in low or low.startswith(k):
            return k
    return "edgetts"


SF_VOICE_OPTIONS: List[Tuple[str, str]] = [
    ("FunAudioLLM/CosyVoice2-0.5B:benjamin", "班杰明 · 低沉磁性男声 (benjamin · 说书推荐)"),
    ("FunAudioLLM/CosyVoice2-0.5B:alex", "亚历克斯 · 沉稳大气男声 (alex)"),
    ("FunAudioLLM/CosyVoice2-0.5B:charles", "查尔斯 · 儒雅温和男声 (charles)"),
    ("FunAudioLLM/CosyVoice2-0.5B:david", "大卫 · 活力青年男声 (david)"),
    ("FunAudioLLM/CosyVoice2-0.5B:anna", "安娜 · 亲和温柔女声 (anna)"),
    ("FunAudioLLM/CosyVoice2-0.5B:bella", "贝拉 · 甜美清晰女声 (bella)"),
    ("FunAudioLLM/CosyVoice2-0.5B:claire", "克莱尔 · 优雅知性女声 (claire)"),
    ("FunAudioLLM/CosyVoice2-0.5B:diana", "戴安娜 · 元气活泼女声 (diana)"),
]
_SF_LABEL_BY_ID = {vid: lbl for vid, lbl in SF_VOICE_OPTIONS}
_SF_ID_BY_LABEL = {lbl: vid for vid, lbl in SF_VOICE_OPTIONS}


def list_siliconflow_custom_voices(cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    """尝试拉取用户已在硅基流动上传的自定克隆音色列表。"""
    sf = cfg.get("tts", {}).get("siliconflow", {})
    api_key = str(sf.get("api_key", "")).strip()
    if not api_key or api_key.startswith("填入"):
        return []
    base = str(sf.get("base_url", "https://api.siliconflow.cn/v1")).rstrip("/")
    req = urllib.request.Request(
        f"{base}/audio/voice/list",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            items = data.get("result") or data.get("data") or []
            out: List[Tuple[str, str]] = []
            for it in items:
                uri = str(it.get("uri") or "").strip()
                cname = str(it.get("customName") or uri).strip()
                if uri:
                    out.append((uri, f"☁ 云端克隆 · {cname} ({uri})"))
            return out
    except Exception:
        return []


def sf_voice_options_all(cfg: Dict[str, Any], custom: Optional[List[Tuple[str, str]]] = None) -> List[Tuple[str, str]]:
    extra = custom if custom is not None else []
    seen = {vid for vid, _ in extra}
    return list(extra) + [(vid, lbl) for vid, lbl in SF_VOICE_OPTIONS if vid not in seen]


def sf_voice_label_for_id(voice_uri: str, options: Optional[List[Tuple[str, str]]] = None) -> str:
    vid = str(voice_uri or "").strip()
    if not vid:
        return _SF_LABEL_BY_ID["FunAudioLLM/CosyVoice2-0.5B:benjamin"]
    for oid, olbl in (options or SF_VOICE_OPTIONS):
        if oid == vid or olbl == vid:
            return olbl
    return vid


def sf_voice_id_from_label(label_or_id: str, options: Optional[List[Tuple[str, str]]] = None) -> str:
    raw = str(label_or_id or "").strip()
    if not raw:
        return ""
    for oid, olbl in (options or SF_VOICE_OPTIONS):
        if raw == olbl or raw == oid:
            return oid
    if raw in _SF_ID_BY_LABEL:
        return _SF_ID_BY_LABEL[raw]
    m = re.search(r"\((speech:[^)]+)\)\s*$", raw)
    if m:
        return m.group(1)
    return raw


def suggest_sf_voice_for_host(avatar_id: str, edge_voice: str = "") -> str:
    aid = str(avatar_id or "").lower()
    ev = str(edge_voice or "").lower()
    if "shuoshu" in aid or any(m in ev for m in ("yunjian", "yunxi", "yunyang", "yunxia", "wanlung", "yunjhe", "andrew", "guy")):
        return "FunAudioLLM/CosyVoice2-0.5B:benjamin"
    if "youxi" in aid:
        return "FunAudioLLM/CosyVoice2-0.5B:diana"
    if "yingjian" in aid:
        return "FunAudioLLM/CosyVoice2-0.5B:claire"
    return "FunAudioLLM/CosyVoice2-0.5B:anna"


def list_gptsovits_ref_options() -> List[Tuple[str, str]]:
    vdir = ROOT_DIR / "voices"
    vdir.mkdir(parents=True, exist_ok=True)
    out: List[Tuple[str, str]] = []
    for p in sorted(vdir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".flac", ".m4a"):
            out.append((p.as_posix(), f"本地参考音频 · {p.name}"))
    if not out:
        fallback = (vdir / "host_ref.wav").as_posix()
        out.append((fallback, "示例路径 · voices/host_ref.wav（请放入 5~10 秒人声）"))
    return out


def gsv_voice_label_for_path(path_str: str, options: Optional[List[Tuple[str, str]]] = None) -> str:
    raw = str(path_str or "").strip().replace("\\", "/")
    opts = options or list_gptsovits_ref_options()
    for p, lbl in opts:
        if p.replace("\\", "/").lower() == raw.lower() or Path(p).name.lower() == Path(raw).name.lower():
            return lbl
    return raw or opts[0][1]


def gsv_voice_path_from_label(label_or_path: str, options: Optional[List[Tuple[str, str]]] = None) -> str:
    raw = str(label_or_path or "").strip()
    opts = options or list_gptsovits_ref_options()
    for p, lbl in opts:
        if raw == lbl or raw.replace("\\", "/").lower() == p.lower():
            return p
    return raw

OBS_PRESETS: List[Dict[str, Any]] = [
    {"key": "douyin", "orientation": "landscape", "label": "抖音 · 横屏 (1296×1080)", "cn_name": "抖音横屏_1296x1080", "profile": "Douyin_1296x1080", "scene": "抖音横屏", "width": 1296, "height": 1080},
    {"key": "douyin", "orientation": "portrait", "label": "抖音 · 竖屏 (720×1280)", "cn_name": "抖音竖屏_720x1280", "profile": "Douyin_720x1280", "scene": "抖音竖屏", "width": 720, "height": 1280},
    {"key": "bilibili", "orientation": "landscape", "label": "B站 · 横屏 (1920×1080)", "cn_name": "B站横屏_1920x1080", "profile": "Bilibili_1920x1080", "scene": "B站横屏", "width": 1920, "height": 1080},
    {"key": "bilibili", "orientation": "portrait", "label": "B站 · 竖屏 (720×1280)", "cn_name": "B站竖屏_720x1280", "profile": "Bilibili_720x1280", "scene": "B站竖屏", "width": 720, "height": 1280},
    {"key": "wechat", "orientation": "portrait", "label": "视频号 · 竖屏 (720×1280)", "cn_name": "视频号竖屏_720x1280", "profile": "WeChat_720x1280", "scene": "视频号竖屏", "width": 720, "height": 1280},
    {"key": "wechat", "orientation": "landscape", "label": "视频号 · 横屏 (1280×720)", "cn_name": "视频号横屏_1280x720", "profile": "WeChat_1280x720", "scene": "视频号横屏", "width": 1280, "height": 720},
    {"key": "youtube", "orientation": "landscape", "label": "YouTube · 横屏 (1280×720)", "cn_name": "YouTube横屏_1280x720", "profile": "YouTube_1280x720", "scene": "YouTube横屏", "width": 1280, "height": 720},
    {"key": "youtube", "orientation": "portrait", "label": "YouTube · 竖屏 (720×1280)", "cn_name": "YouTube竖屏_720x1280", "profile": "YouTube_720x1280", "scene": "YouTube竖屏", "width": 720, "height": 1280},
    {"key": "tiktok", "orientation": "portrait", "label": "TikTok · 竖屏 (720×1280)", "cn_name": "TikTok竖屏_720x1280", "profile": "TikTok_720x1280", "scene": "TikTok竖屏", "width": 720, "height": 1280},
    {"key": "tiktok", "orientation": "landscape", "label": "TikTok · 横屏 (1280×720)", "cn_name": "TikTok横屏_1280x720", "profile": "TikTok_1280x720", "scene": "TikTok横屏", "width": 1280, "height": 720},
]

TONE_PRESETS: List[str] = [
    "用温暖亲切、像跟家长和长辈聊家常一样的语气说",
    "用专业沉稳、抑扬顿挫、绘声绘色的说书先生语气说",
    "用元气活泼、节奏明快、有梗有趣的游戏女主播语气说",
    "用干练专业、条理清晰、通俗易懂的硬件评测女主播语气说",
    "用幽默风趣、接地气的生活化科普语气说",
    "用语速放慢、耐心温和、重点突出的防骗提醒语气说",
]

_CN_PINYIN_MAP: Dict[str, str] = {
    "说": "shuo", "书": "shu", "先": "xian", "生": "sheng", "游": "you", "戏": "xi",
    "女": "nv", "主": "zhu", "播": "bo", "电": "dian", "脑": "nao", "硬": "ying",
    "件": "jian", "全": "quan", "科": "ke", "技": "ji", "装": "zhuang", "默": "mo",
    "认": "ren", "绿": "lv", "幕": "mu", "讲": "jiang", "师": "shi", "老": "lao",
    "小": "xiao", "柔": "rou", "星": "xing", "辰": "chen", "明": "ming", "月": "yue",
    "大": "da", "白": "bai", "话": "hua", "家": "jia", "长": "zhang", "防": "fang",
    "诈": "zha", "普": "pu", "风": "feng", "趣": "qu", "数": "shu", "码": "ma",
    "评": "ping", "测": "ce", "解": "jie", "助": "zhu", "手": "shou", "智": "zhi",
    "能": "neng", "云": "yun", "海": "hai", "天": "tian", "文": "wen", "武": "wu",
    "阿": "a", "姨": "yi", "叔": "shu", "哥": "ge", "姐": "jie", "妹": "mei",
}


def suggest_english_id(cn_name: str) -> str:
    """根据中文主播名自动生成规范英文/拼音 ID，支持用户后续自定义。"""
    text = str(cn_name or "").strip()
    if not text:
        return ""
    tokens: List[str] = []
    ascii_buf = ""
    for ch in text:
        if re.match(r"[A-Za-z0-9_]", ch):
            ascii_buf += ch.lower()
        else:
            if ascii_buf:
                tokens.append(ascii_buf)
                ascii_buf = ""
            if ch in _CN_PINYIN_MAP:
                tokens.append(_CN_PINYIN_MAP[ch])
            elif "\u4e00" <= ch <= "\u9fff":
                tokens.append(f"c{ord(ch):x}"[:4])
    if ascii_buf:
        tokens.append(ascii_buf)
    slug = "_".join(t for t in tokens if t)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or f"host_{uuid.uuid4().hex[:6]}"


def _call_llm_chat(
    cfg: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 360,
    temperature: float = 0.7,
) -> Tuple[bool, str, str]:
    """调用 config.json 已配置并启用的 LLM 容灾池，返回 (ok, content_or_err, provider_name)。"""
    providers = [
        p for p in cfg.get("llm_providers", [])
        if p.get("enabled") and str(p.get("api_key", "")).strip() and not str(p.get("api_key", "")).strip().startswith("填入")
    ]
    if not providers:
        return False, "未配置可用 API Key", ""
    last_err = ""
    for p in providers:
        base_url = str(p.get("base_url", "")).rstrip("/")
        model = str(p.get("model", "")).strip()
        api_key = str(p.get("api_key", "")).strip()
        pname = str(p.get("name") or model or "LLM")
        if not base_url or not model:
            continue
        body: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if isinstance(p.get("extra_body"), dict):
            body.update(p["extra_body"])
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=float(p.get("timeout", 15))) as resp:
                res = json.loads(resp.read().decode("utf-8", errors="replace"))
                txt = ((res.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
                if txt:
                    return True, txt, pname
        except Exception as exc:
            last_err = f"{pname}: {exc}"
    return False, last_err or "所有节点均无返回", ""


def generate_persona_prompt(host_name: str, style: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """根据主播名和直播主题/风格调用已配置的 AI 模型生成系统提示词。"""
    user_q = (
        f"请为数字人主播「{host_name or '主播'}」编写一段直播间系统人设提示词（system_prompt）。\n"
        f"直播主题与风格：{style or '成年人实用知识科普'}\n"
        "要求：\n"
        "1. 必须包含占位符 {name} 代表主播称呼；\n"
        "2. 仅面向成年观众，绝不引导未成年人观看、充值或打赏；\n"
        "3. 规定每次回答控制在 35 到 65 个中文字以内，纯口语化，禁止输出 Markdown 符号、表情和括号动作；\n"
        "4. 直接输出提示词正文，不要加任何解释或标题。"
    )
    ok, res, _ = _call_llm_chat(
        cfg,
        "你是专业的直播间人设策划助手，只输出可直接使用的 system_prompt 正文。",
        user_q,
        max_tokens=360,
        temperature=0.7,
    )
    if not ok and "未配置可用 API Key" in res:
        return False, "请先在「1. AI 模型与声音」中启用并填写至少一个可用 API Key"
    return (True, res) if ok else (False, f"AI 生成失败：{res}")


def load_ultralight_config_kb() -> str:
    """加载 Ultralight 训练与显存参数配置知识库（兼容源码与 PyInstaller 打包 EXE）。"""
    cands = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        cands.append(Path(sys._MEIPASS) / "launcher" / "ultralight_config_kb.md")
    cands.append(Path(__file__).resolve().parent / "ultralight_config_kb.md")
    cands.append(ROOT_DIR / "launcher" / "ultralight_config_kb.md")
    for p in cands:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def _sanitize_ultralight_plan(raw: Dict[str, Any], video_info: Dict[str, Any], has_ckpt: bool) -> Dict[str, Any]:
    """对 AI 或规则输出做硬件安全红线钳位（RTX 2080 8GB + 防无音轨硬训）。"""
    valid_modes = ("从头本地训练 + 导出ONNX", "断点续训 (.pth)", "封装外部权重 (.pth/.onnx)")
    mode = str(raw.get("mode") or valid_modes[0]).strip()
    if mode not in valid_modes:
        if "封装" in mode:
            mode = valid_modes[2]
        elif "续" in mode or "resume" in mode.lower():
            mode = valid_modes[1]
        else:
            mode = valid_modes[0]

    has_audio = bool(video_info.get("has_audio", True)) if video_info.get("ok") else True
    frames = int(video_info.get("frames") or 0)
    dur = float(video_info.get("duration_sec") or 0.0)

    if mode != valid_modes[2] and not has_ckpt and video_info.get("ok") and not has_audio:
        # 强制防呆：无音轨视频绝不允许直接开本地训练
        warnings = str(raw.get("warnings") or "").strip()
        msg = "所选视频不含音轨（如静音待机片），不能从头训练，请换用带清晰人声说话的视频；若您有现成 .pth/.onnx 权重，请选「封装外部权重」。"
        if msg not in warnings:
            warnings = (warnings + "；" + msg).strip("；")
        raw["can_train"] = False
        raw["warnings"] = warnings
    else:
        raw["can_train"] = True

    if mode != valid_modes[0] and not has_ckpt and not raw.get("warnings"):
        raw["warnings"] = "该模式还需在高级选项中选择对应的 .pth 或 .onnx 权重文件。"

    asr = str(raw.get("asr") or "hubert").strip().lower()
    if asr not in ("hubert", "wenet"):
        asr = "hubert"

    if mode == valid_modes[2]:
        epochs = 0
    else:
        try:
            epochs = int(raw.get("epochs") or 100)
        except Exception:
            epochs = 100
        epochs = max(5, min(300, epochs))

    try:
        batch_size = int(raw.get("batch_size") or 16)
    except Exception:
        batch_size = 16
    max_safe_batch = 16
    if frames > 0:
        max_safe_batch = min(16, max(1, frames // 10 if frames < 160 else 16))
    batch_size = max(1, min(max_safe_batch, batch_size))

    try:
        lr_val = float(raw.get("lr") or 0.001)
        lr_val = max(1e-5, min(1e-2, lr_val))
        lr_str = f"{lr_val:g}"
    except Exception:
        lr_str = "0.001"

    warn_txt = str(raw.get("warnings") or "").strip()
    if mode == valid_modes[0] and video_info.get("ok") and has_audio and 0 < dur < 15.0 and epochs >= 60 and not warn_txt:
        warn_txt = f"当前视频仅 {dur:.1f} 秒，建议正式开播前换用 1~5 分钟不同说话口型视频以获得更自然效果。"

    return {
        "mode": mode,
        "asr": asr,
        "epochs": epochs,
        "batch_size": batch_size,
        "lr": lr_str,
        "set_default": bool(raw.get("set_default", True)),
        "summary_title": str(raw.get("summary_title") or "智能推荐方案"),
        "reason": str(raw.get("reason") or "已按您的需求和视频素材自动匹配最稳妥的显存与画质参数。"),
        "warnings": warn_txt,
        "source": str(raw.get("source") or "智能规则引擎"),
        "can_train": bool(raw.get("can_train", True)),
    }


def rule_recommend_ultralight_config(user_requirement: str, video_info: Dict[str, Any], has_ckpt: bool = False) -> Dict[str, Any]:
    """离线兜底：严格按 ultralight_config_kb 规则引擎推导傻瓜参数。"""
    req = str(user_requirement or "").strip()
    low = req.lower()
    dur = float(video_info.get("duration_sec") or 0.0)
    frames = int(video_info.get("frames") or 0)
    has_audio = bool(video_info.get("has_audio", True)) if video_info.get("ok") else True

    if has_ckpt or any(k in req for k in ("封装", "外部权重", "已有权重", "不训", "直接打包", ".onnx", ".pth")):
        if any(k in req for k in ("续训", "微调", "继续训练", "多训")):
            plan = {
                "mode": "断点续训 (.pth)",
                "asr": "wenet" if any(k in low for k in ("省显存", "wenet", "轻量", "游戏")) else "hubert",
                "epochs": 60,
                "batch_size": 12 if frames >= 300 or frames == 0 else 4,
                "lr": "0.0005",
                "summary_title": "断点续训优化方案",
                "reason": "将在已有 .pth 权重基础上用较低学习率稳步续训微调口型，保留既有特征并导出最新 ONNX。",
            }
        else:
            plan = {
                "mode": "封装外部权重 (.pth/.onnx)",
                "asr": "wenet" if any(k in low for k in ("wenet", "20fps")) else "hubert",
                "epochs": 0,
                "batch_size": 16,
                "lr": "0.001",
                "summary_title": "外部权重零训练直出封装",
                "reason": "跳过耗时的本地训练，直接把现成权重与人物视频帧/关键点打包成可立即开播的 ONNX 数字人。",
            }
    elif any(k in low for k in ("测试", "快速", "极速", "试试", "demo", "看效果", "几分钟")):
        plan = {
            "mode": "从头本地训练 + 导出ONNX",
            "asr": "wenet" if any(k in low for k in ("省显存", "wenet", "轻量")) else "hubert",
            "epochs": 15,
            "batch_size": 8 if (frames == 0 or frames >= 200) else 4,
            "lr": "0.001",
            "summary_title": "极速体验测试方案",
            "reason": "用 15 轮快速走通训练并导出 GPU ONNX 模型，适合先快速预览口型驱动效果（耗时短）。",
        }
    elif any(k in low for k in ("省显存", "轻量", "低配", "游戏", "多开", "wenet", "20fps")):
        plan = {
            "mode": "从头本地训练 + 导出ONNX",
            "asr": "wenet",
            "epochs": 100,
            "batch_size": 8,
            "lr": "0.001",
            "summary_title": "极省显存轻量挂机方案",
            "reason": "采用 20fps WeNet 纯 ONNX 音频路线并控制 Batch=8，显存更宽裕，适合边开其他软件边长播。",
        }
    elif any(k in low for k in ("极致", "高精", "最准", "最自然", "画质拉满", "精细")):
        plan = {
            "mode": "从头本地训练 + 导出ONNX",
            "asr": "hubert",
            "epochs": 150,
            "batch_size": 16 if (frames == 0 or frames >= 400) else 8,
            "lr": "0.001",
            "summary_title": "高精口型精训方案",
            "reason": "启用 HuBERT 25fps 高精度音频特征并将训练轮数提高到 150 轮，充分拟合复杂发音口型。",
        }
    else:
        ep = 120 if (0 < dur < 90.0) else 100
        bs = 16 if (frames == 0 or frames >= 400) else 8
        plan = {
            "mode": "从头本地训练 + 导出ONNX",
            "asr": "hubert",
            "epochs": ep,
            "batch_size": bs,
            "lr": "0.001",
            "summary_title": "标准高画质开播推荐方案",
            "reason": f"选用口型最自然的 HuBERT 25fps 特征，针对当前素材自动配好 {ep} 轮与 Batch={bs}（RTX 2080 显存安全无压力）。",
        }

    plan["source"] = "本地知识库规则引擎"
    return _sanitize_ultralight_plan(plan, video_info, has_ckpt)


def ai_recommend_ultralight_config(
    user_requirement: str,
    video_info: Dict[str, Any],
    has_ckpt: bool,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """将配置知识库 + 视频体检数据 + 用户口语要求送入已配置的大模型生成参数；不可用时无缝降级规则引擎。"""
    kb_text = load_ultralight_config_kb()
    req_txt = (user_requirement or "").strip() or "按当前参考视频帮我配置最适合正式开播的清晰稳定参数"
    vdesc = (
        f"视频有效={video_info.get('ok', False)}, 分辨率={video_info.get('width', 0)}x{video_info.get('height', 0)}, "
        f"FPS={video_info.get('fps', 0)}, 总帧数={video_info.get('frames', 0)}, "
        f"时长={video_info.get('duration_sec', 0)}秒, 是否含音轨(has_audio)={video_info.get('has_audio', False)}, "
        f"用户是否已选外部权重(has_ckpt)={bool(has_ckpt)}"
    )
    sys_p = (
        "你是泳昇 Ultralight-Digital-Human 配置专家助手。请严格遵循以下《配置知识库》与硬件红线，"
        "根据用户视频信息与要求，只输出严格合法的 JSON 对象，禁止输出任何 Markdown 解释文字。\n\n"
        + kb_text
    )
    usr_p = f"【参考视频体检信息】\n{vdesc}\n\n【用户口语要求】\n{req_txt}\n\n请严格返回符合规范的单个 JSON 对象："
    ok, txt, prov = _call_llm_chat(cfg, sys_p, usr_p, max_tokens=420, temperature=0.2)
    if ok and txt:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip(), flags=re.IGNORECASE | re.DOTALL)
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    parsed["source"] = f"AI 大模型 ({prov})"
                    return _sanitize_ultralight_plan(parsed, video_info, has_ckpt)
            except Exception:
                pass
    return rule_recommend_ultralight_config(req_txt, video_info, has_ckpt)

def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def parse_env_bat() -> Dict[str, str]:
    """env.bat 默认值 + env.local.bat 个人设置（与 cmd 实际生效的值一致）。"""
    import config_guide as _cg

    return _cg.parse_env_bat(ENV_BAT_PATH)


def update_env_bat(updates: Dict[str, str]) -> None:
    """主播 / 声线 / 引擎参数只写入 env.local.bat（不进 git），env.bat 保持仓库原样，以后更新不再冲突。"""
    import config_guide as _cg

    if updates:
        _cg.update_env_bat(ENV_BAT_PATH, {k: str(v) for k, v in updates.items()})


def is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(url: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, raw
    except Exception as exc:
        return 0, str(exc)


def test_llm_provider(provider: Dict[str, Any]) -> Tuple[bool, str]:
    api_key = str(provider.get("api_key", "")).strip()
    if not api_key:
        return False, "未填写 API Key"
    base_url = str(provider.get("base_url", "")).rstrip("/")
    model = str(provider.get("model", "")).strip()
    if not base_url or not model:
        return False, "缺少 base_url 或 model"
    body: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "你好，回复收到即可"}],
        "max_tokens": 16,
    }
    if isinstance(provider.get("extra_body"), dict):
        body.update(provider["extra_body"])
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=float(provider.get("timeout", 10))) as resp:
            res = json.loads(resp.read().decode("utf-8", errors="replace"))
            txt = ((res.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            return True, f"连通成功 ({time.time() - t0:.2f}s): {str(txt).strip()[:24]}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:120]}"
    except Exception as exc:
        return False, f"连接失败: {exc}"


# ============================================================
# 主播与知识库工具
# ============================================================
def get_avatars_dir() -> Optional[Path]:
    cfg = load_json(CONFIG_PATH, {})
    cfg_dir = str(cfg.get("livetalking", {}).get("avatars_dir", "")).strip()
    if cfg_dir and Path(cfg_dir).exists():
        return Path(cfg_dir)
    cand = ROOT_DIR / "avatars"
    cand.mkdir(parents=True, exist_ok=True)
    return cand


def _ul_avatar_ready(sub: Path) -> bool:
    return (sub / "unet.onnx").is_file() or (sub / "ultralight.pth").is_file()


def list_hosts() -> List[Dict[str, Any]]:
    cfg = load_json(CONFIG_PATH, {})
    hidden = {str(x).strip().lower() for x in cfg.get("wardrobe_settings", {}).get("hidden_avatars", [])}
    d = get_avatars_dir()
    ready_ids = set()
    if d and d.exists():
        for sub in d.iterdir():
            if sub.is_dir() and not sub.name.startswith(("__", ".")) and sub.name.lower() not in hidden and _ul_avatar_ready(sub):
                ready_ids.add(sub.name)
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in cfg.get("wardrobe", []):
        aid = str(item.get("id", "")).strip()
        if not aid or aid.lower() in hidden:
            continue
        seen.add(aid)
        entry = dict(item)
        entry["ready"] = (aid in ready_ids) if (d and d.exists()) else True
        if d and (d / aid / "meta.json").is_file():
            meta = load_json(d / aid / "meta.json", {})
            entry.setdefault("asr", meta.get("asr", "hubert"))
            entry.setdefault("fps", meta.get("fps", 25))
        out.append(entry)
    for aid in sorted(ready_ids):
        if aid not in seen:
            meta = load_json(d / aid / "meta.json", {}) if d else {}
            disp = str(meta.get("name") or aid)
            out.append({
                "id": aid,
                "name": disp,
                "persona_name": disp,
                "tts_voice": cfg.get("livetalking", {}).get("tts_voice", "zh-CN-XiaoxiaoNeural"),
                "asr": meta.get("asr", "hubert"),
                "fps": meta.get("fps", 25),
                "keywords": [disp, aid],
                "announce": f"大家好，我是{disp}，已经准备好了！",
                "ready": True,
            })
    return out


def register_host_profile(
    avatar_id: str,
    display_name: str,
    persona_name: str,
    tts_voice: str,
    style: str,
    system_prompt: str,
    source_video: str = "",
    base_avatar: str = "",
    keywords: Optional[List[str]] = None,
    sf_voice: str = "",
) -> Tuple[bool, str]:
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", avatar_id.strip())
    if not aid:
        return False, "英文 ID 不能为空且只能含英文、数字、下划线"
    cfg = load_json(CONFIG_PATH, {})
    wardrobe = cfg.setdefault("wardrobe", [])
    base_w = next((x for x in wardrobe if str(x.get("id", "")).strip() == base_avatar.strip()), {}) if base_avatar else {}
    act_dir = ACTION_DIR / aid
    act_dir.mkdir(parents=True, exist_ok=True)
    dname = display_name.strip() or aid
    kw_list = [k.strip() for k in (keywords or []) if k.strip()] or [dname, aid]
    entry: Dict[str, Any] = {
        "id": aid,
        "name": dname,
        "persona_name": persona_name.strip() or base_w.get("persona_name") or dname,
        "persona_name_en": aid,
        "tts_voice": voice_id_from_label(tts_voice) or base_w.get("tts_voice") or "zh-CN-XiaoxiaoNeural",
        "style": style.strip() or base_w.get("style") or "",
        "system_prompt": system_prompt.strip() or base_w.get("system_prompt") or "",
        "source_video": source_video.strip(),
        "action_media_dir": f"action_media/{aid}",
        "props": {
            "sunglasses": "act_sunglasses.mp4",
            "milktea": "act_milktea.mp4",
            "heart": "act_heart.mp4",
            "wave": "act_wave.mp4",
            "thumbsup": "act_thumbsup.mp4",
        },
        "keywords": kw_list,
        "announce": f"大家好，我是{persona_name.strip() or dname}，已经换好新造型上线啦！",
    }
    if base_avatar:
        entry["base_avatar"] = base_avatar
    if sf_voice or base_w.get("sf_voice"):
        entry["sf_voice"] = sf_voice or base_w.get("sf_voice")
    idx = next((i for i, x in enumerate(wardrobe) if x.get("id") == aid), None)
    if idx is not None:
        old = wardrobe[idx]
        if old.get("sf_voice") and not entry.get("sf_voice"):
            entry["sf_voice"] = old["sf_voice"]
        if old.get("asr"):
            entry["asr"] = old["asr"]
        if old.get("fps"):
            entry["fps"] = old["fps"]
        wardrobe[idx] = entry
    else:
        entry.setdefault("sf_voice", suggest_sf_voice_for_host(aid, entry["tts_voice"]))
        wardrobe.append(entry)
    save_json(CONFIG_PATH, cfg)
    # 同步到项目专属目录：主播素材/<主播名称>/
    host_folder = HOSTS_DIR / (entry["name"] or aid)
    host_folder.mkdir(parents=True, exist_ok=True)
    save_json(host_folder / "主播配置.json", entry)
    return True, f"已保存主播 [{entry['name']}] ({aid}) 及专属目录 {host_folder}"


def list_kb_theme_dirs() -> List[str]:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    dirs = ["(根目录)"]
    for d in sorted(KB_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            dirs.append(d.name)
    return dirs


def list_bg_videos() -> List[str]:
    BG_DIR.mkdir(parents=True, exist_ok=True)
    exts = {".mp4", ".webm", ".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p.name for p in BG_DIR.iterdir() if p.is_file() and p.suffix.lower() in exts)


def add_bg_video_file(src_path: Path) -> Tuple[bool, str]:
    if not src_path.is_file():
        return False, "视频文件不存在"
    BG_DIR.mkdir(parents=True, exist_ok=True)
    dst = BG_DIR / src_path.name
    if src_path.resolve() != dst.resolve():
        shutil.copy2(src_path, dst)
    return True, dst.name


def bind_kb_bg_video(doc_name: str, bg_video: str) -> None:
    if not doc_name:
        return
    cfg = load_json(CONFIG_PATH, {})
    bg = cfg.setdefault("background", {})
    dmap = bg.setdefault("doc_video_map", {})
    if bg_video:
        dmap[doc_name] = bg_video
    else:
        dmap.pop(doc_name, None)
    save_json(CONFIG_PATH, cfg)


def mark_custom_action_clip(avatar_id: str, filename: str) -> None:
    """记录用户显式上传的真实动作短片文件名，与默认生成静态占位片区分。"""
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (avatar_id or "").strip())
    target_dir = (ACTION_DIR / aid) if aid else ACTION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    marker = target_dir / ".custom_clips.json"
    cur = load_json(marker, [])
    if not isinstance(cur, list):
        cur = []
    fn = Path(filename).name
    if fn and fn not in cur:
        cur.append(fn)
        save_json(marker, cur)


def list_kb_hosts() -> List[str]:
    KB_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    dirs = sorted(d.name for d in KB_ROOT_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
    return [KB_ALL_HOSTS_LABEL] + dirs


def sync_kb_doc_to_hub(src_md: Path) -> str:
    """确保 知识库/ 下的讲稿与 ai-live-hub/kb_data/ 同步存在，并返回 kb_engine 可识别的相对路径。"""
    if not src_md.is_file():
        return ""
    KB_DIR.mkdir(parents=True, exist_ok=True)
    for cand in KB_DIR.rglob(src_md.name):
        if cand.is_file() and ".indexes" not in cand.parts:
            try:
                if src_md.stat().st_mtime > cand.stat().st_mtime + 1 or src_md.stat().st_size != cand.stat().st_size:
                    shutil.copy2(src_md, cand)
            except Exception:
                pass
            return cand.relative_to(KB_DIR).as_posix()
    # 尚未在 kb_data 存在：保留分类子目录，例如 知识库/主播/分类/a.md -> kb_data/分类/a.md
    try:
        rel_parts = src_md.relative_to(KB_ROOT_DIR).parts
        sub = rel_parts[-2] if len(rel_parts) >= 2 else ""
    except Exception:
        sub = ""
    dst_dir = (KB_DIR / sub) if sub else KB_DIR
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src_md.name
    shutil.copy2(src_md, dst)
    return dst.relative_to(KB_DIR).as_posix()


def list_host_kb_docs(kb_host: str) -> List[Dict[str, Any]]:
    """扫描知识库讲稿与同目录视频；传空或全部标签时扫描全部讲稿，不再把主播和知识库死绑。"""
    is_all = (not kb_host) or kb_host == KB_ALL_HOSTS_LABEL
    base = KB_ROOT_DIR if is_all else (KB_ROOT_DIR / kb_host)
    if not base.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    seen_files: set = set()
    for p in sorted(base.rglob("*.md")):
        if ".indexes" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
        hub_rel = sync_kb_doc_to_hub(p) or p.name
        if is_all:
            # 默认科普主题 与 默认主播 里可能同文件名去重：优先保留具体分类目录
            if p.name in seen_files and rel.startswith("默认科普主题/"):
                continue
            seen_files.add(p.name)
        vids = sorted(v for v in p.parent.iterdir() if v.is_file() and v.suffix.lower() in (".mp4", ".webm"))
        out.append({
            "display": rel,
            "full_path": str(p),
            "folder": str(p.parent),
            "hub_doc": hub_rel,
            "videos": [str(v) for v in vids],
        })
    return out


def parse_kb_preview_sections(md_path: Path) -> List[Dict[str, Any]]:
    """解析讲稿章节/单节及同目录关联视频，供桌面端预览与弹窗播放。"""
    if not md_path.is_file():
        return []
    txt = md_path.read_text(encoding="utf-8", errors="ignore")
    folder_vids = sorted(v for v in md_path.parent.iterdir() if v.is_file() and v.suffix.lower() in (".mp4", ".webm"))
    default_vid = str(folder_vids[0]) if folder_vids else ""

    sections: List[Dict[str, Any]] = []
    cur_title = ""
    cur_bg = ""
    cur_lines: List[str] = []

    def _flush() -> None:
        if not cur_title and not cur_lines:
            return
        vpath = ""
        if cur_bg:
            cand = md_path.parent / Path(cur_bg).name
            if cand.is_file():
                vpath = str(cand)
            elif (BG_DIR / Path(cur_bg).name).is_file():
                vpath = str(BG_DIR / Path(cur_bg).name)
        if not vpath and default_vid:
            idx = len(sections) % len(folder_vids) if folder_vids else 0
            vpath = str(folder_vids[idx]) if folder_vids else ""
        sections.append({
            "title": cur_title or f"单节 {len(sections) + 1}",
            "text": "\n".join(cur_lines).strip()[:240],
            "video_path": vpath,
            "video_name": Path(vpath).name if vpath else "暂无视频",
        })

    for ln in txt.splitlines():
        s = ln.strip()
        if s.startswith("# ") or s.startswith("## "):
            if cur_title or cur_lines:
                _flush()
                cur_lines = []
            cur_title = s.lstrip("# ").strip()
        elif s.startswith("> [背景]"):
            cur_bg = s.replace("> [背景]", "").strip()
        elif s.startswith("> [板书]") or not s or s.startswith("<!--"):
            continue
        else:
            cur_lines.append(s)
    _flush()
    return sections


def list_kb_docs() -> List[Dict[str, Any]]:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_json(CONFIG_PATH, {})
    dmap = cfg.get("background", {}).get("doc_video_map", {})
    docs: List[Dict[str, Any]] = []
    for p in sorted(KB_DIR.rglob("*.md")):
        rel = p.relative_to(KB_DIR).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        chapters = [ln.lstrip("# ").strip() for ln in txt.splitlines() if re.match(r"^#\s+", ln)]
        theme_dir = p.parent.relative_to(KB_DIR).as_posix()
        docs.append({
            "filename": rel,
            "theme_dir": "" if theme_dir == "." else theme_dir,
            "chapters": len(chapters),
            "first_title": chapters[0] if chapters else p.stem,
            "chars": len(txt),
            "bg_video": dmap.get(rel, "") or dmap.get(p.name, ""),
        })
    return docs


def import_kb_file(src_path: Path, target_name: str = "", theme_dir: str = "", bg_video: str = "") -> Tuple[bool, str]:
    if not src_path.is_file():
        return False, "文件不存在"
    raw = src_path.read_text(encoding="utf-8", errors="ignore")
    if str(HUB_DIR) not in sys.path:
        sys.path.insert(0, str(HUB_DIR))
    from script_converter import import_markdown_document
    res = import_markdown_document(raw, title=src_path.stem)
    if res.get("minor_hits"):
        return False, f"包含对未成年人直接喊话内容，已拦截：{res['minor_hits'][:3]}"
    fname = (target_name.strip() or src_path.stem)
    fname = re.sub(r'[\\/:*?"<>|]', "_", fname)
    if not fname.lower().endswith(".md"):
        fname += ".md"
    sub = re.sub(r'[\\/:*?"<>|.]', "_", (theme_dir or "").strip())
    if sub == "(根目录)":
        sub = ""
    target_dir = (KB_DIR / sub) if sub else KB_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / fname
    md_out = res["markdown"]
    if bg_video and "> [背景]" not in md_out:
        md_out = re.sub(r"^(#\s+[^\n]+\n)", rf"\1> [背景] {bg_video}\n", md_out, count=1, flags=re.MULTILINE)
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(md_out, encoding="utf-8")
    os.replace(tmp, out)
    rel = out.relative_to(KB_DIR).as_posix()
    if bg_video:
        bind_kb_bg_video(rel, bg_video)
    return True, f"已导入 {rel}（{res.get('chapters', 0)} 章 / {res.get('chunks', 0)} 段）"


def save_new_kb_text(filename: str, content: str, theme_dir: str = "", bg_video: str = "") -> Tuple[bool, str]:
    fname = re.sub(r'[\\/:*?"<>|]', "_", filename.strip())
    if not fname:
        return False, "文件名不能为空"
    if not fname.lower().endswith(".md"):
        fname += ".md"
    if str(HUB_DIR) not in sys.path:
        sys.path.insert(0, str(HUB_DIR))
    from script_converter import import_markdown_document
    res = import_markdown_document(content, title=Path(fname).stem)
    if res.get("minor_hits"):
        return False, f"包含对未成年人直接喊话内容，已拦截：{res['minor_hits'][:3]}"
    sub = re.sub(r'[\\/:*?"<>|.]', "_", (theme_dir or "").strip())
    if sub == "(根目录)":
        sub = ""
    target_dir = (KB_DIR / sub) if sub else KB_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / fname
    md_out = res["markdown"]
    if bg_video and "> [背景]" not in md_out:
        md_out = re.sub(r"^(#\s+[^\n]+\n)", rf"\1> [背景] {bg_video}\n", md_out, count=1, flags=re.MULTILINE)
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(md_out, encoding="utf-8")
    os.replace(tmp, out)
    # 同步保存到项目专属 知识库/<主题名称>/ 目录
    kb_theme_folder = (KB_ROOT_DIR / sub) if sub else (KB_ROOT_DIR / "默认主题")
    kb_theme_folder.mkdir(parents=True, exist_ok=True)
    (kb_theme_folder / fname).write_text(md_out, encoding="utf-8")
    rel = out.relative_to(KB_DIR).as_posix()
    if bg_video:
        bind_kb_bg_video(rel, bg_video)
    return True, f"已保存 {rel}（{res.get('chapters', 0)} 章 / {res.get('chunks', 0)} 段）"


# ============================================================
# OBS 路径探测 + 多模式配置文件生成 + 拉起
# ============================================================
def _resolve_lnk_target(lnk_path: Path) -> str:
    if not lnk_path.exists():
        return ""
    try:
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{str(lnk_path)}');"
            "Write-Output $s.TargetPath"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.stdout.strip()
    except Exception:
        return ""


def find_obs_executable() -> str:
    lcfg = load_json(LAUNCHER_CFG_PATH, {})
    saved = str(lcfg.get("obs_path", "")).strip()
    if saved and Path(saved).is_file():
        return saved

    lnk = ROOT_DIR / "OBS Studio.lnk"
    if lnk.exists():
        t = _resolve_lnk_target(lnk)
        if t and Path(t).is_file():
            return t

    for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"SOFTWARE\OBS Studio", r"SOFTWARE\WOW6432Node\OBS Studio"):
            try:
                with winreg.OpenKey(root_key, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    cand = Path(str(val)) / "bin" / "64bit" / "obs64.exe"
                    if cand.is_file():
                        return str(cand)
            except OSError:
                pass

    common = [
        r"D:\software\obs-studio\bin\64bit\obs64.exe",
        r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
        r"D:\Program Files\obs-studio\bin\64bit\obs64.exe",
        r"D:\obs-studio\bin\64bit\obs64.exe",
    ]
    for c in common:
        if Path(c).is_file():
            return c
    return ""


def get_obs_appdata_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "obs-studio"


def list_existing_obs_profiles() -> List[str]:
    pdir = get_obs_appdata_dir() / "basic" / "profiles"
    if not pdir.exists():
        return []
    return sorted([d.name for d in pdir.iterdir() if d.is_dir()])


def sync_obs_ws_to_hub() -> Tuple[bool, str]:
    """读取 OBS WebSocket 配置并同步到 platform_profiles.json。"""
    ws_cfg_path = get_obs_appdata_dir() / "plugin_config" / "obs-websocket" / "config.json"
    if not ws_cfg_path.exists():
        return False, "未找到 OBS WebSocket 配置文件，请先在 OBS 工具里开启一次 WebSocket"
    ws_cfg = load_json(ws_cfg_path, {})
    profs = load_json(PROFILES_PATH, {})
    obs_ws = profs.setdefault("obs_ws", {})
    obs_ws["enabled"] = bool(ws_cfg.get("server_enabled", True))
    obs_ws["port"] = int(ws_cfg.get("server_port", 4455))
    if ws_cfg.get("auth_required", False):
        obs_ws["password"] = str(ws_cfg.get("server_password", ""))
    else:
        obs_ws["password"] = ""
    save_json(PROFILES_PATH, profs)
    return True, f"已同步 OBS WebSocket (端口 {obs_ws['port']}，{'带密码' if obs_ws['password'] else '免密'})"


def generate_project_obs_presets(hub_url: str = "http://127.0.0.1:28910/stage?layer=all") -> Tuple[bool, str]:
    """在项目专用目录 OBS配置/ 下以中文名称实际生成 5 套 OBS 配置文件与场景集合。"""
    OBS_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    proj_scenes = OBS_PRESETS_DIR / "场景集合"
    proj_scenes.mkdir(parents=True, exist_ok=True)

    obs_profiles_dir = get_obs_appdata_dir() / "basic" / "profiles"
    template_ini = None
    if obs_profiles_dir.exists():
        for cand in obs_profiles_dir.iterdir():
            if cand.is_dir() and (cand / "basic.ini").is_file():
                template_ini = cand / "basic.ini"
                break

    created: List[str] = []
    for p in OBS_PRESETS:
        cn_dir = OBS_PRESETS_DIR / p["cn_name"]
        cn_dir.mkdir(parents=True, exist_ok=True)
        ini_path = cn_dir / "basic.ini"
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        if ini_path.exists():
            cp.read(ini_path, encoding="utf-8-sig")
        elif template_ini and template_ini.exists():
            cp.read(template_ini, encoding="utf-8-sig")

        for sec in ("General", "Output", "AdvOut", "SimpleOutput", "Video", "Audio"):
            if not cp.has_section(sec):
                cp.add_section(sec)
        cp.set("General", "Name", p["profile"])
        cp.set("Output", "Mode", "Advanced")
        cp.set("AdvOut", "Encoder", "obs_nvenc_h264_tex")
        cp.set("AdvOut", "RecEncoder", "obs_nvenc_h264_tex")
        cp.set("SimpleOutput", "StreamEncoder", "nvenc")
        cp.set("SimpleOutput", "RecEncoder", "nvenc")
        cp.set("Video", "BaseCX", str(p["width"]))
        cp.set("Video", "BaseCY", str(p["height"]))
        cp.set("Video", "OutputCX", str(p["width"]))
        cp.set("Video", "OutputCY", str(p["height"]))
        # 数字人 HuBERT 形象为 25fps：OBS 也用 25fps，避免 30fps 每秒重复 5 帧造成轻微顿挫
        cp.set("Video", "FPSType", "1")
        cp.set("Video", "FPSInt", "25")
        cp.set("Video", "FPSCommon", "25 PAL")
        cp.set("Video", "FPSNum", "25")
        cp.set("Video", "FPSDen", "1")

        tmp_ini = ini_path.with_suffix(".ini.tmp")
        with tmp_ini.open("w", encoding="utf-8") as f:
            cp.write(f, space_around_delimiters=False)
        os.replace(tmp_ini, ini_path)
        created.append(p["cn_name"])

    scene_path = proj_scenes / "泳昇Ultralight直播.json"
    shared_src_name = "数字人舞台"
    shared_src_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "ysaizb.browser.shared"))
    sources: List[Dict[str, Any]] = [{
        "prev_ver": 553648131,
        "name": shared_src_name,
        "uuid": shared_src_uuid,
        "id": "browser_source",
        "versioned_id": "browser_source",
        "settings": {
            "url": hub_url,
            "width": 1296,
            "height": 1080,
            "fps": 30,
            "reroute_audio": True,
            "restart_when_active": False,
            "shutdown": False,
        },
        "mixers": 255, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5,
        "enabled": True, "muted": False, "deinterlace_mode": 0,
        "deinterlace_field_order": 0, "monitoring_type": 2, "private_settings": {},
    }]
    scene_order: List[Dict[str, str]] = []
    for p in OBS_PRESETS:
        scene_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ysaizb.scene.{p['profile']}"))
        sources.append({
            "prev_ver": 553648131,
            "name": p["scene"],
            "uuid": scene_uuid,
            "id": "scene",
            "versioned_id": "scene",
            "settings": {
                "id_counter": 1,
                "custom_size": False,
                "items": [{
                    "name": shared_src_name, "source_uuid": shared_src_uuid, "visible": True, "locked": False,
                    "rot": 0.0, "pos": {"x": 0.0, "y": 0.0}, "scale": {"x": 1.0, "y": 1.0},
                    "align": 5, "bounds_type": 2, "bounds_align": 0, "bounds": {"x": float(p["width"]), "y": float(p["height"])},
                    "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0,
                    "id": 1, "group_item_backup": False,
                    "hide_transition": {"duration": 0}, "show_transition": {"duration": 0},
                    "private_settings": {},
                }],
            },
            "mixers": 0, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5,
            "enabled": True, "muted": False, "deinterlace_mode": 0,
            "deinterlace_field_order": 0, "monitoring_type": 0, "private_settings": {},
        })
        scene_order.append({"name": p["scene"]})

    save_json(scene_path, {
        "name": "AI_Live_Hub_Ultralight",
        "current_scene": OBS_PRESETS[0]["scene"],
        "current_program_scene": OBS_PRESETS[0]["scene"],
        "scene_order": scene_order,
        "sources": sources,
    })
    return True, f"已在 {OBS_PRESETS_DIR} 生成 {len(created)} 套中文名 OBS 配置文件与场景集合"


def import_obs_preset_to_obs(profile_name: str = "", scene_name: str = "") -> Tuple[bool, str]:
    """将项目专用目录 OBS配置/ 下的中文名配置自动导入到本机 OBS AppData 目录，并写入目标启动场景与 Profile。"""
    if not (OBS_PRESETS_DIR / "场景集合" / "泳昇Ultralight直播.json").is_file():
        generate_project_obs_presets()
    basic_dir = get_obs_appdata_dir() / "basic"
    obs_profiles = basic_dir / "profiles"
    obs_scenes = basic_dir / "scenes"
    obs_profiles.mkdir(parents=True, exist_ok=True)
    obs_scenes.mkdir(parents=True, exist_ok=True)

    target_scene = scene_name.strip()
    if not target_scene and profile_name:
        m = next((x for x in OBS_PRESETS if x["profile"] == profile_name), None)
        if m:
            target_scene = m["scene"]

    targets = [p for p in OBS_PRESETS if (not profile_name or p["profile"] == profile_name)]
    imported: List[str] = []
    for p in targets:
        src_dir = OBS_PRESETS_DIR / p["cn_name"]
        if not src_dir.is_dir():
            continue
        dst_dir = obs_profiles / p["profile"]
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, dst_dir / f.name)
        imported.append(f"{p['cn_name']}→{p['profile']}")

    src_scene = OBS_PRESETS_DIR / "场景集合" / "泳昇Ultralight直播.json"
    if src_scene.is_file():
        dst_scene = obs_scenes / "AI_Live_Hub_Ultralight.json"
        if target_scene or profile_name:
            try:
                sc_data = json.loads(src_scene.read_text(encoding="utf-8"))
                p_match = next(
                    (x for x in OBS_PRESETS if (profile_name and x["profile"] == profile_name) or (target_scene and x["scene"] == target_scene)),
                    None,
                )
                if target_scene:
                    sc_data["current_scene"] = target_scene
                    sc_data["current_program_scene"] = target_scene
                    sc_data["current_preview_scene"] = target_scene
                if p_match:
                    for src in sc_data.get("sources", []):
                        if src.get("name") == "数字人舞台" and isinstance(src.get("settings"), dict):
                            src["settings"]["width"] = int(p_match["width"])
                            src["settings"]["height"] = int(p_match["height"])
                            break
                save_json(dst_scene, sc_data)
            except Exception:
                shutil.copy2(src_scene, dst_scene)
        else:
            shutil.copy2(src_scene, dst_scene)
    # 同步 user.ini 默认激活 AI_Live_Hub_Ultralight 场景集合与选定 Profile
    user_ini = get_obs_appdata_dir() / "user.ini"
    try:
        ucp = configparser.ConfigParser(interpolation=None)
        ucp.optionxform = str
        if user_ini.is_file():
            ucp.read(user_ini, encoding="utf-8-sig")
        if not ucp.has_section("Basic"):
            ucp.add_section("Basic")
        ucp.set("Basic", "SceneCollection", "AI_Live_Hub_Ultralight")
        ucp.set("Basic", "SceneCollectionFile", "AI_Live_Hub_Ultralight")
        if profile_name:
            ucp.set("Basic", "Profile", profile_name)
            ucp.set("Basic", "ProfileDir", profile_name)
        tmp_u = user_ini.with_suffix(".ini.tmp")
        with tmp_u.open("w", encoding="utf-8") as f:
            ucp.write(f, space_around_delimiters=False)
        os.replace(tmp_u, user_ini)
    except Exception:
        pass
    sync_obs_ws_to_hub()
    return True, f"已自动导入 OBS 配置：{', '.join(imported)} + 场景集合 AI_Live_Hub_Ultralight（目标：{target_scene or '默认'}）"


def manual_import_obs_file(file_path: Path, target_profile: str = "") -> Tuple[bool, str]:
    """手动选择 .ini 或 .json 配置文件导入到 OBS。"""
    if not file_path.is_file():
        return False, "所选文件不存在"
    basic_dir = get_obs_appdata_dir() / "basic"
    ext = file_path.suffix.lower()
    if ext == ".ini":
        pname = target_profile.strip() or file_path.parent.name
        dst_dir = basic_dir / "profiles" / pname
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dst_dir / "basic.ini")
        return True, f"已手动导入配置文件到 OBS Profile [{pname}]"
    if ext == ".json":
        if file_path.name.lower() == "service.json":
            pname = target_profile.strip() or file_path.parent.name
            dst_dir = basic_dir / "profiles" / pname
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dst_dir / "service.json")
            return True, f"已手动导入推流配置到 OBS Profile [{pname}]"
        dst_scenes = basic_dir / "scenes"
        dst_scenes.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dst_scenes / file_path.name)
        return True, f"已手动导入场景集合 [{file_path.name}] 到 OBS"
    return False, "仅支持导入 .ini 或 .json 格式的 OBS 配置文件"


# ============================================================
# 全局配置专属目录管理（多份保存 / 读取加载）
# ============================================================
def ensure_project_folders() -> None:
    """初始化项目下专属文件夹：主播素材、知识库、OBS配置、全局配置。"""
    HOSTS_DIR.mkdir(parents=True, exist_ok=True)
    for h in list_hosts():
        hdir = HOSTS_DIR / (h.get("name") or h["id"])
        hdir.mkdir(parents=True, exist_ok=True)
        pfile = hdir / "主播配置.json"
        if not pfile.exists():
            save_json(pfile, h)
        # 同步动作短片目录
        adir = ACTION_DIR / h["id"]
        adir.mkdir(parents=True, exist_ok=True)

    KB_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    if KB_DIR.exists() and not any(KB_ROOT_DIR.rglob("*.md")):
        def_kb = KB_ROOT_DIR / "默认科普主题"
        def_kb.mkdir(parents=True, exist_ok=True)
        for md in KB_DIR.glob("*.md"):
            dst = def_kb / md.name
            if not dst.exists():
                shutil.copy2(md, dst)

    generate_project_obs_presets()
    GLOBAL_CFG_DIR.mkdir(parents=True, exist_ok=True)
    if not list(GLOBAL_CFG_DIR.glob("*.json")):
        save_global_config("默认全局配置")


def list_global_configs() -> List[str]:
    GLOBAL_CFG_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in GLOBAL_CFG_DIR.glob("*.json"))


def save_global_config(name: str) -> Tuple[bool, str]:
    clean = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
    if not clean:
        return False, "全局配置名称不能为空"
    GLOBAL_CFG_DIR.mkdir(parents=True, exist_ok=True)
    lcfg = load_json(LAUNCHER_CFG_PATH, {})
    lcfg["last_global_config"] = clean
    save_json(LAUNCHER_CFG_PATH, lcfg)
    bundle = {
        "name": clean,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": load_json(CONFIG_PATH, {}),
        "platform_profiles": load_json(PROFILES_PATH, {}),
        "launcher_settings": lcfg,
    }
    out = GLOBAL_CFG_DIR / f"{clean}.json"
    save_json(out, bundle)
    return True, f"已保存全局配置到：{out}"


def load_global_config(name_or_path: str) -> Tuple[bool, str]:
    p = Path(name_or_path)
    if not p.is_file():
        p = GLOBAL_CFG_DIR / f"{name_or_path}.json"
    if not p.is_file():
        return False, f"找不到全局配置文件: {name_or_path}"
    bundle = load_json(p, {})
    cur_lcfg = load_json(LAUNCHER_CFG_PATH, {})
    if isinstance(bundle.get("config"), dict):
        save_json(CONFIG_PATH, bundle["config"])
        aid = str(bundle["config"].get("livetalking", {}).get("avatar_id") or "").strip()
        tv = str(bundle["config"].get("livetalking", {}).get("tts_voice") or "").strip()
        ul_cfg = bundle["config"].get("ultralight", {})
        env_patch: Dict[str, str] = {}
        if aid:
            env_patch["UL_AVATAR"] = aid
        if tv:
            env_patch["UL_VOICE"] = tv
        if ul_cfg.get("asr"):
            env_patch["UL_ASR"] = str(ul_cfg["asr"])
        if ul_cfg.get("backend"):
            env_patch["UL_BACKEND"] = str(ul_cfg["backend"])
        if env_patch:
            update_env_bat(env_patch)
    if isinstance(bundle.get("platform_profiles"), dict):
        save_json(PROFILES_PATH, bundle["platform_profiles"])
    new_lcfg = dict(cur_lcfg)
    if isinstance(bundle.get("launcher_settings"), dict):
        new_lcfg.update(bundle["launcher_settings"])
    new_lcfg["last_global_config"] = p.stem
    save_json(LAUNCHER_CFG_PATH, new_lcfg)
    if is_port_open("127.0.0.1", 28910, 0.25):
        http_json("http://127.0.0.1:28910/api/reload", method="POST", timeout=5.0)
    return True, f"已加载全局配置：{p.stem}"


def ensure_obs_presets(hub_url: str = "http://127.0.0.1:28910/stage?layer=all") -> Tuple[bool, str]:
    ok, msg = generate_project_obs_presets(hub_url=hub_url)
    if not ok:
        return ok, msg
    return import_obs_preset_to_obs()


# ============================================================
# Root 安卓手机模拟摄像头推流（ADB / RTMP）
# ============================================================
def check_adb_devices() -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [ln.strip() for ln in r.stdout.splitlines()[1:] if ln.strip()]
        if not lines:
            return False, "未检测到已连接的安卓手机（请开启 USB 调试并授权电脑）"
        return True, f"已检测到安卓设备：{'; '.join(lines)}"
    except FileNotFoundError:
        return False, "系统未安装 adb 命令，如走局域网 Wi-Fi 推流可直接填写手机 IP 的 RTMP 地址"
    except Exception as exc:
        return False, f"ADB 检测失败: {exc}"


def setup_android_adb_reverse(port: int = 1935) -> Tuple[bool, str]:
    ok, msg = check_adb_devices()
    if not ok:
        return False, msg
    try:
        r = subprocess.run(
            ["adb", "reverse", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0:
            return True, f"ADB 端口反向转发成功 (tcp:{port} -> 手机端)，OBS 可直接推流到 rtmp://127.0.0.1:{port}/live"
        return False, f"ADB 转发失败: {r.stderr.strip() or r.stdout.strip()}"
    except Exception as exc:
        return False, f"ADB 转发异常: {exc}"


def apply_android_stream_to_obs(profile_name: str, rtmp_url: str, stream_key: str = "vcam") -> Tuple[bool, str]:
    """将 Root 安卓手机模拟摄像头的 RTMP 推流地址写入 OBS Profile 的 service.json。"""
    p_obj = next((x for x in OBS_PRESETS if x["profile"] == profile_name), None)
    cn_name = p_obj["cn_name"] if p_obj else profile_name
    if not (OBS_PRESETS_DIR / cn_name).is_dir():
        generate_project_obs_presets()
    service = {
        "type": "rtmp_custom",
        "settings": {
            "server": rtmp_url.strip() or "rtmp://127.0.0.1:1935/live",
            "key": stream_key.strip() or "vcam",
            "use_auth": False,
        },
    }
    proj_svc = OBS_PRESETS_DIR / cn_name / "service.json"
    save_json(proj_svc, service)
    import_obs_preset_to_obs(profile_name)
    return True, f"已将 Root 安卓模拟摄像头推流地址写入 [{cn_name}]：{service['settings']['server']}/{service['settings']['key']}"


# ============================================================
# 平台应用检测 + 平台配置导出 + 集成指导教程
# ============================================================
PLATFORM_GUIDES: Dict[str, str] = {
    "douyin": (
        "【抖音直播接入教程】\n"
        "1. 电脑打开「抖音直播伴侣」或准备 OBS 推流码；\n"
        "2. 如用 OBS 推流：导入 Douyin_1296x1080 配置，将抖音开播获得的推流地址/推流码填入 OBS → 设置 → 直播；\n"
        "3. 如推流到 Root 安卓手机模拟摄像头开播：在「4. OBS 多模式配置」开启安卓模拟摄像头推流并点击 ADB 转发；\n"
        "4. 弹幕采集：启动本地 douyinLive (:1089) 后，填入直播间号，启动工作流即可自动拉起转发。"
    ),
    "bilibili": (
        "【B站直播接入教程】\n"
        "1. 打开「哔哩哔哩直播姬」或 B站网页开播中心获取 RTMP 服务器与推流码；\n"
        "2. 导入 Bilibili_1920x1080（1920×1080 横屏）到 OBS，填入推流码开始推流；\n"
        "3. 弹幕接入：将外部弹幕工具的 HTTP 推送目标设为 http://127.0.0.1:28910/api/danmaku。"
    ),
    "wechat": (
        "【视频号直播接入教程】\n"
        "1. 打开微信视频号助手（网页端）或微信客户端开播，选择「OBS 推流」或摄像头模式（支持 Root 安卓模拟摄像头）；\n"
        "2. 导入 WeChat_720x1280（720×1280 竖屏）到 OBS，编码器保持 NVIDIA NVENC H.264；\n"
        "3. 注意合规：平台配置已自动启用成人科普视角，禁止利益诱导分享与夸大宣传。"
    ),
    "youtube": (
        "【YouTube Live 接入教程】\n"
        "1. 打开 YouTube Studio → 直播，获取推流密钥并填入 OBS（YouTube_1280x720）；\n"
        "2. 海外弹幕采集如需代理，请在上方「采集代理」填入 http://127.0.0.1:7890。"
    ),
    "tiktok": (
        "【TikTok Live 接入教程】\n"
        "1. 在 TikTok Live Studio 或推流中心获取推流地址，OBS 导入 TikTok_720x1280 竖屏配置；\n"
        "2. 在上方填入 TikTok 用户名（@handle）与本地代理地址，启动工作流会自动拉起 tiktok_relay.py 采集。"
    ),
}


def detect_platform_app(platform_key: str) -> Tuple[bool, str]:
    """检测对应平台直播伴侣/客户端或采集依赖是否已开启。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="mbcs", errors="ignore", timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        procs = r.stdout.lower()
    except Exception:
        procs = ""

    obs_running = "obs64.exe" in procs
    if platform_key == "douyin":
        companion = any(k in procs for k in ("直播伴侣", "douyin", "streamingtool"))
        danmaku_up = is_port_open("127.0.0.1", 1089, 0.25)
        parts = [
            f"抖音直播伴侣: {'已运行' if companion else '未检测到'}",
            f"douyinLive(:1089): {'已开启' if danmaku_up else '未开启'}",
            f"OBS: {'已运行' if obs_running else '未运行'}",
        ]
        return (companion or danmaku_up or obs_running), " | ".join(parts)
    if platform_key == "bilibili":
        livehime = "livehime.exe" in procs
        return (livehime or obs_running), f"B站直播姬: {'已运行' if livehime else '未检测到'} | OBS: {'已运行' if obs_running else '未运行'}"
    if platform_key == "wechat":
        wx = ("wechat.exe" in procs) or ("weixin.exe" in procs) or ("wechatappex.exe" in procs)
        return (wx or obs_running), f"微信客户端: {'已运行' if wx else '未检测到'} | OBS: {'已运行' if obs_running else '未运行'}"
    return obs_running, f"OBS Studio: {'已运行' if obs_running else '未运行（海外平台推荐直接用 OBS 推流）'}"


def export_platform_config(platform_key: str, room_id: str = "", proxy: str = "") -> Tuple[bool, str]:
    """生成对应平台可直接导入/使用的配置文件到 platform_configs/<platform>/。"""
    profs = load_json(PROFILES_PATH, {})
    prof = profs.get("profiles", {}).get(platform_key, {})
    out_dir = PLATFORM_CFG_DIR / platform_key
    out_dir.mkdir(parents=True, exist_ok=True)

    obs_p = next((p for p in OBS_PRESETS if p["key"] == platform_key), OBS_PRESETS[0])
    import_obs_preset_to_obs(obs_p["profile"])

    cfg_out = {
        "platform": platform_key,
        "label": prof.get("label", platform_key),
        "room_id": room_id,
        "proxy": proxy,
        "obs_profile": obs_p["profile"],
        "obs_scene": obs_p["scene"],
        "canvas": f"{obs_p['width']}x{obs_p['height']}",
        "obs_browser_source_url": "http://127.0.0.1:28910/stage?layer=all",
        "danmaku_webhook": "http://127.0.0.1:28910/api/danmaku",
    }
    save_json(out_dir / "platform_config.json", cfg_out)
    (out_dir / "obs_browser_source.txt").write_text(
        f"OBS 浏览器源地址: http://127.0.0.1:28910/stage?layer=all\n"
        f"画幅宽高: {obs_p['width']} x {obs_p['height']}\n"
        f"OBS 配置文件: {obs_p['profile']}\n"
        f"OBS 场景名: {obs_p['scene']}\n",
        encoding="utf-8",
    )
    return True, f"已生成 [{prof.get('label', platform_key)}] 平台配置到 {out_dir} 并导入 OBS"


def launch_obs(obs_exe: str, profile: str = "", scene: str = "", collection: str = "AI_Live_Hub_Ultralight") -> Tuple[bool, str]:
    exe = Path(obs_exe) if obs_exe else None
    if not exe or not exe.is_file():
        return False, "未找到 obs64.exe，请点击「浏览」手动选择 OBS 安装路径"
    try:
        tl = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"],
            capture_output=True, text=True, encoding="mbcs", errors="ignore", timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if "obs64.exe" in (tl.stdout or "").lower():
            return True, "检测到 OBS Studio 已在运行，将直接同步目标场景与画幅"
    except Exception:
        pass
    # 无 obs64.exe 运行时清理上次强退遗留的 .sentinel 标记，避免 OBS 33+ 弹窗询问 Safe Mode 阻塞 WebSocket 启动
    sentinel_dir = get_obs_appdata_dir() / ".sentinel"
    if sentinel_dir.is_dir():
        for mk in sentinel_dir.iterdir():
            if mk.is_file():
                try:
                    mk.unlink()
                except Exception:
                    pass
    cmd = [str(exe)]
    if profile:
        cmd += ["--profile", profile]
    if collection:
        cmd += ["--collection", collection]
    if scene:
        cmd += ["--scene", scene]
    if "--disable-shutdown-check" not in cmd:
        cmd.append("--disable-shutdown-check")
    try:
        subprocess.Popen(cmd, cwd=str(exe.parent))
        return True, f"已拉起 OBS Studio（配置: {profile or '默认'}，场景: {scene or '默认'}）"
    except Exception as exc:
        return False, f"拉起 OBS 失败: {exc}"


# ============================================================
# 开播预设方案管理
# ============================================================
_LEGACY_DEFAULT_PRESET_NAMES = {
    "抖音 · 默认主播 · 家庭AI启蒙",
    "视频号 · 绿幕主播 · 长辈防骗专场",
    "B站 · 科技装主播 · AI黑话词典",
    "抖音 · 说书先生 · 三国草船借箭",
    "B站 · 游戏女主播 · 上分节奏运营",
    "抖音 · 电脑硬件全科女主播 · 装机避坑",
    "抖音 · 默认主播",
    "视频号 · 绿幕主播",
    "B站 · 科技装主播",
    "抖音 · 说书先生",
    "B站 · 游戏女主播",
    "抖音 · 电脑硬件全科女主播",
}

PLAT_CN_LABELS: Dict[str, str] = {
    "douyin": "抖音",
    "wechat": "视频号",
    "bilibili": "B站",
    "youtube": "YouTube",
    "tiktok": "TikTok",
}


def plat_cn_label(plat_key: str) -> str:
    return PLAT_CN_LABELS.get(str(plat_key or "").strip().lower(), str(plat_key or "直播"))


def kb_short_title(doc_name: str, full_path: str = "") -> str:
    """提取讲稿可读标题（优先首行一级标题，去编号前缀，避免硬编码旧名称）。"""
    p = Path(full_path) if full_path else None
    if (not p or not p.is_file()) and doc_name:
        raw = str(doc_name).replace("\\", "/").lstrip("/")
        cand = KB_DIR / raw
        if not cand.is_file():
            for h in KB_DIR.rglob(Path(raw).name):
                cand = h
                break
        if cand.is_file():
            p = cand
    if p and p.is_file():
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = ln.strip()
            if s.startswith("# "):
                t = re.sub(r"^#\s*(?:第[一二三四五六七八九十百\d]+[章节回]\s*[:：]?\s*)?", "", s).strip()
                t = re.sub(r"^(?:醒木一拍[:：]\s*)", "", t).strip()
                if t:
                    return t[:14]
    stem = Path(str(doc_name or "").replace("\\", "/")).stem
    stem = re.sub(r"^\d+_", "", stem)
    return stem or "未选知识库"


DEFAULT_PRESETS: List[Dict[str, Any]] = []


def load_presets() -> List[Dict[str, Any]]:
    if not PRESETS_PATH.exists():
        save_json(PRESETS_PATH, DEFAULT_PRESETS)
        return list(DEFAULT_PRESETS)
    data = load_json(PRESETS_PATH, [])
    if not isinstance(data, list):
        return list(DEFAULT_PRESETS)
    # 迁移清理系统旧预置模板，仅保留用户自定义开播预设方案
    cleaned = [p for p in data if p.get("name") not in _LEGACY_DEFAULT_PRESET_NAMES]
    if len(cleaned) != len(data):
        save_json(PRESETS_PATH, cleaned)
    return cleaned


def save_preset(preset: Dict[str, Any]) -> List[Dict[str, Any]]:
    presets = load_presets()
    name = str(preset.get("name", "")).strip()
    if not name:
        return presets
    presets = [p for p in presets if p.get("name") != name]
    presets.insert(0, preset)
    save_json(PRESETS_PATH, presets)
    return presets


def delete_preset(name: str) -> List[Dict[str, Any]]:
    presets = [p for p in load_presets() if p.get("name") != name]
    save_json(PRESETS_PATH, presets)
    return presets


# ============================================================
# 直播工作流控制器（拉起 Ultralight 引擎 + AI-Live-Hub + OBS）
# ============================================================
class WorkflowController:
    def __init__(self) -> None:
        self.lt_proc: Optional[subprocess.Popen] = None
        self.hub_proc: Optional[subprocess.Popen] = None

    def apply_prelaunch_config(self, state_data: Dict[str, Any]) -> None:
        """将面板选定的全部参数完整落盘到 config.json / platform_profiles.json / launcher_settings.json / env.bat。"""
        cfg = load_json(CONFIG_PATH, {})
        profs = load_json(PROFILES_PATH, {})
        lcfg = load_json(LAUNCHER_CFG_PATH, {})

        avatar_id = str(state_data.get("avatar_id") or "").strip()
        host_name = str(state_data.get("host_name") or "").strip()
        live_style = str(state_data.get("live_style") or "").strip()
        system_prompt = str(state_data.get("system_prompt") or "").strip()
        kb_doc = str(state_data.get("kb_doc") or "").strip()
        kb_host = str(state_data.get("kb_host") or "").strip()
        kb_mode = bool(state_data.get("kb_narration_mode", True))
        plat = str(state_data.get("platform") or "douyin").strip()
        room_id = str(state_data.get("room_id") or "").strip()
        proxy = str(state_data.get("proxy") or "").strip()
        obs_profile = str(state_data.get("obs_profile") or "").strip()
        obs_scene = str(state_data.get("obs_scene") or "").strip()
        orientation = str(state_data.get("orientation") or "").strip()
        if not orientation and obs_profile:
            orientation = "portrait" if "720x1280" in obs_profile else "landscape"

        tts_mode = tts_mode_key_from_label(str(state_data.get("tts_mode") or ""))
        tts_voice = voice_id_from_label(str(state_data.get("tts_voice") or ""))
        sf_voice = sf_voice_id_from_label(str(state_data.get("sf_voice") or ""))
        tts_emotion = str(state_data.get("tts_emotion") or "")
        gsv_ref = str(state_data.get("gsv_ref") or "").strip()
        gsv_prompt = str(state_data.get("gsv_prompt") or "")

        if avatar_id:
            cfg.setdefault("livetalking", {})["avatar_id"] = avatar_id
        if tts_voice:
            cfg.setdefault("livetalking", {})["tts_voice"] = tts_voice
        if tts_mode:
            cfg.setdefault("tts", {})["mode"] = tts_mode
        sf_cfg = cfg.setdefault("tts", {}).setdefault("siliconflow", {})
        if sf_voice:
            sf_cfg["voice"] = sf_voice
        if "tts_emotion" in state_data:
            sf_cfg["emotion_prefix"] = tts_emotion.strip()
        gs_cfg = cfg.setdefault("tts", {}).setdefault("gptsovits", {})
        if gsv_ref:
            gs_cfg["ref_audio_path"] = gsv_ref.replace("\\", "/")
        if "gsv_prompt" in state_data and gsv_prompt.strip():
            gs_cfg["prompt_text"] = gsv_prompt.strip()
        persona = cfg.setdefault("persona", {})
        if host_name:
            persona["name"] = host_name
        if live_style:
            persona["style"] = live_style
        if system_prompt:
            persona["system_prompt"] = system_prompt

        # 同步写回对应主播条目，避免下次切换主播或触发换装时还原旧台词/旧声线
        if avatar_id:
            for item in cfg.get("wardrobe", []):
                if str(item.get("id", "")).strip() == avatar_id:
                    if host_name:
                        item["persona_name"] = host_name
                    if live_style:
                        item["style"] = live_style
                    if system_prompt:
                        item["system_prompt"] = system_prompt
                    if tts_voice:
                        item["tts_voice"] = tts_voice
                    if sf_voice:
                        item["sf_voice"] = sf_voice
                    host_dir = HOSTS_DIR / (item.get("name") or avatar_id)
                    if host_dir.is_dir():
                        save_json(host_dir / "主播配置.json", item)
                    break

        if kb_doc:
            cfg.setdefault("knowledge_base", {})["active_doc"] = kb_doc
        cfg.setdefault("knowledge_base", {})["narration_mode"] = kb_mode
        if isinstance(state_data.get("overlay"), dict):
            cfg["overlay"] = sanitize_overlay_cfg(state_data["overlay"], base=cfg.get("overlay"))
        save_json(CONFIG_PATH, cfg)

        profs["active_profile"] = plat
        p_entry = profs.setdefault("profiles", {}).setdefault(plat, {})
        if kb_doc:
            p_entry["kb_doc"] = kb_doc
        p_entry["kb_narration_mode"] = kb_mode
        p_entry["proxy"] = proxy
        p_entry["last_room_id"] = room_id
        if obs_profile:
            p_entry["obs_profile"] = obs_profile
        if obs_scene:
            p_entry["obs_scene"] = obs_scene
        if orientation in ("landscape", "portrait"):
            p_entry["orientation"] = orientation
        save_json(PROFILES_PATH, profs)

        if state_data.get("obs_path"):
            lcfg["obs_path"] = str(state_data["obs_path"]).strip()
        if kb_host:
            lcfg["kb_host"] = kb_host
        if kb_doc:
            lcfg["kb_doc"] = kb_doc
        if "auto_obs" in state_data:
            lcfg["auto_obs"] = bool(state_data["auto_obs"])
        if state_data.get("preset"):
            lcfg["preset"] = str(state_data["preset"]).strip()
        if state_data.get("last_global_config"):
            lcfg["last_global_config"] = str(state_data["last_global_config"]).strip()
        save_json(LAUNCHER_CFG_PATH, lcfg)

        env_patch: Dict[str, str] = {}
        if avatar_id:
            env_patch["UL_AVATAR"] = avatar_id
        if tts_voice:
            env_patch["UL_VOICE"] = tts_voice
        ul_asr = str(state_data.get("ul_asr") or "").strip().lower()
        ul_backend = str(state_data.get("ul_backend") or "").strip().lower()
        if ul_asr in ("hubert", "wenet"):
            cfg.setdefault("ultralight", {})["asr"] = ul_asr
            env_patch["UL_ASR"] = ul_asr
        if ul_backend in ("onnx", "torch"):
            cfg.setdefault("ultralight", {})["backend"] = ul_backend
            env_patch["UL_BACKEND"] = ul_backend
        if env_patch:
            save_json(CONFIG_PATH, cfg)
            update_env_bat(env_patch)

    @staticmethod
    def _read_new_log_tail(log_path: Path, start_offset: int, max_lines: int = 12) -> str:
        if not log_path.is_file():
            return ""
        try:
            with log_path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                end_pos = f.tell()
                pos = start_offset if 0 <= start_offset <= end_pos else max(0, end_pos - 4096)
                f.seek(pos)
                raw = f.read()
            txt = raw.decode("utf-8", errors="replace").strip()
            lines = [ln for ln in txt.splitlines() if ln.strip()]
            return "\n".join(lines[-max_lines:])
        except Exception:
            return ""

    def _wait_bat_service(
        self,
        service_name: str,
        proc: subprocess.Popen,
        port: int,
        log_path: Path,
        start_offset: int,
        timeout_sec: float,
        poll_interval: float,
        log_fn: Callable[[str], None],
    ) -> Tuple[bool, str]:
        t0 = time.time()
        next_beat = 5.0
        while time.time() - t0 < timeout_sec:
            if is_port_open("127.0.0.1", port):
                return True, ""
            new_tail = self._read_new_log_tail(log_path, start_offset, max_lines=12)
            if proc.poll() is not None or ("===== exit code " in new_tail):
                stop_process_tree(proc)
                detail = f"\n最近日志：\n{new_tail}" if new_tail else ""
                return False, f"{service_name} (:{port}) 启动失败并已退出。{detail}\n\n完整日志：{log_path}"
            elapsed = time.time() - t0
            if elapsed >= next_beat:
                log_fn(f"正在等待 {service_name} (:{port}) 加载模型与初始化... 已等待 {int(elapsed)}s / {int(timeout_sec)}s")
                next_beat += 5.0
            time.sleep(poll_interval)
        if is_port_open("127.0.0.1", port):
            return True, ""
        new_tail = self._read_new_log_tail(log_path, start_offset, max_lines=12)
        stop_process_tree(proc)
        detail = f"\n最近日志：\n{new_tail}" if new_tail else ""
        return False, f"{service_name} (:{port}) 启动超时（>{int(timeout_sec)}s）。{detail}\n\n请查看 logs\\{log_path.name}"

    def start_workflow(
        self,
        state_data: Dict[str, Any],
        log_fn: Callable[[str], None],
    ) -> Tuple[bool, str]:
        self.apply_prelaunch_config(state_data)
        log_fn("已写入开播配置（Ultralight 引擎 / 主播 / 人设 / 声线 / 知识库 / 平台）...")

        # 1. 启动 Ultralight 独立推理引擎 (:8020)，冷加载 HuBERT(1.26GB)+帧缓存约 15~60s，给予充裕 120s 等待
        if is_port_open("127.0.0.1", 8020):
            log_fn("检测到 Ultralight 引擎 (:8020) 已在运行，跳过重复启动。")
        else:
            eng_bat = ROOT_DIR / "scripts" / "run_engine.bat"
            if not eng_bat.exists():
                return False, f"找不到引擎启动脚本: {eng_bat}"
            eng_log = LOGS_DIR / "ultralight_engine.log"
            eng_off = eng_log.stat().st_size if eng_log.is_file() else 0
            log_fn("正在启动 Ultralight-Digital-Human 推理引擎 (:8020)（首次加载模型约需 15~50 秒）...")
            self.lt_proc = subprocess.Popen(
                ["cmd", "/k", str(eng_bat)],
                cwd=str(ROOT_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            ok_eng, err_eng = self._wait_bat_service(
                "Ultralight 引擎", self.lt_proc, 8020, eng_log, eng_off, 120.0, 1.0, log_fn
            )
            if not ok_eng:
                self.lt_proc = None
                return False, err_eng
            log_fn("Ultralight 引擎 (:8020) 已就绪！")

        # 2. 启动 AI-Live-Hub (:28910)
        if is_port_open("127.0.0.1", 28910):
            log_fn("检测到 AI-Live-Hub (:28910) 已在运行，正在热重载配置...")
            http_json("http://127.0.0.1:28910/api/reload", method="POST")
        else:
            hub_bat = ROOT_DIR / "scripts" / "run_hub.bat"
            if not hub_bat.exists():
                return False, f"找不到中控脚本: {hub_bat}"
            hub_log = LOGS_DIR / "hub.log"
            hub_off = hub_log.stat().st_size if hub_log.is_file() else 0
            log_fn("正在启动 AI-Live-Hub (:28910)...")
            self.hub_proc = subprocess.Popen(
                ["cmd", "/k", str(hub_bat)],
                cwd=str(ROOT_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            ok_hub, err_hub = self._wait_bat_service(
                "AI-Live-Hub 中控", self.hub_proc, 28910, hub_log, hub_off, 45.0, 0.8, log_fn
            )
            if not ok_hub:
                self.hub_proc = None
                return False, err_hub
            log_fn("AI-Live-Hub (:28910) 已就绪！")

        # 3. 同步并按需拉起 OBS Studio（先让 OBS 在 Hub :28910 就绪后启动，再切播同步场景，避免误报未开启 WebSocket）
        plat = str(state_data.get("platform") or "douyin")
        room_id = str(state_data.get("room_id") or "").strip()
        obs_prof = str(state_data.get("obs_profile") or "")
        obs_scene = str(state_data.get("obs_scene") or "")
        ori = str(state_data.get("orientation") or ("portrait" if "720x1280" in obs_prof else "landscape"))
        kb_doc = str(state_data.get("kb_doc") or "").strip()
        if obs_prof:
            ok_imp, msg_imp = import_obs_preset_to_obs(obs_prof, scene_name=obs_scene)
            log_fn(msg_imp if ok_imp else f"导入 OBS 预设警告: {msg_imp}")

        if state_data.get("auto_obs", True):
            obs_exe = str(state_data.get("obs_path") or "") or find_obs_executable()
            if is_port_open("127.0.0.1", 4455, 0.25):
                log_fn("检测到 OBS WebSocket (:4455) 已在线，将自动同步场景集、画幅与音视频...")
            else:
                ok_obs, msg_obs = launch_obs(obs_exe, profile=obs_prof, scene=obs_scene)
                log_fn(msg_obs)
                if ok_obs:
                    for _ in range(30):
                        if is_port_open("127.0.0.1", 4455, 0.3):
                            break
                        time.sleep(0.5)
                    if not is_port_open("127.0.0.1", 4455, 0.2):
                        log_fn("提示：OBS 已拉起但未开启 WebSocket (:4455)，请在 OBS 菜单「工具 → WebSocket 服务器设置」勾选开启。")

        # 4. 热重载并激活平台、知识库、主播形象与 OBS 场景联动
        http_json("http://127.0.0.1:28910/api/reload", method="POST", timeout=5.0)
        code, res = http_json(
            "http://127.0.0.1:28910/api/platform/switch",
            method="POST",
            payload={
                "platform": plat,
                "room_id": room_id,
                "sync_obs": True,
                "orientation": ori,
                "obs_profile": obs_prof,
                "obs_scene": obs_scene,
                "kb_doc": kb_doc,
                "kb_narration_mode": bool(state_data.get("kb_narration_mode", True)),
            },
            timeout=8.0,
        )
        if kb_doc:
            http_json(
                "http://127.0.0.1:28910/api/kb/control",
                method="POST",
                payload={"action": "select_doc", "doc": kb_doc},
                timeout=5.0,
            )
        st_code, st_data = http_json("http://127.0.0.1:28910/api/status", method="GET", timeout=3.0)
        cur_srv_aid = ((st_data or {}).get("status") or {}).get("avatar_id", "") if st_code == 200 and isinstance(st_data, dict) else ""
        avatar_id = str(state_data.get("avatar_id") or "").strip()
        if avatar_id and avatar_id != cur_srv_aid:
            http_json(
                "http://127.0.0.1:28910/api/avatar/switch",
                method="POST",
                payload={"avatar_id": avatar_id, "force": True, "announce": ""},
                timeout=6.0,
            )
        obs_msg_tail = res.get("obs_msg", "") if isinstance(res, dict) else ""
        log_fn(f"已激活平台 [{plat}] ({ori}) · Ultralight 主播 [{avatar_id}] · 讲稿 [{kb_doc or '默认'}]" + (f" · {obs_msg_tail}" if obs_msg_tail else ""))

        if is_port_open("127.0.0.1", 4455, 0.3):
            http_json("http://127.0.0.1:28910/api/obs/sync", method="POST", payload={"obs_profile": obs_prof, "obs_scene": obs_scene, "orientation": ori}, timeout=6.0)

        return True, "Ultralight 直播工作流启动完成！"

    def stop_workflow(self, log_fn: Callable[[str], None]) -> None:
        for name, proc in (("AI-Live-Hub", self.hub_proc), ("Ultralight-Engine", self.lt_proc)):
            if proc and proc.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    log_fn(f"已停止 {name} (PID {proc.pid})")
                except Exception as exc:
                    log_fn(f"停止 {name} 失败: {exc}")
        self.hub_proc = None
        self.lt_proc = None


def get_ultralight_python() -> Path:
    """定位独立 ultralight conda 环境的 python.exe。"""
    env = parse_env_bat()
    conda_root = env.get("CONDA_ROOT", "").strip()
    conda_env = env.get("CONDA_ENV", "ultralight").strip()
    if conda_root:
        cand = Path(conda_root) / "envs" / conda_env / "python.exe"
        if cand.is_file():
            return cand
    for cand in (
        Path(os.environ.get("USERPROFILE", "")) / "miniconda3" / "envs" / conda_env / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "miniconda3" / "envs" / conda_env / "python.exe",
        Path(r"C:\Users\anyong\miniconda3\envs\ultralight\python.exe"),
    ):
        if cand.is_file():
            return cand
    return Path(sys.executable)


def probe_video_info(video_path: str) -> Dict[str, Any]:
    """检测视频分辨率、FPS、帧数与是否包含音轨（防呆：无音轨不可从头训练）。"""
    p = Path(video_path)
    if not p.is_file():
        return {"ok": False, "error": "视频文件不存在"}
    info: Dict[str, Any] = {"ok": True, "has_audio": False, "fps": 0.0, "width": 0, "height": 0, "frames": 0, "duration_sec": 0.0}
    try:
        import cv2  # noqa: WPS433

        cap = cv2.VideoCapture(str(p))
        if cap.isOpened():
            info["fps"] = round(float(cap.get(cv2.CAP_PROP_FPS) or 0.0), 2)
            info["frames"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            info["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            info["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if info["fps"] > 0 and info["frames"] > 0:
                info["duration_sec"] = round(info["frames"] / info["fps"], 1)
        cap.release()
    except Exception:
        pass
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(p)],
                capture_output=True, text=True, timeout=10, creationflags=flags,
            )
            info["has_audio"] = "audio" in (r.stdout or "").lower()
        except Exception:
            pass
    else:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            try:
                r = subprocess.run([ffmpeg, "-i", str(p)], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10, creationflags=flags)
                info["has_audio"] = "Audio:" in (r.stderr or "")
            except Exception:
                pass
    return info


def list_ultralight_assets() -> List[Dict[str, Any]]:
    """汇总扫描 avatars/ 形象资产、wardrobe 元信息与真实动作短片数量。"""
    cfg = load_json(CONFIG_PATH, {})
    wardrobe_map = {str(x.get("id", "")).strip(): x for x in cfg.get("wardrobe", []) if x.get("id")}
    av_dir = get_avatars_dir()
    seen: set = set()
    rows: List[Dict[str, Any]] = []

    def _build_row(aid: str, sub: Optional[Path]) -> Dict[str, Any]:
        w = wardrobe_map.get(aid, {})
        meta = load_json(sub / "meta.json", {}) if (sub and (sub / "meta.json").is_file()) else {}
        has_onnx = bool(sub and (sub / "unet.onnx").is_file())
        has_pth = bool(sub and (sub / "ultralight.pth").is_file())
        img_dir = (sub / "full_body_img") if sub else None
        frames = int(meta.get("frames") or (len(list(img_dir.glob("*"))) if (img_dir and img_dir.is_dir()) else 0))
        act_dir = ACTION_DIR / aid
        act_cnt = len(list(act_dir.glob("act_*.mp4"))) if act_dir.is_dir() else 0
        asr = str(meta.get("asr") or w.get("asr") or "hubert")
        fps = int(meta.get("fps") or w.get("fps") or (20 if asr == "wenet" else 25))
        return {
            "id": aid,
            "name": str(meta.get("name") or w.get("name") or aid),
            "asr": asr,
            "fps": fps,
            "frames": frames,
            "width": int(meta.get("width") or 0),
            "height": int(meta.get("height") or 0),
            "has_onnx": has_onnx,
            "has_pth": has_pth,
            "ready": has_onnx or has_pth,
            "action_count": act_cnt,
            "tts_voice": w.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
            "sf_voice": w.get("sf_voice", ""),
            "base_avatar": str(meta.get("base_avatar") or w.get("base_avatar") or ""),
            "created_at": str(meta.get("updated_at") or meta.get("created_at") or ""),
            "path": str(sub) if sub else "",
        }

    if av_dir and av_dir.is_dir():
        for sub in sorted(av_dir.iterdir()):
            if sub.is_dir() and not sub.name.startswith(("__", ".")):
                seen.add(sub.name)
                rows.append(_build_row(sub.name, sub))
    for aid in wardrobe_map:
        if aid not in seen:
            rows.append(_build_row(aid, None))
    return rows


def delete_ultralight_avatar(avatar_id: str) -> Tuple[bool, str]:
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (avatar_id or "").strip())
    if not aid:
        return False, "无效的形象 ID"
    av_dir = get_avatars_dir()
    removed = False
    if av_dir and (av_dir / aid).is_dir():
        shutil.rmtree(av_dir / aid, ignore_errors=False)
        removed = True
    cfg = load_json(CONFIG_PATH, {})
    before = len(cfg.get("wardrobe", []))
    cfg["wardrobe"] = [x for x in cfg.get("wardrobe", []) if x.get("id") != aid]
    if cfg.get("livetalking", {}).get("avatar_id") == aid:
        next_ready = next((r["id"] for r in list_ultralight_assets() if r["ready"]), "demo_ul_host")
        cfg.setdefault("livetalking", {})["avatar_id"] = next_ready
    save_json(CONFIG_PATH, cfg)
    return True, f"已清理形象 [{aid}]（模型目录{'已删' if removed else '不存在'}，wardrobe 移除 {before - len(cfg['wardrobe'])} 条）"


def import_external_avatar_dir(src_dir: Path, avatar_id: str, name: str = "", asr: str = "hubert") -> Tuple[bool, str]:
    """从外部导入一个完整的 Ultralight 形象目录（含 full_body_img/landmarks/unet.onnx 或 .pth）。"""
    if not src_dir.is_dir():
        return False, f"源目录不存在: {src_dir}"
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (avatar_id or src_dir.name).strip())
    if not aid:
        return False, "目标英文 ID 不能为空且仅含英文、数字、下划线"
    has_img = (src_dir / "full_body_img").is_dir() or (src_dir / "dataset" / "full_body_img").is_dir()
    has_lms = (src_dir / "landmarks").is_dir() or (src_dir / "dataset" / "landmarks").is_dir()
    pth_src = src_dir / "ultralight.pth"
    if not pth_src.is_file():
        pth_cands = list(src_dir.glob("*.pth"))
        if pth_cands:
            pth_src = pth_cands[0]
    onnx_src = src_dir / "unet.onnx"
    if not onnx_src.is_file():
        onnx_cands = list(src_dir.glob("*.onnx"))
        if onnx_cands:
            onnx_src = onnx_cands[0]
    if not (has_img and has_lms and (pth_src.is_file() or onnx_src.is_file())):
        return False, "源目录必须包含 full_body_img、landmarks 以及 unet.onnx 或 .pth 权重文件"
    av_dir = get_avatars_dir() / aid
    if av_dir.resolve() != src_dir.resolve():
        if av_dir.exists():
            shutil.rmtree(av_dir)
        shutil.copytree(src_dir, av_dir)
        if not (av_dir / "ultralight.pth").is_file() and pth_src.is_file():
            shutil.copy2(src_dir / pth_src.name, av_dir / "ultralight.pth")
        if not (av_dir / "unet.onnx").is_file() and onnx_src.is_file():
            shutil.copy2(src_dir / onnx_src.name, av_dir / "unet.onnx")
    if not (av_dir / "meta.json").is_file():
        fps = 20 if asr == "wenet" else 25
        n_f = len(list((av_dir / "full_body_img").glob("*"))) if (av_dir / "full_body_img").is_dir() else 0
        save_json(av_dir / "meta.json", {
            "avatar_id": aid, "name": name.strip() or aid, "asr": asr, "fps": fps,
            "frames": n_f, "has_onnx": (av_dir / "unet.onnx").is_file(), "has_pth": (av_dir / "ultralight.pth").is_file(),
        })
    register_host_profile(aid, name or aid, name or aid, "zh-CN-XiaoxiaoNeural", "", "")
    return True, f"成功导入 Ultralight 形象 [{aid}] -> {av_dir}"


def engine_live_sessions() -> int:
    """数字人引擎当前在线的直播画面路数（引擎未启动返回 0）；用于训练前防呆提醒。"""
    if not is_port_open("127.0.0.1", 8020, 0.2):
        return 0
    code, res = http_json("http://127.0.0.1:8020/api/admin/sessions", timeout=2.0)
    if code != 200 or not isinstance(res, dict):
        return 0
    return sum(1 for x in (res.get("data") or {}).get("sessions", []) if x.get("live", True))


def spawn_ultralight_pipeline_proc(
    avatar_id: str,
    name: str,
    asr: str,
    video_path: str = "",
    idle_video: str = "",
    checkpoint: str = "",
    resume: str = "",
    epochs: int = 100,
    batch_size: int = 16,
    lr: float = 1e-3,
    export_only: bool = False,
    base_avatar: str = "",
) -> subprocess.Popen:
    py = get_ultralight_python()
    script = ROOT_DIR / "scripts" / "ultralight_pipeline.py"
    cmd = [
        str(py), str(script),
        "--avatar_id", avatar_id,
        "--name", name or avatar_id,
        "--asr", asr,
        "--epochs", str(epochs),
        "--batch_size", str(batch_size),
        "--lr", str(lr),
    ]
    if video_path:
        cmd += ["--video", video_path]
    if idle_video:
        cmd += ["--idle_video", idle_video]
    if checkpoint:
        cmd += ["--checkpoint", checkpoint]
    if resume:
        cmd += ["--resume", resume]
    if export_only:
        cmd += ["--export_only"]
    if base_avatar:
        cmd += ["--base_avatar", base_avatar]
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stop_process_tree(proc: Optional[subprocess.Popen]) -> None:
    if proc and proc.poll() is None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def run_ultralight_train_cli(
    avatar_id: str,
    name: str,
    asr: str,
    video_path: str,
    idle_video: str = "",
    checkpoint: str = "",
    epochs: int = 100,
    batch_size: int = 16,
    log_cb: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """在独立 ultralight conda 环境中调用 scripts/ultralight_pipeline.py。"""
    proc = spawn_ultralight_pipeline_proc(
        avatar_id=avatar_id, name=name, asr=asr, video_path=video_path,
        idle_video=idle_video, checkpoint=checkpoint, epochs=epochs, batch_size=batch_size,
    )
    last_lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        ln = line.rstrip("\r\n")
        last_lines.append(ln)
        if len(last_lines) > 30:
            last_lines.pop(0)
        if log_cb:
            log_cb(ln)
    rc = proc.wait()
    if rc == 0:
        return True, f"Ultralight 形象 [{avatar_id}] 训练与 ONNX 导出完成！"
    return False, "\n".join(last_lines[-8:]) or f"退出码 {rc}"


def run_tool_script_cli(rel_script: str, args: Optional[List[str]] = None, log_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """在独立 ultralight conda 环境执行维护脚本（download_models / check_env / test_keys）。"""
    py = get_ultralight_python()
    sp = ROOT_DIR / rel_script
    if not sp.is_file():
        return False, f"找不到脚本: {sp}"
    cmd = [str(py), str(sp)] + list(args or [])
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        ln = line.rstrip("\r\n")
        lines.append(ln)
        if log_cb:
            log_cb(ln)
    rc = proc.wait()
    return rc == 0, "\n".join(lines[-20:])


def fetch_hub_status() -> Optional[Dict[str, Any]]:
    """轮询本地 Ultralight 中控 :28910 状态与日志。"""
    if not is_port_open("127.0.0.1", 28910, 0.2):
        return None
    code, data = http_json("http://127.0.0.1:28910/api/status", method="GET", timeout=1.5)
    if code == 200 and isinstance(data, dict):
        st = dict(data.get("status") or data)
        st["logs"] = data.get("logs", [])
        return st
    return None


def fetch_hub_health() -> Optional[Dict[str, Any]]:
    if not is_port_open("127.0.0.1", 28910, 0.2):
        return None
    code, data = http_json("http://127.0.0.1:28910/api/health", method="GET", timeout=2.5)
    return data if (code == 200 and isinstance(data, dict)) else None


def save_scheduler_and_bg_cfg(sched_patch: Dict[str, Any], bg_patch: Dict[str, Any]) -> None:
    cfg = load_json(CONFIG_PATH, {})
    cfg.setdefault("scheduler", {}).update(sched_patch)
    cfg.setdefault("background", {}).update(bg_patch)
    save_json(CONFIG_PATH, cfg)
    if is_port_open("127.0.0.1", 28910, 0.2):
        if sched_patch:
            http_json("http://127.0.0.1:28910/api/settings", "POST", sched_patch, timeout=3.0)
        if bg_patch:
            http_json("http://127.0.0.1:28910/api/bg/config", "POST", bg_patch, timeout=3.0)


def _overlay_pairs(kind: str) -> List[Tuple[str, str]]:
    if kind == "font":
        return [(f["id"], f["label"]) for f in OVERLAY_FONT_PRESETS]
    if kind == "effect":
        return [(e["id"], e["label"]) for e in OVERLAY_EFFECT_PRESETS]
    if kind == "position":
        return BOARD_POSITION_OPTIONS
    if kind == "animation":
        return OVERLAY_ANIMATION_OPTIONS
    raise ValueError(f"Unknown overlay option kind: {kind}")


def overlay_labels(kind: str) -> List[str]:
    """返回指定字幕/字体维度的中文选项列表。"""
    return [label for _, label in _overlay_pairs(kind)]


def overlay_id_to_label(kind: str, option_id: str) -> str:
    """将内部枚举 ID 转为展示文案。"""
    pairs = _overlay_pairs(kind)
    return {k: v for k, v in pairs}.get(option_id, pairs[0][1])


def overlay_label_to_id(kind: str, label: str) -> str:
    """将展示文案或合法 ID 转回内部枚举 ID。"""
    pairs = _overlay_pairs(kind)
    by_label = {v: k for k, v in pairs}
    if label in by_label:
        return by_label[label]
    if label in {k for k, _ in pairs}:
        return label
    return pairs[0][0]


def get_overlay_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读取并校验直播舞台章节卡、口播字幕与免费字体配置。"""
    src = cfg if cfg is not None else load_json(CONFIG_PATH, {})
    return sanitize_overlay_cfg(src.get("overlay") if isinstance(src, dict) else {})


def resolve_overlay_font_file(font_id: str) -> Optional[Path]:
    """解析免费商用字体在本地磁盘的字体文件路径。"""
    return resolve_font_path(font_id, FONTS_DIR)


def save_overlay_cfg(patch: Dict[str, Any], apply_effect_preset: bool = False) -> Dict[str, Any]:
    """保存字幕与字体配置到 config.json，并在后端运行时实时热推送给 OBS 舞台。"""
    cfg = load_json(CONFIG_PATH, {})
    cur = get_overlay_cfg(cfg)
    incoming = dict(patch or {})
    if apply_effect_preset:
        eff_id = str(incoming.get("effect_preset") or cur.get("effect_preset") or "classic_dark")
        eff = OVERLAY_EFFECT_BY_ID.get(eff_id)
        if eff:
            for k in ("text_color", "stroke_color", "stroke_width", "bg_opacity", "font_weight"):
                incoming[k] = eff[k]
    merged = sanitize_overlay_cfg(incoming, base=cur)
    cfg["overlay"] = merged
    save_json(CONFIG_PATH, cfg)
    if is_port_open("127.0.0.1", 28910, 0.2):
        http_json("http://127.0.0.1:28910/api/overlay/config", "POST", merged, timeout=3.0)
    return merged


def upload_siliconflow_clone_voice(cfg: Dict[str, Any], audio_path: Path, custom_name: str, ref_text: str) -> Tuple[bool, str]:
    """纯粹用标准库向硅基流动上传 8~10 秒人声做 CosyVoice2 声音克隆，返回 (ok, uri_or_error)。"""
    sf = cfg.get("tts", {}).get("siliconflow", {})
    key = str(sf.get("api_key") or "").strip()
    if not key or key.startswith("填入"):
        return False, "请先在 config.json 填写 tts.siliconflow.api_key"
    if not audio_path.is_file():
        return False, f"找不到音频文件: {audio_path}"
    cname = re.sub(r"[^a-zA-Z0-9_\-]", "", (custom_name or "").strip())
    if not cname or not ref_text.strip():
        return False, "音色英文名与参考原话均不能为空"

    boundary = f"----YsaizbBoundary{uuid.uuid4().hex}"
    fields = [
        ("model", str(sf.get("model") or "FunAudioLLM/CosyVoice2-0.5B")),
        ("customName", cname),
        ("text", ref_text.strip()),
    ]
    parts: List[bytes] = []
    for k, v in fields:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    mime = "audio/mpeg" if audio_path.suffix.lower() == ".mp3" else "audio/wav"
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{audio_path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8")
        + audio_path.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    url = str(sf.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/") + "/uploads/audio/voice"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            res = json.loads(resp.read().decode("utf-8", errors="replace"))
            uri = str(res.get("uri") or "")
            if uri:
                return True, uri
            return False, f"上传成功但未返回 uri: {res}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as exc:
        return False, f"上传失败: {exc}"
