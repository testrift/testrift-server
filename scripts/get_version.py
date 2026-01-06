#!/usr/bin/env python3
"""Extract version from VERSION file."""

import sys
from pathlib import Path

def get_version():
    """Get version from VERSION file."""
    repo_root = Path(__file__).parent.parent
    version_path = repo_root / "VERSION"

    if not version_path.exists():
        raise FileNotFoundError(f"VERSION file not found at {version_path}")

    version = version_path.read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("VERSION file is empty")

    return version

if __name__ == "__main__":
    try:
        print(get_version())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

