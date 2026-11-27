@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight - one-click install
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
nvidia-smi > "%ROOT%logs\gpu_info.txt" 2>&1
if defined CONDA_ROOT goto :have_conda
type "%ROOT%scripts\msg\no_conda.txt"
choice /c YN /n /m "Install Miniconda automatically now? [Y/N] "
if errorlevel 2 goto :abort
call "%ROOT%scripts\install_miniconda.bat"
if errorlevel 1 goto :abort
set "CONDA_ROOT="
call "%~dp0env.bat"
if not defined CONDA_ROOT goto :abort
:have_conda
echo [OK] conda: %CONDA_ROOT%
set "FROM=0"
if not "%~1"=="" set "FROM=%~1"
"%CONDA_ROOT%\python.exe" "%ROOT%scripts\install.py" --from %FROM%
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" type "%ROOT%scripts\msg\install_ok.txt"
if not "%RC%"=="0" type "%ROOT%scripts\msg\install_fail.txt"
pause
exit /b %RC%
:abort
>> "%ROOT%logs\install.log" echo [ERROR] %DATE% %TIME% Miniconda not found and not installed.
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
