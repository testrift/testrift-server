"""Deterministic Collection Summary profile selection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable

UTC = timezone.utc


@dataclass(frozen=True)
class SourceSelector:
    source_role: str
    branch: str
    target_key: str | None = None


@dataclass(frozen=True)
class SummaryProfile:
    purpose: str
    window_hours: int
    selectors: tuple[SourceSelector, ...]


@dataclass(frozen=True)
class RunCandidate:
    run_id: str
    target_key: str
    purpose: str
    status: str
    end_time: datetime | None
    sources: Dict[str, Dict[str, Any]]


@dataclass(frozen=True)
class SummarySelection:
    target_key: str
    run_id: str | None
    reason: str | None


def select_representative_runs(
    profile: SummaryProfile,
    target_keys: Iterable[str],
    candidates: Iterable[RunCandidate],
    requested_at: datetime,
) -> list[SummarySelection]:
    """Choose one eligible finished Run per Target without fallback selection."""
    requested_at = _as_utc(requested_at)
    cutoff = requested_at - timedelta(hours=profile.window_hours)
    candidates_by_target: dict[str, list[RunCandidate]] = {}
    for candidate in candidates:
        candidates_by_target.setdefault(candidate.target_key, []).append(candidate)

    selections: list[SummarySelection] = []
    for target_key in target_keys:
        required_sources = _selectors_for_target(profile.selectors, target_key)
        eligible = [
            candidate
            for candidate in candidates_by_target.get(target_key, [])
            if _is_eligible(candidate, profile.purpose, required_sources, cutoff, requested_at)
        ]
        if not eligible:
            selections.append(SummarySelection(target_key, None, "no_matching_run"))
            continue
        selected = max(eligible, key=lambda candidate: (candidate.end_time, candidate.run_id))
        selections.append(SummarySelection(target_key, selected.run_id, None))
    return selections


async def select_profile_from_database(database: Any, profile_id: int, requested_at: datetime) -> list[SummarySelection]:
    """Load persisted profile inputs and select one eligible Run per member Target."""
    inputs = await database.get_summary_profile_selection_inputs(profile_id)
    profile = SummaryProfile(
        purpose=inputs["profile"]["purpose"],
        window_hours=inputs["profile"]["window_hours"],
        selectors=tuple(SourceSelector(**selector) for selector in inputs["selectors"]),
    )
    candidates = [
        RunCandidate(
            run_id=run["run_id"],
            target_key=run["target_key"],
            purpose=run["purpose"],
            status=run["status"],
            end_time=datetime.fromisoformat(run["end_time"].replace("Z", "+00:00")) if run["end_time"] else None,
            sources=run["sources"],
        )
        for run in inputs["runs"]
    ]
    return select_representative_runs(profile, inputs["target_keys"], candidates, requested_at)


async def resolve_run_set(
    database: Any,
    *,
    target_key: str | None = None,
    collection_key: str | None = None,
    profile_id: int | None = None,
    requested_at: datetime | None = None,
) -> dict[str, Any]:
    """Resolve the sole Run set consumed by Target and Collection views."""
    if bool(target_key) == bool(collection_key):
        raise ValueError("Specify exactly one of target or collection")
    if target_key:
        target = await database.get_target(target_key)
        if not target:
            raise ValueError("Target not found")
        runs = await database.get_test_runs(limit=1000, target_key=target_key)
        return {"context": "target", "target_key": target_key, "run_ids": [run["run_id"] for run in runs], "missing_targets": []}

    collection = await database.get_collection(collection_key)
    if not collection:
        raise ValueError("Collection not found")
    profile_id = profile_id or next((profile["id"] for profile in collection["profiles"] if profile["is_primary"]), None)
    if not profile_id or profile_id not in {profile["id"] for profile in collection["profiles"]}:
        raise ValueError("A Collection Summary profile is required")
    requested_at = _as_utc(requested_at or datetime.now(UTC))
    selections = await select_profile_from_database(database, profile_id, requested_at)
    return {
        "context": "collection",
        "collection_key": collection_key,
        "profile_id": profile_id,
        "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
        "run_ids": [selection.run_id for selection in selections if selection.run_id],
        "missing_targets": [selection.target_key for selection in selections if not selection.run_id],
    }


def _selectors_for_target(
    selectors: tuple[SourceSelector, ...], target_key: str) -> dict[str, str]:
    defaults = {selector.source_role: selector.branch for selector in selectors if selector.target_key is None}
    overrides = {
        selector.source_role: selector.branch
        for selector in selectors
        if selector.target_key == target_key
    }
    return {**defaults, **overrides}


def _is_eligible(
    candidate: RunCandidate,
    purpose: str,
    required_sources: dict[str, str],
    cutoff: datetime,
    requested_at: datetime,
) -> bool:
    if candidate.status != "finished" or candidate.purpose != purpose or candidate.end_time is None:
        return False
    completed_at = _as_utc(candidate.end_time)
    if completed_at < cutoff or completed_at > requested_at:
        return False
    return all(
        candidate.sources.get(source_role, {}).get("branch") == branch
        for source_role, branch in required_sources.items()
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
