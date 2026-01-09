#!/bin/sh
# Wrapper script to run testrift-server from NuGet package
# This script locates the Python server files in the NuGet package and runs them

# Get the directory where this script is located (NuGet package content directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server/testrift_server"
VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS_FILE="$SCRIPT_DIR/server/requirements.txt"
VENV_PY="$VENV_DIR/bin/python"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements_installed"

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

ensure_venv() {
    [ -f "$VENV_PY" ] && return 0
    create_venv
}

create_venv() {
    echo "Creating Python virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR" || {
        echo "ERROR: Failed to create virtual environment" >&2
        return 1
    }
    ensure_pip
}

ensure_pip() {
    echo "Ensuring pip is installed..."
    "$VENV_PY" -m ensurepip --default-pip >/dev/null 2>&1 || {
        echo "ERROR: Failed to install pip in virtual environment" >&2
        return 1
    }
}

validate_venv() {
    if "$VENV_PY" -m pip --version >/dev/null 2>&1; then
        return 0
    fi
    echo "Virtual environment is broken, recreating..."
    rm -rf "$VENV_DIR"
    create_venv || return 1
    rm -f "$REQUIREMENTS_MARKER"
}

install_requirements() {
    [ -f "$REQUIREMENTS_FILE" ] || return 0
    if [ -f "$REQUIREMENTS_MARKER" ] && [ "$REQUIREMENTS_FILE" -ot "$REQUIREMENTS_MARKER" ]; then
        return 0
    fi

    echo "Installing/updating Python dependencies..."
    "$VENV_PY" -m pip install --upgrade pip || {
        echo "ERROR: Failed to upgrade pip" >&2
        return 1
    }
    "$VENV_PY" -m pip install -r "$REQUIREMENTS_FILE" || {
        echo "ERROR: Failed to install dependencies" >&2
        return 1
    }
    touch "$REQUIREMENTS_MARKER"
}

ensure_venv || exit 1
validate_venv || exit 1
install_requirements || exit 1

if [ "${TESTRIFT_BOOTSTRAP_TEST:-}" = "1" ]; then
    echo "Bootstrap test mode complete."
    exit 0
fi

# Add server directory to PYTHONPATH and run with venv Python
export PYTHONPATH="$SCRIPT_DIR/server:$PYTHONPATH"
exec "$VENV_PY" -m testrift_server "$@"

