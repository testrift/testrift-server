from datetime import datetime, timedelta, timezone

from testrift_server.summary_profiles import (
    RunCandidate,
    SourceSelector,
    SummaryProfile,
    select_representative_runs,
)

UTC = timezone.utc
REQUESTED_AT = datetime(2026, 1, 2, 12, tzinfo=UTC)


def make_run(
    run_id,
    target_key="nora-b26x",
    purpose="nightly",
    end_time=REQUESTED_AT - timedelta(hours=1),
    firmware_branch="main",
    test_system_branch="development",
):
    return RunCandidate(
        run_id=run_id,
        target_key=target_key,
        purpose=purpose,
        status="finished",
        end_time=end_time,
        sources={
            "firmware": {"branch": firmware_branch},
            "test-system": {"branch": test_system_branch},
        },
    )


def profile(selectors=None):
    return SummaryProfile(
        purpose="nightly",
        window_hours=24,
        selectors=tuple(
            selectors
            or (
                SourceSelector("firmware", "main"),
                SourceSelector("test-system", "development"),
            )
        ),
    )


def test_newer_feature_and_manual_runs_are_excluded():
    selected = select_representative_runs(
        profile(),
        ["nora-b26x"],
        [
            make_run("nightly"),
            make_run("feature", purpose="feature", end_time=REQUESTED_AT - timedelta(minutes=5), firmware_branch="feature/x"),
            make_run("manual", purpose="manual", end_time=REQUESTED_AT - timedelta(minutes=1)),
        ],
        REQUESTED_AT,
    )
    assert selected[0].run_id == "nightly"


def test_missing_or_mismatched_sources_do_not_fallback():
    missing_source = make_run("missing")
    missing_source = RunCandidate(
        missing_source.run_id,
        missing_source.target_key,
        missing_source.purpose,
        missing_source.status,
        missing_source.end_time,
        {"firmware": {"branch": "main"}},
    )
    mismatch = make_run("mismatch", test_system_branch="release")
    selected = select_representative_runs(profile(), ["nora-b26x"], [missing_source, mismatch], REQUESTED_AT)
    assert selected == [type(selected[0])("nora-b26x", None, "no_matching_run")]


def test_target_override_wins_and_ties_are_deterministic():
    selected = select_representative_runs(
        profile((SourceSelector("firmware", "main"), SourceSelector("firmware", "release", "nora-b26x"))),
        ["nora-b26x"],
        [
            make_run("main", firmware_branch="main"),
            make_run("run-a", firmware_branch="release"),
            make_run("run-z", firmware_branch="release"),
        ],
        REQUESTED_AT,
    )
    assert selected[0].run_id == "run-z"


def test_stale_runs_and_overlapping_collections_remain_deterministic():
    stale = make_run("stale", end_time=REQUESTED_AT - timedelta(hours=25))
    current = make_run("current", end_time=REQUESTED_AT - timedelta(hours=2))
    short_window = SummaryProfile("nightly", 1, profile().selectors)
    assert select_representative_runs(short_window, ["nora-b26x"], [stale, current], REQUESTED_AT)[0].reason == "no_matching_run"
    assert select_representative_runs(profile(), ["nora-b26x"], [stale, current], REQUESTED_AT)[0].run_id == "current"
