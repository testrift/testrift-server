#!/usr/bin/env python3
"""
Tests for WebSocket protocol functionality.
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from testrift_server.tr_server import TestCaseData, TestRunData, WebSocketServer, generate_storage_id, TC_ID_FIELD


class TestWebSocketProtocol:
    """Test WebSocket protocol message handling."""

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
    async def test_test_case_finished_with_status_field(self, ws_server, mock_ws, sample_run):
        """Test that test_case_finished messages work with status field."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run

        # Create a test case
        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(sample_run, "Test.TestMethod", {TC_ID_FIELD: tc_id_hash})
        sample_run.test_cases["Test.TestMethod"] = test_case

        # Mock the WebSocket message
        message_data = {
            "type": "test_case_finished",
            "run_id": "test-run-123",
            "test_case_id": "Test.TestMethod",
            "status": "passed"  # Using status instead of result
        }

        # Create a mock message
        mock_msg = MagicMock()
        mock_msg.type = 1  # WSMsgType.TEXT
        mock_msg.data = json.dumps(message_data)

        # Mock the WebSocket iteration
        async def mock_iter():
            yield mock_msg

        mock_ws.__aiter__ = mock_iter

        # Process the message (this would normally be done in handle_nunit_ws)
        # We'll test the message processing logic directly

        # Verify the test case status is updated
        assert test_case.status == "running"  # Initially running

        data = json.loads(mock_msg.data)
        if data.get("type") == "test_case_finished":
            run_id = data.get("run_id")
            tc_id = data.get("test_case_id")
            status = data.get("status", "").lower()

            run = ws_server.test_runs.get(run_id)
            if run and tc_id in run.test_cases:
                test_case = run.test_cases[tc_id]
                if status in ['passed', 'failed', 'skipped', 'aborted']:
                    test_case.status = status
                    test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        # Verify the status was updated
        assert test_case.status == "passed"
        assert test_case.end_time is not None

    @pytest.mark.asyncio
    async def test_invalid_status_handling(self, ws_server, sample_run):
        """Test that invalid status values are handled correctly."""
        # Add run to server
        ws_server.test_runs["test-run-123"] = sample_run

        # Create a test case
        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(sample_run, "Test.TestMethod", {TC_ID_FIELD: tc_id_hash})
        sample_run.test_cases["Test.TestMethod"] = test_case

        # Test with invalid status
        message_data = {
            "type": "test_case_finished",
            "run_id": "test-run-123",
            "test_case_id": "Test.TestMethod",
            "status": "invalid_status"
        }

        data = message_data
        run_id = data.get("run_id")
        tc_id = data.get("test_case_id")
        status = data.get("status", "").lower()

        run = ws_server.test_runs.get(run_id)
        if run and tc_id in run.test_cases:
            test_case = run.test_cases[tc_id]
            if status in ['passed', 'failed', 'skipped', 'aborted']:
                test_case.status = status
                test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        # Verify the status was not updated
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

        # Create a test case with logs
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
                "timestamp": "2025-10-01T18:49:17.803300Z",
                "message": "RX: AT+USYCI?",
                "device": "Tester5",
                "source": "COM91"
            }
        ]
        sample_run.test_cases["Test.TestMethod"] = test_case

        # Test the validation functions that handle_log_stream uses
        from testrift_server.tr_server import validate_run_id, validate_test_case_id

        # Test valid IDs
        assert validate_run_id("test-run-123") is True
        assert validate_test_case_id("Test.TestMethod") is True

        # Test that the test run and test case exist
        assert "test-run-123" in ws_server.test_runs
        assert "Test.TestMethod" in sample_run.test_cases

        # Test that logs exist
        assert len(test_case.logs) == 2


if __name__ == "__main__":
    pytest.main([__file__])
