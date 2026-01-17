#!/usr/bin/env python3
"""
Tests for database schema and migration functionality.
"""

import asyncio
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from testrift_server import database
from testrift_server.database import TestCaseData, TestRunData, UserMetadata


class TestDatabaseSchema:
    """Test database schema and migration functionality."""

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
        # Database should already be initialized by temp_db fixture
        assert database.db is not None
        return database.db

    @pytest.mark.asyncio
    async def test_database_initialization(self, initialized_db):
        """Test that database initializes correctly with new schema."""
        # Test that we can create a test run
        test_run = TestRunData(
            run_id="test-run-123",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None,
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )

        result = await initialized_db.insert_test_run(test_run, {})
        assert result is True

    @pytest.mark.asyncio
    async def test_test_case_without_result_field(self, initialized_db):
        """Test that test cases work without the result field."""
        # Create test run first (required for foreign key constraint)
        test_run = TestRunData(
            run_id="test-run-123",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None,
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )
        await initialized_db.insert_test_run(test_run, {})

        # Create a test case with only status field
        test_case = TestCaseData(
            id=0,
            run_id="test-run-123",
            tc_full_name="Test.TestMethod",
            tc_id="tc_test_001",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None
        )

        result = await initialized_db.insert_test_case(test_case)
        assert result is True

        # Retrieve the test case
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        assert len(test_cases) == 1
        assert test_cases[0]["tc_full_name"] == "Test.TestMethod"
        assert test_cases[0]["status"] == "running"
        # Should not have result field
        assert "result" not in test_cases[0]

    @pytest.mark.asyncio
    async def test_test_case_status_updates(self, initialized_db):
        """Test updating test case status."""
        # Create test run first
        test_run = TestRunData(
            run_id="test-run-123",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None,
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )
        await initialized_db.insert_test_run(test_run, {})

        # Create test case
        test_case = TestCaseData(
            id=0,
            run_id="test-run-123",
            tc_full_name="Test.TestMethod",
            tc_id="tc_test_002",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None
        )
        await initialized_db.insert_test_case(test_case)

        # Update test case status
        await database.log_test_case_finished("test-run-123", "Test.TestMethod", "passed")

        # Verify update
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        assert len(test_cases) == 1
        assert test_cases[0]["status"] == "passed"

    @pytest.mark.asyncio
    async def test_database_queries_with_status_field(self, initialized_db):
        """Test that database queries work with status field instead of result."""
        # Create test run with multiple test cases
        test_run = TestRunData(
            run_id="test-run-123",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None,
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )
        await initialized_db.insert_test_run(test_run, {})

        # Create test cases with different statuses
        test_cases = [
            TestCaseData(0, "test-run-123", "Test.Passed", "tc_passed_001", "passed", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", None),
            TestCaseData(0, "test-run-123", "Test.Failed", "tc_failed_001", "failed", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", None),
            TestCaseData(0, "test-run-123", "Test.Skipped", "tc_skipped_001", "skipped", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", None),
            TestCaseData(0, "test-run-123", "Test.Aborted", "tc_aborted_001", "aborted", datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", None),
        ]

        for tc in test_cases:
            await initialized_db.insert_test_case(tc)

        # Test that queries work correctly
        runs = await initialized_db.get_test_runs(limit=10)
        assert len(runs) == 1
        run = runs[0]

        # Check that counts are calculated correctly
        assert run["passed_count"] == 1
        assert run["failed_count"] == 1
        assert run["skipped_count"] == 1
        assert run["aborted_count"] == 1


if __name__ == "__main__":
    pytest.main([__file__])
