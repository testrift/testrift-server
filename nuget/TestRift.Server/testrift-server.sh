#!/bin/sh
# Wrapper script to run testrift-server from NuGet package
# This script locates the Python server files in the NuGet package and runs them

# Get the directory where this script is located (NuGet package content directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server/testrift_server"

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

# Add server directory to PYTHONPATH and run
export PYTHONPATH="$SCRIPT_DIR/server:$PYTHONPATH"
exec "$PYTHON_CMD" -m testrift_server "$@"

