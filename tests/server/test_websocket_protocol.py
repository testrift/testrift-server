#!/usr/bin/env python3
"""
Tests for WebSocket protocol functionality using optimized binary format.
"""

import asyncio
import msgpack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from testrift_server.models import TestCaseData, TestRunData
from testrift_server.websocket import WebSocketServer, normalize_message
from testrift_server.protocol import (
    MSG_RUN_STARTED,
    MSG_TEST_CASE_STARTED,
    MSG_LOG_BATCH,
    MSG_TEST_CASE_FINISHED,
    MSG_RUN_FINISHED,
    MSG_BATCH,
    STATUS_RUNNING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_ABORTED,
    STATUS_FINISHED,
    F_TYPE,
    F_RUN_ID,
    F_TC_FULL_NAME,
    F_TC_ID,
    F_STATUS,
    F_TIMESTAMP,
    F_EVENT_TYPE,
    F_EVENTS,
    F_ENTRIES,
    F_MESSAGE,
    F_COMPONENT,
    F_CHANNEL,
)
from testrift_server.utils import generate_storage_id, TC_ID_FIELD


class TestWebSocketProtocol:
    """Test WebSocket protocol message handling with optimized binary format."""

    @pytest.fixture
    def ws_server(self):
        """Create a WebSocket server instance."""
        return WebSocketServer()

    @pytest.fixture
    def mock_ws(self):
        """Create a mock WebSocket connection."""
        ws = AsyncMock()
        ws.closed = False
        ws.exception.return_value = None
        return ws

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

    @pytest.mark.asyncio
    async def test_normalize_message_converts_optimized_format(self):
        """Test that normalize_message converts optimized format to internal format."""
        string_table = {}

        # Optimized format message
        raw_data = {
            F_TYPE: MSG_TEST_CASE_STARTED,
            F_RUN_ID: "test-run-123",
            F_TC_FULL_NAME: "Test.TestMethod",
            F_TC_ID: "0-1009",
            F_STATUS: STATUS_RUNNING,
            F_TIMESTAMP: 1737820282736
        }

        normalized = normalize_message(raw_data, string_table)

        assert normalized["type"] == "test_case_started"
        assert normalized["run_id"] == "test-run-123"
        assert normalized["tc_full_name"] == "Test.TestMethod"
        assert normalized["tc_id"] == "0-1009"
        assert normalized["status"] == "running"
        assert "Z" in normalized["timestamp"]  # ISO format

    @pytest.mark.asyncio
    async def test_normalize_message_handles_log_batch_with_interning(self):
        """Test log batch normalization with string interning."""
        string_table = {}

        # Log batch with interned strings
        # Note: component and channel use separate ID spaces (1 for component, 2 for channel)
        raw_data = {
            F_TYPE: MSG_LOG_BATCH,
            F_RUN_ID: "test-run-123",
            F_TC_ID: "0-1009",
            F_ENTRIES: [
                {F_TIMESTAMP: 1737820282736, F_MESSAGE: "Hello", F_COMPONENT: [1, "Tester5"], F_CHANNEL: [2, "COM91"]},
                {F_TIMESTAMP: 1737820282737, F_MESSAGE: "World", F_COMPONENT: 1, F_CHANNEL: 2},  # Interned references
            ]
        }

        normalized = normalize_message(raw_data, string_table)

        assert normalized["type"] == "log_batch"
        assert len(normalized["entries"]) == 2

        # First entry registers strings
        assert normalized["entries"][0]["component"] == "Tester5"
        assert normalized["entries"][0]["channel"] == "COM91"

        # Second entry uses interned strings
        assert normalized["entries"][1]["component"] == "Tester5"
        assert normalized["entries"][1]["channel"] == "COM91"

    @pytest.mark.asyncio
    async def test_test_case_finished_with_status_code(self, ws_server, mock_ws, sample_run):
        """Test that test_case_finished messages work with numeric status codes."""
        ws_server.test_runs["test-run-123"] = sample_run

        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(sample_run, "Test.TestMethod", {TC_ID_FIELD: tc_id_hash})
        sample_run.test_cases["Test.TestMethod"] = test_case
        sample_run.test_cases_by_tc_id[test_case.tc_id] = test_case

        # Optimized format message
        message_data = {
            F_TYPE: MSG_TEST_CASE_FINISHED,
            F_RUN_ID: "test-run-123",
            F_TC_ID: test_case.tc_id,
            F_STATUS: STATUS_PASSED,
            F_TIMESTAMP: 1737820282736
        }

        # Normalize and process
        string_table = {}
        data = normalize_message(message_data, string_table)

        assert data["type"] == "test_case_finished"
        assert data["status"] == "passed"

        # Simulate handler logic
        status = data.get("status", "").lower()
        if status in ['passed', 'failed', 'skipped', 'aborted']:
            test_case.status = status
            test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        assert test_case.status == "passed"
        assert test_case.end_time is not None

    @pytest.mark.asyncio
    async def test_test_case_finished_invalid_status(self, ws_server, mock_ws, sample_run):
        """Test that invalid status values are rejected."""
        ws_server.test_runs["test-run-123"] = sample_run

        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(sample_run, "Test.TestMethod", {TC_ID_FIELD: tc_id_hash})
        sample_run.test_cases["Test.TestMethod"] = test_case
        sample_run.test_cases_by_tc_id[test_case.tc_id] = test_case

        # Test with invalid status code (999)
        message_data = {
            F_TYPE: MSG_TEST_CASE_FINISHED,
            F_RUN_ID: "test-run-123",
            F_TC_ID: test_case.tc_id,
            F_STATUS: 999,  # Invalid status code
            F_TIMESTAMP: 1737820282736
        }

        # Normalize message
        string_table = {}
        data = normalize_message(message_data, string_table)

        # Status code 999 should normalize to "unknown"
        status = data.get("status", "").lower()
        run = ws_server.test_runs.get(data["run_id"])
        tc_id = data.get("tc_id")

        if run and tc_id in run.test_cases_by_tc_id:
            tc = run.test_cases_by_tc_id[tc_id]
            if status in ['passed', 'failed', 'skipped', 'aborted']:
                tc.status = status
                tc.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        # Verify the status was not updated since "unknown" is not valid
        assert test_case.status == "running"  # Should remain running
        assert test_case.end_time is None

    @pytest.mark.asyncio
    async def test_test_case_counting_with_status(self, ws_server, sample_run):
        """Test that test case counting works with status field."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run

        # Create test cases with different statuses
        test_cases = [
            ("Test.Passed", "passed"),
            ("Test.Failed", "failed"),
            ("Test.Skipped", "skipped"),
            ("Test.Aborted", "aborted"),
            ("Test.Running", "running"),  # Should not be counted
        ]

        for tc_id, status in test_cases:
            tc_id_hash = generate_storage_id()
            test_case = TestCaseData(sample_run, tc_id, {TC_ID_FIELD: tc_id_hash})
            test_case.status = status
            if status != "running":
                test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
            sample_run.test_cases[tc_id] = test_case

        # Count test results
        passed_count = 0
        failed_count = 0
        skipped_count = 0
        aborted_count = 0

        for tc in sample_run.test_cases.values():
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

        # Verify counts
        assert passed_count == 1
        assert failed_count == 1
        assert skipped_count == 1
        assert aborted_count == 1

    @pytest.mark.asyncio
    async def test_log_stream_websocket_connection(self, ws_server, sample_run):
        """Test WebSocket log stream connection."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run

        # Create a test case with logs (using NUnit test ID format)
        tc_id = "0-1009"  # NUnit test ID format
        test_case = TestCaseData(sample_run, "Test.TestMethod", {TC_ID_FIELD: tc_id})
        test_case.logs = [
            {
                "timestamp": "2025-10-01T18:49:17.803300Z",
                "message": "TX: AT+USYCI?",
                "device": "Tester5",
                "source": "COM91"
            },
            {
                "timestamp": "2025-10-01T18:49:17.803300Z",
                "message": "RX: AT+USYCI?",
                "device": "Tester5",
                "source": "COM91"
            }
        ]
        sample_run.test_cases["Test.TestMethod"] = test_case
        # Ensure test case is in test_cases_by_tc_id for lookup by tc_id
        sample_run.test_cases_by_tc_id[test_case.tc_id] = test_case

        # Test the validation functions that handle_log_stream uses
        from testrift_server.utils import validate_run_id, validate_test_case_id

        # Test valid IDs (NUnit test ID format)
        assert validate_run_id("test-run-123") is True
        assert validate_test_case_id("0-1009") is True

        # Test that the test run and test case exist
        assert "test-run-123" in ws_server.test_runs
        assert "Test.TestMethod" in sample_run.test_cases
        assert "0-1009" in sample_run.test_cases_by_tc_id

        # Test that logs exist
        assert len(test_case.logs) == 2


if __name__ == "__main__":
    pytest.main([__file__])
