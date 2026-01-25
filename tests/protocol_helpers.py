"""
Test helpers for sending optimized protocol messages.

This module provides helper functions and a WebSocket wrapper for tests
to easily send messages in the optimized binary protocol format.
"""

import msgpack
import time
from datetime import datetime, UTC
from typing import Any

from testrift_server.protocol import (
    MSG_RUN_STARTED,
    MSG_TEST_CASE_STARTED,
    MSG_LOG_BATCH,
    MSG_EXCEPTION,
    MSG_TEST_CASE_FINISHED,
    MSG_RUN_FINISHED,
    MSG_RUN_STARTED_RESPONSE,
    STATUS_RUNNING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_ABORTED,
    STATUS_FINISHED,
    STATUS_FROM_NAME,
    DIR_TX,
    DIR_RX,
    DIR_FROM_NAME,
    PHASE_TEARDOWN,
    F_TYPE,
    F_RUN_ID,
    F_RUN_NAME,
    F_STATUS,
    F_TIMESTAMP,
    F_TC_FULL_NAME,
    F_TC_ID,
    F_TC_META,
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
    F_GROUP,
    F_GROUP_NAME,
    F_GROUP_METADATA,
    F_GROUP_HASH,
    F_RETENTION_DAYS,
    F_LOCAL_RUN,
    F_RUN_URL,
    F_GROUP_URL,
    MSG_TYPE_NAMES,
)


def current_timestamp_ms() -> int:
    """Get current timestamp as milliseconds since epoch."""
    return int(time.time() * 1000)


def iso_to_ms(iso_str: str) -> int:
    """Convert ISO timestamp string to milliseconds."""
    # Remove 'Z' suffix if present
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1]
    dt = datetime.fromisoformat(iso_str.replace('+00:00', ''))
    return int(dt.timestamp() * 1000)


class ProtocolClient:
    """
    Wrapper around aiohttp WebSocket for sending optimized protocol messages.
    
    Tracks string interning state per connection.
    """
    
    def __init__(self, ws):
        self.ws = ws
        self._next_string_id = 1
        self._component_ids: dict[str, int] = {}
        self._channel_ids: dict[str, int] = {}
    
    def _intern_component(self, name: str) -> int | list:
        """Get interned component representation."""
        if name in self._component_ids:
            return self._component_ids[name]
        str_id = self._next_string_id
        self._next_string_id += 1
        self._component_ids[name] = str_id
        return [str_id, name]
    
    def _intern_channel(self, name: str) -> int | list:
        """Get interned channel representation."""
        if name in self._channel_ids:
            return self._channel_ids[name]
        str_id = self._next_string_id
        self._next_string_id += 1
        self._channel_ids[name] = str_id
        return [str_id, name]
    
    async def send(self, msg: dict):
        """Send a MessagePack-encoded message."""
        data = msgpack.packb(msg)
        await self.ws.send_bytes(data)
    
    async def receive_response(self) -> dict:
        """Receive and decode a MessagePack response."""
        msg = await self.ws.receive()
        if msg.type.name == 'BINARY':
            data = msgpack.unpackb(msg.data)
            # Normalize response for easier assertion
            return self._normalize_response(data)
        elif msg.type.name == 'TEXT':
            # Fallback for text responses (shouldn't happen with new protocol)
            import json
            return json.loads(msg.data)
        else:
            raise ValueError(f"Unexpected message type: {msg.type}")
    
    def _normalize_response(self, data: dict) -> dict:
        """Normalize response to use readable field names."""
        result = {}
        if F_TYPE in data:
            result["type"] = MSG_TYPE_NAMES.get(data[F_TYPE], f"unknown_{data[F_TYPE]}")
        if F_RUN_ID in data:
            result["run_id"] = data[F_RUN_ID]
        if F_RUN_URL in data:
            result["run_url"] = data[F_RUN_URL]
        if F_GROUP_URL in data:
            result["group_url"] = data[F_GROUP_URL]
        # Pass through any other fields
        for k, v in data.items():
            if k not in (F_TYPE, F_RUN_ID, F_RUN_URL, F_GROUP_URL):
                result[k] = v
        return result
    
    async def send_run_started(
        self,
        user_metadata: dict | None = None,
        retention_days: int = 1,
        local_run: bool = False,
        group: dict | None = None,
    ) -> dict:
        """Send run_started message and return the response with run_id."""
        msg = {
            F_TYPE: MSG_RUN_STARTED,
            F_USER_METADATA: user_metadata or {},
            F_RETENTION_DAYS: retention_days,
            F_LOCAL_RUN: local_run,
        }
        if group:
            group_msg = {}
            if "hash" in group:
                group_msg[F_GROUP_HASH] = group["hash"]
            if "name" in group:
                group_msg[F_GROUP_NAME] = group["name"]
            if "metadata" in group:
                group_msg[F_GROUP_METADATA] = group["metadata"]
            msg[F_GROUP] = group_msg
        
        await self.send(msg)
        response = await self.receive_response()
        return response
    
    async def send_test_case_started(
        self,
        run_id: str,
        tc_full_name: str,
        tc_id: str,
        tc_meta: dict | None = None,
    ):
        """Send test_case_started message."""
        msg = {
            F_TYPE: MSG_TEST_CASE_STARTED,
            F_RUN_ID: run_id,
            F_TC_FULL_NAME: tc_full_name,
            F_TC_ID: tc_id,
            F_STATUS: STATUS_RUNNING,
            F_TIMESTAMP: current_timestamp_ms(),
        }
        if tc_meta:
            msg[F_TC_META] = tc_meta
        await self.send(msg)
    
    async def send_log_batch(
        self,
        run_id: str,
        tc_id: str,
        entries: list[dict],
    ):
        """
        Send log_batch message with entries.
        
        Each entry can have:
        - message: str (required)
        - timestamp: int (ms) or str (ISO) - defaults to now
        - component: str - will be interned
        - channel: str - will be interned
        - dir: str ("tx" or "rx") - will be converted to int code
        - phase: str ("teardown") - will be converted to int code
        """
        converted_entries = []
        for entry in entries:
            e = {F_MESSAGE: entry["message"]}
            
            # Timestamp
            if "timestamp" in entry:
                ts = entry["timestamp"]
                if isinstance(ts, str):
                    e[F_TIMESTAMP] = iso_to_ms(ts)
                else:
                    e[F_TIMESTAMP] = ts
            else:
                e[F_TIMESTAMP] = current_timestamp_ms()
            
            # Component (interned)
            if "component" in entry:
                e[F_COMPONENT] = self._intern_component(entry["component"])
            elif "device" in entry:  # Backward compat field name
                e[F_COMPONENT] = self._intern_component(entry["device"])
            
            # Channel (interned)
            if "channel" in entry:
                e[F_CHANNEL] = self._intern_channel(entry["channel"])
            elif "source" in entry:  # Backward compat field name
                e[F_CHANNEL] = self._intern_channel(entry["source"])
            
            # Direction
            if "dir" in entry:
                e[F_DIR] = DIR_FROM_NAME.get(entry["dir"], entry["dir"])
            
            # Phase
            if "phase" in entry:
                if entry["phase"] == "teardown":
                    e[F_PHASE] = PHASE_TEARDOWN
            
            converted_entries.append(e)
        
        msg = {
            F_TYPE: MSG_LOG_BATCH,
            F_RUN_ID: run_id,
            F_TC_ID: tc_id,
            F_ENTRIES: converted_entries,
        }
        await self.send(msg)
    
    async def send_exception(
        self,
        run_id: str,
        tc_id: str,
        message: str,
        exception_type: str = "",
        stack_trace: list[str] | None = None,
        is_error: bool = False,
        timestamp: int | str | None = None,
    ):
        """Send exception message."""
        msg = {
            F_TYPE: MSG_EXCEPTION,
            F_RUN_ID: run_id,
            F_TC_ID: tc_id,
            F_MESSAGE: message,
            F_EXCEPTION_TYPE: exception_type,
            F_STACK_TRACE: stack_trace or [],
            F_IS_ERROR: is_error,
        }
        if timestamp:
            if isinstance(timestamp, str):
                msg[F_TIMESTAMP] = iso_to_ms(timestamp)
            else:
                msg[F_TIMESTAMP] = timestamp
        else:
            msg[F_TIMESTAMP] = current_timestamp_ms()
        await self.send(msg)
    
    async def send_test_case_finished(
        self,
        run_id: str,
        tc_id: str,
        status: str | int,
    ):
        """Send test_case_finished message."""
        if isinstance(status, str):
            status_code = STATUS_FROM_NAME.get(status, STATUS_PASSED)
        else:
            status_code = status
        
        msg = {
            F_TYPE: MSG_TEST_CASE_FINISHED,
            F_RUN_ID: run_id,
            F_TC_ID: tc_id,
            F_STATUS: status_code,
            F_TIMESTAMP: current_timestamp_ms(),
        }
        await self.send(msg)
    
    async def send_run_finished(
        self,
        run_id: str,
        status: str | int = "finished",
    ):
        """Send run_finished message."""
        if isinstance(status, str):
            if status == "finished":
                status_code = STATUS_FINISHED
            else:
                status_code = STATUS_FROM_NAME.get(status, STATUS_FINISHED)
        else:
            status_code = status
        
        msg = {
            F_TYPE: MSG_RUN_FINISHED,
            F_RUN_ID: run_id,
            F_STATUS: status_code,
            F_TIMESTAMP: current_timestamp_ms(),
        }
        await self.send(msg)
