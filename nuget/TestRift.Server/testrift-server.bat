@echo off
REM Wrapper script to run testrift-server from NuGet package
REM This script locates the Python server files in the NuGet package and runs them

setlocal

REM Try to find Python
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Please install Python 3.10+ and ensure it's on PATH.
    exit /b 1
)

REM Get the directory where this script is located (NuGet package content directory)
set SCRIPT_DIR=%~dp0
set SERVER_DIR=%SCRIPT_DIR%server\testrift_server
set VENV_DIR=%SCRIPT_DIR%.venv
call :configure_venv_dir
set REQUIREMENTS_FILE=%SCRIPT_DIR%server\requirements.txt
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PYTHON_BOOTSTRAP=%PYTHON_EXE%"
set "REQUIREMENTS_MARKER=%VENV_DIR%\.requirements_installed"
call :set_bootstrap

REM Check if server files exist
if not exist "%SERVER_DIR%\__main__.py" (
    echo ERROR: TestRift Server files not found in NuGet package at: %SERVER_DIR%
    exit /b 1
)

call :ensure_venv
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

call :validate_venv
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

call :install_requirements
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

if "%TESTRIFT_BOOTSTRAP_TEST%"=="1" (
    echo Bootstrap test mode complete.
    exit /b 0
)

REM Add server directory to PYTHONPATH and run with venv Python
set PYTHONPATH=%SCRIPT_DIR%server;%PYTHONPATH%
"%PYTHON_EXE%" -m testrift_server %*

goto :EOF

:configure_venv_dir
if defined TESTRIFT_VENV_DIR (
    set "VENV_DIR=%TESTRIFT_VENV_DIR%"
    exit /b 0
)

echo %SCRIPT_DIR% | findstr /I \\.nuget\\packages\\ >nul
if errorlevel 1 exit /b 0

if defined LOCALAPPDATA (
    set "CACHE_ROOT=%LOCALAPPDATA%\testrift-server"
) else if defined USERPROFILE (
    set "CACHE_ROOT=%USERPROFILE%\.testrift-server"
) else (
    set "CACHE_ROOT=%TEMP%\testrift-server"
)
if not exist "%CACHE_ROOT%" mkdir "%CACHE_ROOT%" >nul 2>&1

for /f %%i in ('powershell -NoProfile -Command "$hash=[System.BitConverter]::ToString((New-Object System.Security.Cryptography.SHA1Managed).ComputeHash([System.Text.Encoding]::UTF8.GetBytes($env:SCRIPT_DIR))).Replace('-', '').Substring(0, 16); Write-Output $hash"') do set "SCRIPT_HASH=%%i"
if not defined SCRIPT_HASH set "SCRIPT_HASH=default"
set "VENV_DIR=%CACHE_ROOT%\%SCRIPT_HASH%"
exit /b 0

:ensure_venv
if exist "%PYTHON_EXE%" exit /b 0
echo Creating Python virtual environment...
call :create_venv
exit /b %ERRORLEVEL%

:validate_venv
call :set_bootstrap
"%PYTHON_BOOTSTRAP%" -m pip --version > "%TEMP%\pip-version.log" 2>&1
if %ERRORLEVEL% EQU 0 exit /b 0
echo Virtual environment is broken, recreating...
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
call :create_venv
if %ERRORLEVEL% NEQ 0 exit /b 1
if exist "%REQUIREMENTS_MARKER%" del "%REQUIREMENTS_MARKER%"
exit /b 0

:install_requirements
if exist "%REQUIREMENTS_MARKER%" exit /b 0
if not exist "%REQUIREMENTS_FILE%" exit /b 0
echo Installing/updating Python dependencies...
call :set_bootstrap
set "VENV_CMD_ARGS=-m pip install --upgrade pip"
call :run_venv_command pip-upgrade.log "Failed to upgrade pip"
if %ERRORLEVEL% NEQ 0 exit /b 1
set "VENV_CMD_ARGS=-m pip install -r ""%REQUIREMENTS_FILE%"""
call :run_venv_command pip-install.log "Failed to install dependencies"
if %ERRORLEVEL% NEQ 0 exit /b 1
type nul > "%REQUIREMENTS_MARKER%"
exit /b 0

:create_venv
python -m venv "%VENV_DIR%"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to create virtual environment
    exit /b 1
)
call :set_bootstrap
set "VENV_CMD_ARGS=-m ensurepip --default-pip"
call :run_venv_command ensurepip.log "Failed to install pip in virtual environment"
exit /b %ERRORLEVEL%

:set_bootstrap
set "PYTHON_BOOTSTRAP=%PYTHON_EXE%"
exit /b 0

:run_venv_command
set "LOG_PATH=%TEMP%\%~1"
"%PYTHON_BOOTSTRAP%" %VENV_CMD_ARGS% > "%LOG_PATH%" 2>&1
if %ERRORLEVEL% EQU 0 exit /b 0
echo ERROR: %~2
type "%LOG_PATH%"
exit /b 1

