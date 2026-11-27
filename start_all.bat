@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight Launcher - RTX 2080 8G
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
if not defined CONDA_ROOT goto :noconda
if not exist "%UDH_DIR%\unet.py" goto :noudh
echo [1/3] Starting Ultralight Engine :%UL_PORT%  asr=%UL_ASR%  backend=%UL_BACKEND%  avatar=%UL_AVATAR%
start "Ultralight-Engine-%UL_PORT%" cmd /k call "%ROOT%scripts\run_engine.bat"
timeout /t 6 /nobreak >nul
echo [2/3] Starting AI Live Hub :%HUB_PORT%
start "AI-Live-Hub-%HUB_PORT%" cmd /k call "%ROOT%scripts\run_hub.bat"
timeout /t 3 /nobreak >nul
echo [3/3] Opening WebUI ...
start "" "http://127.0.0.1:%HUB_PORT%/"
echo.
echo OBS browser source : http://127.0.0.1:%HUB_PORT%/stage?layer=all
echo Logs               : logs\ultralight_engine.log  logs\hub.log
echo Douyin danmaku     : start douyinLive :1089 first, then scripts\run_relay.bat ROOM_ID
pause
exit /b 0
:noconda
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
:noudh
echo [ERROR] Ultralight-Digital-Human not found at %UDH_DIR% - run 01_install.bat first.
pause
exit /b 1
