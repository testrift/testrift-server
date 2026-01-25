"""
Data models for TestRift server.

TestRunData and TestCaseData classes for managing test run state.
"""

import asyncio
import json
import logging
from datetime import datetime, UTC

import aiofiles

from .utils import (
    get_run_meta_path,
    get_case_log_path,
    get_case_stack_path,
    read_jsonl,
    normalize_group_payload,
    compute_group_hash,
    TC_ID_FIELD,
    TC_FULL_NAME_FIELD,
)

logger = logging.getLogger(__name__)


class TestRunData:
    """Represents a test run with its metadata and test cases."""
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
        self.abort_reason = None  # Reason for abort (if status is "aborted")
        self.start_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
        self.end_time = None
        self.test_cases: dict[str, 'TestCaseData'] = {}  # tc_full_name -> TestCaseData
        self.test_cases_by_tc_id: dict[str, 'TestCaseData'] = {}  # tc_id (hash) -> TestCaseData
        self.logs = {}  # tc_full_name -> list of logs entries
        self.last_update = datetime.now(UTC)

    def update_last(self):
        """Update the last activity timestamp."""
        self.last_update = datetime.now(UTC)

    def to_dict(self):
        """Serialize the test run to a dictionary."""
        result = {
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
            "test_cases": {tc_full_name: tc.to_dict() for tc_full_name, tc in self.test_cases.items()},
        }
        if self.abort_reason:
            result["abort_reason"] = self.abort_reason
        return result

    @classmethod
    def from_dict(cls, run_id, meta):
        """Create a TestRunData instance from a dictionary."""
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
        run.abort_reason = meta.get("abort_reason")
        run.start_time = meta.get("start_time", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z")
        run.end_time = meta.get("end_time", "")
        run.test_cases = {tc_full_name: TestCaseData.from_dict(run, tc_full_name, tc_meta) for tc_full_name, tc_meta in meta.get("test_cases", {}).items()}
        run.test_cases_by_tc_id = {tc.tc_id: tc for tc in run.test_cases.values() if getattr(tc, "tc_id", None)}
        return run

    @staticmethod
    def load_from_disk(run_id):
        """Load a test run from disk by its run_id."""
        meta_path = get_run_meta_path(run_id)
        if not meta_path.exists():
            return None
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return TestRunData.from_dict(run_id, meta)


class TestCaseData:
    """Represents a test case within a test run."""
    __test__ = False  # Tell pytest to ignore this class

    _ALLOWED_LOG_FIELDS = {"timestamp", "message", "component", "channel", "dir", "phase"}
    _ALLOWED_PHASE_VALUES = {"teardown"}
    _ALLOWED_DIR_VALUES = {"tx", "rx"}

    def __init__(self, run, tc_full_name, meta={}):
        self.run = run
        self.id = tc_full_name
        self.full_name = tc_full_name
        self.status = meta.get("status", "running")
        self.start_time = meta.get("start_time", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z")
        self.end_time = meta.get("end_time", None)
        self.logs = meta.get("logs", [])
        self.stack_traces = meta.get("stack_traces", [])
        self.subscribers = []

        # tc_id MUST be in meta - it should have been generated once when test case started
        # and stored in meta.json. If it's missing, that's a bug.
        if TC_ID_FIELD not in meta:
            raise ValueError(f"tc_id missing in meta for test case {tc_full_name}. tc_id must be generated once and stored.")
        self.tc_id = meta[TC_ID_FIELD]

        # Ensure persisted stack traces are loaded even if meta.json lacked them
        stack_path = get_case_stack_path(self.run.id, tc_id=self.tc_id)
        if stack_path.exists():
            try:
                file_traces = read_jsonl(stack_path)
                if file_traces:
                    self.stack_traces = file_traces
            except Exception as e:
                logger.error(f"Failed to load stack traces for {self.id}: {e}")

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
        # Require timestamp, but allow empty/whitespace messages (they'll be filtered by JS if needed)
        if not timestamp:
            return None
        # Allow None/empty message - convert to empty string
        if message is None:
            message = ""

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
        """Serialize the test case to a dictionary."""
        return {
            TC_ID_FIELD: self.tc_id,
            TC_FULL_NAME_FIELD: self.id,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "logs": self.logs,
            "stack_traces": self.stack_traces,
            # Note: subscribers are not serialized
        }

    @classmethod
    def from_dict(cls, run, tc_full_name, meta):
        """Create a TestCaseData instance from a dictionary."""
        return cls(run, tc_full_name, meta)

    async def add_log_entries(self, entries):
        """Add log entries to this test case using async file I/O."""
        log_path = get_case_log_path(self.run.id, tc_id=self.tc_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Sanitize all entries first
        sanitized_entries = []
        skipped_count = 0
        for entry in entries:
            log_entry = self._sanitize_log_entry(entry)
            if not log_entry:
                skipped_count += 1
                continue
            sanitized_entries.append(log_entry)

        if skipped_count > 0:
            logger.warning(f"Skipped {skipped_count} log entries for {self.tc_id} (missing timestamp or message)")

        if not sanitized_entries:
            return

        # Write all entries in one async operation
        lines = [json.dumps(entry) + "\n" for entry in sanitized_entries]
        async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
            await f.writelines(lines)

        # Update in-memory logs and notify subscribers
        self.logs.extend(sanitized_entries)

        # Batch notify subscribers
        if self.subscribers:
            for entry in sanitized_entries:
                for subscriber in self.subscribers:
                    await subscriber.put(entry)

    async def add_stack_trace(self, trace_entry):
        """Add a stack trace entry to this test case using async file I/O."""
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

        stack_path = get_case_stack_path(self.run.id, tc_id=self.tc_id)
        stack_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Append to disk file using async I/O
            async with aiofiles.open(stack_path, "a", encoding="utf-8") as f:
                await f.write(json.dumps(entry) + "\n")
            logger.info(f"Persisted stack trace to {stack_path}")
        except Exception as persist_error:
            logger.error(f"Failed to persist stack trace for {self.id}: {persist_error}")

        try:
            # Keep authoritative list synced by re-reading from disk
            self.stack_traces = read_jsonl(stack_path)
        except Exception as reload_error:
            logger.error(f"Failed to reload stack traces for {self.id}: {reload_error}")
            self.stack_traces.append(entry)

        # Push live updates to subscribers listening on /ws/logs
        payload = {"type": "exception", **entry}
        for subscriber in self.subscribers:
            await subscriber.put(payload)

    def load_log_from_disk(self) -> bool:
        """Load log entries from disk into memory."""
        self.logs = []
        log_path = get_case_log_path(self.run.id, tc_id=self.tc_id)
        if not log_path.exists():
            return False

        raw_logs = read_jsonl(log_path)
        for entry in raw_logs:
            log_entry = self._sanitize_log_entry(entry)
            if not log_entry:
                continue
            self.logs.append(log_entry)
        return True
