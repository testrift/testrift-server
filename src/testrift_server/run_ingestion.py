"""Shared Target-context ingestion for direct and prepared TestRift Runs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from .database import TestResultsDatabase, TestRunData

UTC = timezone.utc


@dataclass(frozen=True)
class IngestedRun:
    """The server-owned Run context persisted from one normalized client message."""

    run_id: str
    target_key: str
    target_setup_state: str
    purpose: str
    parent_run_id: str | None
    sources: Dict[str, Dict[str, Any]]
    collection_keys: list[str]

    @property
    def target_url(self) -> str:
        return f"/targets/{self.target_key}"

    @property
    def collection_urls(self) -> list[str]:
        return [f"/collections/{collection_key}" for collection_key in self.collection_keys]


async def ingest_run_context(
    database: TestResultsDatabase,
    *,
    run_id: str,
    context: Dict[str, Any],
    status: str,
    retention_days: int | None,
    local_run: bool,
    user_metadata: Dict[str, Any],
    run_name: str | None,
    start_time: str | None = None,
) -> IngestedRun:
    """Persist the normalized Target context used by both Run entry paths."""
    target = await database.get_or_create_target(context["target_key"])
    timestamp = start_time or datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
    database_run = TestRunData(
        run_id=run_id,
        status=status,
        start_time=timestamp,
        end_time=None,
        retention_days=retention_days,
        local_run=local_run,
        run_name=run_name,
        target_key=target["key"],
        purpose=context["purpose"],
        parent_run_id=context.get("parent_run_id"),
    )
    if not await database.insert_test_run(database_run, user_metadata, context["sources"]):
        raise RuntimeError(f"Unable to persist Run {run_id}")

    return IngestedRun(
        run_id=run_id,
        target_key=target["key"],
        target_setup_state=target["setup_state"],
        purpose=context["purpose"],
        parent_run_id=context.get("parent_run_id"),
        sources=context["sources"],
        collection_keys=await database.get_collection_keys_for_target(target["key"]),
    )
