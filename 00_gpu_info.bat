@echo off
chcp 65001 >nul
call "%~dp0env.bat"
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
nvidia-smi > "%ROOT%logs\gpu_info.txt" 2>&1
type "%ROOT%logs\gpu_info.txt"
echo.
echo Saved to logs\gpu_info.txt
pause
