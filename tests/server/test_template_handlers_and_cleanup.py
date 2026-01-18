#!/usr/bin/env python3
"""
Tests for template rendering handlers (index, group_runs, test_run_index, test_case_log)
and cleanup functions.
"""

import json
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from testrift_server import database
from testrift_server.tr_server import (
    index_handler,
    group_runs_handler,
    test_run_index_handler as handle_test_run_index,
    test_case_log_handler as handle_test_case_log,
    cleanup_runs_sweep,
    get_run_path,
    get_case_log_path,
    TestRunData,
    TestCaseData,
    generate_storage_id,
)


class TestTemplateHandlers:
    """Test template rendering handlers."""

    @pytest_asyncio.fixture
    async def temp_data_dir(self):
        """Create a temporary data directory for testing."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        database.initialize_database(data_dir)
        await database.db.initialize()

        yield data_dir

        shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def mock_app(self, temp_data_dir):
        """Create a mock aiohttp app with WebSocket server."""
        from testrift_server.tr_server import WebSocketServer

        app = MagicMock()
        ws_server = WebSocketServer()
        # Ensure test_runs.get() returns None for non-existent runs (not MagicMock)
        ws_server.test_runs = {}
        # Make app["ws_server"] return the real ws_server
        app.__getitem__ = lambda self, key: ws_server if key == "ws_server" else MagicMock()
        app.__contains__ = lambda self, key: key == "ws_server"
        return app

    @pytest_asyncio.fixture
    async def sample_run_in_db(self, temp_data_dir):
        """Create a sample test run in the database."""
        run_id = "test-run-template"
        test_run = database.TestRunData(
            run_id=run_id,
            status="finished",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,
            local_run=False,
            dut="TestDevice"
        )

        user_metadata = {"DUT": {"value": "TestDevice"}}
        await database.db.insert_test_run(test_run, user_metadata)

        # Add test case
        from testrift_server.tr_server import generate_storage_id
        tc_id = generate_storage_id()
        test_case = database.TestCaseData(
            0, run_id, "Test.TemplateTest", tc_id, "passed",
            datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
        )
        await database.db.insert_test_case(test_case)

        return run_id

    @pytest.mark.asyncio
    async def test_index_handler(self, temp_data_dir, mock_app):
        """Test index page handler."""
        request = MagicMock()
        request.app = mock_app

        response = await index_handler(request)

        assert response.status == 200
        assert response.content_type == "text/html"
        assert "Cache-Control" in response.headers
        assert "no-cache" in response.headers["Cache-Control"]
        assert "runs" in response.text.lower() or "test" in response.text.lower()

    @pytest.mark.asyncio
    async def test_group_runs_handler_valid(self, temp_data_dir, mock_app):
        """Test group runs handler with valid group hash."""
        # Create a run with a group
        run_id = "test-run-group"
        test_run = database.TestRunData(
            run_id=run_id,
            status="finished",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,
            local_run=False,
            dut="TestDevice"
        )

        user_metadata = {"DUT": {"value": "TestDevice"}}
        await database.db.insert_test_run(test_run, user_metadata)

        # Get the group hash from the database
        run_data = await database.db.get_test_run_by_id(run_id)
        group_hash = run_data.get("group_hash")

        if group_hash:
            request = MagicMock()
            request.app = mock_app
            request.match_info = {"group_hash": group_hash}

            response = await group_runs_handler(request)

            assert response.status == 200
            assert response.content_type == "text/html"

    @pytest.mark.asyncio
    async def test_group_runs_handler_invalid_hash(self, temp_data_dir, mock_app):
        """Test group runs handler with invalid group hash."""
        request = MagicMock()
        request.app = mock_app
        request.match_info = {"group_hash": "invalid-hash"}

        response = await group_runs_handler(request)

        assert response.status == 400
        assert "invalid" in response.text.lower()

    @pytest.mark.asyncio
    async def test_group_runs_handler_not_found(self, temp_data_dir, mock_app):
        """Test group runs handler when group doesn't exist."""
        # Use a valid format but non-existent hash
        request = MagicMock()
        request.app = mock_app
        request.match_info = {"group_hash": "a" * 16}  # Valid format, but doesn't exist

        response = await group_runs_handler(request)

        assert response.status == 404

    @pytest.mark.asyncio
    async def test_test_run_index_handler_from_db(self, temp_data_dir, mock_app, sample_run_in_db):
        """Test test run index handler loading from database."""
        run_id = sample_run_in_db

        request = MagicMock()
        request.app = mock_app
        request.match_info = {"run_id": run_id}

        response = await handle_test_run_index(request)

        assert response.status == 200
        assert response.content_type == "text/html"
        assert run_id in response.text

    @pytest.mark.asyncio
    async def test_test_run_index_handler_not_found(self, temp_data_dir, mock_app):
        """Test test run index handler when run doesn't exist."""
        request = MagicMock()
        request.app = mock_app
        request.match_info = {"run_id": "nonexistent-run"}

        response = await handle_test_run_index(request)

        assert response.status == 404

    @pytest.mark.asyncio
    async def test_test_case_log_handler_from_disk(self, temp_data_dir, mock_app):
        """Test test case log handler loading from disk."""
        run_id = "test-run-log"
        test_case_id = "Test.LogTest"

        # Create run directory structure
        run_path = get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)

        # Create meta.json
        meta = {
            "run_id": run_id,
            "status": "finished",
            "start_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            "end_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            "retention_days": 7,
            "local_run": False,
            "user_metadata": {},
            "test_cases": {
                test_case_id: {
                    "status": "passed",
                    "start_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                    "end_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                    "logs": [],
                    "stack_traces": []
                }
            }
        }

        from testrift_server.tr_server import TC_ID_FIELD
        storage_id = generate_storage_id()
        meta["test_cases"][test_case_id][TC_ID_FIELD] = storage_id

        (run_path / "meta.json").write_text(json.dumps(meta))

        # Create log file
        log_path = get_case_log_path(run_id, tc_id=storage_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", "message": "Test log"}) + "\n"
        )

        request = MagicMock()
        request.app = mock_app
        request.match_info = {"run_id": run_id, "test_case_id": storage_id}

        response = await handle_test_case_log(request)

        assert response.status == 200
        assert response.content_type == "text/html"
        assert test_case_id in response.text

    @pytest.mark.asyncio
    async def test_test_case_log_handler_not_found(self, temp_data_dir, mock_app):
        """Test test case log handler when run doesn't exist."""
        request = MagicMock()
        request.app = mock_app
        request.match_info = {"run_id": "nonexistent-run", "test_case_id": "0-1009"}

        response = await handle_test_case_log(request)

        assert response.status == 404


class TestCleanupFunctions:
    """Test cleanup functions."""

    @pytest_asyncio.fixture
    async def temp_data_dir(self):
        """Create a temporary data directory for testing."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        database.initialize_database(data_dir)
        await database.db.initialize()

        yield data_dir

        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_cleanup_runs_sweep_expired(self, temp_data_dir):
        """Test cleanup sweep removes expired runs."""
        # Create an expired run (older than retention_days)
        run_id = "expired-run"
        old_start_time = (datetime.now(UTC) - timedelta(days=10)).replace(tzinfo=None).isoformat() + "Z"

        test_run = database.TestRunData(
            run_id=run_id,
            status="finished",
            start_time=old_start_time,
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,  # 7 days retention, but run is 10 days old
            local_run=False,
            dut="TestDevice"
        )

        user_metadata = {}
        await database.db.insert_test_run(test_run, user_metadata)

        # Create run directory
        run_path = get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "meta.json").write_text(json.dumps({"run_id": run_id}))

        # Run cleanup sweep
        await cleanup_runs_sweep()

        # Verify run directory was deleted
        assert not run_path.exists()

        # Verify run still exists in database (cleanup only removes files, not DB records)
        run_data = await database.db.get_test_run_by_id(run_id)
        assert run_data is not None

    @pytest.mark.asyncio
    async def test_cleanup_runs_sweep_not_expired(self, temp_data_dir):
        """Test cleanup sweep doesn't remove non-expired runs."""
        # Create a recent run (within retention_days)
        run_id = "recent-run"
        recent_start_time = (datetime.now(UTC) - timedelta(days=2)).replace(tzinfo=None).isoformat() + "Z"

        test_run = database.TestRunData(
            run_id=run_id,
            status="finished",
            start_time=recent_start_time,
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,  # 7 days retention, run is only 2 days old
            local_run=False,
            dut="TestDevice"
        )

        user_metadata = {}
        await database.db.insert_test_run(test_run, user_metadata)

        # Create run directory
        run_path = get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "meta.json").write_text(json.dumps({"run_id": run_id}))

        # Run cleanup sweep
        await cleanup_runs_sweep()

        # Verify run directory still exists
        assert run_path.exists()

    @pytest.mark.asyncio
    async def test_cleanup_runs_sweep_no_retention(self, temp_data_dir):
        """Test cleanup sweep doesn't remove runs without retention_days."""
        # Create a run without retention_days
        run_id = "no-retention-run"
        old_start_time = (datetime.now(UTC) - timedelta(days=100)).replace(tzinfo=None).isoformat() + "Z"

        test_run = database.TestRunData(
            run_id=run_id,
            status="finished",
            start_time=old_start_time,
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=None,  # No retention policy
            local_run=False,
            dut="TestDevice"
        )

        user_metadata = {}
        await database.db.insert_test_run(test_run, user_metadata)

        # Create run directory
        run_path = get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "meta.json").write_text(json.dumps({"run_id": run_id}))

        # Run cleanup sweep
        await cleanup_runs_sweep()

        # Verify run directory still exists (no retention means never delete)
        assert run_path.exists()

