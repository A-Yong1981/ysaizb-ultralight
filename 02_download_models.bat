@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight - download models
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
if not defined CONDA_ROOT goto :noconda
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
set "MODE=%~1"
if "%MODE%"=="" set "MODE=all"
python "%ROOT%scripts\tee_run.py" "%ROOT%logs\download_models.log" python "%ROOT%scripts\download_models.py" --mode %MODE% --with-vgg
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
:noconda
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
