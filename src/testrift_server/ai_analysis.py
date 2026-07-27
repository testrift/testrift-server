"""
AI failure analysis orchestrator.

Coordinates test failure analysis: context extraction, deduplication,
tiered AI calls, result storage, and email notification.
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from .ai_models import AnalysisContext, AnalysisResult, AnalysisRunStatus, BudgetExceededError, CollectionReportContext
from .ai_prompts import (
    SYSTEM_PROMPT_TIER1,
    SYSTEM_PROMPT_TIER2,
    SYSTEM_PROMPT_DEEP,
    format_context_tier1,
    format_context_tier2,
    format_context_deep,
)
from .config import AI_ANALYSIS_CONFIG, EMAIL_CONFIG
from .utils import get_run_path, read_meta_msgpack
from . import database
from .summary_profiles import select_profile_from_database

logger = logging.getLogger(__name__)

# Track running analysis tasks: run_id -> AnalysisRunStatus
_analysis_tasks: dict[str, AnalysisRunStatus] = {}

# Pricing per 1M tokens (approximate, as of 2025)
MODEL_PRICING = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}


async def build_collection_report_context(db_instance, profile_id: int, requested_at: datetime) -> CollectionReportContext:
    """Build a report input from deterministic Summary selections only."""
    inputs = await db_instance.get_summary_profile_selection_inputs(profile_id)
    selections = await select_profile_from_database(db_instance, profile_id, requested_at)
    selected_ids = {selection.run_id for selection in selections if selection.run_id}
    sources = {run["run_id"]: run["sources"] for run in inputs["runs"] if run["run_id"] in selected_ids}
    clusters = {}
    if selected_ids:
        placeholders = ", ".join("?" for _ in selected_ids)
        async with db_instance.get_connection() as connection:
            cursor = await connection.execute(
                f"""SELECT tc_full_name, status, run_id FROM test_cases
                    WHERE run_id IN ({placeholders}) AND status IN ('failed', 'error')""",
                list(selected_ids),
            )
            for test_name, status, run_id in await cursor.fetchall():
                clusters.setdefault((test_name, status), []).append(run_id)
    return CollectionReportContext(
        collection_id=inputs["profile"]["collection_id"],
        profile_id=profile_id,
        requested_at=requested_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        selections=[selection.__dict__ for selection in selections],
        sources=sources,
        failure_clusters=[{"test_name": name, "status": status, "run_ids": run_ids, "scope": "shared" if len(run_ids) > 1 else "target-specific"} for (name, status), run_ids in clusters.items()],
    )


async def create_collection_report(db_instance, profile_id: int, requested_at: datetime) -> dict:
    """Persist the deterministic report context without independently selecting Runs."""
    context = await build_collection_report_context(db_instance, profile_id, requested_at)
    return await db_instance.get_or_create_collection_report(context.__dict__)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD for a given model and token counts."""
    pricing = MODEL_PRICING.get(model, {"input": 2.0, "output": 8.0})
    return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1_000_000


def should_analyze(run_meta: dict, config: dict) -> bool:
    """Determine whether AI analysis should run for this run.

    Implements the trigger resolution logic from the design doc.
    """
    if not config.get("enabled", False):
        return False

    api_key = config.get("openai_api_key", "")
    if not api_key:
        logger.info("AI analysis disabled: no OpenAI API key configured")
        return False

    preference = run_meta.get("ai_analysis_preference", 0)

    if preference == 2:  # skip
        return False

    if preference == 1:  # auto (client explicitly requested)
        return True

    trigger = config.get("trigger", "auto")
    if trigger == "auto":
        return True
    if trigger == "manual":
        return False
    if trigger == "disabled":
        return False

    return False


def compute_symptom_fingerprint(stack_traces: list[dict]) -> str:
    """Compute a deterministic fingerprint of the failure symptom."""
    if not stack_traces:
        return "no-stack-trace"

    trace = stack_traces[-1]
    parts = [
        trace.get("exception_type", ""),
        _first_line(trace.get("message", "")),
    ]
    for frame in trace.get("stack_trace", [])[:3]:
        parts.append(_normalize_frame(frame))

    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _first_line(text: str) -> str:
    """Get the first line of a multi-line string."""
    return text.split("\n")[0].strip() if text else ""


def _normalize_frame(frame: str) -> str:
    """Normalize a stack frame by stripping file paths and line numbers."""
    # Remove file paths like "in /path/to/file.cs:line 42"
    normalized = re.sub(r'\s+in\s+\S+:\s*line\s+\d+', '', frame)
    # Remove line numbers like ":42"
    normalized = re.sub(r':\d+$', '', normalized)
    return normalized.strip()


def _compute_context_hash(fingerprint: str, commit_subjects: str, classification: str) -> str:
    """Compute a hash of the analysis context for cache invalidation."""
    content = fingerprint + commit_subjects + (classification or "")
    return hashlib.sha256(content.encode()).hexdigest()[:16]


async def build_analysis_context(run_id: str, tc_info: dict, db_instance) -> AnalysisContext:
    """Gather and compress all relevant context for a single TC failure."""
    tc_full_name = tc_info["tc_full_name"]
    tc_id = tc_info.get("tc_id", "")
    group_hash = tc_info.get("group_hash")

    context = AnalysisContext(
        run_id=run_id,
        tc_full_name=tc_full_name,
        tc_id=tc_id,
    )

    # Load run metadata
    meta = read_meta_msgpack(run_id)
    if meta:
        context.user_metadata = meta.get("user_metadata", {})

        # Load test case data for stack traces and logs
        tc_meta = meta.get("test_cases", {}).get(tc_full_name, {})
        if tc_meta:
            from .models import TestRunData
            run_data = TestRunData.from_dict(run_id, meta)
            tc_data = run_data.test_cases.get(tc_full_name)
            if tc_data:
                tc_data.load_log_from_disk()
                context.stack_traces = tc_data.stack_traces or []
                # Extract key log lines
                context.log_lines = _extract_key_logs(tc_data.logs)

    # Load commit context
    commits_file = get_run_path(run_id) / "commits.json"
    if commits_file.exists():
        try:
            with open(commits_file, 'r', encoding='utf-8') as f:
                commits_data = json.load(f)
            context.commit_context = _extract_commit_context(commits_data)
            # Extract repo link info for HTML link construction
            for diff in commits_data.get("diffs", []):
                repo_url = diff.get("url", "")
                sha = diff.get("current_sha", "")
                if repo_url:
                    context.repo_links.append({
                        "name": diff.get("name", ""),
                        "url": repo_url,
                        "sha": sha,
                    })
        except Exception as e:
            logger.warning(f"Failed to load commits for {run_id}: {e}")

    # Load test history
    try:
        history = await db_instance.get_test_case_history(tc_full_name, limit=5, group_hash=group_hash)
        context.test_history = history
        context.classification = await db_instance.classify_test_case(tc_full_name, group_hash=group_hash)
    except Exception as e:
        logger.warning(f"Failed to load test history for {tc_full_name}: {e}")

    # Compute fingerprint and context hash
    context.fingerprint = compute_symptom_fingerprint(context.stack_traces)
    commit_subjects = context.commit_context[:200] if context.commit_context else ""
    context.context_hash = _compute_context_hash(
        context.fingerprint, commit_subjects, context.classification
    )

    return context


def _extract_key_logs(logs: list, max_lines: int = 100) -> list[str]:
    """Extract key log lines from raw log entries."""
    if not logs:
        return []

    result = []

    # First 5 lines (setup context)
    for entry in logs[:5]:
        msg = entry.get("m", "") if isinstance(entry, dict) else str(entry)
        if msg:
            result.append(msg)

    # Error/warning lines
    error_lines = []
    for entry in logs:
        msg = entry.get("m", "") if isinstance(entry, dict) else str(entry)
        if msg and any(kw in msg.upper() for kw in ["ERROR", "EXCEPTION", "FAIL", "WARN"]):
            error_lines.append(msg)
    result.extend(error_lines[:20])

    # Last 50 lines (most diagnostic value)
    for entry in logs[-50:]:
        msg = entry.get("m", "") if isinstance(entry, dict) else str(entry)
        if msg:
            result.append(msg)

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for line in result:
        if line not in seen:
            seen.add(line)
            deduped.append(line)

    return deduped[:max_lines]


def _extract_commit_context(commits_data: dict) -> str:
    """Extract commit context from commits.json data."""
    lines = []
    for repo_diff in commits_data.get("diffs", []):
        lines.append(f"Repository: {repo_diff.get('name', 'unknown')}")
        for commit in repo_diff.get("commits", [])[:10]:
            lines.append(f"  - {commit.get('subject', '')} ({commit.get('author', '')})")
            for f in commit.get("files", [])[:10]:
                lines.append(f"    {f.get('change_type', '?')} {f.get('path', '')}")
    return "\n".join(lines)


async def _call_openai_tier1(client: AsyncOpenAI, context: AnalysisContext,
                              config: dict) -> AnalysisResult:
    """Call OpenAI Tier-1 model for initial fast analysis."""
    model = config.get("model_tier1", "gpt-4.1-mini")
    prompt_text = format_context_tier1(context)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_TIER1},
            {"role": "user", "content": prompt_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    content = response.choices[0].message.content
    prompt_tokens = response.usage.prompt_tokens if response.usage else 0
    completion_tokens = response.usage.completion_tokens if response.usage else 0

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning(f"Tier-1 returned invalid JSON: {content[:200]}")
        data = {"summary": content[:500], "confidence": 0.3, "category": "unknown", "references": []}

    return AnalysisResult(
        summary=data.get("summary", "Analysis failed"),
        confidence=float(data.get("confidence", 0.3)),
        category=data.get("category", "unknown"),
        summary_html=data.get("summary_html", ""),
        references=data.get("references", []),
        model_used=model,
        tier_used=1,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        token_count=prompt_tokens + completion_tokens,
    )


async def _call_openai_tier2(client: AsyncOpenAI, context: AnalysisContext,
                              tier1_result: AnalysisResult, config: dict) -> AnalysisResult:
    """Call OpenAI Tier-2 model for advanced analysis."""
    model = config.get("model_tier2", "gpt-4.1")
    prompt_text = format_context_tier2(context, tier1_result)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_TIER2},
            {"role": "user", "content": prompt_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    content = response.choices[0].message.content
    prompt_tokens = response.usage.prompt_tokens if response.usage else 0
    completion_tokens = response.usage.completion_tokens if response.usage else 0

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning(f"Tier-2 returned invalid JSON: {content[:200]}")
        data = {"summary": content[:500], "confidence": 0.3, "category": "unknown",
                "references": [], "reasoning": ""}

    return AnalysisResult(
        summary=data.get("summary", "Analysis failed"),
        confidence=float(data.get("confidence", 0.3)),
        category=data.get("category", "unknown"),
        summary_html=data.get("summary_html", ""),
        references=data.get("references", []),
        reasoning=data.get("reasoning"),
        model_used=model,
        tier_used=2,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        token_count=prompt_tokens + completion_tokens,
    )


def _resolve_references(references: list[dict], run_id: str, commits_data: dict) -> list[dict]:
    """Resolve reference links for commits and log lines."""
    resolved = []
    for ref in references:
        ref_type = ref.get("type", "")
        if ref_type == "commit":
            sha = ref.get("sha", "")
            repo = ref.get("repo", "")
            # Try to find repo URL from commits data
            url = None
            for diff in commits_data.get("diffs", []):
                if diff.get("name") == repo:
                    repo_url = diff.get("url", "")
                    if repo_url and sha:
                        url = f"{repo_url.rstrip('/')}/commit/{sha}"
                    break
            resolved.append({**ref, "url": url})

        elif ref_type == "log_line":
            ts_ms = ref.get("timestamp_ms")
            if ts_ms:
                ref["url"] = f"/testRun/{run_id}/log/.html#ts-{ts_ms}"
            resolved.append(ref)
        else:
            resolved.append(ref)

    return resolved


async def analyze_single_tc(client: AsyncOpenAI, context: AnalysisContext,
                             config: dict, db_instance) -> AnalysisResult:
    """Analyze a single test case failure with tiered AI calling."""
    effort = config.get("effort", "normal")

    if effort == "high":
        # Skip tier-1, go straight to tier-2
        result = await _call_openai_tier2(client, context,
                                           AnalysisResult(summary="", confidence=0, category="unknown"),
                                           config)
    else:
        # Tier-1 first
        result = await _call_openai_tier1(client, context, config)

        # Check if we should escalate to tier-2
        if effort == "normal" and result.confidence < 0.6:
            logger.info(f"Tier-1 confidence {result.confidence:.2f} < 0.6, escalating to Tier-2 for {context.tc_full_name}")

            # Record tier-1 usage before escalating
            month = datetime.now(UTC).strftime("%Y-%m")
            cost = _estimate_cost(result.model_used, result.prompt_tokens, result.completion_tokens)
            await db_instance.record_ai_usage(month, result.prompt_tokens, result.completion_tokens, cost)

            tier2_result = await _call_openai_tier2(client, context, result, config)
            # Combine token counts
            tier2_result.prompt_tokens += result.prompt_tokens
            tier2_result.completion_tokens += result.completion_tokens
            tier2_result.token_count = tier2_result.prompt_tokens + tier2_result.completion_tokens
            result = tier2_result

    return result


async def run_failure_analysis(run_id: str, broadcast_fn=None):
    """Main entry point: analyze all failures in a run.

    Args:
        run_id: The run to analyze
        broadcast_fn: Optional async function to broadcast UI updates
    """
    config = AI_ANALYSIS_CONFIG
    status = AnalysisRunStatus(status="running")
    _analysis_tasks[run_id] = status

    try:
        db_instance = database.db
        api_key = config.get("openai_api_key", "")
        if not api_key:
            status.status = "failed"
            status.error = "No OpenAI API key configured"
            return

        client = AsyncOpenAI(api_key=api_key)

        # Get all failed test cases
        failed_tcs = await db_instance.get_failed_test_cases_for_run(run_id)
        status.total_failures = len(failed_tcs)

        if not failed_tcs:
            status.status = "completed"
            status.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
            return

        # Apply max failures limit
        max_failures = config.get("max_failures_per_run", 20)
        if max_failures > 0 and len(failed_tcs) > max_failures:
            failed_tcs = failed_tcs[:max_failures]
            status.skipped_count = status.total_failures - max_failures

        # Load commits data (shared across all TCs)
        commits_data = {}
        commits_file = get_run_path(run_id) / "commits.json"
        if commits_file.exists():
            try:
                with open(commits_file, 'r', encoding='utf-8') as f:
                    commits_data = json.load(f)
            except Exception:
                pass

        # Analyze each failure
        for tc_info in failed_tcs:
            try:
                # Check monthly budget
                month = datetime.now(UTC).strftime("%Y-%m")
                usage = await db_instance.get_ai_usage_for_month(month)
                if usage and config.get("monthly_budget_usd", 0) > 0:
                    if usage["estimated_cost_usd"] >= config["monthly_budget_usd"]:
                        raise BudgetExceededError(
                            f"Monthly AI budget of ${config['monthly_budget_usd']:.2f} reached"
                        )
                    # Check warning threshold
                    threshold = config.get("budget_warning_threshold", 0.8)
                    if (not usage.get("warning_sent") and
                            usage["estimated_cost_usd"] >= config["monthly_budget_usd"] * threshold):
                        await db_instance.mark_budget_warning_sent(month)
                        logger.warning(f"AI budget warning: {usage['estimated_cost_usd']:.2f} / {config['monthly_budget_usd']:.2f} USD")

                # Build context
                context = await build_analysis_context(run_id, tc_info, db_instance)

                # Check dedup
                dedup_window = config.get("dedup_window_days", 30)
                existing = await db_instance.get_analysis_by_fingerprint(
                    context.fingerprint, max_age_days=dedup_window
                )

                if existing:
                    # Reuse existing analysis
                    await db_instance.link_analysis_to_test_case(
                        run_id, tc_info["tc_full_name"], existing["id"]
                    )
                    status.deduped_count += 1
                    status.analyzed_count += 1
                    logger.info(f"Deduped analysis for {tc_info['tc_full_name']} (fingerprint={context.fingerprint})")
                    continue

                # Run AI analysis
                result = await analyze_single_tc(client, context, config, db_instance)

                # Resolve references
                result.references = _resolve_references(result.references, run_id, commits_data)

                # Store result
                analysis_id = await db_instance.insert_ai_analysis(
                    fingerprint=context.fingerprint,
                    summary=result.summary,
                    references_json=json.dumps(result.references),
                    confidence=result.confidence,
                    category=result.category,
                    model_used=result.model_used,
                    tier_used=result.tier_used,
                    reasoning=result.reasoning,
                    context_hash=context.context_hash,
                    token_count=result.token_count,
                    summary_html=result.summary_html,
                )

                await db_instance.link_analysis_to_test_case(
                    run_id, tc_info["tc_full_name"], analysis_id
                )

                # Record usage
                cost = _estimate_cost(result.model_used, result.prompt_tokens, result.completion_tokens)
                await db_instance.record_ai_usage(month, result.prompt_tokens, result.completion_tokens, cost)

                status.analyzed_count += 1
                logger.info(
                    f"Analyzed {tc_info['tc_full_name']}: {result.category} "
                    f"(confidence={result.confidence:.2f}, tier={result.tier_used})"
                )

            except BudgetExceededError as e:
                logger.warning(f"Budget exceeded during analysis: {e}")
                status.error = str(e)
                break
            except Exception as e:
                logger.error(f"Error analyzing {tc_info.get('tc_full_name', '?')}: {e}")
                continue

        status.status = "completed"
        status.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        # Broadcast completion to UI
        if broadcast_fn:
            # Collect category counts
            analyses = await db_instance.get_analyses_for_run(run_id)
            categories = {}
            for a in analyses:
                cat = a.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1

            await broadcast_fn({
                "type": "analysis_completed",
                "run_id": run_id,
                "analyzed_count": status.analyzed_count,
                "deduped_count": status.deduped_count,
                "categories": categories,
            })

        # Send email if configured
        try:
            await _maybe_send_email(run_id, status)
        except Exception as e:
            logger.error(f"Error sending analysis email: {e}")

    except Exception as e:
        logger.exception(f"Fatal error in failure analysis for {run_id}")
        status.status = "failed"
        status.error = str(e)


async def _maybe_send_email(run_id: str, status: AnalysisRunStatus):
    """Send analysis email if conditions are met."""
    if not EMAIL_CONFIG.get("enabled", False):
        return

    config = AI_ANALYSIS_CONFIG
    meta = read_meta_msgpack(run_id)
    if not meta:
        return

    # Check per-run email preference
    email_pref = meta.get("ai_email_preference", 0)
    if email_pref == 2:  # suppress
        return
    if email_pref != 1 and not config.get("send_email", True):
        return

    if status.analyzed_count == 0:
        return

    # Resolve recipients
    recipients = meta.get("ai_email_to")
    if not recipients:
        # Try DB setting first, then config default
        db_recipients = await database.db.get_setting("email_recipients")
        if db_recipients:
            try:
                recipients = json.loads(db_recipients)
            except json.JSONDecodeError:
                pass
        if not recipients:
            recipients = EMAIL_CONFIG.get("to_addresses", [])

    if not recipients:
        logger.info("No email recipients configured, skipping email")
        return

    # Import here to avoid circular dependency
    from .email_sender import send_analysis_email
    await send_analysis_email(run_id, recipients)


def get_analysis_status(run_id: str) -> AnalysisRunStatus:
    """Get the current analysis status for a run."""
    return _analysis_tasks.get(run_id, AnalysisRunStatus())


# Track deep analysis tasks: "{run_id}/{tc_full_name}" -> status dict
_deep_analysis_tasks: dict[str, dict] = {}


def get_deep_analysis_status(run_id: str, tc_full_name: str) -> dict:
    """Get the status of a deep analysis task."""
    key = f"{run_id}/{tc_full_name}"
    return _deep_analysis_tasks.get(key, {"status": "not_requested"})


async def run_deep_analysis(run_id: str, tc_full_name: str):
    """Run a deep analysis for a single test case.

    This performs a high-effort analysis using the most capable model
    and stores the result as deep_html on the existing analysis record.
    """
    config = AI_ANALYSIS_CONFIG
    key = f"{run_id}/{tc_full_name}"
    _deep_analysis_tasks[key] = {"status": "running"}

    try:
        db_instance = database.db
        api_key = config.get("openai_api_key", "")
        if not api_key:
            _deep_analysis_tasks[key] = {"status": "failed", "error": "No OpenAI API key configured"}
            return

        # Get existing analysis
        existing = await db_instance.get_analysis_for_test_case(run_id, tc_full_name)
        if not existing:
            _deep_analysis_tasks[key] = {"status": "failed", "error": "No analysis found — run standard analysis first"}
            return

        analysis_id = existing["analysis_id"]

        # Get the TC info from the database
        tc_info = await db_instance.get_test_case_info(run_id, tc_full_name)
        if not tc_info:
            _deep_analysis_tasks[key] = {"status": "failed", "error": "Test case not found"}
            return

        tc_info["group_hash"] = (await db_instance.get_run_info(run_id) or {}).get("group_hash")

        # Build full context (with maximum detail for logs)
        context = await build_analysis_context(run_id, tc_info, db_instance)
        # For deep analysis, use all log lines (not truncated)
        meta = read_meta_msgpack(run_id)
        if meta:
            tc_meta = meta.get("test_cases", {}).get(tc_full_name, {})
            if tc_meta:
                from .models import TestRunData
                run_data = TestRunData.from_dict(run_id, meta)
                tc_data = run_data.test_cases.get(tc_full_name)
                if tc_data:
                    tc_data.load_log_from_disk()
                    # Use all log lines for deep analysis
                    all_lines = []
                    for entry in (tc_data.logs or []):
                        msg = entry.get("m", "") if isinstance(entry, dict) else str(entry)
                        if msg:
                            all_lines.append(msg)
                    context.log_lines = all_lines

        # Call OpenAI with deep analysis prompt
        client = AsyncOpenAI(api_key=api_key)
        model = config.get("model_tier2", "gpt-4.1")
        prompt_text = format_context_deep(context, existing)

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_DEEP},
                {"role": "user", "content": prompt_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        content = response.choices[0].message.content
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            _deep_analysis_tasks[key] = {"status": "failed", "error": "AI returned invalid JSON"}
            return

        deep_html = data.get("deep_html", "")
        if not deep_html:
            _deep_analysis_tasks[key] = {"status": "failed", "error": "AI returned empty deep analysis"}
            return

        # Update existing analysis with deep_html
        await db_instance.update_deep_analysis(analysis_id, deep_html, prompt_tokens + completion_tokens)

        # If the AI updated the summary, apply it
        new_summary = data.get("summary", "")
        new_summary_html = data.get("summary_html", "")
        if new_summary:
            async with db_instance.get_connection() as db:
                await db.execute(
                    "UPDATE ai_analyses SET summary = ?, summary_html = ?, confidence = ?, category = ? WHERE id = ?",
                    (new_summary, new_summary_html,
                     float(data.get("confidence", existing.get("confidence", 0))),
                     data.get("category", existing.get("category", "unknown")),
                     analysis_id)
                )
                await db.commit()

        # Record usage
        month = datetime.now(UTC).strftime("%Y-%m")
        cost = _estimate_cost(model, prompt_tokens, completion_tokens)
        await db_instance.record_ai_usage(month, prompt_tokens, completion_tokens, cost)

        _deep_analysis_tasks[key] = {"status": "completed"}
        logger.info(f"Deep analysis completed for {tc_full_name} in run {run_id}")

    except Exception as e:
        logger.exception(f"Error in deep analysis for {tc_full_name} in run {run_id}")
        _deep_analysis_tasks[key] = {"status": "failed", "error": str(e)}
