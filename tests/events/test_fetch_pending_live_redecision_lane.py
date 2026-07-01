# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: rest-pull, terminal-no-fill, held-position, and price-screen
#   continuations all use the same EDLI_REDECISION_PENDING live lane. Regular FSR
#   stays Tier 1; live redecision stays Tier 0.
"""RELATIONSHIP test across the boundary

    fetch_pending claim ORDER (event_store + event_priority tier authority)
    -> bounded per-cycle decision budget K
    -> WHEN does a just-cancelled armed escalation family get its decision turn?

The cross-module invariant: a live redecision event must be claimed AHEAD of a
full N-city regular FSR round-robin backlog, so order-management and held-position
work fires on the next cycle instead of waiting ~2-3h for the city rotation.
Asserted on the ORDER fetch_pending returns, never a re-implementation of the SQL.
"""
from __future__ import annotations

import sqlite3

from src.config import runtime_cities_by_name
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
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


def _rest_pull_redecision(city: str, snapshot_id: str, *, available_at: str, received_at: str):
    """A rest-pull EDLI_REDECISION_PENDING row.

    DELIBERATELY the WEAKEST possible non-tier signal: priority=0 and the OLDEST
    available_at, so ONLY the standard EDLI_REDECISION_PENDING Tier-0 lane can
    promote it. If the lane works it still leads; if reverted it sinks to the tail.
    """
    payload = _fsr_payload(city, snapshot_id, available_at=available_at)
    payload_dict = payload.__dict__.copy()
    payload_dict["redecision_origin"] = "rest_pull"
    return make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key=f"{city}|{_TARGET_DATE}|high|{snapshot_id}",
        source="cycle-rest-pull-0",
        observed_at="2026-06-16T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload_dict,
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


def _day0_event(city: str, snapshot_id: str, *, available_at: str, received_at: str):
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"{city}|{_TARGET_DATE}|high|82",
        source="day0",
        observed_at=available_at,
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=Day0ExtremeUpdatedPayload(
            city=city,
            target_date=_TARGET_DATE,
            metric="high",
            settlement_source="wu_icao_history",
            station_id="TEST",
            observation_time=available_at,
            observation_available_at=available_at,
            raw_value=82.0,
            rounded_value=82,
            high_so_far=82.0,
        ),
        priority=20,
    )


def test_rest_pull_redecision_jumps_full_city_backlog():
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
    # The rest-pull re-decision for a city BURIED at index 40 of the backlog,
    # with the OLDEST available_at and priority 0.
    armed = _rest_pull_redecision(
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
        "the rest-pull re-decision did NOT jump the 49-city FSR backlog — it "
        "would wait ~2-3h for its city's round-robin turn. (Revert proof: with the "
        "Tier-0 clause removed from claim_tier_expr_sql this assert is RED.)"
    )

    # And across a full page it leads — every FSR is Tier 1, the escalation event is
    # Tier 0, so it is index 0 and appears exactly once.
    page = store.fetch_pending(decision_time=_DECISION_TIME, limit=60)
    assert page[0].event_id == armed.event_id, (
        f"rest-pull re-decision must lead the claimed page, got "
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


def test_requeued_continuous_redecision_jumps_tier0_backlog():
    """A transient live redecision retry is active money-path work, not ordinary
    discovery. After a cancel/price-move/shift-old-leg transient block, the next
    cycle must re-claim it before unrelated fresh Tier-0 work can consume the
    budget; otherwise the order/position redecision line vanishes until a later
    city rotation.
    """
    conn = _world_conn()
    store = EventStore(conn)

    cities = _REAL_CITIES[:8]
    for rank, city in enumerate(cities):
        store.insert_or_ignore(
            _day0_event(
                city,
                f"snap-day0-{city}",
                available_at=f"2026-06-16T06:{rank:02d}:00+00:00",
                received_at=f"2026-06-16T06:{rank:02d}:30+00:00",
            )
        )

    cont = _continuous_redecision(
        cities[-1],
        "snap-retry-continuous",
        available_at="2026-06-16T05:00:00+00:00",
        received_at="2026-06-16T05:00:30+00:00",
    )
    store.insert_or_ignore(cont)
    assert store.claim(cont.event_id, claimed_at="2026-06-16T06:10:00+00:00") is True
    store.requeue_pending(
        cont.event_id,
        last_error="SHIFT_BIN_EXIT_OLD_LEG_PENDING",
    )

    claimed = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)
    assert len(claimed) == 1
    assert claimed[0].event_id == cont.event_id, (
        "a requeued EDLI_REDECISION_PENDING money-path retry must reclaim the "
        "next decision slot instead of disappearing behind unrelated Tier-0 work"
    )
