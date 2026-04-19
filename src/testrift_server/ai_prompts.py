"""
AI prompt templates for failure analysis.
"""

SYSTEM_PROMPT_TIER1 = """You are a test failure analyst. Given a failed test case with its stack trace, \
recent logs, code changes, and test history, determine the most likely root cause.

Output JSON with these fields:
{
  "summary": "Single sentence (max ~120 chars) plain-text summary of the failure cause",
  "summary_html": "Same summary but with inline HTML: <code> for identifiers/files, <a href> for links",
  "confidence": 0.0-1.0,
  "category": "code_bug|test_bug|environment|flaky|infrastructure|unknown",
  "references": [
    {"type": "commit", "sha": "...", "repo": "...", "description": "..."},
    {"type": "log_line", "timestamp_ms": 12345, "message": "..."}
  ]
}

HTML formatting rules for summary_html:
- Wrap exception types, class names, method names, and file names in <code>
- If LINK_INFO is provided below, construct <a href> links:
  - Source files: <a href="{repo_url}/blob/{sha}/{path}#L{line}">filename:line</a>
  - Commits: <a href="{repo_url}/commit/{sha}">sha_short</a>
- Keep it a single sentence — this is displayed in a summary table
- Use only inline HTML (no block elements, no <p>, no <div>)

Guidelines:
- If the stack trace points to a specific code location AND recent commits modified that code, \
call it a likely regression with high confidence.
- If the test has a history of intermittent failures with the same symptom, classify as flaky.
- If the failure is in test setup/teardown or infrastructure (DB, network), classify as infrastructure/environment.
- Be specific: mention the exception type, the method, and the suspect commit if applicable.
- Keep confidence below 0.5 if the evidence is ambiguous.
- In the references array, include any commits or log lines you specifically mention in the summary.
- Only output valid JSON, no other text."""

SYSTEM_PROMPT_TIER2 = """You are a senior test failure analyst. You are given the Tier-1 analysis as a starting point. \
Evaluate it critically. You have access to more detailed logs and commit data.

Output JSON with these fields:
{
  "summary": "Single sentence (max ~120 chars) plain-text summary of the failure cause",
  "summary_html": "Same summary but with inline HTML: <code> for identifiers/files, <a href> for links",
  "confidence": 0.0-1.0,
  "category": "code_bug|test_bug|environment|flaky|infrastructure|unknown",
  "reasoning": "Step-by-step explanation of your analysis",
  "references": [
    {"type": "commit", "sha": "...", "repo": "...", "description": "..."},
    {"type": "log_line", "timestamp_ms": 12345, "message": "..."}
  ]
}

HTML formatting rules for summary_html:
- Wrap exception types, class names, method names, and file names in <code>
- If LINK_INFO is provided below, construct <a href> links:
  - Source files: <a href="{repo_url}/blob/{sha}/{path}#L{line}">filename:line</a>
  - Commits: <a href="{repo_url}/commit/{sha}">sha_short</a>
- Keep it a single sentence — this is displayed in a summary table
- Use only inline HTML (no block elements, no <p>, no <div>)

Guidelines:
- If the Tier-1 summary is accurate, confirm it and optionally refine the wording.
- If it's wrong or incomplete, provide a corrected analysis.
- Use the additional log detail and commit information to provide more precise diagnosis.
- Be specific: mention the exception type, the method, and the suspect commit if applicable.
- In the references array, include any commits or log lines you specifically mention in the summary.
- Only output valid JSON, no other text."""

SYSTEM_PROMPT_DEEP = """You are an expert test failure analyst performing a thorough root cause investigation. \
You have full logs, stack traces, commit history, and test history available.

Your task is to produce a detailed, well-structured HTML analysis that will help an engineer fix the issue.

Output JSON with these fields:
{
  "deep_html": "<HTML analysis — see formatting rules below>",
  "summary": "Updated single-sentence plain-text summary if the original was inaccurate, or empty string to keep it",
  "summary_html": "Updated HTML summary if changed, or empty string to keep it",
  "confidence": 0.0-1.0,
  "category": "code_bug|test_bug|environment|flaky|infrastructure|unknown"
}

HTML formatting rules for deep_html:
- Structure with <h4> for sections (e.g., Root Cause, Evidence, Affected Code, Recommendation)
- Use <code> for identifiers, file names, exception types, variable names
- Use <pre> for multi-line code snippets or stack trace excerpts
- Construct <a href> links using LINK_INFO provided below:
  - Source files: <a href="{repo_url}/blob/{sha}/{path}#L{line}">path/file.cs:line</a>
  - Commits: <a href="{repo_url}/commit/{sha}">sha_short — description</a>
  - Test case log: <a href="{tc_log_url}">View test log</a>
- Use <ul>/<ol> for lists of evidence or steps
- Use <strong> for emphasis on key findings
- Be thorough: trace the failure from symptom to root cause with evidence at each step
- If you can identify the exact commit that introduced the regression, highlight it prominently
- If the failure is flaky, explain the timing/race condition and suggest a fix
- Only output valid JSON, no other text."""


def _format_link_info(context) -> str:
    """Format link construction info for the AI to build HTML links."""
    if not context.repo_links:
        return ""

    parts = ["\n## LINK_INFO (use for constructing <a href> links)"]
    for repo in context.repo_links:
        name = repo.get("name", "")
        url = repo.get("url", "")
        sha = repo.get("sha", "")
        if url and sha:
            parts.append(f"Repository '{name}': url={url} sha={sha}")
            parts.append(f"  File link pattern: {url}/blob/{sha}/{{path}}#L{{line}}")
            parts.append(f"  Commit link pattern: {url}/commit/{{sha}}")

    if context.run_id and context.tc_id:
        tc_log_url = f"/testRun/{context.run_id}/log/{context.tc_id}.html"
        parts.append(f"Test case log: {tc_log_url}")

    return "\n".join(parts)


def format_context_tier1(context) -> str:
    """Format an AnalysisContext into a compact prompt for Tier-1."""
    parts = []

    parts.append(f"## Test Case: {context.tc_full_name}")

    if context.classification:
        parts.append(f"Classification: {context.classification}")

    if context.stack_traces:
        parts.append("\n## Stack Traces")
        for i, trace in enumerate(context.stack_traces[-3:]):  # Last 3
            exc_type = trace.get("exception_type", "Unknown")
            message = trace.get("message", "")
            parts.append(f"Exception {i+1}: {exc_type}: {message}")
            stack = trace.get("stack_trace", [])
            for line in stack[:10]:  # First 10 frames
                parts.append(f"  {line}")

    if context.log_lines:
        parts.append("\n## Recent Log Lines")
        for line in context.log_lines[-50:]:  # Last 50 lines
            parts.append(line)

    if context.test_history:
        parts.append("\n## Test History (recent runs)")
        for h in context.test_history[:5]:
            parts.append(f"  {h.get('status', '?')} in run '{h.get('run_name', '?')}' ({h.get('start_time', '?')[:10]})")

    if context.commit_context:
        parts.append("\n## Recent Code Changes")
        parts.append(context.commit_context)

    if context.user_metadata:
        parts.append("\n## Run Metadata")
        for k, v in context.user_metadata.items():
            val = v.get("value", v) if isinstance(v, dict) else v
            parts.append(f"  {k}: {val}")

    link_info = _format_link_info(context)
    if link_info:
        parts.append(link_info)

    return "\n".join(parts)


def format_context_tier2(context, tier1_result) -> str:
    """Format an AnalysisContext with expanded detail for Tier-2, including Tier-1 result."""
    parts = []

    parts.append("## Tier-1 Analysis (evaluate critically)")
    parts.append(f"Summary: {tier1_result.summary}")
    parts.append(f"Confidence: {tier1_result.confidence}")
    parts.append(f"Category: {tier1_result.category}")
    parts.append("")

    # Reuse tier1 format but with more log lines
    parts.append(f"## Test Case: {context.tc_full_name}")

    if context.classification:
        parts.append(f"Classification: {context.classification}")

    if context.stack_traces:
        parts.append("\n## Stack Traces (full)")
        for i, trace in enumerate(context.stack_traces):
            exc_type = trace.get("exception_type", "Unknown")
            message = trace.get("message", "")
            parts.append(f"Exception {i+1}: {exc_type}: {message}")
            stack = trace.get("stack_trace", [])
            for line in stack:  # Full stack
                parts.append(f"  {line}")

    if context.log_lines:
        parts.append("\n## Log Lines (expanded)")
        for line in context.log_lines[-100:]:  # More lines for tier-2
            parts.append(line)

    if context.test_history:
        parts.append("\n## Test History (recent runs)")
        for h in context.test_history[:5]:
            parts.append(f"  {h.get('status', '?')} in run '{h.get('run_name', '?')}' ({h.get('start_time', '?')[:10]})")

    if context.commit_context:
        parts.append("\n## Recent Code Changes (detailed)")
        parts.append(context.commit_context)

    if context.user_metadata:
        parts.append("\n## Run Metadata")
        for k, v in context.user_metadata.items():
            val = v.get("value", v) if isinstance(v, dict) else v
            parts.append(f"  {k}: {val}")

    link_info = _format_link_info(context)
    if link_info:
        parts.append(link_info)

    return "\n".join(parts)


def format_context_deep(context, existing_result) -> str:
    """Format maximum-detail context for deep analysis."""
    parts = []

    if existing_result:
        parts.append("## Previous Analysis (evaluate and improve)")
        parts.append(f"Summary: {existing_result.get('summary', '')}")
        parts.append(f"Category: {existing_result.get('category', '')}")
        parts.append(f"Confidence: {existing_result.get('confidence', '')}")
        if existing_result.get("reasoning"):
            parts.append(f"Reasoning: {existing_result['reasoning']}")
        parts.append("")

    parts.append(f"## Test Case: {context.tc_full_name}")

    if context.classification:
        parts.append(f"Classification: {context.classification}")

    if context.stack_traces:
        parts.append("\n## Stack Traces (full)")
        for i, trace in enumerate(context.stack_traces):
            exc_type = trace.get("exception_type", "Unknown")
            message = trace.get("message", "")
            parts.append(f"Exception {i+1}: {exc_type}: {message}")
            stack = trace.get("stack_trace", [])
            for line in stack:  # Full stack
                parts.append(f"  {line}")

    if context.log_lines:
        parts.append("\n## Log Lines (full)")
        for line in context.log_lines:  # All available lines
            parts.append(line)

    if context.test_history:
        parts.append("\n## Test History (recent runs)")
        for h in context.test_history[:10]:
            parts.append(f"  {h.get('status', '?')} in run '{h.get('run_name', '?')}' ({h.get('start_time', '?')[:10]})")

    if context.commit_context:
        parts.append("\n## Recent Code Changes (detailed)")
        parts.append(context.commit_context)

    if context.user_metadata:
        parts.append("\n## Run Metadata")
        for k, v in context.user_metadata.items():
            val = v.get("value", v) if isinstance(v, dict) else v
            parts.append(f"  {k}: {val}")

    link_info = _format_link_info(context)
    if link_info:
        parts.append(link_info)

    return "\n".join(parts)
