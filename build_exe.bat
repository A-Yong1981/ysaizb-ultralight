@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight - build standalone EXE
if not defined CONDA_ROOT goto :noconda
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
python "%ROOT%scripts\build_exe.py"
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
:noconda
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
