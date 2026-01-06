# Wrapper script to run testrift-server from NuGet package
# This script locates the Python server files in the NuGet package and runs them

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)

# Get the directory where this script is located (NuGet package content directory)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerDir = Join-Path $ScriptDir "server\testrift_server"

# Check if Python is available
$pythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $pythonCmd = "python3"
} else {
    Write-Error "ERROR: Python not found. Please install Python 3.10+ and ensure it's on PATH."
    exit 1
}

# Check if server files exist
$mainPy = Join-Path $ServerDir "__main__.py"
if (-not (Test-Path $mainPy)) {
    Write-Error "ERROR: TestRift Server files not found in NuGet package at: $ServerDir"
    exit 1
}

# Add server directory to PYTHONPATH and run
$env:PYTHONPATH = "$ScriptDir\server;$env:PYTHONPATH"
& $pythonCmd -m testrift_server $Arguments

