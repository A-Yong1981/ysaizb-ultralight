# -*- coding: utf-8 -*-
"""
tts_engine.py —— 克隆音色语音路由（RTX 2080 8GB 不爆显存版）
  - siliconflow : 硅基流动 FunAudioLLM/CosyVoice2-0.5B 云端合成（+0 显存）
  - gptsovits   : 本地 GPT-SoVITS v2 FP16（api_v2.py :9880，单批低显存推理）
  - edgetts     : 不合成，交给 LiveTalking 原生 Edge-TTS(/human)
统一输出 16000Hz 单声道 WAV，与 Wav2Lip 内部采样率对齐，直推 LiveTalking /humanaudio。
固定暖场话术等 cacheable 文本会落盘到 tts_cache/*.wav，复播零 API 消耗。
"""
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx


def is_placeholder_key(key: Any) -> bool:
    k = str(key or "").strip()
    return (not k) or k.startswith("填入")


class TTSEngine:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        # 硅基流动 CosyVoice2 建议正文不带多余空格：中文字符间的空格去掉，英文单词间保留一个空格
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(?<=[\u3000-\u9fff\uff00-\uffef])\s+|\s+(?=[\u3000-\u9fff\uff00-\uffef])", "", text)
        return text

    def _cache_path(self, key_raw: str) -> Path:
        digest = hashlib.md5(key_raw.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.wav"

    async def synthesize(
        self,
        client: httpx.AsyncClient,
        tts_cfg: Dict[str, Any],
        text: str,
        use_cache: bool = False,
    ) -> Tuple[Optional[bytes], str]:
        """
        返回 (wav_bytes, provider_tag)。
        若返回 (None, reason)，上层自动降级到 LiveTalking 原生 Edge-TTS (/human)。
        """
        mode = tts_cfg.get("mode", "edgetts")
        if mode == "edgetts":
            return None, "edgetts"
        clean_text = self._clean_for_tts(text)
        if not clean_text:
            return None, "empty"
        cache_enabled = bool(tts_cfg.get("enable_cache", True)) and use_cache

        # ---------------- 方案 A：硅基流动 CosyVoice2 ----------------
        if mode == "siliconflow":
            sf = tts_cfg.get("siliconflow", {})
            api_key = str(sf.get("api_key", "")).strip()
            if is_placeholder_key(api_key):
                return None, "siliconflow_no_key"
            voice = sf.get("voice", "FunAudioLLM/CosyVoice2-0.5B:anna")
            speed = float(sf.get("speed", 1.0))
            prefix = str(sf.get("emotion_prefix", "")).strip()
            final_input = f"{prefix}<|endofprompt|>{clean_text}" if prefix else clean_text
            cache_key = f"sf|{sf.get('model')}|{voice}|{speed}|{final_input}"
            cpath = self._cache_path(cache_key)
            if cache_enabled and cpath.exists():
                return cpath.read_bytes(), "siliconflow(cache)"

            url = sf.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/") + "/audio/speech"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": sf.get("model", "FunAudioLLM/CosyVoice2-0.5B"),
                "input": final_input,
                "voice": voice,
                "response_format": "wav",
                "sample_rate": int(sf.get("sample_rate", 16000)),
                "speed": speed,
                "gain": float(sf.get("gain", 0.0)),
            }
            try:
                resp = await client.post(
                    url, headers=headers, json=payload, timeout=float(sf.get("timeout", 15))
                )
                if resp.status_code == 200 and len(resp.content) > 128:
                    if cache_enabled:
                        cpath.write_bytes(resp.content)
                    return resp.content, "siliconflow"
                return None, f"siliconflow_http_{resp.status_code}"
            except Exception as exc:
                return None, f"siliconflow_err:{exc}"

        # ---------------- 方案 B：本地 GPT-SoVITS v2 FP16 ----------------
        if mode == "gptsovits":
            gs = tts_cfg.get("gptsovits", {})
            ref_path = str(gs.get("ref_audio_path", "")).strip()
            if not ref_path:
                return None, "gptsovits_no_ref"
            speed = float(gs.get("speed_factor", 1.0))
            cache_key = f"gsv|{ref_path}|{speed}|{clean_text}"
            cpath = self._cache_path(cache_key)
            if cache_enabled and cpath.exists():
                return cpath.read_bytes(), "gptsovits(cache)"

            url = gs.get("base_url", "http://127.0.0.1:9880").rstrip("/") + "/tts"
            # 强制 batch_size=1 且关闭 parallel_infer，死守 RTX 2080 8GB 显存红线
            payload = {
                "text": clean_text,
                "text_lang": gs.get("text_lang", "zh"),
                "ref_audio_path": ref_path,
                "prompt_text": gs.get("prompt_text", ""),
                "prompt_lang": gs.get("prompt_lang", "zh"),
                "media_type": "wav",
                "streaming_mode": False,
                "parallel_infer": False,
                "batch_size": 1,
                "speed_factor": speed,
            }
            try:
                resp = await client.post(url, json=payload, timeout=float(gs.get("timeout", 15)))
                if resp.status_code == 200 and len(resp.content) > 128:
                    if cache_enabled:
                        cpath.write_bytes(resp.content)
                    return resp.content, "gptsovits_fp16"
                return None, f"gptsovits_http_{resp.status_code}"
            except Exception as exc:
                return None, f"gptsovits_err:{exc}"

        return None, "unknown_mode"

    async def upload_siliconflow_voice(
        self,
        client: httpx.AsyncClient,
        sf_cfg: Dict[str, Any],
        filename: str,
        file_bytes: bytes,
        custom_name: str,
        ref_text: str,
    ) -> Dict[str, Any]:
        api_key = str(sf_cfg.get("api_key", "")).strip()
        if is_placeholder_key(api_key):
            return {"code": 1, "msg": "请先在 config.json 的 tts.siliconflow.api_key 填写硅基流动 Key"}
        url = sf_cfg.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/") + "/uploads/audio/voice"
        headers = {"Authorization": f"Bearer {api_key}"}
        mime = "audio/mpeg" if filename.lower().endswith(".mp3") else "audio/wav"
        files = {"file": (filename, file_bytes, mime)}
        data = {
            "model": sf_cfg.get("model", "FunAudioLLM/CosyVoice2-0.5B"),
            "customName": custom_name,
            "text": ref_text,
        }
        try:
            resp = await client.post(url, headers=headers, files=files, data=data, timeout=30.0)
        except Exception as exc:
            return {"code": 1, "msg": f"上传失败: {exc}"}
        if resp.status_code == 200:
            res = resp.json()
            return {"code": 0, "uri": res.get("uri"), "raw": res}
        return {"code": resp.status_code, "msg": resp.text}

    async def list_siliconflow_voices(
        self, client: httpx.AsyncClient, sf_cfg: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        api_key = str(sf_cfg.get("api_key", "")).strip()
        if is_placeholder_key(api_key):
            return []
        url = sf_cfg.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/") + "/audio/voice/list"
        try:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0)
            if resp.status_code == 200:
                body = resp.json()
                return body.get("result") or body.get("data") or []
        except Exception:
            pass
        return []
