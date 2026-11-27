# -*- coding: utf-8 -*-
"""测试 config.json 里已填的 Key 能否调通（不打印完整 Key），结果写入 logs/key_test.log"""
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "ai-live-hub" / "config.json").read_text(encoding="utf-8"))
lines = []


def log(msg):
    print(msg, flush=True)
    lines.append(msg)


def mask(k):
    k = str(k or "")
    return (k[:6] + "..." + k[-4:]) if len(k) > 12 else "(空)"


def filled(k):
    k = str(k or "").strip()
    return bool(k) and not k.startswith("填入")


def post(url, key, body, timeout=30):
    t = time.time()
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json=body, timeout=timeout)
        return r.status_code, r, round(time.time() - t, 2)
    except Exception as exc:
        return None, exc, round(time.time() - t, 2)


ok_all = True
log(f"== Key 测试 {time.strftime('%Y-%m-%d %H:%M:%S')} ==")
for p in CFG.get("llm_providers", []):
    if not p.get("enabled") or not filled(p.get("api_key")):
        log(f"[SKIP] {p['name']}：未启用或未填 Key")
        continue
    url = p["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": p["model"], "max_tokens": 80, "temperature": 0.7,
        "messages": [{"role": "system", "content": "你是 AI 科普直播间主播小琴，面向成年观众，用 30 到 50 字口语回答。"},
                     {"role": "user", "content": "观众【测试】在公屏说：AI 会犯错吗"}]}
    if isinstance(p.get("extra_body"), dict):
        body.update(p["extra_body"])          # 和中控一致：DeepSeek 关闭思考模式
    s, r, dt = post(url, p["api_key"], body)
    if s == 200:
        reply = (r.json()["choices"][0]["message"].get("content") or "").strip()
        log(f"[ OK ] {p['name']} ({mask(p['api_key'])}) {p['model']} {dt}s -> {reply or '(返回空内容，检查是否关了思考模式)'}")
    else:
        ok_all = False
        log(f"[FAIL] {p['name']} ({mask(p['api_key'])}) {dt}s -> {s} {str(r.text if hasattr(r, 'text') else r)[:240]}")

kb = CFG.get("knowledge_base", {}).get("embedding", {})
ekey = kb.get("api_key") if filled(kb.get("api_key")) else next(
    (p["api_key"] for p in CFG.get("llm_providers", []) if "siliconflow" in p.get("base_url", "") and filled(p.get("api_key"))), "")
if filled(ekey):
    s, r, dt = post(kb.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/") + "/embeddings", ekey,
                    {"model": kb.get("model", "BAAI/bge-m3"), "input": ["AI 防骗", "视频借钱怎么办"], "encoding_format": "float"})
    if s == 200:
        d = r.json()["data"]
        log(f"[ OK ] 向量 {kb.get('model', 'BAAI/bge-m3')} {dt}s -> {len(d)} 条，维度 {len(d[0]['embedding'])}")
    else:
        ok_all = False
        log(f"[FAIL] 向量 {dt}s -> {s} {str(r.text if hasattr(r, 'text') else r)[:240]}")
else:
    log("[SKIP] 向量检索：没有可用的硅基流动 Key")

sf = CFG.get("tts", {}).get("siliconflow", {})
if filled(sf.get("api_key")):
    s, r, dt = post(sf.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/") + "/audio/speech", sf["api_key"], {
        "model": sf.get("model", "FunAudioLLM/CosyVoice2-0.5B"), "input": "大家好，我是小琴。",
        "voice": sf.get("voice", "FunAudioLLM/CosyVoice2-0.5B:anna"), "response_format": "wav", "sample_rate": 16000})
    if s == 200 and len(r.content) > 1000:
        out = ROOT / "logs" / "tts_test.wav"
        out.write_bytes(r.content)
        log(f"[ OK ] CosyVoice2 语音合成 {dt}s -> {len(r.content) // 1024} KB，已保存 logs/tts_test.wav（当前 TTS 模式仍为 {CFG.get('tts', {}).get('mode')}）")
    else:
        log(f"[WARN] CosyVoice2 语音合成 {dt}s -> {s} {str(r.text if hasattr(r, 'text') else r)[:240]}（不影响默认 Edge-TTS）")

log("== 结论：" + ("大模型与向量检索均可用" if ok_all else "有失败项，见上方") + " ==")
(ROOT / "logs").mkdir(exist_ok=True)
(ROOT / "logs" / "key_test.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
