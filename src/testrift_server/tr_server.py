import asyncio
import argparse
import hashlib
import json
import os
import re
import shutil
import time
import uuid
import urllib.parse
import zipfile
import aiofiles
import yaml
import sys
import urllib.request
import urllib.error
from aiohttp import web, WSMsgType, MultipartReader
from datetime import datetime, timedelta, UTC
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from . import database

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "testrift_server.yaml"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
GROUP_HASH_LENGTH = 16

# Set when loading config at import time.
CONFIG_PATH_USED = None

# --- Configuration Loading ---

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
            print(f"ERROR: Configuration file '{config_path}' not found.")
            sys.exit(1)

        print(f"Warning: Configuration file '{config_path}' not found. Using defaults.")
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
        print(f"Error parsing configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

# Load configuration
CONFIG = load_config()

# --- Configuration Variables ---
PORT = CONFIG['port']
DATA_DIR = CONFIG['data_dir']
DEFAULT_RETENTION_DAYS = CONFIG['default_retention_days']
LOCALHOST_ONLY = CONFIG['localhost_only']
ATTACHMENTS_ENABLED = CONFIG['attachments_enabled']
ATTACHMENT_MAX_SIZE = CONFIG['attachment_max_size']

# --- Server identity / config fingerprint (used to detect already-running server) ---

def _testrift_config_fingerprint(config: dict) -> dict:
    """Return a stable, JSON-serializable representation of config for hashing/comparison."""
    return {
        "port": int(config["port"]),
        "localhost_only": bool(config["localhost_only"]),
        "data_dir": str(Path(config["data_dir"]).resolve()),
        "default_retention_days": config["default_retention_days"],
        "attachments_enabled": bool(config["attachments_enabled"]),
        "attachment_max_size": int(config["attachment_max_size"]),
    }


def _testrift_config_hash(config: dict) -> str:
    payload = json.dumps(_testrift_config_fingerprint(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_running_server_info(port: int) -> dict | None:
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


def _request_running_server_shutdown(port: int, running_hash: str) -> bool:
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

# --- Global state ---
ui_clients = set()  # UI WebSocket clients for live updates

app = web.Application()

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

def render_template(template_name, **context):
    template = env.get_template(template_name)
    return template.render(**context)

# --- Logging helper ---
def log_event(event: str, **fields):
    record = {"event": event, **fields, "ts": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"}
    print(json.dumps(record))

# --- Utility functions ---

def now_utc_iso():
    return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"


def parse_iso(dtstr):
    return datetime.fromisoformat(dtstr.replace("Z", ""))


def get_run_path(run_id):
    # Store runs in data/<run_id>/
    return DATA_DIR / run_id


def get_run_meta_path(run_id):
    return get_run_path(run_id) / "meta.json"


def sanitize_filename(filename):
    """Sanitize filename by replacing invalid characters with safe alternatives"""
    if not filename or not isinstance(filename, str):
        return "invalid_filename"

    # Remove any path separators and directory traversal attempts
    filename = filename.replace('/', '_').replace('\\', '_')
    filename = re.sub(r'\.\.+', '_', filename)  # Remove .. sequences

    # Replace invalid characters for Windows file paths
    # Note: quotes are valid in file contents but not in filenames, so we replace them
    invalid_chars = '<>:"|?*[]' + chr(0)  # Added null character
    sanitized = filename
    for char in invalid_chars:
        if char == '"':
            sanitized = sanitized.replace(char, '_QUOTE_')
        elif char == chr(0):
            sanitized = sanitized.replace(char, '_NULL_')
        else:
            sanitized = sanitized.replace(char, '_')

    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip('. ')

    # Ensure filename is not empty after sanitization
    if not sanitized or sanitized in ['.', '..', 'CON', 'PRN', 'AUX', 'NUL']:
        return "sanitized_filename"

    # Limit filename length
    if len(sanitized) > 255:
        sanitized = sanitized[:255]

    return sanitized


def validate_run_id(run_id):
    """Validate that run_id is safe and doesn't contain path traversal"""
    if not run_id or not isinstance(run_id, str):
        return False

    # Check for path traversal attempts
    if '..' in run_id or '/' in run_id or '\\' in run_id:
        return False

    # Check for dangerous characters
    if any(char in run_id for char in '<>:"|?*[]'):
        return False

    # Limit length
    if len(run_id) > 100:
        return False

    return True


def validate_custom_run_id(run_id):
    """
    Validate a custom run ID provided by the client.
    - Must be URL-safe (can use percent encoding)
    - Raw slash character not allowed
    - Returns (is_valid, error_message)
    """
    if not run_id or not isinstance(run_id, str):
        return False, "Run ID must be a non-empty string"

    # Check for raw slash (not allowed)
    if '/' in run_id:
        return False, "Run ID cannot contain raw slash character (use percent encoding %2F if needed)"

    # Check for backslash (not allowed)
    if '\\' in run_id:
        return False, "Run ID cannot contain backslash character"

    # Check for path traversal attempts
    if '..' in run_id:
        return False, "Run ID cannot contain '..'"

    # Validate URL-safe characters and percent encoding
    # Allow: A-Z, a-z, 0-9, -, _, ., ~, and percent-encoded sequences (%XX where XX is hex)
    # First, validate that any % is followed by exactly two hex digits
    if '%' in run_id:
        # Check that all percent-encoded sequences are valid (%XX where XX is hex)
        percent_pattern = re.compile(r'%[0-9A-Fa-f]{2}')
        # Replace all valid percent-encoded sequences with a placeholder
        temp = percent_pattern.sub('_', run_id)
        # If there's still a % left, it's invalid
        if '%' in temp:
            return False, "Run ID contains invalid percent encoding (must be %XX where XX is hexadecimal)"
        # After removing valid percent-encoded sequences, check remaining characters
        remaining = temp
    else:
        remaining = run_id

    # Check that remaining characters are URL-safe
    # URL-safe characters: A-Z, a-z, 0-9, -, _, ., ~
    url_safe_pattern = re.compile(r'^[A-Za-z0-9\-_.~]+$')
    if not url_safe_pattern.match(remaining):
        return False, "Run ID contains invalid characters (must be URL-safe or percent-encoded)"

    # Limit length (reasonable limit for URL path segments)
    if len(run_id) > 200:
        return False, "Run ID is too long (maximum 200 characters)"

    return True, None


def validate_test_case_id(test_case_id):
    """Validate that test_case_id is safe"""
    if not test_case_id or not isinstance(test_case_id, str):
        return False

    # Check for path traversal attempts
    if '..' in test_case_id or '/' in test_case_id or '\\' in test_case_id:
        return False

    # Limit length
    if len(test_case_id) > 200:
        return False

    return True


def validate_group_hash_value(group_hash):
    """Ensure group hash only contains safe hex characters."""
    if not group_hash or not isinstance(group_hash, str):
        return False
    return re.fullmatch(r"[0-9a-fA-F]{6,64}", group_hash) is not None


def normalize_group_payload(group_data):
    """Return canonical group dict with 'name' and dict metadata."""
    if not isinstance(group_data, dict):
        return None

    name = str(group_data.get("name", "") or "").strip()
    if not name:
        return None

    raw_metadata = group_data.get("metadata") or {}
    normalized_metadata = {}

    if isinstance(raw_metadata, dict):
        items = raw_metadata.items()
    elif isinstance(raw_metadata, list):
        items = []
        for entry in raw_metadata:
            if isinstance(entry, dict):
                items.append((entry.get("name"), entry))
    else:
        items = []

    for key, meta_value in items:
        key_str = str(key or "").strip()
        if not key_str:
            continue

        value = ""
        url = None
        if isinstance(meta_value, dict):
            value = str(meta_value.get("value", "") or "")
            url_raw = meta_value.get("url")
            url = str(url_raw) if url_raw is not None else None
        else:
            value = str(meta_value or "")

        normalized_metadata[key_str] = {"value": value, "url": url}

    return {"name": name, "metadata": normalized_metadata}


def compute_group_hash(group_data):
    """Compute deterministic hash for normalized group payload."""
    normalized = normalize_group_payload(group_data)
    if not normalized:
        return None

    metadata_items = []
    for key, meta_value in (normalized.get("metadata") or {}).items():
        metadata_items.append((key, meta_value.get("value", "")))

    metadata_items.sort(key=lambda item: (item[0].lower(), item[1]))
    canonical_payload = {
        "name": normalized["name"],
        "metadata": metadata_items
    }
    digest = hashlib.sha256(
        json.dumps(canonical_payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:GROUP_HASH_LENGTH]

def get_case_log_path(run_id, test_case_id):
    sanitized_id = sanitize_filename(test_case_id)
    return get_run_path(run_id) / f"{sanitized_id}.jsonl"

def get_case_stack_path(run_id, test_case_id):
    log_path = get_case_log_path(run_id, test_case_id)
    return log_path.with_suffix(".stack.jsonl")

def get_attachments_dir(run_id, test_case_id):
    """Get the attachments directory for a specific test case"""
    sanitized_id = sanitize_filename(test_case_id)
    return get_run_path(run_id) / "attachments" / sanitized_id

def get_attachment_path(run_id, test_case_id, filename):
    """Get the full path for a specific attachment"""
    sanitized_filename = sanitize_filename(filename)
    return get_attachments_dir(run_id, test_case_id) / sanitized_filename


def read_jsonl(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def load_run_from_disk(run_id):
    meta_path = get_run_meta_path(run_id)
    if not meta_path.exists():
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return TestRunData.from_dict(run_id, meta)


# --- HTTP Handlers ---


async def build_run_index_entries(runs_from_db):
    runs_index = []
    for run in runs_from_db:
        run_id = run['run_id']

        # Get user metadata for this run (already in correct format)
        user_metadata = await database.db.get_user_metadata_for_run(run_id)
        group_metadata = await database.db.get_group_metadata_for_run(run_id)
        group_name = run.get('group_name')
        group_hash = run.get('group_hash')
        group_info = None
        if group_name or group_hash or group_metadata:
            group_info = {
                'name': group_name,
                'hash': group_hash,
                'metadata': group_metadata
            }

        # Check if files exist on disk
        run_path = get_run_path(run_id)
        files_exist = run_path.exists()

        # Build run info for template
        run_info = {
            'run_id': run_id,
            'run_name': run.get('run_name'),
            'status': run.get('status'),
            'start_time': run.get('start_time'),
            'end_time': run.get('end_time'),
            'retention_days': run.get('retention_days'),
            'passed_count': run.get('passed_count', 0),
            'failed_count': run.get('failed_count', 0),
            'skipped_count': run.get('skipped_count', 0),
            'error_count': run.get('error_count', 0),
            'user_metadata': user_metadata,
            'group': group_info,
            'group_hash': group_hash,  # Also include at top level for easy access
            'files_exist': files_exist
        }

        # Apply test summary logic for finished runs with error precedence
        if run_info['status'] and run_info['status'].lower() == 'finished':
            if run_info['error_count'] > 0:
                run_info['status'] = 'Error'
            elif run_info['failed_count'] > 0:
                run_info['status'] = 'Failed'
            elif run_info['passed_count'] > 0:
                run_info['status'] = 'Passed'
            # Keep 'Finished' as fallback if no test results
        elif run_info['status'] and run_info['status'].lower() == 'aborted':
            run_info['status'] = 'Aborted'

        runs_index.append(run_info)

    # Sort by start time descending (database should already sort, but ensure it)
    runs_index.sort(key=lambda r: r.get("start_time") or "", reverse=True)

    return runs_index


async def index_handler(request):
    # Serve Test Runs index with embedded JavaScript for live updates

    # Get all runs from database
    runs_from_db = await database.db.get_test_runs(limit=1000)
    runs_index = await build_run_index_entries(runs_from_db)

    html = render_template('index.html', runs_index=runs_index, group_context=None)

    # Add cache control headers to prevent caching of live data
    headers = {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }

    return web.Response(text=html, content_type="text/html", headers=headers)


async def group_runs_handler(request):
    group_hash = request.match_info.get("group_hash")
    if not validate_group_hash_value(group_hash):
        return web.Response(status=400, text="Invalid group hash")

    runs_from_db = await database.db.get_test_runs(limit=1000, group_hash=group_hash)
    if not runs_from_db:
        return web.Response(status=404, text="No runs found for this group")

    runs_index = await build_run_index_entries(runs_from_db)
    first_run_id = runs_from_db[0]['run_id']
    group_metadata = await database.db.get_group_metadata_for_run(first_run_id)
    group_name = runs_from_db[0].get('group_name')
    group_context = {
        "hash": group_hash,
        "name": group_name,
        "metadata": group_metadata
    }

    html = render_template('index.html', runs_index=runs_index, group_context=group_context)

    headers = {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }
    return web.Response(text=html, content_type="text/html", headers=headers)


async def test_run_index_handler(request):
    # Serve the test run page, embed all test case metadata, and logs summary (not full logs yet)
    run_id = request.match_info["run_id"]

    # Validate run_id to prevent path traversal
    if not validate_run_id(run_id):
        return web.Response(status=400, text="Invalid run ID")

    # First try to get the run from WebSocket server's in-memory data
    ws_server = request.app["ws_server"]
    run = ws_server.test_runs.get(run_id)
    live_run = False

    group_info = None

    if run:
        # Use in-memory data, but only consider it "live" if it's actually running
        live_run = (run.status == "running")
        test_cases_dict = {tc_id: tc.to_dict() for tc_id, tc in run.test_cases.items()}

        # Count test results for multiple badges
        passed_count = 0
        failed_count = 0
        skipped_count = 0
        error_count = 0

        for tc in run.test_cases.values():
            status_val = tc.status.lower()
            if status_val in ['passed', 'failed', 'skipped', 'aborted', 'error']:
                if status_val == 'passed':
                    passed_count += 1
                elif status_val == 'failed':
                    failed_count += 1
                elif status_val == 'skipped':
                    skipped_count += 1
                elif status_val == 'aborted':
                    # Aborted tests are counted as failed for display purposes
                    failed_count += 1
                elif status_val == 'error':
                    error_count += 1

        # Determine status display with error precedence
        if run.status.lower() == 'finished':
            if error_count > 0:
                status = 'Error'
            elif failed_count > 0:
                status = 'Failed'
            elif passed_count > 0:
                status = 'Passed'
            else:
                status = 'Finished'  # Fallback if no test results
        elif run.status.lower() == 'aborted':
            status = 'Aborted'
        else:
            status = run.status

        start_time = run.start_time
        end_time = run.end_time
        user_metadata = run.user_metadata
        retention_days = run.retention_days
        run_name = run.run_name
        if run.group or run.group_hash:
            group_info = {
                "name": run.group.get("name") if run.group else None,
                "hash": run.group_hash,
                "metadata": (run.group or {}).get("metadata", {}) if run.group else {}
            }
    else:
        # Fall back to database for completed runs
        run_data = await database.db.get_test_run_by_id(run_id)

        if not run_data:
            return web.Response(status=404, text="Run not found")

        # Get test cases from database
        test_cases_list = await database.db.get_test_cases_for_run(run_id)

        # Get user metadata from database (already in correct format)
        user_metadata = await database.db.get_user_metadata_for_run(run_id)
        group_metadata = await database.db.get_group_metadata_for_run(run_id)
        group_name = run_data.get("group_name")
        group_hash = run_data.get("group_hash")
        if group_name or group_hash or group_metadata:
            group_info = {
                "name": group_name,
                "hash": group_hash,
                "metadata": group_metadata
            }

        # Convert test cases list to dict format expected by template
        test_cases_dict = {}
        for tc in test_cases_list:
            test_cases_dict[tc['test_case_id']] = {
                'id': tc['test_case_id'],
                'status': tc['status'],
                'start_time': tc.get('start_time'),
                'end_time': tc.get('end_time')
            }

        # Get run_name from database
        run_name = run_data.get('run_name')

        # Count test results from database data
        passed_count = run_data.get('passed_count', 0)
        failed_count = run_data.get('failed_count', 0)
        skipped_count = run_data.get('skipped_count', 0)
        error_count = run_data.get('error_count', 0)

        # Determine status display with error precedence
        run_status = run_data.get("status")
        if run_status and run_status.lower() == 'finished':
            if error_count > 0:
                status = 'Error'
            elif failed_count > 0:
                status = 'Failed'
            elif passed_count > 0:
                status = 'Passed'
            else:
                status = 'Finished'  # Fallback if no test results
        elif run_status and run_status.lower() == 'aborted':
            status = 'Aborted'
        else:
            status = run_status

        start_time = run_data.get("start_time")
        end_time = run_data.get("end_time")
        retention_days = run_data.get("retention_days")

    # Check if files exist on disk (to determine if run has been cleaned up)
    run_path = get_run_path(run_id)
    files_exist = run_path.exists()

    html = render_template(
        'test_run.html',
        run_id=run_id,
        run_name=run_name,
        status=status,
        start_time=start_time,
        end_time=end_time,
        user_metadata=user_metadata,
        group=group_info,
        retention_days=retention_days,
        test_cases=test_cases_dict,
        live_run=live_run,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        error_count=error_count,
        files_exist=files_exist,
        server_mode=True
    )

    # Add cache control headers to prevent caching of live runs
    headers = {}
    if live_run:
        headers.update({
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        })

    return web.Response(text=html, content_type="text/html", headers=headers)

async def test_case_log_handler(request):
    run_id = request.match_info["run_id"]
    test_case_id = request.match_info["test_case_id"]

    # Validate run_id and test_case_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(test_case_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    ws_server = request.app["ws_server"]
    run = ws_server.test_runs.get(run_id)
    live_run = False

    if run:
        # Consider it live if the run is running OR if the specific test case is running
        live_run = (run.status == "running")
        test_case = run.test_cases.get(test_case_id)
        if test_case is None:
            return web.Response(status=404, text="Test case not found")
        # Also consider it live if this specific test case is running
        if test_case.status == "running":
            live_run = True
        print(f"Live run detection - Run in memory: {run_id}, Run status: {run.status}, Test case status: {test_case.status}, Live: {live_run}")
    else:
        run = TestRunData.load_from_disk(run_id)
        if run is None:
            return web.Response(status=404, text="Run not found")
        test_case = run.test_cases.get(test_case_id)
        if test_case is None:
            return web.Response(status=404, text="Test case not found")
        if not test_case.load_log_from_disk():
            return web.Response(status=404, text="Log not found")

        # Check if this test case is still running by checking if it has recent log activity
        # If the test case status is "running" and we have recent logs, consider it live
        if test_case.status == "running":
            # Check if there are recent logs (within last 30 seconds)
            import time
            current_time = time.time()
            recent_logs = False
            for log_entry in test_case.logs:
                try:
                    log_time = datetime.fromisoformat(log_entry.get("timestamp", "").replace("Z", "+00:00")).timestamp()
                    if current_time - log_time < 30:  # Within last 30 seconds
                        recent_logs = True
                        break
                except:
                    pass

            if recent_logs:
                live_run = True
                print(f"Live run detection - Run from disk: {run_id}, Test case status: {test_case.status}, Recent logs: {recent_logs}, Live: {live_run}")
            else:
                print(f"Live run detection - Run from disk: {run_id}, Test case status: {test_case.status}, No recent logs, Live: {live_run}")
        else:
            print(f"Live run detection - Run from disk: {run_id}, Test case status: {test_case.status}, Live: {live_run}")

    # Get group_hash for history feature
    group_hash = None
    run_dict = run.to_dict()
    if hasattr(run, 'group_hash'):
        group_hash = run.group_hash
    elif 'group_hash' in run_dict:
        group_hash = run_dict.get('group_hash')

    html = render_template(
        'test_case_log.html',
        run_id=run_id,
        run_name=run.run_name,
        test_case_id=test_case_id,
        run=run,
        run_meta=run_dict,
        test_case=test_case,
        test_case_meta=test_case.to_dict(),
        logs=[] if live_run else test_case.logs,  # Don't embed logs for live runs, WebSocket will send them
        # Don't embed stack traces for live runs either; /ws/logs will replay existing exceptions on connect.
        # Embedding + replay would cause duplicate stack traces in the stack trace section.
        stack_traces=[] if live_run else test_case.stack_traces,
        live_run=live_run,
        server_mode=True,  # Always True when served from live server
        attachments=None,  # Attachments loaded via API in server mode
        group_hash=group_hash
    )

    # Add cache control headers to prevent caching of live test case logs
    headers = {}
    if live_run:
        headers.update({
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        })

    return web.Response(text=html, content_type="text/html", headers=headers)


# --- ZIP Export Handler ---

async def zip_export_handler(request):
    run_id = request.match_info["run_id"]

    # Validate run_id to prevent path traversal
    if not validate_run_id(run_id):
        return web.Response(status=400, text="Invalid run ID")

    run_path = get_run_path(run_id)

    try:
        if not run_path.exists():
            raise FileNotFoundError("Run not found")

        zip_name = f"{run_id}.zip"
        zip_path = run_path / zip_name

        # Remove existing zip if exists
        if zip_path.exists():
            zip_path.unlink()

        # Create zip archive with all HTML pages and logs embedded
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add test run index page (static mode via unified template)
            meta_path = run_path / "meta.json"
            if not meta_path.exists():
                raise FileNotFoundError("meta.json not found")
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            run = TestRunData.from_dict(run_id, meta)
            test_cases_dict = {tc_id: tc.to_dict() for tc_id, tc in run.test_cases.items()}
            # Count test results for multiple badges
            passed_count = 0
            failed_count = 0
            skipped_count = 0

            for tc in run.test_cases.values():
                if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                    status = tc.status.lower()
                    if status == 'passed':
                        passed_count += 1
                    elif status == 'failed':
                        failed_count += 1
                    elif status == 'skipped':
                        skipped_count += 1
                    elif status == 'aborted':
                        failed_count += 1

            run_html = render_template(
                'test_run.html',
                run_id=run_id,
                status=run.status,
                start_time=run.start_time,
                end_time=run.end_time,
                user_metadata=meta.get("user_metadata", {}),
                retention_days=meta.get("retention_days"),
                test_cases=test_cases_dict,
                live_run=False,
                passed_count=passed_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                files_exist=True  # Files always exist for ZIP export
            )
            zf.writestr("index.html", run_html)

            # Add CSS file for test case logs
            css_path = STATIC_DIR / "test_case_log.css"
            if css_path.exists():
                with open(css_path, "r", encoding="utf-8") as f:
                    css_content = f.read()
                zf.writestr("static/test_case_log.css", css_content)

            # Add JavaScript files for test case logs
            js_path = STATIC_DIR / "at_syntax.js"
            if js_path.exists():
                with open(js_path, "r", encoding="utf-8") as f:
                    js_content = f.read()
                zf.writestr("static/at_syntax.js", js_content)

            # Add main test case log JavaScript file
            tc_js_path = STATIC_DIR / "test_case_log.js"
            if tc_js_path.exists():
                with open(tc_js_path, "r", encoding="utf-8") as f:
                    tc_js_content = f.read()
                zf.writestr("static/test_case_log.js", tc_js_content)

            # Add each test case log page (static mode via unified template)
            for tc_id, tc in run.test_cases.items():
                sanitized_tc_id = sanitize_filename(tc_id)
                log_path = get_case_log_path(run_id, tc_id)
                if log_path.exists():
                    raw_logs = read_jsonl(log_path)
                    logs = []
                    for entry in raw_logs:
                        log_entry = TestCaseData._sanitize_log_entry(entry)
                        if log_entry:
                            logs.append(log_entry)

                    # Collect attachment information for this test case
                    attachments = []
                    attachments_dir = get_attachments_dir(run_id, tc_id)
                    if attachments_dir.exists():
                        for attachment_file in attachments_dir.iterdir():
                            if attachment_file.is_file():
                                attachments.append({
                                    "filename": attachment_file.name,
                                    "size": attachment_file.stat().st_size,
                                    "modified_time": datetime.fromtimestamp(attachment_file.stat().st_mtime, UTC).isoformat() + "Z"
                                })

                    log_html = render_template(
                        'test_case_log.html',
                        run_id=run_id,
                        run_name=meta.get('run_name'),
                        test_case_id=tc_id,
                        run_meta=meta,
                        test_case_meta=tc.to_dict(),
                        logs=logs,
                        attachments=attachments,  # Add attachments to template
                        live_run=False,
                        server_mode=False
                    )
                    zf.writestr(f"log/{sanitized_tc_id}.html", log_html)

                # Add attachments for this test case
                attachments_dir = get_attachments_dir(run_id, tc_id)
                if attachments_dir.exists():
                    for attachment_file in attachments_dir.iterdir():
                        if attachment_file.is_file():
                            with open(attachment_file, "rb") as f:
                                attachment_data = f.read()
                            zf.writestr(f"attachments/{sanitized_tc_id}/{attachment_file.name}", attachment_data)
        headers = {
            "Content-Disposition": f"attachment; filename={zip_name}"
        }
        return web.FileResponse(path=zip_path, headers=headers)
    except FileNotFoundError as e:
        log_event("zip_export_missing", run_id=run_id, error=str(e))
        return web.Response(status=404, text=f"Export failed: {str(e)}. Try re-running the export after the test finishes.")
    except Exception as e:
        log_event("zip_export_error", run_id=run_id, error=str(e))
        return web.Response(status=500, text="Export failed due to a server error. Please try again later.")


# --- Static file serving for logs and runs ---

async def static_handler(request):
    # Serve static files under /testRun/
    rel_path = request.match_info["tail"]

    # Validate path to prevent directory traversal
    if not rel_path or '..' in rel_path or rel_path.startswith('/'):
        return web.Response(status=400, text="Invalid path")

    # Normalize path and ensure it stays within DATA_DIR
    try:
        # Use resolve() to get absolute path and check it's within DATA_DIR
        full_path = (DATA_DIR / rel_path).resolve()
        data_dir_resolved = DATA_DIR.resolve()

        # Ensure the resolved path is within DATA_DIR
        if not str(full_path).startswith(str(data_dir_resolved)):
            return web.Response(status=403, text="Access denied")

    except (OSError, ValueError):
        return web.Response(status=400, text="Invalid path")

    if not full_path.exists() or not full_path.is_file():
        return web.Response(status=404, text="Not Found")
    return web.FileResponse(path=full_path)

async def static_file_handler(request):
    # Serve static files from static directory
    static_path = request.match_info["path"]

    # Validate path to prevent directory traversal
    if not static_path or '..' in static_path or static_path.startswith('/'):
        return web.Response(status=400, text="Invalid path")

    # Normalize path and ensure it stays within static directory
    try:
        static_dir = STATIC_DIR
        full_path = (static_dir / static_path).resolve()
        static_dir_resolved = static_dir.resolve()

        # Ensure the resolved path is within static directory
        if not str(full_path).startswith(str(static_dir_resolved)):
            return web.Response(status=403, text="Access denied")

    except (OSError, ValueError):
        return web.Response(status=400, text="Invalid path")

    if not full_path.exists() or not full_path.is_file():
        return web.Response(status=404, text="Static file not found")
    return web.FileResponse(path=full_path)

# --- Attachment handlers ---

async def upload_attachment_handler(request):
    """Handle attachment uploads for test cases"""
    # Check if attachments are enabled
    if not ATTACHMENTS_ENABLED:
        return web.Response(status=403, text="Attachment upload is disabled")

    run_id = request.match_info["run_id"]
    test_case_id = request.match_info["test_case_id"]

    # Validate run_id and test_case_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(test_case_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    # Verify the test run exists
    run_path = get_run_path(run_id)
    if not run_path.exists():
        return web.Response(status=404, text="Test run not found")

    try:
        # Parse multipart form data
        reader = await request.multipart()

        attachment_files = []
        while True:
            part = await reader.next()
            if part is None:
                break

            if part.name == 'attachment':
                # Read the file content
                filename = part.filename
                if not filename:
                    continue

                # Validate and sanitize filename
                if not isinstance(filename, str) or len(filename) == 0:
                    continue

                # Sanitize the filename to prevent path traversal
                sanitized_filename = sanitize_filename(filename)

                # Additional validation for file size (configurable limit)
                content_length = 0
                max_size = ATTACHMENT_MAX_SIZE

                # Create attachments directory
                attachments_dir = get_attachments_dir(run_id, test_case_id)
                attachments_dir.mkdir(parents=True, exist_ok=True)

                # Save the file with size validation
                file_path = get_attachment_path(run_id, test_case_id, sanitized_filename)
                async with aiofiles.open(file_path, 'wb') as f:
                    while True:
                        chunk = await part.read_chunk(8192)  # 8KB chunks
                        if not chunk:
                            break
                        content_length += len(chunk)
                        if content_length > max_size:
                            # Delete the file if it exceeds size limit
                            await f.close()
                            if file_path.exists():
                                file_path.unlink()
                            max_size_mb = max_size // (1024 * 1024)
                            return web.Response(status=413, text=f"File too large (max {max_size_mb}MB)")
                        await f.write(chunk)

                attachment_files.append({
                    "filename": filename,
                    "size": file_path.stat().st_size,
                    "upload_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                })

                log_event("attachment_uploaded", run_id=run_id, test_case_id=test_case_id,
                         filename=filename, size=file_path.stat().st_size)

        return web.json_response({
            "success": True,
            "attachments": attachment_files
        })

    except Exception as e:
        log_event("attachment_upload_error", run_id=run_id, test_case_id=test_case_id, error=str(e))
        return web.Response(status=500, text=f"Upload failed: {str(e)}")

async def download_attachment_handler(request):
    """Handle attachment downloads for test cases"""
    run_id = request.match_info["run_id"]
    test_case_id = request.match_info["test_case_id"]
    filename = request.match_info["filename"]

    # Validate run_id and test_case_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(test_case_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    # Validate filename
    if not filename or not isinstance(filename, str):
        return web.Response(status=400, text="Invalid filename")

    # Sanitize filename to ensure it's safe
    sanitized_filename = sanitize_filename(filename)
    if sanitized_filename != filename:
        return web.Response(status=400, text="Invalid filename characters")

    # Verify the test run exists
    run_path = get_run_path(run_id)
    if not run_path.exists():
        return web.Response(status=404, text="Test run not found")

    # Get the attachment file path
    file_path = get_attachment_path(run_id, test_case_id, filename)

    if not file_path.exists() or not file_path.is_file():
        return web.Response(status=404, text="Attachment not found")

    # Set appropriate headers for file download
    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": "application/octet-stream"
    }

    return web.FileResponse(path=file_path, headers=headers)

async def list_attachments_handler(request):
    """List all attachments for a test case"""
    run_id = request.match_info["run_id"]
    test_case_id = request.match_info["test_case_id"]

    # Validate run_id and test_case_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(test_case_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    # Verify the test run exists
    run_path = get_run_path(run_id)
    if not run_path.exists():
        return web.Response(status=404, text="Test run not found")

    # Get attachments directory
    attachments_dir = get_attachments_dir(run_id, test_case_id)

    attachments = []
    if attachments_dir.exists():
        for file_path in attachments_dir.iterdir():
            if file_path.is_file():
                attachments.append({
                    "filename": file_path.name,
                    "size": file_path.stat().st_size,
                    "modified_time": datetime.fromtimestamp(file_path.stat().st_mtime, UTC).replace(tzinfo=None).isoformat() + "Z"
                })

    return web.json_response({"attachments": attachments})



# --- Cleanup task for retention and local runs replacement ---

async def cleanup_abandoned_running_runs():
    """Clean up runs that were left in running state due to server restart."""
    print("Checking for abandoned running runs on server startup...")

    try:
        # Get all runs that might have running test cases (both 'running' and 'aborted' runs)
        all_runs = await database.db.get_test_runs(limit=10000)

        # Filter for runs that are either running or aborted
        running_or_aborted_runs = [run for run in all_runs if run.get('status') in ('running', 'aborted')]

        for run in running_or_aborted_runs:
            run_id = run['run_id']
            run_status = run.get('status')
            print(f"Found abandoned {run_status} run: {run_id}")

            # Get test cases for this run from database
            test_cases = await database.db.get_test_cases_for_run(run_id)

            # Find the last test case event time
            last_tc_event_time = None
            aborted_count = 0

            # Abort any running test cases
            for tc in test_cases:
                if tc.get('status') == 'running':
                    start_time = tc.get('start_time')

                    # Update test case to aborted in database
                    try:
                        await database.log_test_case_finished(
                            run_id,
                            tc['test_case_id'],
                            'aborted'
                        )
                        aborted_count += 1

                        # Track the latest event time
                        if start_time and (not last_tc_event_time or start_time > last_tc_event_time):
                            last_tc_event_time = start_time
                    except Exception as e:
                        print(f"Error aborting test case {tc['test_case_id']}: {e}")

            # Only update run status if it's still running
            if run_status == 'running' and aborted_count > 0:
                # Set run end time
                run_end_time = last_tc_event_time if last_tc_event_time else now_utc_iso()

                # Update run status to aborted in database
                try:
                    await database.log_test_run_finished(run_id, run_end_time, 'aborted')
                    print(f"Aborted run {run_id}: {aborted_count} test cases marked as aborted")
                    log_event("run_aborted_on_startup", run_id=run_id, aborted_test_cases=aborted_count)
                except Exception as e:
                    print(f"Error aborting run {run_id}: {e}")
            elif aborted_count > 0:
                print(f"Updated {aborted_count} test cases to aborted status for already-aborted run {run_id}")

    except Exception as e:
        print(f"Error during cleanup_abandoned_running_runs: {e}")

async def cleanup_runs_sweep():
    now = datetime.now(UTC)

    try:
        # Get all runs from database
        all_runs = await database.db.get_test_runs(limit=100000)

        for run in all_runs:
            run_id = run['run_id']
            retention_days = run.get('retention_days')
            start_time_str = run.get('start_time')

            should_delete = False
            reason = None

            # Check if run files should be deleted based on retention_days
            if retention_days and start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    # Make start_time timezone-aware if it's not
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=UTC)
                    age_days = (now - start_time).days
                    if age_days > int(retention_days):
                        should_delete = True
                        reason = "expired_retention_days"
                except Exception as e:
                    print(f"Error calculating age for run {run_id}: {e}")

            if should_delete:
                log_event("run_files_deleted", run_id=run_id, reason=reason)

                # Delete from filesystem only (keep database records for historical analysis)
                run_path = get_run_path(run_id)
                if run_path.exists():
                    try:
                        shutil.rmtree(run_path)
                        print(f"Deleted filesystem data for run {run_id} (keeping database metadata)")
                    except Exception as e:
                        print(f"Error deleting filesystem data for run {run_id}: {e}")

    except Exception as e:
        print(f"Error during cleanup_runs_sweep: {e}")

async def cleanup_old_runs():
    while True:
        await cleanup_runs_sweep()
        await asyncio.sleep(3600)  # Run every hour

# --- TestRun class ---

class TestRunData:  # pytest: disable=collection
    __test__ = False  # Tell pytest to ignore this class

    def __init__(self, run_id, retention_days, local_run, user_metadata=None, group=None, group_hash=None, run_name=None, dut="TestDevice-001"):
        self.id = run_id
        self.dut = dut
        self.retention_days = retention_days
        self.local_run = local_run
        self.user_metadata = user_metadata or {}
        self.group = normalize_group_payload(group)
        self.group_hash = group_hash or (compute_group_hash(self.group) if self.group else None)
        self.run_name = run_name  # Human-readable name displayed in UI
        self.status = "running"
        self.start_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
        self.end_time = None
        self.test_cases: dict[str, TestCaseData] = {}  # tc_id -> metadata dict
        self.logs = {}  # tc_id -> list of logs entries
        self.last_update = datetime.now(UTC)

    def update_last(self):
        self.last_update = datetime.now(UTC)

    def to_dict(self):
        return {
            "run_id": self.id,
            "run_name": self.run_name,
            "retention_days": self.retention_days,
            "local_run": self.local_run,
            "user_metadata": self.user_metadata,
            "group": self.group,
            "group_hash": self.group_hash,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "test_cases": {tc_id: tc.to_dict() for tc_id, tc in self.test_cases.items()},
        }

    @classmethod
    def from_dict(cls, run_id, meta):
        run = cls(
            run_id,
            meta.get("retention_days", 0),
            meta.get("local_run", False),
            meta.get("user_metadata", {}),
            meta.get("group"),
            meta.get("group_hash"),
            meta.get("run_name")
        )
        # If group hash missing but group present, compute now
        if run.group and not run.group_hash:
            run.group_hash = compute_group_hash(run.group)
        run.status = meta.get("status", "running")
        run.start_time = meta.get("start_time", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z")
        run.end_time = meta.get("end_time", "")
        run.test_cases = {tc_id: TestCaseData.from_dict(run, tc_id, tc_meta) for tc_id, tc_meta in meta.get("test_cases", {}).items()}
        return run

    def load_from_disk(run_id):
        meta_path = get_run_meta_path(run_id)
        if not meta_path.exists():
            return None
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return TestRunData.from_dict(run_id, meta)

# --- TestCase class ---
class TestCaseData:  # pytest: disable=collection
    __test__ = False  # Tell pytest to ignore this class

    _ALLOWED_LOG_FIELDS = {"timestamp", "message", "component", "channel", "dir", "phase"}
    _ALLOWED_PHASE_VALUES = {"teardown"}
    _ALLOWED_DIR_VALUES = {"tx", "rx"}

    def __init__(self, run, test_case_id, meta={}):
        self.run = run
        self.id = test_case_id
        self.status = meta.get("status", "running")
        self.start_time = meta.get("start_time", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z")
        self.end_time = meta.get("end_time", None)
        self.logs = meta.get("logs", [])
        self.stack_traces = meta.get("stack_traces", [])
        self.subscribers = []

        # Ensure persisted stack traces are loaded even if meta.json lacked them
        stack_path = get_case_stack_path(self.run.id, self.id)
        if stack_path.exists():
            try:
                file_traces = read_jsonl(stack_path)
                if file_traces:
                    self.stack_traces = file_traces
            except Exception as e:
                print(f"Failed to load stack traces for {self.id}: {e}")

    @classmethod
    def _sanitize_log_entry(cls, entry: dict) -> dict | None:
        """
        Whitelist filter for log entries so random extra keys don't leak into persisted JSONL or UI.
        Allowed keys: timestamp, message, component, channel, dir, phase
        """
        if not isinstance(entry, dict):
            return None

        timestamp = entry.get("timestamp")
        message = entry.get("message")
        if not timestamp or not message:
            return None

        out = {
            "timestamp": timestamp,
            "message": message,
            "component": entry.get("component", ""),
            "channel": entry.get("channel", ""),
        }

        direction = entry.get("dir")
        if isinstance(direction, str):
            d = direction.lower()
            if d in cls._ALLOWED_DIR_VALUES:
                out["dir"] = d

        phase = entry.get("phase")
        if isinstance(phase, str):
            p = phase.strip().lower()
            if p in cls._ALLOWED_PHASE_VALUES:
                out["phase"] = p

        return out

    def to_dict(self):
        return {
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "logs": self.logs,
            "stack_traces": self.stack_traces,
            # Note: subscribers are not serialized
        }

    @classmethod
    def from_dict(cls, run, test_case_id, meta):
        return cls(run, test_case_id, meta)

    async def add_log_entries(self, entries):
        log_path = get_case_log_path(self.run.id, self.id)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Append entries to log file and in-memory logs
        with open(log_path, "a", encoding="utf-8") as f:
            for entry in entries:
                log_entry = self._sanitize_log_entry(entry)
                if not log_entry:
                    continue

                f.write(json.dumps(log_entry) + "\n")
                # Save in memory
                self.logs.append(log_entry)
                # Send to subscribers
                for subscriber in self.subscribers:
                    await subscriber.put(log_entry)

    async def add_stack_trace(self, trace_entry):
        # Canonical exception representation:
        # - timestamp: ISO 8601 string
        # - message: exception or failure message
        # - exception_type: fully qualified exception type name (if available)
        # - stack_trace: list[str] – complete multiline stack trace, one line per entry
        timestamp = trace_entry.get("timestamp") or datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
        message = trace_entry.get("message", "")
        exception_type = trace_entry.get("exception_type", "")
        stack_trace_value = trace_entry.get("stack_trace") or []

        # Normalize stack_trace to a list of strings
        if isinstance(stack_trace_value, str):
            lines = [line for line in stack_trace_value.replace("\r\n", "\n").split("\n") if line]
        else:
            lines = list(stack_trace_value) if stack_trace_value else []

        entry = {
            "timestamp": timestamp,
            "message": message,
            "exception_type": exception_type,
            "stack_trace": lines,
            # Optional error classification hint from client
            "is_error": bool(trace_entry.get("is_error", False)),
        }

        stack_path = get_case_stack_path(self.run.id, self.id)
        stack_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Append to disk file
            with open(stack_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"Persisted stack trace to {stack_path}")
        except Exception as persist_error:
            print(f"Failed to persist stack trace for {self.id}: {persist_error}")

        try:
            # Keep authoritative list synced by re-reading from disk
            self.stack_traces = read_jsonl(stack_path)
        except Exception as reload_error:
            print(f"Failed to reload stack traces for {self.id}: {reload_error}")
            self.stack_traces.append(entry)

        # Push live updates to subscribers listening on /ws/logs
        payload = {"type": "exception", **entry}
        for subscriber in self.subscribers:
            await subscriber.put(payload)

    def load_log_from_disk(self) -> bool:
        self.logs = []
        log_path = get_case_log_path(self.run.id, self.id)
        if not log_path.exists():
            return False

        raw_logs = read_jsonl(log_path)
        for entry in raw_logs:
            log_entry = self._sanitize_log_entry(entry)
            if not log_entry:
                continue
            self.logs.append(log_entry)
        return True


# --- WebSocket handlers for test runs and UI clients ---


class WebSocketServer:
    def __init__(self):
        self.test_runs: dict[str, TestRunData] = {}  # run_id -> TestRunData
        self.ui_clients = set()  # websockets for UI clients

    async def get_unique_run_name(self, base_name: str, group_hash: str = None) -> str:
        """
        Ensure run_name is unique within a group by appending a counter if needed.
        E.g., "My Run" -> "My Run", "My Run 1", "My Run 2", etc.
        Names are scoped per group - the same name can exist in different groups.
        """
        # Check both in-memory runs and database
        existing_names = set()

        # Check in-memory runs (filter by group_hash)
        for run in self.test_runs.values():
            if run.run_name and run.group_hash == group_hash:
                existing_names.add(run.run_name)

        # Check database (filter by group_hash)
        try:
            db_names = await database.db.get_run_names_starting_with(base_name, group_hash)
            existing_names.update(db_names)
        except Exception as e:
            print(f"Error checking existing run names: {e}")

        # If base_name doesn't exist, use it
        if base_name not in existing_names:
            return base_name

        # Find the next available counter
        counter = 1
        while True:
            candidate = f"{base_name} {counter}"
            if candidate not in existing_names:
                return candidate
            counter += 1

    async def handle_ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        path = request.path
        if path == "/ws/nunit":
            await self.handle_nunit_ws(ws)
        elif path == "/ws/ui":
            await self.handle_ui_ws(ws)
        else:
            # Try matching /ws/logs/{run_id}/{test_case_id}
            match = re.match(r"^/ws/logs/([^/]+)/([^/]+)$", path)
            if match:
                run_id = match.group(1)
                test_case_id = match.group(2)
                await self.handle_log_stream(ws, run_id, test_case_id)
            else:
                await ws.close()

        return ws

    async def handle_nunit_ws(self, ws):
        # One connection per test run
        run = None
        last_activity = datetime.now(UTC)

        # Helper function to mark run as aborted
        async def mark_run_aborted(reason):
            nonlocal run
            if run and run.status == "running":
                print(f"Marking run {run.id} as aborted: {reason}")
                run.status = "aborted"
                run.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                run.update_last()

                # Mark all running test cases as aborted
                aborted_test_cases = []
                for tc_id, test_case in run.test_cases.items():
                    if test_case.status == "running":
                        print(f"Marking test case {tc_id} as aborted")
                        test_case.status = "aborted"
                        test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                        aborted_test_cases.append(tc_id)

                # Save to disk
                run_path = get_run_path(run.id)
                meta_path = run_path / "meta.json"
                if meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        current_meta = json.load(f)
                else:
                    current_meta = {}
                run_data = run.to_dict()
                if "deletes_at" in current_meta:
                    run_data["deletes_at"] = current_meta["deletes_at"]
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(run_data, f)

                # Calculate updated counts after aborting test cases
                passed_count = 0
                failed_count = 0
                skipped_count = 0
                aborted_count = 0

                for tc in run.test_cases.values():
                    if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                        status = tc.status.lower()
                        if status == 'passed':
                            passed_count += 1
                        elif status == 'failed':
                            failed_count += 1
                        elif status == 'skipped':
                            skipped_count += 1
                        elif status == 'aborted':
                            aborted_count += 1

                # Broadcast test case updates for all aborted test cases and log to database
                for tc_id in aborted_test_cases:
                    test_case = run.test_cases[tc_id]
                    tc_meta = test_case.to_dict()

                    # Log test case as aborted in database
                    try:
                        await database.log_test_case_finished(run.id, tc_id, 'aborted')
                    except Exception as db_error:
                        print(f"Database logging error for aborted test case {tc_id}: {db_error}")

                    # Broadcast UI update
                    await self.broadcast_ui({
                        "type": "test_case_finished",
                        "run_id": run.id,
                        "test_case_id": tc_id,
                        "test_case_meta": tc_meta,
                        "counts": {
                            "passed": passed_count,
                            "failed": failed_count,
                            "skipped": skipped_count,
                            "aborted": aborted_count
                        }
                    })

                # Log run finished to database
                try:
                    await database.log_test_run_finished(run.id, "aborted")
                except Exception as db_error:
                    print(f"Database logging error for run_aborted: {db_error}")

                # Broadcast run finished event
                await self.broadcast_ui({"type": "run_finished", "run": run_data})

                # Remove aborted run from memory to ensure consistent static banner behavior
                if run.id in self.test_runs:
                    del self.test_runs[run.id]
                    print(f"Removed aborted run {run.id} from memory")

        # Start a background task to monitor connection timeout
        async def monitor_connection():
            nonlocal run, last_activity
            while True:
                try:
                    await asyncio.sleep(5)  # Check every 5 seconds

                    # Check if WebSocket is still open and responsive
                    if ws.closed:
                        await mark_run_aborted("WebSocket closed")
                        break

                    # Try to send a ping frame to test the connection
                    try:
                        await ws.ping()
                    except Exception as e:
                        print(f"WebSocket ping failed: {e}")
                        await mark_run_aborted("WebSocket ping failed")
                        break

                    if run and run.status == "running":
                        time_since_activity = (datetime.now(UTC) - last_activity).total_seconds()
                        if time_since_activity > 30:  # 30 second timeout (reduced for testing)
                            await mark_run_aborted("Connection timeout")
                            break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Monitor connection error: {e}")
                    break

        monitor_task = asyncio.create_task(monitor_connection())

        try:
            print(f"Starting NUnit WebSocket connection monitoring")
            async for msg in ws:
                last_activity = datetime.now(UTC)  # Update activity timestamp
                print(f"Received message from NUnit client: {msg.type}")

                # Check for close/error messages
                if msg.type == web.WSMsgType.CLOSE:
                    print(f"NUnit WebSocket connection closed normally for run {run.id if run else 'unknown'}")
                    # If run didn't finish properly (no run_finished message was received), mark as aborted
                    if run and run.status == "running":
                        await mark_run_aborted("WebSocket closed before run_finished was sent")
                    break
                elif msg.type == web.WSMsgType.ERROR:
                    print(f"NUnit WebSocket connection error: {ws.exception()}")
                    # If run didn't finish properly, mark as aborted
                    if run and run.status == "running":
                        await mark_run_aborted("WebSocket error before run_finished was sent")
                    break
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")
                    except Exception as e:
                        print(f"Error parsing JSON message: {e}")
                        continue

                    if msg_type == "run_started":
                        try:
                            # Check if client provided a custom run_id
                            client_run_id = data.get("run_id")
                            validation_error = None

                            if client_run_id:
                                # Validate the custom run ID
                                is_valid, error_msg = validate_custom_run_id(client_run_id)
                                if not is_valid:
                                    validation_error = error_msg
                                else:
                                    # Check if run_id already exists (in-memory or database)
                                    if client_run_id in self.test_runs:
                                        validation_error = f"Run ID '{client_run_id}' is already in use"
                                    else:
                                        # Check database for existing run_id
                                        try:
                                            existing_run = await database.db.get_test_run_by_id(client_run_id)
                                            if existing_run:
                                                validation_error = f"Run ID '{client_run_id}' is already in use"
                                        except Exception as db_check_error:
                                            print(f"Error checking database for run_id: {db_check_error}")
                                            validation_error = "Error validating run ID"

                                if validation_error:
                                    # Send error response
                                    error_response = {
                                        "type": "run_started_response",
                                        "error": validation_error
                                    }
                                    await ws.send_json(error_response)
                                    continue

                                run_id = client_run_id
                            else:
                                # Server generates run_id (12 hex chars)
                                run_id = uuid.uuid4().hex[:12]

                            retention_days = data.get("retention_days", DEFAULT_RETENTION_DAYS)
                            local_run = data.get("local_run", False)
                            user_metadata = data.get("user_metadata", {})
                            raw_group = data.get("group")
                            group_payload = normalize_group_payload(raw_group)
                            group_hash = compute_group_hash(group_payload) if group_payload else None

                            # Get or generate run_name
                            run_name = data.get("run_name")
                            if not run_name:
                                # Generate default run_name from timestamp
                                run_name = datetime.now(UTC).strftime("Run %Y-%m-%d %H:%M:%S")

                            # Check for duplicate run_names within the group and append counter if needed
                            run_name = await self.get_unique_run_name(run_name, group_hash)
                            start_time = data.get("start_time")  # Get start_time from NUnit plugin

                            if run_id in self.test_runs:
                                self.test_runs.pop(run_id)
                            run = TestRunData(run_id, retention_days, local_run, user_metadata, group_payload, group_hash, run_name)

                            # Use the start_time from NUnit plugin if provided
                            if start_time:
                                run.start_time = start_time
                            # Compute deletes_at for server-side retention
                            try:
                                days = int(retention_days) if retention_days is not None else None
                            except Exception:
                                days = None
                            if days:
                                deletes_at = (datetime.now(UTC) + timedelta(days=days)).replace(tzinfo=None).isoformat() + "Z"
                            else:
                                deletes_at = None
                            self.test_runs[run_id] = run
                            # Create folder and save meta
                            run_path = get_run_path(run_id)
                            run_path.mkdir(parents=True, exist_ok=True)
                            meta_path = run_path / "meta.json"
                            meta_dict = run.to_dict()
                            if deletes_at:
                                meta_dict["deletes_at"] = deletes_at
                            with open(meta_path, "w", encoding="utf-8") as f:
                                json.dump(meta_dict, f)
                            log_event("run_started", run_id=run_id, run_name=run_name, retention_days=retention_days, deletes_at=deletes_at, user_metadata=user_metadata)

                            # Log to database
                            try:
                                await database.log_test_run_started(
                                    run_id,
                                    retention_days,
                                    local_run,
                                    user_metadata,
                                    run_name=run_name,
                                    group_name=group_payload["name"] if group_payload else None,
                                    group_hash=group_hash,
                                    group_metadata=(group_payload or {}).get("metadata")
                                )
                            except Exception as db_error:
                                print(f"Database logging error for run_started: {db_error}")

                            # Broadcast to UI clients
                            await self.broadcast_ui({"type": "run_started", "run": meta_dict})

                            # Send response to NUnit client with run_id, run_name, and URLs
                            response = {
                                "type": "run_started_response",
                                "run_id": run_id,
                                "run_name": run_name,
                                "run_url": f"/testRun/{run_id}/index.html"
                            }
                            if group_hash:
                                response["group_hash"] = group_hash
                                response["group_url"] = f"/groups/{group_hash}"
                            await ws.send_json(response)
                        except Exception as e:
                            print(f"Error in run_started: {e}")
                            import traceback
                            traceback.print_exc()

                    elif msg_type == "test_case_started":
                        try:
                            run_id = data.get("run_id")
                            tc_id = data.get("test_case_id")

                            if not run_id:
                                print("Error: run_id missing from test_case_started message")
                                continue

                            # Find the run by run_id
                            run = self.test_runs.get(run_id)

                            if not run:
                                print(f"Error: Run '{run_id}' not found for test_case_started message")
                                continue

                            # Replace HTML entities with actual quotes
                            tc_id = tc_id.replace("&quot;", '"')
                            tc_meta = data.get("test_case_meta", {})
                            run.test_cases[tc_id] = TestCaseData(run, tc_id, tc_meta)
                            run.update_last()
                            # Ensure log file exists
                            log_path = get_case_log_path(run.id, tc_id)
                            log_path.parent.mkdir(parents=True, exist_ok=True)
                            if not log_path.exists():
                                with open(log_path, "w", encoding="utf-8") as f:
                                    pass
                            log_event("test_case_started", run_id=run.id, test_case_id=tc_id)

                            # Log to database
                            try:
                                await database.log_test_case_started(run.id, tc_id, tc_meta.get("start_time"))
                            except Exception as db_error:
                                print(f"Database logging error for test_case_started: {db_error}")

                            # Update meta.json on disk
                            run_path = get_run_path(run.id)
                            meta_path = run_path / "meta.json"
                            # preserve deletes_at if it exists
                            current_meta = {}
                            if meta_path.exists():
                                with open(meta_path, "r", encoding="utf-8") as f:
                                    current_meta = json.load(f)
                            run_data = run.to_dict()
                            if "deletes_at" in current_meta:
                                run_data["deletes_at"] = current_meta["deletes_at"]
                            with open(meta_path, "w", encoding="utf-8") as f:
                                json.dump(run_data, f)
                            # Calculate counts for targeted update
                            passed_count = 0
                            failed_count = 0
                            skipped_count = 0
                            aborted_count = 0

                            for tc in run.test_cases.values():
                                if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                                    status = tc.status.lower()
                                    if status == 'passed':
                                        passed_count += 1
                                    elif status == 'failed':
                                        failed_count += 1
                                    elif status == 'skipped':
                                        skipped_count += 1
                                    elif status == 'aborted':
                                        aborted_count += 1

                            # Broadcast targeted test_case_started event
                            await self.broadcast_ui({
                                "type": "test_case_started",
                                "run_id": run.id,
                                "test_case_id": tc_id,
                                "test_case_meta": tc_meta,
                                "counts": {
                                    "passed": passed_count,
                                    "failed": failed_count,
                                    "skipped": skipped_count,
                                    "aborted": aborted_count
                                }
                            })
                        except Exception as e:
                            print(f"Error in test_case_started: {e}")
                            import traceback
                            traceback.print_exc()

                    elif msg_type == "log_batch":
                        try:
                            run_id = data.get("run_id")
                            tc_id = data.get("test_case_id")

                            if not run_id:
                                print("Error: run_id missing from log_batch message")
                                continue

                            # Find the run by run_id
                            run = self.test_runs.get(run_id)

                            if not run:
                                print(f"Error: Run '{run_id}' not found for log_batch message")
                                continue

                            # Replace HTML entities with actual quotes
                            tc_id = tc_id.replace("&quot;", '"')
                            entries = data.get("entries", [])
                            run.update_last()
                            if tc_id not in run.test_cases:
                                continue
                            test_case = run.test_cases[tc_id]
                            await test_case.add_log_entries(entries)
                            log_event("log_batch", run_id=run.id, test_case_id=tc_id, count=len(entries))
                        except Exception as e:
                            print(f"Error in log_batch: {e}")
                            import traceback
                            traceback.print_exc()

                    elif msg_type == "exception":
                        try:
                            run_id = data.get("run_id")
                            tc_id = data.get("test_case_id")

                            if not run_id or not tc_id:
                                print("Error: run_id or test_case_id missing from exception message")
                                continue

                            run = self.test_runs.get(run_id)
                            if not run:
                                print(f"Error: Run '{run_id}' not found for exception message")
                                continue

                            tc_id = tc_id.replace("&quot;", '"')
                            test_case = run.test_cases.get(tc_id)
                            if not test_case:
                                print(f"Error: Test case '{tc_id}' not found for exception message")
                                continue

                            # Extract fields directly from data (NUnit sends them at top level)
                            timestamp = data.get("timestamp") or datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                            message_text = data.get("message", "")
                            exception_type = data.get("exception_type", "")
                            stack_trace_value = data.get("stack_trace") or []
                            is_error = bool(data.get("is_error", False))

                            trace_entry = {
                                "timestamp": timestamp,
                                "message": message_text,
                                "exception_type": exception_type,
                                "stack_trace": stack_trace_value,
                                "is_error": is_error,
                            }

                            await test_case.add_stack_trace(trace_entry)
                            run.update_last()

                            # Persist updated metadata to disk
                            run_path = get_run_path(run.id)
                            meta_path = run_path / "meta.json"
                            current_meta = {}
                            if meta_path.exists():
                                with open(meta_path, "r", encoding="utf-8") as f:
                                    current_meta = json.load(f)
                            run_data = run.to_dict()
                            if "deletes_at" in current_meta:
                                run_data["deletes_at"] = current_meta["deletes_at"]
                            with open(meta_path, "w", encoding="utf-8") as f:
                                json.dump(run_data, f)

                            log_event("exception", run_id=run.id, test_case_id=tc_id)

                        except Exception as e:
                            print(f"Error in exception handling: {e}")
                            import traceback
                            traceback.print_exc()

                    elif msg_type == "test_case_finished":
                        try:
                            run_id = data.get("run_id")
                            tc_id = data.get("test_case_id")

                            if not run_id:
                                print("Error: run_id missing from test_case_finished message")
                                continue

                            # Find the run by run_id
                            run = self.test_runs.get(run_id)

                            if not run:
                                print(f"Error: Run '{run_id}' not found for test_case_finished message")
                                continue

                            # Replace HTML entities with actual quotes
                            tc_id = tc_id.replace("&quot;", '"')
                            if tc_id not in run.test_cases:
                                raise Exception(f"Test case {tc_id} not found")
                            test_case = run.test_cases[tc_id]

                            # Validate and set status
                            status = data.get("status", "").lower()
                            # Accept "error" in addition to standard statuses
                            if status in ['passed', 'failed', 'skipped', 'aborted', 'error']:
                                test_case.status = status
                                test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                            else:
                                print(f"Error: Invalid test status '{data.get('status')}' for test case {tc_id}, ignoring test case")
                                continue
                            tc_meta = test_case.to_dict()

                            # Calculate counts for targeted update
                            passed_count = 0
                            failed_count = 0
                            skipped_count = 0
                            aborted_count = 0

                            for tc in run.test_cases.values():
                                if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                                    status = tc.status.lower()
                                    if status == 'passed':
                                        passed_count += 1
                                    elif status == 'failed':
                                        failed_count += 1
                                    elif status == 'skipped':
                                        skipped_count += 1
                                    elif status == 'aborted':
                                        aborted_count += 1

                            # Broadcast targeted test_case_updated event
                            await self.broadcast_ui({
                                "type": "test_case_updated",
                                "run_id": run.id,
                                "test_case_id": tc_id,
                                "test_case_meta": tc_meta,
                                "counts": {
                                    "passed": passed_count,
                                    "failed": failed_count,
                                    "skipped": skipped_count,
                                    "aborted": aborted_count
                                }
                            })
                            run.update_last()
                            # Update meta.json on disk
                            run_path = get_run_path(run.id)
                            meta_path = run_path / "meta.json"
                            # preserve deletes_at if it exists
                            current_meta = {}
                            if meta_path.exists():
                                with open(meta_path, "r", encoding="utf-8") as f:
                                    current_meta = json.load(f)
                            run_data = run.to_dict()
                            if "deletes_at" in current_meta:
                                run_data["deletes_at"] = current_meta["deletes_at"]
                            with open(meta_path, "w", encoding="utf-8") as f:
                                json.dump(run_data, f)
                            log_event("test_case_finished", run_id=run.id, test_case_id=tc_id, status=test_case.status)

                            # Log to database
                            try:
                                await database.log_test_case_finished(run.id, tc_id, test_case.status)
                            except Exception as db_error:
                                print(f"Database logging error for test_case_finished: {db_error}")

                            # Calculate counts for targeted update
                            passed_count = 0
                            failed_count = 0
                            skipped_count = 0
                            aborted_count = 0

                            for tc in run.test_cases.values():
                                if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                                    status = tc.status.lower()
                                    if status == 'passed':
                                        passed_count += 1
                                    elif status == 'failed':
                                        failed_count += 1
                                    elif status == 'skipped':
                                        skipped_count += 1
                                    elif status == 'aborted':
                                        aborted_count += 1

                            # Broadcast targeted test_case_finished event
                            await self.broadcast_ui({
                                "type": "test_case_finished",
                                "run_id": run.id,
                                "test_case_id": tc_id,
                                "test_case_meta": tc_meta,
                                "counts": {
                                    "passed": passed_count,
                                    "failed": failed_count,
                                    "skipped": skipped_count,
                                    "aborted": aborted_count
                                }
                            })
                        except Exception as e:
                            print(f"Error in test_case_finished: {e}")
                            import traceback
                            traceback.print_exc()

                    elif msg_type == "run_finished":
                        try:
                            run_id = data.get("run_id")

                            if not run_id:
                                print("Error: run_id missing from run_finished message")
                                continue

                            # Find the run by run_id
                            run = self.test_runs.get(run_id)

                            if not run:
                                print(f"Error: Run '{run_id}' not found for run_finished message")
                                continue

                            # CRITICAL: Before marking run as finished, check for any test cases still in "running" state
                            # This can happen if WebSocket closes or test case crashes before sending test_case_finished
                            aborted_test_cases = []
                            for tc_id, test_case in run.test_cases.items():
                                if test_case.status == "running":
                                    print(f"Test case {tc_id} was still running when run_finished received, marking as aborted")
                                    test_case.status = "aborted"
                                    test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                                    aborted_test_cases.append(tc_id)

                                    # Log to database
                                    try:
                                        await database.log_test_case_finished(run.id, tc_id, 'aborted')
                                    except Exception as db_error:
                                        print(f"Database logging error for aborted test case {tc_id}: {db_error}")

                            # Broadcast updates for aborted test cases
                            if aborted_test_cases:
                                # Calculate counts
                                passed_count = 0
                                failed_count = 0
                                skipped_count = 0
                                aborted_count = 0

                                for tc in run.test_cases.values():
                                    if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                                        status = tc.status.lower()
                                        if status == 'passed':
                                            passed_count += 1
                                        elif status == 'failed':
                                            failed_count += 1
                                        elif status == 'skipped':
                                            skipped_count += 1
                                        elif status == 'aborted':
                                            aborted_count += 1

                                # Broadcast each aborted test case
                                for tc_id in aborted_test_cases:
                                    test_case = run.test_cases[tc_id]
                                    tc_meta = test_case.to_dict()
                                    await self.broadcast_ui({
                                        "type": "test_case_finished",
                                        "run_id": run.id,
                                        "test_case_id": tc_id,
                                        "test_case_meta": tc_meta,
                                        "counts": {
                                            "passed": passed_count,
                                            "failed": failed_count,
                                            "skipped": skipped_count,
                                            "aborted": aborted_count
                                        }
                                    })

                            run.status = data.get("status", "finished")
                            run.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                            run.update_last()
                            # Update meta.json on disk
                            run_path = get_run_path(run.id)
                            meta_path = run_path / "meta.json"
                            current_meta = {}
                            if meta_path.exists():
                                with open(meta_path, "r", encoding="utf-8") as f:
                                    current_meta = json.load(f)
                            run_data = run.to_dict()
                            if "deletes_at" in current_meta:
                                run_data["deletes_at"] = current_meta["deletes_at"]
                            with open(meta_path, "w", encoding="utf-8") as f:
                                json.dump(run_data, f)
                            log_event("run_finished", run_id=run.id, status=run.status)

                            # Log to database
                            try:
                                await database.log_test_run_finished(run.id, run.status)
                            except Exception as db_error:
                                print(f"Database logging error for run_finished: {db_error}")

                            # Broadcast to UI
                            await self.broadcast_ui({"type": "run_finished", "run": run_data})

                            # Remove finished run from memory to ensure consistent static banner behavior
                            if run_id in self.test_runs:
                                del self.test_runs[run_id]
                                print(f"Removed finished run {run_id} from memory")
                        except Exception as e:
                            print(f"Error in run_finished: {e}")
                            import traceback
                            traceback.print_exc()

        except Exception as e:
            print(f'NUnit WebSocket connection error: {e}')
            if run and run.status == "running":
                await mark_run_aborted("WebSocket connection exception")
        finally:
            print(f"Cleaning up NUnit WebSocket connection for run {run.id if run else 'unknown'}")

            # CRITICAL: When WebSocket closes, check if run finished properly
            # If run is still running, mark it and all running test cases as aborted
            if run and run.status == "running":
                print(f"Run {run.id} was still running when WebSocket closed, marking as aborted")
                await mark_run_aborted("WebSocket closed while run was still running")

            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

    async def handle_ui_ws(self, ws):
        self.ui_clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # UI clients currently send no commands
                    pass
                elif msg.type == web.WSMsgType.ERROR:
                    print('UI ws connection closed with exception %s' % ws.exception())
        finally:
            self.ui_clients.remove(ws)

    async def handle_log_stream(self, ws, run_id, test_case_id):
        print(f"WebSocket log stream request: run_id={run_id}, test_case_id={test_case_id}")

        # Validate run_id and test_case_id to prevent path traversal
        if not validate_run_id(run_id) or not validate_test_case_id(test_case_id):
            print(f"Invalid run_id or test_case_id: {run_id}, {test_case_id}")
            await ws.send_json({ "type": "error", "message": "Invalid run ID or test case ID" })
            await ws.close()
            return

        # Register this client to receive live logs
        test_run = self.test_runs.get(run_id)  # Use WebSocketServer's test_runs dict
        if not test_run:
            print(f"Test run not found in memory: {run_id}")
            await ws.send_json({ "type": "error", "message": "Test run not found" })
            await ws.close()
            return

        test_case = test_run.test_cases.get(test_case_id)
        if not test_case:
            print(f"Couldn't find test case {test_case_id} in test run {run_id}")
            await ws.send_json({ "type": "error", "message": "Test case not found" })
            await ws.close()
            return

        print(f"WebSocket log stream established for {run_id}/{test_case_id}")

        # For live runs, send all existing logs + exceptions first, then subscribe to new ones.
        # This ensures no events are missed when connecting (or reconnecting after refresh) to a running test case.
        try:
            initial_items = []

            # Existing log entries
            for existing_log in test_case.logs:
                ts = existing_log.get("timestamp", "")
                initial_items.append((ts, existing_log))

            # Existing exceptions/stack traces (wire format: type="exception")
            for trace in getattr(test_case, "stack_traces", []) or []:
                ts = trace.get("timestamp", "")
                payload = {"type": "exception", **trace}
                initial_items.append((ts, payload))

            # ISO 8601 timestamps sort lexicographically in chronological order.
            initial_items.sort(key=lambda x: x[0] or "")

            for _, item in initial_items:
                await ws.send_json(item)
        except Exception as e:
            print(f"Error sending existing logs: {e}")
            await ws.send_json({ "type": "error", "message": "Error sending existing logs" })
            await ws.close()
            return

        # Now subscribe to future log entries
        queue = asyncio.Queue()
        test_case.subscribers.append(queue)

        try:
            while True:
                entry = await queue.get()
                await ws.send_json(entry)
        except Exception:
            pass
        finally:
            test_case.subscribers.remove(queue)

    async def broadcast_ui(self, message):
        dead = []
        for ws in self.ui_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ui_clients.remove(ws)

# --- HTTP Handlers additions ---
async def health_handler(request):
    return web.json_response({"status": "ok"})


# --- Test Results Analyzer API ---

async def api_test_runs_handler(request):
    """Get test runs with filtering capabilities."""
    try:
        # Parse query parameters
        limit = int(request.query.get('limit', 100))
        offset = int(request.query.get('offset', 0))
        status = request.query.get('status')

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group') or request.query.get('group_hash')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test runs from database
        runs = await database.db.get_test_runs(
            limit=limit,
            offset=offset,
            status_filter=status,
            metadata_filters=metadata_filters if metadata_filters else None,
            group_hash=group_hash
        )

        return web.json_response({
            "success": True,
            "data": runs,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "count": len(runs)
            }
        })

    except Exception as e:
        print(f"Error in api_test_runs_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_run_details_handler(request):
    """Get detailed information about a specific test run."""
    try:
        run_id = request.match_info["run_id"]

        # Get test run details
        run = await database.db.get_test_run_by_id(run_id)
        if not run:
            return web.json_response({
                "success": False,
                "error": "Test run not found"
            }, status=404)

        # Get test cases for this run
        test_cases = await database.db.get_test_cases_for_run(run_id)

        # Get metadata for this run
        user_metadata = await database.db.get_user_metadata_for_run(run_id)
        group_metadata = await database.db.get_group_metadata_for_run(run_id)

        return web.json_response({
            "success": True,
            "data": {
                "run": run,
                "test_cases": test_cases,
                "user_metadata": user_metadata,
                "group": {
                    "name": run.get("group_name"),
                    "hash": run.get("group_hash"),
                    "metadata": group_metadata
                }
            }
        })

    except Exception as e:
        print(f"Error in api_test_run_details_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_results_for_runs_handler(request):
    """Get test results for multiple runs efficiently."""
    try:
        run_ids_param = request.query.get('run_ids', '')
        if not run_ids_param:
            return web.json_response({
                "success": False,
                "error": "run_ids parameter is required"
            }, status=400)

        # Parse run IDs (comma-separated)
        run_ids = [run_id.strip() for run_id in run_ids_param.split(',') if run_id.strip()]

        if not run_ids:
            return web.json_response({
                "success": False,
                "error": "No valid run IDs provided"
            }, status=400)

        # Get test results for all runs in one efficient query
        test_results = await database.db.get_test_results_for_runs(run_ids)

        return web.json_response({
            "success": True,
            "data": test_results
        })

    except Exception as e:
        print(f"Error in api_test_results_for_runs_handler: {e}")
        return web.json_response({
            "success": False,
            "error": f"Internal server error: {str(e)}"
        }, status=500)


async def api_test_results_over_time_handler(request):
    """Get test results aggregated over time for trending analysis."""
    try:
        days_back = int(request.query.get('days_back', 30))

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group') or request.query.get('group_hash')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test runs over time (individual runs, not aggregated by date)
        results = await database.db.get_test_runs_over_time(
            days_back=days_back,
            metadata_filters=metadata_filters if metadata_filters else None,
            group_hash=group_hash
        )

        # Log the results
        print(f"API test-runs-over-time: {len(results)} test runs")
        for result in results[:3]:  # Show first 3 runs
            print(f"  Run: {result.get('run_id')[:8]}..., Passed: {result.get('passed_tests')}, Failed: {result.get('failed_tests')}, Skipped: {result.get('skipped_tests')}")

        return web.json_response({
            "success": True,
            "data": results
        })

    except Exception as e:
        print(f"Error in api_test_results_over_time_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_case_history_handler(request):
    """Get execution history for a specific test case."""
    try:
        test_case_id = request.query.get('test_case_id')
        if not test_case_id:
            return web.json_response({
                "success": False,
                "error": "test_case_id parameter is required"
            }, status=400)

        limit = int(request.query.get('limit', 50))

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group') or request.query.get('group_hash')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test case history
        history = await database.db.get_test_case_history(
            test_case_id=test_case_id,
            limit=limit,
            metadata_filters=metadata_filters if metadata_filters else None,
            group_hash=group_hash
        )

        return web.json_response({
            "success": True,
            "data": history
        })

    except Exception as e:
        print(f"Error in api_test_case_history_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_case_history_with_links_handler(request):
    """Get test case history with log file existence check."""
    try:
        test_case_id = request.query.get('test_case_id')
        if not test_case_id:
            return web.json_response({
                "success": False,
                "error": "test_case_id is required"
            }, status=400)

        limit = int(request.query.get('limit', 10))
        current_run_id = request.query.get('current_run_id')  # Exclude current run

        group_hash = request.query.get('group')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test case history
        history = await database.db.get_test_case_history(
            test_case_id=test_case_id,
            limit=limit + 1,  # Get one extra to account for current run exclusion
            group_hash=group_hash
        )

        # Filter out current run and check log existence
        result = []
        for item in history:
            run_id = item.get('run_id')
            if current_run_id and run_id == current_run_id:
                continue

            # Check if log file exists
            log_path = get_case_log_path(run_id, test_case_id)
            item['has_log'] = log_path.exists()

            result.append(item)

            if len(result) >= limit:
                break

        return web.json_response({
            "success": True,
            "data": result
        })

    except Exception as e:
        print(f"Error in api_test_case_history_with_links_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_metadata_keys_handler(request):
    """Get all available metadata keys."""
    try:
        keys = await database.db.get_all_metadata_keys()
        return web.json_response({
            "success": True,
            "data": keys
        })

    except Exception as e:
        print(f"Error in api_metadata_keys_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_metadata_values_handler(request):
    """Get unique values for a specific metadata key."""
    try:
        key = request.query.get('key')
        if not key:
            return web.json_response({
                "success": False,
                "error": "key parameter is required"
            }, status=400)

        values = await database.db.get_unique_metadata_values(key)
        return web.json_response({
            "success": True,
            "data": values
        })

    except Exception as e:
        print(f"Error in api_metadata_values_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_group_details_handler(request):
    """Return metadata for a specific group hash."""
    group_hash = request.match_info.get("group_hash")
    if not validate_group_hash_value(group_hash):
        return web.json_response({
            "success": False,
            "error": "Invalid group hash"
        }, status=400)

    runs = await database.db.get_test_runs(limit=1, group_hash=group_hash)
    if not runs:
        return web.json_response({
            "success": False,
            "error": "Group not found"
        }, status=404)

    run = runs[0]
    metadata = await database.db.get_group_metadata_for_run(run["run_id"])

    return web.json_response({
        "success": True,
        "data": {
            "hash": group_hash,
            "name": run.get("group_name"),
            "metadata": metadata
        }
    })


async def api_failures_toplist_handler(request):
    """Get top failing test cases or symptoms."""
    try:
        mode = request.query.get('mode', 'by_test_case')
        days_back = int(request.query.get('days', 30))
        top_n = int(request.query.get('top', 20))

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        if mode == 'by_symptom':
            # Get failed test cases and analyze by stack trace
            failed_cases = await database.db.get_failed_test_cases(
                days_back=days_back,
                limit=1000,  # Get more to analyze symptoms
                group_hash=group_hash,
                metadata_filters=metadata_filters if metadata_filters else None
            )

            # Group by first line of stack trace (symptom)
            symptom_map = {}
            for case in failed_cases:
                # Load stack trace from file
                stack_path = get_case_stack_path(case['run_id'], case['test_case_id'])
                symptom = None
                stack_trace_sample = None

                if stack_path.exists():
                    try:
                        traces = read_jsonl(stack_path)
                        if traces and len(traces) > 0:
                            first_trace = traces[0]
                            stack_lines = first_trace.get('stack_trace', [])
                            if stack_lines and len(stack_lines) > 0:
                                # Use first line of stack trace as symptom
                                symptom = stack_lines[0].strip() if isinstance(stack_lines[0], str) else str(stack_lines[0])
                                # Store full trace for sample
                                stack_trace_sample = '\n'.join(stack_lines[:10])  # First 10 lines
                    except Exception as e:
                        print(f"Error reading stack trace: {e}")

                if not symptom:
                    symptom = "No stack trace available"

                if symptom not in symptom_map:
                    symptom_map[symptom] = {
                        'symptom': symptom,
                        'failure_count': 0,
                        'affected_test_cases': {},  # Dict: test_case_id -> {run_id, time}
                        'last_failure': None,
                        'last_failure_run_id': None,
                        'last_failure_test_case': None,
                        'stack_trace_sample': stack_trace_sample
                    }

                symptom_map[symptom]['failure_count'] += 1

                # Track last failure and count for each test case
                tc_id = case['test_case_id']
                case_time = case.get('start_time')
                if tc_id not in symptom_map[symptom]['affected_test_cases']:
                    symptom_map[symptom]['affected_test_cases'][tc_id] = {
                        'run_id': case['run_id'],
                        'time': case_time,
                        'count': 1
                    }
                else:
                    symptom_map[symptom]['affected_test_cases'][tc_id]['count'] += 1
                    if case_time and case_time > (symptom_map[symptom]['affected_test_cases'][tc_id].get('time') or ''):
                        symptom_map[symptom]['affected_test_cases'][tc_id]['run_id'] = case['run_id']
                        symptom_map[symptom]['affected_test_cases'][tc_id]['time'] = case_time

                # Track overall last failure for the symptom
                if case_time:
                    current_last = symptom_map[symptom]['last_failure']
                    if not current_last or case_time > current_last:
                        symptom_map[symptom]['last_failure'] = case_time
                        symptom_map[symptom]['last_failure_run_id'] = case['run_id']
                        symptom_map[symptom]['last_failure_test_case'] = case['test_case_id']
                        if stack_trace_sample:
                            symptom_map[symptom]['stack_trace_sample'] = stack_trace_sample

            # Convert to list and sort
            results = list(symptom_map.values())
            for r in results:
                # Convert affected_test_cases dict to list of objects with run_id and count
                # Only include run_id if the log file still exists
                affected_list = []
                for tc_id, info in r['affected_test_cases'].items():
                    run_id = info['run_id']
                    count = info.get('count', 1)
                    log_path = get_case_log_path(run_id, tc_id)
                    if log_path.exists():
                        affected_list.append({'test_case_id': tc_id, 'last_failure_run_id': run_id, 'failure_count': count})
                    else:
                        affected_list.append({'test_case_id': tc_id, 'last_failure_run_id': None, 'failure_count': count})
                # Sort by failure count descending
                affected_list.sort(key=lambda x: x['failure_count'], reverse=True)
                r['affected_test_cases'] = affected_list

                # Also check if the overall last failure log exists
                if r['last_failure_run_id'] and r['last_failure_test_case']:
                    last_log_path = get_case_log_path(r['last_failure_run_id'], r['last_failure_test_case'])
                    if not last_log_path.exists():
                        r['last_failure_run_id'] = None
                        r['last_failure_test_case'] = None

            results.sort(key=lambda x: x['failure_count'], reverse=True)
            results = results[:top_n]

            return web.json_response({
                "success": True,
                "data": results
            })
        else:
            # By test case name
            results = await database.db.get_failure_counts_by_test_case(
                days_back=days_back,
                top_n=top_n,
                group_hash=group_hash,
                metadata_filters=metadata_filters if metadata_filters else None
            )

            # Check if log files exist for each result
            for r in results:
                if r.get('last_failure_run_id') and r.get('test_case_id'):
                    log_path = get_case_log_path(r['last_failure_run_id'], r['test_case_id'])
                    if not log_path.exists():
                        r['last_failure_run_id'] = None

            return web.json_response({
                "success": True,
                "data": results
            })

    except Exception as e:
        print(f"Error in api_failures_toplist_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def failures_handler(request):
    """Serve the failure top list page."""
    try:
        html = render_template('failures.html')

        # Add cache control headers
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }

        return web.Response(text=html, content_type="text/html", headers=headers)

    except Exception as e:
        print(f"Error in failures_handler: {e}")
        return web.Response(status=500, text=f"Error loading failures page: {str(e)}")


async def analyzer_handler(request):
    """Serve the test results analyzer page."""
    try:
        html = render_template('analyzer.html')

        # Add cache control headers
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }

        return web.Response(text=html, content_type="text/html", headers=headers)

    except Exception as e:
        print(f"Error in analyzer_handler: {e}")
        return web.Response(status=500, text=f"Error loading analyzer page: {str(e)}")


async def matrix_handler(request):
    """Serve the test results matrix page."""
    try:
        html = render_template('matrix.html')

        # Add cache control headers
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }

        return web.Response(text=html, content_type="text/html", headers=headers)

    except Exception as e:
        print(f"Error in matrix_handler: {e}")
        return web.Response(status=500, text=f"Error loading matrix page: {str(e)}")


async def api_classifications_for_run_handler(request):
    """Get test case classifications for all TCs in a run.

    Returns classification data (flaky, fixed, regression) and new TC indicators.
    """
    try:
        run_id = request.match_info.get('run_id')
        if not run_id:
            return web.json_response({
                "success": False,
                "error": "run_id is required"
            }, status=400)

        if not validate_run_id(run_id):
            return web.json_response({
                "success": False,
                "error": "Invalid run_id"
            }, status=400)

        # Get run details to find group_hash
        run_data = await database.db.get_test_run_by_id(run_id)
        if not run_data:
            return web.json_response({
                "success": False,
                "error": "Run not found"
            }, status=404)

        group_hash = run_data.get('group_hash')

        # Get classifications for all test cases in the run
        classifications = await database.db.get_classifications_for_run(run_id, group_hash)

        # Add has_log info to history items
        for tc_id, class_data in classifications.items():
            if 'history' in class_data:
                for hist_item in class_data['history']:
                    hist_run_id = hist_item.get('run_id')
                    if hist_run_id:
                        log_path = get_case_log_path(hist_run_id, tc_id)
                        hist_item['has_log'] = log_path.exists()

        return web.json_response({
            "success": True,
            "data": classifications
        })

    except Exception as e:
        print(f"Error in api_classifications_for_run_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_tc_hover_history_handler(request):
    """Get test case history for hover tooltip.

    Returns both previous results (before current run) and latest results (all runs).
    """
    try:
        test_case_id = request.query.get('test_case_id')
        if not test_case_id:
            return web.json_response({
                "success": False,
                "error": "test_case_id is required"
            }, status=400)

        group_hash = request.query.get('group')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        current_run_id = request.query.get('current_run_id')

        # Get current run's start time if we have a run_id
        current_run_start_time = None
        if current_run_id:
            async with database.db.get_connection() as db:
                cursor = await db.execute(
                    "SELECT start_time FROM test_runs WHERE run_id = ?",
                    (current_run_id,)
                )
                row = await cursor.fetchone()
                if row:
                    current_run_start_time = row[0]

        # Get previous results (before current run)
        previous_history = await database.db.get_test_case_classification_data(
            test_case_id=test_case_id,
            group_hash=group_hash,
            limit=10,
            current_run_id=current_run_id,
            current_run_start_time=current_run_start_time
        )

        # Get latest results (all runs, including current and future)
        latest_history = await database.db.get_test_case_classification_data(
            test_case_id=test_case_id,
            group_hash=group_hash,
            limit=10
        )

        # Helper function to add has_log and format
        def format_history(history_items):
            result = []
            for item in history_items:
                run_id = item.get('run_id')
                log_path = get_case_log_path(run_id, test_case_id)
                result.append({
                    'status': item['status'],
                    'run_id': run_id,
                    'run_name': item.get('run_name'),
                    'run_start_time': item.get('run_start_time'),
                    'has_log': log_path.exists()
                })
            return result

        return web.json_response({
            "success": True,
            "data": {
                "previous": format_history(previous_history),
                "latest": format_history(latest_history)
            }
        })

    except Exception as e:
        print(f"Error in api_tc_hover_history_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_run_hover_history_handler(request):
    """Get test run history for hover tooltip within a group.

    Returns previous runs (before current) and latest runs (all), each limited to 10.
    """
    try:
        group_hash = request.match_info.get('group_hash')
        if not group_hash:
            return web.json_response({
                "success": False,
                "error": "group_hash is required"
            }, status=400)

        if not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        current_run_id = request.query.get('current_run_id')
        current_run_start_time = None
        if current_run_id:
            async with database.db.get_connection() as db:
                cursor = await db.execute(
                    "SELECT start_time FROM test_runs WHERE run_id = ?",
                    (current_run_id,)
                )
                row = await cursor.fetchone()
                if row:
                    current_run_start_time = row[0]

        # Previous runs: before the current run, exclude current
        previous_history = await database.db.get_test_run_history_in_group(
            group_hash=group_hash,
            limit=10,
            exclude_run_id=current_run_id,
            current_run_start_time=current_run_start_time
        )

        # Latest runs: recent runs excluding current (keep existing behavior)
        latest_history = await database.db.get_test_run_history_in_group(
            group_hash=group_hash,
            limit=10,
            exclude_run_id=current_run_id
        )

        return web.json_response({
            "success": True,
            "data": {
                "previous": previous_history,
                "latest": latest_history
            }
        })

    except Exception as e:
        print(f"Error in api_run_hover_history_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_migrate_data_handler(request):
    """Trigger migration of existing test data from disk to database."""
    try:
        return web.json_response({
            "success": False,
            "error": "Migration module not available in this build."
        }, status=501)

    except Exception as e:
        print(f"Error in api_migrate_data_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_server_info_handler(request):
    """Returns server identity and config fingerprint for startup checks."""
    try:
        from importlib.metadata import version as _pkg_version  # py>=3.8
        ver = _pkg_version("testrift-server")
    except Exception:
        ver = "unknown"

    return web.json_response({
        "service": "testrift-server",
        "version": ver,
        "config_path": str(CONFIG_PATH_USED) if CONFIG_PATH_USED else None,
        "config": _testrift_config_fingerprint(CONFIG),
        "config_hash": _testrift_config_hash(CONFIG),
    })


async def api_admin_shutdown_handler(request):
    """Shutdown endpoint used for local auto-restart flows.

    This is intentionally restricted to localhost callers and requires the running config_hash.
    """
    remote = request.remote or ""
    if remote not in ("127.0.0.1", "::1", "localhost"):
        return web.json_response({"success": False, "error": "forbidden"}, status=403)

    expected = _testrift_config_hash(CONFIG)
    provided = request.headers.get("X-TestRift-Config-Hash")
    if not provided:
        try:
            body = await request.json()
            provided = body.get("config_hash")
        except Exception:
            provided = None

    if provided != expected:
        return web.json_response({"success": False, "error": "config_hash mismatch"}, status=403)

    # Respond first, then hard-exit quickly to ensure the port is released even if the loop is busy.
    loop = asyncio.get_running_loop()
    loop.call_later(0.2, lambda: os._exit(0))
    return web.json_response({"success": True})


# --- Main app setup ---

app = web.Application()
ws_server = WebSocketServer()

app["ws_server"] = ws_server
# Base routes
routes = [
    web.get("/", index_handler),
    web.get("/groups/{group_hash}", group_runs_handler),
    web.get("/testRun/{run_id}/index.html", test_run_index_handler),
    web.get("/testRun/{run_id}/log/{test_case_id}.html", test_case_log_handler),
    web.get("/testRun/{tail:.*}", static_handler),
    web.get("/static/{path:.*}", static_file_handler),
    web.get("/ws/{tail:.*}", ws_server.handle_ws),
    web.get("/export/{run_id}.zip", zip_export_handler),
    web.get("/health", health_handler),
    web.get("/analyzer", analyzer_handler),
    web.get("/matrix", matrix_handler),
    web.get("/failures", failures_handler),

    # Test Results Analyzer API routes
    web.get("/api/test-runs", api_test_runs_handler),
    web.get("/api/test-runs/{run_id}", api_test_run_details_handler),
    web.get("/api/test-results/for-runs", api_test_results_for_runs_handler),
    web.get("/api/test-results/over-time", api_test_results_over_time_handler),
    web.get("/api/test-case/history", api_test_case_history_handler),
    web.get("/api/test-case/history-with-links", api_test_case_history_with_links_handler),
    web.get("/api/metadata/keys", api_metadata_keys_handler),
    web.get("/api/metadata/values", api_metadata_values_handler),
    web.get("/api/groups/{group_hash}", api_group_details_handler),
    web.get("/api/failures/toplist", api_failures_toplist_handler),
    web.get("/api/classifications/{run_id}", api_classifications_for_run_handler),
    web.get("/api/tc-hover-history", api_tc_hover_history_handler),
    web.get("/api/run-hover-history/{group_hash}", api_run_hover_history_handler),
    web.post("/api/migrate-data", api_migrate_data_handler),
    web.get("/api/server-info", api_server_info_handler),
    web.post("/api/admin/shutdown", api_admin_shutdown_handler),
]

# Add attachment routes only if enabled
if ATTACHMENTS_ENABLED:
    routes.extend([
        web.post("/api/attachments/{run_id}/{test_case_id}/upload", upload_attachment_handler),
        web.get("/api/attachments/{run_id}/{test_case_id}/list", list_attachments_handler),
        web.get("/api/attachments/{run_id}/{test_case_id}/download/{filename}", download_attachment_handler),
    ])

app.add_routes(routes)


async def on_startup(app):
    # Initialize database with configured data directory
    try:
        database.initialize_database(DATA_DIR)
        await database.db.initialize()
        log_event("database_initialized")
    except Exception as e:
        log_event("database_init_error", error=str(e))

    # Run an immediate cleanup sweep at startup
    try:
        await cleanup_runs_sweep()
        await cleanup_abandoned_running_runs()
    except Exception as e:
        log_event("startup_cleanup_error", error=str(e))
    app["cleanup_task"] = asyncio.create_task(cleanup_old_runs())


async def on_cleanup(app):
    app["cleanup_task"].cancel()
    await app["cleanup_task"]


app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

def main(argv=None):
    parser = argparse.ArgumentParser(prog="testrift-server")
    parser.add_argument(
        "--restart-on-config",
        action="store_true",
        help="If a server is already running on the configured port with a different config, "
             "ask it to shut down and then start with the new config.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Determine host based on configuration
    host = "127.0.0.1" if LOCALHOST_ONLY else "0.0.0.0"

    # Detect already-running server on the configured port.
    new_hash = _testrift_config_hash(CONFIG)
    try:
        running = _get_running_server_info(PORT)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 2

    if running is not None:
        running_hash = running.get("config_hash")
        if running_hash == new_hash:
            print(f"TestRift server already running on 127.0.0.1:{PORT} with identical config. Exiting.")
            return 0

        print(f"ERROR: TestRift server already running on 127.0.0.1:{PORT} but config differs.")
        print(f"  running config_path: {running.get('config_path')}")
        print(f"  running config_hash: {running_hash}")
        print(f"  new     config_path: {str(CONFIG_PATH_USED) if CONFIG_PATH_USED else None}")
        print(f"  new     config_hash: {new_hash}")
        if args.restart_on_config and running_hash:
            print("Attempting to restart running server with new config...")
            if not _request_running_server_shutdown(PORT, running_hash):
                print("ERROR: Failed to request shutdown of running server.")
                return 2

            # Wait for the running server to exit and release the port.
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if _get_running_server_info(PORT) is None:
                    break
                time.sleep(0.2)
            else:
                print("ERROR: Timed out waiting for running server to shut down.")
                return 2

            print("Old server stopped. Starting new server...")
        else:
            return 2

    print(f"Starting server on {host}:{PORT}")
    print(f"Default retention days: {DEFAULT_RETENTION_DAYS}")
    print(f"Data directory: {DATA_DIR}")
    print(f"Localhost only: {LOCALHOST_ONLY}")
    print(f"Attachments enabled: {ATTACHMENTS_ENABLED}")
    if ATTACHMENTS_ENABLED:
        max_size_mb = ATTACHMENT_MAX_SIZE // (1024 * 1024)
        print(f"Max attachment size: {max_size_mb}MB")

    web.run_app(app, host=host, port=PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
