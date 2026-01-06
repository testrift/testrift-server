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

REM Check if server files exist
if not exist "%SERVER_DIR%\__main__.py" (
    echo ERROR: TestRift Server files not found in NuGet package at: %SERVER_DIR%
    exit /b 1
)

REM Add server directory to PYTHONPATH and run
set PYTHONPATH=%SCRIPT_DIR%server;%PYTHONPATH%
python -m testrift_server %*

