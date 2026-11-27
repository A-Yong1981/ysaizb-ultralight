@echo off
setlocal
call "%~dp0..\env.bat"
if defined CONDA_ROOT goto :already
set "TARGET=%USERPROFILE%\miniconda3"
set "INSTALLER=%TEMP%\Miniconda3-latest-Windows-x86_64.exe"
type "%~dp0msg\miniconda_start.txt"
echo.
echo   URL    : %MINICONDA_URL%
echo   TARGET : %TARGET%
echo.
if exist "%TARGET%\" goto :bad_target
if exist "%INSTALLER%" del /q "%INSTALLER%" >nul 2>nul
where curl.exe >nul 2>nul
if errorlevel 1 goto :ps_download
curl.exe -L --fail --retry 3 --retry-delay 3 -o "%INSTALLER%" "%MINICONDA_URL%"
if errorlevel 1 goto :ps_download
goto :check_file
:ps_download
echo [INFO] curl download failed, retrying with PowerShell ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%MINICONDA_URL%' -OutFile '%INSTALLER%'"
if errorlevel 1 goto :dl_fail
:check_file
if not exist "%INSTALLER%" goto :dl_fail
for %%S in ("%INSTALLER%") do if %%~zS LSS 40000000 goto :dl_fail
echo [INFO] Installing Miniconda silently, please wait 2-5 minutes ...
start "" /wait "%INSTALLER%" /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=%TARGET%
if not exist "%TARGET%\Scripts\activate.bat" goto :inst_fail
del /q "%INSTALLER%" >nul 2>nul
echo [OK] Miniconda installed: %TARGET%
exit /b 0
:already
echo [OK] conda already installed: %CONDA_ROOT%
exit /b 0
:bad_target
echo [ERROR] Folder %TARGET% already exists but is not a working conda install.
echo         Rename or delete that folder, then run 01_install.bat again.
exit /b 1
:dl_fail
type "%~dp0msg\miniconda_dl_fail.txt"
exit /b 1
:inst_fail
type "%~dp0msg\miniconda_inst_fail.txt"
exit /b 1
