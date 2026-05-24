# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Frontier tests against REAL source_run shapes (horizon-expanded track,
#   release_calendar_key, NULL target_local_date, NOT_RELEASED status) — PR review #329 F1/F2.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + collection_frontier before relying on it.
"""Relationship tests for the in-memory collection frontier (PR2 + #329 fixes).

Fixtures use the REAL source_run write shape (ecmwf_open_data writes track=
'mx2t6_high_full_horizon', release_calendar_key='ecmwf_open_data:mx2t6_high:full',
target_local_date=NULL). The prior fixtures used track='mx2t6_high' with target_local_date
set — which masked F1 (track mismatch) and F2 (NOT_RELEASED misclassification).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

# Real source_run columns the frontier reads.
_SR_COLS = [
    "source_id", "track", "release_calendar_key", "source_issue_time", "source_release_time",
    "source_available_at", "fetch_started_at", "captured_at", "imported_at",
    "target_local_date", "completeness_status", "status", "recorded_at",
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(f"CREATE TABLE source_run ({' TEXT, '.join(_SR_COLS)} TEXT)")
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


# Real OpenData HIGH write identity (NOT the bare calendar track 'mx2t6_high').
_REAL_HIGH = dict(
    source_id="ecmwf_open_data",
    track="mx2t6_high_full_horizon",
    release_calendar_key="ecmwf_open_data:mx2t6_high:full",
)


@pytest.fixture(autouse=True)
def _no_health(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.data.collection_frontier as cf
    monkeypatch.setattr(cf, "_load_health", lambda: {})


def test_frontier_finds_real_horizon_expanded_opendata_track() -> None:
    """F1: a real OpenData source_run (track=mx2t6_high_full_horizon, NULL target_local_date)
    must be FOUND for calendar entry track='mx2t6_high' — not reported UNKNOWN_BLOCKED."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)  # past 485min safe-fetch, within freshness
    c = _conn()
    _insert_run(
        c, **_REAL_HIGH,
        source_issue_time=issue.isoformat(),
        source_available_at=(issue + timedelta(hours=8)).isoformat(),
        captured_at=now.isoformat(), target_local_date=None,   # REAL: source-level row has none
        completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat(),
    )
    rows = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}
    high = rows["mx2t6_high"]            # frontier keyed by calendar track
    assert high.live_blocker != "UNKNOWN_BLOCKED"   # the F1 bug would give UNKNOWN_BLOCKED
    assert high.source_issue_time == issue          # it resolved the real row
    assert high.freshness_state == "CURRENT"


def test_skipped_not_released_after_safe_fetch_is_not_ok() -> None:
    """F2: a source_run that says NOT_RELEASED, observed past safe-fetch, must NOT be OK."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)  # past safe-fetch
    c = _conn()
    _insert_run(
        c, **_REAL_HIGH,
        source_issue_time=issue.isoformat(), captured_at=now.isoformat(),
        target_local_date=None, completeness_status="NOT_RELEASED",
        status="SKIPPED_NOT_RELEASED", recorded_at=now.isoformat(),
    )
    high = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}["mx2t6_high"]
    assert high.live_blocker != "OK"                # the F2 bug returned OK


def test_backfill_write_time_cannot_fake_freshness() -> None:
    """Old cycle (issue 40h ago) with fresh captured_at must report EXPIRED + STALE."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    _insert_run(
        c, **_REAL_HIGH,
        source_issue_time=(now - timedelta(hours=40)).isoformat(),  # > 30h ceiling
        captured_at=now.isoformat(), target_local_date=None,
        completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat(),
    )
    high = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}["mx2t6_high"]
    assert high.freshness_state == "EXPIRED"
    assert high.live_blocker == "STALE_SOURCE"


def test_missing_source_run_is_unknown_blocked() -> None:
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()  # no rows
    rows = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}
    assert rows["mn2t6_low"].live_blocker == "UNKNOWN_BLOCKED"


def test_not_released_before_safe_fetch() -> None:
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    _insert_run(
        c, **_REAL_HIGH,
        source_issue_time=(now - timedelta(minutes=10)).isoformat(),  # before 485min safe-fetch
        captured_at=now.isoformat(), target_local_date=None,
        completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat(),
    )
    high = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}["mx2t6_high"]
    assert high.live_blocker == "NOT_RELEASED"


def test_backfill_source_is_not_live_authorized() -> None:
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    rows = compute_frontier(conn=_conn(), now=now, role_filter="backfill")
    assert rows and all(r.live_blocker == "NOT_LIVE_AUTHORIZED" for r in rows)
    assert any(r.source_id == "tigge" for r in rows)


def test_healthy_complete_recent_run_is_ok() -> None:
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    issue = now - timedelta(hours=9)
    c = _conn()
    _insert_run(
        c, **_REAL_HIGH,
        source_issue_time=issue.isoformat(), source_available_at=(issue + timedelta(hours=8)).isoformat(),
        captured_at=now.isoformat(), target_local_date="2026-05-25",
        completeness_status="COMPLETE", status="SUCCESS", recorded_at=now.isoformat(),
    )
    c.execute(
        "INSERT INTO readiness_state VALUES (?,?,?,?,?,?)",
        ("ecmwf_open_data", "mx2t6_high_full_horizon", "2026-05-25", "READY",
         (now + timedelta(hours=12)).isoformat(), now.isoformat()),
    )
    high = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}["mx2t6_high"]
    assert high.freshness_state == "CURRENT"
    assert high.live_blocker == "OK"
