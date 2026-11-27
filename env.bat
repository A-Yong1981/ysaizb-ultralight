@echo off
rem ===== ysaizb-ultralight shared settings - edit values here only =====
for %%I in ("%~dp0.") do set "ROOT=%%~fI\"
set "HUB_DIR=%ROOT%ai-live-hub"
set "ENGINE_DIR=%ROOT%engine"
set "UDH_DIR=%ROOT%Ultralight-Digital-Human"
set "CONDA_ENV=ultralight"

rem Ultralight engine runtime options (i5-12400F / 32GB / RTX 2080 8GB)
rem   Plain-language help for every option: WebUI -> Easy Setup, or desktop app -> Easy Setup button
rem   UL_ASR         : wenet (20fps ONNX, <1GB VRAM) | hubert (25fps FP16, ~1.3GB VRAM)
rem   UL_BACKEND     : onnx | torch
rem   UL_MAX_SESSION : video outputs at the same time (OBS needs 1; each extra one costs a CPU encoder)
rem   UL_MAX_FRAMES  : body-motion loop frames kept in RAM (25 = 1 second)
rem   UL_OUT_HEIGHT  : downscale avatar frames to this height before streaming (0 = keep original)
rem   UL_BATCH       : frames per UNet call when catching up (needs dynamic-batch unet.onnx)
rem   UL_ORT_GPU_MB  : VRAM cap for ONNX Runtime arena (keeps whole box under 4.5GB)
rem   UL_MAX_LOADED  : avatars/outfits kept in RAM (LRU)
rem   UL_HOST        : 127.0.0.1 = this PC only (safe); 0.0.0.0 = whole LAN (unsafe)
rem   UL_AV_OFFSET_MS: lip-sync fine tune in ms; positive = delay voice (mouth lags voice -> raise it)
rem   UL_MAX_PREVIEW : preview windows sharing the OBS feed (each one costs an extra video encoder)
set "UL_ASR=hubert"
set "UL_BACKEND=onnx"
set "UL_AVATAR=a_c52c"
set "UL_VOICE=zh-CN-XiaoxiaoNeural"
set "UL_BATCH=4"
set "UL_MAX_SESSION=1"
set "UL_MAX_FRAMES=300"
set "UL_OUT_HEIGHT=1280"
set "UL_ORT_GPU_MB=2048"
set "UL_MAX_LOADED=2"
set "UL_HOST=127.0.0.1"
set "UL_AV_OFFSET_MS=0"
set "UL_MAX_PREVIEW=1"
set "UL_PORT=8020"
set "HUB_PORT=28910"

rem ---- your own settings live in env.local.bat (written by the desktop app / Easy Setup, not tracked by git) ----
rem ---- so "git pull" / 06_update.bat never conflicts with your avatar, voice or tuning choices ----
if exist "%~dp0env.local.bat" call "%~dp0env.local.bat"

rem Download mirrors
set "PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
set "CONDA_FORGE_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge"
set "MINICONDA_URL=https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Windows-x86_64.exe"
set "HF_HUB_ENABLE_HF_TRANSFER=0"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem ---- locate conda. To force a path, add a line: set "CONDA_ROOT=X:\your\miniconda3" ----
if defined CONDA_ROOT if exist "%CONDA_ROOT%\Scripts\activate.bat" goto :eof
set "CONDA_ROOT="
for %%D in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3" "C:\miniconda3" "D:\miniconda3" "C:\anaconda3" "D:\anaconda3") do (
  if not defined CONDA_ROOT if exist "%%~D\Scripts\activate.bat" set "CONDA_ROOT=%%~D"
)
if defined CONDA_ROOT goto :eof
for /f "delims=" %%P in ('where conda.exe 2^>nul') do (
  if not defined CONDA_ROOT set "CONDA_ROOT=%%~dpP.."
)
goto :eof
