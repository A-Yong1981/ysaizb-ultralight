@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
rem defaults for env.bat files created before these options existed
if not defined UL_HOST set "UL_HOST=127.0.0.1"
if not defined UL_BATCH set "UL_BATCH=4"
if not defined UL_MAX_FRAMES set "UL_MAX_FRAMES=300"
if not defined UL_OUT_HEIGHT set "UL_OUT_HEIGHT=1280"
if not defined UL_MAX_LOADED set "UL_MAX_LOADED=2"
if not defined UL_ORT_GPU_MB set "UL_ORT_GPU_MB=2048"
if not defined UL_MAX_SESSION set "UL_MAX_SESSION=1"
if not defined UL_AV_OFFSET_MS set "UL_AV_OFFSET_MS=0"
if not defined UL_MAX_PREVIEW set "UL_MAX_PREVIEW=1"
title Ultralight-Engine-%UL_PORT%
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%ROOT%"
python "%ROOT%scripts\tee_run.py" "%ROOT%logs\ultralight_engine.log" python "%ENGINE_DIR%\ultralight_server.py" --asr %UL_ASR% --backend %UL_BACKEND% --avatar_id %UL_AVATAR% --voice %UL_VOICE% --max_session %UL_MAX_SESSION% --listenport %UL_PORT% --host %UL_HOST% --batch %UL_BATCH% --max_avatar_frames %UL_MAX_FRAMES% --out_height %UL_OUT_HEIGHT% --max_loaded_avatars %UL_MAX_LOADED% --ort_gpu_mem_mb %UL_ORT_GPU_MB% --av_offset_ms %UL_AV_OFFSET_MS% --max_preview %UL_MAX_PREVIEW%
