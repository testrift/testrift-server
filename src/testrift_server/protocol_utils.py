"""Utility helpers for decoding optimized MessagePack protocol payloads."""
from __future__ import annotations

from typing import Any, Dict

from .protocol import (
    MSG_RUN_STARTED,
    MSG_RUN_STARTED_RESPONSE,
    MSG_TEST_CASE_STARTED,
    MSG_LOG_BATCH,
    MSG_EXCEPTION,
    MSG_TEST_CASE_FINISHED,
    MSG_RUN_FINISHED,
    MSG_BATCH,
    MSG_HEARTBEAT,
    MSG_METRICS,
    MSG_RUN_PREPARE,
    MSG_RUN_PREPARE_RESPONSE,
    STATUS_RUNNING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_ABORTED,
    STATUS_FINISHED,
    DIR_TX,
    DIR_RX,
    PHASE_TEARDOWN,
    F_TYPE,
    F_RUN_ID,
    F_RUN_NAME,
    F_STATUS,
    F_TIMESTAMP,
    F_TC_FULL_NAME,
    F_TC_ID,
    F_MESSAGE,
    F_COMPONENT,
    F_CHANNEL,
    F_DIR,
    F_PHASE,
    F_ENTRIES,
    F_EVENTS,
    F_EVENT_TYPE,
    F_EXCEPTION_TYPE,
    F_STACK_TRACE,
    F_IS_ERROR,
    F_USER_METADATA,
    F_RETENTION_DAYS,
    F_LOCAL_RUN,
    F_ERROR,
    F_RUN_URL,
    F_TARGET_KEY,
    F_PURPOSE,
    F_PARENT_RUN_ID,
    F_SOURCES,
    F_SOURCE_BRANCH,
    F_SOURCE_REVISION,
    F_SOURCE_REPOSITORY_URL,
    F_SOURCE_DIRTY,
    F_TARGET_URL,
    F_COLLECTION_URLS,
    F_METRICS,
    F_CPU,
    F_MEMORY,
    F_NET,
    F_NET_INTERFACES,
    status_code_to_name,
    ms_to_timestamp,
    decode_interned_string,
)

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
    MSG_METRICS: "metrics",
    MSG_RUN_PREPARE: "run_prepare",
    MSG_RUN_PREPARE_RESPONSE: "run_prepare_response",
}


def normalize_message(data: Dict[str, Any], string_table: Dict[int, str]) -> Dict[str, Any]:
    """Normalize an optimized-format message to the internal format."""
    msg_type_code = data.get(F_TYPE)
    if not isinstance(msg_type_code, int):
        raise ValueError(f"Invalid message type: {msg_type_code}")
    msg_type = MSG_TYPE_NAMES.get(msg_type_code, f"unknown_{msg_type_code}")

    result: Dict[str, Any] = {"type": msg_type}

    key_mappings = {
        F_RUN_ID: "run_id",
        F_RUN_NAME: "run_name",
        F_STATUS: "status",
        F_TIMESTAMP: "timestamp",
        F_TC_FULL_NAME: "tc_full_name",
        F_TC_ID: "tc_id",
        F_MESSAGE: "message",
        F_USER_METADATA: "user_metadata",
        F_RETENTION_DAYS: "retention_days",
        F_LOCAL_RUN: "local_run",
        F_ERROR: "error",
        F_EXCEPTION_TYPE: "exception_type",
        F_STACK_TRACE: "stack_trace",
        F_IS_ERROR: "is_error",
        F_ENTRIES: "entries",
        F_EVENTS: "events",
        F_EVENT_TYPE: "event_type",
        F_RUN_URL: "run_url",
        F_TARGET_URL: "target_url",
        F_COLLECTION_URLS: "collection_urls",
        F_METRICS: "metrics",
    }

    for short_key, long_key in key_mappings.items():
        value = data.get(short_key)
        if value is not None:
            if long_key == "status" and isinstance(value, int):
                value = status_code_to_name(value)
            elif long_key == "timestamp" and isinstance(value, int):
                value = ms_to_timestamp(value)
            result[long_key] = value

    if msg_type in ("run_prepare", "run_started"):
        is_prepared_run_activation = msg_type == "run_started" and F_RUN_ID in data
        has_context_fields = any(
            field in data for field in (F_TARGET_KEY, F_PURPOSE, F_PARENT_RUN_ID, F_SOURCES)
        )
        if not is_prepared_run_activation or has_context_fields:
            result.update(normalize_run_context(data))

    if "start_time" not in result and "timestamp" in result:
        result["start_time"] = result["timestamp"]

    if msg_type == "test_case_started":
        tc_meta = {}
        if "status" in result:
            tc_meta["status"] = result["status"]
        if "timestamp" in result:
            tc_meta["start_time"] = result["timestamp"]
        if tc_meta:
            result["tc_meta"] = tc_meta

    if "entries" in result:
        result["entries"] = [
            normalize_log_entry(e, string_table) for e in result["entries"]
        ]

    if "events" in result:
        result["events"] = [
            normalize_event(e, string_table) for e in result["events"]
        ]

    # Normalize metrics array (convert short field names to long names)
    if "metrics" in result and isinstance(result["metrics"], list):
        result["metrics"] = [
            normalize_metric_sample(m) for m in result["metrics"]
        ]

    return result


_RUN_PURPOSES = {"nightly", "release", "feature", "manual", "sanity", "rerun"}


def normalize_run_context(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and expand compact Target and source context fields."""
    target_key = _required_trimmed_string(data.get(F_TARGET_KEY), "target_key")
    purpose = _required_trimmed_string(data.get(F_PURPOSE), "purpose").lower()
    if purpose not in _RUN_PURPOSES:
        raise ValueError(f"Unsupported purpose: {purpose}")

    parent_run_id = data.get(F_PARENT_RUN_ID)
    if purpose == "rerun":
        parent_run_id = _required_trimmed_string(parent_run_id, "parent_run_id")
    elif parent_run_id is not None:
        raise ValueError("parent_run_id is only allowed for rerun purpose")

    raw_sources = data.get(F_SOURCES)
    if not isinstance(raw_sources, dict):
        raise ValueError("sources must be a map keyed by source role")

    sources: Dict[str, Dict[str, Any]] = {}
    for source_role, raw_source in raw_sources.items():
        role = _required_trimmed_string(source_role, "source role")
        if not isinstance(raw_source, dict):
            raise ValueError(f"source '{role}' must be an object")

        source = {
            "branch": _required_trimmed_string(raw_source.get(F_SOURCE_BRANCH), f"source '{role}' branch"),
            "revision": _required_trimmed_string(raw_source.get(F_SOURCE_REVISION), f"source '{role}' revision"),
        }
        repository_url = raw_source.get(F_SOURCE_REPOSITORY_URL)
        if repository_url is not None:
            source["repository_url"] = _required_trimmed_string(
                repository_url, f"source '{role}' repository_url"
            )
        dirty = raw_source.get(F_SOURCE_DIRTY)
        if dirty is not None:
            if not isinstance(dirty, bool):
                raise ValueError(f"source '{role}' dirty must be a boolean")
            source["dirty"] = dirty
        sources[role] = source

    result: Dict[str, Any] = {
        "target_key": target_key,
        "purpose": purpose,
        "sources": sources,
    }
    if parent_run_id is not None:
        result["parent_run_id"] = parent_run_id
    return result


def _required_trimmed_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def normalize_metric_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a metrics sample from short keys to long keys."""
    result = {
        "ts": sample.get(F_TIMESTAMP) or sample.get("ts"),
        "cpu": sample.get(F_CPU) or sample.get("cpu", 0),
        "mem": sample.get(F_MEMORY) or sample.get("mem", 0),
        "net": sample.get(F_NET) or sample.get("net", 0),
    }
    # Include network interface details if present
    ni = sample.get(F_NET_INTERFACES) or sample.get("ni")
    if ni:
        result["ni"] = ni
    return result


def normalize_event(event: Dict[str, Any], string_table: Dict[int, str]) -> Dict[str, Any]:
    """Normalize an event nested inside a batch message."""
    event_type_code = event.get(F_EVENT_TYPE)
    if not isinstance(event_type_code, int):
        raise ValueError(f"Invalid event type: {event_type_code}")
    event_type = MSG_TYPE_NAMES.get(event_type_code, f"unknown_{event_type_code}")

    result: Dict[str, Any] = {"event_type": event_type}

    key_mappings = {
        F_TC_FULL_NAME: "tc_full_name",
        F_TC_ID: "tc_id",
        F_STATUS: "status",
        F_TIMESTAMP: "timestamp",
        F_MESSAGE: "message",
        F_EXCEPTION_TYPE: "exception_type",
        F_STACK_TRACE: "stack_trace",
        F_IS_ERROR: "is_error",
        F_ENTRIES: "entries",
    }

    for short_key, long_key in key_mappings.items():
        value = event.get(short_key)
        if value is not None:
            if long_key == "status" and isinstance(value, int):
                value = status_code_to_name(value)
            elif long_key == "timestamp" and isinstance(value, int):
                value = ms_to_timestamp(value)
            result[long_key] = value

    if event_type == "test_case_started":
        tc_meta = {}
        if "status" in result:
            tc_meta["status"] = result["status"]
        if "timestamp" in result:
            tc_meta["start_time"] = result["timestamp"]
        if tc_meta:
            result["tc_meta"] = tc_meta

    if "entries" in result:
        result["entries"] = [
            normalize_log_entry(e, string_table) for e in result["entries"]
        ]

    return result


def normalize_log_entry(entry: Dict[str, Any], string_table: Dict[int, str]) -> Dict[str, Any]:
    """Normalize a log entry, decoding interned component/channel strings."""
    result: Dict[str, Any] = {}

    ts = entry.get(F_TIMESTAMP)
    if isinstance(ts, int):
        result["timestamp"] = ms_to_timestamp(ts)

    if F_MESSAGE in entry:
        result["message"] = entry.get(F_MESSAGE, "")

    comp = entry.get(F_COMPONENT)
    if comp is not None:
        result["component"] = decode_interned_string(comp, string_table)

    ch = entry.get(F_CHANNEL)
    if ch is not None:
        result["channel"] = decode_interned_string(ch, string_table)

    dir_val = entry.get(F_DIR)
    if dir_val is not None:
        if dir_val == DIR_TX:
            result["dir"] = "tx"
        elif dir_val == DIR_RX:
            result["dir"] = "rx"

    phase_val = entry.get(F_PHASE)
    if phase_val is not None and phase_val == PHASE_TEARDOWN:
        result["phase"] = "teardown"

    return result


def decode_log_entries(
    raw_entries: list[Dict[str, Any]],
    string_table: Dict[int, str] | None = None
) -> list[Dict[str, Any]]:
    """Decode a list of compact log entries to normalized format.

    Args:
        raw_entries: List of compact log entries with short keys
        string_table: Optional pre-populated string table for interned strings.
                     If not provided, a new empty table is used.

    This is used for static HTML export where entries need to be pre-decoded.
    For live server mode, entries are sent as-is and decoded in JavaScript.
    """
    if string_table is None:
        string_table = {}
    # Make a copy to avoid modifying the original during decode
    table = dict(string_table)
    decoded: list[Dict[str, Any]] = []
    for entry in raw_entries:
        normalized = normalize_log_entry(entry, table)
        # Require timestamp
        if normalized.get("timestamp"):
            decoded.append(normalized)
    return decoded
