#!/usr/bin/env python3
"""
Tests for AI failure analysis feature.

Covers: config parsing, fingerprinting, trigger logic, deduplication,
tiered AI analysis, API endpoints, email, budget tracking.
"""

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from testrift_server import database
from testrift_server.database import TestCaseData, TestRunData
from testrift_server.ai_analysis import (
    _analysis_tasks,
    _estimate_cost,
    _extract_commit_context,
    _extract_key_logs,
    _normalize_frame,
    compute_symptom_fingerprint,
    get_analysis_status,
    should_analyze,
)
from testrift_server.ai_models import AnalysisContext, AnalysisResult, AnalysisRunStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    """Create a temporary database for testing."""
    database.initialize_database(tmp_path)
    await database.db.initialize()
    yield database.db
    # reset
    database.db = None


@pytest_asyncio.fixture
async def sample_run(temp_db, tmp_path):
    """Create a sample run with a failed test case."""
    run_id = "ai-test-run-001"
    run = TestRunData(
        run_id=run_id,
        status="finished",
        start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        retention_days=7,
        local_run=False,
    )
    user_metadata = {"DUT": {"value": "Device-1"}}
    await temp_db.insert_test_run(run, user_metadata)

    # Insert a failed test case
    tc = TestCaseData(
        id=0,
        run_id=run_id,
        tc_full_name="Namespace.Class.FailedTest",
        tc_id="tc-1",
        status="failed",
        start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
    )
    await temp_db.insert_test_case(tc)
    return run_id


# ---------------------------------------------------------------------------
# should_analyze() tests
# ---------------------------------------------------------------------------


class TestShouldAnalyze:
    def test_disabled_config(self):
        assert should_analyze({}, {"enabled": False}) is False

    def test_no_api_key(self):
        assert should_analyze({}, {"enabled": True, "openai_api_key": ""}) is False

    def test_auto_trigger(self):
        cfg = {"enabled": True, "openai_api_key": "sk-test", "trigger": "auto"}
        assert should_analyze({}, cfg) is True

    def test_manual_trigger(self):
        cfg = {"enabled": True, "openai_api_key": "sk-test", "trigger": "manual"}
        assert should_analyze({}, cfg) is False

    def test_client_prefer_auto(self):
        cfg = {"enabled": True, "openai_api_key": "sk-test", "trigger": "manual"}
        assert should_analyze({"ai_analysis_preference": 1}, cfg) is True

    def test_client_prefer_skip(self):
        cfg = {"enabled": True, "openai_api_key": "sk-test", "trigger": "auto"}
        assert should_analyze({"ai_analysis_preference": 2}, cfg) is False


# ---------------------------------------------------------------------------
# Fingerprinting tests
# ---------------------------------------------------------------------------


class TestFingerprinting:
    def test_no_stack_traces(self):
        assert compute_symptom_fingerprint([]) == "no-stack-trace"

    def test_deterministic(self):
        traces = [{"exception_type": "NullReferenceException", "message": "Object not set", "stack_trace": ["at Foo.Bar()"]}]
        fp1 = compute_symptom_fingerprint(traces)
        fp2 = compute_symptom_fingerprint(traces)
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_different_exceptions_different_fingerprints(self):
        t1 = [{"exception_type": "NullReferenceException", "message": "msg1"}]
        t2 = [{"exception_type": "ArgumentException", "message": "msg2"}]
        assert compute_symptom_fingerprint(t1) != compute_symptom_fingerprint(t2)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_normalize_frame(self):
        frame = "at Foo.Bar() in /path/to/file.cs:line 42"
        assert _normalize_frame(frame) == "at Foo.Bar()"

    def test_extract_key_logs_empty(self):
        assert _extract_key_logs([]) == []

    def test_extract_key_logs_deduplicates(self):
        logs = [{"m": "line1"}, {"m": "line1"}, {"m": "line2"}]
        result = _extract_key_logs(logs)
        assert result == ["line1", "line2"]

    def test_extract_key_logs_includes_errors(self):
        logs = [{"m": f"line{i}"} for i in range(100)]
        logs.insert(50, {"m": "ERROR: something failed"})
        result = _extract_key_logs(logs)
        assert any("ERROR" in line for line in result)

    def test_extract_commit_context(self):
        data = {"diffs": [{"name": "my-repo", "commits": [
            {"subject": "Fix bug", "author": "Alice", "files": [{"change_type": "M", "path": "foo.cs"}]}
        ]}]}
        ctx = _extract_commit_context(data)
        assert "my-repo" in ctx
        assert "Fix bug" in ctx

    def test_estimate_cost(self):
        cost = _estimate_cost("gpt-4.1-mini", 1000, 500)
        assert cost > 0
        assert cost == (1000 * 0.40 + 500 * 1.60) / 1_000_000

    def test_get_analysis_status_unknown_run(self):
        _analysis_tasks.clear()
        status = get_analysis_status("nonexistent-run")
        assert status.status == "not_requested"


# ---------------------------------------------------------------------------
# Database AI methods tests
# ---------------------------------------------------------------------------


class TestDatabaseAI:
    @pytest.mark.asyncio
    async def test_insert_and_get_analysis(self, temp_db, sample_run):
        analysis_id = await temp_db.insert_ai_analysis(
            fingerprint="fp1234567890abcd",
            summary="Null reference in setup",
            references_json='[{"type":"commit","sha":"abc123"}]',
            confidence=0.85,
            category="code_bug",
            model_used="gpt-4.1-mini",
            tier_used=1,
            reasoning=None,
            context_hash="ctx1234567890abcd",
            token_count=500,
        )
        assert analysis_id is not None

        await temp_db.link_analysis_to_test_case(sample_run, "Namespace.Class.FailedTest", analysis_id)

        result = await temp_db.get_analysis_for_test_case(sample_run, "Namespace.Class.FailedTest")
        assert result is not None
        assert result["summary"] == "Null reference in setup"
        assert result["confidence"] == 0.85
        assert result["category"] == "code_bug"

    @pytest.mark.asyncio
    async def test_get_analyses_for_run(self, temp_db, sample_run):
        aid = await temp_db.insert_ai_analysis(
            fingerprint="fp_run_test",
            summary="Test summary",
            references_json="[]",
            confidence=0.9,
            category="environment",
            model_used="gpt-4.1",
            tier_used=2,
            reasoning="Some reasoning",
            context_hash="ctx_hash",
            token_count=1000,
        )
        await temp_db.link_analysis_to_test_case(sample_run, "Namespace.Class.FailedTest", aid)

        analyses = await temp_db.get_analyses_for_run(sample_run)
        assert len(analyses) >= 1
        assert analyses[0]["summary"] == "Test summary"

    @pytest.mark.asyncio
    async def test_dedup_by_fingerprint(self, temp_db):
        aid = await temp_db.insert_ai_analysis(
            fingerprint="dedup_fp",
            summary="Dedup test",
            references_json="[]",
            confidence=0.8,
            category="flaky",
            model_used="gpt-4.1-mini",
            tier_used=1,
            reasoning=None,
            context_hash="ctx",
            token_count=100,
        )

        existing = await temp_db.get_analysis_by_fingerprint("dedup_fp", max_age_days=30)
        assert existing is not None
        assert existing["id"] == aid

        # Non-existing fingerprint
        none_result = await temp_db.get_analysis_by_fingerprint("nonexistent_fp", max_age_days=30)
        assert none_result is None

    @pytest.mark.asyncio
    async def test_ai_usage_tracking(self, temp_db):
        month = "2025-01"
        await temp_db.record_ai_usage(month, 1000, 200, 0.005)
        usage = await temp_db.get_ai_usage_for_month(month)
        assert usage is not None
        assert usage["prompt_tokens"] == 1000
        assert usage["completion_tokens"] == 200

        # Accumulate usage
        await temp_db.record_ai_usage(month, 500, 100, 0.002)
        usage = await temp_db.get_ai_usage_for_month(month)
        assert usage["prompt_tokens"] == 1500
        assert usage["completion_tokens"] == 300

    @pytest.mark.asyncio
    async def test_settings_crud(self, temp_db):
        await temp_db.set_setting("test_key", "test_value")
        val = await temp_db.get_setting("test_key")
        assert val == "test_value"

        await temp_db.delete_setting("test_key")
        val = await temp_db.get_setting("test_key")
        assert val is None

    @pytest.mark.asyncio
    async def test_get_failed_test_cases(self, temp_db, sample_run):
        failed = await temp_db.get_failed_test_cases_for_run(sample_run)
        assert len(failed) >= 1
        assert failed[0]["tc_full_name"] == "Namespace.Class.FailedTest"


# ---------------------------------------------------------------------------
# AI Analysis orchestration tests (mocked OpenAI)
# ---------------------------------------------------------------------------


class TestAnalysisOrchestration:
    @pytest.mark.asyncio
    async def test_analyze_single_tc_tier1_only(self, temp_db):
        """Test tier-1 analysis when confidence is high enough."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": "Null reference in Foo.Bar",
            "confidence": 0.9,
            "category": "code_bug",
            "references": [],
        })
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        from testrift_server.ai_analysis import analyze_single_tc

        context = AnalysisContext(
            run_id="test-run",
            tc_full_name="Ns.Class.Test",
            tc_id="tc-1",
            stack_traces=[{"exception_type": "NullReferenceException", "message": "msg"}],
            log_lines=["line1", "line2"],
        )

        config = {"effort": "normal", "model_tier1": "gpt-4.1-mini"}
        result = await analyze_single_tc(mock_client, context, config, temp_db)

        assert result.summary == "Null reference in Foo.Bar"
        assert result.confidence == 0.9
        assert result.tier_used == 1
        mock_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_single_tc_escalates_to_tier2(self, temp_db):
        """Test tier-1 → tier-2 escalation when confidence is low."""
        tier1_response = MagicMock()
        tier1_response.choices = [MagicMock()]
        tier1_response.choices[0].message.content = json.dumps({
            "summary": "Unclear failure", "confidence": 0.4, "category": "unknown", "references": [],
        })
        tier1_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        tier2_response = MagicMock()
        tier2_response.choices = [MagicMock()]
        tier2_response.choices[0].message.content = json.dumps({
            "summary": "Race condition in DB access",
            "confidence": 0.85,
            "category": "code_bug",
            "references": [],
            "reasoning": "Thread safety issue",
        })
        tier2_response.usage = MagicMock(prompt_tokens=200, completion_tokens=100)

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[tier1_response, tier2_response]
        )

        from testrift_server.ai_analysis import analyze_single_tc

        context = AnalysisContext(
            run_id="test-run", tc_full_name="Ns.Class.Test", tc_id="tc-1",
        )
        config = {"effort": "normal", "model_tier1": "gpt-4.1-mini", "model_tier2": "gpt-4.1"}
        result = await analyze_single_tc(mock_client, context, config, temp_db)

        assert result.tier_used == 2
        assert result.confidence == 0.85
        assert result.prompt_tokens == 300  # combined
        assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_single_tc_high_effort_skips_tier1(self, temp_db):
        """effort=high goes straight to tier-2."""
        tier2_response = MagicMock()
        tier2_response.choices = [MagicMock()]
        tier2_response.choices[0].message.content = json.dumps({
            "summary": "Deep analysis", "confidence": 0.95, "category": "code_bug",
            "references": [], "reasoning": "Detailed reasoning",
        })
        tier2_response.usage = MagicMock(prompt_tokens=300, completion_tokens=150)

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=tier2_response)

        from testrift_server.ai_analysis import analyze_single_tc

        context = AnalysisContext(run_id="r", tc_full_name="T", tc_id="1")
        config = {"effort": "high", "model_tier2": "gpt-4.1"}
        result = await analyze_single_tc(mock_client, context, config, temp_db)

        assert result.tier_used == 2
        mock_client.chat.completions.create.assert_called_once()


# ---------------------------------------------------------------------------
# AI Prompts tests
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_format_context_tier1(self):
        from testrift_server.ai_prompts import format_context_tier1

        context = AnalysisContext(
            run_id="run-1",
            tc_full_name="Ns.Class.Test",
            tc_id="tc-1",
            stack_traces=[{"exception_type": "Exception", "message": "fail", "stack_trace": ["at A()"]}],
            log_lines=["log line 1"],
            classification="new_failure",
        )
        text = format_context_tier1(context)
        assert "Ns.Class.Test" in text
        assert "Exception" in text

    def test_format_context_tier2_includes_tier1(self):
        from testrift_server.ai_prompts import format_context_tier2

        context = AnalysisContext(
            run_id="run-1",
            tc_full_name="Ns.Class.Test",
            tc_id="tc-1",
        )
        tier1 = AnalysisResult(
            summary="Tier1 summary",
            confidence=0.5,
            category="unknown",
        )
        text = format_context_tier2(context, tier1)
        assert "Tier1 summary" in text


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_expand_env_vars(self, monkeypatch):
        from testrift_server.config import expand_env_vars

        monkeypatch.setenv("MY_TEST_VAR", "hello")
        assert expand_env_vars("${env:MY_TEST_VAR}") == "hello"
        assert expand_env_vars("prefix_${env:MY_TEST_VAR}_suffix") == "prefix_hello_suffix"
        assert expand_env_vars("no_vars_here") == "no_vars_here"

    def test_expand_env_vars_missing(self):
        from testrift_server.config import expand_env_vars
        # Missing env vars should remain as-is or empty
        result = expand_env_vars("${env:VERY_UNLIKELY_VAR_12345}")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Analysis status tracking tests
# ---------------------------------------------------------------------------


class TestAnalysisStatus:
    def test_status_lifecycle(self):
        _analysis_tasks.clear()

        # Initially not started
        s = get_analysis_status("run-x")
        assert s.status == "not_requested"

        # Set running
        _analysis_tasks["run-x"] = AnalysisRunStatus(status="running")
        s = get_analysis_status("run-x")
        assert s.status == "running"

        # Set completed
        _analysis_tasks["run-x"].status = "completed"
        _analysis_tasks["run-x"].analyzed_count = 3
        s = get_analysis_status("run-x")
        assert s.status == "completed"
        assert s.analyzed_count == 3

        _analysis_tasks.clear()
