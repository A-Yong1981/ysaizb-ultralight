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


class WeNetEncoder:
    """基于 WeNet encoder.onnx 的 20fps 音频编码器。"""

    def __init__(self, encoder_onnx: Path) -> None:
        import kaldi_native_fbank as knf
        import onnxruntime as ort

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

        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if "CUDAExecutionProvider" in ort.get_available_providers()
            else ["CPUExecutionProvider"]
        )
        self._sess = ort.InferenceSession(str(encoder_onnx), providers=providers)
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
