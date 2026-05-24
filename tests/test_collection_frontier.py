# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Frontier tests against REAL source_run/source_run_coverage shapes — PR review #329
#   F1/F2 (track join, NOT_RELEASED) + R2-A (short-horizon not live) + R2-B (coverage aggregate, no false OK).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + collection_frontier before relying on it.
"""Relationship tests for the in-memory collection frontier (PR2 + #329 R1/R2 fixes).

Fixtures use REAL write shapes: source_run track='mx2t6_high_full_horizon',
release_calendar_key='ecmwf_open_data:mx2t6_high:full', target_local_date=NULL; per-target
readiness in source_run_coverage. OK requires non-empty coverage with zero blocked AND a
full-horizon (not 06/18 short) latest cycle.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

_SR_COLS = [
    "source_id", "track", "release_calendar_key", "source_issue_time", "source_release_time",
    "source_available_at", "fetch_started_at", "captured_at", "imported_at",
    "target_local_date", "completeness_status", "status", "recorded_at",
]
_COV_COLS = ["source_id", "track", "release_calendar_key", "target_local_date",
             "readiness_status", "completeness_status", "expires_at"]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(f"CREATE TABLE source_run ({' TEXT, '.join(_SR_COLS)} TEXT)")
    c.execute(f"CREATE TABLE source_run_coverage ({' TEXT, '.join(_COV_COLS)} TEXT)")
    c.execute(
        "CREATE TABLE readiness_state (source_id TEXT, track TEXT, target_local_date TEXT, "
        "status TEXT, expires_at TEXT, computed_at TEXT)"
    )
    return c


def _insert_run(c: sqlite3.Connection, **kw: object) -> None:
    c.execute(
        f"INSERT INTO source_run ({', '.join(_SR_COLS)}) VALUES ({', '.join('?' for _ in _SR_COLS)})",
        tuple(kw.get(col) for col in _SR_COLS),
    )


def _insert_cov(c: sqlite3.Connection, target: str, readiness: str) -> None:
    c.execute(
        f"INSERT INTO source_run_coverage ({', '.join(_COV_COLS)}) VALUES ({', '.join('?' for _ in _COV_COLS)})",
        ("ecmwf_open_data", "mx2t6_high_full_horizon", "ecmwf_open_data:mx2t6_high:full",
         target, readiness, "COMPLETE", None),
    )


_REAL_HIGH = dict(
    source_id="ecmwf_open_data", track="mx2t6_high_full_horizon",
    release_calendar_key="ecmwf_open_data:mx2t6_high:full",
)
_SHORT_HIGH = dict(
    source_id="ecmwf_open_data", track="mx2t6_high_short_horizon",
    release_calendar_key="ecmwf_open_data:mx2t6_high:short",
)


@pytest.fixture(autouse=True)
def _no_health(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.data.collection_frontier as cf
    monkeypatch.setattr(cf, "_load_health", lambda: {})


def _frontier(c, now):
    from src.data.collection_frontier import compute_frontier
    return {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}


def test_frontier_finds_real_horizon_expanded_opendata_track() -> None:
    """F1: real OpenData source_run (horizon-expanded track, NULL target_local_date) is FOUND
    for calendar track 'mx2t6_high' — not UNKNOWN_BLOCKED."""
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=issue.isoformat(), captured_at=now.isoformat(),
                target_local_date=None, completeness_status="COMPLETE", status="SUCCESS",
                recorded_at=now.isoformat())
    high = _frontier(c, now)["mx2t6_high"]
    assert high.live_blocker != "UNKNOWN_BLOCKED"
    assert high.source_issue_time == issue


def test_skipped_not_released_after_safe_fetch_is_not_ok() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=(now - timedelta(hours=9)).isoformat(),
                captured_at=now.isoformat(), target_local_date=None,
                completeness_status="NOT_RELEASED", status="SKIPPED_NOT_RELEASED",
                recorded_at=now.isoformat())
    assert _frontier(c, now)["mx2t6_high"].live_blocker != "OK"


def test_06z_short_horizon_latest_is_not_live_ok() -> None:
    """R2-A: a 06/18 short-horizon cycle (live_authorization=false per calendar cycle_profile),
    even when it is the LATEST row, must NOT be reported OK."""
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    # older full + newer short, BOTH past the 485min safe-fetch so the short is the live latest:
    _insert_run(c, **_REAL_HIGH, source_issue_time=(now - timedelta(hours=15)).isoformat(),
                captured_at=now.isoformat(), target_local_date=None,
                completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat())
    _insert_run(c, **_SHORT_HIGH, source_issue_time=(now - timedelta(hours=9)).isoformat(),
                captured_at=now.isoformat(), target_local_date=None,
                completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat())
    high = _frontier(c, now)["mx2t6_high"]
    assert high.live_blocker == "SHORT_HORIZON_ONLY"


def test_complete_run_without_coverage_is_coverage_unknown_not_ok() -> None:
    """R2-B: a complete fresh full-horizon run with NO per-target coverage rows must be
    COVERAGE_UNKNOWN, never OK (a single readiness row cannot prove all targets ready)."""
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=issue.isoformat(), captured_at=now.isoformat(),
                target_local_date=None, completeness_status="COMPLETE", status="SUCCESS",
                recorded_at=now.isoformat())
    assert _frontier(c, now)["mx2t6_high"].live_blocker == "COVERAGE_UNKNOWN"


def test_any_target_blocked_in_coverage_is_not_ok() -> None:
    """R2-B: if any target's coverage readiness is blocked, the frontier must NOT be OK."""
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=issue.isoformat(), captured_at=now.isoformat(),
                target_local_date=None, completeness_status="COMPLETE", status="SUCCESS",
                recorded_at=now.isoformat())
    _insert_cov(c, "2026-05-25", "LIVE_ELIGIBLE")
    _insert_cov(c, "2026-05-26", "BLOCKED")
    assert _frontier(c, now)["mx2t6_high"].live_blocker != "OK"


def test_full_coverage_all_ready_is_ok() -> None:
    """OK only when full-horizon + fresh + all coverage targets ready."""
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=issue.isoformat(), captured_at=now.isoformat(),
                target_local_date=None, completeness_status="COMPLETE", status="SUCCESS",
                recorded_at=now.isoformat())
    _insert_cov(c, "2026-05-25", "LIVE_ELIGIBLE")
    _insert_cov(c, "2026-05-26", "LIVE_ELIGIBLE")
    high = _frontier(c, now)["mx2t6_high"]
    assert high.freshness_state == "CURRENT"
    assert high.live_blocker == "OK"


def test_backfill_write_time_cannot_fake_freshness() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=(now - timedelta(hours=40)).isoformat(),
                captured_at=now.isoformat(), target_local_date=None,
                completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat())
    high = _frontier(c, now)["mx2t6_high"]
    assert high.freshness_state == "EXPIRED" and high.live_blocker == "STALE_SOURCE"


def test_missing_source_run_is_unknown_blocked() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    assert _frontier(_conn(), now)["mn2t6_low"].live_blocker == "UNKNOWN_BLOCKED"


def test_not_released_before_safe_fetch() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    _insert_run(c, **_REAL_HIGH, source_issue_time=(now - timedelta(minutes=10)).isoformat(),
                captured_at=now.isoformat(), target_local_date=None,
                completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat())
    assert _frontier(c, now)["mx2t6_high"].live_blocker == "NOT_RELEASED"


def test_backfill_source_is_not_live_authorized() -> None:
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    rows = compute_frontier(conn=_conn(), now=now, role_filter="backfill")
    assert rows and all(r.live_blocker == "NOT_LIVE_AUTHORIZED" for r in rows)
    assert any(r.source_id == "tigge" for r in rows)
