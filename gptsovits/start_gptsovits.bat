@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
title GPT-SoVITS v2 FP16 :9880
rem Optional plan B. First copy the custom block of gptsovits\tts_infer_2080.yaml into
rem %GPTSOVITS_DIR%\GPT_SoVITS\configs\tts_infer.yaml, and set LT_BATCH=4 in env.bat
set "PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128"
if not exist "%GPTSOVITS_DIR%\api_v2.py" goto :missing
cd /d "%GPTSOVITS_DIR%"
if exist "runtime\python.exe" goto :runtime
call "%CONDA_ROOT%\Scripts\activate.bat" %GPTSOVITS_ENV%
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
exit /b
:runtime
runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
exit /b
:missing
echo [ERROR] %GPTSOVITS_DIR%\api_v2.py not found. Install GPT-SoVITS and fix GPTSOVITS_DIR in env.bat
pause
exit /b 1
