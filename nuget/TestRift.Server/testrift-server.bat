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
set REQUIREMENTS_FILE=%SCRIPT_DIR%server\requirements.txt

REM Check if server files exist
if not exist "%SERVER_DIR%\__main__.py" (
    echo ERROR: TestRift Server files not found in NuGet package at: %SERVER_DIR%
    exit /b 1
)

REM Create venv if it doesn't exist
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv "%VENV_DIR%"
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: Failed to create virtual environment
        exit /b 1
    )
)

REM Check if requirements need updating (compare timestamps)
set NEEDS_UPDATE=0
if not exist "%VENV_DIR%\.requirements_installed" set NEEDS_UPDATE=1
if exist "%REQUIREMENTS_FILE%" (
    if exist "%VENV_DIR%\.requirements_installed" (
        for /f %%i in ('powershell -command "if ((Get-Item '%REQUIREMENTS_FILE%').LastWriteTime -gt (Get-Item '%VENV_DIR%\.requirements_installed').LastWriteTime) { Write-Output '1' } else { Write-Output '0' }"') do set NEEDS_UPDATE=%%i
    )
)

REM Install/update requirements if needed
if %NEEDS_UPDATE%==1 (
    if exist "%REQUIREMENTS_FILE%" (
        echo Installing/updating Python dependencies...
        "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
        "%VENV_DIR%\Scripts\python.exe" -m pip install -r "%REQUIREMENTS_FILE%"
        if %ERRORLEVEL% NEQ 0 (
            echo ERROR: Failed to install dependencies
            exit /b 1
        )
        REM Create marker file
        type nul > "%VENV_DIR%\.requirements_installed"
    )
)

REM Add server directory to PYTHONPATH and run with venv Python
set PYTHONPATH=%SCRIPT_DIR%server;%PYTHONPATH%
"%VENV_DIR%\Scripts\python.exe" -m testrift_server %*

