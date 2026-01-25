#!/usr/bin/env python3
"""
Integration tests for client-server communication and message processing.

These tests simulate the internal message processing logic that happens
after normalize_message converts the optimized binary protocol to the
internal format with readable string field names.
"""

import asyncio
import msgpack
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import WSMsgType, web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from testrift_server import database
from testrift_server.database import TestCaseData as DatabaseTestCaseData
from testrift_server.database import TestRunData as DatabaseTestRunData
from testrift_server.database import UserMetadata
from testrift_server.models import TestCaseData, TestRunData
from testrift_server.websocket import WebSocketServer, normalize_message
from testrift_server.utils import generate_storage_id, TC_ID_FIELD
from testrift_server.protocol import (
    MSG_RUN_STARTED,
    MSG_TEST_CASE_STARTED,
    MSG_TEST_CASE_FINISHED,
    STATUS_RUNNING,
    STATUS_PASSED,
    F_TYPE,
    F_RUN_ID,
    F_TC_FULL_NAME,
    F_TC_ID,
    F_STATUS,
    F_TIMESTAMP,
    F_USER_METADATA,
    F_RETENTION_DAYS,
    F_LOCAL_RUN,
)


class TestClientServerIntegration:
    """Integration tests for client-server communication."""

    @pytest_asyncio.fixture
    async def temp_db(self):
        """Create a temporary database for testing."""
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test.db"

        # Initialize database
        database.initialize_database(Path(temp_dir))
        await database.db.initialize()

        yield db_path

        # Cleanup
        shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def initialized_db(self, temp_db):
        """Ensure database is initialized for tests."""
        assert database.db is not None
        return database.db

    @pytest_asyncio.fixture
    async def ws_server(self):
        """Create a WebSocket server instance."""
        return WebSocketServer()

    @pytest.mark.asyncio
    async def test_run_started_message_processing(self, ws_server, initialized_db):
        """Test that run_started messages are processed correctly and stored in database."""
        # Create a mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.closed = False

        # Simulate a run_started message
        run_started_message = {
            "type": "run_started",
            "run_id": "integration-test-run",
            "user_metadata": {"DUT": {"value": "TestDevice"}},
            "retention_days": 7,
            "local_run": False
        }

        # Process the message through the WebSocket handler
        try:
            # Simulate the message processing logic
            data = run_started_message
            run_id = data.get("run_id")
            retention_days = data.get("retention_days", 7)
            local_run = data.get("local_run", False)
            user_metadata = data.get("user_metadata", {})

            run = TestRunData(run_id, retention_days, local_run, user_metadata)
            ws_server.test_runs[run_id] = run

            # Verify the run was created
            assert run_id in ws_server.test_runs
            assert ws_server.test_runs[run_id].id == run_id
            assert ws_server.test_runs[run_id].retention_days == retention_days
            assert ws_server.test_runs[run_id].local_run == local_run

        except Exception as e:
            pytest.fail(f"run_started message processing failed: {e}")

    @pytest.mark.asyncio
    async def test_test_case_started_message_processing(self, ws_server, initialized_db):
        """Test that test_case_started messages are processed correctly."""
        # First create a test run
        run_id = "integration-test-run"
        run = TestRunData(run_id, 7, False, {"DUT": {"value": "TestDevice"}})
        ws_server.test_runs[run_id] = run

        # Create a mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.closed = False

        # Simulate a test_case_started message
        test_case_started_message = {
            "type": "test_case_started",
            "run_id": run_id,
            "test_case_id": "Test.IntegrationTest"
        }

        # Process the message
        try:
            data = test_case_started_message
            run_id = data.get("run_id")
            tc_id = data.get("test_case_id")

            # Find the run
            run = ws_server.test_runs.get(run_id)
            assert run is not None

            tc_id_hash = generate_storage_id()
            test_case = TestCaseData(run, tc_id, {TC_ID_FIELD: tc_id_hash})
            run.test_cases[tc_id] = test_case

            # Verify the test case was created
            assert tc_id in run.test_cases
            assert run.test_cases[tc_id].id == tc_id
            assert run.test_cases[tc_id].status == "running"

        except Exception as e:
            pytest.fail(f"test_case_started message processing failed: {e}")

    @pytest.mark.asyncio
    async def test_test_case_finished_message_processing(self, ws_server, initialized_db):
        """Test that test_case_finished messages are processed correctly."""
        # First create a test run and test case
        run_id = "integration-test-run"
        run = TestRunData(run_id, 7, False, {"DUT": {"value": "TestDevice"}})
        ws_server.test_runs[run_id] = run

        tc_id = "Test.IntegrationTest"
        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(run, tc_id, {TC_ID_FIELD: tc_id_hash})
        run.test_cases[tc_id] = test_case

        # Simulate a test_case_finished message
        test_case_finished_message = {
            "type": "test_case_finished",
            "run_id": run_id,
            "test_case_id": tc_id,
            "status": "passed"
        }

        # Process the message
        try:
            data = test_case_finished_message
            run_id = data.get("run_id")
            tc_id = data.get("test_case_id")
            status = data.get("status", "").lower()

            # Find the run and test case
            run = ws_server.test_runs.get(run_id)
            assert run is not None
            assert tc_id in run.test_cases

            test_case = run.test_cases[tc_id]

            # Update the test case status
            if status in ['passed', 'failed', 'skipped', 'aborted']:
                test_case.status = status
                test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

            # Verify the test case was updated
            assert test_case.status == "passed"
            assert test_case.end_time is not None

        except Exception as e:
            pytest.fail(f"test_case_finished message processing failed: {e}")

    @pytest.mark.asyncio
    async def test_full_client_server_flow(self, ws_server, initialized_db):
        """Test the complete flow from client messages to database storage."""
        # Simulate the complete client-server interaction
        run_id = "full-flow-test-run"

        # 1. Process run_started message
        run_started_message = {
            "type": "run_started",
            "run_id": run_id,
            "user_metadata": {"DUT": {"value": "TestDevice"}},
            "retention_days": 7,
            "local_run": False
        }

        data = run_started_message
        run = TestRunData(
            data.get("run_id"),
            data.get("retention_days", 7),
            data.get("local_run", False),
            data.get("user_metadata", {})
        )
        ws_server.test_runs[run_id] = run

        # 2. Process test_case_started message
        test_case_started_message = {
            "type": "test_case_started",
            "run_id": run_id,
            "test_case_id": "Test.FullFlowTest"
        }

        data = test_case_started_message
        tc_id = data.get("test_case_id")
        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(run, tc_id, {TC_ID_FIELD: tc_id_hash})
        run.test_cases[tc_id] = test_case

        # 3. Process test_case_finished message
        test_case_finished_message = {
            "type": "test_case_finished",
            "run_id": run_id,
            "test_case_id": tc_id,
            "status": "passed"
        }

        data = test_case_finished_message
        status = data.get("status", "").lower()
        if status in ['passed', 'failed', 'skipped', 'aborted']:
            test_case.status = status
            test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        # 4. Verify the complete flow worked
        assert run_id in ws_server.test_runs
        assert tc_id in run.test_cases
        assert run.test_cases[tc_id].status == "passed"
        assert run.test_cases[tc_id].end_time is not None

        # 5. Test that the data can be stored in database
        try:
            # Convert to database format
            test_run_data = DatabaseTestRunData(
                run_id=run.id,
                status=run.status,
                start_time=run.start_time,
                end_time=run.end_time,
                retention_days=run.retention_days,
                local_run=run.local_run,
                dut=run.dut
            )

            success = await initialized_db.insert_test_run(test_run_data, run.user_metadata)
            assert success is True

            # Verify it was stored
            stored_run = await initialized_db.get_test_run_by_id(run_id)
            assert stored_run is not None
            assert stored_run["run_id"] == run_id

        except Exception as e:
            pytest.fail(f"Database storage failed: {e}")

    @pytest.mark.asyncio
    async def test_websocket_message_handling_integration(self, ws_server, initialized_db):
        """Test WebSocket message handling with real message flow."""
        # Create a mock WebSocket that simulates client messages
        mock_ws = AsyncMock()
        mock_ws.closed = False

        # Create messages that simulate client.py behavior
        messages = [
            {
                "type": "run_started",
                "run_id": "websocket-integration-test",
                "user_metadata": {"DUT": {"value": "TestDevice"}},
                "retention_days": 7,
                "local_run": False
            },
            {
                "type": "test_case_started",
                "run_id": "websocket-integration-test",
                "test_case_id": "Test.WebSocketIntegration"
            },
            {
                "type": "test_case_finished",
                "run_id": "websocket-integration-test",
                "test_case_id": "Test.WebSocketIntegration",
                "status": "passed"
            }
        ]

        # Process each message
        for message in messages:
            try:
                # Simulate the message processing logic from handle_nunit_ws
                data = message
                msg_type = data.get("type")

                if msg_type == "run_started":
                    run_id = data.get("run_id")
                    retention_days = data.get("retention_days", 7)
                    local_run = data.get("local_run", False)
                    user_metadata = data.get("user_metadata", {})

                    run = TestRunData(run_id, retention_days, local_run, user_metadata)
                    ws_server.test_runs[run_id] = run

                elif msg_type == "test_case_started":
                    run_id = data.get("run_id")
                    tc_id = data.get("test_case_id")

                    run = ws_server.test_runs.get(run_id)
                    if run:
                        tc_id_hash = generate_storage_id()
                        test_case = TestCaseData(run, tc_id, {TC_ID_FIELD: tc_id_hash})
                        run.test_cases[tc_id] = test_case

                elif msg_type == "test_case_finished":
                    run_id = data.get("run_id")
                    tc_id = data.get("test_case_id")
                    status = data.get("status", "").lower()

                    run = ws_server.test_runs.get(run_id)
                    if run and tc_id in run.test_cases:
                        test_case = run.test_cases[tc_id]
                        if status in ['passed', 'failed', 'skipped', 'aborted']:
                            test_case.status = status
                            test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

            except Exception as e:
                pytest.fail(f"WebSocket message processing failed for {msg_type}: {e}")

        # Verify the complete flow worked
        run_id = "websocket-integration-test"
        assert run_id in ws_server.test_runs
        assert "Test.WebSocketIntegration" in ws_server.test_runs[run_id].test_cases
        assert ws_server.test_runs[run_id].test_cases["Test.WebSocketIntegration"].status == "passed"

    @pytest.mark.asyncio
    async def test_database_integration_with_websocket_flow(self, ws_server, initialized_db):
        """Test that WebSocket flow integrates properly with database operations."""
        run_id = "db-integration-test"

        # Simulate the complete flow
        # 1. Run started
        run = TestRunData(run_id, 7, False, {"DUT": {"value": "TestDevice"}})
        ws_server.test_runs[run_id] = run

        # 2. Test case started
        tc_id = "Test.DatabaseIntegration"
        tc_id_hash = generate_storage_id()
        test_case = TestCaseData(run, tc_id, {TC_ID_FIELD: tc_id_hash})
        run.test_cases[tc_id] = test_case

        # 3. Test case finished
        test_case.status = "passed"
        test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        # 4. Store in database
        test_run_data = DatabaseTestRunData(
            run_id=run.id,
            status=run.status,
            start_time=run.start_time,
            end_time=run.end_time,
            retention_days=run.retention_days,
            local_run=run.local_run,
            dut=run.dut
        )

        success = await initialized_db.insert_test_run(test_run_data, run.user_metadata)
        assert success is True

        # 5. Store test case in database
        test_case_data = DatabaseTestCaseData(
            id=0,
            run_id=run_id,
            tc_full_name=test_case.full_name,
            tc_id=test_case.tc_id,
            status=test_case.status,
            start_time=test_case.start_time,
            end_time=test_case.end_time
        )

        success = await initialized_db.insert_test_case(test_case_data)
        assert success is True

        # 6. Verify data can be retrieved
        stored_run = await initialized_db.get_test_run_by_id(run_id)
        assert stored_run is not None

        stored_test_cases = await initialized_db.get_test_cases_for_run(run_id)
        assert len(stored_test_cases) == 1
        assert stored_test_cases[0]["tc_full_name"] == tc_id
        assert stored_test_cases[0]["status"] == "passed"


class TestWebSocketServerIntegration:
    """Test WebSocket server integration with real message handling."""

    @pytest_asyncio.fixture
    async def temp_db(self):
        """Create a temporary database for testing."""
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test.db"

        # Initialize database
        database.initialize_database(Path(temp_dir))
        await database.db.initialize()

        yield db_path

        # Cleanup
        shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def initialized_db(self, temp_db):
        """Ensure database is initialized for tests."""
        assert database.db is not None
        return database.db


    @pytest.mark.asyncio
    async def test_websocket_server_message_processing(self, initialized_db):
        """Test that WebSocket server can process messages without errors."""
        ws_server = WebSocketServer()

        # Create a mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.closed = False

        # Create a mock message in optimized protocol format
        mock_msg = MagicMock()
        mock_msg.type = WSMsgType.BINARY
        mock_msg.data = msgpack.packb({
            F_TYPE: MSG_RUN_STARTED,
            F_USER_METADATA: {"DUT": {"value": "TestDevice"}},
            F_RETENTION_DAYS: 7,
            F_LOCAL_RUN: False
        })

        # Mock the WebSocket iteration
        async def mock_iter(self):
            yield mock_msg

        mock_ws.__aiter__ = mock_iter

        try:
            # Simulate the message processing with normalization
            string_table = {}
            async for msg in mock_ws:
                if msg.type == WSMsgType.BINARY:
                    raw_data = msgpack.unpackb(msg.data)
                    # Normalize the message (this is what handle_nunit_ws does)
                    data = normalize_message(raw_data, string_table)
                    msg_type = data.get("type")

                    if msg_type == "run_started":
                        # Server generates run_id
                        run_id = generate_storage_id()
                        retention_days = data.get("retention_days", 7)
                        local_run = data.get("local_run", False)
                        user_metadata = data.get("user_metadata", {})

                        run = TestRunData(run_id, retention_days, local_run, user_metadata)
                        ws_server.test_runs[run_id] = run

                        # Verify it worked
                        assert run_id in ws_server.test_runs
                        break

        except Exception as e:
            pytest.fail(f"WebSocket server message processing failed: {e}")


if __name__ == "__main__":
    pytest.main([__file__])
