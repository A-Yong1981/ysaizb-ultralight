# -*- coding: utf-8 -*-
"""
泳昇数字人 · AI Live Hub 中控网关
=================================
融合三份技术文档：
  1) 基础中控：免费 LLM 容灾池 + 违禁词过滤 + 防截断队列 + 洗牌暖场 + SSE 广播 + WebRTC 代理自动捕获 sessionid
  2) 克隆音色：tts_engine.py（硅基流动 CosyVoice2 / 本地 GPT-SoVITS v2 FP16）-> /humanaudio，失败自动降级 Edge-TTS(/human)
  3) 衣柜换装：wardrobe 配置 + 弹幕点装(冷却锁) + OBS 定格无黑屏重连 + WebUI 直接上传视频生成 wav2lip256 形象

RTX 2080 8GB 原则：中控本身 0 显存；GPU 只留给 LiveTalking(wav2lip256) 推理与 OBS NVENC。
"""
import asyncio
import copy
import hashlib
import json
import os
import random
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from profile_manager import SinglePlatformManager, OBS_SCENE_MAP
from kb_engine import KnowledgeBaseEngine, match_faq_list
from script_converter import (
    IMPORT_EXTS,
    STYLE_GUIDES,
    convert_article_to_live_script,
    extract_file_bytes,
    fetch_webpage_text,
    import_markdown_document,
)
from health_monitor import SystemHealthMonitor
from tts_engine import TTSEngine, is_placeholder_key
from overlay_presets import (
    OVERLAY_ANIMATION_OPTIONS,
    OVERLAY_EFFECT_PRESETS,
    OVERLAY_FONT_PRESETS,
    sanitize_overlay_cfg,
)
from topic_router import TopicIndex, fill_template, has_request_intent
import config_guide
from app_version import APP_NAME, APP_VERSION

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
# 全新克隆 / 首次运行：缺 config.json、platform_profiles.json 时自动从 *.example.json 复制
config_guide.ensure_runtime_configs(BASE_DIR)
CONFIG_PATH = BASE_DIR / "config.json"
MODERATION_PATH = BASE_DIR / "moderation.json"
WARMUP_PATH = BASE_DIR / "warmup_topics.json"
STATIC_DIR = BASE_DIR / "static"

tts_engine = TTSEngine(BASE_DIR / "tts_cache")
prof_mgr = SinglePlatformManager(
    BASE_DIR / "platform_profiles.json",
    BASE_DIR / "platform_state.json",
    BASE_DIR,
    BASE_DIR.parent / "logs",
)
health_mon = SystemHealthMonitor(BASE_DIR)
scheduler_task: Optional[asyncio.Task] = None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ============================================================
# 全局状态
# ============================================================
class LiveState:
    def __init__(self) -> None:
        self.config: Dict[str, Any] = load_json(CONFIG_PATH, {})
        self.moderation: Dict[str, Any] = load_json(MODERATION_PATH, {})
        self.warmup_master: List[str] = load_json(WARMUP_PATH, [])
        self.warmup_bag: List[str] = []

        self.session_id: Optional[str] = None
        self.lt_online: bool = False
        self.is_speaking: bool = False
        self.min_busy_until: float = 0.0
        self.last_idle_since: float = time.time()

        self.active_provider_idx: int = 0
        self.last_provider_name: str = "待命（有提问才调用）"

        # 礼物优先队列与普通弹幕队列
        self.priority_queue: deque = deque()
        self.normal_queue: deque = deque()

        # 最近日志与当前字幕
        self.logs: deque = deque(maxlen=120)
        self.current_subtitle: str = ""
        self.current_question: str = ""

        # 一键换装状态
        self.switching_avatar: bool = False
        self.last_switch_ts: float = 0.0
        self.pending_announce: str = ""

        # 知识库解说
        self.kb_mode_override: Optional[bool] = None   # WebUI 临时切换「章节连播 / 仅答疑」，切平台时复位
        self.beats_since_warmup: int = 0
        self.converting: bool = False

        # 观众主题点播（插播计划）：礼物 > 点播 > 普通提问 > 章节连播
        self.topic_active: Optional[Dict[str, Any]] = None
        self.topic_pending: deque = deque()
        self.topic_last_played: Dict[str, float] = {}
        self.topic_beats_since_answer: int = 0
        self.tts_prefetching: set = set()
        self.preview_sessions: set = set()     # 预览页的会话 ID（镜像或临时主画面）

        # 健康诊断与响应提速
        self.last_tts_tag: str = ""
        self.last_danmaku_ts: float = 0.0
        self.current_speech_kind: str = ""
        self.wake_event: Optional[asyncio.Event] = None

        # SSE 订阅客户端
        self.sse_queues: List[asyncio.Queue] = []

    def notify_wake(self) -> None:
        if self.wake_event is not None:
            self.wake_event.set()

    def reload_files(self) -> None:
        self.config = load_json(CONFIG_PATH, {})
        self.moderation = load_json(MODERATION_PATH, {})
        self.warmup_master = load_json(WARMUP_PATH, [])
        self.warmup_bag.clear()

    def pop_warmup(self) -> Optional[str]:
        """洗牌式不重复暖场：一轮说完才重新洗牌。"""
        if not self.warmup_master:
            return None
        if not self.warmup_bag:
            self.warmup_bag = list(self.warmup_master)
            random.shuffle(self.warmup_bag)
        return self.warmup_bag.pop()

    async def broadcast(self, event_type: str, payload: Dict[str, Any]) -> None:
        msg = {"type": event_type, "ts": time.strftime("%H:%M:%S"), **payload}
        stale = []
        for q in self.sse_queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                stale.append(q)
        for q in stale:
            if q in self.sse_queues:
                self.sse_queues.remove(q)

        if event_type == "board_update":
            await broadcast_bg_update(payload.get("bg_video", ""))

    async def add_log(self, level: str, text: str) -> None:
        entry = {"ts": time.strftime("%H:%M:%S"), "level": level, "text": text}
        self.logs.append(entry)
        print(f"[{entry['ts']}][{level}] {text}", flush=True)
        await self.broadcast("log", entry)

    def snapshot(self) -> Dict[str, Any]:
        sched = self.config.get("scheduler", {})
        tts_cfg = self.config.get("tts", {})
        return {
            "background": resolve_bg_payload(kb_engine.last_board.get("bg_video", "")),
            "session_id": self.session_id,
            "lt_online": self.lt_online,
            "is_speaking": self.is_speaking,
            "auto_reply": sched.get("auto_reply", True),
            "auto_warmup": sched.get("auto_warmup", True),
            "gift_interrupt_narration": sched.get("gift_interrupt_narration", True),
            "warmup_interval_sec": sched.get("warmup_interval_sec", 25),
            "priority_queue_len": len(self.priority_queue),
            "normal_queue_len": len(self.normal_queue),
            "last_provider_name": self.last_provider_name,
            "current_subtitle": self.current_subtitle,
            "current_question": self.current_question,
            "stage_panel": build_stage_panel(),
            "overlay": resolve_overlay_cfg(),
            # 单平台切播
            "active_platform": prof_mgr.active_key,
            "active_platform_label": prof_mgr.get_active().get("label", prof_mgr.active_key),
            "active_room": prof_mgr.active_room,
            "collector_running": prof_mgr.collector_running(),
            "available_platforms": prof_mgr.list_profiles(),
            "orientation": prof_mgr.get_active().get("orientation", "landscape"),
            "platform_lang": plat_lang(),
            "last_obs_msg": prof_mgr.last_obs_msg,
            "last_collector_msg": prof_mgr.last_collector_msg,
            # 知识库
            "kb": kb_snapshot(),
            "kb_board": render_board(kb_engine.last_board) if narration_active() else None,
            "topic": topic_snapshot(),
            "preview": preview_snapshot(),
            "app_version": APP_VERSION,
            "converting": self.converting,
            # TTS
            "tts_mode": tts_cfg.get("mode", "edgetts"),
            "tts_voice_uri": tts_cfg.get("siliconflow", {}).get("voice", ""),
            "tts_emotion": tts_cfg.get("siliconflow", {}).get("emotion_prefix", ""),
            # 衣柜 / 多主播
            "host_name": self.config.get("persona", {}).get("name", "主播"),
            "avatar_id": self.config.get("livetalking", {}).get("avatar_id", ""),
            "switching_avatar": self.switching_avatar,
            "wardrobe": effective_wardrobe(self.config),
            "action_media_status": get_action_media_status(self.config),
            "enable_danmaku_switch": self.config.get("wardrobe_settings", {}).get(
                "enable_danmaku_switch", True
            ),
        }


state = LiveState()
kb_engine = KnowledgeBaseEngine(BASE_DIR / "kb_data", state.config.get("knowledge_base", {}))
topic_index = TopicIndex(BASE_DIR / "kb_data", state.config.get("knowledge_base", {}))
http_client: Optional[httpx.AsyncClient] = None


# ============================================================
# 平台适配：运行时叠加（不改写 config.json 的基础人设 / TTS）
# ============================================================
LANG_TEXT = {
    "zh": {
        "no_key": "谢谢{user}的提问，{text}这个问题有意思，等我查查资料，下次直播给你讲明白。",
        "all_fail": "谢谢{user}的提问呀，刚才公屏有点快，再打一遍我马上给你讲。",
        "gift": "哇，感谢{user}送出的{num}个{gift}，知识就是力量，老板就是能量，想问啥尽管问！",
        "gift_banner": "感谢 {user} 赠送 {gift} x{num}",
        "user_prompt": "观众【{user}】在公屏说：{text}",
        "hint": "（请自然地{hint}）",
        "warmup_banner": "主播暖场互动",
        "safe_banner": "温馨提示 · 本直播间面向成年观众",
        "resume": "好，咱们回到「{chapter}」接着讲。",
        "loop_restart": "这一轮内容讲完啦，新来的朋友别走，咱们从头再讲一遍。",
        "narration_banner": "正在讲解 · {chapter}",
        "kb_rule": "\n\n【本直播间讲稿资料】\n{refs}\n回答时优先依据上面的资料，用口语讲清楚；资料里没有的数据和结论不要编；资料没涉及的问题就简短回应，再自然地拉回当前话题。",
    },
    "en": {
        "no_key": "Thanks {user} for the question! Great one, keep them coming.",
        "all_fail": "Thanks {user}! Chat is moving fast, could you send that again?",
        "gift": "Wow, thank you {user} for the {num} {gift}! You just powered up the science lab!",
        "gift_banner": "Thanks {user} for {gift} x{num}",
        "user_prompt": "Viewer [{user}] says in chat: {text}",
        "hint": " (Naturally respond by: {hint})",
        "warmup_banner": "Live Science",
        "safe_banner": "Friendly reminder · adults only",
        "resume": "Okay, back to {chapter}. ",
        "loop_restart": "That wraps up this round. New friends, stick around, we are starting again from the top. ",
        "narration_banner": "Now explaining · {chapter}",
        "kb_rule": "\n\nReference notes from this stream:\n{refs}\nPrefer these notes, never invent facts that are not in them, and if the question is not covered, answer briefly and steer back to the topic.",
    },
}


def plat() -> Dict[str, Any]:
    return prof_mgr.get_active() or {}


def plat_lang() -> str:
    lang = plat().get("lang", "zh")
    return lang if lang in LANG_TEXT else "zh"


def persona_name() -> str:
    persona = state.config.get("persona", {})
    if plat_lang() == "en":
        return persona.get("name_en") or "Xiaoya"
    return persona.get("name", "小雅")


def T(key: str, **kw: Any) -> str:
    tpl = (plat().get("texts") or {}).get(key) or LANG_TEXT[plat_lang()][key]
    return fill_template(tpl, **kw)   # 不用 str.format：平台配置里的模板不能做属性注入


def fill_name(text: str) -> str:
    return str(text or "").replace("{name}", persona_name())


def render_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(panel or {})
    for k in ("left_title", "right_title", "compliance_badge"):
        if k in out:
            out[k] = fill_name(out[k])
    for k in ("left_lines", "right_lines"):
        out[k] = [fill_name(x) for x in out.get(k, [])]
    return out


def is_ai_popsci_context() -> bool:
    """判断当前播讲主题是否为默认 AI 科普专场（区分说书/游戏/硬件/自定义主题，避免强行串台）。"""
    doc = Path(str(kb_engine.active_doc or kb_doc_for_platform() or "").replace("\\", "/")).name.lower()
    if doc:
        if doc.startswith(("01_", "02_", "03_", "04_")):
            return True
        if any(k in doc for k in ("ai_", "popsci", "antifraud", "adults_family", "parents_guide", "glossary")):
            return True
        return False
    persona = state.config.get("persona", {})
    txt = f"{persona.get('name', '')} {persona.get('style', '')} {persona.get('system_prompt', '')}".lower()
    if any(k in txt for k in ("说书", "评书", "三国", "故事", "游戏", "攻略", "硬件", "装机", "storyteller", "game", "hardware")):
        return False
    return True


def build_stage_panel() -> Dict[str, Any]:
    """根据当前主题与人设生成左右信息卡与角标（非 AI 科普专场不再硬贴 AI 科普标题）。"""
    if is_ai_popsci_context() or plat_lang() == "en":
        return render_panel(plat().get("stage_panel") or state.config.get("stage_panel", {}))
    name = persona_name()
    style = str(state.config.get("persona", {}).get("style", "") or "").strip()
    ch_lines = [fill_name(c.get("title", "")) for c in (kb_engine.chapters or [])[:3] if c.get("title")]
    sec = str((kb_engine.last_board or {}).get("section") or "").strip()
    if sec and sec not in ch_lines and len(ch_lines) < 3:
        ch_lines.append(fill_name(sec))
    if not ch_lines:
        ch_lines = [x.strip() for x in re.split(r"[·•|｜]", style) if x.strip()][:3] or [f"{name}主题专场 · 欢迎互动提问"]
    return {
        "left_title": f"{name} · 主题专场",
        "left_lines": ch_lines[:3],
        "right_title": "直播间互动与公约",
        "right_lines": [
            "仅面向成年观众 · 纯口语实时互动",
            f"欢迎发送弹幕与{name}交流点播",
            "文明观播 · 勿发个人隐私与联系方式",
        ],
        "compliance_badge": f"AI 实时生成画面 · {name}专场",
    }


def _topic_warmup_pool() -> List[str]:
    """为非默认 AI 科普的主播与讲稿动态生成贴合人设+讲稿章节的暖场话术。"""
    name = persona_name()
    style = str(state.config.get("persona", {}).get("style", "") or "").strip()
    chapters = [fill_name(c.get("title", "")) for c in (kb_engine.chapters or []) if c.get("title")]
    ch_hint = re.sub(r"^第[一二三四五六七八九十百\d]+[章节回]\s*", "", chapters[0]).strip() if chapters else ""
    doc_name = Path(str(kb_engine.active_doc or "").replace("\\", "/")).name.lower()
    if any(k in (style + ch_hint + doc_name) for k in ("说书", "评书", "三国", "storyteller")):
        subject = ch_hint or "古今典故"
        return [
            f"醒木一拍，欢迎刚进直播间的各位成年朋友！今天{name}带您细品《{subject}》，先沏壶好茶咱们接着往下听。",
            f"各位听友要是觉得这段评书对味，给{name}点点关注不迷路，想听哪个经典桥段随时打在公屏上。",
            f"书接上文，正讲到《{subject}》的关键处，您对里头的人物谋略有什么看法，公屏一起聊聊。",
            f"本茶馆专场面向成年听友纯公益说书，不推收费课，欢迎留步慢慢品故事。",
        ]
    if any(k in (style + ch_hint + doc_name) for k in ("游戏", "上分", "gaming")):
        subject = ch_hint or style or "游戏攻略"
        return [
            f"欢迎新来的小伙伴！我是{name}，正在分享「{subject}」，卡关或者想练哪套阵容直接扣在公屏上。",
            f"觉得这波思路有用的朋友点个赞支持一下，{name}继续带大家拆解实战细节。",
            f"本直播间面向成年玩家纯聊技巧整活，不引导充值代练，想看什么打法弹幕刷起来！",
        ]
    if any(k in (style + ch_hint + doc_name) for k in ("硬件", "装机", "hardware", "显卡", "cpu")):
        subject = ch_hint or style or "装机避坑"
        return [
            f"欢迎各位朋友！我是{name}，今天专聊「{subject}」，把预算和用途打在公屏我帮您避坑参谋。",
            f"配电脑只看真实性价比，不盲从高价配件；有具体型号对比问题尽管在弹幕提问！",
        ]
    subject = ch_hint or style or "精彩专题"
    return [
        f"欢迎新进直播间的各位成年朋友！我是{name}，正在为您开讲「{subject}」，有想聊的内容欢迎打在公屏。",
        f"觉得内容有帮助的话可以给{name}点个赞、加个关注，咱们继续把「{subject}」聊深聊透。",
        f"温馨提醒，本直播间仅面向成年观众交流分享，欢迎大家文明互动。",
    ]


def effective_system_prompt() -> str:
    base = str(state.config.get("persona", {}).get(
        "system_prompt", "你是直播间 AI 主播，回答控制在 40 字以内，口语化，不要用表情和符号。"
    ) or "").strip()
    p = plat()
    # 海外纯英文平台（如 TikTok）才允许使用平台英文 system_prompt 替换；中文平台始终以主播自身人设为核心
    if plat_lang() == "en" and p.get("system_prompt"):
        sp = str(p["system_prompt"])
    else:
        sp = base
        chapters = [c.get("title", "") for c in (kb_engine.chapters or []) if c.get("title")]
        topic_line = ""
        if chapters:
            topic_line = f"\n【当前播讲主题】正在讲《{fill_name(chapters[0])}》，回答必须紧扣当前人设语气与主题风格，不要擅自跑偏到其他题材。"
        addon = str(p.get("prompt_addon", "") or "").strip()
        sp = f"【核心主播人设（必须严格遵守人设称呼、说话口吻与行文风格）】\n{sp}{topic_line}"
        if addon:
            sp += f"\n\n{addon}"
        return fill_name(sp)
    addon = str(p.get("prompt_addon", "") or "").strip()
    return fill_name((sp + ("\n" + addon if addon else "")).strip())


def effective_tts_cfg() -> Dict[str, Any]:
    cfg = copy.deepcopy(state.config.get("tts", {"mode": "edgetts"}))
    p = plat()
    sf = cfg.setdefault("siliconflow", {})
    if p.get("tts_emotion_prefix"):
        sf["emotion_prefix"] = p["tts_emotion_prefix"]
    elif p.get("lang") == "en":
        sf["emotion_prefix"] = ""  # 中文情绪指令不用于英文平台
    if p.get("tts_voice"):
        sf["voice"] = p["tts_voice"]
    if p.get("tts_speed"):
        sf["speed"] = float(p["tts_speed"])
        cfg.setdefault("gptsovits", {})["speed_factor"] = float(p["tts_speed"])
    if p.get("lang") == "en":
        cfg.setdefault("gptsovits", {})["text_lang"] = "en"
    return cfg


def effective_edge_voice() -> str:
    """优先级：中文/双语平台始终以当前主播/面板选定声线为主，海外英文平台才切英文声线。"""
    cfg_voice = str(state.config.get("livetalking", {}).get("tts_voice", "") or "").strip()
    p_voice = str(plat().get("edge_voice", "") or "").strip()
    if plat_lang() == "en":
        if cfg_voice.lower().startswith("en-"):
            return cfg_voice
        if any(m in cfg_voice.lower() for m in ("yun", "wanlung", "guy", "andrew", "christopher")):
            return "en-US-AndrewNeural"
        return p_voice or "en-US-JennyNeural"
    return cfg_voice or p_voice or "zh-CN-XiaoxiaoNeural"


def output_blocked(reply: str) -> Optional[str]:
    words = list(state.moderation.get("output_blocked_keywords", [])) + list(plat().get("output_blocked_keywords", []))
    for w in words:
        if w and w.lower() in reply.lower():
            return w
    return None


def apply_platform_runtime() -> None:
    """按当前活跃讲稿 + 主播人设 + 平台重建暖场池，防止不同主题串台（说书场不再插入 AI 科普）。"""
    p = plat()
    if plat_lang() == "en":
        topics = [fill_name(x) for x in list(p.get("warmup_topics", [])) if x]
    elif is_ai_popsci_context():
        base = load_json(WARMUP_PATH, []) if p.get("use_base_warmup", True) else []
        topics = [fill_name(x) for x in list(base) + list(p.get("warmup_topics", [])) if x]
    else:
        topics = [fill_name(x) for x in _topic_warmup_pool() if x]
    state.warmup_master = topics
    state.warmup_bag.clear()


# ============================================================
# 违禁词过滤与路由
# ============================================================
PII_DIGITS_RE = re.compile(r"\d{7,}")


def match_safety_faq(text: str) -> Optional[Dict[str, Any]]:
    """全平台通用的安全兜底问答（moderation.json 的 safety_faqs），不依赖当前平台绑定的是哪份讲稿。"""
    return match_faq_list(state.moderation.get("safety_faqs", []), text)


def check_moderation(text: str) -> Optional[str]:
    clean = text.strip()
    if not clean:
        return "空内容"
    max_len = int(plat().get("max_text_length") or state.moderation.get("max_text_length", 80))
    if len(clean) > max_len:
        return "超过最大长度"
    for kw in list(state.moderation.get("blocked_keywords", [])) + list(plat().get("extra_blocked_keywords", [])):
        if kw and kw.lower() in clean.lower():
            return f"命中屏蔽词:{kw}"
    return None


def match_action_route(text: str) -> Optional[Dict[str, Any]]:
    for route in state.moderation.get("action_routes", []):
        for kw in route.get("keywords", []):
            if kw and kw in text:
                return route
    return None


def match_gift_interaction(kind: str, text: str = "", gift_item: str = "") -> Optional[Dict[str, Any]]:
    """匹配礼物或互动弹幕（墨镜 / 奶茶 / 比心 / 挥手 / 点赞），驱动 0 延迟舞台动作与极速口播。"""
    gi = state.config.get("gift_interactions", {})
    if not gi.get("enabled", True):
        return None
    rules = gi.get("rules", [])
    if kind == "gift":
        g = (gift_item or "").strip().lower()
        for r in rules:
            for kw in r.get("match_gifts", []):
                if kw and kw.lower() in g:
                    return r
        return gi.get("default_gesture") or None
    t = (text or "").strip().lower()
    if not t:
        return None
    for r in rules:
        for kw in r.get("match_chat", []):
            if kw and kw.lower() in t:
                return r
    return None


SHORT_QUESTION_HINTS = ("?", "？", "吗", "么", "怎么", "怎样", "如何", "什么", "啥", "为啥", "为什么", "ai", "防骗", "作业", "验证码", "换脸", "课")


def should_skip_dense_search(text: str) -> bool:
    """极短闲聊弹幕跳过 bge-m3 外网向量往返，省去 0.3~0.6s 延迟。"""
    if not state.config.get("scheduler", {}).get("short_chat_skip_dense", True):
        return False
    t = text.strip().lower()
    if len(t) > 5:
        return False
    return not any(h in t for h in SHORT_QUESTION_HINTS)


def match_wardrobe_outfit(text: str) -> Optional[Dict[str, Any]]:
    """弹幕点装：需包含“换/穿”字，或弹幕正好等于服装名/关键词，避免误触发。"""
    if not state.config.get("wardrobe_settings", {}).get("enable_danmaku_switch", True):
        return None
    t = text.strip()
    has_verb = ("换" in t) or ("穿" in t)
    for outfit in state.config.get("wardrobe", []):
        names = [outfit.get("name", "")] + list(outfit.get("keywords", []))
        for kw in names:
            kw = (kw or "").strip()
            if not kw:
                continue
            if (kw in t and has_verb) or t == kw:
                return outfit
    return None


def kb_cfg_effective() -> Dict[str, Any]:
    """基础 knowledge_base 配置 + 当前平台的 kb_* 覆盖 + WebUI 临时开关；向量 Key 留空时复用硅基流动 Key。"""
    base = copy.deepcopy(state.config.get("knowledge_base", {}))
    p = plat()
    if "kb_enabled" in p:
        base["enabled"] = bool(p["kb_enabled"])
    if "kb_narration_mode" in p:
        base["narration_mode"] = bool(p["kb_narration_mode"])
    if state.kb_mode_override is not None:
        base["narration_mode"] = state.kb_mode_override
    emb = base.setdefault("embedding", {})
    if is_placeholder_key(emb.get("api_key")):
        for prov in state.config.get("llm_providers", []):
            if "siliconflow" in str(prov.get("base_url", "")) and not is_placeholder_key(prov.get("api_key")):
                emb["api_key"] = prov["api_key"]
                break
        else:
            sf_key = state.config.get("tts", {}).get("siliconflow", {}).get("api_key")
            if not is_placeholder_key(sf_key):
                emb["api_key"] = sf_key
    return base


def kb_doc_for_platform() -> str:
    if not kb_cfg_effective().get("enabled", True):
        return ""
    # 单一事实来源：以知识库当前激活讲稿为准，平台预设仅在未指定讲稿时兜底，防止切平台覆盖用户所选讲稿
    active = str(state.config.get("knowledge_base", {}).get("active_doc", "") or "").strip()
    return active or str(plat().get("kb_doc") or "").strip()


def kb_ready() -> bool:
    return bool(kb_cfg_effective().get("enabled", True)) and bool(kb_engine.chunks or kb_engine.faqs)


def narration_active() -> bool:
    c = kb_cfg_effective()
    return bool(c.get("enabled", True) and c.get("narration_mode", True)
                and not kb_engine.paused and not kb_engine.building and kb_engine.chunks)


def render_board(b: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    b = dict(b or {})
    b["chapter"] = fill_name(b.get("chapter", ""))
    b["section"] = fill_name(b.get("section", ""))
    b["points"] = [fill_name(x) for x in b.get("points", [])]
    return b


def kb_snapshot() -> Dict[str, Any]:
    c = kb_cfg_effective()
    snap = kb_engine.snapshot()
    snap.update({
        "enabled": bool(c.get("enabled", True)),
        "narration_mode": bool(c.get("narration_mode", True)),
        "narration_active": narration_active(),
        "bound_doc": kb_doc_for_platform(),
        "embedding_ready": not is_placeholder_key(c.get("embedding", {}).get("api_key")),
        "board": render_board(snap.get("board")),
    })
    return snap


async def kb_load_for_platform(reset_cursor: bool) -> str:
    """按当前平台加载其绑定讲稿（命中缓存秒切；讲稿改过或新配了向量 Key 自动重建）。"""
    c = kb_cfg_effective()
    kb_engine.update_config(c)
    if not c.get("enabled", True):
        return "知识库：本平台未启用"
    doc = kb_doc_for_platform()
    if not doc:
        return "知识库：本平台未绑定讲稿"
    res = await kb_engine.build_index(http_client, doc_name=doc, force=False, reset_cursor=reset_cursor)
    await state.broadcast("board_update", render_board(kb_engine.last_board))
    if res.get("code") != 0:
        return f"知识库：{res.get('msg')}"
    mode = "bge-m3 向量+词法" if res.get("vectorized") else "CPU 词法"
    return (f"讲稿 [{doc}] {res.get('chapters', 0)} 章 / {res.get('chunks', 0)} 段 / FAQ {res.get('faqs', 0)} 条，"
            f"从第 {res.get('cursor', 0) + 1} 段讲（{res.get('msg')}，{mode}）")


# ============================================================
# 观众主题点播：按主题索引调取知识库 → AI/规则判定点播 → 插播口播 → 讲完自动回原章节
# ============================================================
def topic_sync_cfg() -> None:
    topic_index.update_config(kb_cfg_effective())


def llm_providers_ready() -> List[Dict[str, Any]]:
    return [p for p in state.config.get("llm_providers", [])
            if p.get("enabled") and not is_placeholder_key(p.get("api_key"))]


def topic_snapshot() -> Dict[str, Any]:
    a = state.topic_active
    cfg = topic_index.cfg
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "detect_mode": cfg.get("detect_mode", "auto"),
        "ai_ready": bool(llm_providers_ready()),
        "total_topics": len(topic_index.topics),
        "active": ({"tid": a["tid"], "title": a["title"], "user": a["user"], "idx": a["idx"],
                    "total": len(a["beats"]), "source": a.get("source", "")} if a else None),
        "pending": [{"tid": p["tid"], "title": p["title"], "user": p["user"]} for p in state.topic_pending],
    }


async def topic_route(text: str) -> Dict[str, Any]:
    topic_sync_cfg()
    return await topic_index.route(http_client, text, llm_providers_ready(), active_doc=kb_engine.active_doc)


async def soft_interrupt_for_priority() -> None:
    """礼物 / 点播 / 互动：柔和打断当前讲稿、暖场或点播段落，之后自动从断点接着讲。"""
    if not (state.config.get("scheduler", {}).get("gift_interrupt_narration", True) and state.is_speaking):
        return
    kind = state.current_speech_kind
    if kind not in ("narration", "warmup", "topic"):
        return
    if kind == "topic" and state.topic_active:
        # 被打断的这段点播内容稍后重讲，并加一句“咱们接着说……”
        state.topic_active["idx"] = max(0, int(state.topic_active["idx"]) - 1)
        state.topic_active["resume_needed"] = True
    elif narration_active():
        kb_engine.mark_interrupted()
    await lt_interrupt()


def schedule_tts_prefetch(text: str, max_chars: Optional[int] = None) -> None:
    """克隆声音模式下，提前把“下一段”合成进缓存：当前段一播完，下一段几乎零等待开口。"""
    tts_cfg = effective_tts_cfg()
    if tts_cfg.get("mode", "edgetts") == "edgetts" or not tts_cfg.get("enable_cache", True) or http_client is None:
        return
    clean = sanitize_speech_text(fill_name(text), max_chars)
    if not clean or clean in state.tts_prefetching or len(state.tts_prefetching) >= 2:
        return
    state.tts_prefetching.add(clean)

    async def _run() -> None:
        try:
            await tts_engine.synthesize(http_client, tts_cfg, clean, use_cache=True)
        except Exception:
            pass
        finally:
            state.tts_prefetching.discard(clean)

    asyncio.create_task(_run())


async def start_topic(tid: str, user: str, source: str = "弹幕点播") -> Dict[str, Any]:
    topic_sync_cfg()
    cfg = topic_index.cfg
    t = topic_index.by_id.get(tid)
    if not t:
        return {"code": 1, "msg": "知识库里没有这个主题"}
    if state.topic_active and state.topic_active["tid"] == tid:
        return {"code": 0, "msg": "already_playing", "title": t["title"]}
    if any(p["tid"] == tid for p in state.topic_pending):
        return {"code": 0, "msg": "already_queued", "title": t["title"]}
    plan = topic_index.build_plan(tid, user=user)
    if not plan or not plan["beats"]:
        return {"code": 1, "msg": "该主题没有可口播的段落"}
    plan["source"] = source
    if state.topic_active:
        if len(state.topic_pending) >= int(cfg.get("max_pending", 3)):
            await state.add_log("warn", f"[主题点播] 排队已满，忽略 {user} 点播「{t['title']}」")
            return {"code": 1, "msg": "点播排队已满"}
        state.topic_pending.append(plan)
        ack = fill_template(cfg.get("queued_template", ""), user=user, title=t["title"])
        if ack:
            await lt_send_speech(ack, question_banner=f"{user} 点播 · {t['title']}", speech_kind="reply")
        await state.add_log("info", f"[主题点播] {user} 点播「{t['title']}」已排队（第 {len(state.topic_pending)} 个）")
        await state.broadcast("topic_update", topic_snapshot())
        return {"code": 0, "msg": "queued", "title": t["title"]}
    if narration_active():
        kb_engine.mark_interrupted()   # 点播讲完后，主讲稿自动“回到「原章节」接着讲”
    state.topic_active = plan
    state.topic_beats_since_answer = 0
    await state.add_log("info", f"[主题点播] {source}：{user} →「{t['title']}」"
                                f"（{len(plan['beats'])} 段，来自 {t['doc']}）")
    await state.broadcast("topic_update", topic_snapshot())
    await state.broadcast("status", state.snapshot())
    state.notify_wake()
    return {"code": 0, "msg": "started", "title": t["title"], "beats": len(plan["beats"])}


async def finish_topic(reason: str = "讲完") -> None:
    a = state.topic_active
    if a:
        state.topic_last_played[a["tid"]] = time.time()
        await state.add_log("info", f"[主题点播] 「{a['title']}」{reason}")
    state.topic_active = state.topic_pending.popleft() if state.topic_pending else None
    state.topic_beats_since_answer = 0
    if state.topic_active is None and narration_active():
        await state.broadcast("board_update", render_board(kb_engine.last_board))
    await state.broadcast("topic_update", topic_snapshot())
    await state.broadcast("status", state.snapshot())


async def play_next_topic_beat() -> bool:
    plan = state.topic_active
    if not plan:
        return False
    if plan["idx"] >= len(plan["beats"]):
        await finish_topic()
        return True
    beat = plan["beats"][plan["idx"]]
    text = beat["text"]
    resumed = bool(plan.get("resume_needed")) and not beat.get("outro")
    if resumed:
        text = fill_template(topic_index.cfg.get("resume_template", ""), title=plan["title"], user=plan["user"]) + text
    max_chars = int(kb_cfg_effective().get("max_narration_chars", 240))
    ok = await lt_send_speech(
        text,
        question_banner=f"{plan['user']} 点播 · {plan['title']}",
        cacheable=(plan["idx"] > 0 and not resumed and not beat.get("outro")),
        max_chars=max_chars,
        speech_kind="topic",
    )
    if ok:
        plan["idx"] += 1
        plan["resume_needed"] = False
        plan["fails"] = 0
        state.topic_beats_since_answer += 1
        state.last_idle_since = time.time()
        await state.broadcast("board_update", render_board(beat["board"]))
        await state.broadcast("topic_update", topic_snapshot())
        if plan["idx"] < len(plan["beats"]) and not plan["beats"][plan["idx"]].get("outro"):
            schedule_tts_prefetch(plan["beats"][plan["idx"]]["text"], max_chars)
    else:
        plan["fails"] = int(plan.get("fails", 0)) + 1
        if plan["fails"] >= 3:
            await finish_topic("连续 3 次播报失败，已中止")
    return True


def sanitize_speech_text(text: str, limit: Optional[int] = None) -> str:
    # 去除 Markdown 符号和括号舞台动作描述，防止 TTS 朗读异常字符
    text = re.sub(r"[（(][^）)]{1,20}[）)]", "", text)
    text = re.sub(r"[*#`>_~\[\]]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: int(limit or plat().get("max_speech_chars") or 120)]


# ============================================================
# 免费 LLM 容灾轮询引擎（OpenAI 兼容格式）
# ============================================================
async def generate_free_llm_reply(
    user: str, text: str, hint: str = "", kb_hits: Optional[List[Dict[str, Any]]] = None
) -> str:
    providers = [
        p for p in state.config.get("llm_providers", [])
        if p.get("enabled") and not is_placeholder_key(p.get("api_key"))
    ]
    if not providers:
        state.last_provider_name = "本地兜底 (未配 Key)"
        return T("no_key", user=user, text=text[:15])

    system_prompt = effective_system_prompt()
    if kb_hits:
        refs = "\n".join(f"[{i + 1}]（{h['chapter']}）{h['text']}" for i, h in enumerate(kb_hits))
        system_prompt += T("kb_rule", refs=fill_name(refs))
    user_prompt = T("user_prompt", user=user, text=text)
    if hint:
        user_prompt += T("hint", hint=hint)

    total = len(providers)
    start_idx = state.active_provider_idx % total
    for offset in range(total):
        idx = (start_idx + offset) % total
        prov = providers[idx]
        url = prov["base_url"].rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {prov['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": prov["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.75,
            "max_tokens": 110,
        }
        if isinstance(prov.get("extra_body"), dict):
            payload.update(prov["extra_body"])          # 例：DeepSeek 关闭思考模式 {"thinking": {"type": "disabled"}}
        try:
            resp = await http_client.post(
                url, headers=headers, json=payload, timeout=prov.get("timeout", 12)
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"].get("content") or ""
                reply = sanitize_speech_text(reply)
                bad = output_blocked(reply) if reply else None
                if bad:
                    await state.add_log("warn", f"{prov['name']} 回复含平台禁用词「{bad}」，已丢弃并换节点")
                    continue
                if reply:
                    state.active_provider_idx = idx
                    state.last_provider_name = prov["name"]
                    return reply
            else:
                await state.add_log(
                    "warn", f"免费节点 {prov['name']} 返回 HTTP {resp.status_code}，自动切换下一节点"
                )
        except Exception as exc:
            await state.add_log("warn", f"免费节点 {prov['name']} 异常: {exc}，自动切换")

    state.last_provider_name = "全部限流 - 本地兜底"
    return T("all_fail", user=user)


# ============================================================
# LiveTalking 控制封装
# ============================================================
def lt_url(path: str) -> str:
    base = state.config.get("livetalking", {}).get("base_url", "http://127.0.0.1:8020")
    return base.rstrip("/") + path


def _ul_avatar_ready(sub: Path) -> bool:
    return (sub / "unet.onnx").is_file() or (sub / "ultralight.pth").is_file()


def avatars_dir() -> Optional[Path]:
    """Ultralight 独立形象目录：优先 config，其次环境变量 UL_AVATARS_DIR，最后默认工程根目录 avatars/。"""
    cfg_dir = str(state.config.get("livetalking", {}).get("avatars_dir", "")).strip()
    if cfg_dir:
        return Path(cfg_dir)
    env_dir = os.environ.get("UL_AVATARS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return BASE_DIR.parent / "avatars"


def effective_wardrobe(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """合并配置衣柜与 avatars/ 下已就绪的 Ultralight 形象。"""
    hidden = {str(x).strip().lower() for x in cfg.get("wardrobe_settings", {}).get("hidden_avatars", [])}
    d = avatars_dir()
    ready_ids: set = set()
    if d and d.exists():
        for sub in d.iterdir():
            if sub.is_dir() and not sub.name.startswith(("__", ".")) and sub.name.lower() not in hidden and _ul_avatar_ready(sub):
                ready_ids.add(sub.name)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in cfg.get("wardrobe", []):
        aid = str(item.get("id", "")).strip()
        if not aid or aid.lower() in hidden:
            continue
        seen.add(aid)
        entry = dict(item)
        entry["ready"] = (aid in ready_ids) if d and d.exists() else True
        out.append(entry)
    for aid in sorted(ready_ids):
        if aid not in seen:
            meta = load_json(d / aid / "meta.json", {}) if d else {}
            disp = str(meta.get("name") or aid)
            out.append({
                "id": aid,
                "name": disp,
                "persona_name": disp,
                "keywords": [disp, aid],
                "announce": f"已经切换到 Ultralight 主播 {disp} 啦，大家有什么问题可以打在公屏！",
                "ready": True,
            })
    return out


def avatar_ready(avatar_id: str) -> Optional[bool]:
    """返回 True/False；无法判断（目录未知）时返回 None。"""
    d = avatars_dir()
    if not d or not d.exists():
        return None
    return _ul_avatar_ready(d / avatar_id)


async def _clear_stale_session(reason: str = "") -> None:
    if state.session_id:
        state.session_id = None
        state.is_speaking = False
        state.current_speech_kind = ""
        await state.add_log("warn", f"数字人画面会话已失效（{reason or 'session not found'}），已自动重置等待重连")
        await state.broadcast("status", state.snapshot())


async def lt_probe() -> bool:
    """LiveTalking 是否在运行（只测端口能否连上，不需要画面会话）。"""
    try:
        await http_client.get(lt_url("/"), timeout=2.0)
        return True
    except Exception:
        return False


_LT_PROBE = {"ts": 0.0}
_OBS_PROBE: Dict[str, Any] = {"ts": 0.0, "scene": "", "profile": "", "pending_push": True, "lock_until": 0.0}


def _mark_obs_authoritative(obs_msg: str, target_scene: str, target_prof: str, lock_sec: float = 10.0) -> None:
    """标记由中控/桌面端下发的目标平台与场景为准；若 OBS 尚未启动连上则保留 pending_push 以便连接首帧强制推送。"""
    _OBS_PROBE["ts"] = time.time()
    _OBS_PROBE["lock_until"] = time.time() + lock_sec
    synced = bool(obs_msg.startswith("OBS:")) and ("⚠" not in obs_msg) and ("未" not in obs_msg) and ("没打开" not in obs_msg)
    if synced:
        _OBS_PROBE["pending_push"] = False
        _OBS_PROBE["scene"] = target_scene
        _OBS_PROBE["profile"] = target_prof
    else:
        _OBS_PROBE["pending_push"] = True
        _OBS_PROBE["scene"] = ""
        _OBS_PROBE["profile"] = ""


async def lt_check_speaking() -> bool:
    if not state.session_id:
        return False
    try:
        resp = await http_client.post(
            lt_url("/is_speaking"), json={"sessionid": state.session_id}, timeout=3.0
        )
        if resp.status_code == 200:
            state.lt_online = True
            body = resp.json()
            if body.get("code") == -1 and "session not found" in str(body.get("msg", "")).lower():
                await _clear_stale_session("is_speaking")
                return False
            return bool(body.get("data", False))
    except Exception:
        state.lt_online = False
    return False


async def lt_send_speech(
    text: str,
    question_banner: str = "",
    interrupt: bool = False,
    audiotype: Optional[int] = None,
    cacheable: bool = False,
    max_chars: Optional[int] = None,
    speech_kind: str = "reply",
    abort_if_busy_queue: bool = False,
) -> bool:
    """优先走克隆音色推 /humanaudio，失败自动回退 /human(Edge-TTS)。"""
    if not state.session_id:
        await state.add_log("warn", "数字人画面还没连上，请先打开 OBS 浏览器源，或打开网页控制台的「直播画面预览」开关")
        return False
    clean_text = sanitize_speech_text(fill_name(text), max_chars)
    if not clean_text:
        return False
    try:
        tts_cfg = effective_tts_cfg()
        wav_bytes, tts_tag = await tts_engine.synthesize(
            http_client, tts_cfg, clean_text, use_cache=cacheable
        )

        # 竞态保护：讲稿/暖场语音合成期间若有礼物或观众弹幕进入，立刻让位不再抢占播报
        if abort_if_busy_queue and (state.priority_queue or state.normal_queue):
            await state.add_log("info", "检测到新礼物或弹幕，当前讲稿段自动让位")
            return False

        if audiotype is not None:
            await http_client.post(
                lt_url("/set_audiotype"),
                json={"sessionid": state.session_id, "audiotype": int(audiotype)},
                timeout=3.0,
            )

        # 路径 1：克隆音色（硅基流动 CosyVoice2 或本地 GPT-SoVITS FP16）-> 直推 /humanaudio
        if wav_bytes:
            if interrupt:
                await http_client.post(
                    lt_url("/interrupt_talk"), json={"sessionid": state.session_id}, timeout=3.0
                )
            resp = await http_client.post(
                lt_url("/humanaudio"),
                data={"sessionid": state.session_id},
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
                timeout=10.0,
            )
        # 路径 2：主动选 edgetts，或克隆接口超时 / 限流时自动降级 -> 走 /human 保证不断播
        else:
            if tts_cfg.get("mode", "edgetts") != "edgetts":
                if not tts_cfg.get("fallback_to_edgetts", True):
                    await state.add_log("error", f"克隆 TTS 失败 ({tts_tag}) 且已禁用降级")
                    return False
                await state.add_log("warn", f"克隆 TTS 异常 ({tts_tag})，已自动降级为 Edge-TTS")
                tts_tag = "edgetts(fallback)"
            voice = effective_edge_voice()
            body: Dict[str, Any] = {
                "sessionid": state.session_id,
                "text": clean_text,
                "type": "echo",
                "interrupt": interrupt,
            }
            if voice:
                # LiveTalking tts/edge.py 读取 tts.ref_file 而非 voice，双字段透传确保音色切换必生效
                body["tts"] = {"voice": voice, "ref_file": voice}
            resp = await http_client.post(lt_url("/human"), json=body, timeout=5.0)

        if resp.status_code == 200 and resp.json().get("code") == 0:
            state.lt_online = True
            guard = state.config.get("scheduler", {}).get("min_speak_guard_sec", 2.8)
            state.min_busy_until = time.time() + guard
            state.is_speaking = True
            state.current_speech_kind = speech_kind
            state.current_subtitle = clean_text
            state.current_question = question_banner
            await state.broadcast("subtitle", {"subtitle": clean_text, "question": question_banner})
            state.last_tts_tag = tts_tag
            await state.add_log("speak", f"[{tts_tag}] 播报：{clean_text}")
            return True
        if resp.status_code == 200 and "session not found" in resp.text.lower():
            await _clear_stale_session("speech")
            return False
        await state.add_log("error", f"数字人引擎返回异常: {resp.text}")
    except Exception as exc:
        state.lt_online = False
        await state.add_log("error", f"调用数字人引擎失败（:8020 是否已启动？）: {exc}")
    return False


async def lt_interrupt() -> bool:
    if not state.session_id:
        return False
    try:
        resp = await http_client.post(
            lt_url("/interrupt_talk"), json={"sessionid": state.session_id}, timeout=3.0
        )
        state.min_busy_until = 0.0
        state.is_speaking = False
        state.current_speech_kind = ""
        state.current_subtitle = ""
        await state.broadcast("subtitle", {"subtitle": "", "question": ""})
        await state.add_log("info", "已发送打断指令，清空数字人语音队列")
        return resp.status_code == 200
    except Exception as exc:
        await state.add_log("error", f"打断失败: {exc}")
        return False


# ============================================================
# 一键换装核心
# ============================================================
async def trigger_switch_avatar(
    avatar_id: str,
    reason: str = "主播手动",
    announce_text: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    avatar_id = avatar_id.strip()
    if not avatar_id:
        return {"code": 1, "msg": "avatar_id 不能为空"}
    current_id = state.config.get("livetalking", {}).get("avatar_id", "")
    if avatar_id == current_id and not force:
        return {"code": 0, "msg": "已经是当前造型"}
    if state.switching_avatar:
        return {"code": 1, "msg": "正在换装重连中，请稍候"}
    if avatar_ready(avatar_id) is False:
        await state.add_log("warn", f"形象 [{avatar_id}] 尚未训练/打包完成（缺 unet.onnx 或 ultralight.pth），已忽略换装")
        return {"code": 1, "msg": f"形象 {avatar_id} 还没准备好"}

    cooldown = float(state.config.get("wardrobe_settings", {}).get("cooldown_sec", 30))
    now = time.time()
    if not force and (now - state.last_switch_ts) < cooldown:
        remain = int(cooldown - (now - state.last_switch_ts))
        await state.add_log("warn", f"换装冷却中，剩余 {remain} 秒，忽略本次请求")
        return {"code": 1, "msg": f"换装冷却中（剩余 {remain}s）"}

    # 1. 查找主播配置，同步更新人设称呼 / 音色（适合不同主播自由切换）
    host_cfg = next(
        (x for x in state.config.get("wardrobe", []) if str(x.get("id", "")).strip() == avatar_id),
        None,
    )
    if host_cfg:
        if host_cfg.get("persona_name"):
            state.config.setdefault("persona", {})["name"] = str(host_cfg["persona_name"])
        if host_cfg.get("persona_name_en"):
            state.config.setdefault("persona", {})["name_en"] = str(host_cfg["persona_name_en"])
        if host_cfg.get("style"):
            state.config.setdefault("persona", {})["style"] = str(host_cfg["style"])
        if host_cfg.get("system_prompt"):
            state.config.setdefault("persona", {})["system_prompt"] = str(host_cfg["system_prompt"])
        if host_cfg.get("tts_voice"):
            state.config.setdefault("livetalking", {})["tts_voice"] = str(host_cfg["tts_voice"])
        if host_cfg.get("sf_voice"):
            state.config.setdefault("tts", {}).setdefault("siliconflow", {})["voice"] = str(host_cfg["sf_voice"])

    # 2. 若当前没有活跃舞台/预览会话，直接完成离线切换，不阻塞等待 10 秒超时
    had_session = bool(state.session_id)
    state.last_switch_ts = now
    state.config.setdefault("livetalking", {})["avatar_id"] = avatar_id
    save_json(CONFIG_PATH, state.config)

    if not had_session:
        state.switching_avatar = False
        await state.add_log("info", f"已切换默认主播/形象 -> {avatar_id}（来源：{reason}，等待预览或 OBS 连接画面）")
        await state.broadcast("status", state.snapshot())
        await state.broadcast("switch_avatar", {"avatar_id": avatar_id, "reason": reason})
        return {"code": 0, "msg": "switched_offline", "avatar_id": avatar_id}

    # 3. 有活跃会话时上锁并打断当前播报，防止语音发往即将销毁的旧 session
    state.switching_avatar = True
    await lt_interrupt()
    state.session_id = None
    state.is_speaking = False
    announce_on = state.config.get("wardrobe_settings", {}).get("announce_after_switch", True)
    state.pending_announce = announce_text if announce_on else ""
    await state.add_log("info", f"开始切换主播/造型 -> {avatar_id}（来源：{reason}）")
    await state.broadcast("status", state.snapshot())
    await state.broadcast("switch_avatar", {"avatar_id": avatar_id, "reason": reason})

    # 4. 安全兜底超时：若 10 秒内未收到舞台页重连，自动释放锁防止死锁
    async def _guard_unlock():
        await asyncio.sleep(10.0)
        if state.switching_avatar:
            state.switching_avatar = False
            await state.add_log("warn", "换装/切主播重连超时，已自动解除调度锁，请检查 OBS 浏览器源或预览页")
            await state.broadcast("status", state.snapshot())

    asyncio.create_task(_guard_unlock())
    return {"code": 0, "msg": "switching", "avatar_id": avatar_id}


# ============================================================
# 核心防截断调度循环
# ============================================================
async def _sleep_or_wake(timeout_sec: float) -> None:
    if state.wake_event is None:
        await asyncio.sleep(timeout_sec)
        return
    try:
        await asyncio.wait_for(state.wake_event.wait(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        pass
    finally:
        state.wake_event.clear()


async def scheduler_loop() -> None:
    state.wake_event = asyncio.Event()
    while True:
        sched = state.config.get("scheduler", {})
        poll_sec = float(sched.get("poll_interval_sec", 0.3))
        try:
            # 换装重连窗口期暂停调度，避免消息丢失
            if state.switching_avatar:
                await _sleep_or_wake(poll_sec)
                continue

            now = time.time()
            # 0. 画面还没连上时，每 5 秒探测一次 LiveTalking 是否在运行（控制台顶部状态用）
            if not state.session_id and now - _LT_PROBE["ts"] > 5:
                _LT_PROBE["ts"] = now
                was = state.lt_online
                state.lt_online = await lt_probe()
                if was != state.lt_online:
                    await state.broadcast("status", state.snapshot())
            # 0.5 权威锁定与双向联动 OBS 场景：
            #   * 启动/切播锁定期间（pending_push 或 lock_until 内或未建立基线）：以中控平台为准推送目标场景到 OBS，绝不被 OBS 启动默认场景反向跳回抖音
            #   * 建立基线并过锁定期后：仅当用户在 OBS 窗口实际切到与基线不同的新场景时，双向同步中控与桌面端
            if now - _OBS_PROBE["ts"] >= 2.5:
                _OBS_PROBE["ts"] = now
                obs_info = await asyncio.to_thread(prof_mgr.poll_obs_scene_blocking)
                if obs_info.get("connected"):
                    sc = obs_info.get("scene", "")
                    cur_prof = plat()
                    target_scene = cur_prof.get("obs_scene", "")
                    target_prof_name = cur_prof.get("obs_profile", "")
                    force_push = bool(_OBS_PROBE.get("pending_push")) or (now < float(_OBS_PROBE.get("lock_until", 0.0)))
                    last_seen = _OBS_PROBE.get("scene", "")

                    if force_push or not last_seen:
                        if (target_scene and sc != target_scene) or (target_prof_name and obs_info.get("profile", "") != target_prof_name) or obs_info.get("need_fix"):
                            obs_msg = await asyncio.to_thread(prof_mgr.sync_obs_blocking, target_prof_name, target_scene)
                            prof_mgr.last_obs_msg = obs_msg
                            _mark_obs_authoritative(obs_msg, target_scene, target_prof_name, lock_sec=4.0)
                            await state.add_log("info", f"OBS 锁定校准 -> {target_scene}（{target_prof_name} / {cur_prof.get('orientation', '')}）| {obs_msg}")
                            await state.broadcast("bg_update", resolve_bg_payload(kb_engine.last_board.get("bg_video", "")))
                            await state.broadcast("status", state.snapshot())
                        else:
                            _OBS_PROBE["pending_push"] = False
                            _OBS_PROBE["scene"] = target_scene or sc
                            _OBS_PROBE["profile"] = target_prof_name or obs_info.get("profile", "")
                    elif sc and sc in OBS_SCENE_MAP:
                        exp_prof = obs_info["expected_profile"]
                        exp_ori = obs_info["orientation"]
                        exp_plat = obs_info["platform"]
                        user_switched = (sc != last_seen)
                        need_fix = bool(obs_info.get("need_fix"))
                        if user_switched or need_fix:
                            target_prof = prof_mgr.data.get("profiles", {}).get(exp_plat)
                            if target_prof is not None:
                                target_prof["orientation"] = exp_ori
                                target_prof["obs_profile"] = exp_prof
                                target_prof["obs_scene"] = sc
                            if exp_plat and exp_plat in prof_mgr.data.get("profiles", {}) and exp_plat != prof_mgr.active_key:
                                prof_mgr.set_active(exp_plat, prof_mgr.active_room)
                                apply_platform_runtime()
                            else:
                                cur_prof["orientation"] = exp_ori
                                cur_prof["obs_profile"] = exp_prof
                                cur_prof["obs_scene"] = sc
                            obs_msg = await asyncio.to_thread(prof_mgr.sync_obs_blocking, exp_prof, sc)
                            prof_mgr.last_obs_msg = obs_msg
                            _OBS_PROBE["scene"] = sc
                            _OBS_PROBE["profile"] = exp_prof
                            await state.add_log("info", f"OBS 手动切场同步 -> {sc}（{exp_prof} / {exp_ori}）| {obs_msg}")
                            await state.broadcast("bg_update", resolve_bg_payload(kb_engine.last_board.get("bg_video", "")))
                            await state.broadcast("status", state.snapshot())
            # 1. 探测说话状态（叠加 TTS 合成保护期）
            if now < state.min_busy_until:
                speaking = True
            else:
                speaking = await lt_check_speaking()
            if speaking != state.is_speaking:
                state.is_speaking = speaking
                if not speaking:
                    state.current_speech_kind = ""
                    state.last_idle_since = now
                    state.current_subtitle = ""
                await state.broadcast("status", state.snapshot())

            if not speaking and state.session_id:
                item = None
                topic_turn = False
                tcfg = topic_index.cfg
                if state.priority_queue:
                    item = state.priority_queue.popleft()
                elif state.topic_active:
                    # 点播讲解中：每讲 N 段穿插回答一条观众提问，再“咱们接着说……”
                    can_answer = (bool(state.normal_queue) and sched.get("auto_reply", True)
                                  and tcfg.get("answer_during_topic", True)
                                  and state.topic_beats_since_answer >= int(tcfg.get("answer_every_n_beats", 2)))
                    if can_answer:
                        item = state.normal_queue.popleft()
                    else:
                        topic_turn = True
                elif state.normal_queue and sched.get("auto_reply", True):
                    item = state.normal_queue.popleft()

                if topic_turn:
                    await play_next_topic_beat()
                elif item:
                    if narration_active():
                        kb_engine.mark_interrupted()   # 答完自动回到原章节断点续讲
                    kind = item.get("kind", "chat")
                    user = item.get("user", "观众")
                    text = item.get("text", "")
                    if kind == "safe_faq":
                        await lt_send_speech(
                            item.get("answer", ""), question_banner=T("safe_banner"),
                            cacheable=True, speech_kind="reply",
                        )
                    elif kind == "gift":
                        gift_name = item.get("gift_item", "礼物")
                        gift_num = item.get("gift_num", 1)
                        inter = match_gift_interaction("gift", gift_item=gift_name)
                        tpl = (inter or {}).get("reply") or T("gift", user=user, num=gift_num, gift=gift_name)
                        reply = fill_template(tpl, user=user, num=gift_num, gift=gift_name)
                        await lt_send_speech(
                            reply,
                            question_banner=T("gift_banner", user=user, num=gift_num, gift=gift_name),
                            audiotype=(inter or {}).get("audiotype", 2),
                            cacheable=True,
                            speech_kind="gift",
                        )
                    else:
                        outfit = match_wardrobe_outfit(text)
                        if outfit:
                            await trigger_switch_avatar(
                                outfit["id"],
                                reason=f"弹幕点装 ({user})",
                                announce_text=outfit.get(
                                    "announce", f"已按{user}的要求换好{outfit.get('name', '')}啦！"
                                ),
                                force=False,
                            )
                            await state.broadcast("status", state.snapshot())
                            await _sleep_or_wake(poll_sec)
                            continue
                        inter = match_gift_interaction("chat", text=text)
                        if inter and inter.get("chat_reply"):
                            await lt_send_speech(
                                inter["chat_reply"],
                                question_banner=f"{user}：{text}",
                                audiotype=inter.get("audiotype"),
                                cacheable=True,
                                speech_kind="action",
                            )
                            await state.broadcast("status", state.snapshot())
                            await _sleep_or_wake(poll_sec)
                            continue
                        # 主题点播：观众要求“先讲/讲讲/想听”知识库里已有的主题 → 整段推给数字人口播
                        if kb_cfg_effective().get("enabled", True) and topic_index.cfg.get("enabled", True):
                            tr_res = await topic_route(text)
                            if tr_res.get("is_request") and tr_res.get("tid"):
                                tid = tr_res["tid"]
                                title = tr_res["topic"]["title"]
                                ago = time.time() - state.topic_last_played.get(tid, 0.0)
                                if ago >= float(topic_index.cfg.get("cooldown_sec", 300)):
                                    await state.add_log("info", f"[主题点播识别·{tr_res['method']}] {user}：{text} →「{title}」（{tr_res['reason']}）")
                                    res = await start_topic(tid, user)
                                    if res.get("msg") == "started":
                                        await play_next_topic_beat()
                                    await state.broadcast("status", state.snapshot())
                                    await _sleep_or_wake(poll_sec)
                                    continue
                                recap = topic_index.recap_text(tid, user=user)
                                await state.add_log("info", f"[主题点播] 「{title}」{int(ago)} 秒前刚讲过，这次只回顾重点")
                                if recap:
                                    await lt_send_speech(recap, question_banner=f"{user} 点播 · {title}",
                                                         speech_kind="reply")
                                    if state.topic_active:
                                        state.topic_active["resume_needed"] = True
                                    await state.broadcast("status", state.snapshot())
                                    await _sleep_or_wake(poll_sec)
                                    continue
                        route = match_action_route(text)
                        audiotype = route["audiotype"] if route else None
                        hint = route["reply_hint"] if route else ""
                        reply = None
                        hits: Optional[List[Dict[str, Any]]] = None
                        if kb_ready():
                            kbc = kb_cfg_effective()
                            kb_engine.update_config(kbc)
                            if should_skip_dense_search(text):
                                direct = kb_engine.match_direct_faq(text)
                                if direct and not route:
                                    reply = direct["answer"]
                                    await state.add_log("info", f"[知识库 L1 直出] 命中触发词「{direct.get('keyword', '')}」")
                            else:
                                sr = await kb_engine.hybrid_search(http_client, text, top_k=int(kbc.get("top_k", 3)))
                                if sr.get("direct_answer") and not route:
                                    reply = sr["direct_answer"]
                                    await state.add_log("info", f"[知识库 L1 直出] 命中触发词「{sr.get('keyword', '')}」")
                                else:
                                    hits = sr.get("hits") or None
                                    if hits:
                                        await state.add_log(
                                            "info", f"[知识库 {sr.get('mode')}] 命中 {len(hits)} 段，最高分 {hits[0]['score']}")
                        if reply is None and not route:
                            sf = match_safety_faq(text)
                            if sf:
                                reply = sf["answer"]
                                await state.add_log("info", f"[安全兜底直出] 命中触发词「{sf['keyword']}」")
                        if reply is None:
                            reply = await generate_free_llm_reply(user, text, hint, kb_hits=hits)
                        await lt_send_speech(
                            reply, question_banner=f"{user}：{text}", audiotype=audiotype,
                            cacheable=bool(hits is None and not route), speech_kind="reply",
                        )
                        if state.topic_active:
                            state.topic_active["resume_needed"] = True
                            state.topic_beats_since_answer = 0
                    await state.broadcast("status", state.snapshot())

                # 2. 知识库章节主动解说（断点续讲 + OBS 板书联动，每 N 段穿插一句平台互动话术）
                elif narration_active():
                    kbc = kb_cfg_effective()
                    if now - state.last_idle_since >= float(kbc.get("narration_gap_sec", 3.5)):
                        every = int(kbc.get("warmup_every_n_beats", 4) or 0)
                        topic = None
                        if every > 0 and sched.get("auto_warmup", True) and state.beats_since_warmup >= every:
                            topic = state.pop_warmup()
                        if topic:
                            ok = await lt_send_speech(
                                topic, question_banner=T("warmup_banner"),
                                cacheable=True, speech_kind="warmup", abort_if_busy_queue=True,
                            )
                            if ok:
                                state.beats_since_warmup = 0
                                state.last_idle_since = time.time()
                        else:
                            beat = kb_engine.peek_beat()
                            if beat:
                                speech = beat["text"]
                                if beat.get("course_restart"):
                                    speech = T("loop_restart") + speech
                                elif beat.get("resumed"):
                                    speech = T("resume", chapter=fill_name(beat["chapter"])) + speech
                                ok = await lt_send_speech(
                                    speech,
                                    question_banner=T("narration_banner", chapter=fill_name(beat["chapter"])),
                                    cacheable=True,
                                    max_chars=int(kbc.get("max_narration_chars", 240)),
                                    speech_kind="narration",
                                    abort_if_busy_queue=True,
                                )
                                if ok:
                                    kb_engine.commit_beat(beat)
                                    kb_engine.save_cursors()
                                    state.beats_since_warmup += 1
                                    state.last_idle_since = time.time()
                                    nxt = kb_engine.peek_beat()
                                    if nxt and not nxt.get("resumed") and not nxt.get("course_restart"):
                                        schedule_tts_prefetch(nxt["text"], int(kbc.get("max_narration_chars", 240)))
                                    await state.broadcast("board_update", render_board(beat["board"]))
                                    await state.broadcast("status", state.snapshot())

                # 3. 冷场自动暖场（未开解说或讲稿为空时）
                elif sched.get("auto_warmup", True):
                    warmup_gap = float(sched.get("warmup_interval_sec", 25))
                    if now - state.last_idle_since >= warmup_gap:
                        topic = state.pop_warmup()
                        if topic:
                            await state.add_log("info", "触发冷场暖场话术")
                            ok = await lt_send_speech(
                                topic, question_banner=T("warmup_banner"),
                                cacheable=True, speech_kind="warmup", abort_if_busy_queue=True,
                            )
                            if ok:
                                state.last_idle_since = time.time()
                                await state.broadcast("status", state.snapshot())
        except Exception as exc:
            await state.add_log("error", f"调度循环异常: {exc}")
        await _sleep_or_wake(poll_sec)


# ============================================================
# 系统健康诊断（纯规则确定性探针，5 秒一次，经 SSE `health` 推给控制台右上角信号灯）
# ============================================================
HEALTH_TTS_LABEL = {"edgetts": "Edge-TTS 免费声音", "siliconflow": "CosyVoice2 克隆声音", "gptsovits": "本地 GPT-SoVITS"}


def health_context() -> Dict[str, Any]:
    kbc = kb_cfg_effective()
    p = plat()
    tts_cfg = state.config.get("tts", {})
    phrases = ["小朋友们好", "小朋友记住", "小朋友请注意", "小朋友们记住", "小朋友，", "小朋友可不能", "同学们好"]
    phrases += [w for w in state.moderation.get("output_blocked_keywords", []) if "小朋友" in w or "同学们" in w]
    return {
        "scheduler_alive": scheduler_task is not None and not scheduler_task.done(),
        "lt_base": state.config.get("livetalking", {}).get("base_url", "http://127.0.0.1:8020"),
        "llm_ready": sum(1 for x in state.config.get("llm_providers", [])
                         if x.get("enabled") and not is_placeholder_key(x.get("api_key"))),
        "tts_mode": tts_cfg.get("mode", "edgetts"),
        "tts_label": HEALTH_TTS_LABEL.get(tts_cfg.get("mode", "edgetts"), tts_cfg.get("mode", "edgetts")),
        "tts_key_ok": not is_placeholder_key(tts_cfg.get("siliconflow", {}).get("api_key")),
        "last_tts_tag": state.last_tts_tag,
        "tts_cache_dir": str(tts_engine.cache_dir),
        "kb_enabled": bool(kbc.get("enabled", True)),
        "narration_active": narration_active(),
        "bound_doc": kb_doc_for_platform(),
        "embedding_ready": not is_placeholder_key(kbc.get("embedding", {}).get("api_key")),
        "minor_phrases": list(dict.fromkeys(phrases)),
        "compliance_texts": {
            "人设": effective_system_prompt(),
            "暖场话术": "\n".join(state.warmup_master),
            "画面说明卡": json.dumps(render_panel(p.get("stage_panel") or state.config.get("stage_panel", {})), ensure_ascii=False),
        },
        "platform_label": p.get("label", prof_mgr.active_key),
        "last_danmaku_ago": int(time.time() - state.last_danmaku_ts) if state.last_danmaku_ts else None,
        "health_cfg": state.config.get("health", {}),
    }


async def health_loop() -> None:
    while True:
        try:
            report = await health_mon.run_all_checks(http_client, state, kb_engine, prof_mgr, health_context())
            await state.broadcast("health", report)
        except Exception as exc:
            print(f"[health] 诊断异常: {exc}", flush=True)
        await asyncio.sleep(float(state.config.get("health", {}).get("interval_sec", 5)))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http_client, scheduler_task
    http_client = httpx.AsyncClient()
    try:
        kb_boot_msg = await kb_load_for_platform(reset_cursor=False)
    except Exception as exc:
        kb_boot_msg = f"加载讲稿失败: {exc}"
    apply_platform_runtime()
    try:
        topic_sync_cfg()
        topic_index.ensure_fresh(0)
        topic_boot_msg = (f"主题点播目录：{len(topic_index.topics)} 个主题（来自 {len(topic_index.list_docs())} 份讲稿），"
                          f"识别方式={topic_index.cfg.get('detect_mode', 'auto')}，"
                          f"AI 复核{'可用' if llm_providers_ready() else '不可用（未配 Key，自动降级关键词规则）'}")
    except Exception as exc:
        topic_boot_msg = f"主题点播目录构建失败: {exc}"
    scheduler_task = asyncio.create_task(scheduler_loop())
    wd_task = asyncio.create_task(prof_mgr.watchdog(state.add_log))
    h_task = asyncio.create_task(health_loop())
    await state.add_log("info", f"{APP_NAME} v{APP_VERSION} · 中控已启动")
    await state.add_log("info", kb_boot_msg)
    await state.add_log("info", topic_boot_msg)
    await state.add_log(
        "info",
        f"当前平台预设: {plat().get('label', prof_mgr.active_key)}（弹幕采集器需在 WebUI 点「一键切播」后启动）",
    )
    d = avatars_dir()
    await state.add_log("info", f"数字人形象目录: {d if d else '未配置（换装前不做存在性检查）'}")
    try:
        yield
    finally:
        scheduler_task.cancel()
        wd_task.cancel()
        h_task.cancel()
        prof_mgr.stop_collector()  # 防止关掉网关后留下孤儿采集进程
        kb_engine.save_cursors()
        await http_client.aclose()


app = FastAPI(title=f"{APP_NAME} · 中控 v{APP_VERSION}", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# 页面路由
# ============================================================
from urllib.parse import quote
BG_DEFAULTS = {'mode': 'kb_auto', 'default_video': 'bg_family_kitchen.mp4', 'chroma_color': '#00ff00', 'similarity': 0.34, 'smoothness': 0.08, 'spill': 0.16, 'doc_video_map': {'01_adults_family_ai_basics.md': 'bg_family_kitchen.mp4', '02_parents_guide_kids_ai.md': 'bg_study_desk.mp4', '03_elders_practical_antifraud.md': 'bg_antifraud_warn.mp4', '04_ai_glossary_metaphors.md': 'bg_tech_chalkboard.mp4'}}
BG_DIR = BASE_DIR / "bg_media"
BG_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/bg_media", StaticFiles(directory=str(BG_DIR)), name="bg_media")

ACTION_DIR = BASE_DIR / "action_media"
ACTION_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/action_media", StaticFiles(directory=str(ACTION_DIR)), name="action_media")


def resolve_action_video(vid: str, avatar_id: str = "") -> tuple[Optional[Path], str]:
    """优先查找当前主播专属动作短片 action_media/<avatar_id>/<vid>，其次通用 action_media/<vid>。"""
    if not vid:
        return None, "none"
    fname = Path(vid).name
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (avatar_id or "").strip())
    if aid:
        host_path = ACTION_DIR / aid / fname
        if host_path.is_file():
            return host_path, "host"
    global_path = ACTION_DIR / fname
    if global_path.is_file():
        return global_path, "global"
    return None, "none"


_ACTION_CLIP_CACHE: Dict[str, Tuple[int, bool]] = {}


def _is_real_action_clip(vpath: Optional[Path]) -> bool:
    """识别用户真实上传的动作 MP4 与系统初始静态克隆占位片，防止静态占位片覆盖说话嘴型和特效图层。"""
    if not vpath or not vpath.is_file():
        return False
    marker_file = vpath.parent / ".custom_clips.json"
    if marker_file.is_file():
        custom = load_json(marker_file, [])
        if isinstance(custom, list) and vpath.name in custom:
            return True
    try:
        st = vpath.stat()
        key = str(vpath)
        cached = _ACTION_CLIP_CACHE.get(key)
        if cached and cached[0] == st.st_mtime_ns:
            return cached[1]
        # 若同目录下存在 2 个以上字节/MD5 完全相同的 act_*.mp4，则为静态克隆占位片
        cur_md5 = hashlib.md5(vpath.read_bytes()).hexdigest()
        same_count = 0
        for p in vpath.parent.glob("act_*.mp4"):
            if p.is_file() and p.stat().st_size == st.st_size:
                if hashlib.md5(p.read_bytes()).hexdigest() == cur_md5:
                    same_count += 1
        # act_sunglasses 若非用户主动上传的真实短片且同目录其余动作全为静态占位片，则视为初始生成占位片
        has_dup_siblings = same_count >= 2
        if not has_dup_siblings and vpath.name == "act_sunglasses.mp4":
            sibling_md5s = [hashlib.md5(p.read_bytes()).hexdigest() for p in vpath.parent.glob("act_*.mp4") if p.is_file() and p != vpath]
            if len(sibling_md5s) >= 2 and len(set(sibling_md5s)) == 1:
                has_dup_siblings = True
        is_real = not has_dup_siblings
        _ACTION_CLIP_CACHE[key] = (st.st_mtime_ns, is_real)
        return is_real
    except Exception:
        return False


def get_action_media_status(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    aid = str(cfg.get("livetalking", {}).get("avatar_id", "")).strip()
    gi = cfg.get("gift_interactions", {})
    status: Dict[str, Dict[str, Any]] = {}
    for rule in gi.get("rules", []):
        rid = str(rule.get("id", "")).strip()
        vid = str(rule.get("video", "")).strip()
        if not rid:
            continue
        vpath, scope = resolve_action_video(vid, aid)
        use_video = _is_real_action_clip(vpath)
        rel = f"{aid}/{vpath.name}" if (vpath and scope == "host") else (vpath.name if vpath else "")
        status[rid] = {
            "label": rule.get("label", rid),
            "filename": Path(vid).name if vid else f"act_{rid}.mp4",
            "has_video": bool(use_video),
            "scope": scope if use_video else "builtin_motion",
            "video_url": f"/action_media/{quote(rel)}?v={vpath.stat().st_mtime_ns}" if use_video else "",
        }
    return status


async def broadcast_play_action(
    rule: Dict[str, Any], user: str = "", gift_item: str = "", gift_num: int = 0
) -> Dict[str, Any]:
    """0 延迟推送舞台互动画面（用户上传的真实动作短片 + 人物物理摆动关键帧 + 动态道具粒子特效）。"""
    vid = str(rule.get("video", "")).strip()
    aid = str(state.config.get("livetalking", {}).get("avatar_id", "")).strip()
    vpath, scope = resolve_action_video(vid, aid)
    use_video = _is_real_action_clip(vpath)
    rel = f"{aid}/{vpath.name}" if (vpath and scope == "host") else (vpath.name if vpath else "")
    payload = {
        "action_id": rule.get("id") or rule.get("sticker") or "heart",
        "label": rule.get("label") or "互动特效",
        "sticker": rule.get("sticker") or "heart",
        "motion": rule.get("id") or rule.get("sticker") or "heart",
        "use_video": use_video,
        "video_url": f"/action_media/{quote(rel)}?v={vpath.stat().st_mtime_ns}" if use_video else "",
        "duration_sec": float(rule.get("duration_sec", 4.0)),
        "user": user,
        "gift_item": gift_item,
        "gift_num": int(gift_num or 0),
    }
    await state.broadcast("play_action", payload)
    return payload


def list_bg_files() -> List[str]:
    exts = {".mp4", ".webm", ".jpg", ".jpeg", ".png", ".webp"}
    return sorted([p.name for p in BG_DIR.iterdir() if p.is_file() and p.suffix.lower() in exts])


def resolve_bg_payload(chapter_override: str = "") -> Dict[str, Any]:
    bg_cfg = {**BG_DEFAULTS, **state.config.get("background", {})}
    mode = bg_cfg.get("mode", "kb_auto")
    doc_map = bg_cfg.get("doc_video_map", {})
    active_doc = kb_engine.active_doc or ""
    active_name = Path(active_doc.replace("\\", "/")).name if active_doc else ""

    available = list_bg_files()
    chosen = ""
    source = "default"
    doc_mapped = doc_map.get(active_doc) or next(
        (v for k, v in doc_map.items() if active_name and Path(k.replace("\\", "/")).name == active_name),
        "",
    )
    if chapter_override and chapter_override in available:
        chosen = chapter_override
        source = "chapter"
    elif doc_mapped and doc_mapped in available:
        chosen = doc_mapped
        source = "doc"
    else:
        default_v = bg_cfg.get("default_video", "")
        if default_v and default_v in available:
            chosen = default_v
            source = "default"
        else:
            avail = available
            chosen = avail[0] if avail else ""
            source = "fallback"

    # 若当前为竖屏模式且存在同题材 *_portrait 背景文件，自动选用竖屏专属背景
    if chosen and plat().get("orientation") == "portrait":
        cp = Path(chosen)
        if not cp.stem.endswith("_portrait"):
            port_cand = f"{cp.stem}_portrait{cp.suffix}"
            if port_cand in available:
                chosen = port_cand

    ext = Path(chosen).suffix.lower() if chosen else ""
    media_type = "image" if ext in {".jpg", ".jpeg", ".png", ".webp"} else "video"
    return {
        "mode": mode,
        "filename": chosen,
        "video_url": f"/bg_media/{quote(chosen)}?v={(BG_DIR / chosen).stat().st_mtime_ns}" if chosen else "",
        "media_type": media_type,
        "source": source,
        "active_doc": active_doc,
        "chroma_color": bg_cfg.get("chroma_color", "#00ff00"),
        "similarity": float(bg_cfg.get("similarity", 0.34)),
        "smoothness": float(bg_cfg.get("smoothness", 0.08)),
        "spill": float(bg_cfg.get("spill", 0.16)),
        "available_files": available,
        "doc_video_map": doc_map,
    }


async def broadcast_bg_update(chapter_override: str = "") -> Dict[str, Any]:
    payload = resolve_bg_payload(chapter_override)
    await state.broadcast("bg_update", payload)
    return payload


def resolve_overlay_cfg() -> Dict[str, Any]:
    return sanitize_overlay_cfg(state.config.get("overlay"))


@app.get("/api/overlay/config")
async def api_overlay_get():
    return {
        "code": 0,
        "overlay": resolve_overlay_cfg(),
        "fonts": OVERLAY_FONT_PRESETS,
        "effects": OVERLAY_EFFECT_PRESETS,
        "animations": [{"id": k, "label": v} for k, v in OVERLAY_ANIMATION_OPTIONS],
    }


@app.post("/api/overlay/config")
async def api_overlay_post(patch: Dict[str, Any]):
    cur = resolve_overlay_cfg()
    updated = sanitize_overlay_cfg(patch, base=cur)
    state.config["overlay"] = {k: v for k, v in updated.items() if k not in ("font_css", "font_license")}
    save_json(CONFIG_PATH, state.config)
    await state.broadcast("overlay_update", updated)
    await state.broadcast("status", state.snapshot())
    await state.add_log(
        "info",
        f"画面字幕更新 -> 章节卡:{'开' if updated['show_kb_board'] else '关'}({updated['board_position']}) | "
        f"口播字幕:{'开' if updated['show_subtitle'] else '关'} | 字体:{updated['font_preset']} | 效果:{updated['effect_preset']}",
    )
    return {"code": 0, "overlay": updated}


@app.get("/api/bg/list")
async def api_bg_list():
    return {"code": 0, **resolve_bg_payload(kb_engine.last_board.get("bg_video", ""))}


@app.post("/api/bg/config")
async def api_bg_config(patch: Dict[str, Any]):
    bg_cfg = {**BG_DEFAULTS, **state.config.get("background", {})}
    if "mode" in patch and patch["mode"] not in ("original", "greenscreen", "kb_auto"):
        raise HTTPException(400, "未知背景模式")
    for key, lo, hi in (("similarity", .1, .6), ("smoothness", .01, .3), ("spill", .02, .45)):
        if key in patch:
            value = patch[key]
            if not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise HTTPException(400, "抠像参数超出范围")
            bg_cfg[key] = value
    if "mode" in patch:
        bg_cfg["mode"] = patch["mode"]
    for key in ("default_video", "bind_video"):
        if key in patch and patch[key] not in list_bg_files():
            raise HTTPException(400, "背景文件不存在")
    if "default_video" in patch:
        bg_cfg["default_video"] = patch["default_video"]
    if "bind_video" in patch:
        doc = patch.get("bind_doc") or kb_engine.active_doc
        if doc not in kb_engine.list_docs():
            raise HTTPException(400, "请先选择讲稿")
        bg_cfg["doc_video_map"] = {**bg_cfg["doc_video_map"], doc: patch["bind_video"]}
    state.config["background"] = bg_cfg
    save_json(CONFIG_PATH, state.config)
    payload = await broadcast_bg_update(kb_engine.last_board.get("bg_video", ""))
    await state.add_log("info", f"背景更新 -> 模式:{payload['mode']} | 素材:{payload['filename'] or '无'}")
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "background": payload}


@app.post("/api/bg/upload")
async def api_bg_upload(
    file: UploadFile = File(...),
    bind_doc: str = Form(...),
):
    from tempfile import NamedTemporaryFile
    if bind_doc not in kb_engine.list_docs():
        raise HTTPException(400, "请选择已导入的讲稿")
    name = re.sub(r"[^\w. -]", "_", Path(file.filename or "").name)
    if Path(name).suffix.lower() not in {".mp4", ".webm", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "仅支持 MP4、WebM 或常见图片")
    with NamedTemporaryFile(dir=BASE_DIR, delete=False) as tmp:
        path = Path(tmp.name)
    try:
        size = 0
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 256 * 1024 * 1024:
                    raise HTTPException(413, "文件超过 256 MB")
                out.write(chunk)
        if not size:
            raise HTTPException(400, "文件为空")
        path.replace(BG_DIR / name)
    finally:
        path.unlink(missing_ok=True)
    return await api_bg_config({"bind_doc": bind_doc, "bind_video": name, "mode": "kb_auto"})

# 页面不让浏览器 / OBS 缓存：更新代码后刷新一下就是最新版，不会还显示旧名称旧按钮
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@app.get("/")
async def index_page():
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)


@app.get("/stage")
async def stage_page():
    return FileResponse(STATIC_DIR / "overlay.html", headers=NO_CACHE)


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ============================================================
# WebRTC 代理（自动捕获 sessionid + 换装完成自动解锁播报）
# ============================================================
@app.post("/api/webrtc/offer")
async def proxy_webrtc_offer(request: Request):
    body = await request.json()
    lt_cfg = state.config.get("livetalking", {})
    # 优先使用客户端显式传入的 avatar，否则读配置当前 avatar_id；若默认形象尚未训练则自动回退到首个已就绪 Ultralight 形象
    target_avatar = body.get("avatar") or lt_cfg.get("avatar_id", "demo_ul_host")
    if not body.get("avatar") and not avatar_ready(target_avatar):
        av_d = avatars_dir()
        if av_d and av_d.is_dir():
            cand = next((p.name for p in sorted(av_d.iterdir()) if p.is_dir() and _ul_avatar_ready(p)), "")
            if cand:
                target_avatar = cand
    body["avatar"] = target_avatar
    if effective_edge_voice():
        body.setdefault("refaudio", effective_edge_voice())
    try:
        resp = await http_client.post(lt_url("/offer"), json=body, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        sid = data.get("sessionid")
        if sid and data.get("mirror"):
            # 预览镜像：共享 OBS 同一路画面，绝不接管主播说话的会话
            state.preview_sessions.add(str(sid))
            await state.add_log("info", f"预览已连接（共享 OBS 画面，不额外占用推理）: {sid}")
            await state.broadcast("preview_update", preview_snapshot())
            return JSONResponse(data)
        if sid:
            if str(data.get("role") or "") == "preview":
                state.preview_sessions.add(str(sid))
            was_switching = state.switching_avatar
            state.session_id = str(sid)
            state.lt_online = True
            state.switching_avatar = False
            state.last_idle_since = time.time()
            await state.add_log("info", f"已绑定形象 [{target_avatar}] 会话 ID: {state.session_id}")
            await state.broadcast("status", state.snapshot())
            # 若刚完成换装且配置了播报语，延迟 1 秒让视频流稳定后自动开口
            if was_switching and state.pending_announce:
                msg = state.pending_announce
                state.pending_announce = ""

                async def _delayed_say(text: str):
                    await asyncio.sleep(1.0)
                    await lt_send_speech(text, question_banner="换装完成", cacheable=True)

                asyncio.create_task(_delayed_say(msg))
        else:
            err_msg = str(data.get("msg") or "数字人引擎未返回画面会话")
            await state.add_log("warn", f"WebRTC 绑定形象 [{target_avatar}] 未就绪: {err_msg}")
            if "maximum session" not in err_msg.lower():
                state.switching_avatar = False
                await state.broadcast("status", state.snapshot())
        return JSONResponse(data)
    except Exception as exc:
        state.lt_online = False
        state.switching_avatar = False
        raise HTTPException(status_code=502, detail=f"连接数字人引擎失败（:8020 是否已启动？）: {exc}")


@app.post("/api/webrtc/release")
async def release_webrtc_session(request: Request):
    """舞台/预览页关闭时通知中控释放对应 session_id，避免残留无效会话。"""
    try:
        body = await request.json()
        sid = str(body.get("sessionid") or "").strip()
    except Exception:
        sid = ""
    if sid:
        state.preview_sessions.discard(sid)
    if sid and state.session_id == sid:
        state.session_id = None
        state.is_speaking = False
        await state.broadcast("status", state.snapshot())
    await state.broadcast("preview_update", preview_snapshot())
    return {"code": 0}


# ============================================================
# 预览开关（WebUI 与桌面端共用；关掉后所有预览页自动断开，释放 CPU 编码）
# ============================================================
def preview_snapshot() -> Dict[str, Any]:
    ui = state.config.get("ui", {}) or {}
    return {"enabled": bool(ui.get("preview_enabled", True)), "connected": len(state.preview_sessions),
            "obs_main": bool(state.session_id and state.session_id not in state.preview_sessions)}


@app.get("/api/preview")
async def api_preview_get():
    return {"code": 0, **preview_snapshot()}


@app.post("/api/preview")
async def api_preview_set(payload: Dict[str, Any]):
    enabled = bool(payload.get("enabled"))
    state.config.setdefault("ui", {})["preview_enabled"] = enabled
    save_json(CONFIG_PATH, state.config)
    if not enabled:
        state.preview_sessions.clear()
    await state.add_log("info", "已开启直播画面预览" if enabled else "已关闭直播画面预览（预览页自动断开）")
    await state.broadcast("preview_update", preview_snapshot())
    return {"code": 0, **preview_snapshot()}


# ============================================================
# 统一弹幕入口（兼容 gary & cyber-xiaoya 格式）
# ============================================================
@app.post("/api/danmaku")
async def ingest_danmaku(payload: Dict[str, Any]):
    # 单平台拦截：带 platform 字段且不是当前平台的消息直接丢弃，杜绝误开的采集器串流
    incoming_plat = str(payload.get("platform") or "").strip().lower()
    if incoming_plat and incoming_plat not in ("mock", "webui", prof_mgr.active_key):
        return {"code": 0, "msg": f"ignored_inactive_platform:{incoming_plat}"}
    state.last_danmaku_ts = time.time()
    user = str(payload.get("user") or payload.get("nick") or "匿名观众").strip()
    text = str(payload.get("text") or payload.get("content") or "").strip()
    kind = str(payload.get("kind") or "chat").lower()
    gift_val = payload.get("gift", 0)
    is_gift = kind == "gift" or (isinstance(gift_val, (int, float)) and gift_val > 0)

    if is_gift:
        item = {
            "kind": "gift",
            "user": user,
            "gift_item": payload.get("gift_item", "礼物"),
            "gift_num": payload.get("gift_num", 1),
        }
        state.priority_queue.append(item)
        # 1. 0 延迟立即触发墨镜/奶茶/比心等舞台互动画面
        inter = match_gift_interaction("gift", gift_item=item["gift_item"])
        if inter:
            await broadcast_play_action(inter, user=user, gift_item=item["gift_item"], gift_num=item["gift_num"])
        # 2. 若当前正在播讲稿/暖场/点播段落，柔和打断让礼物 1 秒内回应（不打断其他观众问答）
        await soft_interrupt_for_priority()
        state.notify_wake()
        await state.add_log("gift", f"[礼物] {user} 送出 {item['gift_item']} x{item['gift_num']}")
        await state.broadcast("status", state.snapshot())
        return {"code": 0, "msg": "queued_priority"}

    # 隐私硬拦截：含 7 位以上连续数字（疑似手机号 / 证件号 / 银行卡号）一律不处理、不上屏、日志不回显原文
    if PII_DIGITS_RE.search(re.sub(r"[\s\-_.]", "", text)):
        await state.add_log("warn", f"[拦截] {user}: 弹幕含疑似手机号/证件号/卡号（已隐藏，不上屏）")
        return {"code": 1, "msg": "blocked:pii_digits"}

    reason = check_moderation(text)
    if reason:
        # 命中屏蔽词但当前讲稿有 L1 安全标准答案（如 验证码、买课、代写作业）：直接念标准答案，绝不交给大模型
        safe = None
        if reason.startswith("命中屏蔽词"):
            safe = (kb_engine.match_direct_faq(text) if kb_ready() else None) or match_safety_faq(text)
        if safe:
            state.priority_queue.append({"kind": "safe_faq", "user": user, "answer": safe["answer"]})
            await soft_interrupt_for_priority()
            state.notify_wake()
            await state.add_log("warn", f"[安全直出] {user} 命中屏蔽词，改念讲稿安全答案「{safe['keyword']}」（{reason}）")
            await state.broadcast("status", state.snapshot())
            return {"code": 0, "msg": "queued_safe_answer"}
        await state.add_log("warn", f"[拦截] {user}: {text} ({reason})")
        return {"code": 1, "msg": f"blocked:{reason}"}

    max_q = int(state.config.get("scheduler", {}).get("max_queue_size", 30))
    if len(state.normal_queue) >= max_q:
        state.normal_queue.popleft()
    chat_item = {"kind": "chat", "user": user, "text": text}
    state.normal_queue.append(chat_item)
    inter = match_gift_interaction("chat", text=text)
    if inter:
        await broadcast_play_action(inter, user=user)
        await soft_interrupt_for_priority()
    elif kb_cfg_effective().get("enabled", True) and state.current_speech_kind in ("narration", "warmup"):
        # 说法明确的主题点播（如“先讲讲防骗”）不调 AI 秒判：插到队首并柔和打断讲稿，1~2 秒内开讲
        topic_sync_cfg()
        qc = topic_index.quick_check(text, kb_engine.active_doc)
        in_cooldown = bool(qc) and (time.time() - state.topic_last_played.get(qc["tid"], 0.0)
                                    < float(topic_index.cfg.get("cooldown_sec", 300)))
        if qc and not in_cooldown:
            try:
                state.normal_queue.remove(chat_item)
            except ValueError:
                pass
            state.normal_queue.appendleft(chat_item)
            await state.add_log("info", f"[主题点播] {user} 想听「{qc['title']}」，先停下当前讲稿")
            await soft_interrupt_for_priority()
    state.notify_wake()
    await state.add_log("danmaku", f"[弹幕] {user}: {text}")
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "msg": "queued"}


# ============================================================
# WebUI 手动控制接口
# ============================================================
class EchoReq(BaseModel):
    text: str
    interrupt: bool = False
    audiotype: Optional[int] = None


@app.post("/api/echo")
async def manual_echo(req: EchoReq):
    ok = await lt_send_speech(
        req.text, question_banner="主播插播", interrupt=req.interrupt, audiotype=req.audiotype
    )
    return {"code": 0 if ok else 1, "ok": ok}


@app.post("/api/interrupt")
async def manual_interrupt():
    ok = await lt_interrupt()
    await state.broadcast("status", state.snapshot())
    return {"code": 0 if ok else 1, "ok": ok}


@app.post("/api/action/play")
async def api_play_action(payload: Dict[str, Any]):
    """WebUI 一键测试礼物/手势互动（戴墨镜 / 拿奶茶 / 比心 / 挥手 / 点赞）。"""
    action_id = str(payload.get("action_id", "")).strip().lower()
    user = str(payload.get("user", "测试观众")).strip() or "测试观众"
    speak = bool(payload.get("speak", True))
    gi = state.config.get("gift_interactions", {})
    rule = next((r for r in gi.get("rules", []) if str(r.get("id", "")).lower() == action_id), None)
    if not rule:
        rule = gi.get("default_gesture") or {"id": action_id or "heart", "label": "互动特效", "sticker": action_id or "heart", "duration_sec": 4.0}
    res = await broadcast_play_action(rule, user=user, gift_item=rule.get("label", ""), gift_num=1)
    if speak and state.session_id:
        say = rule.get("chat_reply") or ""
        if say and not is_ai_popsci_context():
            say = re.sub(r"实用的 AI 小知识|AI 科普|AI 趣味科普课堂", "精彩内容", say)
        if say:
            if state.is_speaking and state.current_speech_kind in ("narration", "warmup"):
                await lt_interrupt()
            asyncio.create_task(lt_send_speech(say, question_banner=f"互动特效 · {rule.get('label', '')}", audiotype=rule.get("audiotype"), cacheable=True, speech_kind="action"))
    await state.add_log("info", f"触发舞台互动：{res['label']}（{'真实短片+舞台动效' if res['video_url'] else '人物动作+道具粒子'}）")
    return {"code": 0, "action": res}


@app.post("/api/action/upload")
async def api_upload_action_video(
    action_id: str = Form(...),
    avatar_id: str = Form(""),
    video_file: UploadFile = File(...),
):
    """上传指定主播的真实动作短片（戴墨镜 / 拿奶茶 / 比心 / 挥手 / 点赞）。"""
    act_id = action_id.strip().lower()
    gi = state.config.get("gift_interactions", {})
    rule = next((r for r in gi.get("rules", []) if str(r.get("id", "")).lower() == act_id), None)
    if not rule:
        return {"code": 1, "msg": f"未知动作类型: {action_id}"}
    fname = Path(str(rule.get("video") or f"act_{act_id}.mp4")).name
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (avatar_id or "").strip())
    target_dir = (ACTION_DIR / aid) if aid else ACTION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / fname
    raw = await video_file.read()
    if not raw:
        return {"code": 1, "msg": "上传文件为空"}
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp_path.write_bytes(raw)
    os.replace(tmp_path, target_path)
    marker_file = target_dir / ".custom_clips.json"
    custom = load_json(marker_file, [])
    if not isinstance(custom, list):
        custom = []
    if fname not in custom:
        custom.append(fname)
        save_json(marker_file, custom)
    _ACTION_CLIP_CACHE.pop(str(target_path), None)
    await state.add_log("info", f"已上传真实动作短片：{target_dir.name}/{fname}")
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "msg": "ok", "path": f"{aid}/{fname}" if aid else fname}


@app.post("/api/action/{audiotype}")
async def trigger_action(audiotype: int):
    if not state.session_id:
        return {"code": 1, "msg": "未绑定 sessionid"}
    try:
        resp = await http_client.post(
            lt_url("/set_audiotype"),
            json={"sessionid": state.session_id, "audiotype": audiotype},
            timeout=3.0,
        )
        await state.add_log("info", f"手动触发动作槽位 audiotype={audiotype}")
        return resp.json()
    except Exception as exc:
        return {"code": 1, "msg": str(exc)}


@app.get("/api/status")
async def get_status():
    return {
        "status": state.snapshot(),
        "logs": list(state.logs),
        "providers": [
            {
                "name": p["name"],
                "model": p["model"],
                "enabled": p.get("enabled", False),
                "has_key": not is_placeholder_key(p.get("api_key")),
            }
            for p in state.config.get("llm_providers", [])
        ],
    }


@app.post("/api/settings")
async def update_settings(patch: Dict[str, Any]):
    sched = state.config.setdefault("scheduler", {})
    for k in ("auto_reply", "auto_warmup", "gift_interrupt_narration", "warmup_interval_sec"):
        if k in patch:
            sched[k] = patch[k]
    if "session_id" in patch and patch["session_id"]:
        state.session_id = str(patch["session_id"]).strip()
    save_json(CONFIG_PATH, state.config)
    await state.broadcast("status", state.snapshot())
    await state.add_log("info", f"已更新策略配置: {patch}")
    return {"code": 0, "status": state.snapshot()}


@app.post("/api/clear_queue")
async def clear_queue():
    state.priority_queue.clear()
    state.normal_queue.clear()
    state.topic_pending.clear()
    await state.broadcast("status", state.snapshot())
    await state.add_log("info", "已清空待回复弹幕队列与排队中的点播主题")
    return {"code": 0}


@app.post("/api/reload")
async def reload_config():
    state.reload_files()
    prof_mgr.reload()
    kb_msg = await kb_load_for_platform(reset_cursor=False)
    apply_platform_runtime()
    topic_sync_cfg()
    topic_index.ensure_fresh(0)
    _OBS_PROBE["pending_push"] = True
    _OBS_PROBE["lock_until"] = time.time() + 10.0
    _OBS_PROBE["scene"] = ""
    await state.broadcast("bg_update", resolve_bg_payload(kb_engine.last_board.get("bg_video", "")))
    await state.broadcast("status", state.snapshot())
    await state.broadcast("overlay_update", resolve_overlay_cfg())
    await state.broadcast("preview_update", preview_snapshot())
    await state.add_log("info", f"已热重载 config / moderation / warmup_topics / platform_profiles | {kb_msg}")
    return {"code": 0}


@app.get("/api/events")
async def sse_events(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    state.sse_queues.append(q)

    async def event_stream():
        try:
            init_data = json.dumps({"type": "status", **state.snapshot()}, ensure_ascii=False)
            yield f"data: {init_data}\n\n"
            board = json.dumps({"type": "board_update", **render_board(kb_engine.last_board)}, ensure_ascii=False)
            yield f"data: {board}\n\n"
            bg_init = json.dumps({"type": "bg_update", **resolve_bg_payload(kb_engine.last_board.get("bg_video", ""))}, ensure_ascii=False)
            yield f"data: {bg_init}\n\n"
            yield f"data: {json.dumps({'type': 'health', **health_mon.last_report}, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in state.sse_queues:
                state.sse_queues.remove(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ============================================================
# TTS 热切换与一键克隆
# ============================================================
@app.post("/api/tts/config")
async def update_tts_config(patch: Dict[str, Any]):
    tts_cfg = state.config.setdefault("tts", {})
    if "mode" in patch:
        tts_cfg["mode"] = patch["mode"]
    if "enable_cache" in patch:
        tts_cfg["enable_cache"] = bool(patch["enable_cache"])
    mode = str(tts_cfg.get("mode", "edgetts"))
    sf = tts_cfg.setdefault("siliconflow", {})
    gs = tts_cfg.setdefault("gptsovits", {})
    v = str(patch.get("voice") or "").strip()
    if mode == "edgetts" and v:
        state.config.setdefault("livetalking", {})["tts_voice"] = v
    elif mode == "gptsovits":
        if "ref_audio_path" in patch and patch["ref_audio_path"]:
            gs["ref_audio_path"] = str(patch["ref_audio_path"]).strip()
        elif v:
            gs["ref_audio_path"] = v
        if "prompt_text" in patch:
            gs["prompt_text"] = str(patch["prompt_text"] or "").strip()
    elif v:
        sf["voice"] = v
    for k in ("speed", "emotion_prefix"):
        if k in patch and patch[k] not in (None, ""):
            sf[k] = patch[k]
    if "emotion_prefix" in patch and patch["emotion_prefix"] == "":
        sf["emotion_prefix"] = ""
    save_json(CONFIG_PATH, state.config)
    await state.broadcast("status", state.snapshot())
    await state.add_log("info", f"已热切换 TTS 配置: mode={tts_cfg.get('mode')}")
    return {"code": 0, "tts": tts_cfg}


@app.post("/api/tts/upload_voice")
async def upload_voice(
    file: UploadFile = File(...),
    custom_name: str = Form(...),
    ref_text: str = Form(...),
):
    sf_cfg = state.config.get("tts", {}).get("siliconflow", {})
    raw_bytes = await file.read()
    res = await tts_engine.upload_siliconflow_voice(
        http_client, sf_cfg, file.filename or "ref.mp3", raw_bytes, custom_name, ref_text
    )
    if res.get("code") == 0 and res.get("uri"):
        state.config.setdefault("tts", {})["mode"] = "siliconflow"
        state.config["tts"].setdefault("siliconflow", {})["voice"] = res["uri"]
        save_json(CONFIG_PATH, state.config)
        await state.add_log("info", f"克隆音色上传成功并已启用: {res['uri']}")
        await state.broadcast("status", state.snapshot())
    return res


@app.get("/api/tts/voices")
async def get_uploaded_voices():
    sf_cfg = state.config.get("tts", {}).get("siliconflow", {})
    custom = await tts_engine.list_siliconflow_voices(http_client, sf_cfg)
    sf_builtin = [
        {"id": "FunAudioLLM/CosyVoice2-0.5B:benjamin", "label": "班杰明 · 低沉磁性男声（说书推荐）"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:alex", "label": "亚历克斯 · 沉稳大气男声"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:charles", "label": "查尔斯 · 儒雅温和男声"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:david", "label": "大卫 · 活力青年男声"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:anna", "label": "安娜 · 亲和温柔女声"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:bella", "label": "贝拉 · 甜美清晰女声"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:claire", "label": "克莱尔 · 优雅知性女声"},
        {"id": "FunAudioLLM/CosyVoice2-0.5B:diana", "label": "戴安娜 · 元气活泼女声"},
    ]
    edgetts = [
        {"id": "zh-CN-YunjianNeural", "label": "云健 · 稳重评书叙事男声"},
        {"id": "zh-CN-YunxiNeural", "label": "云希 · 温和青年男声"},
        {"id": "zh-CN-YunyangNeural", "label": "云扬 · 专业新闻播音男声"},
        {"id": "zh-CN-YunxiaNeural", "label": "云夏 · 阳光活力男声"},
        {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 亲和甜美女声"},
        {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 元气活泼女声"},
        {"id": "zh-CN-liaoning-XiaobeiNeural", "label": "晓北 · 东北幽默女声"},
        {"id": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮 · 陕西特色女声"},
        {"id": "zh-HK-WanLungNeural", "label": "云龙 · 粤语沉稳男声"},
        {"id": "zh-HK-HiuMaanNeural", "label": "晓曼 · 粤语亲切女声"},
        {"id": "zh-TW-YunJheNeural", "label": "云哲 · 台湾温和男声"},
        {"id": "zh-TW-HsiaoChenNeural", "label": "晓臻 · 台湾知性女声"},
        {"id": "en-US-JennyNeural", "label": "珍妮 · 美式亲和女声"},
        {"id": "en-US-AndrewNeural", "label": "安德鲁 · 美式温暖男声"},
    ]
    vdir = BASE_DIR.parent / "voices"
    gsv = [{"id": str(p), "label": f"本地参考音频 · {p.name}"} for p in sorted(vdir.glob("*")) if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".flac")] if vdir.is_dir() else []
    return {"code": 0, "voices": custom, "catalog": {"edgetts": edgetts, "siliconflow": sf_builtin, "gptsovits": gsv}}


# ============================================================
# 衣柜换装 / 形象制作
# ============================================================
class SwitchAvatarReq(BaseModel):
    avatar_id: str
    announce: str = ""
    force: bool = True


@app.post("/api/avatar/switch")
async def api_switch_avatar(req: SwitchAvatarReq):
    return await trigger_switch_avatar(
        req.avatar_id, reason="WebUI 一键换装", announce_text=req.announce, force=req.force
    )


@app.get("/api/avatar/list")
async def api_avatar_list():
    """列出 avatars/ 下已就绪（含 unet.onnx 或 ultralight.pth）的 Ultralight 形象。"""
    d = avatars_dir()
    items = []
    if d and d.exists():
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and not sub.name.startswith(("__", ".")):
                meta = load_json(sub / "meta.json", {})
                items.append({
                    "id": sub.name,
                    "ready": _ul_avatar_ready(sub),
                    "asr": meta.get("asr", "hubert"),
                    "fps": meta.get("fps", 25),
                    "has_onnx": (sub / "unet.onnx").is_file(),
                    "has_pth": (sub / "ultralight.pth").is_file(),
                })
    return {"code": 0, "dir": str(d) if d else "", "avatars": items}


@app.post("/api/avatar/create")
async def api_create_avatar(
    video_file: UploadFile = File(...),
    avatar_id: str = Form(...),
    outfit_name: str = Form(...),
    keywords: str = Form(""),
    asr: str = Form("hubert"),
    epochs: int = Form(100),
    batch_size: int = Form(16),
    ckpt_file: Optional[UploadFile] = File(None),
    force: str = Form(""),
):
    """代理调用 Ultralight 引擎 POST /api/avatar/task（提交预处理 + 训练/权重导入 + ONNX 导出任务）"""
    avatar_id = re.sub(r"[^a-zA-Z0-9_\-]", "", avatar_id.strip())
    if not avatar_id:
        return {"code": 1, "msg": "avatar_id 仅允许英文、数字和下划线"}
    asr_clean = asr.strip().lower() if asr.strip().lower() in ("hubert", "wenet") else "hubert"
    raw_bytes = await video_file.read()
    files: Dict[str, Any] = {"video_file": (video_file.filename or f"{avatar_id}.mp4", raw_bytes, "video/mp4")}
    if ckpt_file is not None and ckpt_file.filename:
        ckpt_bytes = await ckpt_file.read()
        if ckpt_bytes:
            files["ckpt_file"] = (ckpt_file.filename, ckpt_bytes, "application/octet-stream")
    data = {
        "avatar_id": avatar_id,
        "name": outfit_name,
        "asr": asr_clean,
        "epochs": str(max(1, min(500, int(epochs)))),
        "batch_size": str(max(1, min(32, int(batch_size)))),
        "force": "1" if str(force).strip().lower() in ("1", "true", "yes", "on") else "",
    }
    token = str(os.environ.get("UL_API_TOKEN") or state.config.get("ultralight", {}).get("api_token") or "").strip()
    try:
        resp = await http_client.post(
            lt_url("/api/avatar/task"), files=files, data=data, timeout=120.0,
            headers={"X-UL-Token": token} if token else None,
        )
        res = resp.json()
        if res.get("code") == 0:
            wardrobe = state.config.setdefault("wardrobe", [])
            if not any(x.get("id") == avatar_id for x in wardrobe):
                kw_list = [k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()]
                if outfit_name not in kw_list:
                    kw_list.insert(0, outfit_name)
                wardrobe.append({
                    "id": avatar_id,
                    "name": outfit_name,
                    "keywords": kw_list,
                    "announce": f"已经换好{outfit_name}啦，大家觉得怎么样？",
                })
                save_json(CONFIG_PATH, state.config)
                await state.broadcast("status", state.snapshot())
            task_id = (res.get("data") or {}).get("task_id", "")
            await state.add_log("info", f"已提交 Ultralight 形象训练/打包任务: {avatar_id} ({asr_clean}, task_id={task_id})")
        return res
    except Exception as exc:
        return {"code": 1, "msg": f"提交 Ultralight 引擎失败: {exc}"}


@app.get("/api/avatar/task/{task_id}")
async def api_avatar_task_status(task_id: str):
    try:
        resp = await http_client.get(lt_url(f"/api/avatar/task/{task_id}"), timeout=5.0)
        return resp.json()
    except Exception as exc:
        return {"code": 1, "msg": str(exc)}


# ============================================================
# 单平台一键切播
# ============================================================
class SwitchPlatformReq(BaseModel):
    platform: str
    room_id: str = ""
    sync_obs: bool = True
    start_collector: bool = True
    orientation: str = ""
    obs_profile: str = ""
    obs_scene: str = ""
    kb_doc: str = ""
    kb_narration_mode: Optional[bool] = None


class ObsSyncReq(BaseModel):
    obs_profile: str = ""
    obs_scene: str = ""
    orientation: str = ""


def hub_url() -> str:
    srv = state.config.get("server", {})
    return f"http://{srv.get('host', '127.0.0.1')}:{int(srv.get('port', 28910))}"


@app.get("/api/platform/list")
async def api_platform_list():
    return {"code": 0, "active": prof_mgr.active_key, "room": prof_mgr.active_room,
            "platforms": prof_mgr.list_profiles()}


@app.post("/api/platform/switch")
async def api_switch_platform(req: SwitchPlatformReq):
    key = req.platform.strip().lower()
    if key not in prof_mgr.data.get("profiles", {}):
        return {"code": 1, "msg": f"未找到平台预设: {key}"}
    if state.switching_avatar:
        return {"code": 1, "msg": "正在换装重连中，请稍后再切平台"}
    # 1. 打断播报 + 清空上一场残留队列
    await lt_interrupt()
    state.priority_queue.clear()
    state.normal_queue.clear()
    # 2. 激活平台：人设 / 暖场池 / 违禁词 / TTS / 叠层全部按平台运行时叠加
    prof_mgr.set_active(key, req.room_id)
    prof = plat()
    if req.orientation in ("landscape", "portrait"):
        prof["orientation"] = req.orientation
    if req.obs_profile:
        prof["obs_profile"] = req.obs_profile
    if req.obs_scene:
        prof["obs_scene"] = req.obs_scene
    if req.kb_doc:
        state.config.setdefault("knowledge_base", {})["active_doc"] = req.kb_doc
        prof["kb_doc"] = req.kb_doc
    if req.kb_narration_mode is not None:
        state.config.setdefault("knowledge_base", {})["narration_mode"] = bool(req.kb_narration_mode)
        prof["kb_narration_mode"] = bool(req.kb_narration_mode)
    # 3. 切换讲稿与播讲策略（先加载讲稿章节，再刷新暖场池）
    state.kb_mode_override = None
    kb_engine.paused = False
    state.beats_since_warmup = 0
    kb_msg = await kb_load_for_platform(reset_cursor=bool(prof.get("kb_reset_cursor", False)))
    apply_platform_runtime()
    # 4. 停旧启新弹幕采集器（独占，仅该进程走代理）
    if req.start_collector:
        collector_msg = prof_mgr.start_collector(prof, prof_mgr.active_room, hub_url())
    else:
        prof_mgr.stop_collector()
        collector_msg = "未启动采集器"
    prof_mgr.last_collector_msg = collector_msg
    # 5. OBS WebSocket 联动切配置文件与场景
    obs_msg = "已跳过 OBS 联动"
    if req.sync_obs:
        obs_msg = await asyncio.to_thread(
            prof_mgr.sync_obs_blocking, prof.get("obs_profile", ""), prof.get("obs_scene", "")
        )
    prof_mgr.last_obs_msg = obs_msg
    _mark_obs_authoritative(obs_msg, prof.get("obs_scene", ""), prof.get("obs_profile", ""), lock_sec=12.0)
    # 6. 可选：该平台指定了专属形象（如竖屏底片），自动换装
    avatar_msg = ""
    target_avatar = str(prof.get("avatar_id", "")).strip()
    if target_avatar and target_avatar != state.config.get("livetalking", {}).get("avatar_id", ""):
        res = await trigger_switch_avatar(target_avatar, reason=f"切播 {prof.get('label', key)}", force=True)
        avatar_msg = f" | 形象: {res.get('msg')}"
    state.last_idle_since = time.time()
    label = prof.get("label", key)
    await broadcast_bg_update(kb_engine.last_board.get("bg_video", ""))
    await state.add_log("info", f"已切换单平台直播 -> [{label}] {prof.get('canvas', '')} | {kb_msg} | {collector_msg} | {obs_msg}{avatar_msg}")
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "active_profile": key, "label": label, "kb_msg": kb_msg,
            "collector_msg": collector_msg, "obs_msg": obs_msg}


@app.post("/api/obs/sync")
async def api_obs_sync(req: ObsSyncReq):
    """立即同步 OBS 配置文件、场景与横竖屏预览方向（同时开启浏览器源监听输出）。"""
    prof = plat()
    if req.orientation in ("landscape", "portrait"):
        prof["orientation"] = req.orientation
    if req.obs_profile:
        prof["obs_profile"] = req.obs_profile
    if req.obs_scene:
        prof["obs_scene"] = req.obs_scene
    obs_msg = await asyncio.to_thread(
        prof_mgr.sync_obs_blocking,
        req.obs_profile or prof.get("obs_profile", ""),
        req.obs_scene or prof.get("obs_scene", ""),
    )
    prof_mgr.last_obs_msg = obs_msg
    _mark_obs_authoritative(obs_msg, prof.get("obs_scene", ""), prof.get("obs_profile", ""), lock_sec=12.0)
    await state.broadcast("bg_update", resolve_bg_payload(kb_engine.last_board.get("bg_video", "")))
    await state.broadcast("status", state.snapshot())
    await state.add_log("info", f"OBS 同步：{obs_msg}")
    return {"code": 0, "obs_msg": obs_msg}


@app.post("/api/platform/stop_collector")
async def api_stop_collector():
    prof_mgr.stop_collector()
    prof_mgr.stopped_by_user = True
    prof_mgr.last_collector_msg = "采集器已停止"
    await state.add_log("info", "已停止当前弹幕采集子进程")
    await state.broadcast("status", state.snapshot())
    return {"code": 0}


# ============================================================
# 知识库讲稿：绑定 / 控制 / 检索测试 / 上传 / 一键拆稿
# ============================================================
def _safe_doc_name(name: str, default_stem: str = "") -> str:
    stem = Path(str(name or "").strip()).name
    stem = re.sub(r"\.(?:md|markdown|txt|docx)$", "", stem, flags=re.I)
    stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", stem).strip("_") or default_stem or f"script_{int(time.time())}"
    return stem[:60] + ".md"


def _platforms_using_doc(doc: str) -> List[str]:
    """哪些平台绑定了这篇讲稿（删除前检查）。"""
    out = [str(pr.get("label", k)) for k, pr in prof_mgr.data.get("profiles", {}).items()
           if str(pr.get("kb_doc", "")).strip() == doc]
    if str(state.config.get("knowledge_base", {}).get("active_doc", "")).strip() == doc:
        out.append("config.json 默认讲稿")
    return out


class BindKbReq(BaseModel):
    platform: str = ""
    doc: str


@app.post("/api/platform/bind_kb")
async def api_bind_platform_kb(req: BindKbReq):
    key = (req.platform or prof_mgr.active_key).strip().lower()
    raw_doc = req.doc.strip().replace("\\", "/")
    doc_p = kb_engine.doc_path(raw_doc)
    if not raw_doc or not doc_p.is_file():
        return {"code": 1, "msg": f"讲稿不存在: {req.doc}"}
    try:
        doc = doc_p.relative_to(kb_engine.kb_dir).as_posix()
    except Exception:
        doc = doc_p.name
    if not prof_mgr.bind_kb_doc(key, doc):
        return {"code": 1, "msg": f"未知平台: {key}"}
    if key == prof_mgr.active_key:
        state.config.setdefault("knowledge_base", {})["active_doc"] = doc
        save_json(CONFIG_PATH, state.config)
        msg = await kb_load_for_platform(reset_cursor=True)
        apply_platform_runtime()
        await state.broadcast("bg_update", resolve_bg_payload(kb_engine.last_board.get("bg_video", "")))
    else:
        res = await kb_engine.build_index(http_client, doc_name=doc, activate=False)   # 预建索引，切过去秒开
        msg = f"已预建索引：{res.get('msg')}"
    label = prof_mgr.data["profiles"][key].get("label", key)
    await state.add_log("info", f"已把讲稿 [{doc}] 绑定到 [{label}] | {msg}")
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "platform": key, "doc": doc, "msg": msg}


@app.get("/api/kb/status")
async def api_kb_status():
    return {"code": 0, "kb": kb_snapshot()}


@app.post("/api/kb/control")
async def api_kb_control(payload: Dict[str, Any]):
    action = str(payload.get("action", ""))
    if action == "toggle_pause":
        kb_engine.paused = not kb_engine.paused
        await state.add_log("info", "讲稿解说已" + ("暂停" if kb_engine.paused else "恢复"))
    elif action == "toggle_mode":
        cur = bool(kb_cfg_effective().get("narration_mode", True))
        state.kb_mode_override = not cur
        await state.add_log("info", "已切换为「" + ("章节连播" if state.kb_mode_override else "仅讲稿答疑 + 暖场") + "」（切平台后恢复预设）")
    elif action == "jump":
        kb_engine.jump_to(int(payload.get("index", 0)))
        await state.broadcast("board_update", render_board(kb_engine.last_board))
        await state.add_log("info", f"已跳到第 {kb_engine.cursor + 1} 段：{kb_engine.last_board.get('chapter', '')}")
    elif action == "reindex":
        res = await kb_engine.build_index(http_client, doc_name=kb_doc_for_platform(), force=True, reset_cursor=False)
        await state.add_log("info", f"已重建讲稿索引：{res.get('msg')}（{'向量' if res.get('vectorized') else '词法'}）")
    elif action == "select_doc":
        doc_name = str(payload.get("doc", "")).strip()
        if not doc_name:
            return {"code": 1, "msg": "未指定讲稿文件名"}
        state.config.setdefault("knowledge_base", {})["active_doc"] = doc_name
        prof = prof_mgr.get_active()
        if prof is not None:
            prof["kb_doc"] = doc_name
            save_json(BASE_DIR / "platform_profiles.json", prof_mgr.data)
        save_json(CONFIG_PATH, state.config)
        res = await kb_engine.build_index(http_client, doc_name=doc_name, force=False, reset_cursor=bool(payload.get("reset_cursor", False)))
        apply_platform_runtime()
        await state.broadcast("board_update", render_board(kb_engine.last_board))
        await state.broadcast("bg_update", resolve_bg_payload(kb_engine.last_board.get("bg_video", "")))
        await state.add_log("info", f"已切换讲稿：{doc_name}（{res.get('msg', '')}）")
    else:
        return {"code": 1, "msg": f"未知操作: {action}"}
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "kb": kb_snapshot()}


@app.post("/api/kb/test_search")
async def api_kb_test_search(payload: Dict[str, Any]):
    q = str(payload.get("query", "")).strip()
    if not q:
        return {"code": 1, "msg": "请输入测试问题"}
    kbc = kb_cfg_effective()
    kb_engine.update_config(kbc)
    return {"code": 0, **(await kb_engine.hybrid_search(http_client, q, top_k=int(kbc.get("top_k", 3))))}


@app.get("/api/kb/doc/{name}")
async def api_kb_doc(name: str):
    p = kb_engine.kb_dir / Path(name).name
    if not p.exists():
        return {"code": 1, "msg": "讲稿不存在"}
    return {"code": 0, "doc": p.name, "content": p.read_text(encoding="utf-8", errors="ignore")}


MAX_IMPORT_BYTES = 3 * 1024 * 1024


@app.post("/api/kb/upload")
async def api_kb_upload(files: List[UploadFile] = File(...), bind_platform: str = Form("")):
    """导入 Markdown 讲稿（.md / .markdown / .txt，可多选）。
    任意普通 Markdown 都行：标题层级、列表、表格、引用、链接、图片、代码块会被纯规则整理成标准讲稿，
    原文措辞不改、不调大模型。同名讲稿直接覆盖更新；含直接对小朋友喊话的句子则拒绝导入。"""
    report: List[str] = []
    imported: List[str] = []
    current = kb_doc_for_platform()
    for f in files:
        fn = Path(f.filename or "").name or "未命名.md"
        if Path(fn).suffix.lower() not in IMPORT_EXTS:
            report.append(f"✗ {fn}：这里只收 .md / .markdown / .txt；Word 文档请用下方「把文章变成讲稿」")
            continue
        raw = await f.read()
        if len(raw) > MAX_IMPORT_BYTES:
            report.append(f"✗ {fn}：文件超过 3MB，请拆成几篇再导入")
            continue
        try:
            imp = import_markdown_document(extract_file_bytes(fn, raw), title=Path(fn).stem, source=fn)
        except Exception as exc:
            report.append(f"✗ {fn}：解析失败（{exc}）")
            continue
        if imp["minor_hits"]:
            h = imp["minor_hits"][0]
            report.append(f"✗ {fn}：没导入——有直接对小朋友喊话的句子「{h['text']}」。本直播间只面向成年人，"
                          f"请改成对家长说的话（如「家长朋友们可以这样跟孩子说」）再导入")
            continue
        st = imp["stats"]
        if not st.get("chunks"):
            report.append(f"✗ {fn}：没找到可以播讲的正文")
            continue
        name = _safe_doc_name(fn)
        existed = (kb_engine.kb_dir / name).exists()
        (kb_engine.kb_dir / name).write_text(imp["markdown"], encoding="utf-8")
        kb_engine.update_config(kb_cfg_effective())
        res = await kb_engine.build_index(http_client, doc_name=name, force=True, activate=False)
        if name == current:
            await kb_load_for_platform(reset_cursor=False)      # 正在播的讲稿被更新：原地刷新，进度保留
        imported.append(name)
        report.append(f"✓ {name}{'（同名旧版已覆盖更新）' if existed else ''}：{res.get('chapters', st['chapters'])} 章 / "
                      f"{res.get('chunks', st['chunks'])} 段 / 问答 {res.get('faqs', st['faqs'])} 条"
                      f"{'，' + ('向量' if res.get('vectorized') else '词法') + '索引已建好' if res.get('code') == 0 else ''}")
        report.extend("   · " + w for w in imp["warnings"])
    if imported and bind_platform.strip():
        r = await api_bind_platform_kb(BindKbReq(platform=bind_platform.strip(), doc=imported[0]))
        report.append(r.get("msg", ""))
    summary = "；".join(x for x in report if x.startswith(("✓", "✗")))
    await state.add_log("info", f"导入 Markdown 讲稿：{summary}")
    await state.broadcast("status", state.snapshot())
    return {"code": 0 if imported else 1, "docs": imported, "msg": "\n".join(report) or "没有选中文件"}


@app.post("/api/kb/delete")
async def api_kb_delete(payload: Dict[str, Any]):
    """删除讲稿（连同它的索引和断点）；正绑定在某个平台上的不让删。"""
    doc = Path(str(payload.get("doc", "")).strip()).name
    p = kb_engine.kb_dir / doc
    if not doc or p.suffix.lower() != ".md" or not p.exists():
        return {"code": 1, "msg": "讲稿不存在"}
    users = _platforms_using_doc(doc)
    if users or doc == kb_engine.active_doc:
        return {"code": 1, "msg": f"[{doc}] 正绑定在：{'、'.join(users) or '当前平台'}。先给这些平台换一篇讲稿再删"}
    p.unlink()
    try:
        kb_engine._index_file_for(doc).unlink(missing_ok=True)
    except Exception:
        pass
    kb_engine.doc_cursors.pop(doc, None)
    kb_engine.save_cursors()
    await state.add_log("info", f"已删除讲稿 [{doc}]")
    await state.broadcast("status", state.snapshot())
    return {"code": 0, "msg": f"已删除讲稿 [{doc}]"}


@app.post("/api/kb/convert")
async def api_kb_convert(
    source_type: str = Form("text"),
    url: str = Form(""),
    raw_text: str = Form(""),
    doc_filename: str = Form(""),
    style: str = Form(""),
    language: str = Form(""),
    faq_count: int = Form(8),
    bind_platform: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    if state.converting:
        return {"code": 1, "msg": "已有一篇正在拆稿，请等它完成"}
    state.converting = True
    await state.broadcast("status", state.snapshot())
    try:
        style = style.strip() or prof_mgr.active_key
        if style not in STYLE_GUIDES:
            style = "douyin"
        language = language.strip() or plat().get("kb_language", "zh")
        title, source, extracted = "", "", ""
        if source_type == "url":
            u = url.strip()
            if not u.startswith(("http://", "https://")):
                return {"code": 1, "msg": "请输入以 http:// 或 https:// 开头的网页链接"}
            await state.add_log("info", f"拆稿：正在抓取网页 {u}")
            title, extracted = await fetch_webpage_text(u)
            source = u
        elif source_type == "file":
            if file is None:
                return {"code": 1, "msg": "请选择要拆解的 .txt / .md / .docx 文件"}
            extracted = extract_file_bytes(file.filename or "input.txt", await file.read())
            title = Path(file.filename or "").stem
            source = file.filename or ""
        else:
            extracted = raw_text
        if state.session_id:
            await state.add_log("warn", "拆稿会占用免费大模型额度，直播中弹幕回复可能变慢，建议下播后再拆")
        await state.add_log("info", f"拆稿：原文 {len(extracted.strip())} 字，风格 {style}，语言 {language}，开始调用免费大模型...")

        async def _progress(msg: str) -> None:
            await state.add_log("info", msg)

        kbc = state.config.get("knowledge_base", {})
        conv = await convert_article_to_live_script(
            http_client, state.config.get("llm_providers", []), extracted, title=title, style=style,
            language=language, faq_count=max(2, min(15, int(faq_count))),
            audience=kbc.get("converter_audience", ""), max_chars=int(kbc.get("converter_max_chars", 30000)),
            progress=_progress, source=source,
        )
        name = _safe_doc_name(doc_filename, default_stem=title or "")        # 同名直接覆盖更新（不留旧版）
        (kb_engine.kb_dir / name).write_text(conv["markdown"], encoding="utf-8")
        kb_engine.update_config(kb_cfg_effective())
        idx = await kb_engine.build_index(http_client, doc_name=name, force=True, activate=False)
        bound = ""
        target = bind_platform.strip().lower()
        if target:
            r = await api_bind_platform_kb(BindKbReq(platform=target, doc=name))
            bound = target if r.get("code") == 0 else ""
        await state.add_log(
            "info",
            f"一键拆稿完成 -> [{name}]（节点 {conv['provider']} / {idx.get('chapters', 0)} 章 / "
            f"{idx.get('chunks', 0)} 段 / FAQ {idx.get('faqs', 0)} 条{' / 原文过长已截断' if conv.get('truncated') else ''}）"
            + (f"，已绑定到 {bound}" if bound else "，未绑定平台（可在讲稿下拉框选中后绑定）"),
        )
        return {"code": 0, "doc": name, "provider": conv["provider"], "chapters": idx.get("chapters", 0),
                "chunks": idx.get("chunks", 0), "faqs": idx.get("faqs", 0), "bound_platform": bound,
                "truncated": conv.get("truncated", False), "preview": conv["markdown"][:1500]}
    except Exception as exc:
        await state.add_log("error", f"拆稿失败: {exc}")
        return {"code": 1, "msg": str(exc)}
    finally:
        state.converting = False
        await state.broadcast("status", state.snapshot())


# ============================================================
# 健康诊断：手动复检 / 紧急解锁
# ============================================================
# ============================================================
# 主题点播 API（WebUI / 桌面端：目录、问法测试、手动点播、停止）
# ============================================================
def _topic_brief(t: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not t:
        return None
    return {"tid": t["tid"], "title": t["title"], "doc": t["doc"], "level": t["level"],
            "chapter": t["chapter"], "section": t["section"], "beats": len(t["chunks"]), "summary": t["summary"]}


@app.get("/api/topics")
async def api_topics():
    topic_sync_cfg()
    topic_index.ensure_fresh(0)
    return {"code": 0, "catalog": topic_index.catalog(), "built_at": topic_index.built_at,
            "status": topic_snapshot(), "tips": config_guide.KB_WRITING_TIPS}


@app.post("/api/topics/rebuild")
async def api_topics_rebuild():
    topic_sync_cfg()
    res = topic_index.build()
    await state.add_log("info", f"已重建主题点播目录：{res['topics']} 个主题 / {res['docs']} 份讲稿")
    await state.broadcast("topic_update", topic_snapshot())
    return {"code": 0, **res}


@app.post("/api/topics/test")
async def api_topics_test(payload: Dict[str, Any]):
    """问法测试：看一条弹幕会不会被识别成点播、点中哪个主题、为什么（可选是否真的调用 AI）。"""
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"code": 1, "msg": "请输入要测试的观众弹幕"}
    use_ai = bool(payload.get("use_ai", True))
    topic_sync_cfg()
    res = await topic_index.route(http_client, text, llm_providers_ready() if use_ai else [],
                                  active_doc=kb_engine.active_doc, allow_ai=use_ai)
    plan = topic_index.build_plan(res["tid"], user="测试观众") if res.get("tid") else None
    return {"code": 0, "is_request": res["is_request"], "method": res["method"], "score": res["score"],
            "reason": res["reason"], "phrase": res["phrase"], "topic": _topic_brief(res.get("topic")),
            "candidates": res["candidates"][:5], "ai": res.get("ai"),
            "first_beat": plan["beats"][0]["text"] if plan and plan["beats"] else ""}


@app.post("/api/topics/play")
async def api_topics_play(payload: Dict[str, Any]):
    tid = str(payload.get("tid") or "").strip()
    user = str(payload.get("user") or "主播").strip() or "主播"
    topic_sync_cfg()
    topic_index.ensure_fresh(0)
    res = await start_topic(tid, user, source="主播手动点播")
    if res.get("msg") == "started":
        await soft_interrupt_for_priority()
    return res


@app.post("/api/topics/stop")
async def api_topics_stop(payload: Optional[Dict[str, Any]] = None):
    if (payload or {}).get("clear_pending", True):
        state.topic_pending.clear()
    if state.topic_active:
        await finish_topic("已被主播手动停止")
        if state.current_speech_kind == "topic":
            await lt_interrupt()
    return {"code": 0, "status": topic_snapshot()}


# ============================================================
# 傻瓜配置向导 API（白话说明 / 一键预设 / 三问向导 / 配置体检一键修复）
# ============================================================
@app.get("/api/guide")
async def api_guide():
    topic_sync_cfg()
    topic_index.ensure_fresh()
    return {"code": 0, **config_guide.guide_bundle(BASE_DIR, ROOT_DIR, topic_count=len(topic_index.topics))}


@app.post("/api/guide/wizard")
async def api_guide_wizard(payload: Dict[str, Any]):
    """只预览，不保存：返回向导组合出的配置与白话解读。"""
    return {"code": 0, **config_guide.wizard(payload.get("answers") or {})}


@app.post("/api/guide/apply")
async def api_guide_apply(payload: Dict[str, Any]):
    """支持 {"preset": id} | {"wizard": {...answers}} | {"values": {"env.UL_BATCH": 4, ...}} | {"fix_index": n}。
    不接受任意补丁：只能写说明书里登记过的参数，或体检给出的修复项。"""
    try:
        if payload.get("preset"):
            patch = config_guide.preset_patch(str(payload["preset"]))
        elif isinstance(payload.get("wizard"), dict):
            patch = config_guide.wizard(payload["wizard"])["patch"]
        elif isinstance(payload.get("values"), dict):
            patch = config_guide.items_to_patch(payload["values"])
        elif payload.get("fix_index") is not None:
            bundle = config_guide.guide_bundle(BASE_DIR, ROOT_DIR, topic_count=len(topic_index.topics))
            idx = int(payload["fix_index"])
            fix = bundle["doctor"][idx].get("fix") if 0 <= idx < len(bundle["doctor"]) else None
            if not fix:
                return {"code": 1, "msg": "这一项没有可自动修复的设置"}
            patch = fix
        else:
            return {"code": 1, "msg": "请选择预设、回答向导或修改具体参数"}
        res = config_guide.apply_patch(BASE_DIR, ROOT_DIR, patch)
    except (ValueError, KeyError, IndexError) as exc:
        return {"code": 1, "msg": str(exc)}
    await reload_config()
    await state.add_log("info", f"[配置向导] {res['plain']} 改动：{', '.join(res['changed']) or '无变化'}")
    return {"code": 0, **res,
            "bundle": config_guide.guide_bundle(BASE_DIR, ROOT_DIR, topic_count=len(topic_index.topics))}


@app.get("/api/health")
async def api_health_check():
    report = await health_mon.run_all_checks(http_client, state, kb_engine, prof_mgr, health_context())
    await state.broadcast("health", report)
    return {"code": 0, **report}


@app.post("/api/health/unlock")
async def api_force_unlock():
    """换装或说话状态卡住时，一键解除换装锁和说话保护锁。"""
    state.switching_avatar = False
    state.min_busy_until = 0.0
    state.is_speaking = False
    state.last_idle_since = time.time()
    await state.add_log("warn", "已通过健康诊断执行强制解锁（重置换装锁与说话状态）")
    await state.broadcast("status", state.snapshot())
    return {"code": 0}


if __name__ == "__main__":
    import uvicorn

    srv = state.config.get("server", {})
    uvicorn.run(
        app,
        host=srv.get("host", "127.0.0.1"),
        port=int(srv.get("port", 28910)),
        reload=False,
    )
