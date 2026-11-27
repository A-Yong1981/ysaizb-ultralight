# -*- coding: utf-8 -*-
"""泳昇 Ultralight 数字人 环境自检：GPU / PyTorch / ONNX Runtime / Ultralight 模型与形象 / 中控依赖与配置"""
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB = ROOT / "ai-live-hub"
UDH_DIR = ROOT / "Ultralight-Digital-Human"
MODELS_DIR = ROOT / "models"
AVATARS_DIR = ROOT / "avatars"

n_err = 0
n_warn = 0


def ok(msg):
    print(f"  [OK]   {msg}")


def warn(msg):
    global n_warn
    n_warn += 1
    print(f"  [WARN] {msg}")


def err(msg):
    global n_err
    n_err += 1
    print(f"  [ERR]  {msg}")


print("== 1. Python / PyTorch / GPU / ONNX Runtime ==")
ok(f"Python {sys.version.split()[0]}  ({sys.executable})")
try:
    import torch

    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        cap = torch.cuda.get_device_capability(0)
        vram = p.total_memory / 1024 ** 3
        ok(f"torch {torch.__version__} / CUDA {torch.version.cuda} / {p.name} sm_{cap[0]}{cap[1]} / {vram:.1f} GB")
        if cap < (8, 0):
            ok("Turing 架构：仅 FP16 / ONNX（禁止 cu130 / BF16 / 大显存本地 LLM）")
    else:
        err(f"torch {torch.__version__} 不能使用 CUDA，请重新运行 01_install.bat")
except ImportError:
    err("未安装 PyTorch，请运行 01_install.bat")

try:
    import onnx
    import onnxruntime as ort

    providers = ort.get_available_providers()
    if "CUDAExecutionProvider" in providers:
        ok(f"onnx {onnx.__version__} / onnxruntime-gpu {ort.__version__} ({', '.join(providers)})")
    else:
        warn(f"onnxruntime {ort.__version__} 未启用 CUDAExecutionProvider（仅 {providers}）")
except ImportError as exc:
    err(f"缺少 ONNX / onnxruntime-gpu: {exc}")

try:
    import kaldi_native_fbank  # noqa: F401
    ok("kaldi_native_fbank (WeNet 特征提取) 已安装")
except ImportError:
    warn("未安装 kaldi-native-fbank（使用 wenet 20fps 模式时必需）")

print("== 2. ffmpeg ==")
ff = shutil.which("ffmpeg")
ok(f"ffmpeg: {ff}") if ff else err("未找到 ffmpeg（conda install -c conda-forge ffmpeg）")

print(f"== 3. Ultralight-Digital-Human ({UDH_DIR}) ==")
if (UDH_DIR / "unet.py").exists() and (UDH_DIR / "train.py").exists():
    ok("上游 Ultralight-Digital-Human 源码已就绪")
    if any(ord(c) > 127 for c in str(ROOT)):
        warn("工作目录路径含中文，OpenCV 读图可能失败，建议放到纯英文路径")
    scrfd = UDH_DIR / "data_utils" / "scrfd_2.5g_kps.onnx"
    pfld = UDH_DIR / "data_utils" / "checkpoint_epoch_335.pth.tar"
    ok(f"SCRFD 人脸检测: {scrfd.name}") if scrfd.is_file() else err(f"缺少 {scrfd}")
    ok(f"PFLD 98点关键点: {pfld.name}") if pfld.is_file() else err(f"缺少 {pfld}")

    encoder_onnx = MODELS_DIR / "encoder.onnx"
    if not encoder_onnx.is_file():
        alt_enc = UDH_DIR / "data_utils" / "encoder.onnx"
        if alt_enc.is_file():
            encoder_onnx = alt_enc
    if encoder_onnx.is_file():
        ok(f"WeNet encoder.onnx ({encoder_onnx.stat().st_size / 1024 ** 2:.1f} MB)")
    else:
        warn("缺少 WeNet encoder.onnx（请运行 02_download_models.bat 或改用 hubert 模式）")

    hubert_dir = MODELS_DIR / "hubert-large-ls960-ft"
    if (hubert_dir / "config.json").is_file() and any(hubert_dir.glob("*.safetensors")) or any(hubert_dir.glob("pytorch_model.bin")):
        ok(f"HuBERT 模型已就绪: {hubert_dir}")
    else:
        warn("缺少本地 HuBERT 模型 models/hubert-large-ls960-ft（请运行 02_download_models.bat 下载）")

    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    avatars = sorted(d for d in AVATARS_DIR.iterdir() if d.is_dir() and not d.name.startswith(("__", ".")))
    if not avatars:
        warn("avatars/ 暂无已封装的 Ultralight 形象（请运行 04_train_avatar.bat 训练或导入外部 .pth/.onnx）")
    for d in avatars:
        has_img = (d / "full_body_img").is_dir()
        has_lms = (d / "landmarks").is_dir()
        has_model = (d / "unet.onnx").is_file() or any(d.glob("*.pth")) or (d / "checkpoint" / "ultralight.pth").is_file()
        n = len(list((d / "full_body_img").glob("*"))) if has_img else 0
        ready = has_img and has_lms and has_model
        (ok if ready else warn)(f"形象 {d.name}: {'就绪' if ready else '不完整'}，{n} 帧，模型={'有' if has_model else '缺失'}")
else:
    err("未找到 Ultralight-Digital-Human 源码，请运行 01_install.bat")
    avatars = []

print("== 4. 引擎 & 中控 ai-live-hub 依赖 ==")
for mod in ("aiohttp", "aiortc", "av", "cv2", "transformers", "fastapi", "uvicorn", "httpx", "websockets", "pydantic", "multipart", "bs4", "docx"):
    try:
        importlib.import_module(mod)
        ok(mod)
    except ImportError:
        err(f"缺少 {mod}（pip install -r ai-live-hub/requirements.txt）")

print("== 5. config.json ==")
try:
    cfg = json.loads((HUB / "config.json").read_text(encoding="utf-8"))

    def filled(k):
        k = str(k or "").strip()
        return bool(k) and not k.startswith("填入")

    provs = [p for p in cfg.get("llm_providers", []) if p.get("enabled") and filled(p.get("api_key"))]
    if provs:
        ok(f"免费 LLM 可用节点 {len(provs)} 个: {', '.join(p['name'] for p in provs)}")
    else:
        warn("未填任何免费 LLM Key —— 弹幕只会用本地兜底话术回复")
    tts = cfg.get("tts", {})
    mode = tts.get("mode", "edgetts")
    ok(f"TTS 模式: {mode}")
    if mode == "siliconflow" and not filled(tts.get("siliconflow", {}).get("api_key")):
        warn("TTS=siliconflow 但未填 tts.siliconflow.api_key，将自动降级 Edge-TTS")
    ul = cfg.get("ultralight", {})
    ok(f"Ultralight 默认配置: port=8020 asr={ul.get('asr', 'hubert')} backend={ul.get('backend', 'onnx')}")
except Exception as exc:
    err(f"读取 config.json 失败: {exc}")

print("== 6. 平台预设 platform_profiles.json ==")
try:
    pp = json.loads((HUB / "platform_profiles.json").read_text(encoding="utf-8"))
    profs = pp.get("profiles", {})
    ok(f"共 {len(profs)} 个平台: {', '.join(v.get('label', k) for k, v in profs.items())}")
    if pp.get("obs_ws", {}).get("enabled"):
        try:
            importlib.import_module("obsws_python")
            ok("obsws-python 已安装（OBS 自动切配置文件 / 场景）")
        except ImportError:
            warn("未安装 obsws-python，OBS 需手动切换")
except Exception as exc:
    err(f"读取 platform_profiles.json 失败: {exc}")

print("== 7. 知识库讲稿 kb_data ==")
try:
    kb_dir = HUB / "kb_data"
    docs = sorted(p.name for p in kb_dir.glob("*.md"))
    ok(f"讲稿 {len(docs)} 篇: {', '.join(docs)}") if docs else warn("kb_data 里没有讲稿")
except Exception as exc:
    err(f"检查讲稿失败: {exc}")

print("== 8. 冒烟测试 + 回归测试（tests/） ==")
try:
    (ROOT / "logs").mkdir(exist_ok=True)
    log_parts = []
    for name, marker in (("test_smoke.py", "ALL SMOKE TESTS PASSED"), ("test_regressions.py", "ALL REGRESSION CHECKS PASSED")):
        r = subprocess.run([sys.executable, str(HUB / "tests" / name)], cwd=str(HUB), capture_output=True, timeout=180,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
        txt = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        log_parts.append(txt)
        if r.returncode == 0 and marker in txt:
            ok(f"{name} 全部通过")
        else:
            fails = [ln for ln in txt.splitlines() if "[FAIL]" in ln or "Error" in ln][:3]
            err(f"{name} 未通过：{' | '.join(fails) or (txt.strip().splitlines() or ['无输出'])[-1][:160]}")
    (ROOT / "logs" / "smoke_test.log").write_text("\n".join(log_parts), encoding="utf-8")
except Exception as exc:
    err(f"运行测试失败: {exc}")

print()
print(f"自检完成：{n_err} 个错误，{n_warn} 个提醒")
sys.exit(1 if n_err else 0)
