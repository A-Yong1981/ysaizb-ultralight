@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight - API key test
if not defined CONDA_ROOT goto :noconda
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
python "%ROOT%scripts\test_keys.py"
echo.
echo Saved to logs\key_test.log
pause
exit /b 0
:noconda
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
