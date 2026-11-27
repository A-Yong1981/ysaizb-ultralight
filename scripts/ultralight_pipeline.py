# -*- coding: utf-8 -*-
"""
ultralight_pipeline.py —— Ultralight-Digital-Human 一键预处理、训练、ONNX 导出与形象打包流水线

支持两条 ASR 路线：
  - wenet  (20 fps, 纯 ONNX 超轻量推理，显存 < 1GB)
  - hubert (25 fps, HuBERT FP16 + UNet ONNX/PyTorch，嘴型精度更高)

也支持「外部已有 .pth/.onnx 权重直接导入打包」（--checkpoint）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
import soundfile as sf
import torch

ROOT = Path(__file__).resolve().parent.parent
UDH_DIR = ROOT / "Ultralight-Digital-Human"
MODELS_DIR = ROOT / "models"
AVATARS_DIR = ROOT / "avatars"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(UDH_DIR) not in sys.path:
    sys.path.insert(0, str(UDH_DIR))

from engine.audio_encoders import ensure_onnxruntime_cuda_paths, safe_torch_load  # noqa: E402
from face_utils import compute_face_bbox, read_landmarks  # noqa: E402
from unet import Model  # noqa: E402

ensure_onnxruntime_cuda_paths()

FPS_BY_ASR = {"hubert": 25, "wenet": 20}


def _emit(cb: Optional[Callable[[int, str], None]], pct: int, msg: str) -> None:
    print(f"[PROGRESS] {pct:3d}% | {msg}", flush=True)
    if cb:
        cb(pct, msg)


def _probe_video(video_path: Path) -> Tuple[bool, float, int, int]:
    """检测视频是否包含音频流、FPS、宽高。"""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频文件: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()

    has_audio = False
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True,
        )
        has_audio = "audio" in (r.stdout or "").lower()
    else:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        r = subprocess.run([ffmpeg, "-i", str(video_path)], capture_output=True, text=True)
        has_audio = "Audio:" in (r.stderr or "")
    return has_audio, fps, w, h


def _transcode_video(src: Path, dst: Path, fps: int, keep_audio: bool = True) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先运行 01_install.bat 安装依赖")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 短边上限 720，保持横竖屏原比例且宽高均为偶数
    vf = f"scale='if(gt(iw,ih),-2,min(ih,720))':'if(gt(iw,ih),min(iw,720),-2)',scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps}"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
    if keep_audio:
        cmd += ["-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1"]
    else:
        cmd += ["-an"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)


def _extract_frames_and_audio(video_path: Path, full_img_dir: Path, wav_path: Optional[Path]) -> int:
    full_img_dir.mkdir(parents=True, exist_ok=True)
    for old in full_img_dir.glob("*.jpg"):
        old.unlink()

    if wav_path is not None:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(video_path), "-vn", "-f", "wav", "-ar", "16000", "-ac", "1", str(wav_path)],
            check=True,
        )

    cap = cv2.VideoCapture(str(video_path))
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imwrite(str(full_img_dir / f"{idx}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        idx += 1
    cap.release()
    if idx < 2:
        raise RuntimeError(f"视频帧数过少 ({idx})，至少需要 2 帧")
    return idx


def _detect_landmarks_resilient(full_img_dir: Path, lms_dir: Path, n_frames: int) -> List[Tuple[int, int, int, int]]:
    """逐帧检测 98 点人脸关键点；若偶发单帧漏检，自动复用上一有效帧关键点并钳位 bbox。"""
    lms_dir.mkdir(parents=True, exist_ok=True)
    for old in lms_dir.glob("*.lms"):
        old.unlink()

    data_utils_dir = UDH_DIR / "data_utils"
    if str(data_utils_dir) not in sys.path:
        sys.path.insert(0, str(data_utils_dir))

    cwd = os.getcwd()
    os.chdir(str(data_utils_dir))
    bboxes: List[Tuple[int, int, int, int]] = []
    try:
        ensure_onnxruntime_cuda_paths()
        # 上游 get_landmark.py 第 2 行误写 `from os import wait3`（仅 Unix 存在），Windows 需兼容垫片
        if os.name == "nt" and not hasattr(os, "wait3"):
            os.wait3 = lambda *a, **kw: (0, 0, None)  # type: ignore[attr-defined]
        from get_landmark import Landmark  # type: ignore

        detector = Landmark()
        last_valid: Optional[np.ndarray] = None
        missed = 0
        for i in range(n_frames):
            img_p = full_img_dir / f"{i}.jpg"
            img = cv2.imread(str(img_p))
            if img is None:
                raise RuntimeError(f"读取帧失败: {img_p}")
            h, w = img.shape[:2]
            try:
                pts, x1, y1 = detector.detect(str(img_p))
                abs_pts = np.stack([pts[:, 0] + x1, pts[:, 1] + y1], axis=1).astype(np.int32)
                last_valid = abs_pts
            except Exception:
                if last_valid is None:
                    raise RuntimeError(f"第 {i} 帧未检测到人脸，请确保视频全程正脸清晰无遮挡")
                abs_pts = last_valid
                missed += 1

            lms_out = lms_dir / f"{i}.lms"
            with lms_out.open("w", encoding="utf-8") as f:
                for p in abs_pts:
                    f.write(f"{int(p[0])} {int(p[1])}\n")

            xmin, ymin, xmax, ymax = compute_face_bbox(abs_pts)
            xmin = max(0, min(w - 16, xmin))
            ymin = max(0, min(h - 16, ymin))
            xmax = max(xmin + 16, min(w, xmax))
            ymax = max(ymin + 16, min(h, ymax))
            bboxes.append((int(xmin), int(ymin), int(xmax), int(ymax)))

        if missed:
            print(f"[WARN] 共 {missed}/{n_frames} 帧人脸漏检，已自动用邻帧关键点平滑补齐", flush=True)
    finally:
        os.chdir(cwd)
    return bboxes


def extract_wenet_features_from_speech(speech_16k: np.ndarray, encoder_onnx: Path) -> np.ndarray:
    """使用 kaldi_native_fbank + WeNet encoder.onnx 提取 20fps 音频特征 (T, 16, 512)。"""
    import kaldi_native_fbank as knf
    import onnxruntime as ort

    if speech_16k.ndim == 2:
        speech_16k = speech_16k[:, 0]
    pcm = np.clip(speech_16k.astype(np.float32) * 32767.0, -32768.0, 32767.0).astype(np.float32)
    # 与上游 wenet_infer.py 一致：前置 32*160 静音，后置 35*160 静音
    pcm = np.concatenate([np.zeros(32 * 160, dtype=np.float32), pcm, np.zeros(35 * 160, dtype=np.float32)])

    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0.0
    opts.frame_opts.snip_edges = False
    opts.mel_opts.num_bins = 80
    opts.mel_opts.debug_mel = False
    fbank = knf.OnlineFbank(opts)
    fbank.accept_waveform(16000, pcm.tolist())
    n_mel = fbank.num_frames_ready
    mel = np.array([fbank.get_frame(i) for i in range(n_mel)], dtype=np.float32)  # [T_mel, 80]

    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    sess = ort.InferenceSession(str(encoder_onnx), providers=providers)
    offset = np.ones((1,), dtype=np.int64) * 100
    att_cache = np.zeros([3, 8, 16, 128], dtype=np.float32)
    cnn_cache = np.zeros([3, 1, 512, 14], dtype=np.float32)

    frames_stride = 67
    aud_npy = []
    start = 0
    while start < n_mel:
        end = start + frames_stride
        feat = mel[start:end]
        if feat.shape[0] < frames_stride:
            pad = np.zeros((frames_stride - feat.shape[0], 80), dtype=np.float32)
            feat = np.concatenate([feat, pad], axis=0)
        chunk = feat[None, None, :, :]  # [1, 1, 67, 80]
        outs = sess.run(
            None,
            {"chunk": chunk, "offset": offset, "att_cache": att_cache, "cnn_cache": cnn_cache},
        )
        aud_npy.append(outs[0][0])  # [16, 512]
        start += 5
        if end >= n_mel:
            break
    if not aud_npy:
        return np.zeros((1, 16, 512), dtype=np.float32)
    return np.asarray(aud_npy, dtype=np.float32)


def extract_hubert_features_from_speech(speech_16k: np.ndarray, model_source: str, device: str = "cuda") -> np.ndarray:
    """使用 HuBERT Large 提取 25fps 音频特征 (T, 2, 1024)。"""
    from transformers import HubertModel, Wav2Vec2Processor

    if speech_16k.ndim == 2:
        speech_16k = speech_16k[:, 0]
    processor = Wav2Vec2Processor.from_pretrained(model_source)
    model = HubertModel.from_pretrained(model_source)
    use_fp16 = device.startswith("cuda") and torch.cuda.is_available()
    model = model.to(device)
    if use_fp16:
        model = model.half()
    model.eval()

    with torch.no_grad():
        input_values_all = processor(speech_16k.astype(np.float32), return_tensors="pt", sampling_rate=16000).input_values
        input_values_all = input_values_all.to(device)
        if use_fp16:
            input_values_all = input_values_all.half()

        kernel, stride = 400, 320
        clip_length = stride * 1000
        num_iter = input_values_all.shape[1] // clip_length
        expected_t = max(1, (input_values_all.shape[1] - (kernel - stride)) // stride)
        res_lst = []
        for i in range(num_iter):
            start_idx = 0 if i == 0 else clip_length * i
            end_idx = start_idx + (clip_length - stride + kernel)
            hidden = model(input_values_all[:, start_idx:end_idx]).last_hidden_state
            res_lst.append(hidden[0].float().cpu())
        tail = input_values_all[:, clip_length * num_iter:] if num_iter > 0 else input_values_all
        if tail.shape[1] >= kernel:
            hidden = model(tail).last_hidden_state
            res_lst.append(hidden[0].float().cpu())
        if not res_lst:
            return np.zeros((1, 2, 1024), dtype=np.float32)
        ret = torch.cat(res_lst, dim=0)
        if ret.shape[0] < expected_t:
            ret = torch.nn.functional.pad(ret, (0, 0, 0, expected_t - ret.shape[0]))
        else:
            ret = ret[:expected_t]
        if ret.shape[0] % 2 == 1:
            ret = torch.cat([ret, torch.zeros_like(ret[:1])], dim=0)
        return ret.reshape(-1, 2, 1024).numpy().astype(np.float32)


def _export_unet_onnx(ckpt_path: Path, onnx_path: Path, asr: str, trusted: bool = False) -> None:
    import onnx
    import onnxruntime as ort

    net = Model(6, asr).eval()
    # trusted=True 仅限本机刚训练出的权重或导入时已严格校验过的权重；其余一律 weights_only=True
    ckpt = safe_torch_load(ckpt_path, map_location="cpu", trusted=trusted)
    state_dict = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
    net.load_state_dict(state_dict)

    img = torch.zeros([1, 6, 160, 160], dtype=torch.float32)
    audio = torch.zeros([1, 128, 16, 32], dtype=torch.float32) if asr == "wenet" else torch.zeros([1, 16, 32, 32], dtype=torch.float32)

    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch_out = net(img, audio)
        export_kw = {
            "input_names": ["input", "audio"],
            "output_names": ["output"],
            "opset_version": 11,
            "export_params": True,
            # 动态 batch 维：引擎积压时可一次推理多帧追进度（旧模型无此维时引擎自动逐帧）
            "dynamic_axes": {"input": {0: "batch"}, "audio": {0: "batch"}, "output": {0: "batch"}},
        }
        try:
            # PyTorch 2.9 兼容：优先使用 legacy TorchScript 导出确保 opset 11 静态图无 dynamo 报错
            torch.onnx.export(net, (img, audio), str(onnx_path), dynamo=False, **export_kw)
        except TypeError:
            torch.onnx.export(net, (img, audio), str(onnx_path), **export_kw)

    onnx_model = onnx.load(str(onnx_path))
    onnx.checker.check_model(onnx_model)
    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    sess = ort.InferenceSession(str(onnx_path), providers=providers)
    ort_out = sess.run(None, {sess.get_inputs()[0].name: img.numpy(), sess.get_inputs()[1].name: audio.numpy()})[0]
    np.testing.assert_allclose(torch_out[0].numpy(), ort_out[0], rtol=1e-3, atol=1e-4)
    print(f"[OK] ONNX 导出并校验通过: {onnx_path} (providers={sess.get_providers()})", flush=True)


def run_pipeline(
    avatar_id: str,
    name: str,
    asr: str = "hubert",
    video_path: Optional[Path] = None,
    silent_idle_video: Optional[Path] = None,
    checkpoint: Optional[Path] = None,
    epochs: int = 100,
    batch_size: int = 16,
    lr: float = 1e-3,
    save_every: int = 10,
    resume: Optional[Path] = None,
    export_onnx: bool = True,
    export_only: bool = False,
    base_avatar: str = "",
    progress_cb: Optional[Callable[[int, str], None]] = None,
    num_workers: int = 2,
    val_ratio: float = 0.05,
    amp: bool = True,
) -> Path:
    aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (avatar_id or "").strip())
    if not aid:
        raise ValueError("avatar_id 不能为空且仅支持英文、数字、下划线和短横线")
    base_aid = re.sub(r"[^a-zA-Z0-9_\-]", "", (base_avatar or "").strip())
    if base_aid and base_aid == aid:
        raise ValueError("目标换装 ID 不能与基础主播 ID 相同，请使用新英文 ID（如 host1_suit）")
    base_dir = (AVATARS_DIR / base_aid) if base_aid else None
    if base_dir is not None:
        if not base_dir.is_dir():
            raise FileNotFoundError(f"找不到基础主播目录: {base_dir}")
        base_meta = json.loads((base_dir / "meta.json").read_text(encoding="utf-8")) if (base_dir / "meta.json").is_file() else {}
        if base_meta.get("asr") in FPS_BY_ASR:
            asr = str(base_meta["asr"])
    if asr not in FPS_BY_ASR:
        raise ValueError(f"不支持的 ASR 路线: {asr}")

    fps = FPS_BY_ASR[asr]
    av_dir = AVATARS_DIR / aid
    dataset_dir = av_dir / "dataset"
    full_img_dir = dataset_dir / "full_body_img"
    lms_dir = dataset_dir / "landmarks"
    ckpt_dir = av_dir / "checkpoints"
    av_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir.mkdir(parents=True, exist_ok=True)

    pth_out = av_dir / "ultralight.pth"
    onnx_out = av_dir / "unet.onnx"

    if export_only:
        if not pth_out.is_file():
            raise FileNotFoundError(f"找不到待导出的权重文件: {pth_out}")
        meta_old = json.loads((av_dir / "meta.json").read_text(encoding="utf-8")) if (av_dir / "meta.json").is_file() else {}
        use_asr = str(meta_old.get("asr") or asr).lower()
        use_fps = FPS_BY_ASR.get(use_asr, fps)
        _emit(progress_cb, 40, f"导出并校验 unet.onnx ({use_asr})...")
        _export_unet_onnx(pth_out, onnx_out, use_asr, trusted=bool(meta_old.get("pth_trusted")))
        dst_full = av_dir / "full_body_img"
        exm = cv2.imread(str(dst_full / "0.jpg"))
        ih, iw = exm.shape[:2] if exm is not None else (int(meta_old.get("height", 0)), int(meta_old.get("width", 0)))
        n_frames = len(list(dst_full.glob("*.jpg"))) if dst_full.is_dir() else int(meta_old.get("frames", 0))
        meta = {
            **meta_old,
            "avatar_id": aid,
            "name": (name or meta_old.get("name") or aid).strip(),
            "asr": use_asr,
            "fps": use_fps,
            "frames": n_frames,
            "width": int(iw),
            "height": int(ih),
            "has_onnx": onnx_out.is_file(),
            "has_pth": pth_out.is_file(),
            "has_idle_loop": (av_dir / "img_inference").is_dir(),
            "updated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        meta_tmp = av_dir / "meta.json.tmp"
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(meta_tmp, av_dir / "meta.json")
        _emit(progress_cb, 100, f"Ultralight [{aid}] ONNX 导出完成 -> {onnx_out}")
        return av_dir

    # 0. 检查基础模型
    encoder_onnx = UDH_DIR / "data_utils" / "encoder.onnx"
    hubert_local = MODELS_DIR / "hubert-large-ls960-ft"
    if asr == "wenet" and not encoder_onnx.is_file():
        raise FileNotFoundError(f"缺少 WeNet encoder.onnx: {encoder_onnx}，请先运行 02_download_models.bat")
    if asr == "hubert" and not (hubert_local / "config.json").is_file():
        print("[WARN] 本地 models/hubert-large-ls960-ft 未找到，将尝试在线加载 facebook/hubert-large-ls960-ft", flush=True)

    # 若指定了 base_avatar 且 epochs <= 0，视为「同主播极速零训练换装」；epochs > 0 则以基础主播 .pth 为起点微调
    if base_dir is not None and epochs <= 0:
        b_onnx = base_dir / "unet.onnx"
        b_pth = base_dir / "ultralight.pth"
        if not (b_onnx.is_file() or b_pth.is_file()):
            raise FileNotFoundError(f"基础主播 [{base_aid}] 尚未训练完成，缺少 unet.onnx 与 ultralight.pth")
        need_train = False
    elif base_dir is not None and checkpoint is None:
        b_pth = base_dir / "ultralight.pth"
        if b_pth.is_file() and resume is None:
            resume = b_pth
        need_train = True
    else:
        need_train = checkpoint is None
    if video_path is not None:
        if not video_path.is_file():
            raise FileNotFoundError(f"找不到输入视频: {video_path}")
        _emit(progress_cb, 5, f"检查并标准化视频为 {fps} fps ({asr})...")
        has_audio, _, _, _ = _probe_video(video_path)
        if need_train and not has_audio:
            raise ValueError(
                "该视频没有音轨（例如 Wav2Lip 专用的无声底片）。\n"
                "Ultralight 训练必须使用【真人开口说话、音画同步、含清晰人声】的 1~5 分钟视频！\n"
                "若只想用外部已训练好的 .pth/.onnx 权重打包形象，请传入 --checkpoint。"
            )
        norm_vid = dataset_dir / f"source_{fps}fps.mp4"
        _transcode_video(video_path, norm_vid, fps=fps, keep_audio=has_audio)

        _emit(progress_cb, 12, "抽取视频帧与 16kHz 音频...")
        wav_path = (dataset_dir / "aud.wav") if has_audio else None
        n_frames = _extract_frames_and_audio(norm_vid, full_img_dir, wav_path)

        _emit(progress_cb, 22, f"检测人脸与 98 点关键点（共 {n_frames} 帧）...")
        bboxes = _detect_landmarks_resilient(full_img_dir, lms_dir, n_frames)

        if need_train and wav_path is not None:
            _emit(progress_cb, 35, f"提取 {asr} 音频特征...")
            speech, sr = sf.read(str(wav_path))
            if sr != 16000:
                import librosa
                speech = librosa.resample(speech.astype(np.float32), orig_sr=sr, target_sr=16000)
            if asr == "wenet":
                feats = extract_wenet_features_from_speech(speech, encoder_onnx)
                np.save(str(dataset_dir / "aud_wenet.npy"), feats)
            else:
                src = str(hubert_local) if (hubert_local / "config.json").is_file() else "facebook/hubert-large-ls960-ft"
                feats = extract_hubert_features_from_speech(speech, src)
                np.save(str(dataset_dir / "aud_hu.npy"), feats)
    else:
        if not full_img_dir.is_dir() or not lms_dir.is_dir():
            raise ValueError("未提供 --video 时，必须已存在 dataset/full_body_img 与 dataset/landmarks")
        n_frames = len(list(full_img_dir.glob("*.jpg")))
        bboxes = [tuple(int(x) for x in compute_face_bbox(read_landmarks(str(lms_dir / f"{i}.lms")))) for i in range(n_frames)]

    # 保存镜像/推理目录（同步放置到 avatars/<id>/full_body_img & landmarks & bboxes.json，供引擎快速加载）
    dst_full = av_dir / "full_body_img"
    dst_lms = av_dir / "landmarks"
    if dst_full.exists():
        shutil.rmtree(dst_full)
    if dst_lms.exists():
        shutil.rmtree(dst_lms)
    shutil.copytree(full_img_dir, dst_full)
    shutil.copytree(lms_dir, dst_lms)
    (av_dir / "bboxes.json").write_text(json.dumps(bboxes), encoding="utf-8")

    # 可选：单独静音待机动作素材（上游 README 推荐的前 20 秒闭嘴呼吸视频）
    if silent_idle_video is not None and silent_idle_video.is_file():
        _emit(progress_cb, 42, "处理静音待机视频 (img_inference)...")
        idle_mp4 = dataset_dir / f"idle_{fps}fps.mp4"
        _transcode_video(silent_idle_video, idle_mp4, fps=fps, keep_audio=False)
        idle_imgs = av_dir / "img_inference"
        idle_lms = av_dir / "lms_inference"
        n_idle = _extract_frames_and_audio(idle_mp4, idle_imgs, None)
        idle_bboxes = _detect_landmarks_resilient(idle_imgs, idle_lms, n_idle)
        (av_dir / "idle_bboxes.json").write_text(json.dumps(idle_bboxes), encoding="utf-8")

    # 训练、复用基础主播模型换装，或导入外部权重
    pth_out = av_dir / "ultralight.pth"
    onnx_out = av_dir / "unet.onnx"
    pth_trusted = False
    if base_dir is not None and epochs <= 0 and checkpoint is None:
        _emit(progress_cb, 75, f"复用基础主播 [{base_aid}] 已训练嘴型模型完成换装...")
        b_onnx = base_dir / "unet.onnx"
        b_pth = base_dir / "ultralight.pth"
        try:
            pth_trusted = bool(json.loads((base_dir / "meta.json").read_text(encoding="utf-8")).get("pth_trusted"))
        except Exception:
            pth_trusted = False
        if b_pth.is_file():
            shutil.copy2(b_pth, pth_out)
        if b_onnx.is_file():
            shutil.copy2(b_onnx, onnx_out)
        elif pth_out.is_file() and export_onnx:
            _export_unet_onnx(pth_out, onnx_out, asr, trusted=pth_trusted)
    elif checkpoint is not None:
        if not checkpoint.is_file():
            raise FileNotFoundError(f"找不到指定权重文件: {checkpoint}")
        _emit(progress_cb, 75, f"导入外部权重 {checkpoint.name}...")
        if checkpoint.suffix.lower() == ".onnx":
            shutil.copy2(checkpoint, onnx_out)
        else:
            # 外部权重必须能以纯参数方式读取（weights_only=True），拒绝夹带可执行代码的恶意 .pth
            safe_torch_load(checkpoint, map_location="cpu", trusted=False)
            shutil.copy2(checkpoint, pth_out)
            pth_trusted = True
    else:
        _emit(progress_cb, 45, f"开始训练 Ultralight UNet ({asr}, epochs={epochs}, batch={batch_size})...")
        from datasetsss import MyDataset  # type: ignore
        from train import PerceptualLoss, compute_total_loss, resume_if_any, save_checkpoint  # type: ignore
        from torch import optim
        from torch.utils.data import DataLoader

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        dataset = MyDataset(str(dataset_dir), asr)
        if len(dataset) < 2:
            raise RuntimeError("有效训练样本不足 2 帧")

        # 留出少量帧做验证集：best.pth 按验证 loss 挑选，避免挑到过拟合最重的那份
        val_set = None
        train_set = dataset
        n_val = int(len(dataset) * max(0.0, float(val_ratio)))
        if n_val >= 8 and len(dataset) - n_val >= 32:
            gen = torch.Generator().manual_seed(42)
            train_set, val_set = torch.utils.data.random_split(dataset, [len(dataset) - n_val, n_val], generator=gen)
        use_cuda = device.type == "cuda"
        bs = max(1, min(batch_size, len(train_set)))

        def _make_loader(ds, shuffle: bool, workers: int):
            kw = {"batch_size": bs, "shuffle": shuffle, "drop_last": False, "num_workers": workers,
                  "pin_memory": use_cuda}
            if workers > 0:
                kw["persistent_workers"] = True
            return DataLoader(ds, **kw)

        workers = max(0, int(num_workers))
        loader = _make_loader(train_set, True, workers)
        if workers > 0:
            try:
                next(iter(loader))   # Windows 多进程读数据失败时自动退回单进程
            except Exception as exc:
                _emit(progress_cb, 45, f"多进程读数据不可用（{exc}），退回单进程")
                workers = 0
                loader = _make_loader(train_set, True, 0)
        val_loader = _make_loader(val_set, False, 0) if val_set is not None else None

        net = Model(6, asr).to(device)
        optimizer = optim.Adam(net.parameters(), lr=lr)
        pixel_criterion = torch.nn.L1Loss()
        perceptual_loss = PerceptualLoss(torch.nn.MSELoss(), device=device)
        start_epoch = resume_if_any(str(resume) if resume else "", net, optimizer, device)
        # RTX 2080(Turing) 支持 FP16 Tensor Core：混合精度训练提速并省显存（BF16 不支持，故用 float16）
        amp_on = bool(amp) and use_cuda
        scaler = torch.amp.GradScaler("cuda", enabled=amp_on)
        _emit(progress_cb, 46, f"训练集 {len(train_set)} 帧 / 验证集 {len(val_set) if val_set is not None else 0} 帧，"
                               f"batch={bs}，读数据进程={workers}，混合精度={'开' if amp_on else '关'}")

        best_loss = float("inf")
        for epoch in range(start_epoch, epochs):
            net.train()
            ep_loss = 0.0
            steps = 0
            for imgs, labels, audio_feat in loader:
                imgs = imgs.to(device, non_blocking=True)
                labels = labels.to(device, non_blocking=True)
                audio_feat = audio_feat.to(device, non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=amp_on):
                    preds = net(imgs, audio_feat)
                    loss = compute_total_loss(preds, labels, pixel_criterion, perceptual_loss)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                ep_loss += float(loss.item())
                steps += 1
            avg_loss = ep_loss / max(1, steps)
            score = avg_loss
            if val_loader is not None:
                net.eval()
                v_loss, v_steps = 0.0, 0
                with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16, enabled=amp_on):
                    for imgs, labels, audio_feat in val_loader:
                        preds = net(imgs.to(device), audio_feat.to(device))
                        v_loss += float(compute_total_loss(preds, labels.to(device), pixel_criterion, perceptual_loss).item())
                        v_steps += 1
                score = v_loss / max(1, v_steps)
            pct = 45 + int(((epoch + 1) / max(1, epochs)) * 43)
            _emit(progress_cb, min(88, pct), f"Epoch {epoch + 1}/{epochs}  loss={avg_loss:.5f}"
                                             + (f"  val={score:.5f}" if val_loader is not None else ""))
            if score < best_loss:
                best_loss = score
                save_checkpoint(str(ckpt_dir / "best.pth"), net, optimizer, epoch)
            if (epoch + 1) % max(1, save_every) == 0 or epoch == epochs - 1:
                save_checkpoint(str(ckpt_dir / f"{epoch}.pth"), net, optimizer, epoch)
                save_checkpoint(str(ckpt_dir / "last.pth"), net, optimizer, epoch)

        best_p = ckpt_dir / "best.pth"
        src_p = best_p if best_p.is_file() else (ckpt_dir / "last.pth")
        shutil.copy2(src_p, pth_out)
        pth_trusted = True   # 本机刚训练出的权重

    if export_onnx and pth_out.is_file() and not (checkpoint is not None and checkpoint.suffix.lower() == ".onnx"):
        _emit(progress_cb, 92, "导出并校验 unet.onnx...")
        _export_unet_onnx(pth_out, onnx_out, asr, trusted=pth_trusted)

    exm = cv2.imread(str(dst_full / "0.jpg"))
    ih, iw = exm.shape[:2] if exm is not None else (0, 0)
    meta = {
        "avatar_id": aid,
        "name": (name or aid).strip(),
        "asr": asr,
        "fps": fps,
        "frames": n_frames,
        "width": int(iw),
        "height": int(ih),
        "has_onnx": onnx_out.is_file(),
        "has_pth": pth_out.is_file(),
        "pth_trusted": bool(pth_trusted and pth_out.is_file()),
        "has_idle_loop": (av_dir / "img_inference").is_dir(),
        "base_avatar": base_aid,
        "created_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    meta_tmp = av_dir / "meta.json.tmp"
    meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(meta_tmp, av_dir / "meta.json")
    _emit(progress_cb, 100, f"Ultralight 形象 [{aid}] ({asr}/{fps}fps) 构建完成 -> {av_dir}")
    return av_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Ultralight-Digital-Human 一键预处理、训练与 ONNX 打包")
    ap.add_argument("--avatar_id", required=True, help="形象英文 ID，如 ul_host1")
    ap.add_argument("--name", default="", help="主播中文名")
    ap.add_argument("--video", default="", help="说话训练视频路径（MP4/MOV/MKV，需含清晰人声）")
    ap.add_argument("--idle_video", default="", help="可选：20秒闭嘴呼吸待机视频")
    ap.add_argument("--asr", default="hubert", choices=["hubert", "wenet"], help="音频特征路线")
    ap.add_argument("--epochs", type=int, default=100, help="训练轮数（建议正式 100~200，快速测试 10~20，换装复用填 0）")
    ap.add_argument("--batch_size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--checkpoint", default="", help="已有 .pth 或 .onnx 权重路径（传入则跳过训练直接打包）")
    ap.add_argument("--resume", default="", help="断点续训权重路径")
    ap.add_argument("--base_avatar", default="", help="已有主播英文 ID（用于同主播零训练换装或微调服饰）")
    ap.add_argument("--no-onnx", action="store_true", help="不导出 ONNX")
    ap.add_argument("--export_only", action="store_true", help="仅将已有 ultralight.pth 重导为 unet.onnx")
    ap.add_argument("--num_workers", type=int, default=2, help="读数据进程数（喂饱显卡；Windows 出错会自动退回 0）")
    ap.add_argument("--val_ratio", type=float, default=0.05, help="留作验证集的帧比例（按验证 loss 挑 best.pth）")
    ap.add_argument("--no-amp", action="store_true", help="关闭 FP16 混合精度训练")
    args = ap.parse_args()

    run_pipeline(
        avatar_id=args.avatar_id,
        name=args.name or args.avatar_id,
        asr=args.asr,
        video_path=Path(args.video) if args.video else None,
        silent_idle_video=Path(args.idle_video) if args.idle_video else None,
        checkpoint=Path(args.checkpoint) if args.checkpoint else None,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        resume=Path(args.resume) if args.resume else None,
        export_onnx=not args.no_onnx,
        export_only=bool(args.export_only),
        base_avatar=args.base_avatar,
        num_workers=args.num_workers,
        val_ratio=args.val_ratio,
        amp=not args.no_amp,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
