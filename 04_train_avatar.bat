@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight - train and export ONNX avatar
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
if not defined CONDA_ROOT goto :noconda
set "AID=%~1"
set "ANAME=%~2"
set "ASR=%~3"
set "VIDEO=%~4"
set "CKPT=%~5"
if "%AID%"=="" set /p AID=Avatar ID (ASCII, e.g. ul_host1): 
if "%ANAME%"=="" set "ANAME=%AID%"
if "%ASR%"=="" set "ASR=%UL_ASR%"
if "%VIDEO%"=="" set /p VIDEO=Path to speaking MP4 video: 
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%ROOT%"
if "%CKPT%"=="" (
  python "%ROOT%scripts\tee_run.py" "%ROOT%logs\train_%AID%.log" python "%ROOT%scripts\ultralight_pipeline.py" --avatar_id "%AID%" --name "%ANAME%" --asr "%ASR%" --video "%VIDEO%"
) else (
  python "%ROOT%scripts\tee_run.py" "%ROOT%logs\train_%AID%.log" python "%ROOT%scripts\ultralight_pipeline.py" --avatar_id "%AID%" --name "%ANAME%" --asr "%ASR%" --video "%VIDEO%" --checkpoint "%CKPT%"
)
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
:noconda
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
