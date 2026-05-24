# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Frontier tests incl. backfill-write-time-cannot-fake-freshness antibody.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR2);
#   operator spec §"Backfill can look fresh" + §10 (frontier report).
"""Relationship tests for the in-memory collection frontier (PR2).

THE antibody: freshness is measured on SOURCE/EVENT time, never write time — a row written
seconds ago for an old cycle must report STALE, not CURRENT.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


_SOURCE_RUN_COLS = (
    "source_id, track, source_issue_time, source_release_time, source_available_at, "
    "fetch_started_at, captured_at, imported_at, target_local_date, completeness_status, "
    "status, recorded_at"
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(f"CREATE TABLE source_run ({_SOURCE_RUN_COLS.replace(', ', ' TEXT, ')} TEXT)")
    c.execute(
        "CREATE TABLE readiness_state (source_id TEXT, track TEXT, target_local_date TEXT, "
        "status TEXT, expires_at TEXT, computed_at TEXT)"
    )
    return c


def _insert_run(c: sqlite3.Connection, **kw: str) -> None:
    cols = [
        "source_id", "track", "source_issue_time", "source_release_time", "source_available_at",
        "fetch_started_at", "captured_at", "imported_at", "target_local_date",
        "completeness_status", "status", "recorded_at",
    ]
    c.execute(
        f"INSERT INTO source_run ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        tuple(kw.get(k) for k in cols),
    )


@pytest.fixture(autouse=True)
def _no_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from any on-disk state/source_health.json."""
    import src.data.collection_frontier as cf

    monkeypatch.setattr(cf, "_load_health", lambda: {})


def test_backfill_write_time_cannot_fake_freshness() -> None:
    """ANTIBODY: an old cycle (source_issue_time 40h ago) written with a FRESH captured_at
    (now) must report EXPIRED + STALE_SOURCE — freshness is on source time, not write time.
    """
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    # ECMWF high: issue 40h ago (> 30h ceiling) but captured_at = now (fresh write).
    _insert_run(
        c,
        source_id="ecmwf_open_data", track="mx2t6_high",
        source_issue_time=(now - timedelta(hours=40)).isoformat(),
        captured_at=now.isoformat(), imported_at=now.isoformat(),
        target_local_date="2026-05-25", completeness_status="complete",
        status="ok", recorded_at=now.isoformat(),
    )

    rows = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}
    high = rows["mx2t6_high"]
    assert high.freshness_state == "EXPIRED"          # NOT current
    assert high.live_blocker == "STALE_SOURCE"
    assert high.captured_at == now                    # fresh write recorded, but ignored for freshness


def test_missing_source_run_is_unknown_blocked() -> None:
    """A live partition with no source_run row = UNKNOWN_BLOCKED (not silently OK)."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()  # no rows at all
    rows = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}
    assert rows["mn2t6_low"].live_blocker == "UNKNOWN_BLOCKED"
    assert rows["mn2t6_low"].freshness_state == "UNKNOWN"


def test_not_released_when_before_safe_fetch() -> None:
    """A cycle issued 10min ago (safe_fetch lag 485min) → NOT_RELEASED."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    _insert_run(
        c, source_id="ecmwf_open_data", track="mx2t6_high",
        source_issue_time=(now - timedelta(minutes=10)).isoformat(),
        captured_at=now.isoformat(), target_local_date="2026-05-25",
        completeness_status="complete", status="ok", recorded_at=now.isoformat(),
    )
    high = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}["mx2t6_high"]
    assert high.live_blocker == "NOT_RELEASED"


def test_backfill_source_is_not_live_authorized() -> None:
    """A backfill-only source (tigge) reports NOT_LIVE_AUTHORIZED, never a fault."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    rows = compute_frontier(conn=c, now=now, role_filter="backfill")
    assert rows, "expected at least the tigge backfill entry"
    assert all(r.live_blocker == "NOT_LIVE_AUTHORIZED" for r in rows)
    assert any(r.source_id == "tigge" for r in rows)


def test_healthy_complete_recent_run_is_ok() -> None:
    """A complete, in-freshness, post-safe-fetch run with ready readiness = OK (no blocker)."""
    from src.data.collection_frontier import compute_frontier

    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    c = _conn()
    issue = now - timedelta(hours=9)  # > 485min safe_fetch, < 24h degraded
    _insert_run(
        c, source_id="ecmwf_open_data", track="mx2t6_high",
        source_issue_time=issue.isoformat(), source_available_at=(issue + timedelta(hours=8)).isoformat(),
        captured_at=now.isoformat(), target_local_date="2026-05-25",
        completeness_status="complete", status="ok", recorded_at=now.isoformat(),
    )
    c.execute(
        "INSERT INTO readiness_state (source_id, track, target_local_date, status, expires_at, computed_at) "
        "VALUES (?,?,?,?,?,?)",
        ("ecmwf_open_data", "mx2t6_high", "2026-05-25", "READY",
         (now + timedelta(hours=12)).isoformat(), now.isoformat()),
    )
    high = {r.track: r for r in compute_frontier(conn=c, now=now, role_filter="live")}["mx2t6_high"]
    assert high.freshness_state == "CURRENT"
    assert high.live_blocker == "OK"
