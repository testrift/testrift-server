#!/usr/bin/env python3
"""
Tests for attachment handlers (upload, download, list) and ZIP export.
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web, MultipartReader

from testrift_server import database
from testrift_server.tr_server import (
    upload_attachment_handler,
    download_attachment_handler,
    list_attachments_handler,
    zip_export_handler,
    get_run_path,
    get_attachments_dir,
    sanitize_filename,
    parse_size_string,
    TestRunData,
)


class TestAttachmentHandlers:
    """Test attachment upload, download, and list handlers."""

    @pytest_asyncio.fixture
    async def temp_data_dir(self):
        """Create a temporary data directory for testing."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        database.initialize_database(data_dir)
        await database.db.initialize()

        yield data_dir

        # Cleanup
        shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def sample_run(self, temp_data_dir):
        """Create a sample test run directory structure."""
        import uuid
        run_id = f"test-run-attachments-{uuid.uuid4().hex[:8]}"
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
            "test_cases": {}
        }
        (run_path / "meta.json").write_text(json.dumps(meta))

        yield run_id

        # Cleanup after test
        if run_path.exists():
            shutil.rmtree(run_path, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_upload_attachment_success(self, temp_data_dir, sample_run):
        """Test successful attachment upload."""
        run_id = sample_run
        test_case_id = "Test.AttachmentTest"

        # Create test case log directory
        (get_run_path(run_id) / test_case_id).mkdir(parents=True, exist_ok=True)

        # Create mock multipart request
        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id}

        # Mock multipart reader
        part = MagicMock()
        part.name = "attachment"
        part.filename = "test_file.txt"
        part.read_chunk = AsyncMock(side_effect=[b"test content", None])

        reader = AsyncMock()
        reader.next = AsyncMock(side_effect=[part, None])
        request.multipart = AsyncMock(return_value=reader)

        # Mock ATTACHMENTS_ENABLED
        with patch("testrift_server.tr_server.ATTACHMENTS_ENABLED", True):
            with patch("testrift_server.tr_server.ATTACHMENT_MAX_SIZE", 10 * 1024 * 1024):
                response = await upload_attachment_handler(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data["success"] is True
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["filename"] == "test_file.txt"

        # Verify file was saved
        attachment_path = get_attachments_dir(run_id, test_case_id) / "test_file.txt"
        assert attachment_path.exists()
        assert attachment_path.read_text() == "test content"

    @pytest.mark.asyncio
    async def test_upload_attachment_disabled(self, temp_data_dir, sample_run):
        """Test upload when attachments are disabled."""
        run_id = sample_run
        test_case_id = "Test.AttachmentTest"

        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id}

        with patch("testrift_server.tr_server.ATTACHMENTS_ENABLED", False):
            response = await upload_attachment_handler(request)

        assert response.status == 403
        assert "disabled" in response.text.lower()

    @pytest.mark.asyncio
    async def test_upload_attachment_file_too_large(self, temp_data_dir, sample_run):
        """Test upload when file exceeds size limit."""
        run_id = sample_run
        test_case_id = "Test.AttachmentTest"

        (get_run_path(run_id) / test_case_id).mkdir(parents=True, exist_ok=True)

        # Create large content (exceeds 1MB limit)
        large_content = b"x" * (2 * 1024 * 1024)  # 2MB

        part = MagicMock()
        part.name = "attachment"
        part.filename = "large_file.txt"
        part.read_chunk = AsyncMock(side_effect=[large_content[:8192], large_content[8192:], None])

        reader = AsyncMock()
        reader.next = AsyncMock(side_effect=[part, None])
        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id}
        request.multipart = AsyncMock(return_value=reader)

        with patch("testrift_server.tr_server.ATTACHMENTS_ENABLED", True):
            with patch("testrift_server.tr_server.ATTACHMENT_MAX_SIZE", 1024 * 1024):  # 1MB limit
                response = await upload_attachment_handler(request)

        assert response.status == 413
        assert "too large" in response.text.lower()

    @pytest.mark.asyncio
    async def test_download_attachment_success(self, temp_data_dir, sample_run):
        """Test successful attachment download."""
        run_id = sample_run
        test_case_id = "Test.AttachmentTest"
        filename = "test_file.txt"

        # Create attachment file
        attachments_dir = get_attachments_dir(run_id, test_case_id)
        attachments_dir.mkdir(parents=True, exist_ok=True)
        attachment_path = attachments_dir / filename
        attachment_path.write_text("test content")

        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id, "filename": filename}

        response = await download_attachment_handler(request)

        assert isinstance(response, web.FileResponse)
        # FileResponse uses _path attribute internally
        assert str(response._path) == str(attachment_path)
        assert "attachment" in response.headers["Content-Disposition"]
        assert filename in response.headers["Content-Disposition"]

    @pytest.mark.asyncio
    async def test_download_attachment_not_found(self, temp_data_dir, sample_run):
        """Test download when attachment doesn't exist."""
        run_id = sample_run
        test_case_id = "Test.AttachmentTest"
        filename = "nonexistent.txt"

        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id, "filename": filename}

        response = await download_attachment_handler(request)

        assert response.status == 404
        assert "not found" in response.text.lower()

    @pytest.mark.asyncio
    async def test_list_attachments_success(self, temp_data_dir, sample_run):
        """Test listing attachments."""
        run_id = sample_run
        test_case_id = "Test.AttachmentTest"

        # Create multiple attachment files
        attachments_dir = get_attachments_dir(run_id, test_case_id)
        attachments_dir.mkdir(parents=True, exist_ok=True)
        (attachments_dir / "file1.txt").write_text("content1")
        (attachments_dir / "file2.txt").write_text("content2")

        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id}

        response = await list_attachments_handler(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert "attachments" in data
        # Get only the files we created (ignore any leftover files)
        filenames = [a["filename"] for a in data["attachments"]]
        assert "file1.txt" in filenames
        assert "file2.txt" in filenames
        # Verify at least our 2 files are present
        assert len([f for f in filenames if f in ["file1.txt", "file2.txt"]]) == 2

    @pytest.mark.asyncio
    async def test_list_attachments_empty(self, temp_data_dir, sample_run):
        """Test listing attachments when none exist."""
        run_id = sample_run
        test_case_id = "Test.EmptyAttachments"

        request = MagicMock()
        request.match_info = {"run_id": run_id, "test_case_id": test_case_id}

        response = await list_attachments_handler(request)

        assert response.status == 200
        data = json.loads(response.text)
        # Should be empty for a test case that doesn't have attachments
        assert data["attachments"] == []


class TestZipExport:
    """Test ZIP export handler."""

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
    async def sample_run_with_data(self, temp_data_dir):
        """Create a sample test run with logs and attachments."""
        run_id = "test-run-export"
        run_path = get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)

        test_case_id = "Test.ExportTest"

        # Create meta.json
        meta = {
            "run_id": run_id,
            "run_name": "Export Test Run",
            "status": "finished",
            "start_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            "end_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            "retention_days": 7,
            "local_run": False,
            "user_metadata": {"DUT": {"value": "TestDevice"}},
            "test_cases": {
                test_case_id: {
                    "status": "passed",
                    "start_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                    "end_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                }
            }
        }
        (run_path / "meta.json").write_text(json.dumps(meta))

        # Create test case log
        log_path = run_path / test_case_id / "log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", "message": "Test log"}) + "\n"
        )

        # Create attachment
        attachments_dir = get_attachments_dir(run_id, test_case_id)
        attachments_dir.mkdir(parents=True, exist_ok=True)
        (attachments_dir / "test_attachment.txt").write_text("attachment content")

        return run_id

    @pytest.mark.asyncio
    async def test_zip_export_success(self, temp_data_dir, sample_run_with_data):
        """Test successful ZIP export."""
        run_id = sample_run_with_data

        request = MagicMock()
        request.match_info = {"run_id": run_id}

        response = await zip_export_handler(request)

        assert isinstance(response, web.FileResponse)
        zip_path = response._path
        assert zip_path.suffix == ".zip"
        assert run_id in zip_path.name

        # Verify ZIP contents
        with zipfile.ZipFile(zip_path, 'r') as zf:
            files = zf.namelist()
            assert "index.html" in files, f"index.html not found. Files: {files}"
            # Check for static files (CSS and JS)
            static_files = [f for f in files if "static" in f.lower()]
            assert len(static_files) > 0, f"No static files found. Files: {files}"
            # Check for attachments (should be present)
            attachment_files = [f for f in files if "attachments" in f.lower()]
            assert len(attachment_files) > 0, f"No attachment files found. Files: {files}"
            # Log HTML files are only created if log.jsonl exists and has content
            # This is optional, so we don't assert on it

    @pytest.mark.asyncio
    async def test_zip_export_run_not_found(self, temp_data_dir):
        """Test ZIP export when run doesn't exist."""
        run_id = "nonexistent-run"

        request = MagicMock()
        request.match_info = {"run_id": run_id}

        response = await zip_export_handler(request)

        assert response.status == 404
        assert "not found" in response.text.lower()


class TestUtilityFunctions:
    """Test utility functions."""

    def test_parse_size_string_bytes(self):
        """Test parsing size strings in bytes."""
        assert parse_size_string("1024") == 1024
        assert parse_size_string("500") == 500
        assert parse_size_string(1024) == 1024

    def test_parse_size_string_kb(self):
        """Test parsing size strings in KB."""
        assert parse_size_string("10KB") == 10 * 1024
        assert parse_size_string("1.5KB") == int(1.5 * 1024)
        assert parse_size_string("500KB") == 500 * 1024

    def test_parse_size_string_mb(self):
        """Test parsing size strings in MB."""
        assert parse_size_string("10MB") == 10 * 1024 * 1024
        assert parse_size_string("1MB") == 1024 * 1024

    def test_parse_size_string_gb(self):
        """Test parsing size strings in GB."""
        assert parse_size_string("1GB") == 1024 * 1024 * 1024
        assert parse_size_string("2.5GB") == int(2.5 * 1024 * 1024 * 1024)

    def test_parse_size_string_case_insensitive(self):
        """Test that size parsing is case insensitive."""
        assert parse_size_string("10kb") == 10 * 1024
        assert parse_size_string("10MB") == parse_size_string("10mb")

    def test_parse_size_string_invalid(self):
        """Test parsing invalid size strings."""
        with pytest.raises(ValueError):
            parse_size_string("invalid")
        with pytest.raises(ValueError):
            parse_size_string("10XX")

    def test_sanitize_filename_safe(self):
        """Test sanitizing safe filenames."""
        assert sanitize_filename("test.txt") == "test.txt"
        assert sanitize_filename("test_file-123.txt") == "test_file-123.txt"

    def test_sanitize_filename_path_traversal(self):
        """Test sanitizing filenames with path traversal attempts."""
        # sanitize_filename replaces .. with _ and / with _
        assert sanitize_filename("../test.txt") == "__test.txt"
        assert sanitize_filename("../../etc/passwd") == "____etc_passwd"
        # test/../file.txt -> test_ (replace /) + __ (replace ..) + file.txt = test___file.txt
        assert sanitize_filename("test/../file.txt") == "test___file.txt"

    def test_sanitize_filename_special_chars(self):
        """Test sanitizing filenames with special characters."""
        assert sanitize_filename("test:file.txt") == "test_file.txt"
        assert sanitize_filename("test|file.txt") == "test_file.txt"
        assert sanitize_filename("test*file.txt") == "test_file.txt"
        assert sanitize_filename("test?file.txt") == "test_file.txt"

