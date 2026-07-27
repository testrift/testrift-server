#!/usr/bin/env python3
"""
Tests for commit diff API endpoints and database functions.
"""

import json
import shutil
import tempfile
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from testrift_server import database
from testrift_server.database import TestRunData


class TestCommitDiffAPI:
    """Test commit diff API endpoints and database functions."""

    @pytest_asyncio.fixture
    async def temp_db(self):
        """Create a temporary database and data dir for testing."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        from testrift_server import config
        old_data_dir = config.DATA_DIR
        config.DATA_DIR = data_dir

        # Initialize database in the temp data directory
        database.initialize_database(data_dir)
        await database.db.initialize()

        try:
            yield data_dir
        finally:
            config.DATA_DIR = old_data_dir
            shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def initialized_db(self, temp_db):
        """Ensure database is initialized for tests."""
        assert database.db is not None
        return database.db

    @pytest_asyncio.fixture
    async def sample_run(self, initialized_db):
        """Create a sample Target Run in the database."""
        await initialized_db.get_or_create_target("test-target")
        test_run = TestRunData(
            run_id="run-with-commits",
            status="finished",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,
            local_run=False,
            target_key="test-target",
            purpose="manual",
        )

        await initialized_db.insert_test_run(test_run, {})
        return test_run

    @pytest.mark.asyncio
    async def test_insert_run_commits(self, initialized_db, sample_run):
        """Test inserting commit SHAs for a run."""
        commits = [
            {
                "repo_name": "my-app",
                "commit_sha": "abc123def456789",
                "repo_url": "https://github.com/org/my-app"
            },
            {
                "repo_name": "firmware",
                "commit_sha": "xyz789abc123456",
                "repo_url": "https://github.com/org/firmware"
            }
        ]

        success = await initialized_db.insert_run_commits("run-with-commits", commits)
        assert success is True

        # Verify commits were stored
        stored_commits = await initialized_db.get_commits_for_run("run-with-commits")
        assert len(stored_commits) == 2

        repo_names = [c["repo_name"] for c in stored_commits]
        assert "my-app" in repo_names
        assert "firmware" in repo_names

    @pytest.mark.asyncio
    async def test_commit_update_on_reinsert(self, initialized_db, sample_run):
        """Test that reinserting commits updates existing records."""
        # Insert initial commit
        await initialized_db.insert_run_commits("run-with-commits", [
            {"repo_name": "my-app", "commit_sha": "old-sha", "repo_url": None}
        ])

        # Update with new SHA
        await initialized_db.insert_run_commits("run-with-commits", [
            {"repo_name": "my-app", "commit_sha": "new-sha", "repo_url": "https://new-url"}
        ])

        # Verify update
        commits = await initialized_db.get_commits_for_run("run-with-commits")
        assert len(commits) == 1
        assert commits[0]["commit_sha"] == "new-sha"
        assert commits[0]["repo_url"] == "https://new-url"

    @pytest.mark.asyncio
    async def test_upload_handler_persists_commit_file(self, initialized_db, temp_db):
        """Uploading diffs stores commits.json on disk while DB keeps only SHAs."""
        from testrift_server.api_handlers import api_run_commits_upload_handler

        run_id = "run-upload-handler"
        test_run = TestRunData(
            run_id=run_id,
            status="finished",
            start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            retention_days=7,
            local_run=False,
            dut="TestDevice-001"
        )
        await initialized_db.insert_test_run(test_run, {})

        payload = {
            "diffs": [
                {
                    "name": "sample-repo",
                    "url": "https://example.com/sample-repo",
                    "current_sha": "abc123",
                    "previous_sha": "def456",
                    "commits": [
                        {
                            "sha": "abc123",
                            "subject": "Add feature",
                            "author": "Dev",
                            "timestamp": "2024-01-02T03:04:05Z",
                            "files": [
                                {"path": "src/main.cs", "change_type": "M"}
                            ]
                        }
                    ]
                }
            ]
        }

        request = MagicMock()
        request.match_info = {"run_id": run_id}
        request.json = AsyncMock(return_value=payload)

        response = await api_run_commits_upload_handler(request)
        assert response.status == 200

        run_dir = Path(temp_db) / run_id
        commits_file = run_dir / "commits.json"
        assert commits_file.exists(), "commits.json should be written next to the run artifacts"

        stored = json.loads(commits_file.read_text(encoding="utf-8"))
        assert stored["diffs"][0]["name"] == "sample-repo"

        db_commits = await initialized_db.get_commits_for_run(run_id)
        assert db_commits == [{
            "repo_name": "sample-repo",
            "commit_sha": "abc123",
            "repo_url": "https://example.com/sample-repo"
        }]
