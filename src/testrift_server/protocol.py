"""
Optimized MessagePack Protocol Constants

This module defines the binary protocol constants for efficient WebSocket communication.
All message types, status codes, and field keys use compact numeric/short string representations.
"""

# =============================================================================
# MESSAGE TYPES (t field) - Use integers for minimal overhead
# =============================================================================
MSG_RUN_STARTED = 1
MSG_RUN_STARTED_RESPONSE = 2
MSG_TEST_CASE_STARTED = 3
MSG_LOG_BATCH = 4
MSG_EXCEPTION = 5
MSG_TEST_CASE_FINISHED = 6
MSG_RUN_FINISHED = 7
MSG_BATCH = 8
MSG_HEARTBEAT = 9
MSG_STRING_TABLE = 10  # For registering component/channel strings

# Reverse lookup for logging/debugging
MSG_TYPE_NAMES = {
    MSG_RUN_STARTED: "run_started",
    MSG_RUN_STARTED_RESPONSE: "run_started_response",
    MSG_TEST_CASE_STARTED: "test_case_started",
    MSG_LOG_BATCH: "log_batch",
    MSG_EXCEPTION: "exception",
    MSG_TEST_CASE_FINISHED: "test_case_finished",
    MSG_RUN_FINISHED: "run_finished",
    MSG_BATCH: "batch",
    MSG_HEARTBEAT: "heartbeat",
    MSG_STRING_TABLE: "string_table",
}

# =============================================================================
# STATUS CODES (s field) - Use integers
# =============================================================================
STATUS_RUNNING = 1
STATUS_PASSED = 2
STATUS_FAILED = 3
STATUS_SKIPPED = 4
STATUS_ABORTED = 5
STATUS_FINISHED = 6  # For runs

STATUS_NAMES = {
    STATUS_RUNNING: "running",
    STATUS_PASSED: "passed",
    STATUS_FAILED: "failed",
    STATUS_SKIPPED: "skipped",
    STATUS_ABORTED: "aborted",
    STATUS_FINISHED: "finished",
}

STATUS_FROM_NAME = {v: k for k, v in STATUS_NAMES.items()}


def status_code_to_name(code: int) -> str:
    """Convert status code to string name."""
    return STATUS_NAMES.get(code, f"unknown_{code}")

# =============================================================================
# DIRECTION CODES (d field) - Use integers
# =============================================================================
DIR_TX = 1  # Transmit (host -> device)
DIR_RX = 2  # Receive (device -> host)

DIR_NAMES = {
    DIR_TX: "tx",
    DIR_RX: "rx",
}

DIR_FROM_NAME = {v: k for k, v in DIR_NAMES.items()}

# =============================================================================
# PHASE CODES (p field) - Use integers
# =============================================================================
PHASE_TEARDOWN = 1

PHASE_NAMES = {
    PHASE_TEARDOWN: "teardown",
}

PHASE_FROM_NAME = {v: k for k, v in PHASE_NAMES.items()}

# =============================================================================
# FIELD KEYS - Short strings for MessagePack efficiency
# MessagePack encodes strings < 32 chars as fixstr (1 byte length prefix)
# Single-char keys are optimal: 1 byte for key + value
# =============================================================================

# Common fields
F_TYPE = "t"           # Message type (int)
F_RUN_ID = "r"         # Run ID (string)
F_RUN_NAME = "n"       # Run name (string)
F_STATUS = "s"         # Status code (int)
F_TIMESTAMP = "ts"     # Timestamp in milliseconds since epoch (int64)
F_ERROR = "err"        # Error message (string)

# Test case fields
F_TC_FULL_NAME = "f"   # Test case full name (string)
F_TC_ID = "i"          # Test case ID (string)
F_TC_META = "tm"       # Test case metadata (object)

# Log entry fields
F_MESSAGE = "m"        # Log message (string)
F_COMPONENT = "c"      # Component ID or [id, name] for first occurrence (int or array)
F_CHANNEL = "ch"       # Channel ID or [id, name] for first occurrence (int or array)
F_DIR = "d"            # Direction code (int: 1=tx, 2=rx)
F_DIRECTION = "d"      # Alias for F_DIR
F_PHASE = "p"          # Phase code (int: 1=teardown)
F_ENTRIES = "e"        # Log entries array

# Batch fields
F_EVENTS = "ev"        # Events array in batch message
F_EVENT_TYPE = "et"    # Event type within batch (int)

# Exception fields
F_EXCEPTION_TYPE = "xt"   # Exception type name (string)
F_STACK_TRACE = "st"      # Stack trace lines (array of strings)
F_IS_ERROR = "ie"         # Is error flag (bool)

# Run metadata fields
F_USER_METADATA = "md"    # User metadata (object)
F_GROUP = "g"             # Group info (object)
F_GROUP_NAME = "gn"       # Group name (string)
F_GROUP_METADATA = "gm"   # Group metadata (object)
F_GROUP_HASH = "gh"       # Group hash (string)
F_RETENTION_DAYS = "rd"   # Retention days (int)
F_LOCAL_RUN = "lr"        # Local run flag (bool)
F_START_TIME = "st"       # Start time (int64 ms)
F_END_TIME = "et"         # End time (int64 ms)

# Response fields
F_RUN_URL = "ru"          # Run URL (string)
F_GROUP_URL = "gu"        # Group URL (string)

# Counts (for UI updates)
F_COUNTS = "ct"           # Counts object
F_COUNT_PASSED = "cp"     # Passed count
F_COUNT_FAILED = "cf"     # Failed count
F_COUNT_SKIPPED = "cs"    # Skipped count
F_COUNT_ABORTED = "ca"    # Aborted count

# String table fields
F_STRINGS = "str"         # String table entries: {id: string, ...}

# =============================================================================
# STRING TABLE MANAGEMENT
# =============================================================================
# Component and channel names are registered in a per-connection string table.
# First occurrence: [id, "string_value"]
# Subsequent: id (integer)
#
# The server maintains a reverse lookup to decode IDs back to strings.
# The client tracks assigned IDs to avoid re-sending full strings.


def decode_interned_string(value, string_table: dict) -> str:
    """
    Decode a potentially interned string.
    
    Args:
        value: Either an integer ID, a [id, string] pair for first occurrence, or a raw string
        string_table: Dict mapping ID -> string (updated if new string is registered)
    
    Returns:
        The decoded string value
    """
    if isinstance(value, int):
        # ID reference
        return string_table.get(value, f"<unknown:{value}>")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        # First occurrence: [id, string]
        str_id, str_value = value
        string_table[str_id] = str_value
        return str_value
    elif isinstance(value, str):
        # Raw string (for backward compatibility or non-interned values)
        return value
    else:
        return str(value) if value is not None else ""


def encode_interned_string(value: str, string_table: dict, next_id_holder: list) -> object:
    """
    Encode a string with interning.
    
    Args:
        value: The string to encode
        string_table: Dict mapping string -> ID (updated if new string)
        next_id_holder: List with single element [next_id] for generating new IDs
    
    Returns:
        Integer ID if already known, or [id, string] for first occurrence
    """
    if not value:
        return None
    
    if value in string_table:
        return string_table[value]
    
    # New string - assign ID
    new_id = next_id_holder[0]
    next_id_holder[0] += 1
    string_table[value] = new_id
    return [new_id, value]


def timestamp_to_ms(iso_string: str) -> int:
    """Convert ISO 8601 timestamp string to milliseconds since epoch."""
    from datetime import datetime, timezone
    
    if not iso_string:
        return 0
    
    # Handle various formats
    ts = iso_string.rstrip('Z')
    
    # Try parsing with microseconds
    try:
        if '.' in ts:
            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
        else:
            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def ms_to_timestamp(ms: int) -> str:
    """Convert milliseconds since epoch to ISO 8601 timestamp string."""
    from datetime import datetime, timezone
    
    if not ms:
        return ""
    
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{(ms % 1000):03d}Z"
