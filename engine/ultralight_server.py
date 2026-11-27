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
      GET  /api/admin/sessions
"""
from __future__ import annotations

import argparse
import asyncio
import fractions
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

from engine.audio_encoders import HuBERTEncoder, WeNetEncoder, ensure_onnxruntime_cuda_paths  # noqa: E402
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
            arr = resampled.to_ndarray().reshape(-1).astype(np.float32)
            chunks.append(arr)
    for resampled in resampler.resample(None):
        arr = resampled.to_ndarray().reshape(-1).astype(np.float32)
        chunks.append(arr)
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

        # 预裁切好说话循环帧的 168x168 人脸底图，避免渲染循环重复 cv2.resize 占用 CPU
        self.face_crops: List[np.ndarray] = []
        for frame, (xmin, ymin, xmax, ymax) in zip(self.speak_frames, self.speak_bboxes):
            region = frame[ymin:ymax, xmin:xmax]
            crop = cv2.resize(region, (FACE_CROP_SIZE, FACE_CROP_SIZE), interpolation=cv2.INTER_AREA)
            self.face_crops.append(crop)

    def infer_frame(self, frame_idx: int, audio_win: np.ndarray) -> np.ndarray:
        """对单帧执行 Ultralight UNet 推理并贴回全身背景帧，返回 BGR uint8。"""
        idx = _bounce_index(len(self.speak_frames), frame_idx)
        base = self.speak_frames[idx].copy()
        xmin, ymin, xmax, ymax = self.speak_bboxes[idx]
        crop_ori = self.face_crops[idx].copy()
        inner = crop_ori[FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE, FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE].copy()
        masked = cv2.rectangle(inner.copy(), MASK_RECT, (0, 0, 0), -1)

        inner_chw = inner.transpose(2, 0, 1).astype(np.float32) / 255.0
        masked_chw = masked.transpose(2, 0, 1).astype(np.float32) / 255.0
        img_in = np.concatenate([inner_chw, masked_chw], axis=0)[None]  # [1, 6, 160, 160]

        if self.asr == "wenet":
            aud_in = audio_win.reshape(1, 128, 16, 32).astype(np.float32)
        else:
            aud_in = audio_win.reshape(1, 16, 32, 32).astype(np.float32)

        with self._infer_lock:
            if self.unet_ort is not None:
                in0 = self.unet_ort.get_inputs()[0].name
                in1 = self.unet_ort.get_inputs()[1].name
                pred = self.unet_ort.run(None, {in0: img_in, in1: aud_in})[0][0]
            elif self.unet_torch is not None:
                with torch.no_grad():
                    t_img = torch.from_numpy(img_in).to(self.device)
                    t_aud = torch.from_numpy(aud_in).to(self.device)
                    pred = self.unet_torch(t_img, t_aud)[0].cpu().numpy()
            else:
                return base

        pred_bgr = np.clip(pred.transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)
        crop_ori[FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE, FACE_BORDER:FACE_BORDER + FACE_INNER_SIZE] = pred_bgr
        crop_resized = cv2.resize(crop_ori, (xmax - xmin, ymax - ymin))
        base[ymin:ymax, xmin:xmax] = crop_resized
        return base


class EngineRuntime:
    def __init__(self, opt: argparse.Namespace) -> None:
        self.opt = opt
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.avatars: Dict[str, LoadedAvatar] = {}
        self.encoders: Dict[str, Any] = {}
        self._load_lock = threading.RLock()
        self.sessions: Dict[str, "UltralightSession"] = {}
        self.pcs: set = set()
        self.sess_lock = threading.Lock()

    def get_encoder(self, asr: str):
        with self._load_lock:
            if asr in self.encoders:
                return self.encoders[asr]
            if asr == "wenet":
                enc_p = MODELS_DIR / "encoder.onnx"
                if not enc_p.is_file():
                    enc_p = UDH_DIR / "data_utils" / "encoder.onnx"
                enc = WeNetEncoder(enc_p)
            else:
                enc = HuBERTEncoder(MODELS_DIR / "hubert-large-ls960-ft")
            self.encoders[asr] = enc
            return enc

    @staticmethod
    def _load_frame_set(img_dir: Path, lms_dir: Path, bboxes_json: Optional[Path], max_frames: int) -> Tuple[List[np.ndarray], List[Tuple[int, int, int, int]]]:
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
            h, w = im.shape[:2]
            if cached_bboxes and idx < len(cached_bboxes):
                xmin, ymin, xmax, ymax = [int(v) for v in cached_bboxes[idx]]
            else:
                lms_p = lms_dir / f"{img_p.stem}.lms"
                xmin, ymin, xmax, ymax = compute_face_bbox(read_landmarks(str(lms_p)))
            xmin = max(0, min(w - 16, xmin))
            ymin = max(0, min(h - 16, ymin))
            xmax = max(xmin + 16, min(w, xmax))
            ymax = max(ymin + 16, min(h, ymax))
            frames.append(im)
            bboxes.append((xmin, ymin, xmax, ymax))
        return frames, bboxes

    def load_avatar(self, avatar_id: str, force_reload: bool = False) -> LoadedAvatar:
        with self._load_lock:
            if not force_reload and avatar_id in self.avatars:
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
                idle_frames = [cv2.imread(str(p)) for p in idle_imgs]
                idle_frames = [f for f in idle_frames if f is not None]

            unet_ort = None
            unet_torch = None
            onnx_p = av_dir / "unet.onnx"
            pth_p = av_dir / "ultralight.pth"

            if (self.opt.backend == "onnx" or not pth_p.is_file()) and onnx_p.is_file():
                import onnxruntime as ort

                providers = (
                    ["CUDAExecutionProvider", "CPUExecutionProvider"]
                    if "CUDAExecutionProvider" in ort.get_available_providers()
                    else ["CPUExecutionProvider"]
                )
                unet_ort = ort.InferenceSession(str(onnx_p), providers=providers)
                logger.info(f"[{avatar_id}] 已加载 ONNX 模型 ({asr}/{fps}fps, providers={unet_ort.get_providers()})")
            elif pth_p.is_file():
                net = Model(6, asr).to(self.device)
                ckpt = torch.load(str(pth_p), map_location=self.device, weights_only=False)
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
            )
            self.avatars[avatar_id] = loaded
            return loaded


# ---------------------------------------------------------------------------
# WebRTC 音视频轨道与渲染会话
# ---------------------------------------------------------------------------
class UltralightStreamTrack(MediaStreamTrack):
    def __init__(self, session: "UltralightSession", kind: str, fps: int) -> None:
        super().__init__()
        self.session = session
        self.kind = kind
        self.fps = fps
        self.video_ptime = 1.0 / max(1, fps)
        self._queue: queue.Queue = queue.Queue(maxsize=120)
        self._start: Optional[float] = None
        self._timestamp: int = 0
        self._frame_count: int = 0

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise Exception("track stopped")
        if self.kind == "video":
            if self._start is None:
                self._start = time.time()
                self._timestamp = 0
            else:
                self._timestamp += int(self.video_ptime * VIDEO_CLOCK_RATE)
                self._frame_count += 1
                wait = self._start + self._frame_count * self.video_ptime - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
            return self._timestamp, VIDEO_TIME_BASE
        else:
            if self._start is None:
                self._start = time.time()
                self._timestamp = 0
            else:
                self._timestamp += int(AUDIO_PTIME * SAMPLE_RATE)
                self._frame_count += 1
                wait = self._start + self._frame_count * AUDIO_PTIME - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
            return self._timestamp, AUDIO_TIME_BASE

    async def recv(self):
        self.session.ensure_started(self)
        while True:
            try:
                frame, eventpoint = self._queue.get_nowait()
                break
            except queue.Empty:
                if self.readyState != "live":
                    raise Exception("track stopped")
                await asyncio.sleep(0.004)
        pts, tb = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = tb
        if eventpoint:
            self.session.on_event(eventpoint)
        return frame

    def stop(self) -> None:
        super().stop()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self.session.on_track_stop(self)


class UltralightSession:
    def __init__(self, runtime: EngineRuntime, sessionid: str, avatar: LoadedAvatar, edge_voice: str) -> None:
        self.runtime = runtime
        self.sessionid = sessionid
        self.avatar = avatar
        self.edge_voice = edge_voice

        self.audio_track = UltralightStreamTrack(self, "audio", avatar.fps)
        self.video_track = UltralightStreamTrack(self, "video", avatar.fps)
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

    def is_speaking(self) -> bool:
        return self._speaking or (not self._speech_q.empty())

    def on_event(self, ev: dict) -> None:
        st = ev.get("status")
        if st == "start":
            self._speaking = True
        elif st == "end":
            if self._speech_q.empty():
                self._speaking = False

    def flush_talk(self) -> None:
        with self._gen_lock:
            while not self._speech_q.empty():
                try:
                    self._speech_q.get_nowait()
                except queue.Empty:
                    break
            self._speaking = False

    def enqueue_pcm16k(self, speech_16k: np.ndarray) -> None:
        """提取音频特征并切成与帧率严格对齐的 (audio_window, pcm_slice, event) 队列。"""
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

    def _push_video(self, bgr: np.ndarray) -> None:
        vf = av.VideoFrame.from_ndarray(bgr, format="bgr24")
        try:
            self.video_track._queue.put((vf, None), timeout=1.0)
        except queue.Full:
            pass

    def _push_audio_slice(self, pcm_slice: np.ndarray, eventpoint: Optional[dict]) -> None:
        if pcm_slice.shape[0] > 0:
            self._audio_accum = np.concatenate([self._audio_accum, pcm_slice])
        first_packet = True
        while self._audio_accum.shape[0] >= AUDIO_CHUNK_SAMPLES:
            chunk = self._audio_accum[:AUDIO_CHUNK_SAMPLES]
            self._audio_accum = self._audio_accum[AUDIO_CHUNK_SAMPLES:]
            af = av.AudioFrame(format="s16", layout="mono", samples=AUDIO_CHUNK_SAMPLES)
            af.planes[0].update(chunk.tobytes())
            af.sample_rate = SAMPLE_RATE
            ev = None
            if eventpoint:
                if eventpoint.get("status") == "start" and first_packet:
                    ev = eventpoint
                elif eventpoint.get("status") == "end" and self._audio_accum.shape[0] < AUDIO_CHUNK_SAMPLES:
                    ev = eventpoint
            first_packet = False
            try:
                self.audio_track._queue.put((af, ev), timeout=1.0)
            except queue.Full:
                break

    def _render_loop(self) -> None:
        samples_per_frame = SAMPLE_RATE // self.avatar.fps
        silence_slice = np.zeros(samples_per_frame, dtype=np.int16)
        frame_interval = 1.0 / self.avatar.fps

        while not self._quit.is_set():
            # 维持约 6 帧的轻度前瞻缓冲，兼顾低延迟与抗抖动
            if self.video_track._queue.qsize() >= 6:
                time.sleep(0.005)
                continue
            t0 = time.perf_counter()
            try:
                audio_win, pcm_slice, ev = self._speech_q.get_nowait()
                frame_bgr = self.avatar.infer_frame(self._speak_idx, audio_win)
                self._speak_idx += 1
                self._push_video(frame_bgr)
                self._push_audio_slice(pcm_slice, ev)
            except queue.Empty:
                if self._speaking and self._speech_q.empty():
                    self._speaking = False
                idx = _bounce_index(len(self.avatar.idle_frames), self._idle_idx)
                self._idle_idx += 1
                idle_bgr = self.avatar.idle_frames[idx]
                self._push_video(idle_bgr)
                self._push_audio_slice(silence_slice, None)
            dt = time.perf_counter() - t0
            if dt < frame_interval * 0.5 and self.video_track._queue.qsize() >= 3:
                time.sleep(min(0.01, frame_interval * 0.4))


# ---------------------------------------------------------------------------
# 后台训练/打包任务管理 (/api/avatar/task)
# ---------------------------------------------------------------------------
class TrainTaskManager:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.tasks: Dict[str, Dict[str, Any]] = {}
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
        }
        with self.lock:
            self.tasks[tid] = task
        self.executor.submit(self._run, tid, params)
        return tid

    def _run(self, tid: str, params: Dict[str, Any]) -> None:
        from scripts.ultralight_pipeline import run_pipeline

        with self.lock:
            self.tasks[tid]["status"] = "running"

        def _cb(pct: int, msg: str) -> None:
            with self.lock:
                self.tasks[tid]["progress"] = pct
                self.tasks[tid]["message"] = msg

        try:
            run_pipeline(
                avatar_id=params["avatar_id"],
                name=params.get("name") or params["avatar_id"],
                asr=params.get("asr", "hubert"),
                video_path=Path(params["video_path"]) if params.get("video_path") else None,
                checkpoint=Path(params["checkpoint"]) if params.get("checkpoint") else None,
                epochs=int(params.get("epochs", 100)),
                batch_size=int(params.get("batch_size", 16)),
                progress_cb=_cb,
            )
            with self.lock:
                self.tasks[tid]["status"] = "completed"
                self.tasks[tid]["progress"] = 100
        except Exception as exc:
            logger.exception("Avatar training task failed")
            with self.lock:
                self.tasks[tid]["status"] = "failed"
                self.tasks[tid]["error_msg"] = str(exc)
        finally:
            with self.lock:
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

    async def index(_request):
        return json_ok({
            "engine": "ultralight-digital-human",
            "asr": runtime.opt.asr,
            "backend": runtime.opt.backend,
            "default_avatar": runtime.opt.avatar_id,
            "active_sessions": len(runtime.sessions),
            "max_session": runtime.opt.max_session,
        })

    async def offer(request):
        params = await request.json()
        avatar_id = str(params.get("avatar") or runtime.opt.avatar_id).strip()
        edge_voice = str(params.get("refaudio") or runtime.opt.voice or "zh-CN-XiaoxiaoNeural").strip()

        with runtime.sess_lock:
            if len(runtime.sessions) >= runtime.opt.max_session:
                # 先清理已关闭的僵尸连接
                stale = [sid for sid, s in runtime.sessions.items() if s._quit.is_set()]
                for sid in stale:
                    runtime.sessions.pop(sid, None)
                if len(runtime.sessions) >= runtime.opt.max_session:
                    return json_err("Maximum session reached", code=-1)

        try:
            avatar = await asyncio.to_thread(runtime.load_avatar, avatar_id)
        except Exception as exc:
            return json_err(f"加载形象 [{avatar_id}] 失败: {exc}")

        sid = str(int(time.time() * 1000) % 1000000 + len(runtime.sessions) + 1)
        session = UltralightSession(runtime, sid, avatar, edge_voice)
        with runtime.sess_lock:
            runtime.sessions[sid] = session

        pc = RTCPeerConnection()
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
        offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        await pc.setRemoteDescription(offer_sdp)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "sessionid": sid,
            }),
        )

    async def human(request):
        params = await request.json()
        sid = str(params.get("sessionid") or "")
        session = runtime.sessions.get(sid)
        if not session:
            return json_err("session not found")
        if params.get("interrupt"):
            session.flush_talk()
        text = str(params.get("text") or "").strip()
        if not text:
            return json_ok()
        tts_p = params.get("tts") or {}
        voice = str(tts_p.get("ref_file") or tts_p.get("voice") or session.edge_voice or "zh-CN-XiaoxiaoNeural")

        async def _synth_and_push():
            try:
                pcm = await synthesize_edgetts_16k(text, voice)
                await asyncio.to_thread(session.enqueue_pcm16k, pcm)
            except Exception as exc:
                logger.warning(f"Edge-TTS 合成失败: {exc}")

        asyncio.create_task(_synth_and_push())
        return json_ok()

    async def humanaudio(request):
        form = await request.post()
        sid = str(form.get("sessionid") or "")
        session = runtime.sessions.get(sid)
        if not session:
            return json_err("session not found")
        fileobj = form["file"]
        raw = fileobj.file.read()

        def _proc():
            pcm = decode_audio_bytes_to_16k(raw)
            session.enqueue_pcm16k(pcm)

        await asyncio.to_thread(_proc)
        return json_ok()

    async def interrupt_talk(request):
        params = await request.json()
        sid = str(params.get("sessionid") or "")
        session = runtime.sessions.get(sid)
        if not session:
            return json_err("session not found")
        session.flush_talk()
        return json_ok()

    async def is_speaking(request):
        params = await request.json()
        sid = str(params.get("sessionid") or "")
        session = runtime.sessions.get(sid)
        if not session:
            return json_err("session not found")
        return json_ok(data=session.is_speaking())

    async def set_audiotype(request):
        params = await request.json()
        sid = str(params.get("sessionid") or "")
        if sid not in runtime.sessions:
            return json_err("session not found")
        return json_ok()

    async def list_avatars(_request):
        items = []
        if AVATARS_DIR.is_dir():
            for sub in sorted(AVATARS_DIR.iterdir()):
                if not sub.is_dir() or sub.name.startswith(("__", ".")):
                    continue
                ready = (sub / "unet.onnx").is_file() or (sub / "ultralight.pth").is_file()
                meta = {}
                if (sub / "meta.json").is_file():
                    try:
                        meta = json.loads((sub / "meta.json").read_text(encoding="utf-8"))
                    except Exception:
                        pass
                items.append({
                    "id": sub.name,
                    "ready": ready,
                    "asr": meta.get("asr", "hubert"),
                    "fps": meta.get("fps", 25),
                    "has_onnx": (sub / "unet.onnx").is_file(),
                    "has_pth": (sub / "ultralight.pth").is_file(),
                    "frames": meta.get("frames", 0),
                })
        return json_ok({"dir": str(AVATARS_DIR), "avatars": items})

    async def create_avatar_task(request):
        tmp_dir = ROOT / "logs" / "tmp_uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        params: Dict[str, Any] = {}
        if request.content_type.startswith("multipart/form-data"):
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "video_file":
                    dst = tmp_dir / f"{uuid.uuid4().hex}_{Path(part.filename or 'upload.mp4').name}"
                    with dst.open("wb") as f:
                        while True:
                            chunk = await part.read_chunk()
                            if not chunk:
                                break
                            f.write(chunk)
                    params["video_path"] = str(dst)
                elif part.name == "ckpt_file":
                    dst = tmp_dir / f"{uuid.uuid4().hex}_{Path(part.filename or 'ckpt.pth').name}"
                    with dst.open("wb") as f:
                        while True:
                            chunk = await part.read_chunk()
                            if not chunk:
                                break
                            f.write(chunk)
                    params["checkpoint"] = str(dst)
                else:
                    params[part.name] = await part.text()
        else:
            params = await request.json()

        aid = re.sub(r"[^a-zA-Z0-9_\-]", "", str(params.get("avatar_id") or "").strip())
        if not aid:
            return json_err("avatar_id is required")
        params["avatar_id"] = aid
        tid = task_mgr.submit(params)
        return json_ok({"task_id": tid})

    async def get_avatar_task(request):
        tid = request.match_info.get("task_id", "")
        t = task_mgr.tasks.get(tid)
        if not t:
            return json_err("Task not found", code=404)
        return json_ok(t)

    async def admin_sessions(_request):
        info = []
        for sid, s in runtime.sessions.items():
            info.append({
                "sessionid": sid,
                "avatar_id": s.avatar.avatar_id,
                "asr": s.avatar.asr,
                "fps": s.avatar.fps,
                "speaking": s.is_speaking(),
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
    app.router.add_get("/api/admin/sessions", admin_sessions)

    cors = aiohttp_cors.setup(
        app,
        defaults={"*": aiohttp_cors.ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*")},
    )
    for route in list(app.router.routes()):
        cors.add(route)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Ultralight-Digital-Human Native Streaming Server")
    ap.add_argument("--asr", default=os.environ.get("UL_ASR", "hubert"), choices=["hubert", "wenet"])
    ap.add_argument("--backend", default=os.environ.get("UL_BACKEND", "onnx"), choices=["onnx", "torch"])
    ap.add_argument("--avatar_id", default=os.environ.get("UL_AVATAR", "demo_ul_host"))
    ap.add_argument("--voice", default=os.environ.get("UL_VOICE", "zh-CN-XiaoxiaoNeural"))
    ap.add_argument("--max_session", type=int, default=int(os.environ.get("UL_MAX_SESSION", "2")))
    ap.add_argument("--listenport", type=int, default=int(os.environ.get("UL_PORT", "8020")))
    ap.add_argument("--max_avatar_frames", type=int, default=600)
    opt = ap.parse_args()

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
    logger.info(f"Ultralight-Digital-Human 引擎启动于 http://127.0.0.1:{opt.listenport} (asr={opt.asr}, backend={opt.backend})")
    web.run_app(app, host="0.0.0.0", port=opt.listenport, print=None)


if __name__ == "__main__":
    main()
