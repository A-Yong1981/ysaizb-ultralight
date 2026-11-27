@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
title AI-Live-Hub-%HUB_PORT%
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%HUB_DIR%"
python "%ROOT%scripts\tee_run.py" "%ROOT%logs\hub.log" python server.py
