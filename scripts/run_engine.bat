@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
title Ultralight-Engine-%UL_PORT%
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%ROOT%"
python "%ROOT%scripts\tee_run.py" "%ROOT%logs\ultralight_engine.log" python "%ENGINE_DIR%\ultralight_server.py" --asr %UL_ASR% --backend %UL_BACKEND% --avatar_id %UL_AVATAR% --voice %UL_VOICE% --max_session %UL_MAX_SESSION% --listenport %UL_PORT%
