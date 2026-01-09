#!/bin/sh
# Wrapper script to run testrift-server from NuGet package
# This script locates the Python server files in the NuGet package and runs them

# Get the directory where this script is located (NuGet package content directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server/testrift_server"
VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS_FILE="$SCRIPT_DIR/server/requirements.txt"

# Check if Python is available
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
    echo "ERROR: Python not found. Please install Python 3.10+ and ensure it's on PATH." >&2
    exit 1
fi

# Prefer python3, fallback to python
PYTHON_CMD="python3"
if ! command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

# Check if server files exist
if [ ! -f "$SERVER_DIR/__main__.py" ]; then
    echo "ERROR: TestRift Server files not found in NuGet package at: $SERVER_DIR" >&2
    exit 1
fi

# Create venv if it doesn't exist
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "Creating Python virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create virtual environment" >&2
        exit 1
    fi
fi

# Check if requirements need updating (compare timestamps)
NEEDS_UPDATE=0
if [ ! -f "$VENV_DIR/.requirements_installed" ]; then
    NEEDS_UPDATE=1
elif [ -f "$REQUIREMENTS_FILE" ] && [ "$REQUIREMENTS_FILE" -nt "$VENV_DIR/.requirements_installed" ]; then
    NEEDS_UPDATE=1
fi

# Install/update requirements if needed
if [ $NEEDS_UPDATE -eq 1 ] && [ -f "$REQUIREMENTS_FILE" ]; then
    echo "Installing/updating Python dependencies..."
    "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
    "$VENV_DIR/bin/python" -m pip install -r "$REQUIREMENTS_FILE"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to install dependencies" >&2
        exit 1
    fi
    # Create marker file
    touch "$VENV_DIR/.requirements_installed"
fi

# Add server directory to PYTHONPATH and run with venv Python
export PYTHONPATH="$SCRIPT_DIR/server:$PYTHONPATH"
exec "$VENV_DIR/bin/python" -m testrift_server "$@"

