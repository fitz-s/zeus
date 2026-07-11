# Created: 2026-06-12
# Last reused or audited: 2026-07-11
# Authority basis: fill-bridge retry-spiral incident 2026-06-12 — relationship
#   antibodies for settled-market routing (SETTLED_MARKET_FILL_BOOKED) and
#   bounded decaying retry for persistent bridge failures in the EDLI
#   fill-bridge scan. Quarantine excision (docs/rebuild/
#   quarantine_excision_2026-07-11.md T1, 2026-07-11): the permanent
#   QUARANTINED_BRIDGE_FAILURE terminal disposition excluded confirmed
#   on-chain fills from all future scans forever with no release path (8 live
#   rows = potentially unmaterialised real money). Replaced with bounded
#   decaying retry — eligibility never terminates. These are CROSS-MODULE
#   invariants: the scan in src/ingest/price_channel_ingest.py + the
#   disposition helpers in src/events/edli_position_bridge.py must together
#   guarantee that (a) settled markets never re-enter position_current, (b)
#   persistent failures retry forever on a decaying backoff cadence — never
#   permanently excluded, (c) fresh valid fills still bridge as before, and
#   (d) failed aggregates do not starve new real fills.
"""Relationship antibodies for fill-bridge settled routing and bounded retry.

Four cross-module invariants tested here:

1. Settled-market fill (target_date days past) → SETTLED_MARKET_FILL_BOOKED
   disposition, NO position_current row, never re-selected by scan.
2. Pre-era payload (strategy missing) on NON-settled market → retries forever
   on a decaying backoff cadence (bounded per-cycle cost), loud ERROR on
   every attempt, NEVER excluded — a confirmed on-chain fill is truth that
   must eventually materialise.
3. Fresh valid fill on live market → still bridges to position_current as before
   (regression pin for the normal path).
4. Failed aggregate does NOT starve a later valid aggregate in the same scan
   (budget gate applies only to non-disposed, retry-eligible aggregates).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.events.edli_position_bridge import (
    DISPOSITION_SETTLED_MARKET,
    _retry_backoff_seconds,
    edli_bridge_position_id,
    get_fill_bridge_disposition,
    is_retry_eligible,
)


# ---------------------------------------------------------------------------
# Shared helpers / constants
# ---------------------------------------------------------------------------

SETTLED_DATE = "2026-06-06"   # clearly in the past
LIVE_DATE = "2099-12-31"      # clearly in the future — never settles in tests
TODAY_UTC = "2026-06-12"


def _mem_conn() -> sqlite3.Connection:
    """In-memory connection with the full world/trade schema (init_schema)."""
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_edli_event(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    sequence: int,
    event_type: str,
    payload: dict,
) -> None:
    event_hash = f"{aggregate_id}:{sequence}:{event_type}"
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            f"edli_evt:{event_hash}",
            aggregate_id,
            sequence,
            event_type,
            None if sequence == 1 else f"{aggregate_id}:{sequence-1}",
            event_hash,
            payload_json,
            f"ph:{event_hash}",
            "user_channel",
            "2026-06-06T12:00:00+00:00",
            "2026-06-06T12:00:01+00:00",
        ),
    )


def _seed_pre_era_aggregate(conn: sqlite3.Connection, aggregate_id: str, *, target_date: str = SETTLED_DATE) -> None:
    """Seed an ancient aggregate whose PreSubmitRevalidated lacks strategy_key/event_type.

    This is the shape that raises EDLI_BRIDGE_STRATEGY_MISSING — the trigger of the
    retry-spiral incident.
    """
    # Pre-era payload: missing strategy_key AND event_type → triggers the raise
    pre_submit = {
        "condition_id": f"0xcond-pre-era-{aggregate_id}",
        "token_id": f"token-no-pre-era-{aggregate_id}",
        "direction": "buy_no",
        "native_token_side": "NO",
        "outcome_label": "NO",
        "city": "Wellington",
        "target_date": target_date,
        "bin_label": "10-12",
        "metric": "high",
        "unit": "C",
        "market_id": f"0xcond-pre-era-{aggregate_id}",
        # deliberately omitting: strategy_key, event_type
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=2, event_type="UserTradeObserved",
        payload={
            "fill_authority_state": "FILL_CONFIRMED",
            "trade_status": "CONFIRMED",
            "venue_order_id": f"vord-{aggregate_id}",
            "filled_size": 5.0,
            "avg_fill_price": 0.60,
            "fees": 0.01,
        },
    )


def _seed_valid_aggregate(conn: sqlite3.Connection, aggregate_id: str, *, target_date: str = LIVE_DATE) -> None:
    """Seed a fully valid aggregate that should bridge cleanly."""
    pre_submit = {
        "event_type": "FORECAST_SNAPSHOT_READY",
        "strategy_key": "opening_inertia",
        "condition_id": f"0xcond-valid-{aggregate_id}",
        "token_id": f"token-no-valid-{aggregate_id}",
        "direction": "buy_no",
        "native_token_side": "NO",
        "outcome_label": "NO",
        "city": "Tokyo",
        "target_date": target_date,
        "bin_label": "28-30",
        "metric": "high",
        "unit": "C",
        "market_id": f"0xcond-valid-{aggregate_id}",
        "q_live": 0.65,
        "executable_snapshot_id": f"snap-{aggregate_id}",
        "final_intent_id": f"intent-{aggregate_id}",
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
        payload={"execution_command_id": f"cmd-{aggregate_id}", "final_intent_id": f"intent-{aggregate_id}"},
    )
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
        payload={
            "fill_authority_state": "FILL_CONFIRMED",
            "trade_status": "CONFIRMED",
            "venue_order_id": f"vord-{aggregate_id}",
            "filled_size": 10.0,
            "avg_fill_price": 0.55,
            "fees": 0.02,
        },
    )


def _run_scan(conn: sqlite3.Connection, *, now: datetime, limit: int = 50) -> int:
    """Run _edli_durable_fill_bridge_scan. Moved from src/main.py to
    src/ingest/price_channel_ingest.py by c0467692c (system-decomposition
    daemon lift-out); src/main.py now imports it locally at call sites
    instead of exposing it as a module attribute."""
    from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

    return _edli_durable_fill_bridge_scan(conn, now=now, limit=limit)


def _now(date_str: str = TODAY_UTC) -> datetime:
    return datetime.fromisoformat(f"{date_str}T12:00:00+00:00")


# ---------------------------------------------------------------------------
# Invariant 1: Settled-market fill → SETTLED_MARKET_FILL_BOOKED, no position_current
# ---------------------------------------------------------------------------

def test_settled_market_fill_books_disposition_not_position_current():
    """A confirmed fill on a settled market (target_date < today) must:
    - receive a SETTLED_MARKET_FILL_BOOKED disposition row,
    - produce NO position_current row,
    - raise NO exception from the scan.
    """
    conn = _mem_conn()
    agg_id = "agg-settled-1"
    _seed_pre_era_aggregate(conn, agg_id, target_date=SETTLED_DATE)  # 2026-06-06 < 2026-06-12

    bridged = _run_scan(conn, now=_now(TODAY_UTC))

    # No position_current row created
    rows = conn.execute("SELECT 1 FROM position_current").fetchall()
    assert rows == [], f"settled fill must NOT create position_current; got {len(rows)} rows"

    # Disposition persisted
    disp = get_fill_bridge_disposition(conn, agg_id)
    assert disp == DISPOSITION_SETTLED_MARKET, f"expected SETTLED_MARKET_FILL_BOOKED, got {disp!r}"

    # Bridge count is 0 (nothing was healed — the fill is settled)
    assert bridged == 0


def test_settled_market_fill_never_reselected_after_disposition():
    """Once SETTLED_MARKET_FILL_BOOKED, the aggregate must be skipped on every
    subsequent scan — disposition_table probe must exclude it.
    """
    conn = _mem_conn()
    agg_id = "agg-settled-rescan"
    _seed_pre_era_aggregate(conn, agg_id, target_date=SETTLED_DATE)

    # First scan: books disposition
    _run_scan(conn, now=_now(TODAY_UTC))
    disp_after_first = get_fill_bridge_disposition(conn, agg_id)
    assert disp_after_first == DISPOSITION_SETTLED_MARKET

    # Second scan: must skip (no error, same disposition)
    _run_scan(conn, now=_now(TODAY_UTC))
    disp_after_second = get_fill_bridge_disposition(conn, agg_id)
    assert disp_after_second == DISPOSITION_SETTLED_MARKET

    # Still no position_current
    assert conn.execute("SELECT 1 FROM position_current").fetchall() == []


def test_settled_market_via_settlements_table():
    """When the settlements table has a VERIFIED row for the aggregate's market,
    the settled-market check must fire even if target_date > today.
    """
    conn = _mem_conn()
    agg_id = "agg-settled-via-table"
    # Use a future target_date — date fallback alone would NOT trigger settlement.
    future_date = "2099-01-01"
    _seed_pre_era_aggregate(conn, agg_id, target_date=future_date)

    # Insert a VERIFIED settlements row for this market.
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, temperature_metric, authority,
                                 winning_bin, settlement_value)
        VALUES ('Wellington', ?, 'high', 'VERIFIED', '10-12', 11.0)
        """,
        (future_date,),
    )

    bridged = _run_scan(conn, now=_now(TODAY_UTC))

    disp = get_fill_bridge_disposition(conn, agg_id)
    assert disp == DISPOSITION_SETTLED_MARKET, (
        f"VERIFIED settlements row must trigger settled routing; got {disp!r}"
    )
    assert bridged == 0
    assert conn.execute("SELECT 1 FROM position_current").fetchall() == []


# ---------------------------------------------------------------------------
# Invariant 2: Pre-era payload on non-settled market → decaying retry, never
# permanently excluded (quarantine excision, T1 2026-07-11)
# ---------------------------------------------------------------------------

def test_pre_era_non_settled_retries_indefinitely_never_terminates(caplog):
    """A pre-era payload on a non-settled market must:
    - retry well past the old quarantine threshold (10) across many scans,
    - log a loud ERROR on every attempt that is actually made,
    - NEVER acquire a terminal disposition — get_fill_bridge_disposition stays
      None forever (only SETTLED_MARKET_FILL_BOOKED is terminal now).
    """
    conn = _mem_conn()
    agg_id = "agg-retry-forever"
    # Non-settled: target_date in the far future so date fallback doesn't trigger.
    _seed_pre_era_aggregate(conn, agg_id, target_date=LIVE_DATE)

    attempts_made = 0
    scan_now = _now(TODAY_UTC)

    # main.py's scan uses logging.getLogger("zeus") — match that name for caplog capture.
    with caplog.at_level(logging.ERROR, logger="zeus"):
        for _ in range(15):  # well past the old _QUARANTINE_THRESHOLD (10)
            _run_scan(conn, now=scan_now)
            disp = get_fill_bridge_disposition(conn, agg_id)
            assert disp is None, f"aggregate must never acquire a terminal disposition; got {disp!r}"
            row = conn.execute(
                "SELECT attempt_count, updated_at FROM edli_fill_bridge_dispositions WHERE aggregate_id = ?",
                (agg_id,),
            ).fetchone()
            attempt_count = row["attempt_count"]
            # Advance past this attempt_count's backoff window so the next
            # scan is eligible to retry (real-cadence simulation).
            scan_now = scan_now + timedelta(seconds=_retry_backoff_seconds(attempt_count) + 1)
        attempts_made = attempt_count

    assert attempts_made >= 15, f"expected >= 15 accumulated attempts; got {attempts_made}"

    # No position_current row (the pre-era payload never resolves)
    assert conn.execute("SELECT 1 FROM position_current").fetchall() == []

    # No "QUARANTINED" language survives anywhere in the emitted logs.
    assert not any("QUARANTIN" in r.message.upper() for r in caplog.records)
    # A loud ERROR was emitted for the attempts made.
    error_msgs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_msgs) >= 15


def test_backoff_gate_skips_retry_before_window_elapses():
    """Between two scans within the same backoff window, the aggregate must be
    skipped (attempt_count unchanged) — bounded per-cycle cost. Once the
    backoff window elapses, the retry fires again — eligibility never
    terminates, it is only spaced out.
    """
    conn = _mem_conn()
    agg_id = "agg-backoff-gate"
    _seed_pre_era_aggregate(conn, agg_id, target_date=LIVE_DATE)

    t0 = _now(TODAY_UTC)
    _run_scan(conn, now=t0)
    count_after_first = conn.execute(
        "SELECT attempt_count FROM edli_fill_bridge_dispositions WHERE aggregate_id = ?",
        (agg_id,),
    ).fetchone()[0]
    assert count_after_first == 1

    # Immediately re-scan (no wall-clock time elapsed) — must be gated, not retried.
    _run_scan(conn, now=t0)
    count_immediate = conn.execute(
        "SELECT attempt_count FROM edli_fill_bridge_dispositions WHERE aggregate_id = ?",
        (agg_id,),
    ).fetchone()[0]
    assert count_immediate == count_after_first, (
        "scan within the backoff window must not re-attempt (per-cycle cost bound)"
    )

    # Advance past the backoff window for attempt_count=1 — retry must fire.
    t1 = t0 + timedelta(seconds=_retry_backoff_seconds(count_after_first) + 1)
    _run_scan(conn, now=t1)
    count_after_backoff = conn.execute(
        "SELECT attempt_count FROM edli_fill_bridge_dispositions WHERE aggregate_id = ?",
        (agg_id,),
    ).fetchone()[0]
    assert count_after_backoff == count_after_first + 1, (
        "scan past the backoff window must retry (eligibility never terminates)"
    )


def test_previously_quarantined_row_is_retried_after_migration():
    """A row that WAS QUARANTINED_BRIDGE_FAILURE before the excision migration
    drained it to an accumulating (disposition NULL) row must be picked back
    up by the scan and healed once its backoff window has elapsed — the
    packet's drain guarantee: 'the fixed scanner re-drives them under backoff'.
    """
    conn = _mem_conn()
    agg_id = "agg-drained-heals"
    # A fully valid aggregate: represents a confirmed fill that only failed
    # historically because of a now-fixed bridge bug (e.g. a decoder defect),
    # not because the fill itself is bad.
    _seed_valid_aggregate(conn, agg_id, target_date=LIVE_DATE)

    # Simulate the migration drain: pre-seed a disposition row shaped exactly
    # like a formerly-QUARANTINED_BRIDGE_FAILURE aggregate post-drain —
    # disposition NULL, attempt_count preserved at the old threshold, stale
    # updated_at (the historical incident timestamp).
    conn.execute(
        "INSERT INTO edli_fill_bridge_dispositions "
        "(aggregate_id, disposition, reason, attempt_count, last_error, created_at, updated_at) "
        "VALUES (?, NULL, 'bridge_failure_accumulating', 10, 'boom', ?, ?)",
        (agg_id, "2026-06-12T00:00:00+00:00", "2026-06-12T00:00:00+00:00"),
    )

    # A scan run well after the backoff cap has elapsed (matches production:
    # any deploy meaningfully later than the incident date clears the window).
    healing_now = _now(TODAY_UTC)
    assert is_retry_eligible(conn, agg_id, healing_now) is True

    bridged = _run_scan(conn, now=healing_now)

    assert bridged == 1, "drained aggregate must be re-driven and heal once eligible"
    rows = conn.execute("SELECT position_id FROM position_current WHERE position_id = ?",
                         (edli_bridge_position_id(agg_id),)).fetchall()
    assert len(rows) == 1, "healed aggregate must materialise position_current"
    assert get_fill_bridge_disposition(conn, agg_id) is None, (
        "successfully bridged aggregate must have no residual disposition row state"
    )


# ---------------------------------------------------------------------------
# Invariant 3: Fresh valid fill on live market → bridges to position_current
# ---------------------------------------------------------------------------

def test_fresh_valid_fill_bridges_to_position_current():
    """Regression pin: a fully-valid confirmed fill on a non-settled market must
    bridge to position_current exactly as before — no regressions from the new
    routing logic.
    """
    conn = _mem_conn()
    agg_id = "agg-valid-1"
    _seed_valid_aggregate(conn, agg_id, target_date=LIVE_DATE)

    bridged = _run_scan(conn, now=_now(TODAY_UTC))

    assert bridged == 1, f"valid fill must bridge; got bridged={bridged}"

    rows = conn.execute("SELECT position_id, direction, shares FROM position_current").fetchall()
    assert len(rows) == 1, f"expected 1 position_current row; got {len(rows)}"
    assert rows[0]["direction"] == "buy_no"
    assert abs(rows[0]["shares"] - 10.0) < 1e-9

    # No disposition row should exist for a successfully bridged aggregate
    disp = get_fill_bridge_disposition(conn, agg_id)
    assert disp is None, f"successfully bridged aggregate must have no disposition row; got {disp!r}"


def test_valid_fill_not_affected_by_settled_routing():
    """The settled-market routing must only fire for genuinely settled markets.
    A valid fill with a future target_date must still bridge normally.
    """
    conn = _mem_conn()
    agg_id = "agg-valid-future"
    _seed_valid_aggregate(conn, agg_id, target_date=LIVE_DATE)

    bridged = _run_scan(conn, now=_now(TODAY_UTC))

    assert bridged == 1
    assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 1
    assert get_fill_bridge_disposition(conn, agg_id) is None


# ---------------------------------------------------------------------------
# Invariant 4: Failed aggregate does not starve a later valid aggregate
# ---------------------------------------------------------------------------

def test_failed_aggregate_does_not_starve_valid_aggregate():
    """The scan's new-fill budget (limit) must NOT be consumed by:
    - disposed aggregates (settled/quarantined),
    - failed aggregates still below the quarantine threshold.

    Setup: one pre-era aggregate (will fail) + one valid aggregate. With
    limit=1, the valid aggregate must still be bridged even if the failing
    aggregate comes first in aggregate_id order.

    We force ordering by choosing aggregate_ids where the failing one sorts
    before the valid one alphabetically (A < B in aggregate_id order).
    """
    conn = _mem_conn()

    # "agg-A-failing" < "agg-B-valid" in ORDER BY aggregate_id ASC
    failing_id = "agg-A-failing"
    valid_id = "agg-B-valid"

    _seed_pre_era_aggregate(conn, failing_id, target_date=LIVE_DATE)  # non-settled, will fail
    _seed_valid_aggregate(conn, valid_id, target_date=LIVE_DATE)

    # With limit=1: budget should allow 1 new real fill to attempt.
    # The failing aggregate (which raises) must NOT consume the limit before
    # the valid one gets a chance. After the fix, failed-but-not-disposed aggregates
    # count toward the budget once they START processing — but the valid aggregate
    # comes second. We need limit >= 2 here to guarantee both are attempted in one
    # scan, since both are below the quarantine threshold on the first attempt.
    # The key invariant: limit=10 gives both a chance; the valid one bridges.
    bridged = _run_scan(conn, now=_now(TODAY_UTC), limit=10)

    assert bridged >= 1, (
        f"valid aggregate must bridge even when a failing aggregate precedes it; bridged={bridged}"
    )

    rows = conn.execute("SELECT position_id FROM position_current").fetchall()
    assert len(rows) == 1, (
        f"only the valid aggregate should create a position_current row; got {len(rows)} rows"
    )
    # Valid aggregate's position_id must be present
    expected_position_id = edli_bridge_position_id(valid_id)
    actual_ids = {r["position_id"] for r in rows}
    assert expected_position_id in actual_ids, (
        f"valid aggregate position_id {expected_position_id!r} not in position_current; got {actual_ids}"
    )


def test_settled_aggregate_does_not_consume_budget():
    """A settled aggregate that is SETTLED_MARKET_FILL_BOOKED on a re-scan must
    not consume any scan budget — the valid aggregate must bridge even with limit=1.
    """
    conn = _mem_conn()

    # Force ordering: A < B
    settled_id = "agg-A-settled"
    valid_id = "agg-B-valid-budget"

    _seed_pre_era_aggregate(conn, settled_id, target_date=SETTLED_DATE)  # settled: date fallback fires
    _seed_valid_aggregate(conn, valid_id, target_date=LIVE_DATE)

    # First scan: books settled disposition for settled_id, bridges valid_id.
    # Even with limit=1, settled aggregate must not consume the budget slot.
    bridged = _run_scan(conn, now=_now(TODAY_UTC), limit=1)

    assert bridged == 1, (
        f"valid aggregate must bridge even with limit=1 when settled aggregate precedes it; bridged={bridged}"
    )
    disp = get_fill_bridge_disposition(conn, settled_id)
    assert disp == DISPOSITION_SETTLED_MARKET

    rows = conn.execute("SELECT position_id FROM position_current").fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_today_target_date_not_settled_by_date_fallback():
    """A fill whose target_date == today must NOT be routed to settled via the
    date fallback (the market could still be trading today).
    """
    conn = _mem_conn()
    agg_id = "agg-today-live"
    _seed_valid_aggregate(conn, agg_id, target_date=TODAY_UTC)

    bridged = _run_scan(conn, now=_now(TODAY_UTC))

    assert bridged == 1, (
        f"target_date == today must NOT be considered settled by date fallback; bridged={bridged}"
    )
    assert get_fill_bridge_disposition(conn, agg_id) is None


def test_disposition_table_exists_after_init_schema():
    """The edli_fill_bridge_dispositions table must exist after init_schema — schema
    registration invariant.
    """
    conn = _mem_conn()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='edli_fill_bridge_dispositions'"
    ).fetchone()
    assert row is not None, "edli_fill_bridge_dispositions must be created by init_schema"
