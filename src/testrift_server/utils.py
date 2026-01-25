"""
Utility functions for TestRift server.

Path helpers, validators, sanitizers, and file operations.
"""

import hashlib
import json
import re
import uuid
from datetime import datetime, UTC
from pathlib import Path

from .config import DATA_DIR

GROUP_HASH_LENGTH = 16
CASE_STORAGE_DIR_NAME = "cases"
CASE_LOG_FILE_SUFFIX = "_log.jsonl"
CASE_STACK_FILE_SUFFIX = "_stack.jsonl"
TC_ID_FIELD = "tc_id"
TC_FULL_NAME_FIELD = "tc_full_name"


# --- Time utilities ---

def now_utc_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"


def parse_iso(dtstr):
    """Parse ISO 8601 datetime string."""
    return datetime.fromisoformat(dtstr.replace("Z", ""))


# --- Path utilities ---

def get_run_path(run_id):
    """Get the path for a test run's data directory."""
    return DATA_DIR / run_id


def get_run_meta_path(run_id):
    """Get the path for a test run's meta.json file."""
    return get_run_path(run_id) / "meta.json"


def generate_storage_id():
    """Return a short, filesystem-friendly identifier for per-test storage."""
    return uuid.uuid4().hex[:16]


def get_case_storage_dir(run_id, storage_id):
    """Get the storage directory for a specific test case."""
    if not storage_id or not isinstance(storage_id, str):
        raise ValueError("storage_id must be a non-empty string")
    return get_run_path(run_id) / CASE_STORAGE_DIR_NAME / storage_id


def _ensure_tc_id(run_id, tc_full_name=None, tc_id=None, run=None):
    """Ensure tc_id is available, raising if not provided."""
    if tc_id:
        return tc_id
    raise ValueError("tc_id is required - it must be provided directly")


def get_case_log_path(run_id, tc_full_name=None, *, tc_id=None, run=None):
    """Get the path for a test case's log file."""
    resolved_id = _ensure_tc_id(run_id, tc_full_name, tc_id, run)
    cases_dir = get_run_path(run_id) / CASE_STORAGE_DIR_NAME
    cases_dir.mkdir(parents=True, exist_ok=True)
    return cases_dir / f"{resolved_id}{CASE_LOG_FILE_SUFFIX}"


def get_case_stack_path(run_id, tc_full_name=None, *, tc_id=None, run=None):
    """Get the path for a test case's stack trace file."""
    resolved_id = _ensure_tc_id(run_id, tc_full_name, tc_id, run)
    cases_dir = get_run_path(run_id) / CASE_STORAGE_DIR_NAME
    cases_dir.mkdir(parents=True, exist_ok=True)
    return cases_dir / f"{resolved_id}{CASE_STACK_FILE_SUFFIX}"


def get_attachments_dir(run_id, tc_full_name=None, *, tc_id=None, run=None):
    """Get the attachments directory for a specific test case."""
    resolved_id = _ensure_tc_id(run_id, tc_full_name, tc_id, run)
    return get_case_storage_dir(run_id, resolved_id) / "attachments"


def get_attachment_path(run_id, filename, tc_full_name=None, *, tc_id=None, run=None):
    """Get the full path for a specific attachment."""
    sanitized_filename = sanitize_filename(filename)
    return get_attachments_dir(run_id, tc_full_name, tc_id=tc_id, run=run) / sanitized_filename


# --- File operations ---

def read_jsonl(file_path):
    """Read a JSONL file and return a list of parsed JSON objects."""
    with open(file_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- Validation functions ---

def sanitize_filename(filename):
    """Sanitize filename by replacing invalid characters with safe alternatives."""
    if not filename or not isinstance(filename, str):
        return "invalid_filename"

    # Remove any path separators and directory traversal attempts
    filename = filename.replace('/', '_').replace('\\', '_')
    filename = re.sub(r'\.\.+', '_', filename)  # Remove .. sequences

    # Replace invalid characters for Windows file paths
    invalid_chars = '<>:"|?*[]' + chr(0)
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
    """Validate that run_id is safe and doesn't contain path traversal."""
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
    if '%' in run_id:
        # Check that all percent-encoded sequences are valid (%XX where XX is hex)
        percent_pattern = re.compile(r'%[0-9A-Fa-f]{2}')
        # Replace all valid percent-encoded sequences with a placeholder
        temp = percent_pattern.sub('_', run_id)
        # If there's still a % left, it's invalid
        if '%' in temp:
            return False, "Run ID contains invalid percent encoding (must be %XX where XX is hexadecimal)"
        remaining = temp
    else:
        remaining = run_id

    # Check that remaining characters are URL-safe
    url_safe_pattern = re.compile(r'^[A-Za-z0-9\-_.~]+$')
    if not url_safe_pattern.match(remaining):
        return False, "Run ID contains invalid characters (must be URL-safe or percent-encoded)"

    # Limit length
    if len(run_id) > 200:
        return False, "Run ID is too long (maximum 200 characters)"

    return True, None


def validate_test_case_id(test_case_id):
    """Validate that test_case_id is safe.
    NUnit IDs are like "0-1008" (alphanumeric and hyphens)."""
    if not test_case_id or not isinstance(test_case_id, str):
        return False

    # Limit length
    if len(test_case_id) > 20:
        return False

    # NUnit test IDs contain only alphanumeric characters and hyphens
    if not re.match(r'^[a-zA-Z0-9\-]+$', test_case_id):
        return False

    return True


def validate_group_hash_value(group_hash):
    """Ensure group hash only contains safe hex characters."""
    if not group_hash or not isinstance(group_hash, str):
        return False
    return re.fullmatch(r"[0-9a-fA-F]{6,64}", group_hash) is not None


# --- Group hash functions ---

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


# --- Test case helpers ---

def find_test_case_by_tc_id(run, tc_id):
    """Return the TestCaseData matching the tc_id (hash), if any."""
    if not tc_id:
        return None
    return run.test_cases_by_tc_id.get(tc_id)


def get_run_and_test_case_by_tc_id(app, run_id, tc_id):
    """Return (run, test_case) for the provided tc_id (hash), loading from disk if needed."""
    from .models import TestRunData

    ws_server = app["ws_server"]
    run = ws_server.test_runs.get(run_id)
    test_case = None
    if run:
        test_case = find_test_case_by_tc_id(run, tc_id)
        if test_case:
            return run, test_case

    run = TestRunData.load_from_disk(run_id)
    if not run:
        return None, None

    return run, find_test_case_by_tc_id(run, tc_id)


def get_run_and_test_case_by_full_name(app, run_id, tc_full_name):
    """Return (run, test_case) for the provided tc_full_name, loading from disk if needed."""
    from .models import TestRunData

    ws_server = app["ws_server"]
    run = ws_server.test_runs.get(run_id)
    test_case = None
    if run:
        test_case = run.test_cases.get(tc_full_name)
        if test_case:
            return run, test_case

    run = TestRunData.load_from_disk(run_id)
    if not run:
        return None, None

    return run, run.test_cases.get(tc_full_name)


def ensure_test_case_entry(run, tc_full_name, meta_hint=None):
    """Ensure a TestCaseData entry exists for a run, creating one if missing."""
    import logging
    from .models import TestCaseData

    logger = logging.getLogger(__name__)

    existing = run.test_cases.get(tc_full_name)
    if existing:
        return existing, False

    meta = dict(meta_hint or {})
    if TC_ID_FIELD not in meta:
        raise ValueError(f"tc_id missing for test case {tc_full_name} - cannot create entry")

    meta.setdefault(TC_FULL_NAME_FIELD, tc_full_name)

    logger.warning(
        "Creating placeholder test case entry for %s in run %s because it was missing",
        tc_full_name,
        run.id,
    )
    placeholder = TestCaseData(run, tc_full_name, meta)
    run.test_cases[tc_full_name] = placeholder
    run.test_cases_by_tc_id[placeholder.tc_id] = placeholder
    return placeholder, True
