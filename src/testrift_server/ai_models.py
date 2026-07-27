"""
Data models for AI failure analysis.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AnalysisContext:
    """Context bundle gathered for a single test case failure."""
    run_id: str
    tc_full_name: str
    tc_id: str
    stack_traces: list[dict] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    test_history: list[dict] = field(default_factory=list)
    classification: Optional[str] = None  # regression, flaky, new, etc.
    commit_context: str = ""
    user_metadata: dict = field(default_factory=dict)
    fingerprint: str = ""
    context_hash: str = ""
    repo_links: list[dict] = field(default_factory=list)  # [{name, url, sha}]


@dataclass
class AnalysisResult:
    """Result from AI analysis of a single test case failure."""
    summary: str
    confidence: float
    category: str  # code_bug, test_bug, environment, flaky, infrastructure, unknown
    summary_html: str = ""
    references: list[dict] = field(default_factory=list)
    reasoning: Optional[str] = None
    deep_html: Optional[str] = None
    model_used: str = ""
    tier_used: int = 1
    token_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    is_deduped: bool = False
    analysis_id: Optional[int] = None


@dataclass
class AnalysisRunStatus:
    """Status of an analysis run."""
    status: str = "not_requested"  # pending, running, completed, failed, not_requested
    analyzed_count: int = 0
    deduped_count: int = 0
    skipped_count: int = 0
    total_failures: int = 0
    error: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class CollectionReportContext:
    """Persisted deterministic input to a Collection-level AI report."""
    collection_id: int
    profile_id: int
    requested_at: str
    selections: list[dict]
    sources: dict[str, dict]
    failure_clusters: list[dict]


class BudgetExceededError(Exception):
    """Raised when monthly AI budget is exceeded."""
    pass
