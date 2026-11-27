# -*- coding: utf-8 -*-
"""
download_models.py —— Ultralight-Digital-Human 独立底座模型下载与校验脚本
支持：
  1. face_detect (内置于 Ultralight-Digital-Human/data_utils/: scrfd_2.5g_kps.onnx + checkpoint_epoch_335.pth.tar)
  2. wenet  (20fps 超轻量 ONNX 路线): 下载 encoder.onnx -> Ultralight-Digital-Human/data_utils/encoder.onnx
  3. hubert (25fps 高精度路线): 下载 facebook/hubert-large-ls960-ft -> models/hubert-large-ls960-ft/
  4. vgg19  (训练感知损失): 预缓存 torchvision vgg19-dcbb9e9d.pth

用法：
  python scripts/download_models.py --mode all
  python scripts/download_models.py --mode wenet
  python scripts/download_models.py --mode hubert
  python scripts/download_models.py --mode check
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UDH_DIR = ROOT / "Ultralight-Digital-Human"
MODELS_DIR = ROOT / "models"
WENET_ENCODER_PATH = UDH_DIR / "data_utils" / "encoder.onnx"
HUBERT_DIR = MODELS_DIR / "hubert-large-ls960-ft"
SCRFD_PATH = UDH_DIR / "data_utils" / "scrfd_2.5g_kps.onnx"
PFLD_PATH = UDH_DIR / "data_utils" / "checkpoint_epoch_335.pth.tar"

WENET_GDRIVE_ID = "1e4Z9zS053JEWl6Mj3W9Lbc9GDtzHIg6b"


def check_local_assets() -> dict:
    status = {
        "scrfd": SCRFD_PATH.is_file() and SCRFD_PATH.stat().st_size > 1_000_000,
        "pfld": PFLD_PATH.is_file() and PFLD_PATH.stat().st_size > 500_000,
        "wenet_encoder": WENET_ENCODER_PATH.is_file() and WENET_ENCODER_PATH.stat().st_size > 10_000_000,
        "hubert": (HUBERT_DIR / "config.json").is_file() and (
            (HUBERT_DIR / "pytorch_model.bin").is_file()
            or (HUBERT_DIR / "model.safetensors").is_file()
        ),
    }
    return status


def _download_with_progress(url: str, dst: Path, timeout: float = 60.0) -> None:
    import httpx

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            if "text/html" in ctype:
                html = r.read().decode("utf-8", errors="replace")
                # Parse Google Drive confirmation form / confirm token
                m_act = re.search(r'action="([^"]+)"', html)
                m_id = re.search(r'name="id"\s+value="([^"]+)"', html)
                m_conf = re.search(r'name="confirm"\s+value="([^"]+)"', html)
                m_uuid = re.search(r'name="uuid"\s+value="([^"]+)"', html)
                if m_act and m_conf:
                    action = m_act.group(1)
                    fid = m_id.group(1) if m_id else WENET_GDRIVE_ID
                    conf = m_conf.group(1)
                    uuid_q = f"&uuid={m_uuid.group(1)}" if m_uuid else ""
                    confirm_url = f"{action}?id={fid}&export=download&confirm={conf}{uuid_q}"
                    with client.stream("GET", confirm_url, headers={"User-Agent": "Mozilla/5.0"}) as r2:
                        r2.raise_for_status()
                        ctype2 = (r2.headers.get("content-type") or "").lower()
                        if "text/html" in ctype2:
                            raise RuntimeError("Google Drive 返回 HTML 确认页而非二进制文件，请手动下载或检查网络代理")
                        _stream_to_file(r2, tmp)
                else:
                    raise RuntimeError("下载返回 HTML 页面而非模型文件，请检查网络或代理")
            else:
                _stream_to_file(r, tmp)
    os.replace(tmp, dst)


def _stream_to_file(resp, tmp: Path) -> None:
    total = int(resp.headers.get("content-length") or 0)
    got = 0
    last_pct = -1
    with tmp.open("wb") as f:
        for chunk in resp.iter_bytes(chunk_size=1024 * 256):
            if not chunk:
                continue
            f.write(chunk)
            got += len(chunk)
            if total > 0:
                pct = int(got * 100 / total)
                if pct >= last_pct + 10:
                    last_pct = pct
                    print(f"    下载进度: {pct}% ({got // (1024 * 1024)}/{total // (1024 * 1024)} MB)", flush=True)
            elif got % (10 * 1024 * 1024) < 1024 * 256:
                print(f"    已下载: {got // (1024 * 1024)} MB", flush=True)


def download_wenet_encoder() -> bool:
    if WENET_ENCODER_PATH.is_file() and WENET_ENCODER_PATH.stat().st_size > 10_000_000:
        print(f"[OK] WeNet encoder.onnx 已存在 ({WENET_ENCODER_PATH.stat().st_size // (1024 * 1024)} MB)")
        return True
    print("==> 下载 WeNet encoder.onnx (Google Drive)...")
    urls = [
        f"https://drive.usercontent.google.com/download?id={WENET_GDRIVE_ID}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={WENET_GDRIVE_ID}",
    ]
    for url in urls:
        try:
            _download_with_progress(url, WENET_ENCODER_PATH, timeout=120.0)
            size = WENET_ENCODER_PATH.stat().st_size
            if size > 10_000_000:
                # Validate ONNX if onnx package is installed
                try:
                    import onnxruntime as ort
                    ort.InferenceSession(str(WENET_ENCODER_PATH), providers=["CPUExecutionProvider"])
                except ImportError:
                    pass
                print(f"[OK] WeNet encoder.onnx 下载并校验完成 ({size // (1024 * 1024)} MB)")
                return True
        except Exception as exc:
            print(f"[WARN] 从 {url} 下载失败: {exc}")
            WENET_ENCODER_PATH.unlink(missing_ok=True)
    print(
        "[ERR] WeNet encoder.onnx 自动下载失败。\n"
        f"      请手动下载 https://drive.google.com/file/d/{WENET_GDRIVE_ID}/view?usp=drive_link\n"
        f"      并放到: {WENET_ENCODER_PATH}"
    )
    return False


def download_hubert() -> bool:
    st = check_local_assets()
    if st["hubert"]:
        print(f"[OK] HuBERT 模型已就绪: {HUBERT_DIR}")
        return True
    HUBERT_DIR.mkdir(parents=True, exist_ok=True)
    print("==> 下载 facebook/hubert-large-ls960-ft (用于 25fps 高精度嘴型)...")
    from huggingface_hub import snapshot_download

    endpoints = ["https://huggingface.co", "https://hf-mirror.com"]
    for ep in endpoints:
        try:
            print(f"    尝试镜像: {ep}")
            os.environ["HF_ENDPOINT"] = ep
            snapshot_download(
                repo_id="facebook/hubert-large-ls960-ft",
                local_dir=str(HUBERT_DIR),
                ignore_patterns=["*.msgpack", "*.h5", "*.ot", "flax_model*", "tf_model*"],
            )
            if check_local_assets()["hubert"]:
                print(f"[OK] HuBERT 模型下载完成 -> {HUBERT_DIR}")
                return True
        except Exception as exc:
            print(f"[WARN] {ep} 下载失败: {exc}")
    print(
        "[ERR] HuBERT 下载失败，请检查网络或手动将 facebook/hubert-large-ls960-ft 放入:\n"
        f"      {HUBERT_DIR}"
    )
    return False


def download_vgg19() -> bool:
    try:
        import torchvision.models as tv_models
        print("==> 检查/预缓存训练感知损失模型 VGG19...")
        tv_models.vgg19(weights=tv_models.VGG19_Weights.IMAGENET1K_V1)
        print("[OK] VGG19 权重已就绪")
        return True
    except Exception as exc:
        print(f"[WARN] VGG19 预缓存失败（训练时会自动重试）: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="下载并校验 Ultralight-Digital-Human 所需模型权重")
    ap.add_argument("--mode", default="all", choices=["all", "wenet", "hubert", "check"])
    ap.add_argument("--with-vgg", action="store_true", help="同时预缓存训练用 VGG19")
    args = ap.parse_args()

    st = check_local_assets()
    print("当前基础模型状态:")
    print(f"  - 人脸检测 SCRFD        : {'OK' if st['scrfd'] else 'MISSING'} ({SCRFD_PATH})")
    print(f"  - 关键点 PFLD           : {'OK' if st['pfld'] else 'MISSING'} ({PFLD_PATH})")
    print(f"  - WeNet encoder.onnx    : {'OK' if st['wenet_encoder'] else 'MISSING'} ({WENET_ENCODER_PATH})")
    print(f"  - HuBERT large-ls960-ft : {'OK' if st['hubert'] else 'MISSING'} ({HUBERT_DIR})")

    if args.mode == "check":
        ok = st["scrfd"] and st["pfld"] and (st["wenet_encoder"] or st["hubert"])
        return 0 if ok else 1

    ok = st["scrfd"] and st["pfld"]
    if args.mode in ("all", "wenet"):
        ok = download_wenet_encoder() and ok
    if args.mode in ("all", "hubert"):
        ok = download_hubert() and ok
    if args.with_vgg or args.mode == "all":
        download_vgg19()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
