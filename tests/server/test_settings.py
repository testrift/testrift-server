#!/usr/bin/env python3
"""
Tests for settings page and settings API endpoints.
"""

import json
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio

from testrift_server import database


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    """Create a temporary database for testing."""
    database.initialize_database(tmp_path)
    await database.db.initialize()
    yield database.db
    database.db = None


class TestSettingsDatabase:
    """Test settings storage in database."""

    @pytest.mark.asyncio
    async def test_set_and_get_setting(self, temp_db):
        await temp_db.set_setting("email_recipients", json.dumps(["a@b.com"]))
        val = await temp_db.get_setting("email_recipients")
        assert json.loads(val) == ["a@b.com"]

    @pytest.mark.asyncio
    async def test_get_missing_setting_returns_none(self, temp_db):
        val = await temp_db.get_setting("nonexistent")
        assert val is None

    @pytest.mark.asyncio
    async def test_delete_setting(self, temp_db):
        await temp_db.set_setting("email_recipients", json.dumps(["a@b.com"]))
        await temp_db.delete_setting("email_recipients")
        val = await temp_db.get_setting("email_recipients")
        assert val is None

    @pytest.mark.asyncio
    async def test_overwrite_setting(self, temp_db):
        await temp_db.set_setting("email_recipients", json.dumps(["a@b.com"]))
        await temp_db.set_setting("email_recipients", json.dumps(["x@y.com"]))
        val = await temp_db.get_setting("email_recipients")
        assert json.loads(val) == ["x@y.com"]


class TestShouldAnalyzePreferences:
    """Test AI analysis trigger with different preferences."""

    def test_preference_skip_returns_false(self):
        from testrift_server.ai_analysis import should_analyze
        config = {"enabled": True, "openai_api_key": "sk-test", "trigger": "auto"}
        run_meta = {"ai_analysis_preference": 2}
        assert should_analyze(run_meta, config) is False

    def test_preference_auto_returns_true(self):
        from testrift_server.ai_analysis import should_analyze
        config = {"enabled": True, "openai_api_key": "sk-test", "trigger": "manual"}
        run_meta = {"ai_analysis_preference": 1}
        assert should_analyze(run_meta, config) is True

    def test_preference_default_uses_config_trigger(self):
        from testrift_server.ai_analysis import should_analyze
        config = {"enabled": True, "openai_api_key": "sk-test", "trigger": "auto"}
        run_meta = {"ai_analysis_preference": 0}
        assert should_analyze(run_meta, config) is True

    def test_no_api_key_returns_false(self):
        from testrift_server.ai_analysis import should_analyze
        config = {"enabled": True, "openai_api_key": "", "trigger": "auto"}
        run_meta = {}
        assert should_analyze(run_meta, config) is False
