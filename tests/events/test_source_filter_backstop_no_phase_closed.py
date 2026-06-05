# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: consolidated timeliness/tradeability fix (architect design) STEP 6 —
#                  reactor EVENT_BOUND_MARKET_PHASE_CLOSED becomes a regression-only backstop
#                  once the source filters (STEP 2 emission, STEP 3 claim floor) are active.
"""RED→GREEN T7 (relationship): with the source claim-floor active, no event the
reactor's bind-time phase gate would reject as POST_TRADING (already-settled)
ever reaches that gate.

The reactor's EVENT_BOUND_MARKET_PHASE_CLOSED gate
(event_reactor_adapter.py) stays as a fail-closed backstop, but after STEP 3 it
should only fire on REGRESSION: the fetch_pending claim-floor already drops every
strictly-past (POST_TRADING) FORECAST_SNAPSHOT_READY before it can be processed.
This test crosses the boundary: it inserts a mixed queue and proves every event
fetch_pending RETURNS is NOT POST_TRADING at the gate, so a full cycle yields
ZERO POST_TRADING phase rejections from the source-filtered stream.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.state.db import init_schema
from src.strategy.market_phase import MarketPhase


# Chicago local 2026-06-05 01:00 — target 2026-06-04 (and earlier) is strictly
# past; 2026-06-05 is same-day and PRE its F1 12:00Z market close (SETTLEMENT_DAY,
# not POST_TRADING); 2026-06-06 is fresh.
_DECISION_TIME = "2026-06-05T06:00:00+00:00"


def _payload(target_date: str, snapshot_id: str) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date=target_date,
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-03T04:10:00+00:00",
        available_at="2026-06-03T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _event(target_date: str, snapshot_id: str, *, available_at: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|{target_date}|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-03T04:10:00+00:00",
        available_at=available_at,
        received_at=available_at,
        causal_snapshot_id=snapshot_id,
        payload=_payload(target_date, snapshot_id),
        priority=0,
    )


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_source_filtered_queue_yields_no_post_trading_at_gate():
    from datetime import datetime, timezone

    from src.engine.event_reactor_adapter import (
        _edli_forecast_only_phase_evidence,
    )

    conn = _world_conn()
    store = EventStore(conn)
    # Mixed queue: two strictly-past (settled) targets + one same-day + one fresh.
    events = [
        _event("2026-06-03", "snap-d3", available_at="2026-06-02T00:00:00+00:00"),  # settled
        _event("2026-06-04", "snap-d4", available_at="2026-06-03T00:00:00+00:00"),  # settled
        _event("2026-06-05", "snap-d5", available_at="2026-06-04T00:00:00+00:00"),  # same-day
        _event("2026-06-06", "snap-d6", available_at="2026-06-04T12:00:00+00:00"),  # fresh
    ]
    for ev in events:
        store.insert_or_ignore(ev)

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=100)
    returned_targets = {e.entity_key.split("|")[1] for e in returned}

    # STEP 3 floor: both strictly-past (settled) targets are dropped at the source.
    assert "2026-06-03" not in returned_targets
    assert "2026-06-04" not in returned_targets

    decision_dt = datetime.fromisoformat(_DECISION_TIME)
    post_trading_at_gate = []
    for e in returned:
        target_date = e.entity_key.split("|")[1]
        evidence = _edli_forecast_only_phase_evidence(
            city="Chicago",
            target_date=target_date,
            decision_time=decision_dt,
            selected_market_row={},  # F1 12:00Z fallback — same as production reactor row
        )
        if evidence.phase == MarketPhase.POST_TRADING:
            post_trading_at_gate.append(target_date)

    # T7 core: NO event reaching the gate is POST_TRADING — the source filter
    # already removed every already-settled candidate, so the reactor's
    # EVENT_BOUND_MARKET_PHASE_CLOSED:post_trading branch can never fire on the
    # source-filtered stream (it is now a pure regression backstop).
    assert post_trading_at_gate == [], (
        f"source filter leaked POST_TRADING events to the gate: {post_trading_at_gate}"
    )
