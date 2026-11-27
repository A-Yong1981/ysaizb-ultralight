@echo off
rem ===== ysaizb-ultralight shared settings - edit values here only =====
for %%I in ("%~dp0.") do set "ROOT=%%~fI\"
set "HUB_DIR=%ROOT%ai-live-hub"
set "ENGINE_DIR=%ROOT%engine"
set "UDH_DIR=%ROOT%Ultralight-Digital-Human"
set "CONDA_ENV=ultralight"

rem Ultralight engine runtime options (RTX 2080 8GB)
rem   UL_ASR     : wenet (20fps ONNX, <1GB VRAM) | hubert (25fps FP16, ~1.3GB VRAM)
rem   UL_BACKEND : onnx | torch
set "UL_ASR=hubert"
set "UL_BACKEND=onnx"
set "UL_AVATAR=a_c52c"
set "UL_VOICE=zh-CN-XiaoxiaoNeural"
set "UL_BATCH=4"
set "UL_MAX_SESSION=2"
set "UL_PORT=8020"
set "HUB_PORT=28910"

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
