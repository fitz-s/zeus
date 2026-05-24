# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: OpenData ownership singleton + behavior-preserving ingest_main delegation.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR4);
#   operator spec §"OpenData singleton enforcement"; config/source_release_calendar.yaml.
"""PR4: OpenData live-ownership singleton + behavior-preserving ingest_main delegation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_active_opendata_owner_resolves_singleton() -> None:
    from src.data.source_job_registry import active_opendata_owner

    assert active_opendata_owner("forecast_live") == "forecast_live_daemon"
    assert active_opendata_owner("ingest_main") == "ingest_main"
    assert active_opendata_owner("") == "ingest_main"          # default
    assert active_opendata_owner("FORECAST_LIVE") == "forecast_live_daemon"  # case-insensitive


def test_each_env_has_exactly_one_owner_with_jobs() -> None:
    from src.data.source_job_registry import active_opendata_jobs, assert_opendata_singleton

    for env in ("ingest_main", "forecast_live"):
        owner = assert_opendata_singleton(env)          # raises if owner has no jobs
        jobs = active_opendata_jobs(env)
        assert jobs, f"env={env} owner={owner} must have ≥1 active OpenData job"
        assert {j.owner_daemon for j in jobs} == {owner}  # only the singleton owner's jobs


def test_ingest_main_delegation_is_behavior_preserving(monkeypatch) -> None:
    """ingest_main._ingest_main_owns_opendata must equal the registry authority for every env,
    and match the ORIGINAL boolean (owner != 'forecast_live')."""
    import src.ingest_main as im
    from src.data.source_job_registry import active_opendata_owner

    for env_val, expected in [("ingest_main", True), ("forecast_live", False), ("", True), ("other", True)]:
        monkeypatch.setenv("ZEUS_FORECAST_LIVE_OWNER", env_val)
        got = im._ingest_main_owns_opendata()
        assert got == expected
        # equals the registry authority
        assert got == (active_opendata_owner(im._forecast_live_owner()) == "ingest_main")
        # equals the original semantics
        assert got == (im._forecast_live_owner() != "forecast_live")


def test_opendata_safe_fetch_is_485min_not_legacy_0730() -> None:
    """Calendar safe_fetch = 485min after cycle (00Z -> 08:05Z), NOT the stale 07:30 comment."""
    from src.data.source_time import load_temporal_policy

    p = load_temporal_policy("ecmwf_open_data_mx2t6_high")
    assert p.safe_fetch_lag_minutes == 485
    issue_00z = datetime(2026, 5, 24, 0, 0, tzinfo=timezone.utc)
    assert p.safe_fetch_not_before(issue_00z) == issue_00z + timedelta(minutes=485)  # 08:05Z
    assert p.safe_fetch_not_before(issue_00z) > issue_00z.replace(hour=7, minute=30)  # past legacy 07:30
