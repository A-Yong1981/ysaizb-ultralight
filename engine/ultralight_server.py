# -*- coding: utf-8 -*-
"""
ultralight_server.py —— 原生 Ultralight-Digital-Human 实时推理与 WebRTC 推流服务
  * 彻底独立于 LiveTalking，直接运行 anliyuan/Ultralight-Digital-Human 的 UNet + WeNet/HuBERT 管线
  * 支持 ONNXRuntime GPU/CPU 与 PyTorch 双后端
  * 兼容 ai-live-hub 全部接口：
      GET  /
      POST /offer
      POST /human
      POST /humanaudio
      POST /interrupt_talk
      POST /is_speaking
      POST /set_audiotype
      GET  /api/avatar/list
      POST /api/avatar/task
      GET  /api/avatar/task/{task_id}
      POST /api/avatar/task/{task_id}/cancel
      GET  /api/admin/sessions

i5-12400F / 32GB / RTX 2080 8GB 优化要点：
  * 默认只监听 127.0.0.1（中控在本机代理 WebRTC，无需对局域网开放）；CORS 仅放行本机来源
  * 每会话一条 TTS 串行队列：句子严格按顺序播出，按标点分句流水线合成，首句更快开口
  * 打断 = 清空待渲染队列 + 音视频轨道缓冲 + 残余音频，并用“代次号”丢弃打断前仍在合成的句子
  * 音视频共用会话时钟（画面开播为 t0），不再各自计时导致声音领先；可用 --av_offset_ms 微调
  * 预览页（role=preview）在 OBS 已连接时只“镜像”同一路画面：不额外推理、不占名额、不抢 OBS 的说话会话
  * 帧按 --out_height 缩放后常驻内存，形象 LRU 只保留 --max_loaded_avatars 个
  * ONNX Runtime 显存池设上限（--ort_gpu_mem_mb），积压时按 --batch 批量追帧（需动态 batch 的 unet.onnx）
  * 训练在独立子进程运行；有直播画面在线时默认拒绝训练，防止显存爆满断播
  * .pth 一律 weights_only=True 安全加载
"""
from __future__ import annotations

import argparse
import asyncio
import fractions
import gc
import io
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp_cors
import av
import cv2
import edge_tts
import numpy as np
import resampy
import soundfile as sf
import torch
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.rtcrtpsender import RTCRtpSender

ROOT = Path(__file__).resolve().parent.parent
UDH_DIR = ROOT / "Ultralight-Digital-Human"
MODELS_DIR = ROOT / "models"
AVATARS_DIR = ROOT / "avatars"

for p in (str(ROOT), str(UDH_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine.audio_encoders import (  # noqa: E402
    HuBERTEncoder,
    WeNetEncoder,
    ensure_onnxruntime_cuda_paths,
    make_ort_session,
    safe_torch_load,
)
from face_utils import FACE_BORDER, FACE_CROP_SIZE, FACE_INNER_SIZE, MASK_RECT, compute_face_bbox, read_landmarks  # noqa: E402
from unet import Model  # noqa: E402

ensure_onnxruntime_cuda_paths()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ultralight_server")

SAMPLE_RATE = 16000
AUDIO_PTIME = 0.020
AUDIO_CHUNK_SAMPLES = 320  # 20ms @ 16kHz
VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
AUDIO_TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v"}
CKPT_EXTS = {".pth", ".pt", ".onnx"}
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:28910", "http://localhost:28910",
    "http://127.0.0.1:8020", "http://localhost:8020",
)
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;…])|(?<=[.!?])\s+")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 工具函数：解码音频到 16kHz mono float32
# ---------------------------------------------------------------------------
def decode_audio_bytes_to_16k(raw_bytes: bytes) -> np.ndarray:
    """优先用 soundfile，再用 PyAV 解码 WAV/MP3 到 16kHz 单声道 float32 [-1, 1]。"""
    try:
        data, sr = sf.read(io.BytesIO(raw_bytes), dtype="float32")
        if data.ndim > 1:
            data = data[:, 0]
        if sr != SAMPLE_RATE and data.shape[0] > 0:
            data = resampy.resample(data, sr_orig=sr, sr_new=SAMPLE_RATE).astype(np.float32)
        return data.astype(np.float32)
    except Exception:
        pass

    container = av.open(io.BytesIO(raw_bytes))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    chunks: List[np.ndarray] = []
    for frame in container.decode(audio=0):
        for resampled in resampler.resample(frame):
            chunks.append(resampled.to_ndarray().reshape(-1).astype(np.float32))
    for resampled in resampler.resample(None):
        chunks.append(resampled.to_ndarray().reshape(-1).astype(np.float32))
    container.close()
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=0)


async def synthesize_edgetts_16k(text: str, voice: str) -> np.ndarray:
    communicate = edge_tts.Communicate(text, voice or "zh-CN-XiaoxiaoNeural")
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    if not buf:
        return np.zeros(0, dtype=np.float32)
    return await asyncio.to_thread(decode_audio_bytes_to_16k, bytes(buf))


def split_sentences(text: str, max_len: int = 48) -> List[str]:
    """按句末标点分句（过长句在逗号处再断），用于 TTS 流水线：第一句合成完就开口。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    out: List[str] = []
    for s in (x.strip() for x in _SENT_SPLIT_RE.split(text)):
        if not s:
            continue
        while len(s) > max_len:
            cut = max(s.rfind(c, 0, max_len) for c in "，,、：:")
            cut = cut + 1 if cut >= 8 else max_len
            out.append(s[:cut])
            s = s[cut:].strip()
        if s:
            # 太短的碎句并到前一句，避免频繁调用 TTS
            if out and len(s) < 6 and len(out[-1]) + len(s) <= max_len:
                out[-1] += s
            else:
                out.append(s)
    return out


def _bounce_index(n_frames: int, idx: int) -> int:
    if n_frames <= 1:
        return 0
    period = 2 * (n_frames - 1)
    pos = idx % period
    return pos if pos < n_frames else period - pos


# ---------------------------------------------------------------------------
# Avatar 资产与模型加载
# ---------------------------------------------------------------------------
class LoadedAvatar:
    def __init__(
        self,
        avatar_id: str,
        asr: str,
        fps: int,
        speak_frames: List[np.ndarray],
        speak_bboxes: List[Tuple[int, int, int, int]],
        idle_frames: List[np.ndarray],
        unet_ort: Any,
        unet_torch: Optional[Model],
        device: torch.device,
        batch: int = 1,
    ) -> None:
        self.avatar_id = avatar_id
        self.asr = asr
        self.fps = fps
        self.speak_frames = speak_frames
        self.speak_bboxes = speak_bboxes
        self.idle_frames = idle_frames or speak_frames
        self.unet_ort = unet_ort
        self.unet_torch = unet_torch
        self.device = device
        self._infer_lock = threading.Lock()
        self.batch = max(1, int(batch))
        self.batch_ok = False
        self._in_names: Optional[Tuple[str, str]] = None
        if unet_ort is not None:
            ins = unet_ort.get_inputs()
            self._in_names = (ins[0].name, ins[1].name)
            dim0 = ins[0].shape[0] if ins[0].shape else 1
            self.batch_ok = not isinstance(dim0, int)   # 新导出的模型 batch 维为符号 "batch"
        elif unet_torch is not None:
            self.batch_ok = True

        # 预裁切好说话循环帧的 168x168 人脸底图，避免渲染循环重复 cv2.resize 占用 CPU
        self.face_crops: List[np.ndarray] = []
        for frame, (xmin, ymin, xmax, ymax) in zip(self.speak_frames, self.speak_bboxes):
            region = frame[ymin:ymax, xmin:xmax]
            self.face_crops.append(cv2.resize(region, (FACE_CROP_SIZE, FACE_CROP_SIZE), interpolation=cv2.INTER_AREA))

    def memory_mb(self) -> float:
        total = sum(f.nbytes for f in self.speak_frames) + sum(c.nbytes for c in self.face_crops)
        if self.idle_frames is not self.speak_frames:
            total += sum(f.nbytes for f in self.idle_frames)
        return total / (1024 * 1024)

    def release(self) -> None:
        self.speak_frames, self.idle_frames, self.face_crops = [], [], []
        self.unet_ort = None
        self.unet_torch = None

    def _prepare(self, frame_idx: int, audio_win: np.ndarray):
        idx = _bounce_index(len(self.speak_frames), frame_idx)
        crop_ori = self.face_crops[idx].copy()
        inner = crop_ori[FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE, FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE].copy()
        masked = cv2.rectangle(inner.copy(), MASK_RECT, (0, 0, 0), -1)
        img_in = np.concatenate([
            inner.transpose(2, 0, 1).astype(np.float32) / 255.0,
            masked.transpose(2, 0, 1).astype(np.float32) / 255.0,
        ], axis=0)  # [6, 160, 160]
        if self.asr == "wenet":
            aud_in = audio_win.reshape(128, 16, 32).astype(np.float32)
        else:
            aud_in = audio_win.reshape(16, 32, 32).astype(np.float32)
        return idx, crop_ori, img_in, aud_in

    def _run(self, img_b: np.ndarray, aud_b: np.ndarray) -> Optional[np.ndarray]:
        with self._infer_lock:
            if self.unet_ort is not None and self._in_names:
                return self.unet_ort.run(None, {self._in_names[0]: img_b, self._in_names[1]: aud_b})[0]
            if self.unet_torch is not None:
                with torch.no_grad():
                    t_img = torch.from_numpy(img_b).to(self.device)
                    t_aud = torch.from_numpy(aud_b).to(self.device)
                    return self.unet_torch(t_img, t_aud).cpu().numpy()
        return None

    def infer_frames(self, items: List[Tuple[int, np.ndarray]]) -> List[np.ndarray]:
        """批量（或逐帧）推理并贴回全身帧，返回 BGR uint8 列表，顺序与输入一致。"""
        preps = [self._prepare(fi, aw) for fi, aw in items]
        preds: List[Optional[np.ndarray]] = []
        if self.batch_ok and len(preps) > 1:
            out = self._run(np.stack([p[2] for p in preps]), np.stack([p[3] for p in preps]))
            preds = list(out) if out is not None else [None] * len(preps)
        else:
            for p in preps:
                out = self._run(p[2][None], p[3][None])
                preds.append(out[0] if out is not None else None)
        frames: List[np.ndarray] = []
        for (idx, crop_ori, _img, _aud), pred in zip(preps, preds):
            base = self.speak_frames[idx].copy()
            if pred is None:
                frames.append(base)
                continue
            xmin, ymin, xmax, ymax = self.speak_bboxes[idx]
            pred_bgr = np.clip(pred.transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)
            crop_ori[FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE, FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE] = pred_bgr
            base[ymin:ymax, xmin:xmax] = cv2.resize(crop_ori, (xmax - xmin, ymax - ymin))
            frames.append(base)
        return frames

    def infer_frame(self, frame_idx: int, audio_win: np.ndarray) -> np.ndarray:
        return self.infer_frames([(frame_idx, audio_win)])[0]


class EngineRuntime:
    def __init__(self, opt: argparse.Namespace) -> None:
        self.opt = opt
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.avatars: "OrderedDict[str, LoadedAvatar]" = OrderedDict()
        self.encoders: Dict[str, Any] = {}
        self._load_lock = threading.RLock()
        self.sessions: Dict[str, "UltralightSession"] = {}
        self.pcs: set = set()
        self.sess_lock = threading.Lock()

    def live_sessions(self) -> List["UltralightSession"]:
        """在线的“真画面”会话（不含预览镜像）。"""
        return [s for s in list(self.sessions.values()) if not s._quit.is_set() and not getattr(s, "is_mirror", False)]

    def get_encoder(self, asr: str):
        with self._load_lock:
            if asr in self.encoders:
                return self.encoders[asr]
            if asr == "wenet":
                enc_p = MODELS_DIR / "encoder.onnx"
                if not enc_p.is_file():
                    enc_p = UDH_DIR / "data_utils" / "encoder.onnx"
                enc = WeNetEncoder(enc_p, gpu_mem_mb=min(1024, max(256, int(self.opt.ort_gpu_mem_mb) // 2)))
            else:
                enc = HuBERTEncoder(MODELS_DIR / "hubert-large-ls960-ft")
            self.encoders[asr] = enc
            return enc

    def _scale(self, im: np.ndarray, bbox: Optional[Tuple[int, int, int, int]] = None):
        """按 --out_height 等比缩小帧（宽取偶数，便于视频编码），人脸框同步缩放。"""
        oh = int(self.opt.out_height or 0)
        h, w = im.shape[:2]
        if oh <= 0 or h <= oh:
            return im, bbox
        s = oh / float(h)
        nw = max(2, int(round(w * s)) // 2 * 2)
        im2 = cv2.resize(im, (nw, oh), interpolation=cv2.INTER_AREA)
        if bbox is not None:
            bbox = tuple(int(round(v * s)) for v in bbox)  # type: ignore[assignment]
        return im2, bbox

    def _load_frame_set(self, img_dir: Path, lms_dir: Path, bboxes_json: Optional[Path], max_frames: int):
        imgs = sorted(
            (p for p in img_dir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".png")),
            key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem,
        )[:max_frames]
        if not imgs:
            raise FileNotFoundError(f"形象帧目录为空: {img_dir}")

        cached_bboxes = None
        if bboxes_json and bboxes_json.is_file():
            try:
                cached_bboxes = json.loads(bboxes_json.read_text(encoding="utf-8"))
            except Exception:
                cached_bboxes = None

        frames: List[np.ndarray] = []
        bboxes: List[Tuple[int, int, int, int]] = []
        for idx, img_p in enumerate(imgs):
            im = cv2.imread(str(img_p))
            if im is None:
                continue
            if cached_bboxes and idx < len(cached_bboxes):
                box = tuple(int(v) for v in cached_bboxes[idx])
            else:
                box = compute_face_bbox(read_landmarks(str(lms_dir / f"{img_p.stem}.lms")))
            im, box = self._scale(im, box)
            h, w = im.shape[:2]
            xmin, ymin, xmax, ymax = box  # type: ignore[misc]
            xmin = max(0, min(w - 16, xmin))
            ymin = max(0, min(h - 16, ymin))
            xmax = max(xmin + 16, min(w, xmax))
            ymax = max(ymin + 16, min(h, ymax))
            frames.append(im)
            bboxes.append((xmin, ymin, xmax, ymax))
        if bboxes:
            face_w = bboxes[0][2] - bboxes[0][0]
            if face_w < int(FACE_CROP_SIZE * 0.8):
                logger.warning(f"缩放后人脸宽仅 {face_w}px（< {FACE_CROP_SIZE}），口型清晰度会下降；"
                               f"可把 UL_OUT_HEIGHT 调大或设为 0（保持原尺寸）")
        return frames, bboxes

    def _evict_lru(self, keep: str) -> None:
        limit = max(1, int(self.opt.max_loaded_avatars))
        in_use = {s.avatar.avatar_id for s in self.live_sessions()}
        evicted = False
        for aid in list(self.avatars.keys()):
            if len(self.avatars) <= limit:
                break
            if aid == keep or aid in in_use:
                continue
            av_obj = self.avatars.pop(aid)
            mb = av_obj.memory_mb()
            av_obj.release()
            evicted = True
            logger.info(f"[LRU] 释放形象 [{aid}] 帧缓存约 {mb:.0f}MB（常驻上限 {limit} 个）")
        if evicted:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def load_avatar(self, avatar_id: str, force_reload: bool = False) -> LoadedAvatar:
        with self._load_lock:
            if not force_reload and avatar_id in self.avatars:
                self.avatars.move_to_end(avatar_id)
                return self.avatars[avatar_id]

            av_dir = AVATARS_DIR / avatar_id
            if not av_dir.is_dir():
                raise FileNotFoundError(f"未找到 Ultralight 形象目录: {av_dir}")

            meta_p = av_dir / "meta.json"
            meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
            asr = str(meta.get("asr") or self.opt.asr or "hubert").lower()
            fps = int(meta.get("fps") or (20 if asr == "wenet" else 25))

            img_dir = av_dir / "full_body_img"
            lms_dir = av_dir / "landmarks"
            if not img_dir.is_dir() and (av_dir / "dataset" / "full_body_img").is_dir():
                img_dir = av_dir / "dataset" / "full_body_img"
                lms_dir = av_dir / "dataset" / "landmarks"

            speak_frames, speak_bboxes = self._load_frame_set(
                img_dir, lms_dir, av_dir / "bboxes.json", self.opt.max_avatar_frames
            )

            idle_frames: List[np.ndarray] = []
            if (av_dir / "img_inference").is_dir():
                idle_imgs = sorted(
                    (p for p in (av_dir / "img_inference").iterdir() if p.suffix.lower() in (".jpg", ".png")),
                    key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem,
                )[: self.opt.max_avatar_frames]
                for p in idle_imgs:
                    im = cv2.imread(str(p))
                    if im is not None:
                        idle_frames.append(self._scale(im)[0])
                if idle_frames and speak_frames and idle_frames[0].shape != speak_frames[0].shape:
                    th, tw = speak_frames[0].shape[:2]
                    idle_frames = [cv2.resize(f, (tw, th), interpolation=cv2.INTER_AREA) for f in idle_frames]

            unet_ort = None
            unet_torch = None
            onnx_p = av_dir / "unet.onnx"
            pth_p = av_dir / "ultralight.pth"

            if (self.opt.backend == "onnx" or not pth_p.is_file()) and onnx_p.is_file():
                unet_ort = make_ort_session(onnx_p, gpu_mem_mb=int(self.opt.ort_gpu_mem_mb))
                logger.info(f"[{avatar_id}] 已加载 ONNX 模型 ({asr}/{fps}fps, providers={unet_ort.get_providers()})")
            elif pth_p.is_file():
                net = Model(6, asr).to(self.device)
                # 只有本机训练/导入时严格校验过的权重(meta.pth_trusted)才放宽；外部拷进来的一律 weights_only=True
                ckpt = safe_torch_load(pth_p, map_location=self.device, trusted=bool(meta.get("pth_trusted")))
                sd = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
                net.load_state_dict(sd)
                net.eval()
                unet_torch = net
                logger.info(f"[{avatar_id}] 已加载 PyTorch 模型 ({asr}/{fps}fps)")
            else:
                raise FileNotFoundError(f"形象 [{avatar_id}] 缺少 unet.onnx 与 ultralight.pth")

            # 确保对应音频编码器也已预热
            self.get_encoder(asr)

            loaded = LoadedAvatar(
                avatar_id=avatar_id,
                asr=asr,
                fps=fps,
                speak_frames=speak_frames,
                speak_bboxes=speak_bboxes,
                idle_frames=idle_frames,
                unet_ort=unet_ort,
                unet_torch=unet_torch,
                device=self.device,
                batch=int(self.opt.batch),
            )
            self.avatars[avatar_id] = loaded
            self.avatars.move_to_end(avatar_id)
            logger.info(f"[{avatar_id}] 帧缓存约 {loaded.memory_mb():.0f}MB，"
                        f"{'支持' if loaded.batch_ok else '不支持（旧模型，逐帧）'}批量追帧 batch={loaded.batch}")
            self._evict_lru(keep=avatar_id)
            return loaded


# ---------------------------------------------------------------------------
# WebRTC 音视频轨道与渲染会话
# ---------------------------------------------------------------------------
class UltralightStreamTrack(MediaStreamTrack):
    def __init__(self, session: Any, kind: str, fps: int, loop: asyncio.AbstractEventLoop, maxsize: int = 120,
                 drop_oldest: bool = False) -> None:
        super().__init__()
        self.session = session
        self.kind = kind
        self.fps = fps
        self.video_ptime = 1.0 / max(1, fps)
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._drop_oldest = drop_oldest   # 预览镜像：满了丢最旧的，保持低延迟且音视频延迟一致
        self._loop = loop
        self._evt = asyncio.Event()
        self._timestamp: int = 0
        self._frame_count: int = 0

    def put(self, item: Tuple[Any, Optional[dict]], timeout: float = 1.0) -> bool:
        """渲染线程投递帧后唤醒事件循环（替代每 4ms 轮询一次）。"""
        try:
            if self._drop_oldest:
                while True:
                    try:
                        self._queue.put_nowait(item)
                        break
                    except queue.Full:
                        try:
                            self._queue.get_nowait()
                        except queue.Empty:
                            pass
            else:
                self._queue.put(item, timeout=timeout)
        except queue.Full:
            return False
        try:
            self._loop.call_soon_threadsafe(self._evt.set)
        except RuntimeError:
            pass
        return True

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        """音视频共用会话时钟：第 k 个视频帧在 t0+k×帧间隔 发出，第 j 个音频包在 t0+j×20ms(+偏移) 发出。

        原来两条轨道各自从“自己第一次被拉取”开始计时：OBS/浏览器往往先拉音频、后拉画面，
        导致音频永久领先约一个前瞻缓冲（~250ms），出现“声音停了嘴还在动”。
        """
        if self.readyState != "live":
            raise Exception("track stopped")
        is_video = self.kind == "video"
        ptime = self.video_ptime if is_video else AUDIO_PTIME
        step = int(round(ptime * (VIDEO_CLOCK_RATE if is_video else SAMPLE_RATE)))
        t0 = self.session.clock_t0(self.kind)
        while t0 is None:   # 音频等画面开播后一起计时（纯音频客户端 2 秒后兜底自行开始）
            await asyncio.sleep(0.01)
            if self.readyState != "live":
                raise Exception("track stopped")
            t0 = self.session.clock_t0(self.kind)
        idx = self._frame_count
        self._frame_count += 1
        self._timestamp = idx * step
        target = t0 + idx * ptime + (0.0 if is_video else self.session.av_offset)
        wait = target - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        elif is_video and -wait > 2.0:
            # 卡顿超过 2 秒：音视频一起整体顺延，不再狂追，也不会把两者拉开
            self.session.rebase_clock(-wait)
        return self._timestamp, (VIDEO_TIME_BASE if is_video else AUDIO_TIME_BASE)

    async def recv(self):
        self.session.ensure_started(self)
        while True:
            try:
                frame, eventpoint = self._queue.get_nowait()
                break
            except queue.Empty:
                if self.readyState != "live":
                    raise Exception("track stopped")
                if self.session._quit.is_set():
                    raise Exception("session closed")
                self._evt.clear()
                if not self._queue.empty():
                    continue
                try:
                    await asyncio.wait_for(self._evt.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        pts, tb = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = tb
        if eventpoint:
            self.session.on_event(eventpoint)
        return frame

    def stop(self) -> None:
        super().stop()
        self.clear()
        self.session.on_track_stop(self)


class _SharedClock:
    """音视频共用会话时钟（只在事件循环线程读写）：画面开始拉流为 t0，音频等画面一起计时。"""
    _clock_t0: Optional[float] = None
    _audio_wait_since: Optional[float] = None
    av_offset: float = 0.0

    def clock_t0(self, kind: str) -> Optional[float]:
        if self._clock_t0 is None:
            now = time.time()
            if kind == "video":
                self._clock_t0 = now
            elif self._audio_wait_since is None:
                self._audio_wait_since = now
            elif now - self._audio_wait_since > 2.0:
                self._clock_t0 = now
        return self._clock_t0

    def rebase_clock(self, lag_sec: float) -> None:
        if self._clock_t0 is not None and lag_sec > 0:
            self._clock_t0 += lag_sec


class UltralightSession(_SharedClock):
    is_mirror = False

    def __init__(self, runtime: EngineRuntime, sessionid: str, avatar: LoadedAvatar, edge_voice: str,
                 loop: asyncio.AbstractEventLoop, role: str = "main") -> None:
        self.runtime = runtime
        self.sessionid = sessionid
        self.role = role            # main=OBS 正式画面；preview=预览页（无 OBS 时临时当主画面）
        self.pc: Any = None
        self._mirrors: List["MirrorSession"] = []
        self._mirror_lock = threading.Lock()
        self.avatar = avatar
        self.edge_voice = edge_voice
        self._loop = loop

        self.audio_track = UltralightStreamTrack(self, "audio", avatar.fps, loop)
        self.video_track = UltralightStreamTrack(self, "video", avatar.fps, loop)
        self._started_tracks: set = set()
        self._thread: Optional[threading.Thread] = None
        self._quit = threading.Event()

        # 待播说话帧队列：每项为 (audio_window_feat, pcm_int16_slice, eventpoint)
        self._speech_q: queue.Queue = queue.Queue()
        self._speaking = False
        self._idle_idx = 0
        self._speak_idx = 0
        self._audio_accum = np.zeros(0, dtype=np.int16)
        self._gen_lock = threading.Lock()

        # 打断代次：每次打断 +1，打断前还在合成/提特征的句子一律丢弃
        self._flush_gen = 0
        self._flush_pending = threading.Event()
        # 每会话一条串行“说话”队列：文本(Edge-TTS 分句合成) 与 音频(/humanaudio) 统一排队，严格保序
        self._tts_q: asyncio.Queue = asyncio.Queue()
        self._tts_busy = False
        self._tts_task = loop.create_task(self._speech_worker())

        # 音视频共用时钟（只在事件循环线程读写）
        self._clock_t0: Optional[float] = None
        self._audio_wait_since: Optional[float] = None
        self.av_offset = max(-0.5, min(1.0, float(getattr(runtime.opt, "av_offset_ms", 0) or 0) / 1000.0))

    # ---------------- 生命周期
    def ensure_started(self, track: UltralightStreamTrack) -> None:
        self._started_tracks.add(track)
        if self._thread is None:
            self._quit.clear()
            self._thread = threading.Thread(target=self._render_loop, name=f"ul-render-{self.sessionid}", daemon=True)
            self._thread.start()

    def on_track_stop(self, track: UltralightStreamTrack) -> None:
        self._started_tracks.discard(track)
        if not self._started_tracks:
            self.close()

    def close(self) -> None:
        self._quit.set()
        try:
            self._loop.call_soon_threadsafe(self._tts_task.cancel)
        except RuntimeError:
            pass
        for m in self.mirrors():
            m.close()

    # ---------------- 预览镜像（共享同一路画面，不额外推理）
    def mirrors(self) -> List["MirrorSession"]:
        with self._mirror_lock:
            return list(self._mirrors)

    def add_mirror(self, m: "MirrorSession") -> None:
        with self._mirror_lock:
            self._mirrors.append(m)

    def remove_mirror(self, m: "MirrorSession") -> None:
        with self._mirror_lock:
            if m in self._mirrors:
                self._mirrors.remove(m)

    # ---------------- 状态
    def is_speaking(self) -> bool:
        return self._speaking or self._tts_busy or (not self._speech_q.empty()) or (not self._tts_q.empty())

    def on_event(self, ev: dict) -> None:
        st = ev.get("status")
        if st == "start":
            self._speaking = True
        elif st == "end":
            if self._speech_q.empty():
                self._speaking = False

    # ---------------- 说话入口（只在事件循环线程调用）
    def submit_text(self, text: str, voice: str) -> None:
        self._tts_q.put_nowait(("text", self._flush_gen, text, voice))

    def submit_pcm(self, pcm: np.ndarray) -> None:
        self._tts_q.put_nowait(("pcm", self._flush_gen, pcm, ""))

    async def _speech_worker(self) -> None:
        while not self._quit.is_set():
            try:
                kind, gen, payload, voice = await asyncio.wait_for(self._tts_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            if gen != self._flush_gen:
                continue
            self._tts_busy = True
            try:
                if kind == "pcm":
                    await asyncio.to_thread(self.enqueue_pcm16k, payload, gen)
                    continue
                for sent in split_sentences(payload):
                    if gen != self._flush_gen or self._quit.is_set():
                        break
                    try:
                        pcm = await synthesize_edgetts_16k(sent, voice)
                    except Exception as exc:
                        logger.warning(f"Edge-TTS 合成失败（跳过本句）: {exc}")
                        continue
                    if gen != self._flush_gen:
                        break
                    await asyncio.to_thread(self.enqueue_pcm16k, pcm, gen)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"说话队列处理异常: {exc}")
            finally:
                self._tts_busy = False

    def flush_talk(self) -> None:
        """打断：清待渲染队列 + 串行说话队列；渲染线程随后清轨道缓冲与残余音频。"""
        with self._gen_lock:
            self._flush_gen += 1
            while True:
                try:
                    self._speech_q.get_nowait()
                except queue.Empty:
                    break
            self._speaking = False
        while True:
            try:
                self._tts_q.get_nowait()
            except (asyncio.QueueEmpty, RuntimeError):
                break
        self._flush_pending.set()

    def enqueue_pcm16k(self, speech_16k: np.ndarray, gen: Optional[int] = None) -> None:
        """提取音频特征并切成与帧率严格对齐的 (audio_window, pcm_slice, event) 队列。"""
        if gen is not None and gen != self._flush_gen:
            return
        if speech_16k.shape[0] < 320:
            return
        encoder = self.runtime.get_encoder(self.avatar.asr)
        feats = encoder.extract(speech_16k)
        n_frames = int(feats.shape[0])
        if n_frames <= 0:
            return

        samples_per_frame = SAMPLE_RATE // self.avatar.fps  # 640 @25fps, 800 @20fps
        target_len = n_frames * samples_per_frame
        pcm_int16 = np.clip(speech_16k * 32767.0, -32768.0, 32767.0).astype(np.int16)
        if pcm_int16.shape[0] < target_len:
            pcm_int16 = np.pad(pcm_int16, (0, target_len - pcm_int16.shape[0]))
        else:
            pcm_int16 = pcm_int16[:target_len]

        with self._gen_lock:
            if gen is not None and gen != self._flush_gen:
                return
            self._speaking = True
            for i in range(n_frames):
                left = i - 4
                right = i + 4
                pad_l = max(0, -left)
                pad_r = max(0, right - n_frames)
                seg = feats[max(0, left):min(n_frames, right)]
                if pad_l > 0:
                    seg = np.concatenate([np.zeros((pad_l, *feats.shape[1:]), dtype=np.float32), seg], axis=0)
                if pad_r > 0:
                    seg = np.concatenate([seg, np.zeros((pad_r, *feats.shape[1:]), dtype=np.float32)], axis=0)
                pcm_slice = pcm_int16[i * samples_per_frame:(i + 1) * samples_per_frame]
                ev = {"status": "start"} if i == 0 else ({"status": "end"} if i == n_frames - 1 else None)
                self._speech_q.put((seg, pcm_slice, ev))

    # ---------------- 渲染
    def _push_video(self, bgr: np.ndarray) -> None:
        self.video_track.put((av.VideoFrame.from_ndarray(bgr, format="bgr24"), None), timeout=1.0)
        for m in self.mirrors():
            m.feed_video(bgr)

    def _push_audio_slice(self, pcm_slice: np.ndarray, eventpoint: Optional[dict]) -> None:
        if pcm_slice.shape[0] > 0:
            self._audio_accum = np.concatenate([self._audio_accum, pcm_slice])
        first_packet = True
        while self._audio_accum.shape[0] >= AUDIO_CHUNK_SAMPLES:
            chunk = self._audio_accum[:AUDIO_CHUNK_SAMPLES]
            self._audio_accum = self._audio_accum[AUDIO_CHUNK_SAMPLES:]
            raw = chunk.tobytes()
            af = av.AudioFrame(format="s16", layout="mono", samples=AUDIO_CHUNK_SAMPLES)
            af.planes[0].update(raw)
            af.sample_rate = SAMPLE_RATE
            for m in self.mirrors():
                m.feed_audio(raw)
            ev = None
            if eventpoint:
                if eventpoint.get("status") == "start" and first_packet:
                    ev = eventpoint
                elif eventpoint.get("status") == "end" and self._audio_accum.shape[0] < AUDIO_CHUNK_SAMPLES:
                    ev = eventpoint
            first_packet = False
            if not self.audio_track.put((af, ev), timeout=1.0):
                break

    def _apply_flush(self) -> None:
        self._flush_pending.clear()
        self.video_track.clear()
        self.audio_track.clear()
        self._audio_accum = np.zeros(0, dtype=np.int16)
        for m in self.mirrors():
            m.flush()

    def _render_loop(self) -> None:
        samples_per_frame = SAMPLE_RATE // self.avatar.fps
        silence_slice = np.zeros(samples_per_frame, dtype=np.int16)
        frame_interval = 1.0 / self.avatar.fps
        batch = self.avatar.batch if self.avatar.batch_ok else 1

        while not self._quit.is_set():
            if self._flush_pending.is_set():
                self._apply_flush()
            # 维持约 6 帧的轻度前瞻缓冲，兼顾低延迟与抗抖动
            if self.video_track._queue.qsize() >= 6:
                time.sleep(0.005)
                continue
            t0 = time.perf_counter()
            items: List[Tuple[np.ndarray, np.ndarray, Optional[dict]]] = []
            try:
                items.append(self._speech_q.get_nowait())
            except queue.Empty:
                pass
            if items:
                # 追帧：视频缓冲偏低且还有积压时，一次批量算多帧
                while len(items) < batch and self.video_track._queue.qsize() < 3:
                    try:
                        items.append(self._speech_q.get_nowait())
                    except queue.Empty:
                        break
                gen_at = self._flush_gen
                frames = self.avatar.infer_frames([(self._speak_idx + i, it[0]) for i, it in enumerate(items)])
                if gen_at != self._flush_gen or self._flush_pending.is_set():
                    continue  # 推理期间被打断：整批丢弃
                for (_aw, pcm_slice, ev), frame_bgr in zip(items, frames):
                    self._speak_idx += 1
                    self._push_video(frame_bgr)
                    self._push_audio_slice(pcm_slice, ev)
            else:
                if self._speaking and self._speech_q.empty() and not self._tts_busy:
                    self._speaking = False
                idx = _bounce_index(len(self.avatar.idle_frames), self._idle_idx)
                self._idle_idx += 1
                self._push_video(self.avatar.idle_frames[idx])
                self._push_audio_slice(silence_slice, None)
            dt = time.perf_counter() - t0
            if dt < frame_interval * 0.5 and self.video_track._queue.qsize() >= 3:
                time.sleep(min(0.01, frame_interval * 0.4))


class MirrorSession(_SharedClock):
    """预览镜像：复用主画面（OBS）已渲染好的音视频帧，只多一份视频编码，不再做口型推理、
    不占 --max_session 名额，也不会把主播说话的会话从 OBS 抢走。"""

    is_mirror = True
    role = "preview"

    def __init__(self, master: UltralightSession, sessionid: str, loop: asyncio.AbstractEventLoop) -> None:
        self.master = master
        self.runtime = master.runtime
        self.sessionid = sessionid
        self.avatar = master.avatar
        self.pc: Any = None
        self._quit = threading.Event()
        self._started_tracks: set = set()
        self.av_offset = master.av_offset
        # 两条轨道缓冲时长一致（约 0.5 秒）：视频 12 帧 ≈ 音频 24 包，保证预览里音画同步
        n_video = max(6, int(round(0.48 * self.avatar.fps)))
        self.video_track = UltralightStreamTrack(self, "video", self.avatar.fps, loop, maxsize=n_video, drop_oldest=True)
        self.audio_track = UltralightStreamTrack(self, "audio", self.avatar.fps, loop,
                                                 maxsize=int(round(n_video / self.avatar.fps / AUDIO_PTIME)), drop_oldest=True)
        master.add_mirror(self)

    def ensure_started(self, track: UltralightStreamTrack) -> None:
        self._started_tracks.add(track)

    def on_track_stop(self, track: UltralightStreamTrack) -> None:
        self._started_tracks.discard(track)
        if not self._started_tracks:
            self.close()

    def close(self) -> None:
        if not self._quit.is_set():
            self._quit.set()
            self.master.remove_mirror(self)

    def on_event(self, ev: dict) -> None:
        pass

    def is_speaking(self) -> bool:
        return self.master.is_speaking()

    def feed_video(self, bgr: np.ndarray) -> None:
        if not self._quit.is_set():
            self.video_track.put((av.VideoFrame.from_ndarray(bgr, format="bgr24"), None))

    def feed_audio(self, raw: bytes) -> None:
        if self._quit.is_set():
            return
        af = av.AudioFrame(format="s16", layout="mono", samples=AUDIO_CHUNK_SAMPLES)
        af.planes[0].update(raw)
        af.sample_rate = SAMPLE_RATE
        self.audio_track.put((af, None))

    def flush(self) -> None:
        self.video_track.clear()
        self.audio_track.clear()


# ---------------------------------------------------------------------------
# 后台训练/打包任务管理 (/api/avatar/task) —— 独立子进程运行，崩溃/爆显存不拖垮直播引擎
# ---------------------------------------------------------------------------
_PROGRESS_RE = re.compile(r"\[PROGRESS\]\s*(\d+)%\s*\|\s*(.*)")


class TrainTaskManager:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1)   # 同一时间只训练一个，其余排队
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.procs: Dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def submit(self, params: Dict[str, Any]) -> str:
        tid = str(uuid.uuid4())
        task = {
            "task_id": tid,
            "model_type": f"ultralight_{params.get('asr', 'hubert')}",
            "avatar_id": params["avatar_id"],
            "status": "pending",
            "progress": 0,
            "message": "排队中...",
            "error_msg": "",
            "start_time": time.time(),
            "end_time": None,
            "log_tail": [],
        }
        with self.lock:
            self.tasks[tid] = task
        self.executor.submit(self._run, tid, params)
        return tid

    def cancel(self, tid: str) -> bool:
        with self.lock:
            t = self.tasks.get(tid)
            proc = self.procs.get(tid)
            if not t or t["status"] not in ("pending", "running"):
                return False
            t["status"] = "canceled"
            t["message"] = "已取消"
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        return True

    @staticmethod
    def build_cmd(params: Dict[str, Any]) -> List[str]:
        cmd = [sys.executable, "-u", str(ROOT / "scripts" / "ultralight_pipeline.py"),
               "--avatar_id", str(params["avatar_id"]),
               "--name", str(params.get("name") or params["avatar_id"]),
               "--asr", str(params.get("asr", "hubert")),
               "--epochs", str(int(params.get("epochs", 100))),
               "--batch_size", str(int(params.get("batch_size", 16)))]
        if params.get("video_path"):
            cmd += ["--video", str(params["video_path"])]
        if params.get("checkpoint"):
            cmd += ["--checkpoint", str(params["checkpoint"])]
        if params.get("base_avatar"):
            cmd += ["--base_avatar", re.sub(r"[^a-zA-Z0-9_\-]", "", str(params["base_avatar"]))]
        return cmd

    def _run(self, tid: str, params: Dict[str, Any]) -> None:
        with self.lock:
            if self.tasks[tid]["status"] == "canceled":
                self.tasks[tid]["end_time"] = time.time()
                return
            self.tasks[tid]["status"] = "running"
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        tail: List[str] = []
        try:
            proc = subprocess.Popen(
                self.build_cmd(params), cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self.lock:
                self.procs[tid] = proc
            with (log_dir / f"train_{params['avatar_id']}.log").open("a", encoding="utf-8") as lf:
                assert proc.stdout is not None
                for line in proc.stdout:
                    lf.write(line)
                    line = line.rstrip()
                    if not line:
                        continue
                    tail = (tail + [line])[-20:]
                    m = _PROGRESS_RE.search(line)
                    with self.lock:
                        if m:
                            self.tasks[tid]["progress"] = int(m.group(1))
                            self.tasks[tid]["message"] = m.group(2)[:200]
                        self.tasks[tid]["log_tail"] = tail[-6:]
            rc = proc.wait()
            with self.lock:
                if self.tasks[tid]["status"] == "canceled":
                    pass
                elif rc == 0:
                    self.tasks[tid]["status"] = "completed"
                    self.tasks[tid]["progress"] = 100
                else:
                    self.tasks[tid]["status"] = "failed"
                    self.tasks[tid]["error_msg"] = " | ".join(tail[-4:]) or f"训练进程退出码 {rc}"
        except Exception as exc:
            logger.exception("Avatar training task failed")
            with self.lock:
                self.tasks[tid]["status"] = "failed"
                self.tasks[tid]["error_msg"] = str(exc)
        finally:
            with self.lock:
                self.procs.pop(tid, None)
                self.tasks[tid]["end_time"] = time.time()


# ---------------------------------------------------------------------------
# HTTP / WebRTC API 路由
# ---------------------------------------------------------------------------
def json_ok(data=None):
    body: Dict[str, Any] = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return web.Response(content_type="application/json", text=json.dumps(body, ensure_ascii=False))


def json_err(msg: str, code: int = -1):
    return web.Response(content_type="application/json", text=json.dumps({"code": code, "msg": str(msg)}, ensure_ascii=False))


def create_app(runtime: EngineRuntime) -> web.Application:
    task_mgr = TrainTaskManager()
    app = web.Application(client_max_size=1024 ** 2 * 512)
    api_token = str(os.environ.get("UL_API_TOKEN", "") or "").strip()

    def _token_ok(request) -> bool:
        return (not api_token) or request.headers.get("X-UL-Token", "") == api_token

    async def index(_request):
        return json_ok({
            "engine": "ultralight-digital-human",
            "asr": runtime.opt.asr,
            "backend": runtime.opt.backend,
            "default_avatar": runtime.opt.avatar_id,
            "active_sessions": len(runtime.live_sessions()),
            "preview_mirrors": sum(1 for x in runtime.sessions.values() if getattr(x, "is_mirror", False)),
            "max_session": runtime.opt.max_session,
            "loaded_avatars": list(runtime.avatars.keys()),
            "frame_cache_mb": round(sum(a.memory_mb() for a in runtime.avatars.values()), 1),
            "out_height": runtime.opt.out_height,
            "batch": runtime.opt.batch,
            "av_offset_ms": runtime.opt.av_offset_ms,
        })

    async def offer(request):
        params = await request.json()
        avatar_id = str(params.get("avatar") or runtime.opt.avatar_id).strip()
        edge_voice = str(params.get("refaudio") or runtime.opt.voice or "zh-CN-XiaoxiaoNeural").strip()
        role = "preview" if str(params.get("role") or "").lower() == "preview" else "main"
        loop = asyncio.get_running_loop()

        with runtime.sess_lock:
            # 先清理已关闭的僵尸连接
            for sid_old in [sid for sid, s in runtime.sessions.items() if s._quit.is_set()]:
                runtime.sessions.pop(sid_old, None)
            masters = [s for s in runtime.sessions.values() if not s.is_mirror]
            main_live = next((s for s in masters if s.role == "main"), None)
            preview_master = next((s for s in masters if s.role == "preview"), None)

        session: Any = None
        if role == "preview" and (main_live or preview_master):
            # 已有画面在推（通常是 OBS）：预览直接共享这一路，不额外推理、不占名额
            src = main_live or preview_master
            if sum(1 for s in runtime.sessions.values() if s.is_mirror) >= int(runtime.opt.max_preview):
                return json_err("预览窗口开太多了，请先关掉其他预览页")
            session = MirrorSession(src, uuid.uuid4().hex[:12], loop)
        else:
            if role == "main" and preview_master is not None and len(masters) >= runtime.opt.max_session:
                # OBS 优先：临时当主画面的预览页让出名额，它会自动重连为镜像
                logger.info(f"OBS 接入，预览会话 {preview_master.sessionid} 让出画面名额并改为共享模式")
                preview_master.close()
                if preview_master.pc is not None:
                    try:
                        await preview_master.pc.close()
                    except Exception:
                        pass
                with runtime.sess_lock:
                    runtime.sessions.pop(preview_master.sessionid, None)
                masters = [s for s in masters if s is not preview_master]
            if len(masters) >= runtime.opt.max_session:
                return json_err("Maximum session reached", code=-1)
            try:
                avatar = await asyncio.to_thread(runtime.load_avatar, avatar_id)
            except Exception as exc:
                return json_err(f"加载形象 [{avatar_id}] 失败: {exc}")
            session = UltralightSession(runtime, uuid.uuid4().hex[:12], avatar, edge_voice, loop, role=role)

        sid = session.sessionid
        with runtime.sess_lock:
            runtime.sessions[sid] = session

        pc = RTCPeerConnection()
        session.pc = pc
        runtime.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_conn_state():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                session.close()
                with runtime.sess_lock:
                    runtime.sessions.pop(sid, None)
                runtime.pcs.discard(pc)
                try:
                    await pc.close()
                except Exception:
                    pass

        pc.addTrack(session.audio_track)
        pc.addTrack(session.video_track)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.Response(
            content_type="application/json",
            text=json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "sessionid": sid,
                             "role": session.role, "mirror": bool(session.is_mirror),
                             "master_sessionid": session.master.sessionid if session.is_mirror else sid}),
        )

    def _speech_session(sid: str):
        """说话类接口：传进来的若是预览镜像 ID，转给它镜像的主画面，保证 OBS 一定能说话。"""
        s = runtime.sessions.get(sid)
        return s.master if (s is not None and s.is_mirror) else s

    async def human(request):
        params = await request.json()
        session = _speech_session(str(params.get("sessionid") or ""))
        if not session:
            return json_err("session not found")
        if params.get("interrupt"):
            session.flush_talk()
        text = str(params.get("text") or "").strip()
        if not text:
            return json_ok()
        tts_p = params.get("tts") or {}
        voice = str(tts_p.get("ref_file") or tts_p.get("voice") or session.edge_voice or "zh-CN-XiaoxiaoNeural")
        session.submit_text(text, voice)   # 串行队列：严格按到达顺序播出
        return json_ok()

    async def humanaudio(request):
        form = await request.post()
        session = _speech_session(str(form.get("sessionid") or ""))
        if not session:
            return json_err("session not found")
        raw = form["file"].file.read()
        pcm = await asyncio.to_thread(decode_audio_bytes_to_16k, raw)
        session.submit_pcm(pcm)
        return json_ok()

    async def interrupt_talk(request):
        params = await request.json()
        session = _speech_session(str(params.get("sessionid") or ""))
        if not session:
            return json_err("session not found")
        session.flush_talk()
        return json_ok()

    async def is_speaking(request):
        params = await request.json()
        session = _speech_session(str(params.get("sessionid") or ""))
        if not session:
            return json_err("session not found")
        return json_ok(data=session.is_speaking())

    async def set_audiotype(request):
        params = await request.json()
        if str(params.get("sessionid") or "") not in runtime.sessions:
            return json_err("session not found")
        return json_ok()

    async def list_avatars(_request):
        items = []
        if AVATARS_DIR.is_dir():
            for sub in sorted(AVATARS_DIR.iterdir()):
                if not sub.is_dir() or sub.name.startswith(("__", ".")):
                    continue
                meta = {}
                if (sub / "meta.json").is_file():
                    try:
                        meta = json.loads((sub / "meta.json").read_text(encoding="utf-8"))
                    except Exception:
                        pass
                items.append({
                    "id": sub.name,
                    "ready": (sub / "unet.onnx").is_file() or (sub / "ultralight.pth").is_file(),
                    "asr": meta.get("asr", "hubert"),
                    "fps": meta.get("fps", 25),
                    "has_onnx": (sub / "unet.onnx").is_file(),
                    "has_pth": (sub / "ultralight.pth").is_file(),
                    "frames": meta.get("frames", 0),
                    "loaded": sub.name in runtime.avatars,
                })
        return json_ok({"dir": str(AVATARS_DIR), "avatars": items})

    async def _save_part(part, tmp_dir: Path, default_name: str, allowed: set) -> Path:
        name = Path(part.filename or default_name).name
        if Path(name).suffix.lower() not in allowed:
            raise ValueError(f"不支持的文件类型: {name}")
        dst = tmp_dir / f"{uuid.uuid4().hex}_{name}"
        with dst.open("wb") as f:
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                f.write(chunk)
        return dst

    async def create_avatar_task(request):
        if not _token_ok(request):
            return json_err("token 校验失败", code=403)
        tmp_dir = ROOT / "logs" / "tmp_uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        params: Dict[str, Any] = {}
        try:
            if request.content_type.startswith("multipart/form-data"):
                reader = await request.multipart()
                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    if part.name == "video_file":
                        params["video_path"] = str(await _save_part(part, tmp_dir, "upload.mp4", VIDEO_EXTS))
                    elif part.name == "ckpt_file":
                        params["checkpoint"] = str(await _save_part(part, tmp_dir, "ckpt.pth", CKPT_EXTS))
                    else:
                        params[part.name] = await part.text()
            else:
                params = await request.json()
        except ValueError as exc:
            return json_err(str(exc))

        aid = re.sub(r"[^a-zA-Z0-9_\-]", "", str(params.get("avatar_id") or "").strip())
        if not aid:
            return json_err("avatar_id is required")
        params["avatar_id"] = aid
        for key, allowed in (("video_path", VIDEO_EXTS), ("checkpoint", CKPT_EXTS)):
            if params.get(key):
                pth = Path(str(params[key]))
                if not pth.is_file() or pth.suffix.lower() not in allowed:
                    return json_err(f"{key} 文件不存在或类型不支持: {pth.name}")
        force = str(params.get("force", "")).strip().lower() in ("1", "true", "yes", "on")
        live = runtime.live_sessions()
        if live and not force:
            return json_err(
                f"当前有 {len(live)} 路直播画面在线。训练会占满显卡，可能让直播爆显存断播；"
                f"请下播（关闭 OBS 浏览器源/预览）后再训练，或确认已下播后勾选“强制开始”。", code=409)
        tid = task_mgr.submit(params)
        return json_ok({"task_id": tid, "queued_behind": max(0, sum(
            1 for t in task_mgr.tasks.values() if t["status"] in ("pending", "running")) - 1)})

    async def get_avatar_task(request):
        t = task_mgr.tasks.get(request.match_info.get("task_id", ""))
        if not t:
            return json_err("Task not found", code=404)
        return json_ok(t)

    async def cancel_avatar_task(request):
        if not _token_ok(request):
            return json_err("token 校验失败", code=403)
        ok = task_mgr.cancel(request.match_info.get("task_id", ""))
        return json_ok({"canceled": ok}) if ok else json_err("任务不存在或已结束")

    async def admin_sessions(_request):
        info = []
        for sid, s in runtime.sessions.items():
            info.append({
                "sessionid": sid,
                "avatar_id": s.avatar.avatar_id,
                "asr": s.avatar.asr,
                "fps": s.avatar.fps,
                "speaking": s.is_speaking(),
                "live": not s._quit.is_set(),
                "role": s.role,
                "mirror": bool(s.is_mirror),
            })
        return json_ok({"sessions": info})

    async def on_shutdown(_app):
        for s in list(runtime.sessions.values()):
            s.close()
        for pc in list(runtime.pcs):
            try:
                await pc.close()
            except Exception:
                pass
        for proc in list(task_mgr.procs.values()):
            try:
                proc.terminate()
            except OSError:
                pass

    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_post("/human", human)
    app.router.add_post("/humanaudio", humanaudio)
    app.router.add_post("/interrupt_talk", interrupt_talk)
    app.router.add_post("/is_speaking", is_speaking)
    app.router.add_post("/set_audiotype", set_audiotype)
    app.router.add_get("/api/avatar/list", list_avatars)
    app.router.add_post("/api/avatar/task", create_avatar_task)
    app.router.add_get("/api/avatar/task/{task_id}", get_avatar_task)
    app.router.add_post("/api/avatar/task/{task_id}/cancel", cancel_avatar_task)
    app.router.add_get("/api/admin/sessions", admin_sessions)

    # CORS 只放行本机中控/引擎来源；额外来源用环境变量 UL_CORS_ORIGINS（逗号分隔）
    origins = list(DEFAULT_CORS_ORIGINS) + [
        o.strip() for o in str(os.environ.get("UL_CORS_ORIGINS", "")).split(",") if o.strip()]
    cors = aiohttp_cors.setup(app, defaults={
        o: aiohttp_cors.ResourceOptions(allow_credentials=False, expose_headers="*", allow_headers="*")
        for o in origins
    })
    for route in list(app.router.routes()):
        cors.add(route)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Ultralight-Digital-Human Native Streaming Server")
    ap.add_argument("--asr", default=os.environ.get("UL_ASR", "hubert"), choices=["hubert", "wenet"])
    ap.add_argument("--backend", default=os.environ.get("UL_BACKEND", "onnx"), choices=["onnx", "torch"])
    ap.add_argument("--avatar_id", default=os.environ.get("UL_AVATAR", "demo_ul_host"))
    ap.add_argument("--voice", default=os.environ.get("UL_VOICE", "zh-CN-XiaoxiaoNeural"))
    ap.add_argument("--max_session", type=int, default=_env_int("UL_MAX_SESSION", 1),
                    help="同时输出几路画面（OBS 只需 1 路；每多 1 路多一份 CPU 编码）")
    ap.add_argument("--listenport", type=int, default=_env_int("UL_PORT", 8020))
    ap.add_argument("--host", default=os.environ.get("UL_HOST", "127.0.0.1") or "127.0.0.1",
                    help="监听地址：127.0.0.1 只给本机用（安全，默认）；0.0.0.0 对局域网开放（有风险）")
    ap.add_argument("--max_avatar_frames", type=int, default=_env_int("UL_MAX_FRAMES", 300))
    ap.add_argument("--out_height", type=int, default=_env_int("UL_OUT_HEIGHT", 1280),
                    help="帧缩放到此高度后常驻内存与推流；0=保持原尺寸")
    ap.add_argument("--batch", type=int, default=_env_int("UL_BATCH", 4), help="积压追帧时每次推理帧数")
    ap.add_argument("--max_loaded_avatars", type=int, default=_env_int("UL_MAX_LOADED", 2), help="LRU 常驻形象数")
    ap.add_argument("--ort_gpu_mem_mb", type=int, default=_env_int("UL_ORT_GPU_MB", 2048),
                    help="ONNX Runtime 显存池上限(MB)，0=不限制")
    ap.add_argument("--max_preview", type=int, default=_env_int("UL_MAX_PREVIEW", 1),
                    help="最多几个预览窗口共享 OBS 画面（每个多一份视频编码，CPU 吃紧时调 1）")
    ap.add_argument("--av_offset_ms", type=int, default=_env_int("UL_AV_OFFSET_MS", 0),
                    help="音画微调(ms)：正数=声音延后（嘴型比声音慢时调大），负数=声音提前；范围 -500~1000")
    opt = ap.parse_args()
    if opt.host not in ("127.0.0.1", "localhost"):
        logger.warning(f"引擎监听 {opt.host}：同一局域网内的设备都能控制数字人和提交训练任务，请确认网络可信！")

    runtime = EngineRuntime(opt)
    # 预热默认形象（若已存在），避免首次连接等待
    if (AVATARS_DIR / opt.avatar_id).is_dir():
        try:
            runtime.load_avatar(opt.avatar_id)
        except Exception as exc:
            logger.warning(f"默认形象 [{opt.avatar_id}] 预加载跳过（请先训练或导入形象）: {exc}")
    else:
        logger.info(f"尚未制作默认形象 [{opt.avatar_id}]，服务正常启动；请先用 04_train_avatar.bat 或面板制作/导入形象。")

    app = create_app(runtime)
    logger.info(f"Ultralight-Digital-Human 引擎启动于 http://{opt.host}:{opt.listenport} "
                f"(asr={opt.asr}, backend={opt.backend}, out_height={opt.out_height}, batch={opt.batch}, "
                f"max_session={opt.max_session}, ort_gpu_mem={opt.ort_gpu_mem_mb}MB, av_offset={opt.av_offset_ms}ms)")
    web.run_app(app, host=opt.host, port=opt.listenport, print=None)


if __name__ == "__main__":
    main()
