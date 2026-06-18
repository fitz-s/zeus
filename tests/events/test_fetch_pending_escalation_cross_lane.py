# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: docs/evidence/qkernel_rebuild/redecide_block_2026-06-16.md §FIX
#   Phase 2 (FSR routing): the escalation re-decision is now emitted as a
#   FORECAST_SNAPSHOT_READY with source prefix 'escalation_cross-' (not as the
#   dormant EDLI_REDECISION_PENDING type which is hard-blocked at 6+ dispatch sites).
#   The Tier-0 clause in claim_tier_expr_sql keys on FORECAST_SNAPSHOT_READY +
#   source LIKE 'escalation_cross-%'. Regular FSR (source 'forecast_snapshot_ready_trigger'
#   or 'cycle-*') stays Tier 1 — fairness untouched.
"""RELATIONSHIP test across the boundary

    fetch_pending claim ORDER (event_store + event_priority tier authority)
    -> bounded per-cycle decision budget K
    -> WHEN does a just-cancelled armed escalation family get its decision turn?

The cross-module invariant: an escalation-origin FORECAST_SNAPSHOT_READY (source
'escalation_cross-*') must be claimed AHEAD of a full N-city regular FSR
round-robin backlog, so the armed TAKER_ESCALATED_AFTER_REST cross fires the next
cycle instead of waiting ~2-3h for the city rotation. Asserted on the ORDER
fetch_pending returns, never a re-implementation of the SQL. The RED-on-revert
proof lives in ``test_escalation_redecision_jumps_full_city_backlog``: with the
Tier-0 clause in ``claim_tier_expr_sql`` the escalation event leads the claimed
page; revert that clause and it sinks behind every city's rank-1 regular FSR.
"""
from __future__ import annotations

import sqlite3

from src.config import runtime_cities_by_name
from src.events.event_priority import ESCALATION_CROSS_SOURCE_PREFIX
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.state.db import init_schema

# Future local target day so the timeliness floor admits every event; the lane
# property is about claim ORDER, not timeliness.
_DECISION_TIME = "2026-06-16T12:00:00+00:00"
_TARGET_DATE = "2026-06-17"

# fetch_pending's timeliness floor (_is_timely) fails CLOSED on a city with no
# runtime timezone, so the fixtures must use REAL configured cities.
_REAL_CITIES = sorted(runtime_cities_by_name().keys())


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _fsr_payload(city: str, snapshot_id: str, *, available_at: str) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=_TARGET_DATE,
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-16T00:10:00+00:00",
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


def _tradeable_fsr(city: str, snapshot_id: str, *, available_at: str, received_at: str):
    """A COMPLETE + LIVE_ELIGIBLE FORECAST_SNAPSHOT_READY — Tier 1."""
    payload = _fsr_payload(city, snapshot_id, available_at=available_at)
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{_TARGET_DATE}|high|{snapshot_id}",
        source="forecast_snapshot_ready_trigger",
        observed_at="2026-06-16T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=100,  # PRIORITY_TRADEABLE — the strongest WITHIN-tier sub-sort
    )


def _escalation_redecision(city: str, snapshot_id: str, *, available_at: str, received_at: str):
    """An ESCALATION-origin FORECAST_SNAPSHOT_READY (source prefix 'escalation_cross-'),
    mirroring exactly what _emit_escalation_cross_redecisions emits via the FSR machinery.
    DELIBERATELY the WEAKEST possible non-tier signal: priority=0 and the OLDEST
    available_at, so ONLY the Tier-0 clause can promote it. If the lane works it
    still leads; if reverted it sinks to the tail.

    Phase 2: event_type is FORECAST_SNAPSHOT_READY (not the dormant
    EDLI_REDECISION_PENDING). The Tier-0 CASE matches on FORECAST_SNAPSHOT_READY +
    source LIKE 'escalation_cross-%'."""
    payload = _fsr_payload(city, snapshot_id, available_at=available_at)
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{_TARGET_DATE}|high|{snapshot_id}",
        source=f"{ESCALATION_CROSS_SOURCE_PREFIX}tok-0",
        observed_at="2026-06-16T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=0,
    )


def _continuous_redecision(city: str, snapshot_id: str, *, available_at: str, received_at: str):
    """A CONTINUOUS EDLI_REDECISION_PENDING with a 'cycle-*' source.

    The screen owns admission: current robust-positive entry candidates and held
    families survive; stale/unadmitted rows are expired before the skip-set
    snapshot. Once admitted, the queue must not make these rechecks wait behind
    the ordinary FSR round-robin.
    """
    payload = _fsr_payload(city, snapshot_id, available_at=available_at)
    return make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key=f"{city}|{_TARGET_DATE}|high|{snapshot_id}",
        source="cycle-tok-7",
        observed_at="2026-06-16T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=0,
    )


def test_escalation_redecision_jumps_full_city_backlog():
    """RED-ON-REVERT antibody. A 49-city tradeable-FSR backlog (one per city, the
    full live round-robin depth) PLUS ONE escalation-origin re-decision for a city
    deep in the backlog. The escalation event has the WEAKEST within-tier signal
    (priority 0, oldest available_at), so the ONLY thing that can promote it is its
    Tier-0 claim. With the fix it is claimed FIRST (budget of 1 reaches it); revert
    the Tier-0 clause in claim_tier_expr_sql and it waits behind all 49 cities'
    rank-1 FSR — the exact ~2-3h starvation the report measured."""
    conn = _world_conn()
    store = EventStore(conn)

    backlog_cities = _REAL_CITIES[:49]
    assert len(backlog_cities) == 49, "need a 49-city backlog to mirror the live round-robin depth"
    # 49 tradeable FSR, one per city, ALL with NEWER available_at than the escalation
    # event — under the legacy order they each take a round-robin rank-1 slot ahead of
    # any weaker signal.
    for rank, city in enumerate(backlog_cities):
        store.insert_or_ignore(
            _tradeable_fsr(
                city,
                f"snap-fsr-{city}",
                available_at=f"2026-06-16T11:{rank % 60:02d}:00+00:00",
                received_at=f"2026-06-16T11:{rank % 60:02d}:30+00:00",
            )
        )
    # The armed escalation re-decision for a city BURIED at index 40 of the backlog,
    # with the OLDEST available_at and priority 0.
    armed = _escalation_redecision(
        backlog_cities[40],
        "snap-armed-cross",
        available_at="2026-06-16T06:00:00+00:00",  # OLDER than every FSR above
        received_at="2026-06-16T06:01:00+00:00",
    )
    store.insert_or_ignore(armed)

    # Budget of exactly 1: the single slot MUST go to the armed escalation re-decision.
    claimed = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)
    assert len(claimed) == 1, "expected at least one claimable event"
    assert claimed[0].event_id == armed.event_id, (
        "the armed escalation re-decision did NOT jump the 49-city FSR backlog — it "
        "would wait ~2-3h for its city's round-robin turn. (Revert proof: with the "
        "Tier-0 clause removed from claim_tier_expr_sql this assert is RED.)"
    )

    # And across a full page it leads — every FSR is Tier 1, the escalation event is
    # Tier 0, so it is index 0 and appears exactly once.
    page = store.fetch_pending(decision_time=_DECISION_TIME, limit=60)
    assert page[0].event_id == armed.event_id, (
        f"escalation re-decision must lead the claimed page, got "
        f"{[e.event_type for e in page[:3]]}"
    )
    assert sum(1 for e in page if e.event_id == armed.event_id) == 1


def test_continuous_redecision_jumps_ordinary_fsr_backlog():
    """A screen-admitted continuous redecision is already confirmed live work
    (positive entry screen or held-position monitor). It must be claimed before
    the ordinary FSR discovery backlog, otherwise a one-event budget can leave
    orders/positions stale for another full city round-robin."""
    conn = _world_conn()
    store = EventStore(conn)

    cities = _REAL_CITIES[:8]
    for rank, city in enumerate(cities):
        store.insert_or_ignore(
            _tradeable_fsr(
                city,
                f"snap-fsr-{city}",
                available_at=f"2026-06-16T06:{rank:02d}:00+00:00",
                received_at=f"2026-06-16T06:{rank:02d}:30+00:00",
            )
        )
    # A continuous re-decision with the OLDEST available_at and priority 0. Only
    # the Tier-0 EDLI_REDECISION_PENDING lane can promote it ahead of the FSR page.
    cont = _continuous_redecision(
        cities[0],
        "snap-continuous",
        available_at="2026-06-16T05:00:00+00:00",
        received_at="2026-06-16T05:00:30+00:00",
    )
    store.insert_or_ignore(cont)

    claimed = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)
    assert len(claimed) == 1
    assert claimed[0].event_id == cont.event_id, (
        "a screen-admitted EDLI_REDECISION_PENDING row must not be stuck behind "
        "ordinary FSR discovery when the cycle budget reaches only one event"
    )
