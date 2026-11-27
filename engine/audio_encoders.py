# -*- coding: utf-8 -*-
"""
audio_encoders.py —— Ultralight-Digital-Human 实时共享音频特征提取器
  - WeNetEncoder  : 20 fps (50ms/帧), kaldi_native_fbank + encoder.onnx
  - HuBERTEncoder : 25 fps (40ms/帧), facebook/hubert-large-ls960-ft (CUDA FP16)
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import torch

_DLL_HANDLES = []


def ensure_onnxruntime_cuda_paths() -> None:
    """将 PyTorch 自带 CUDA 12 / cuDNN 9 DLL 目录加入搜索路径（Windows GPU ONNX 推理必需）。"""
    if os.name != "nt":
        return
    torch_lib = Path(torch.__file__).resolve().parent / "lib"
    if not torch_lib.is_dir():
        return
    lib_str = str(torch_lib)
    cur_path = os.environ.get("PATH", "")
    if lib_str.lower() not in cur_path.lower():
        os.environ["PATH"] = lib_str + os.pathsep + cur_path
    add_dll = getattr(os, "add_dll_directory", None)
    if add_dll and lib_str not in {str(h) for h in _DLL_HANDLES}:
        try:
            _DLL_HANDLES.append(add_dll(lib_str))
        except OSError:
            pass


ensure_onnxruntime_cuda_paths()


def make_ort_session(model_path: Path, gpu_mem_mb: int = 0, intra_threads: int = 2):
    """创建限显存的 ONNX Runtime 会话（RTX 2080：显存池按需增长且设上限，防止挤爆 OBS NVENC）。"""
    import onnxruntime as ort

    ensure_onnxruntime_cuda_paths()
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = max(1, int(intra_threads))
    providers: list = []
    if "CUDAExecutionProvider" in ort.get_available_providers():
        cuda_opts = {
            "device_id": 0,
            "arena_extend_strategy": "kSameAsRequested",
            "cudnn_conv_algo_search": "HEURISTIC",
            "do_copy_in_default_stream": True,
        }
        if int(gpu_mem_mb or 0) > 0:
            cuda_opts["gpu_mem_limit"] = int(gpu_mem_mb) * 1024 * 1024
        providers.append(("CUDAExecutionProvider", cuda_opts))
    providers.append("CPUExecutionProvider")
    try:
        return ort.InferenceSession(str(model_path), sess_options=so, providers=providers)
    except Exception:
        # 旧版 onnxruntime 不认识某个 provider 选项时退回默认参数
        plain = [p[0] if isinstance(p, tuple) else p for p in providers]
        return ort.InferenceSession(str(model_path), sess_options=so, providers=plain)


def safe_torch_load(path: Path, map_location="cpu", trusted: bool = False):
    """安全加载 .pth：先用 weights_only=True（只读纯参数，拒绝夹带可执行代码的恶意权重）。

    trusted=True 仅用于本项目自己训练产出、或导入时已通过严格校验的权重；
    外部来源的权重若无法以纯参数方式读取，需用户确认来源可信后设置环境变量 UL_ALLOW_UNSAFE_PTH=1。
    """
    try:
        return torch.load(str(path), map_location=map_location, weights_only=True)
    except Exception as exc:
        if trusted or os.environ.get("UL_ALLOW_UNSAFE_PTH") == "1":
            return torch.load(str(path), map_location=map_location, weights_only=False)
        raise RuntimeError(
            f"权重文件 {Path(path).name} 不是纯参数文件（可能夹带可执行代码），已拒绝加载。"
            f"确认来源可信后可设置环境变量 UL_ALLOW_UNSAFE_PTH=1 再试。原始错误: {exc}"
        ) from exc


class WeNetEncoder:
    """基于 WeNet encoder.onnx 的 20fps 音频编码器。"""

    def __init__(self, encoder_onnx: Path, gpu_mem_mb: int = 768) -> None:
        import kaldi_native_fbank as knf

        ensure_onnxruntime_cuda_paths()

        if not encoder_onnx.is_file():
            raise FileNotFoundError(f"缺少 WeNet encoder.onnx: {encoder_onnx}")

        self._knf = knf
        opts = knf.FbankOptions()
        opts.frame_opts.dither = 0.0
        opts.frame_opts.snip_edges = False
        opts.mel_opts.num_bins = 80
        opts.mel_opts.debug_mel = False
        self._fbank_opts = opts

        self._sess = make_ort_session(encoder_onnx, gpu_mem_mb=gpu_mem_mb)
        self._offset = np.ones((1,), dtype=np.int64) * 100
        self._att_cache = np.zeros([3, 8, 16, 128], dtype=np.float32)
        self._cnn_cache = np.zeros([3, 1, 512, 14], dtype=np.float32)
        self._lock = threading.Lock()

    def extract(self, speech_16k: np.ndarray) -> np.ndarray:
        """输入 [-1, 1] float32 16kHz 单声道音频，输出 [T_20fps, 16, 512] float32。"""
        if speech_16k.ndim == 2:
            speech_16k = speech_16k[:, 0]
        pcm = np.clip(speech_16k.astype(np.float32) * 32767.0, -32768.0, 32767.0).astype(np.float32)
        pcm = np.concatenate([np.zeros(32 * 160, dtype=np.float32), pcm, np.zeros(35 * 160, dtype=np.float32)])

        fbank = self._knf.OnlineFbank(self._fbank_opts)
        fbank.accept_waveform(16000, pcm.tolist())
        n_mel = fbank.num_frames_ready
        if n_mel == 0:
            return np.zeros((1, 16, 512), dtype=np.float32)
        mel = np.array([fbank.get_frame(i) for i in range(n_mel)], dtype=np.float32)

        frames_stride = 67
        chunks = []
        with self._lock:
            start = 0
            while start < n_mel:
                end = start + frames_stride
                feat = mel[start:end]
                if feat.shape[0] < frames_stride:
                    feat = np.concatenate([feat, np.zeros((frames_stride - feat.shape[0], 80), dtype=np.float32)], axis=0)
                outs = self._sess.run(
                    None,
                    {
                        "chunk": feat[None, None, :, :],
                        "offset": self._offset,
                        "att_cache": self._att_cache,
                        "cnn_cache": self._cnn_cache,
                    },
                )
                chunks.append(outs[0][0])
                start += 5
                if end >= n_mel:
                    break
        if not chunks:
            return np.zeros((1, 16, 512), dtype=np.float32)
        return np.asarray(chunks, dtype=np.float32)


class HuBERTEncoder:
    """基于 HuBERT Large 的 25fps 音频编码器（RTX 2080 使用 FP16 省显存）。"""

    def __init__(self, model_dir: Path, device: Optional[str] = None) -> None:
        from transformers import HubertModel, Wav2Vec2Processor

        source = str(model_dir) if (model_dir / "config.json").is_file() else "facebook/hubert-large-ls960-ft"
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.use_fp16 = self.device.startswith("cuda") and torch.cuda.is_available()
        self.processor = Wav2Vec2Processor.from_pretrained(source)
        if self.use_fp16:
            try:
                self.model = HubertModel.from_pretrained(
                    source, dtype=torch.float16, low_cpu_mem_usage=True
                ).to(self.device)
            except TypeError:
                self.model = HubertModel.from_pretrained(source).to(self.device)
            self.model = self.model.half()
        else:
            self.model = HubertModel.from_pretrained(source).to(self.device)
        self.model.eval()
        self._lock = threading.Lock()

    @torch.no_grad()
    def extract(self, speech_16k: np.ndarray) -> np.ndarray:
        """输入 [-1, 1] float32 16kHz 单声道音频，输出 [T_25fps, 2, 1024] float32。"""
        if speech_16k.ndim == 2:
            speech_16k = speech_16k[:, 0]
        if speech_16k.shape[0] < 400:
            return np.zeros((1, 2, 1024), dtype=np.float32)

        with self._lock:
            input_values_all = self.processor(
                speech_16k.astype(np.float32), return_tensors="pt", sampling_rate=16000
            ).input_values.to(self.device)
            if self.use_fp16:
                input_values_all = input_values_all.half()

            kernel, stride = 400, 320
            clip_length = stride * 1000
            num_iter = input_values_all.shape[1] // clip_length
            expected_t = max(1, (input_values_all.shape[1] - (kernel - stride)) // stride)
            res_lst = []
            for i in range(num_iter):
                start_idx = 0 if i == 0 else clip_length * i
                end_idx = start_idx + (clip_length - stride + kernel)
                hidden = self.model(input_values_all[:, start_idx:end_idx]).last_hidden_state
                res_lst.append(hidden[0].float().cpu())
            tail = input_values_all[:, clip_length * num_iter:] if num_iter > 0 else input_values_all
            if tail.shape[1] >= kernel:
                hidden = self.model(tail).last_hidden_state
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
