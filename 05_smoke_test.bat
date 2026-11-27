@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
title ysaizb-ultralight - smoke + regression tests
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
if not defined CONDA_ROOT goto :noconda
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%HUB_DIR%"
python tests\test_smoke.py > "%ROOT%logs\smoke_test.log" 2>&1
set "RC1=%ERRORLEVEL%"
echo. >> "%ROOT%logs\smoke_test.log"
python tests\test_regressions.py >> "%ROOT%logs\smoke_test.log" 2>&1
set "RC2=%ERRORLEVEL%"
type "%ROOT%logs\smoke_test.log"
echo.
echo smoke exit %RC1% / regression exit %RC2% - saved to logs\smoke_test.log
pause
if not "%RC1%"=="0" exit /b 1
if not "%RC2%"=="0" exit /b 1
exit /b 0
:noconda
type "%ROOT%scripts\msg\no_conda_manual.txt"
pause
exit /b 1
