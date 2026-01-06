"""
Invoke tasks for the server.
"""

# pyright: reportMissingImports=false
try:
    from invoke import Collection, task
except ImportError as e:  # pragma: no cover
    raise RuntimeError(
        "Invoke is required to run these developer tasks. Install it with: pip install invoke"
    ) from e
import shutil
from pathlib import Path
import sys


@task
def test(c):
    """Run the server test suite (pytest)."""
    server_dir = Path(__file__).parent
    with c.cd(str(server_dir)):
        # Install test deps if present (no-op if already installed)
        req_dev = server_dir / "tests" / "requirements.txt"
        if req_dev.exists():
            c.run(f"{sys.executable} -m pip install -r tests/requirements.txt")
        c.run(f"{sys.executable} -m pytest")


@task
def start(c):
    """Start the testrift server.

    Args:
        config: Path to config file
    """
    server_dir = Path(__file__).parent
    # Use unbuffered output so server logs stream immediately when running under invoke.
    cmd_parts = [sys.executable, "-u", "-m", "testrift_server"]

    print(f"Starting server from {server_dir}...")
    # Use c.cd to change directory instead of passing cwd (not supported in invoke 2.x)
    with c.cd(str(server_dir)):
        c.run(" ".join(cmd_parts), env={"PYTHONUNBUFFERED": "1"})


@task
def clean(c):
    """Remove local build artifacts (dist/, build/, *.egg-info/)."""
    server_dir = Path(__file__).parent
    for p in [server_dir / "dist", server_dir / "build"]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for p in server_dir.glob("*.egg-info"):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


@task(pre=[clean])
def build(c):
    """Build the pip distribution (sdist + wheel). Requires: `pip install build`."""
    server_dir = Path(__file__).parent
    with c.cd(str(server_dir)):
        c.run(f"{sys.executable} -m build")


@task(pre=[build])
def publish(c, repository="pypi"):
    """Publish the pip distribution using twine. Requires: `pip install twine`.

    Args:
        repository: Twine repository name (default: pypi). Common values: pypi, testpypi.
    """
    server_dir = Path(__file__).parent
    with c.cd(str(server_dir)):
        c.run(f"{sys.executable} -m twine upload --repository {repository} dist/*")

