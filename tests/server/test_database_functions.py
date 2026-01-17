#!/usr/bin/env python3
"""
Tests for database functions and operations.
"""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from testrift_server import database
from testrift_server.database import TestCaseData, TestRunData, UserMetadata


class TestDatabaseFunctions:
    """Test database functions and operations."""

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
    async def sample_test_run(self, initialized_db):
        """Create a sample test run in the database."""
        test_run = TestRunData(
            run_id="test-run-123",
            status="finished",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )

        user_metadata = {
            "DUT": {"value": "TestDevice-001"},
            "Environment": {"value": "Test"}
        }

        await initialized_db.insert_test_run(test_run, user_metadata)
        return test_run

    @pytest_asyncio.fixture
    async def sample_test_cases(self, initialized_db, sample_test_run):
        """Create sample test cases in the database."""
        test_cases = [
            TestCaseData(0, "test-run-123", "Test.Passed", "tc_passed_001", "passed",
                        datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                        datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"),
            TestCaseData(0, "test-run-123", "Test.Failed", "tc_failed_002", "failed",
                        datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                        datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"),
            TestCaseData(0, "test-run-123", "Test.Skipped", "tc_skipped_003", "skipped",
                        datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                        datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"),
        ]

        for tc in test_cases:
            await initialized_db.insert_test_case(tc)

        return test_cases

    @pytest.mark.asyncio
    async def test_get_test_run_by_id(self, initialized_db, sample_test_run):
        """Test get_test_run_by_id function."""
        # Test getting existing test run
        run = await initialized_db.get_test_run_by_id("test-run-123")
        assert run is not None
        assert run["run_id"] == "test-run-123"
        assert run["status"] == "finished"
        assert run["dut"] == "TestDevice-001"

        # Test getting non-existent test run
        run = await initialized_db.get_test_run_by_id("non-existent-run")
        assert run is None

    @pytest.mark.asyncio
    async def test_get_test_cases_for_run(self, initialized_db, sample_test_run, sample_test_cases):
        """Test get_test_cases_for_run function."""
        # Test getting test cases for existing run
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        assert isinstance(test_cases, list)
        assert len(test_cases) == 3

        # Verify test case data
        test_case_names = [tc["tc_full_name"] for tc in test_cases]
        assert "Test.Passed" in test_case_names
        assert "Test.Failed" in test_case_names
        assert "Test.Skipped" in test_case_names

        # Test getting test cases for non-existent run
        test_cases = await initialized_db.get_test_cases_for_run("non-existent-run")
        assert isinstance(test_cases, list)
        assert len(test_cases) == 0

    @pytest.mark.asyncio
    async def test_log_test_run_started(self, initialized_db):
        """Test log_test_run_started convenience function."""
        # Test logging test run start
        success = await database.log_test_run_started(
            "test-run-456",
            retention_days=7,
            local_run=True,
            user_metadata={"DUT": {"value": "TestDevice-002"}},
            dut="TestDevice-002",
            group_name="Release Builds",
            group_hash="abc123def4567890",
            group_metadata={
                "Branch": {"value": "release/v2"},
                "Environment": {"value": "staging", "url": "https://staging.example.com"}
            }
        )
        assert success is True

        # Verify the test run was created
        run = await initialized_db.get_test_run_by_id("test-run-456")
        assert run is not None
        assert run["status"] == "running"
        assert run["retention_days"] == 7
        assert run["local_run"] == 1  # SQLite stores boolean as integer
        assert run["dut"] == "TestDevice-002"

        # Verify metadata was stored
        metadata = await initialized_db.get_user_metadata_for_run("test-run-456")
        assert "DUT" in metadata
        assert metadata["DUT"]["value"] == "TestDevice-002"

        group_metadata = await initialized_db.get_group_metadata_for_run("test-run-456")
        assert "Branch" in group_metadata
        assert group_metadata["Branch"]["value"] == "release/v2"
        assert group_metadata["Environment"]["url"] == "https://staging.example.com"
        assert run["group_name"] == "Release Builds"
        assert run["group_hash"] == "abc123def4567890"

    @pytest.mark.asyncio
    async def test_log_test_run_finished(self, initialized_db, sample_test_run):
        """Test log_test_run_finished convenience function."""
        # Test logging test run finish
        success = await database.log_test_run_finished("test-run-123", "aborted")
        assert success is True

        # Verify the test run was updated
        run = await initialized_db.get_test_run_by_id("test-run-123")
        assert run is not None
        assert run["status"] == "aborted"
        assert run["end_time"] is not None

    @pytest.mark.asyncio
    async def test_log_test_case_started(self, initialized_db, sample_test_run):
        """Test log_test_case_started convenience function."""
        # Test logging test case start
        from testrift_server.tr_server import generate_storage_id
        tc_id = generate_storage_id()
        success = await database.log_test_case_started("test-run-123", "Test.NewMethod", tc_id)
        assert success is True

        # Verify the test case was created
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        test_case = next((tc for tc in test_cases if tc["tc_full_name"] == "Test.NewMethod"), None)
        assert test_case is not None
        assert test_case["status"] == "running"
        assert test_case["start_time"] is not None
        assert test_case["end_time"] is None

    @pytest.mark.asyncio
    async def test_log_test_case_finished(self, initialized_db, sample_test_run):
        """Test log_test_case_finished convenience function."""
        from testrift_server.tr_server import generate_storage_id
        tc_id = generate_storage_id()
        # First create a test case
        await database.log_test_case_started("test-run-123", "Test.NewMethod", tc_id)

        # Test logging test case finish
        success = await database.log_test_case_finished("test-run-123", "Test.NewMethod", "passed")
        assert success is True

        # Verify the test case was updated
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        test_case = next((tc for tc in test_cases if tc["tc_full_name"] == "Test.NewMethod"), None)
        assert test_case is not None
        assert test_case["status"] == "passed"
        assert test_case["end_time"] is not None

    @pytest.mark.asyncio
    async def test_database_initialization_multiple_calls(self, initialized_db):
        """Test that database initialization can be called multiple times safely."""
        # Initialize again (should not cause errors)
        await initialized_db.initialize()
        await initialized_db.initialize()

        # Database should still work
        runs = await initialized_db.get_test_runs(limit=10)
        assert isinstance(runs, list)

    @pytest.mark.asyncio
    async def test_foreign_key_constraints(self, initialized_db):
        """Test foreign key constraints work correctly."""
        # Try to insert a test case with non-existent run_id
        test_case = TestCaseData(
            id=0,
            run_id="non-existent-run",
            tc_full_name="Test.Invalid",
            tc_id="tc_invalid_001",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None
        )

        # This should fail due to foreign key constraint
        success = await initialized_db.insert_test_case(test_case)
        assert success is False

    @pytest.mark.asyncio
    async def test_cascade_delete(self, initialized_db, sample_test_run, sample_test_cases):
        """Test that deleting a test run cascades to test cases."""
        # Verify test cases exist
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        assert len(test_cases) == 3

        # Delete the test run (this should cascade to test cases)
        # Note: We need to implement a delete function or test this through SQL
        async with initialized_db.get_connection() as db:
            await db.execute("DELETE FROM test_runs WHERE run_id = ?", ("test-run-123",))
            await db.commit()

        # Verify test run is gone
        run = await initialized_db.get_test_run_by_id("test-run-123")
        assert run is None

        # Verify test cases are also gone (cascade delete)
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        assert len(test_cases) == 0

    @pytest.mark.asyncio
    async def test_unique_constraints(self, initialized_db, sample_test_run):
        """Test unique constraints work correctly."""
        # Try to insert a test case with the same run_id and tc_full_name
        test_case = TestCaseData(
            id=0,
            run_id="test-run-123",
            tc_full_name="Test.Passed",  # This already exists
            tc_id="tc_passed_001",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None
        )

        # This should succeed due to INSERT OR REPLACE
        success = await initialized_db.insert_test_case(test_case)
        assert success is True

        # Verify only one test case with this ID exists
        test_cases = await initialized_db.get_test_cases_for_run("test-run-123")
        passed_cases = [tc for tc in test_cases if tc["tc_full_name"] == "Test.Passed"]
        assert len(passed_cases) == 1

    @pytest.mark.asyncio
    async def test_database_connection_management(self, initialized_db):
        """Test database connection management."""
        # Test that we can get multiple connections
        async with initialized_db.get_connection() as db1:
            async with initialized_db.get_connection() as db2:
                # Both connections should work
                cursor1 = await db1.execute("SELECT COUNT(*) FROM test_runs")
                cursor2 = await db2.execute("SELECT COUNT(*) FROM test_runs")

                count1 = await cursor1.fetchone()
                count2 = await cursor2.fetchone()

                assert count1[0] == count2[0]

    @pytest.mark.asyncio
    async def test_data_type_validation(self, initialized_db):
        """Test data type validation and error handling."""
        # Test with invalid data types
        invalid_run = TestRunData(
            run_id="",  # Empty string should be handled
            status="invalid_status",  # Invalid status
            start_time="invalid_timestamp",  # Invalid timestamp
            end_time="invalid_timestamp",
            retention_days=-1,  # Negative retention days
            local_run="not_boolean",  # Invalid boolean
            dut="",  # Empty DUT
        )

        # This should still succeed (database doesn't validate data types strictly)
        success = await initialized_db.insert_test_run(invalid_run, {})
        assert success is True

        # Verify the data was stored as-is
        run = await initialized_db.get_test_run_by_id("")
        assert run is not None
        assert run["status"] == "invalid_status"

    @pytest.mark.asyncio
    async def test_metadata_edge_cases(self, initialized_db):
        """Test metadata handling edge cases."""
        test_run = TestRunData(
            run_id="test-run-metadata",
            status="running",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=None,
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )

        # Test with various metadata types
        user_metadata = {
            "string_value": "simple_string",
            "dict_value": {"value": "dict_string", "url": "http://example.com"},
            "number_value": {"value": "123"},  # Convert to string
            "boolean_value": {"value": "True"},  # Convert to string
            "empty_string": {"value": ""},
            "empty_dict": {"value": ""},  # Provide a value for empty dict
        }

        success = await initialized_db.insert_test_run(test_run, user_metadata)
        assert success is True

        # Verify metadata was stored
        metadata = await initialized_db.get_user_metadata_for_run("test-run-metadata")
        assert "string_value" in metadata
        assert "dict_value" in metadata
        assert "number_value" in metadata
        assert "boolean_value" in metadata
        assert "empty_string" in metadata
        assert "empty_dict" in metadata


class TestDatabaseInitialization:
    """Test database initialization and setup."""

    @pytest.mark.asyncio
    async def test_initialize_database_function(self):
        """Test the initialize_database function."""
        temp_dir = tempfile.mkdtemp()
        try:
            # Test database initialization
            db = database.initialize_database(temp_dir)
            assert db is not None
            assert isinstance(db, database.TestResultsDatabase)

            # Initialize the database (this creates the file)
            await db.initialize()

            # Test that the database file was created
            db_path = Path(temp_dir) / "test_results.db"
            assert db_path.exists()

            # Test that the global db instance was set
            assert database.db is not None
            assert database.db is db

        finally:
            # Cleanup
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_database_initialization_with_existing_file(self):
        """Test database initialization with existing database file."""
        temp_dir = tempfile.mkdtemp()
        try:
            # Create initial database
            db1 = database.initialize_database(temp_dir)
            await db1.initialize()

            # Add some data
            test_run = TestRunData(
                run_id="test-run-existing",
                status="running",
                start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                end_time=None,
                retention_days=7,
                local_run=False,
                dut="TestDevice-001"
            )
            await db1.insert_test_run(test_run, {})

            # Initialize again (should not lose data)
            db2 = database.initialize_database(temp_dir)
            await db2.initialize()

            # Verify data still exists
            run = await db2.get_test_run_by_id("test-run-existing")
            assert run is not None
            assert run["run_id"] == "test-run-existing"

        finally:
            # Cleanup
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_database_initialization_directory_creation(self):
        """Test that database initialization creates directories if they don't exist."""
        temp_dir = tempfile.mkdtemp()
        non_existent_dir = Path(temp_dir) / "non_existent" / "subdir"

        try:
            # Initialize database in non-existent directory
            db = database.initialize_database(str(non_existent_dir))
            assert db is not None

            # Initialize the database (this creates the file and directories)
            await db.initialize()

            # Verify directory was created
            assert non_existent_dir.exists()
            assert non_existent_dir.is_dir()

            # Verify database file was created
            db_path = non_existent_dir / "test_results.db"
            assert db_path.exists()

        finally:
            # Cleanup
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    pytest.main([__file__])
