@echo off
chcp 65001 >nul
call "%~dp0..\env.bat"
title Ultralight-Train-Pipeline
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV%
cd /d "%ROOT%"
python "%ROOT%scripts\ultralight_pipeline.py" %*
