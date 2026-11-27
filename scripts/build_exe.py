# -*- coding: utf-8 -*-
"""
将 launcher/app.py 打包为独立单文件桌面应用：
  d:\\Antigravity\\ysaizb-ultralight\\泳昇Ultralight直播平台.exe
排除 torch / torchvision / onnxruntime / transformers 等重型 AI 库，使 EXE 保持轻量秒开，
实际模型推理与训练仍通过独立 ultralight conda 环境拉起子进程执行。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "泳昇Ultralight直播平台"
ENTRY = ROOT / "launcher" / "app.py"
ICON = ROOT / "launcher" / "app_icon.ico"
WORK_DIR = ROOT / "build" / "pyinstaller_work"
SPEC_DIR = ROOT / "build" / "pyinstaller_spec"
DIST_DIR = ROOT

EXCLUDES = [
    "torch",
    "torchvision",
    "torchaudio",
    "onnx",
    "onnxruntime",
    "onnxscript",
    "transformers",
    "tokenizers",
    "scipy",
    "librosa",
    "numba",
    "llvmlite",
    "av",
    "aiortc",
    "aiohttp",
    "fastapi",
    "uvicorn",
    "matplotlib",
    "pandas",
    "IPython",
    "pytest",
    "setuptools",
]


def main() -> int:
    if not ENTRY.is_file():
        print(f"[ERR] Missing entry: {ENTRY}")
        return 1
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    # Conda 环境的 tk86t.dll / tcl86t.dll / ffi-8.dll / openssl 等位于 Library\bin，必须加入 PATH 才能被 PyInstaller 打包进去
    env_dir = Path(sys.executable).resolve().parent
    extra_dirs = [str(env_dir / "Library" / "bin"), str(env_dir / "DLLs"), str(env_dir)]
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(extra_dirs) + os.pathsep + env.get("PATH", "")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--paths",
        str(ROOT),
        "--paths",
        str(ROOT / "ai-live-hub"),
        "--hidden-import",
        "launcher.backend",
        "--hidden-import",
        "overlay_presets",
        "--collect-submodules",
        "PIL",
    ]
    for bdir in (env_dir / "Library" / "bin",):
        if bdir.is_dir():
            for dll_name in ("tk86t.dll", "tcl86t.dll", "ffi-8.dll", "libexpat.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll", "liblzma.dll", "libbz2.dll", "sqlite3.dll"):
                dp = bdir / dll_name
                if dp.is_file():
                    cmd += ["--add-binary", f"{dp};."]
    ICON = ROOT / "launcher" / "app_icon.ico"
    KB_MD = ROOT / "launcher" / "ultralight_config_kb.md"
    if ICON.is_file():
        cmd += ["--icon", str(ICON), "--add-data", f"{ICON};."]
    if KB_MD.is_file():
        cmd += ["--add-data", f"{KB_MD};launcher"]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ENTRY))

    print("Running:", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if r.returncode != 0:
        return r.returncode

    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if not exe_path.is_file():
        print(f"[ERR] Expected EXE missing: {exe_path}")
        return 1

    # 清理临时构建目录，避免残留杂物
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"[OK] Packaged standalone app: {exe_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
