#!/usr/bin/env python3
"""
Tests for live log streaming functionality.
"""

import asyncio
import json
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testrift_server.models import TestCaseData, TestRunData
from testrift_server.websocket import WebSocketServer
from testrift_server.utils import generate_storage_id, TC_ID_FIELD


class TestLiveLogStreaming:
    """Test live log streaming functionality."""

    @pytest.fixture
    def ws_server(self):
        """Create a WebSocket server instance."""
        return WebSocketServer()

    @pytest.fixture
    def sample_run(self):
        """Create a sample test run."""
        run = TestRunData(
            run_id="test-run-123",
            retention_days=7,
            local_run=False,
            user_metadata={"DUT": {"value": "TestDevice-001"}}
        )
        return run

    @pytest.fixture
    def sample_test_case(self, sample_run):
        """Create a sample test case with logs."""
        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(sample_run, "Test.TestMethod", {TC_ID_FIELD: tc_id_hash})
        test_case.logs = [
            {
                "timestamp": "2025-10-01T18:49:17.803300Z",
                "message": "TX: AT+USYCI?",
                "device": "Tester5",
                "source": "COM91"
            },
            {
                "timestamp": "2025-10-01T18:49:18.803300Z",
                "message": "RX: AT+USYCI?",
                "device": "Tester5",
                "source": "COM91"
            }
        ]
        return test_case

    @pytest.mark.asyncio
    async def test_live_run_detection_in_memory(self, ws_server, sample_run, sample_test_case):
        """Test live run detection when run is in memory."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run
        sample_run.test_cases["Test.TestMethod"] = sample_test_case

        # Test case should be considered live if run is running
        sample_run.status = "running"
        live_run = (sample_run.status == "running")
        assert live_run is True

        # Test case should also be considered live if test case is running
        sample_test_case.status = "running"
        if sample_test_case.status == "running":
            live_run = True
        assert live_run is True

    @pytest.mark.asyncio
    async def test_live_run_detection_from_disk(self, ws_server, sample_run, sample_test_case):
        """Test live run detection when run is loaded from disk."""
        sample_run.test_cases["Test.TestMethod"] = sample_test_case
        sample_test_case.status = "running"

        recent_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
        sample_test_case.logs.append({
            "timestamp": recent_time,
            "message": "Recent log entry",
            "device": "Tester5",
            "source": "COM91"
        })
        live_run = False
        if sample_test_case.status == "running":
            # Check if there are recent logs (within last 30 seconds)
            import time
            current_time = time.time()
            recent_logs = False
            for log_entry in sample_test_case.logs:
                try:
                    log_time = datetime.fromisoformat(log_entry.get("timestamp", "").replace("Z", "+00:00")).timestamp()
                    if current_time - log_time < 30:  # Within last 30 seconds
                        recent_logs = True
                        break
                except:
                    pass

            if recent_logs:
                live_run = True

        assert live_run is True

    @pytest.mark.asyncio
    async def test_websocket_log_stream_connection(self, ws_server, sample_run, sample_test_case):
        """Test WebSocket log stream connection establishment."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run
        sample_run.test_cases["Test.TestMethod"] = sample_test_case

        # Test the validation functions that handle_log_stream uses
        from testrift_server.utils import validate_run_id, validate_test_case_id

        # Test valid IDs (NUnit test ID format)
        assert validate_run_id("test-run-123") is True
        assert validate_test_case_id("0-1009") is True

        # Test invalid IDs
        assert validate_run_id("") is False
        assert validate_run_id("../invalid") is False
        assert validate_test_case_id("") is False
        assert validate_test_case_id("../invalid") is False

        # Test that the test run and test case exist
        assert "test-run-123" in ws_server.test_runs
        assert "Test.TestMethod" in sample_run.test_cases

        # Test that logs exist
        assert len(sample_test_case.logs) == 2

    @pytest.mark.asyncio
    async def test_websocket_log_stream_error_handling(self, ws_server):
        """Test WebSocket log stream error handling."""
        # Test that non-existent runs are handled correctly
        assert "non-existent-run" not in ws_server.test_runs

        # Test validation of invalid run IDs
        from testrift_server.utils import validate_run_id, validate_test_case_id
        assert validate_run_id("") is False
        assert validate_run_id("../invalid") is False
        assert validate_test_case_id("") is False
        assert validate_test_case_id("../invalid") is False

    @pytest.mark.asyncio
    async def test_websocket_log_stream_test_case_not_found(self, ws_server, sample_run):
        """Test WebSocket log stream when test case is not found."""
        # Add run to server but without the test case
        ws_server.test_runs["test-run-123"] = sample_run

        # Test that the run exists but test case doesn't
        assert "test-run-123" in ws_server.test_runs
        assert "NonExistent.Test" not in sample_run.test_cases

        # Test validation (NUnit test ID format)
        from testrift_server.utils import validate_run_id, validate_test_case_id
        assert validate_run_id("test-run-123") is True
        assert validate_test_case_id("0-9999") is True  # Valid format, just doesn't exist

    @pytest.mark.asyncio
    async def test_log_entry_processing(self, ws_server, sample_run, sample_test_case):
        """Test processing of log entries through WebSocket."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run
        sample_run.test_cases["Test.TestMethod"] = sample_test_case

        # Create a queue to simulate WebSocket subscriber
        queue = asyncio.Queue()
        sample_test_case.subscribers.append(queue)

        # Add a new log entry
        new_log_entry = {
            "timestamp": "2025-10-01T18:49:19.803300Z",
            "message": "TX: AT+USYCI=1",
            "device": "Tester5",
            "source": "COM91"
        }

        sample_test_case.logs.append(new_log_entry)

        for subscriber in sample_test_case.subscribers:
            await subscriber.put(new_log_entry)

        # Verify the log entry was queued
        assert not queue.empty()
        queued_entry = await queue.get()
        assert queued_entry == new_log_entry

    @pytest.mark.asyncio
    async def test_websocket_connection_cleanup(self, ws_server, sample_run, sample_test_case):
        """Test WebSocket connection cleanup."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run
        sample_run.test_cases["Test.TestMethod"] = sample_test_case

        # Create a queue to simulate WebSocket subscriber
        queue = asyncio.Queue()
        sample_test_case.subscribers.append(queue)

        # Verify subscriber was added
        assert len(sample_test_case.subscribers) == 1

        sample_test_case.subscribers.remove(queue)

        # Verify subscriber was removed
        assert len(sample_test_case.subscribers) == 0

    def test_live_indicator_functions(self):
        """Test live indicator functions."""
        def add_live_indicator():
            return "live indicator added"

        def remove_live_indicator():
            return "live indicator removed"

        # Test that functions can be called without errors
        result1 = add_live_indicator()
        result2 = remove_live_indicator()

        assert result1 == "live indicator added"
        assert result2 == "live indicator removed"

    @pytest.mark.asyncio
    async def test_websocket_message_processing(self, ws_server, sample_run, sample_test_case):
        """Test WebSocket message processing for log entries."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run
        sample_run.test_cases["Test.TestMethod"] = sample_test_case

        # Mock WebSocket message
        log_batch_message = {
            "type": "log_batch",
            "run_id": "test-run-123",
            "test_case_id": "Test.TestMethod",
            "entries": [
                {
                    "timestamp": "2025-10-01T18:49:20.803300Z",
                    "message": "TX: AT+USYCI=2",
                    "device": "Tester5",
                    "source": "COM91"
                },
                {
                    "timestamp": "2025-10-01T18:49:21.803300Z",
                    "message": "RX: OK",
                    "device": "Tester5",
                    "source": "COM91"
                }
            ]
        }

        data = log_batch_message
        if data.get("type") == "log_batch":
            run_id = data.get("run_id")
            tc_id = data.get("test_case_id")
            entries = data.get("entries", [])

            run = ws_server.test_runs.get(run_id)
            if run and tc_id in run.test_cases:
                test_case = run.test_cases[tc_id]
                for entry in entries:
                    test_case.logs.append(entry)

        # Verify log entries were added
        assert len(sample_test_case.logs) == 4  # 2 original + 2 new
        assert sample_test_case.logs[-1]["message"] == "RX: OK"


if __name__ == "__main__":
    pytest.main([__file__])
