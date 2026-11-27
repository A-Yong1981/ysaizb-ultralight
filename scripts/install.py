# -*- coding: utf-8 -*-
"""
泳昇 Ultralight-Digital-Human 一键安装器（由 01_install.bat 调起）
- 克隆 Ultralight-Digital-Human 源码（如尚未存在）
- 创建独立 conda 环境 ultralight (Python 3.12 + ffmpeg)
- 安装适配 RTX 2080 (sm_75) 的 CUDA PyTorch（cu128/cu126/cu124）
- 安装 onnx / onnxruntime-gpu / kaldi-native-fbank / onnxscript 及 ai-live-hub 依赖
用法： python install.py              全部步骤
       python install.py --from 4     从第 4 步开始
"""
import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB = ROOT / "ai-live-hub"
UDH_DIR = Path(os.environ.get("UDH_DIR", str(ROOT / "Ultralight-Digital-Human")))
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / "install.log"
SUMMARY_PATH = LOG_DIR / "install_summary.json"

ENV_NAME = os.environ.get("CONDA_ENV", "ultralight")
CONDA_ROOT = Path(os.environ.get("CONDA_ROOT", sys.prefix))
PIP_INDEX = os.environ.get("PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")

_log = open(LOG_PATH, "a", encoding="utf-8")
summary = {"started": _dt.datetime.now().isoformat(timespec="seconds"), "steps": {}, "info": {}}


def out(msg: str = "") -> None:
    print(msg, flush=True)
    _log.write(msg + "\n")
    _log.flush()


def save_summary() -> None:
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def run(cmd, cwd=None, env_extra=None) -> int:
    shown = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    out(f"$ {shown}")
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "CONDA_PLUGINS_AUTO_ACCEPT_TOS": "yes",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError as exc:
        out(f"[错误] 找不到程序: {exc}")
        return 127
    for raw in iter(p.stdout.readline, b""):
        out(_decode(raw).rstrip("\r\n"))
    p.wait()
    out(f"(退出码 {p.returncode})")
    return p.returncode


def capture(cmd) -> str:
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=60)
        return _decode(r.stdout) + _decode(r.stderr)
    except Exception as exc:
        return f"ERR {exc}"


def parse_cuda(smi: str):
    m = re.search(r"CUDA(?:\s+UMD)?\s+Version\s*:\s*([\d.]+)", smi)
    d = re.search(r"(?:Driver|KMD)\s+Version\s*:\s*([\d.]+)", smi)
    return (m.group(1) if m else None), (d.group(1) if d else None)


def conda_exe() -> Path:
    for cand in (CONDA_ROOT / "Scripts" / "conda.exe", CONDA_ROOT / "condabin" / "conda.bat"):
        if cand.exists():
            return cand
    return Path("conda")


def env_python() -> Path:
    return CONDA_ROOT / "envs" / ENV_NAME / "python.exe"


def step0_check() -> bool:
    out(f"Python(安装器): {sys.version.split()[0]}  Conda: {CONDA_ROOT}")
    git = shutil.which("git")
    out(f"git: {git}")
    if not git:
        out("[错误] 未找到 git，请安装 https://git-scm.com/download/win 后重试")
        return False
    smi = capture(["nvidia-smi"])
    out(smi)
    (LOG_DIR / "gpu_info.txt").write_text(smi, encoding="utf-8")
    cuda_v, drv_v = parse_cuda(smi)
    g = capture(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader"]).strip()
    summary["info"].update({"driver": drv_v, "driver_cuda": cuda_v, "gpu": g})
    if not cuda_v:
        out("[错误] 读不到驱动 CUDA 版本，请确认 NVIDIA 驱动已正确安装")
        return False
    out(f"==> 显卡: {g} | 驱动 {drv_v} | 驱动最高支持 CUDA {cuda_v}")
    if any(ord(ch) > 127 for ch in str(ROOT)) or " " in str(ROOT):
        out(f"[错误] ROOT={ROOT} 含中文或空格，请放到纯英文目录")
        return False
    return True


def step1_clone() -> bool:
    if (UDH_DIR / "unet.py").exists() and (UDH_DIR / "train.py").exists():
        out(f"Ultralight-Digital-Human 已存在: {UDH_DIR}（跳过克隆）")
        return True
    url = "https://github.com/anliyuan/Ultralight-Digital-Human.git"
    if UDH_DIR.exists() and not any(UDH_DIR.iterdir()):
        UDH_DIR.rmdir()
    return run(["git", "clone", "--depth", "1", url, str(UDH_DIR)]) == 0 and (UDH_DIR / "unet.py").exists()


def channels() -> list:
    chs = [os.environ.get("CONDA_FORGE_MIRROR", "").strip(), "conda-forge"]
    return [c for c in chs if c]


def step2_env() -> bool:
    if env_python().exists():
        out(f"conda 环境 {ENV_NAME} 已存在: {env_python()}")
        return True
    for ch in channels():
        out(f"==> 用频道 {ch} 创建环境")
        if run([conda_exe(), "create", "-n", ENV_NAME, "python=3.12", "-c", ch,
                "--override-channels", "-y"]) == 0 and env_python().exists():
            return True
    return False


def step3_ffmpeg() -> bool:
    ff = CONDA_ROOT / "envs" / ENV_NAME / "Library" / "bin" / "ffmpeg.exe"
    if ff.exists():
        out(f"ffmpeg 已安装: {ff}")
        return True
    for ch in channels():
        if run([conda_exe(), "install", "-n", ENV_NAME, "-c", ch, "--override-channels", "ffmpeg", "-y"]) == 0:
            return True
    return False


TORCH_PROBE = (
    "import torch;"
    "assert torch.cuda.is_available(),'cuda not available';"
    "x=torch.randn(256,256,device='cuda',dtype=torch.float16);y=(x@x).sum().item();"
    "c=torch.cuda.get_device_capability(0);"
    "print(f'TORCH_OK {torch.__version__} cuda={torch.version.cuda} {torch.cuda.get_device_name(0)} sm_{c[0]}{c[1]}')"
)


def torch_candidates(cuda: tuple) -> list:
    t29 = ["torch==2.9.1", "torchvision==0.24.1", "torchaudio==2.9.1"]
    t26 = ["torch==2.6.0", "torchvision==0.21.0", "torchaudio==2.6.0"]
    c = []
    if cuda >= (12, 8):
        c.append((t29, "cu128"))
    if cuda >= (12, 6):
        c.append((t29, "cu126"))
    if cuda >= (12, 4):
        c.append((t26, "cu124"))
    if cuda >= (11, 8):
        c.append((t26, "cu118"))
    return c


def step4_torch() -> bool:
    py = env_python()
    r = subprocess.run([str(py), "-c", TORCH_PROBE], capture_output=True)
    if r.returncode == 0:
        out(_decode(r.stdout).strip() + "（已安装且 CUDA 内核验证通过，跳过）")
        summary["info"]["torch"] = _decode(r.stdout).strip()
        return True
    v = summary["info"].get("driver_cuda") or parse_cuda(capture(["nvidia-smi"]))[0]
    if not v:
        out("[错误] 无驱动 CUDA 版本信息")
        return False
    parts = v.split(".")
    cuda = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    run([py, "-m", "pip", "install", "-U", "pip", "-i", PIP_INDEX])
    for pkgs, tag in torch_candidates(cuda):
        out(f"==> 本机驱动 CUDA {v}，尝试 PyTorch {pkgs[0]} + {tag}")
        for src in (["--index-url", f"https://download.pytorch.org/whl/{tag}"],
                    ["-f", f"https://mirrors.aliyun.com/pytorch-wheels/{tag}/", "-i", PIP_INDEX]):
            if run([py, "-m", "pip", "install", "--force-reinstall", "--no-cache-dir", *pkgs, *src]) != 0:
                continue
            r = subprocess.run([str(py), "-c", TORCH_PROBE], capture_output=True)
            msg = (_decode(r.stdout) + _decode(r.stderr)).strip()
            out(msg)
            if r.returncode == 0:
                summary["info"]["torch"] = msg
                return True
            break
    return False


def step5_hub_and_ultralight_reqs() -> bool:
    py = env_python()
    if run([py, "-m", "pip", "install", "-r", HUB / "requirements.txt", "-i", PIP_INDEX]) != 0:
        return False
    # 补充 WebRTC 与音频特征依赖
    extra = ["aiortc", "av", "aiohttp", "transformers", "scipy", "librosa", "soundfile", "opencv-python", "tqdm"]
    return run([py, "-m", "pip", "install", *extra, "-i", PIP_INDEX]) == 0


def step6_check() -> bool:
    (ROOT / "models").mkdir(parents=True, exist_ok=True)
    (ROOT / "avatars").mkdir(parents=True, exist_ok=True)
    for ex_name, real_name in (("config.example.json", "config.json"), ("platform_profiles.example.json", "platform_profiles.json")):
        src, dst = HUB / ex_name, HUB / real_name
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
            out(f"[初始化] 已生成默认 {real_name}（请填入自己的 API Key / 房间号）")
    r = subprocess.run([str(env_python()), "-c", TORCH_PROBE], capture_output=True)
    out((_decode(r.stdout) + _decode(r.stderr)).strip())
    if r.returncode != 0:
        out("[错误] 依赖安装后 PyTorch CUDA 失效，请运行: 01_install.bat 4")
        return False
    run([env_python(), ROOT / "scripts" / "check_env.py"])
    return True


STEPS = [
    ("0 环境检查（git / 驱动 / CUDA）", step0_check),
    ("1 获取 Ultralight-Digital-Human 源码", step1_clone),
    ("2 创建 conda 环境 Python 3.12", step2_env),
    ("3 安装 ffmpeg", step3_ffmpeg),
    ("4 按本机 CUDA 安装 PyTorch", step4_torch),
    ("5 安装 Ultralight + ONNX + 中控依赖", step5_hub_and_ultralight_reqs),
    ("6 验证与自检", step6_check),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=0)
    args = ap.parse_args()
    out("=" * 70)
    out(f"泳昇 Ultralight 安装开始 {summary['started']}  UDH_DIR={UDH_DIR}  ENV={ENV_NAME}")
    out("=" * 70)
    failed = None
    for i, (name, fn) in enumerate(STEPS):
        if i < args.start and i != 0:
            continue
        out("")
        out(f"########## [{name}] ##########")
        try:
            ok = fn()
        except Exception as exc:
            out(f"[异常] {exc!r}")
            ok = False
        summary["steps"][name] = "OK" if ok else "FAILED"
        save_summary()
        if not ok:
            failed = (i, name)
            break
    summary["finished"] = _dt.datetime.now().isoformat(timespec="seconds")
    summary["result"] = "OK" if not failed else f"FAILED at step {failed[0]}"
    save_summary()
    out("=" * 70)
    if failed:
        out(f"安装在第 {failed[0]} 步失败：{failed[1]}，请查看 {LOG_PATH}")
        return 1
    out("依赖安装完成！接下来请运行 02_download_models.bat 下载模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
