# -*- coding: utf-8 -*-
"""
config_guide.py —— 专业配置的「白话讲解」+「傻瓜式一键配置」+「配置体检」（WebUI 与桌面端共用唯一真源）

  * GUIDE_ITEMS ：每个关键参数都有 白话说明(plain) + 专业说明(pro) + RTX 2080 推荐值 + 取值范围
  * PRESETS     ：一键预设（稳定开播 / 口型最清晰 / 最省资源 / 互动答疑优先 / 讲课连播优先）
  * wizard()    ：回答 3 个大白话问题，自动组合出整套配置
  * doctor()    ：按 i5-12400F / 32GB / RTX 2080 8GB 红线体检，给出“哪里不妥 + 一键修复”
  * apply_patch()：原子写入 config.json 与 env.bat（env.bat 保持纯 ASCII + CRLF）

参数路径约定：  "env.UL_MAX_SESSION" → env.bat ；  "scheduler.auto_reply" → config.json 嵌套键
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOST_PROFILE = "i5-12400F（6核12线程）/ 32GB 内存 / RTX 2080 8GB（Turing，仅 FP16）"

# ------------------------------------------------------------------ 参数说明书
GUIDE_ITEMS: List[Dict[str, Any]] = [
    # ---------------- 数字人引擎（env.bat，改完需重启引擎）
    {"key": "env.UL_MAX_SESSION", "group": "数字人引擎", "label": "同时输出几路数字人画面", "type": "int",
     "min": 1, "max": 3, "recommend": 1, "restart": "engine", "level": "basic",
     "plain": "就是同时给几个窗口推数字人画面。正式开播只要 OBS 这 1 路；开 2 路等于让 CPU 同时干两份视频编码的活，12400F 会吃紧、画面掉帧。",
     "pro": "aiortc 每个会话独立做软件视频编码，CPU 占用随路数线性增加；多余会话还会额外占一份帧队列内存。"},
    {"key": "env.UL_ASR", "group": "数字人引擎", "label": "口型驱动方式", "type": "choice",
     "choices": [["hubert", "HuBERT（口型更准，推荐）"], ["wenet", "WeNet（更省显卡）"]],
     "recommend": "hubert", "restart": "engine", "level": "basic",
     "plain": "决定“听声音对口型”用哪套耳朵。HuBERT 口型更准，约占 1.3GB 显存；WeNet 不到 1GB，口型略逊。每个主播训练时选的是哪种，播出时就会自动用哪种，这里只是默认值。",
     "pro": "HuBERT-Large FP16 25fps 特征 [16,32,32]；WeNet encoder.onnx 20fps 特征 [128,16,32]，按形象 meta.json 自动匹配。"},
    {"key": "env.UL_AV_OFFSET_MS", "group": "数字人引擎", "label": "音画同步微调(毫秒)", "type": "int",
     "min": -500, "max": 1000, "recommend": 0, "restart": "engine", "level": "basic",
     "plain": "正常保持 0。如果看直播时“声音说完了嘴还在动”，说明嘴型比声音慢，就把它调大（每次加 40，约 1 帧）；"
              "如果“嘴先动、声音后到”，就调成负数。改完重启数字人引擎再看。",
     "pro": "音频包发送时刻 = 会话时钟 t0 + 包序号×20ms + 偏移；视频帧不受影响。用于抵消浏览器/OBS 解码渲染的固定延迟差。"},
    {"key": "env.UL_MAX_PREVIEW", "group": "数字人引擎", "label": "最多几个预览窗口", "type": "int",
     "min": 0, "max": 3, "recommend": 1, "restart": "engine", "level": "advanced",
     "plain": "OBS 开播时，网页/桌面端的预览会共享 OBS 同一路画面，不额外算口型，但每多一个预览窗口要多一份视频编码。1 个就够看；填 0 表示 OBS 开播时不允许预览。",
     "pro": "role=preview 的 /offer 在已有主画面时创建 MirrorSession，复用渲染帧，只增加 aiortc 编码；超过上限返回错误。"},
    {"key": "env.UL_BACKEND", "group": "数字人引擎", "label": "口型模型运行方式", "type": "choice",
     "choices": [["onnx", "ONNX（推荐，更省更稳）"], ["torch", "PyTorch（调试用）"]],
     "recommend": "onnx", "restart": "engine", "level": "advanced",
     "plain": "一般不用动。ONNX 是“打包好的成品”，启动快、占用小；PyTorch 只在排查训练问题时用。",
     "pro": "ONNX Runtime CUDA EP（限显存池）vs torch eager；两者输出数值一致。"},
    {"key": "env.UL_MAX_FRAMES", "group": "数字人引擎", "label": "主播动作循环帧数", "type": "int",
     "min": 100, "max": 600, "recommend": 300, "restart": "engine", "level": "basic",
     "plain": "数字人身体动作循环用多少张图（25 张 = 1 秒）。300 张 = 12 秒动作循环，已经很自然；调太大很吃内存，换几套衣服就可能把 32GB 吃满。",
     "pro": "每帧常驻内存 ≈ 宽×高×3 字节；720×1280 约 2.8MB/帧，说话帧+待机帧两套。"},
    {"key": "env.UL_OUT_HEIGHT", "group": "数字人引擎", "label": "数字人画面输出高度", "type": "choice",
     "choices": [["1280", "1280（推荐，竖屏 720×1280）"], ["1080", "1080"], ["720", "720（最省）"],
                 ["1920", "1920（全高清，最吃 CPU）"], ["0", "0 = 保持原视频尺寸"]],
     "recommend": "1280", "restart": "engine", "level": "basic",
     "plain": "数字人画面先缩到多高再推给 OBS。直播平台竖屏一般就是 720×1280，再大观众也看不出来，反而让 CPU 编码更累。",
     "pro": "加载时按比例缩放帧与人脸框，降低常驻内存与 WebRTC 编码像素量。"},
    {"key": "env.UL_BATCH", "group": "数字人引擎", "label": "口型推理一次算几帧", "type": "int",
     "min": 1, "max": 8, "recommend": 4, "restart": "engine", "level": "advanced",
     "plain": "画面来不及时，一次多算几帧“追进度”。4 帧对 2080 刚好；调太大会让单次计算变慢。",
     "pro": "仅当 unet.onnx 带动态 batch 维（新导出的模型）或 torch 后端时生效，旧模型自动逐帧。"},
    {"key": "env.UL_ORT_GPU_MB", "group": "数字人引擎", "label": "口型模型最多占用显存(MB)", "type": "int",
     "min": 512, "max": 4096, "recommend": 2048, "restart": "engine", "level": "advanced",
     "plain": "给口型模型划一块显存“自留地”，防止它越用越多把 OBS 编码挤掉。2048MB 足够，整机稳定压在 4.5GB 以内。",
     "pro": "ONNX Runtime CUDA EP gpu_mem_limit + arena_extend_strategy=kSameAsRequested。"},
    {"key": "env.UL_MAX_LOADED", "group": "数字人引擎", "label": "同时常驻内存的主播/服装数", "type": "int",
     "min": 1, "max": 4, "recommend": 2, "restart": "engine", "level": "advanced",
     "plain": "换装时旧衣服先不卸载，切回来秒开。2 套 = 当前 + 上一套；再多就费内存。",
     "pro": "LRU 淘汰：超出数量时释放最久没用且不在播的形象帧缓存与模型会话。"},
    {"key": "env.UL_HOST", "group": "数字人引擎", "label": "引擎对谁开放", "type": "choice",
     "choices": [["127.0.0.1", "只给本机用（安全，推荐）"], ["0.0.0.0", "局域网都能访问（有风险）"]],
     "recommend": "127.0.0.1", "restart": "engine", "level": "advanced",
     "plain": "只给本机用最安全。改成局域网开放后，同一 WiFi 下的人都能给你的数字人塞话、提交训练任务。",
     "pro": "aiohttp 监听地址；中控通过 127.0.0.1:8020 访问，不需要对外开放。"},

    # ---------------- 直播画面
    {"key": "ui.preview_enabled", "group": "直播画面", "label": "直播画面预览", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "开着：网页控制台和桌面端能看到和 OBS 一样的直播画面（OBS 开播时共享同一路，不影响直播）；关掉：所有预览页自动断开，省一点 CPU。",
     "pro": "/api/preview 开关；关闭时广播 preview_update，舞台页 preview=1 主动断开 WebRTC 并停止重连。"},
    {"key": "overlay.font_size", "group": "直播画面", "label": "口播字幕字号(px)", "type": "int",
     "min": 14, "max": 80, "recommend": 24, "level": "basic",
     "plain": "字幕字有多大。调大以后会按屏幕宽度自动换行，不会跑出屏幕；竖屏会自动按 0.82 倍显示。长辈观众多可以调到 32~40。",
     "pro": "overlay.html #subtitle-box：overflow-wrap:anywhere + 按 sub_max_lines 分屏或缩小。"},
    {"key": "overlay.sub_max_lines", "group": "直播画面", "label": "字幕最多显示几行", "type": "int",
     "min": 1, "max": 5, "recommend": 3, "level": "basic",
     "plain": "字幕最多占几行，防止字调大后挡住主播的脸。一句话超过这么多行时，按下面的方式处理。",
     "pro": "subFits() 以 line-height 计算实际行数。"},
    {"key": "overlay.sub_overflow", "group": "直播画面", "label": "一句话太长放不下时", "type": "choice",
     "choices": [["paginate", "保持字号，分几屏轮播（推荐）"], ["shrink", "自动缩小字号塞进去"]],
     "recommend": "paginate", "level": "basic",
     "plain": "推荐“分屏轮播”：字号保持你设的大小，长句按标点拆成几屏，跟着说话节奏依次显示；选“自动缩小”则整句一次显示，字会变小。",
     "pro": "paginate：按标点贪心分页 + 二分切长段，每屏停留≈字数/4.6 秒；shrink：每次 -2px 直到放下（最小 14px，仍放不下转分页）。"},

    # ---------------- 弹幕与互动（config.json）
    {"key": "scheduler.auto_reply", "group": "弹幕互动", "label": "自动回答观众弹幕", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "开着：观众提问，主播讲完当前这句就回答；关掉：只讲稿子，不理弹幕。",
     "pro": "normal_queue 消费开关；礼物与安全直出不受影响。"},
    {"key": "scheduler.gift_interrupt_narration", "group": "弹幕互动", "label": "送礼/点播立刻打断讲稿", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "开着：有人送礼或点播主题，主播马上停下当前讲稿先回应，回应完自动接着讲。",
     "pro": "speech_kind 为 narration/warmup/topic 时调用 /interrupt_talk 并标记断点。"},
    {"key": "scheduler.auto_warmup", "group": "弹幕互动", "label": "冷场自动暖场", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "没人说话时主播自己找话题聊，避免直播间安静。",
     "pro": "warmup_topics.json 洗牌不重复抽取。"},
    {"key": "scheduler.warmup_interval_sec", "group": "弹幕互动", "label": "冷场多少秒后暖场", "type": "int",
     "min": 8, "max": 180, "recommend": 25, "level": "basic",
     "plain": "安静多少秒开始暖场。太短会显得话痨重复，25 秒左右最自然。",
     "pro": "距 last_idle_since 的秒数阈值。"},
    {"key": "scheduler.max_queue_size", "group": "弹幕互动", "label": "最多排队多少条弹幕", "type": "int",
     "min": 5, "max": 100, "recommend": 30, "level": "advanced",
     "plain": "弹幕太多时只留最新的这么多条，太旧的问题自动丢掉，免得主播回答十分钟前的问题。",
     "pro": "deque 溢出时 popleft 最旧项。"},
    {"key": "scheduler.min_speak_guard_sec", "group": "弹幕互动", "label": "每句话最短保护时长(秒)", "type": "float",
     "min": 0.5, "max": 5.0, "recommend": 1.8, "level": "advanced",
     "plain": "主播刚开口的这几秒内不会被下一句抢话，防止一句话被截断。",
     "pro": "min_busy_until 保护窗，覆盖 TTS 合成到引擎开始出声的间隙。"},

    # ---------------- 知识库讲解
    {"key": "knowledge_base.enabled", "group": "知识库讲解", "label": "启用知识库", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "开着：主播按讲稿讲课、答题优先查讲稿；关掉：全靠大模型自由发挥。",
     "pro": "KnowledgeBaseEngine 总开关（平台可单独覆盖）。"},
    {"key": "knowledge_base.narration_mode", "group": "知识库讲解", "label": "讲稿章节连播", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "开着：没人提问时主播一段接一段讲讲稿；关掉：只在有人问时才查讲稿回答。",
     "pro": "peek_beat/commit_beat 断点续讲。"},
    {"key": "knowledge_base.narration_gap_sec", "group": "知识库讲解", "label": "讲稿每段之间停几秒", "type": "float",
     "min": 1.0, "max": 15.0, "recommend": 3.5, "level": "basic",
     "plain": "讲完一段歇几秒再讲下一段。留点空让观众有机会打字提问；弹幕多的直播间可以调长一点。",
     "pro": "距上次说完的空闲阈值。"},
    {"key": "knowledge_base.warmup_every_n_beats", "group": "知识库讲解", "label": "每讲几段插一句互动话术", "type": "int",
     "min": 0, "max": 20, "recommend": 4, "level": "advanced",
     "plain": "讲几段就插一句“新来的朋友点个关注”之类的互动；填 0 就不插。",
     "pro": "beats_since_warmup 计数。"},
    {"key": "knowledge_base.chunk_max_chars", "group": "知识库讲解", "label": "每段口播最多多少字", "type": "int",
     "min": 30, "max": 160, "recommend": 65, "level": "advanced",
     "plain": "讲稿切成一段段来念，每段越短，主播开口越快、越好打断；65 字大约 15 秒。改了要重建索引。",
     "pro": "split_spoken_chunks 的 max_len；影响 TTS 首字延迟与可打断粒度。"},
    {"key": "knowledge_base.top_k", "group": "知识库讲解", "label": "回答问题时参考几段讲稿", "type": "int",
     "min": 1, "max": 8, "recommend": 3, "level": "advanced",
     "plain": "观众提问时，从讲稿里找最相关的几段给大模型参考。3 段刚好，太多会让回答又长又慢。",
     "pro": "hybrid_search top_k。"},
    {"key": "knowledge_base.min_score", "group": "知识库讲解", "label": "讲稿相关度门槛（关键词）", "type": "float",
     "min": 0.05, "max": 0.6, "recommend": 0.18, "level": "advanced",
     "plain": "问题和讲稿有多像才算“讲稿里有”。调高：更严格、少乱套讲稿；调低：更爱引用讲稿。",
     "pro": "L3 词法分数阈值（无向量 Key 时生效）。"},

    # ---------------- 主题点播（新）
    {"key": "knowledge_base.topic_router.enabled", "group": "主题点播", "label": "观众点播知识库主题", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "观众打“先讲讲防骗”“想听三国”，只要知识库里有这个主题，主播就把这部分讲稿完整讲一遍，讲完回到原来的章节。",
     "pro": "TopicIndex 跨讲稿主题目录 + 插播计划（topic plan），优先级：礼物 > 点播 > 普通提问 > 章节连播。"},
    {"key": "knowledge_base.topic_router.detect_mode", "group": "主题点播", "label": "怎么判断观众在点播", "type": "choice",
     "choices": [["auto", "关键词秒判 + AI 复核（推荐）"], ["ai", "每条都让 AI 判断（最准，最费额度）"],
                 ["rules", "只用关键词（不花 AI 额度）"]],
     "recommend": "auto", "level": "basic",
     "plain": "推荐「关键词+AI」：说法明显的（“讲讲三国”）关键词马上判；说得含糊的（“讲讲那个怎么防止被骗钱的事”）再让 AI 看一眼。",
     "pro": "auto：has_request_intent 命中且主题分 ≥ strong_score 直接接受；否则调用 LLM 只从目录里选编号；LLM 失败降级规则。"},
    {"key": "knowledge_base.topic_router.max_beats", "group": "主题点播", "label": "一次点播最多讲几段", "type": "int",
     "min": 2, "max": 20, "recommend": 6, "level": "basic",
     "plain": "点播一个主题连着讲几段（每段约 15 秒）。6 段约 1.5 分钟；讲课直播可以调大，互动直播调小。",
     "pro": "build_plan 截取主题前 N 个口播段 + 结束语。"},
    {"key": "knowledge_base.topic_router.answer_during_topic", "group": "主题点播", "label": "点播讲解中也回答提问", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "开着：点播讲到一半有人提问，先简短回答，再说“咱们接着说……”继续讲。",
     "pro": "每 answer_every_n_beats 段穿插消费一条 normal_queue。"},
    {"key": "knowledge_base.topic_router.cooldown_sec", "group": "主题点播", "label": "同一主题多久内不重复整段讲", "type": "int",
     "min": 0, "max": 1800, "recommend": 300, "level": "advanced",
     "plain": "刚讲过的主题，5 分钟内再有人点，就只简短回答，不再从头讲一遍，避免被刷屏点播。",
     "pro": "topic_last_played[tid] 冷却窗；冷却中回落 RAG 问答。"},
    {"key": "knowledge_base.topic_router.strong_score", "group": "主题点播", "label": "关键词直接点播的把握度", "type": "float",
     "min": 0.3, "max": 0.95, "recommend": 0.55, "level": "advanced",
     "plain": "关键词多像才不问 AI 直接开讲。调高：更多交给 AI 判断（更准但慢一点）；调低：更快但可能误判。",
     "pro": "TopicIndex.score 综合分阈值（标题/关键词/内容 IDF 重合 + 关键词命中加分）。"},

    # ---------------- 声音
    {"key": "tts.mode", "group": "主播声音", "label": "主播用什么声音", "type": "choice",
     "choices": [["edgetts", "Edge 免费声音（推荐起步）"], ["siliconflow", "硅基流动克隆声音（需 Key）"],
                 ["gptsovits", "本地 GPT-SoVITS（占 2~3GB 显存）"]],
     "recommend": "edgetts", "level": "basic",
     "plain": "免费声音最稳；克隆声音更像真人但要 Key；本地 GPT-SoVITS 会和数字人抢显卡，2080 上要谨慎。",
     "pro": "siliconflow/gptsovits 走 /humanaudio，失败可自动降级 Edge-TTS(/human)。"},
    {"key": "tts.fallback_to_edgetts", "group": "主播声音", "label": "克隆声音失败时自动换免费声音", "type": "bool",
     "recommend": True, "level": "basic",
     "plain": "克隆声音接口卡了或没额度时，自动换成免费声音继续说，保证直播不冷场。",
     "pro": "tts_engine 返回空音频时回落 /human。"},
    {"key": "tts.enable_cache", "group": "主播声音", "label": "讲稿声音缓存", "type": "bool",
     "recommend": True, "level": "advanced",
     "plain": "讲稿和暖场话术念过一次就存下来，下次直接用，又快又省额度。",
     "pro": "tts_cache/<md5>.wav；讲稿段落还会提前一段预合成。"},
]
ITEMS_BY_KEY = {it["key"]: it for it in GUIDE_ITEMS}

# ------------------------------------------------------------------ 一键预设
PRESETS: List[Dict[str, Any]] = [
    {"id": "stable_2080", "title": "🛡️ 稳定开播（RTX 2080 推荐）", "kind": "engine",
     "plain": "第一次开播就选它：只输出 1 路画面、画面缩到 1280 高、显存留足余量、HuBERT 口型；弹幕回答、讲稿连播、主题点播全开。",
     "env": {"UL_MAX_SESSION": "1", "UL_ASR": "hubert", "UL_BACKEND": "onnx", "UL_MAX_FRAMES": "300",
             "UL_OUT_HEIGHT": "1280", "UL_BATCH": "4", "UL_ORT_GPU_MB": "2048", "UL_MAX_LOADED": "2", "UL_HOST": "127.0.0.1",
             "UL_AV_OFFSET_MS": "0"},
     "config": {"scheduler": {"auto_reply": True, "auto_warmup": True, "gift_interrupt_narration": True},
                "knowledge_base": {"enabled": True, "narration_mode": True,
                                   "topic_router": {"enabled": True, "detect_mode": "auto"}}}},
    {"id": "lip_quality", "title": "👄 口型最清晰（近景特写）", "kind": "engine",
     "plain": "脸部占画面很大的近景主播用：不缩小画面(1920)、动作循环 400 帧。更吃内存和 CPU，只开数字人 + OBS 时用。",
     "env": {"UL_MAX_SESSION": "1", "UL_ASR": "hubert", "UL_MAX_FRAMES": "400", "UL_OUT_HEIGHT": "1920",
             "UL_BATCH": "4", "UL_ORT_GPU_MB": "2560", "UL_MAX_LOADED": "1", "UL_HOST": "127.0.0.1"},
     "config": {}},
    {"id": "save_power", "title": "🍃 最省资源（电脑同时开别的软件）", "kind": "engine",
     "plain": "电脑还要同时跑别的软件时用：WeNet 口型、画面 720 高、动作 200 帧、只常驻 1 套服装；讲稿声音全缓存。",
     "env": {"UL_MAX_SESSION": "1", "UL_ASR": "wenet", "UL_MAX_FRAMES": "200", "UL_OUT_HEIGHT": "720",
             "UL_BATCH": "2", "UL_ORT_GPU_MB": "1024", "UL_MAX_LOADED": "1", "UL_HOST": "127.0.0.1"},
     "config": {"tts": {"enable_cache": True}}},
    {"id": "interactive", "title": "💬 互动答疑优先（弹幕多）", "kind": "content",
     "plain": "弹幕多的直播间：提问优先回答，讲稿每段之间多留空，点播主题讲短一点（4 段）、讲解中穿插回答。",
     "env": {},
     "config": {"scheduler": {"auto_reply": True, "max_queue_size": 20, "warmup_interval_sec": 30},
                "knowledge_base": {"narration_gap_sec": 6.0, "warmup_every_n_beats": 6,
                                   "topic_router": {"enabled": True, "max_beats": 4, "answer_during_topic": True,
                                                    "answer_every_n_beats": 1}}}},
    {"id": "lecture", "title": "📚 讲课连播优先（科普/说书）", "kind": "content",
     "plain": "知识科普、说书类直播：讲稿一段接一段讲，点播主题讲完整（10 段），讲完再统一回答提问。",
     "env": {},
     "config": {"scheduler": {"auto_reply": True, "warmup_interval_sec": 25},
                "knowledge_base": {"narration_mode": True, "narration_gap_sec": 2.5, "warmup_every_n_beats": 5,
                                   "topic_router": {"enabled": True, "max_beats": 10, "answer_during_topic": True,
                                                    "answer_every_n_beats": 3}}}},
]
PRESETS_BY_ID = {p["id"]: p for p in PRESETS}

WIZARD_QUESTIONS: List[Dict[str, Any]] = [
    {"id": "pc", "q": "直播时电脑上还开着什么？", "options": [
        ["only", "只开数字人和 OBS（推荐）", "stable_2080"],
        ["busy", "还要同时开别的软件 / 游戏", "save_power"],
        ["closeup", "只开直播，而且主播是脸部大特写", "lip_quality"]]},
    {"id": "content", "q": "你的直播主要是哪种？", "options": [
        ["lecture", "讲课科普 / 说书，按稿子讲为主", "lecture"],
        ["interactive", "聊天互动，回答观众问题为主", "interactive"],
        ["mixed", "两种都有", ""]]},
    {"id": "ai", "q": "观众点播主题时，要不要让 AI 帮忙判断？", "options": [
        ["auto", "要，说得清楚的秒判，含糊的再问 AI（推荐）", "auto"],
        ["rules", "不要，只认关键词，不花 AI 额度", "rules"],
        ["ai", "每条弹幕都让 AI 判断（最准，最费额度）", "ai"]]},
]

KB_WRITING_TIPS = """讲稿写法（白话版）：
# 第1章 标题          → 一个大主题（观众可以点播“讲讲 + 标题里的词”）
## 小节标题            → 大主题下的小主题（也能单独点播）
> [板书] 要点          → 直播画面右上角的板书要点
> [主题词] 防骗|反诈   → 观众可能怎么叫这个主题，多写几个，点播更准
> [背景] xxx.mp4       → 讲到这里时切换的背景视频
普通段落               → 主播念的内容，自动切成每段约 15 秒
Q: 验证码|短信码        → 观众问到这些词时
A: 标准回答            → 直接念这句，不让大模型发挥（适合防骗、合规类问题）"""


# ------------------------------------------------------------------ 基础读写
def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(default)


def _save_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def ensure_runtime_configs(hub_dir: Path) -> List[str]:
    """首次运行 / 全新克隆：缺 config.json、platform_profiles.json 时从 *.example.json 复制一份。"""
    created: List[str] = []
    for real, example in (("config.json", "config.example.json"),
                          ("platform_profiles.json", "platform_profiles.example.json")):
        rp, ep = Path(hub_dir) / real, Path(hub_dir) / example
        if not rp.exists() and ep.is_file():
            try:
                shutil.copyfile(ep, rp)
                created.append(real)
            except OSError:
                pass
    return created


def parse_env_bat(env_path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for line in Path(env_path).read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r'^\s*set\s+"([A-Za-z0-9_]+)=(.*)"\s*$', line, re.I)
            if m:
                out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def update_env_bat(env_path: Path, updates: Dict[str, str]) -> List[str]:
    """替换已有 set "K=V"；不存在的键插到“Download mirrors”注释前。保持纯 ASCII + CRLF。"""
    env_path = Path(env_path)
    if not env_path.exists() or not updates:
        return []
    for k, v in updates.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", k) or not str(v).isascii() or any(c in str(v) for c in '"&|<>^%\r\n'):
            raise ValueError(f"env.bat 参数不安全或含非 ASCII 字符: {k}={v}")
    lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    pending = {k: str(v) for k, v in updates.items()}
    changed: List[str] = []
    new_lines: List[str] = []
    for line in lines:
        m = re.match(r'^\s*set\s+"([A-Za-z0-9_]+)=(.*)"\s*$', line, re.I)
        if m and m.group(1) in pending:
            k = m.group(1)
            if m.group(2) != pending[k]:
                changed.append(k)
            new_lines.append(f'set "{k}={pending.pop(k)}"')
        else:
            new_lines.append(line)
    if pending:
        idx = next((i for i, l in enumerate(new_lines) if l.strip().lower().startswith("rem download mirrors")), len(new_lines))
        for k, v in pending.items():
            new_lines.insert(idx, f'set "{k}={v}"')
            idx += 1
            changed.append(k)
    tmp = env_path.with_suffix(".bat.tmp")
    tmp.write_bytes(("\r\n".join(new_lines) + "\r\n").encode("ascii", errors="strict"))
    os.replace(tmp, env_path)
    return changed


def _get_path(d: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set_path(d: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any], prefix: str = "", changed: Optional[List[str]] = None) -> List[str]:
    changed = [] if changed is None else changed
    for k, v in (src or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v, key + ".", changed)
        else:
            if dst.get(k) != v:
                changed.append(key)
            dst[k] = copy.deepcopy(v)
    return changed


def coerce_value(item: Dict[str, Any], raw: Any) -> Any:
    t = item.get("type")
    if t == "bool":
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on", "开", "是")
        return bool(raw)
    if t == "int":
        v = int(round(float(raw)))
        return max(int(item.get("min", v)), min(int(item.get("max", v)), v))
    if t == "float":
        v = float(raw)
        return max(float(item.get("min", v)), min(float(item.get("max", v)), v))
    if t == "choice":
        allowed = [c[0] for c in item.get("choices", [])]
        v = str(raw)
        if v not in allowed:
            raise ValueError(f"{item['label']} 只能选：{', '.join(allowed)}")
        return v
    return raw


def items_to_patch(values: Dict[str, Any]) -> Dict[str, Any]:
    """{"env.UL_BATCH": 4, "scheduler.auto_reply": true} → {"env": {...}, "config": {...}}（带类型校验与范围钳位）。"""
    patch: Dict[str, Any] = {"env": {}, "config": {}}
    for key, raw in (values or {}).items():
        item = ITEMS_BY_KEY.get(key)
        if not item:
            continue
        val = coerce_value(item, raw)
        if key.startswith("env."):
            patch["env"][key[4:]] = str(int(val)) if isinstance(val, bool) else str(val)
        else:
            _set_path(patch["config"], key, val)
    return patch


def apply_patch(hub_dir: Path, root_dir: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    """把 {"env": {...}, "config": {...}} 原子写入 env.bat 与 config.json。"""
    hub_dir, root_dir = Path(hub_dir), Path(root_dir)
    ensure_runtime_configs(hub_dir)
    cfg_path = hub_dir / "config.json"
    changed: List[str] = []
    if patch.get("config"):
        cfg = _load_json(cfg_path, {})
        changed += _deep_merge(cfg, patch["config"])
        _save_json_atomic(cfg_path, cfg)
    env_changed: List[str] = []
    if patch.get("env"):
        env_changed = update_env_bat(root_dir / "env.bat", {k: str(v) for k, v in patch["env"].items()})
        changed += [f"env.{k}" for k in env_changed]
        # 引擎参数同时记一份到 config.json 的 ultralight 块，便于中控/桌面端展示
        if any(k in patch["env"] for k in ("UL_ASR", "UL_BACKEND", "UL_MAX_SESSION")):
            cfg = _load_json(cfg_path, {})
            ul = cfg.setdefault("ultralight", {})
            if "UL_ASR" in patch["env"]:
                ul["asr"] = patch["env"]["UL_ASR"]
            if "UL_BACKEND" in patch["env"]:
                ul["backend"] = patch["env"]["UL_BACKEND"]
            if "UL_MAX_SESSION" in patch["env"]:
                ul["max_session"] = int(patch["env"]["UL_MAX_SESSION"])
            _save_json_atomic(cfg_path, cfg)
    return {"changed": changed, "restart_engine": bool(env_changed),
            "plain": ("已保存。" + ("数字人引擎的设置要「重启引擎」后才生效。" if env_changed else "中控设置已立即生效。"))}


def preset_patch(preset_id: str) -> Dict[str, Any]:
    p = PRESETS_BY_ID.get(preset_id)
    if not p:
        raise ValueError(f"没有这个预设: {preset_id}")
    return {"env": dict(p.get("env") or {}), "config": copy.deepcopy(p.get("config") or {})}


def wizard(answers: Dict[str, str]) -> Dict[str, Any]:
    """3 个白话问题 → 组合预设 → 一整套配置 + 白话解读。"""
    patch: Dict[str, Any] = {"env": {}, "config": {}}
    explain: List[str] = []
    for q in WIZARD_QUESTIONS:
        ans = str((answers or {}).get(q["id"]) or q["options"][0][0])
        opt = next((o for o in q["options"] if o[0] == ans), q["options"][0])
        target = opt[2]
        if q["id"] == "ai":
            _set_path(patch["config"], "knowledge_base.topic_router.detect_mode", target)
            _set_path(patch["config"], "knowledge_base.topic_router.enabled", True)
            explain.append(f"• {q['q']} → {opt[1]}")
            continue
        if target:
            pp = preset_patch(target)
            patch["env"].update(pp["env"])
            _deep_merge(patch["config"], pp["config"])
            explain.append(f"• {q['q']} → {opt[1]}：{PRESETS_BY_ID[target]['plain']}")
        else:
            explain.append(f"• {q['q']} → {opt[1]}：讲稿连播和答疑都保持默认均衡设置。")
    if "UL_HOST" not in patch["env"]:
        patch["env"]["UL_HOST"] = "127.0.0.1"
    return {"patch": patch, "explain": explain}


# ------------------------------------------------------------------ 白话解读当前配置
def current_value(key: str, cfg: Dict[str, Any], env: Dict[str, str]) -> Any:
    if key.startswith("env."):
        return env.get(key[4:])
    return _get_path(cfg, key)


def describe(cfg: Dict[str, Any], env: Dict[str, str], level: str = "all") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in GUIDE_ITEMS:
        if level != "all" and it.get("level") != level:
            continue
        cur = current_value(it["key"], cfg, env)
        rec = it.get("recommend")
        same = str(cur).lower() == str(rec).lower() if cur is not None else False
        out.append({**{k: v for k, v in it.items()}, "current": cur, "is_recommended": same,
                    "status": "ok" if same else ("unset" if cur is None else "custom")})
    return out


# ------------------------------------------------------------------ 配置体检（按 2080 主机红线）
def _int(v: Any, d: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def doctor(cfg: Dict[str, Any], env: Dict[str, str], topic_count: int = -1) -> List[Dict[str, Any]]:
    f: List[Dict[str, Any]] = []

    def add(level: str, title: str, plain: str, fix: Optional[Dict[str, Any]] = None) -> None:
        f.append({"level": level, "title": title, "plain": plain, "fix": fix})

    host = env.get("UL_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", ""):
        add("error", "数字人引擎对局域网开放",
            "现在同一 WiFi 下的人都能给数字人塞话、提交训练任务（还能借训练文件做坏事）。没有特殊需要请改回只给本机用。",
            {"env": {"UL_HOST": "127.0.0.1"}})
    ms = _int(env.get("UL_MAX_SESSION"), 1)
    if ms > 1:
        add("warn", f"同时输出 {ms} 路数字人画面",
            "正式直播只需要 OBS 这 1 路。多开一路，12400F 就要多做一份视频编码，容易掉帧。",
            {"env": {"UL_MAX_SESSION": "1"}})
    oh = _int(env.get("UL_OUT_HEIGHT"), 1280)
    frames = _int(env.get("UL_MAX_FRAMES"), 300)
    loaded = _int(env.get("UL_MAX_LOADED"), 2)
    h = 1920 if oh in (0, 1920) else oh
    w = int(h * 9 / 16)
    mem_gb = frames * w * h * 3 * 2 * loaded / (1024 ** 3)
    if mem_gb > 10:
        add("warn", f"数字人帧缓存预计占内存约 {mem_gb:.1f}GB",
            "动作帧数、画面尺寸、常驻服装数乘起来太大了，32GB 内存加上 OBS、浏览器很容易吃满。建议按推荐值调。",
            {"env": {"UL_MAX_FRAMES": "300", "UL_OUT_HEIGHT": "1280", "UL_MAX_LOADED": "2"}})
    if oh == 0 or oh > 1280:
        add("info", "数字人画面没有缩小",
            "平台竖屏只有 720×1280，推更大的画面观众看不出来，却会让 CPU 编码更累。脸部大特写主播可以保留。",
            {"env": {"UL_OUT_HEIGHT": "1280"}})
    avo = _int(env.get("UL_AV_OFFSET_MS"), 0)
    if abs(avo) > 300:
        add("info", f"音画微调设成了 {avo} 毫秒",
            "调得比较大。引擎已改为音视频共用一个时钟，正常 0 就是对齐的；只有 OBS/浏览器本身有固定延迟时才需要小幅调整（±40~120）。",
            {"env": {"UL_AV_OFFSET_MS": "0"}})
    gpu_mb = _int(env.get("UL_ORT_GPU_MB"), 2048)
    tts_mode = str(_get_path(cfg, "tts.mode", "edgetts"))
    asr = env.get("UL_ASR", "hubert")
    vram = gpu_mb / 1024 + (1.3 if asr == "hubert" else 0.8) + (2.5 if tts_mode == "gptsovits" else 0) + 0.6
    if vram > 4.5:
        add("warn", f"显存预计约 {vram:.1f}GB，超过 4.5GB 稳定线",
            "RTX 2080 一共 8GB，OBS NVENC 编码也要用。超过 4.5GB 时一旦有波动就可能爆显存断播。"
            + ("本地 GPT-SoVITS 声音最占显存，可以换成硅基流动克隆声音。" if tts_mode == "gptsovits" else ""),
            {"env": {"UL_ORT_GPU_MB": "2048"}} if gpu_mb > 2048 else None)
    providers = [p for p in cfg.get("llm_providers", []) if p.get("enabled")
                 and str(p.get("api_key") or "").strip() and not str(p.get("api_key")).startswith("填入")]
    if not providers:
        add("warn", "还没有可用的大模型 Key",
            "观众提问只能用兜底话术回答；点播主题时说法含糊的也没法让 AI 帮忙判断（说得明白的“讲讲三国”照样能识别）。去「AI 模型与声音」填一个免费 Key 即可。")
    tr = _get_path(cfg, "knowledge_base.topic_router", {}) or {}
    if tr.get("enabled") is False:
        add("info", "观众主题点播没开", "观众说“讲讲防骗”时只会简短回答，不会把知识库里那一整段讲给他听。",
            {"config": {"knowledge_base": {"topic_router": {"enabled": True}}}})
    if topic_count == 0:
        add("warn", "知识库里还没有讲稿主题", "把讲稿 .md 放进知识库，或用「把文章变成讲稿」生成一份，主播才有东西可讲、可点播。")
    wi = _int(_get_path(cfg, "scheduler.warmup_interval_sec", 25), 25)
    if wi < 12:
        add("info", "暖场太频繁", f"每 {wi} 秒就暖场一次，会显得重复唠叨。", {"config": {"scheduler": {"warmup_interval_sec": 25}}})
    cm = _int(_get_path(cfg, "knowledge_base.chunk_max_chars", 65), 65)
    if cm > 120:
        add("info", "每段口播太长", f"每段 {cm} 字，主播开口前要等更久合成，观众提问也要等这段念完。建议 65 左右。",
            {"config": {"knowledge_base": {"chunk_max_chars": 65}}})
    if not f:
        add("ok", "配置很健康", f"已按 {HOST_PROFILE} 的红线检查，没有发现问题。")
    return f


def guide_bundle(hub_dir: Path, root_dir: Path, topic_count: int = -1) -> Dict[str, Any]:
    """WebUI / 桌面端一次拿全：说明书 + 当前值 + 预设 + 向导问题 + 体检结果。"""
    ensure_runtime_configs(hub_dir)
    cfg = _load_json(Path(hub_dir) / "config.json", {})
    env = parse_env_bat(Path(root_dir) / "env.bat")
    return {
        "host_profile": HOST_PROFILE,
        "items": describe(cfg, env),
        "presets": [{k: v for k, v in p.items()} for p in PRESETS],
        "wizard": WIZARD_QUESTIONS,
        "doctor": doctor(cfg, env, topic_count),
        "kb_tips": KB_WRITING_TIPS,
    }
