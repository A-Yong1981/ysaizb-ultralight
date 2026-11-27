@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
title AI-Live-Hub Script Converter
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%HUB_DIR%"
python script_converter.py %*
