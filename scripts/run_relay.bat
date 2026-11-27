@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
title DouyinLive Relay (Ultralight Hub :28910)
set "ROOM=%~1"
if "%ROOM%"=="" set /p ROOM=Douyin room id: 
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%HUB_DIR%"
python "%ROOT%scripts\tee_run.py" "%ROOT%logs\relay_douyin.log" python danmaku_relay.py --room %ROOM% --hub http://127.0.0.1:%HUB_PORT%
