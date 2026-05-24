# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Frontier tests vs REAL source_run/source_run_coverage shapes — #329 F1/F2 + R2-A/B
#   + R3 SEV-1 (latest-USABLE vs attempted; coverage scoped to source_run_id; expiry/completeness).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + collection_frontier before relying on it.
"""Relationship tests for the in-memory collection frontier (PR2 + #329 R1/R2/R3 fixes).

Fixtures use REAL write shapes and link source_run.source_run_id ↔ source_run_coverage.source_run_id,
so coverage is scoped to a SPECIFIC run (a newer failed/short attempt cannot hide an older usable
full cycle; old/expired coverage cannot fake OK).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

_SR_COLS = [
    "source_run_id", "source_id", "track", "release_calendar_key", "source_issue_time",
    "source_release_time", "source_available_at", "fetch_started_at", "captured_at",
    "imported_at", "target_local_date", "completeness_status", "status", "recorded_at",
]
_COV_COLS = ["source_run_id", "source_id", "track", "release_calendar_key", "target_local_date",
             "readiness_status", "completeness_status", "expires_at"]

_NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(f"CREATE TABLE source_run ({' TEXT, '.join(_SR_COLS)} TEXT)")
    c.execute(f"CREATE TABLE source_run_coverage ({' TEXT, '.join(_COV_COLS)} TEXT)")
    c.execute(
        "CREATE TABLE readiness_state (source_id TEXT, track TEXT, target_local_date TEXT, "
        "status TEXT, expires_at TEXT, computed_at TEXT)"
    )
    return c


def _run(c, run_id, *, track="mx2t6_high_full_horizon", rck="ecmwf_open_data:mx2t6_high:full",
         issue_h=9, status="SUCCESS", completeness="COMPLETE") -> None:
    issue = _NOW - timedelta(hours=issue_h)
    c.execute(
        f"INSERT INTO source_run ({', '.join(_SR_COLS)}) VALUES ({', '.join('?' for _ in _SR_COLS)})",
        (run_id, "ecmwf_open_data", track, rck, issue.isoformat(), None, None, None,
         _NOW.isoformat(), None, None, completeness, status, _NOW.isoformat()),
    )


def _cov(c, run_id, target, readiness, *, completeness="COMPLETE", expires_h=12) -> None:
    exp = (_NOW + timedelta(hours=expires_h)).isoformat() if expires_h is not None else None
    c.execute(
        f"INSERT INTO source_run_coverage ({', '.join(_COV_COLS)}) VALUES ({', '.join('?' for _ in _COV_COLS)})",
        (run_id, "ecmwf_open_data", "mx2t6_high_full_horizon", "ecmwf_open_data:mx2t6_high:full",
         target, readiness, completeness, exp),
    )


@pytest.fixture(autouse=True)
def _no_health(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.data.collection_frontier as cf
    monkeypatch.setattr(cf, "_load_health", lambda: {})


def _f(c):
    from src.data.collection_frontier import compute_frontier
    return {r.track: r for r in compute_frontier(conn=c, now=_NOW, role_filter="live")}


# ---- F1: latest USABLE vs latest ATTEMPTED ----

def test_latest_failed_attempt_does_not_hide_older_usable_full_run() -> None:
    c = _conn()
    _run(c, "R0", issue_h=10, status="SUCCESS")          # older usable full
    _cov(c, "R0", "2026-05-25", "LIVE_ELIGIBLE")
    _run(c, "R1", issue_h=2, status="FAILED", completeness="MISSING")  # newer failed (before safe-fetch anyway)
    # R1 issued 2h ago < 485min safe-fetch, but R0 is usable+fresh+ready → OK
    assert _f(c)["mx2t6_high"].live_blocker == "OK"


def test_only_short_horizon_run_is_short_horizon_only() -> None:
    """SHORT_HORIZON_ONLY only when NO usable full run exists — just a short cycle."""
    c = _conn()
    _run(c, "S1", track="mx2t6_high_short_horizon", rck="ecmwf_open_data:mx2t6_high:short",
         issue_h=9, status="SUCCESS")
    assert _f(c)["mx2t6_high"].live_blocker == "SHORT_HORIZON_ONLY"


def test_newer_short_does_not_block_older_fresh_ready_full() -> None:
    """R2-A/R3-F4: newer short cycle must NOT block an older fresh+ready full cycle."""
    c = _conn()
    _run(c, "F0", issue_h=12, status="SUCCESS")          # full, fresh
    _cov(c, "F0", "2026-05-25", "LIVE_ELIGIBLE")
    _run(c, "S1", track="mx2t6_high_short_horizon", rck="ecmwf_open_data:mx2t6_high:short",
         issue_h=9, status="SUCCESS")
    assert _f(c)["mx2t6_high"].live_blocker == "OK"


# ---- F2: coverage scoped to source_run_id ----

def test_old_coverage_does_not_make_latest_run_ok() -> None:
    """Coverage from an OLD run must not satisfy a newer run with no coverage."""
    c = _conn()
    _run(c, "OLD", issue_h=11, status="SUCCESS")
    _cov(c, "OLD", "2026-05-25", "LIVE_ELIGIBLE")        # coverage belongs to OLD
    _run(c, "NEW", issue_h=9, status="SUCCESS")          # newer usable, but NO coverage
    # usable=NEW (latest full success); coverage(NEW)=empty → COVERAGE_UNKNOWN, not OK
    assert _f(c)["mx2t6_high"].live_blocker == "COVERAGE_UNKNOWN"


def test_old_blocked_coverage_does_not_block_latest_good_run() -> None:
    c = _conn()
    _run(c, "OLD", issue_h=11, status="SUCCESS")
    _cov(c, "OLD", "2026-05-25", "BLOCKED")              # old blocked — must NOT block
    _run(c, "NEW", issue_h=9, status="SUCCESS")
    _cov(c, "NEW", "2026-05-25", "LIVE_ELIGIBLE")        # latest all-ready
    assert _f(c)["mx2t6_high"].live_blocker == "OK"


# ---- F3: coverage expiry + completeness ----

def test_expired_live_eligible_coverage_is_not_ok() -> None:
    c = _conn()
    _run(c, "R1", issue_h=9, status="SUCCESS")
    _cov(c, "R1", "2026-05-25", "LIVE_ELIGIBLE", expires_h=-1)   # expired
    assert _f(c)["mx2t6_high"].live_blocker != "OK"


def test_partial_completeness_coverage_is_not_ready() -> None:
    c = _conn()
    _run(c, "R1", issue_h=9, status="SUCCESS")
    _cov(c, "R1", "2026-05-25", "LIVE_ELIGIBLE", completeness="PARTIAL")
    assert _f(c)["mx2t6_high"].live_blocker != "OK"


def test_any_blocked_target_in_run_coverage_is_not_ok() -> None:
    c = _conn()
    _run(c, "R1", issue_h=9, status="SUCCESS")
    _cov(c, "R1", "2026-05-25", "LIVE_ELIGIBLE")
    _cov(c, "R1", "2026-05-26", "BLOCKED")
    assert _f(c)["mx2t6_high"].live_blocker != "OK"


def test_full_run_all_targets_ready_is_ok() -> None:
    c = _conn()
    _run(c, "R1", issue_h=9, status="SUCCESS")
    _cov(c, "R1", "2026-05-25", "LIVE_ELIGIBLE")
    _cov(c, "R1", "2026-05-26", "LIVE_ELIGIBLE")
    high = _f(c)["mx2t6_high"]
    assert high.freshness_state == "CURRENT" and high.live_blocker == "OK"


# ---- prior-round invariants still hold ----

def test_complete_run_without_coverage_is_coverage_unknown() -> None:
    c = _conn()
    _run(c, "R1", issue_h=9, status="SUCCESS")
    assert _f(c)["mx2t6_high"].live_blocker == "COVERAGE_UNKNOWN"


def test_stale_usable_run_is_stale_source() -> None:
    """A full SUCCESS run 40h old (> ceiling) is not usable → STALE (not OK)."""
    c = _conn()
    _run(c, "R1", issue_h=40, status="SUCCESS")
    _cov(c, "R1", "2026-05-25", "LIVE_ELIGIBLE")
    assert _f(c)["mx2t6_high"].live_blocker == "STALE_SOURCE"


def test_missing_source_run_is_unknown_blocked() -> None:
    assert _f(_conn())["mn2t6_low"].live_blocker == "UNKNOWN_BLOCKED"


def test_not_released_before_safe_fetch() -> None:
    c = _conn()
    # recent cycle, not yet released/fetched (no usable success), before 485min safe-fetch:
    _run(c, "R1", issue_h=0, status="SKIPPED_NOT_RELEASED", completeness="NOT_RELEASED")
    assert _f(c)["mx2t6_high"].live_blocker == "NOT_RELEASED"


def test_skipped_not_released_after_safe_fetch_is_not_ok() -> None:
    c = _conn()
    _run(c, "R1", issue_h=9, status="SKIPPED_NOT_RELEASED", completeness="NOT_RELEASED")
    assert _f(c)["mx2t6_high"].live_blocker != "OK"


def test_backfill_source_is_not_live_authorized() -> None:
    from src.data.collection_frontier import compute_frontier

    rows = compute_frontier(conn=_conn(), now=_NOW, role_filter="backfill")
    assert rows and all(r.live_blocker == "NOT_LIVE_AUTHORIZED" for r in rows)
    assert any(r.source_id == "tigge" for r in rows)


def test_frontier_contains_weather_market_venue_and_observation_sources() -> None:
    """PR #329 review C acceptance: the frontier federates over ALL live data families, not just
    the forecast release calendar. It must contain rows for forecast + observation + solar +
    market_topology + executable_market + venue_user_ws + settlement + diagnostic — otherwise it
    cannot claim to be a data-collection control plane while excluding market/observation truth.

    RED before this PR: compute_frontier emitted forecast-calendar rows only."""
    c = _conn()
    # add the probeable family tables (empty is fine — families still appear; probed ones can
    # report event presence when populated):
    c.execute("CREATE TABLE observations (city TEXT, target_date TEXT, source TEXT, fetched_at TEXT)")
    c.execute("CREATE TABLE market_events_v2 (condition_id TEXT, created_at TEXT)")
    c.execute("CREATE TABLE settlements (city TEXT, target_date TEXT, settled_at TEXT, settlement_source TEXT)")
    c.execute("INSERT INTO observations VALUES ('chicago','2026-05-24','wu_icao_history','2026-05-24T11:00:00Z')")
    c.execute("INSERT INTO market_events_v2 VALUES ('0xabc','2026-05-24T10:00:00Z')")

    from src.data.collection_frontier import compute_frontier

    rows = compute_frontier(conn=c, now=_NOW)
    families = {r.family for r in rows}
    expected = {
        "forecast", "observation", "solar", "market_topology",
        "executable_market", "venue_user_ws", "settlement", "diagnostic",
    }
    missing = expected - families
    assert not missing, f"frontier missing data families (forecast-only regression?): {sorted(missing)}"

    # the observation family probes the EVENT-time column (target_date), and reports presence:
    obs = [r for r in rows if r.family == "observation"]
    assert obs and any(r.source_issue_time is not None for r in obs), "observation event-time not probed"
    # a family with no probe (executable_market truth lives in the trade DB) degrades honestly to
    # COVERAGE_UNKNOWN, never a fabricated freshness:
    exe = [r for r in rows if r.family == "executable_market"]
    assert exe and all(r.live_blocker in ("COVERAGE_UNKNOWN", "OK", "NOT_LIVE_AUTHORIZED") for r in exe)


def test_family_freshness_uses_event_time_not_write_time() -> None:
    """C correctness + the load-bearing rule: a non-forecast family's freshness age is measured on
    its EVENT-time column (observations.target_date), never a write-time column. An old target_date
    backfilled now must report a large age, not ~0."""
    from src.data.collection_frontier import _family_latest_event

    c = _conn()
    c.execute("CREATE TABLE observations (city TEXT, target_date TEXT, source TEXT, fetched_at TEXT)")
    # event (target_date) is a week old, write (fetched_at) is now:
    c.execute("INSERT INTO observations VALUES ('chicago','2026-05-17','wu_icao_history','2026-05-24T11:00:00Z')")
    latest = _family_latest_event(c, "observations", "target_date")
    assert latest is not None and latest.date().isoformat() == "2026-05-17"  # event time, not the write time
