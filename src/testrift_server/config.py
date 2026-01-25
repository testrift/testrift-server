"""
Configuration loading and management for TestRift server.
"""

import hashlib
import json
import logging
import os
import sys
import urllib.request
import urllib.error
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "testrift_server.yaml"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Set when loading config at import time.
CONFIG_PATH_USED = None


def parse_size_string(size_str):
    """Parse size string like '10MB', '1GB', '500KB' into bytes"""
    if isinstance(size_str, (int, float)):
        return int(size_str)

    if not isinstance(size_str, str):
        raise ValueError(f"Size must be a string or number, got: {type(size_str)}")

    size_str = size_str.strip().upper()

    # Define size units (ordered by length, longest first)
    units = [
        ('TB', 1024 * 1024 * 1024 * 1024),
        ('GB', 1024 * 1024 * 1024),
        ('MB', 1024 * 1024),
        ('KB', 1024),
        ('B', 1)
    ]

    # Extract number and unit
    for unit, multiplier in units:
        if size_str.endswith(unit):
            try:
                number = float(size_str[:-len(unit)])
                return int(number * multiplier)
            except ValueError:
                raise ValueError(f"Invalid size format: '{size_str}'")

    # If no unit specified, assume bytes
    try:
        return int(float(size_str))
    except ValueError:
        raise ValueError(f"Invalid size format: '{size_str}'. Use format like '10MB', '1GB', etc.")


def load_config(config_path=None):
    """Load server configuration from YAML file"""
    global CONFIG_PATH_USED
    try:
        cwd_default = (Path.cwd() / "testrift_server.yaml").resolve()
        env_override_used = False
        explicit_path_used = False

        if config_path is None:
            # Allow override via environment variable for tests and custom setups
            env_path = os.getenv("TESTRIFT_SERVER_YAML")
            if env_path:
                env_override_used = True
                config_path = Path(env_path)
            elif cwd_default.exists():
                # Prefer a config next to where the user runs the server from
                config_path = cwd_default
            else:
                config_path = DEFAULT_CONFIG_PATH
        else:
            explicit_path_used = True
            config_path = Path(config_path)

        if not config_path.is_absolute():
            # Treat relative paths as relative to the current working directory
            config_path = (Path.cwd() / config_path).resolve()
        config_path = config_path.resolve()
        CONFIG_PATH_USED = config_path
        # For the packaged default config, resolve relative paths against the working directory.
        config_dir = Path.cwd() if config_path == DEFAULT_CONFIG_PATH else config_path.parent

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Validate required sections
        if 'server' not in config:
            raise ValueError("Missing 'server' section in configuration")

        server_config = config['server']

        # Set defaults and validate
        config_data = {
            'port': server_config.get('port', 8080),
            'localhost_only': server_config.get('localhost_only', True)
        }

        # Validate port
        if not isinstance(config_data['port'], int) or not (1 <= config_data['port'] <= 65535):
            raise ValueError(f"Invalid port number: {config_data['port']}")

        # Validate localhost_only
        if not isinstance(config_data['localhost_only'], bool):
            raise ValueError(f"localhost_only must be a boolean, got: {type(config_data['localhost_only'])}")

        # Data directory configuration
        data_config = config.get('data', {})
        data_dir_value = data_config.get('directory', 'data')
        data_dir_path = Path(data_dir_value)
        if not data_dir_path.is_absolute():
            data_dir_path = (config_dir / data_dir_path).resolve()
        config_data['data_dir'] = data_dir_path
        config_data['default_retention_days'] = data_config.get('default_retention_days', 7)

        # Validate retention days
        if config_data['default_retention_days'] is not None:
            if not isinstance(config_data['default_retention_days'], int) or config_data['default_retention_days'] < 0:
                raise ValueError(f"data.default_retention_days must be a non-negative integer or null, got: {config_data['default_retention_days']}")

        # Attachment configuration
        attachment_config = config.get('attachments', {})
        config_data['attachments_enabled'] = attachment_config.get('enabled', True)
        max_size_str = attachment_config.get('max_size', '10MB')  # Default to 10MB
        config_data['attachment_max_size'] = parse_size_string(max_size_str)

        # Validate attachment settings
        if not isinstance(config_data['attachments_enabled'], bool):
            raise ValueError(f"attachments.enabled must be a boolean, got: {type(config_data['attachments_enabled'])}")

        if not isinstance(config_data['attachment_max_size'], int) or config_data['attachment_max_size'] <= 0:
            raise ValueError(f"attachments.max_size_bytes must be a positive integer, got: {config_data['attachment_max_size']}")

        return config_data

    except FileNotFoundError:
        # If the user explicitly requested a config (via env var or arg) we must fail hard.
        if env_override_used or explicit_path_used:
            logger.error(f" Configuration file '{config_path}' not found.")
            sys.exit(1)

        logger.warning(f" Configuration file '{config_path}' not found. Using defaults.")
        CONFIG_PATH_USED = None
        return {
            'port': 8080,
            'localhost_only': True,
            'default_retention_days': 7,
            'data_dir': (Path.cwd() / "data").resolve(),
            'attachments_enabled': True,
            'attachment_max_size': parse_size_string('10MB')
        }
    except yaml.YAMLError as e:
        logger.error(f"Error parsing configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        sys.exit(1)


# --- Server identity / config fingerprint ---

def get_config_fingerprint(config: dict) -> dict:
    """Return a stable, JSON-serializable representation of config for hashing/comparison."""
    return {
        "port": int(config["port"]),
        "localhost_only": bool(config["localhost_only"]),
        "data_dir": str(Path(config["data_dir"]).resolve()),
        "default_retention_days": config["default_retention_days"],
        "attachments_enabled": bool(config["attachments_enabled"]),
        "attachment_max_size": int(config["attachment_max_size"]),
    }


def get_config_hash(config: dict) -> str:
    """Compute a hash of the config for comparing configurations."""
    payload = json.dumps(get_config_fingerprint(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_running_server_info(port: int) -> dict | None:
    """Return server-info JSON if a TestRift server is running on localhost:port, else None.

    Raises RuntimeError if something is listening on the port but is not a compatible TestRift server.
    """
    url = f"http://127.0.0.1:{port}/api/server-info"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                raise RuntimeError(f"Port {port} is in use but did not return 200 from /api/server-info (status={resp.status}).")
            if "application/json" not in content_type.lower():
                raise RuntimeError(f"Port {port} is in use but /api/server-info did not return JSON (Content-Type={content_type}).")
            info = json.loads(raw)
            if info.get("service") != "testrift-server":
                raise RuntimeError(f"Port {port} is in use but /api/server-info is not a TestRift server.")
            return info
    except urllib.error.HTTPError as e:
        # Something is responding on that port but not our expected endpoint.
        raise RuntimeError(f"Port {port} is in use but /api/server-info returned HTTP {e.code}.")
    except urllib.error.URLError:
        # Connection refused / no listener / timeout -> treat as not running.
        return None


def request_running_server_shutdown(port: int, running_hash: str) -> bool:
    """Ask a running TestRift server on localhost:port to shut down.

    Returns True if the request returned HTTP 200, False otherwise.
    """
    url = f"http://127.0.0.1:{port}/api/admin/shutdown"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-TestRift-Config-Hash": running_hash,
        },
        data=json.dumps({"config_hash": running_hash}).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False


# Load configuration at module import time
CONFIG = load_config()

# --- Configuration Variables ---
PORT = CONFIG['port']
DATA_DIR = CONFIG['data_dir']
DEFAULT_RETENTION_DAYS = CONFIG['default_retention_days']
LOCALHOST_ONLY = CONFIG['localhost_only']
ATTACHMENTS_ENABLED = CONFIG['attachments_enabled']
ATTACHMENT_MAX_SIZE = CONFIG['attachment_max_size']
