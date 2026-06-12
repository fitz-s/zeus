# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~08:30Z — queue priority starvation.
#   Under edli_live_scope='day0_shadow' a DAY0_EXTREME_UPDATED event can ONLY ever
#   produce a DAY0_SCOPE_SHADOW_ONLY receipt (never an order), yet fetch_pending's
#   tier CASE ranked it Tier 0 (above tradeable FORECAST_SNAPSHOT_READY Tier 1), so a
#   shadow-day0 flood starved tradeable forecast families. These RELATIONSHIP tests
#   pin: (a) the scope-aware claim tier claims the tradeable FSR before older shadow
#   day0 events when day0_is_tradeable=False; (b) the historical Tier-0 day0 behaviour
#   when day0_is_tradeable=True; (c) the emission-priority stamp matches the scope.
"""RELATIONSHIP tests across the boundary

    day0 emit-priority (event_priority) -> EventStore claim tier (fetch_pending)
    -> reactor claim order

The cross-module invariant: a shadow-only DAY0_EXTREME_UPDATED event must NEVER be
claimed ahead of a tradeable (COMPLETE + LIVE_ELIGIBLE) FORECAST_SNAPSHOT_READY when
day0 is shadow-only. Expressed as a pytest assertion on the ORDER fetch_pending
returns — not a re-implementation of the CASE.
"""
from __future__ import annotations

import sqlite3

from src.events.event_priority import (
    PRIORITY_DAY0_SHADOW,
    PRIORITY_DAY0_TRADEABLE,
    day0_emit_priority,
)
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.events.triggers.day0_extreme_updated import build_day0_extreme_updated_event
from src.state.db import init_schema

# Decision time: 2026-06-12 local target is FUTURE; the day0 06-11 target is same-day
# (still admissible — the claim floor only drops strictly-past). Both pass timeliness.
_DECISION_TIME = "2026-06-11T12:00:00+00:00"


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _tradeable_fsr(snapshot_id: str, *, available_at: str, received_at: str):
    """A COMPLETE + LIVE_ELIGIBLE FSR for a FUTURE target — Tier 1 tradeable."""
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-12",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-11T00:10:00+00:00",
        available_at=available_at,
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
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-06-12|high|{snapshot_id}",
        source="forecast_snapshot_ready_trigger",
        observed_at="2026-06-11T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=100,  # PRIORITY_TRADEABLE
    )


def _day0_event(station: str, *, available_at: str, received_at: str, priority: int):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-06-11",
        metric="high",
        settlement_source="metar",
        station_id=station,
        observation_time=available_at,
        observation_available_at=available_at,
        raw_value=30.0,
        rounded_value=30,
        high_so_far=30.0,
        low_so_far=20.0,
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|2026-06-11|high|{station}",
        source="day0_extreme_updated_trigger",
        observed_at=available_at,
        received_at=received_at,
        payload=payload,
        causal_snapshot_id=f"ctx-{station}",
        priority=priority,
    )


def test_shadow_day0_flood_does_not_starve_tradeable_fsr_when_not_tradeable():
    """RELATIONSHIP: N shadow day0 events OLDER than 1 tradeable FSR — with
    day0_is_tradeable=False the tradeable FSR is claimed FIRST (the day0 Tier-0
    clause is omitted so day0 falls to Tier 2, below the FSR Tier 1)."""
    conn = _world_conn()
    store = EventStore(conn)
    # 10 shadow day0 events, all OLDER available_at than the one tradeable FSR.
    for i in range(10):
        store.insert_or_ignore(
            _day0_event(
                f"st{i}",
                available_at="2026-06-11T06:00:00+00:00",
                received_at="2026-06-11T06:01:00+00:00",
                priority=PRIORITY_DAY0_SHADOW,
            )
        )
    fsr = _tradeable_fsr(
        "snap-trade",
        available_at="2026-06-11T11:00:00+00:00",  # NEWER than the day0 flood
        received_at="2026-06-11T11:01:00+00:00",
    )
    store.insert_or_ignore(fsr)

    claimed = store.fetch_pending(
        decision_time=_DECISION_TIME, limit=1, day0_is_tradeable=False
    )
    assert len(claimed) == 1
    assert claimed[0].event_id == fsr.event_id
    assert claimed[0].event_type == "FORECAST_SNAPSHOT_READY"


def test_tradeable_lane_keeps_day0_tier0_historical_behaviour():
    """REGRESSION: with day0_is_tradeable=True (forecast_plus_day0 / default), a
    DAY0_EXTREME_UPDATED event still outranks an FSR — historical Tier-0 behaviour
    is byte-preserved so the fix is scope-gated, not a blanket demotion."""
    conn = _world_conn()
    store = EventStore(conn)
    fsr = _tradeable_fsr(
        "snap-trade",
        available_at="2026-06-11T11:00:00+00:00",
        received_at="2026-06-11T11:01:00+00:00",
    )
    store.insert_or_ignore(fsr)
    day0 = _day0_event(
        "st0",
        available_at="2026-06-11T06:00:00+00:00",  # OLDER, but Tier 0 still wins
        received_at="2026-06-11T06:01:00+00:00",
        priority=PRIORITY_DAY0_TRADEABLE,
    )
    store.insert_or_ignore(day0)

    claimed = store.fetch_pending(
        decision_time=_DECISION_TIME, limit=1, day0_is_tradeable=True
    )
    assert len(claimed) == 1
    assert claimed[0].event_id == day0.event_id
    assert claimed[0].event_type == "DAY0_EXTREME_UPDATED"


def test_default_day0_is_tradeable_true_preserves_legacy_ordering():
    """The default parameter value (no kwarg) must equal the day0_is_tradeable=True
    behaviour — every legacy caller is byte-identical."""
    conn = _world_conn()
    store = EventStore(conn)
    fsr = _tradeable_fsr(
        "snap-trade",
        available_at="2026-06-11T11:00:00+00:00",
        received_at="2026-06-11T11:01:00+00:00",
    )
    store.insert_or_ignore(fsr)
    day0 = _day0_event(
        "st0",
        available_at="2026-06-11T06:00:00+00:00",
        received_at="2026-06-11T06:01:00+00:00",
        priority=PRIORITY_DAY0_TRADEABLE,
    )
    store.insert_or_ignore(day0)
    # No day0_is_tradeable kwarg -> default True -> day0 (Tier 0) claimed first.
    claimed = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)
    assert claimed[0].event_type == "DAY0_EXTREME_UPDATED"


def test_day0_emit_carries_shadow_priority_under_shadow_scope():
    """EMISSION test: build_day0_extreme_updated_event with day0_is_tradeable=False
    stamps PRIORITY_DAY0_SHADOW; True stamps PRIORITY_DAY0_TRADEABLE. Pins the
    emission-priority half of the fix to the shared constant."""
    from datetime import datetime, timezone

    from src.contracts.settlement_semantics import SettlementSemantics

    observation = {
        "city": "Chicago",
        "target_date": "2026-06-11",
        "metric": "high",
        "settlement_source": "metar",
        "station_id": "KORD",
        "observation_time": "2026-06-11T11:00:00+00:00",
        "observation_available_at": "2026-06-11T11:00:00+00:00",
        "raw_value": 30.0,
        "high_so_far": 30.0,
        "low_so_far": 20.0,
    }
    semantics = SettlementSemantics(
        resolution_source="TEST",
        measurement_unit="C",
        precision=1.0,
        rounding_rule="wmo_half_up",
        finalization_time="12:00:00Z",
    )
    dt = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)

    shadow = build_day0_extreme_updated_event(
        observation=observation,
        settlement_semantics=semantics,
        decision_time=dt,
        received_at="2026-06-11T11:30:00+00:00",
        day0_is_tradeable=False,
    )
    assert shadow.priority == PRIORITY_DAY0_SHADOW

    tradeable = build_day0_extreme_updated_event(
        observation=observation,
        settlement_semantics=semantics,
        decision_time=dt,
        received_at="2026-06-11T11:30:00+00:00",
        day0_is_tradeable=True,
    )
    assert tradeable.priority == PRIORITY_DAY0_TRADEABLE
    # The default (no kwarg) is the tradeable historical value.
    default = build_day0_extreme_updated_event(
        observation=observation,
        settlement_semantics=semantics,
        decision_time=dt,
        received_at="2026-06-11T11:30:00+00:00",
    )
    assert default.priority == PRIORITY_DAY0_TRADEABLE
    assert day0_emit_priority(day0_is_tradeable=False) == PRIORITY_DAY0_SHADOW
