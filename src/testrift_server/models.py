"""
Data models for TestRift server.

TestRunData and TestCaseData classes for managing test run state.
"""

import asyncio
import logging
from datetime import datetime, UTC

import aiofiles
import msgpack

from .utils import (
    get_run_meta_path,
    get_case_log_path,
    get_case_stack_path,
    get_merged_log_path,
    read_mplog,
    read_meta_msgpack,
    write_mplog_entries_async,
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
        # String table for interned component/channel strings (id -> string)
        self.string_table: dict[int, str] = {}

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
        # Include string table for interned component/channel strings
        if self.string_table:
            # Convert int keys to strings for JSON compatibility
            result["string_table"] = {str(k): v for k, v in self.string_table.items()}
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
        # Load string table for interned component/channel strings
        string_table_raw = meta.get("string_table", {})
        run.string_table = {int(k): v for k, v in string_table_raw.items()}
        return run

    @staticmethod
    def load_from_disk(run_id):
        """Load a test run from disk by its run_id."""
        meta = read_meta_msgpack(run_id)
        if meta is None:
            return None
        return TestRunData.from_dict(run_id, meta)


class TestCaseData:
    """Represents a test case within a test run."""
    __test__ = False  # Tell pytest to ignore this class

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

        # Offset and count for merged log file (set after run finishes)
        self.log_offset = meta.get("log_offset")
        self.log_count = meta.get("log_count", 0)
        self.stack_count = meta.get("stack_count", 0)

        # tc_id MUST be in meta - it should have been generated once when test case started
        # and stored in meta. If it's missing, that's a bug.
        if TC_ID_FIELD not in meta:
            raise ValueError(f"tc_id missing in meta for test case {tc_full_name}. tc_id must be generated once and stored.")
        self.tc_id = meta[TC_ID_FIELD]

        # Load stack traces from individual file if run is still in progress
        # After run finishes, data is in merged file and accessed via offset
        if self.log_offset is None:
            stack_path = get_case_stack_path(self.run.id, tc_id=self.tc_id)
            if stack_path.exists():
                try:
                    file_traces = read_mplog(stack_path)
                    if file_traces:
                        self.stack_traces = file_traces
                except Exception as e:
                    logger.error(f"Failed to load stack traces for {self.id}: {e}")

    def to_dict(self):
        """Serialize the test case to a dictionary."""
        result = {
            TC_ID_FIELD: self.tc_id,
            TC_FULL_NAME_FIELD: self.id,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            # Note: logs and stack_traces are stored in merged file, not in meta
            # Note: subscribers are not serialized
        }
        # Include offset info if available (after run finishes)
        if self.log_offset is not None:
            result["log_offset"] = self.log_offset
            result["log_count"] = self.log_count
            result["stack_count"] = self.stack_count
        return result

    @classmethod
    def from_dict(cls, run, tc_full_name, meta):
        """Create a TestCaseData instance from a dictionary."""
        return cls(run, tc_full_name, meta)

    async def add_log_entries(self, raw_entries):
        """Add log entries to this test case using async file I/O.

        raw_entries contains compact protocol entries as received (short keys, ms timestamps).
        These are stored as-is and sent to UI which decodes them.
        """
        if not raw_entries:
            return

        log_path = get_case_log_path(self.run.id, tc_id=self.tc_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Validate entries have required fields (compact keys: 'ts' for timestamp)
        valid_entries = []
        skipped_count = 0
        for entry in raw_entries:
            if not isinstance(entry, dict) or 'ts' not in entry:
                skipped_count += 1
                continue
            valid_entries.append(entry)

        if skipped_count > 0:
            logger.warning(f"Skipped {skipped_count} log entries for {self.tc_id} (missing timestamp)")

        if not valid_entries:
            return

        await write_mplog_entries_async(log_path, valid_entries)

        # Update in-memory logs (keep compact format) and notify subscribers
        self.logs.extend(valid_entries)

        # Batch notify subscribers (send raw compact entries)
        if self.subscribers:
            for entry in valid_entries:
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
            # Append to disk file using async I/O with MessagePack
            await write_mplog_entries_async(stack_path, [entry])
            logger.info(f"Persisted stack trace to {stack_path}")
        except Exception as persist_error:
            logger.error(f"Failed to persist stack trace for {self.id}: {persist_error}")

        try:
            # Keep authoritative list synced by re-reading from disk
            self.stack_traces = read_mplog(stack_path)
        except Exception as reload_error:
            logger.error(f"Failed to reload stack traces for {self.id}: {reload_error}")
            self.stack_traces.append(entry)

        # Push live updates to subscribers listening on /ws/logs
        payload = {"type": "exception", **entry}
        for subscriber in self.subscribers:
            await subscriber.put(payload)

    def load_log_from_disk(self) -> bool:
        """Load log entries from disk into memory.

        For finished runs, reads from merged log file using offset.
        For running test cases, reads from individual log file.
        Entries are kept in compact protocol format (short keys, ms timestamps).
        """
        self.logs = []
        self.stack_traces = []

        # Check if we should read from merged file (run finished)
        if self.log_offset is not None:
            merged_path = get_merged_log_path(self.run.id)
            if merged_path.exists():
                return self._load_from_merged_file(merged_path)

        # Otherwise read from individual log file (run in progress)
        log_path = get_case_log_path(self.run.id, tc_id=self.tc_id)
        if not log_path.exists():
            return False

        # Keep entries in compact format - UI will decode them
        raw_logs = read_mplog(log_path)
        self.logs.extend(raw_logs)
        return True

    def _load_from_merged_file(self, merged_path) -> bool:
        """Load logs and stack traces from merged .mplog file using stored offsets.

        Entries are kept in compact protocol format.
        """
        import struct

        try:
            with open(merged_path, "rb") as f:
                f.seek(self.log_offset)

                # Read log entries (keep compact format)
                for _ in range(self.log_count):
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        break
                    length = struct.unpack(">I", length_bytes)[0]
                    data = f.read(length)
                    if len(data) < length:
                        break
                    entry = msgpack.unpackb(data, raw=False)
                    self.logs.append(entry)

                # Read stack trace entries
                for _ in range(self.stack_count):
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        break
                    length = struct.unpack(">I", length_bytes)[0]
                    data = f.read(length)
                    if len(data) < length:
                        break
                    entry = msgpack.unpackb(data, raw=False)
                    self.stack_traces.append(entry)

            return True
        except Exception as e:
            logger.error(f"Failed to load from merged file for {self.id}: {e}")
            return False
