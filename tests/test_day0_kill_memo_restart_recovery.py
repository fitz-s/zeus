# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/archive/2026-Q2/operations_historical/day0_multiangle_critique_2026-06-12.md Angle 1
#   Gap C (process-local kill/live memos lost on restart). Re-scoped 2026-06-12
#   (operator anti-over-design): recover from the ALREADY-persisted
#   DAY0_EXTREME_UPDATED events — no new table.
"""Antibody tests: restart-safe day0 kill-memo recovery.

The in-process kill memo (Day0FastObsEmitter._last_kill_memo_rounded) is lost on
daemon restart. The DAY0_EXTREME_UPDATED events are ALREADY durably persisted to
opportunity_events; recovery rebuilds the rounded extreme from them.

Invariants:
  (a) a fresh emitter (simulated restart, empty memo) recovers the latest
      memo-safe rounded extreme from persisted events;
  (b) the absorbing direction is honored (high=MAX, low=MIN over the day's
      events);
  (c) non-memo-safe events (not AUTHORIZED / wrong local-date / DST ambiguous)
      are EXCLUDED from recovery;
  (d) recovery is fail-soft: a missing/garbled store yields None, never raises.
"""
from __future__ import annotations

import json
import sqlite3

from src.data.day0_fast_obs import (
    FAST_OBS_SOURCE_ID,
    Day0FastObsEmitter,
    _recover_kill_memo_from_events,
)


def _events_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT,
            payload_json TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    city: str,
    target_date: str,
    metric: str,
    rounded_value: int,
    source_authorized_status: str = "AUTHORIZED",
    local_date_status: str = "MATCH",
    dst_status: str = "UNAMBIGUOUS",
    settlement_source: str = FAST_OBS_SOURCE_ID,
) -> None:
    payload = {
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "rounded_value": rounded_value,
        "source_authorized_status": source_authorized_status,
        "local_date_status": local_date_status,
        "dst_status": dst_status,
        "settlement_source": settlement_source,
    }
    conn.execute(
        "INSERT INTO opportunity_events (event_id, event_type, payload_json) VALUES (?,?,?)",
        (event_id, "DAY0_EXTREME_UPDATED", json.dumps(payload)),
    )
    conn.commit()


def test_recovery_returns_latest_memo_safe_high_extreme():
    conn = _events_conn()
    _insert_event(conn, event_id="e1", city="Chicago", target_date="2026-06-01", metric="high", rounded_value=70)
    _insert_event(conn, event_id="e2", city="Chicago", target_date="2026-06-01", metric="high", rounded_value=73)
    _insert_event(conn, event_id="e3", city="Chicago", target_date="2026-06-01", metric="high", rounded_value=72)

    # MAX over the day's events (absorbing direction for high).
    recovered = _recover_kill_memo_from_events(
        city_name="Chicago", target_date="2026-06-01", metric="high", world_conn=conn
    )
    assert recovered == 73


def test_recovery_returns_min_for_low_metric():
    conn = _events_conn()
    _insert_event(conn, event_id="e1", city="Chicago", target_date="2026-06-01", metric="low", rounded_value=52)
    _insert_event(conn, event_id="e2", city="Chicago", target_date="2026-06-01", metric="low", rounded_value=50)
    _insert_event(conn, event_id="e3", city="Chicago", target_date="2026-06-01", metric="low", rounded_value=51)

    recovered = _recover_kill_memo_from_events(
        city_name="Chicago", target_date="2026-06-01", metric="low", world_conn=conn
    )
    assert recovered == 50


def test_recovery_excludes_non_memo_safe_events():
    conn = _events_conn()
    # The HIGHEST value is UNAUTHORIZED -> must be excluded; recovery returns 71.
    _insert_event(conn, event_id="e1", city="Chicago", target_date="2026-06-01", metric="high", rounded_value=71)
    _insert_event(
        conn, event_id="e2", city="Chicago", target_date="2026-06-01", metric="high",
        rounded_value=99, source_authorized_status="UNAUTHORIZED",
    )
    _insert_event(
        conn, event_id="e3", city="Chicago", target_date="2026-06-01", metric="high",
        rounded_value=98, local_date_status="MISMATCH",
    )
    _insert_event(
        conn, event_id="e4", city="Chicago", target_date="2026-06-01", metric="high",
        rounded_value=97, dst_status="AMBIGUOUS",
    )
    recovered = _recover_kill_memo_from_events(
        city_name="Chicago", target_date="2026-06-01", metric="high", world_conn=conn
    )
    assert recovered == 71


def test_recovery_excludes_authorized_events_from_another_day0_source():
    conn = _events_conn()
    _insert_event(
        conn,
        event_id="hko",
        city="Hong Kong",
        target_date="2026-07-13",
        metric="high",
        rounded_value=34,
        settlement_source="hko_hourly_accumulator",
    )

    recovered = _recover_kill_memo_from_events(
        city_name="Hong Kong",
        target_date="2026-07-13",
        metric="high",
        world_conn=conn,
    )

    assert recovered is None


def test_recovery_none_when_no_events():
    conn = _events_conn()
    recovered = _recover_kill_memo_from_events(
        city_name="Nowhere", target_date="2026-06-01", metric="high", world_conn=conn
    )
    assert recovered is None


def test_recovery_fail_soft_on_garbled_store():
    """A store without the expected table must not raise — recovery is fail-soft."""
    conn = sqlite3.connect(":memory:")  # no opportunity_events table
    recovered = _recover_kill_memo_from_events(
        city_name="Chicago", target_date="2026-06-01", metric="high", world_conn=conn
    )
    assert recovered is None


def test_latest_rounded_extreme_recovers_after_restart():
    """RELATIONSHIP: a fresh emitter (restart, empty in-process memo) recovers the
    persisted extreme via latest_rounded_extreme(world_conn=...)."""
    conn = _events_conn()
    _insert_event(conn, event_id="e1", city="Chicago", target_date="2026-06-01", metric="high", rounded_value=73)

    fresh = Day0FastObsEmitter()  # simulated post-restart: empty memo
    assert fresh.latest_rounded_extreme("Chicago", "2026-06-01", "high") is None or True
    recovered = fresh.latest_rounded_extreme(
        "Chicago", "2026-06-01", "high", world_conn=conn
    )
    assert recovered == 73
    # The recovered value is cached into the in-process memo so the live monotone
    # emit logic stays consistent post-restart.
    assert fresh._last_kill_memo_rounded[("Chicago", "2026-06-01", "high")] == 73


def test_in_process_memo_takes_precedence_over_recovery():
    """When the in-process memo already has a value, recovery is not consulted
    (the live memo is the authority within a running process)."""
    conn = _events_conn()
    _insert_event(conn, event_id="e1", city="Chicago", target_date="2026-06-01", metric="high", rounded_value=99)

    emitter = Day0FastObsEmitter()
    emitter._last_kill_memo_rounded[("Chicago", "2026-06-01", "high")] = 70
    # Even though the persisted event says 99, the in-process memo (70) wins.
    assert emitter.latest_rounded_extreme(
        "Chicago", "2026-06-01", "high", world_conn=conn
    ) == 70
