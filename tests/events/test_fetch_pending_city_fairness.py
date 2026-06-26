# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~15:40Z — reactor throughput/fairness
#   incident. 50 cities had fresh posteriors + live markets + pending FORECAST_SNAPSHOT_READY
#   decision events, but with a bounded per-cycle decision budget (K) and a STRICTLY
#   freshness-ordered queue (target_date DESC, available_at DESC), the few cities whose
#   snapshots carried the newest available_at won the first K slots EVERY cycle, so the
#   tail cities were never reached before the budget was spent. Measured live: 28x city
#   imbalance (Shanghai 309 decisions/h vs Toronto 11/h), 18+ cities undecided for hours.
#   Fix: per-(tier, city) round-robin rank as the PRIMARY within-tier sort key in
#   EventStore.fetch_pending, so a budget of K reaches K DISTINCT cities per cycle and
#   every one of N cities is reached within ceil(N/K) cycles. Decision semantics unchanged.
"""RELATIONSHIP tests across the boundary

    fetch_pending claim ORDER (event_store) -> bounded per-cycle decision budget K
    -> the set of cities reached over successive cycles

The cross-module invariant the reactor relies on but cannot itself express: under a
budget that admits only K < N decisions per cycle, the queue's claim order must be FAIR
across cities — every city reached within ceil(N/K) cycles — not freshness-greedy (which
lets a few cities monopolise the budget forever). Expressed as pytest assertions on the
ORDER fetch_pending returns across a simulated claim→mark loop, never a re-implementation
of the SQL.
"""
from __future__ import annotations

import json
import math
import sqlite3

from src.config import runtime_cities_by_name
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.state.db import init_schema

# All target a FUTURE local day so the timeliness floor admits every event; the
# fairness property is about cross-city ordering, not timeliness.
_DECISION_TIME = "2026-06-11T12:00:00+00:00"
_TARGET_DATE = "2026-06-12"

# The fetch_pending timeliness floor (_is_timely) fails CLOSED on a city with no
# runtime timezone — so the fairness fixtures must use REAL configured cities, not
# synthetic names. These are the live cities the throughput incident was measured
# against.
_REAL_CITIES = sorted(runtime_cities_by_name().keys())


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _tradeable_fsr(
    city: str,
    snapshot_id: str,
    *,
    available_at: str,
    received_at: str,
    target_date: str = _TARGET_DATE,
):
    """A COMPLETE + LIVE_ELIGIBLE FSR for ``city`` — Tier 1 tradeable."""
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
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
        entity_key=f"{city}|{target_date}|high|{snapshot_id}",
        source="forecast_snapshot_ready_trigger",
        observed_at="2026-06-11T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=100,  # PRIORITY_TRADEABLE
    )


def _bulk_tier2_fsr(city: str, snapshot_id: str, *, available_at: str, received_at: str):
    """A NOT-COMPLETE / NOT-LIVE_ELIGIBLE FSR — falls to the claim tier ELSE
    branch (Tier 2), strictly below a tradeable Tier-1 FSR. Stands in for the
    bulk lane that shares the queue with tradeable decision events."""
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date=_TARGET_DATE,
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
        completeness_status="PARTIAL_ALLOWED",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="PARTIAL_ALLOWED",
        # NOT COMPLETE + NOT LIVE_ELIGIBLE -> claim tier ELSE (Tier 2).
        coverage_completeness_status="INCOMPLETE",
        coverage_readiness_status="NOT_LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{_TARGET_DATE}|high|{snapshot_id}",
        source="forecast_snapshot_ready_trigger",
        observed_at="2026-06-11T00:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=0,  # PRIORITY_FORECAST_INCOMPLETE
    )


def _city_of(event) -> str:
    return event.entity_key.split("|", 1)[0]


def _run_one_cycle(store: EventStore, *, budget_k: int, claim_clock: str) -> list[str]:
    """Simulate ONE reactor cycle: fetch up to K admissible events in claim order,
    claim + mark_processed each (a successful decision), and return the cities
    reached this cycle in claim order."""
    events = store.fetch_pending(decision_time=_DECISION_TIME, limit=budget_k)
    reached: list[str] = []
    for event in events:
        claimed = store.claim(event.event_id, claimed_at=claim_clock)
        assert claimed, "claim must succeed for a freshly-fetched pending event"
        store.mark_processed(event.event_id, processed_at=claim_clock)
        reached.append(_city_of(event))
    return reached


def _insert_recapture_edge_reversed_regret(
    conn: sqlite3.Connection,
    event,
    *,
    created_at: str,
) -> None:
    payload = json.loads(event.payload_json)
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            executable_snapshot_id, created_at, schema_version
        ) VALUES (?, ?, 'EXECUTION_RECEIPT', ?, 'SUBMIT_RECAPTURE',
                  ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            "recapture-edge-" + event.event_id,
            event.event_id,
            "SUBMIT_ABORTED_EDGE_REVERSED:recaptured robust marginal utility nonpositive",
            created_at,
            str(payload.get("city") or ""),
            str(payload.get("target_date") or ""),
            str(payload.get("metric") or ""),
            "|".join(
                (
                    str(payload.get("city") or ""),
                    str(payload.get("target_date") or ""),
                    str(payload.get("metric") or ""),
                )
            ),
            event.causal_snapshot_id,
            "ems2-fresh-book",
            created_at,
        ),
    )


def test_every_city_reached_within_ceil_n_over_k_cycles():
    """RELATIONSHIP (the antibody): N cities each with ONE pending tradeable FSR, a
    per-cycle budget of K < N. Within ceil(N/K) cycles EVERY city must have been
    decided exactly once — the queue interleaves cities instead of draining the
    freshest few. The freshness-greedy legacy order would re-pick the same K
    cities every cycle (their FSR stays rank-1) and never reach the tail."""
    conn = _world_conn()
    store = EventStore(conn)

    n_cities = 12
    budget_k = 4
    # Deliberately ADVERSARIAL available_at: later cities get the NEWEST available_at,
    # so under the legacy "available_at DESC" greedy order they would always be
    # claimed first and the earlier cities would starve. Fairness must defeat this
    # regardless of the freshness skew. Real configured cities (timeliness floor
    # requires a resolvable tz).
    cities = _REAL_CITIES[:n_cities]
    for rank, city in enumerate(cities):
        # rank 0 -> oldest available_at, rank n-1 -> newest. ALL strictly before the
        # decision time (12:00Z) so the available_at<=decision_time floor admits
        # every event; the freshness skew lives in the minute field.
        store.insert_or_ignore(
            _tradeable_fsr(
                city,
                f"snap-{city}",
                available_at=f"2026-06-11T06:{rank:02d}:00+00:00",
                received_at=f"2026-06-11T06:{rank:02d}:30+00:00",
            )
        )

    max_cycles = math.ceil(n_cities / budget_k)
    reached_all: set[str] = set()
    cities_per_cycle: list[list[str]] = []
    for cycle in range(max_cycles):
        reached = _run_one_cycle(
            store,
            budget_k=budget_k,
            claim_clock=f"2026-06-11T12:0{cycle}:00+00:00",
        )
        cities_per_cycle.append(reached)
        # Each cycle must reach DISTINCT cities (one event per city before any
        # city's second event) and never re-decide an already-decided city.
        assert len(reached) == len(set(reached)), (
            f"cycle {cycle} claimed the same city twice: {reached}"
        )
        assert reached_all.isdisjoint(reached), (
            f"cycle {cycle} re-decided an already-decided city: "
            f"{reached_all & set(reached)}"
        )
        reached_all.update(reached)

    assert reached_all == set(cities), (
        f"after ceil({n_cities}/{budget_k})={max_cycles} cycles, these cities were "
        f"NEVER decided: {sorted(set(cities) - reached_all)} "
        f"(per-cycle claims: {cities_per_cycle})"
    )


def test_freshness_skew_does_not_let_newest_cities_monopolise_the_budget():
    """RELATIONSHIP: even when one city has MANY fresh events and 11 others have one
    each, a budget of K must still reach K distinct cities in the FIRST cycle — the
    multi-event city does not consume more than one budget slot per cycle. This is
    the exact live failure mode (a few cities' frequent refreshes ate the budget)."""
    conn = _world_conn()
    store = EventStore(conn)

    # One "noisy" city with 20 fresh FSR events (newest available_at), plus 11
    # single-event cities with older available_at. Real configured cities.
    noisy_city = _REAL_CITIES[0]
    quiet_cities = _REAL_CITIES[1:12]
    for seq in range(20):
        store.insert_or_ignore(
            _tradeable_fsr(
                noisy_city,
                f"snap-noisy-{seq:02d}",
                available_at=f"2026-06-11T11:{seq:02d}:00+00:00",  # newest
                received_at=f"2026-06-11T11:{seq:02d}:30+00:00",
            )
        )
    for city in quiet_cities:
        store.insert_or_ignore(
            _tradeable_fsr(
                city,
                f"snap-{city}",
                available_at="2026-06-11T06:00:00+00:00",  # older than Noisy
                received_at="2026-06-11T06:01:00+00:00",
            )
        )

    budget_k = 6
    reached = _run_one_cycle(
        store, budget_k=budget_k, claim_clock="2026-06-11T12:00:00+00:00"
    )
    # First cycle reaches K DISTINCT cities (NoisyCity at most once), not 6 copies
    # of NoisyCity.
    assert len(set(reached)) == budget_k, (
        f"budget of {budget_k} must reach {budget_k} distinct cities, got {reached}"
    )
    assert reached.count(noisy_city) <= 1, (
        f"the noisy city consumed more than one budget slot in a cycle: {reached}"
    )


def test_recent_recapture_edge_reversed_family_backoff_reaches_other_entry_family():
    """A just-failed submit recapture is terminal for that event, but the next
    ordinary FSR for the same family should not immediately consume the next
    bounded entry slot before other families are reached.

    This is queue feedback only: candidate math is unchanged, and only ordinary
    fresh-entry FSR rows are delayed for the short live backoff window.
    """
    conn = _world_conn()
    store = EventStore(conn)

    cooled_city, open_city = _REAL_CITIES[:2]
    cooled = _tradeable_fsr(
        cooled_city,
        "snap-cooled",
        available_at="2026-06-11T06:00:00+00:00",
        received_at="2026-06-11T06:00:30+00:00",
    )
    open_event = _tradeable_fsr(
        open_city,
        "snap-open",
        available_at="2026-06-11T06:00:00+00:00",
        received_at="2026-06-11T06:00:30+00:00",
    )
    store.insert_or_ignore(cooled)
    store.insert_or_ignore(open_event)
    _insert_recapture_edge_reversed_regret(
        conn,
        cooled,
        created_at="2026-06-11T11:57:00+00:00",
    )

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)

    assert [event.event_id for event in returned] == [open_event.event_id]


def test_recapture_edge_backoff_expires_without_permanent_family_suppression():
    conn = _world_conn()
    store = EventStore(conn)

    cooled_city = _REAL_CITIES[0]
    cooled = _tradeable_fsr(
        cooled_city,
        "snap-cooled",
        available_at="2026-06-11T06:00:00+00:00",
        received_at="2026-06-11T06:00:30+00:00",
    )
    store.insert_or_ignore(cooled)
    _insert_recapture_edge_reversed_regret(
        conn,
        cooled,
        created_at="2026-06-11T11:30:00+00:00",
    )

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)

    assert [event.event_id for event in returned] == [cooled.event_id]


def test_bulk_lane_never_starves_a_tradeable_decision():
    """RELATIONSHIP: a FLOOD of Tier-2 bulk events (incomplete FSR) — even with the
    NEWEST available_at and far more of them than the budget — must NEVER be claimed
    ahead of a single tradeable (Tier-1) FSR. The per-city round-robin operates
    WITHIN a tier, so it can never promote a bulk-lane event past a decision-lane
    one. A budget of 1 must claim the tradeable FSR first."""
    conn = _world_conn()
    store = EventStore(conn)

    # 200 bulk Tier-2 events, all NEWER than the tradeable FSR, across many real
    # cities. The tradeable Tier-1 FSR goes to a DISTINCT city so the only thing that
    # can promote it is tier dominance, not a same-city effect.
    bulk_cities = _REAL_CITIES[1:41]
    for seq in range(200):
        store.insert_or_ignore(
            _bulk_tier2_fsr(
                bulk_cities[seq % len(bulk_cities)],
                f"snap-bulk-{seq:03d}",
                available_at=f"2026-06-11T11:{seq % 60:02d}:00+00:00",  # newest
                received_at=f"2026-06-11T11:{seq % 60:02d}:30+00:00",
            )
        )
    tradeable = _tradeable_fsr(
        _REAL_CITIES[0],
        "snap-trade",
        available_at="2026-06-11T06:00:00+00:00",  # OLDER than the bulk flood
        received_at="2026-06-11T06:01:00+00:00",
    )
    store.insert_or_ignore(tradeable)

    # Budget of exactly 1: the single slot MUST go to the tradeable Tier-1 FSR.
    claimed = store.fetch_pending(decision_time=_DECISION_TIME, limit=1)
    assert len(claimed) == 1
    assert claimed[0].event_id == tradeable.event_id, (
        "a Tier-2 bulk flood (newer available_at, 200 events) starved the single "
        "tradeable Tier-1 FSR — tier dominance was violated by the round-robin"
    )

    # And across a full budget of K, every Tier-1 event is claimed before ANY
    # Tier-2 event (tier dominance holds for the whole claimed page).
    page = store.fetch_pending(decision_time=_DECISION_TIME, limit=10)

    def _is_tradeable(event) -> bool:
        payload = json.loads(event.payload_json)
        return (
            payload.get("coverage_completeness_status") == "COMPLETE"
            and payload.get("coverage_readiness_status") == "LIVE_ELIGIBLE"
        )

    tiers_complete = ["COMPLETE" if _is_tradeable(e) else "BULK" for e in page]
    # The only Tier-1 event is the tradeable one; it must be at index 0.
    assert tiers_complete[0] == "COMPLETE", (
        f"the tradeable Tier-1 FSR must lead the claimed page, got {tiers_complete}"
    )
    assert tiers_complete.count("COMPLETE") == 1


def test_within_city_freshness_order_is_preserved():
    """SEMANTICS-UNCHANGED pin: the round-robin reorders ACROSS cities but the
    WITHIN-city order is still freshest-first. For a single city's events, the
    fresher available_at is still claimed before the staler one."""
    conn = _world_conn()
    store = EventStore(conn)
    solo_city = _REAL_CITIES[0]
    older = _tradeable_fsr(
        solo_city,
        "snap-older",
        available_at="2026-06-11T06:00:00+00:00",
        received_at="2026-06-11T06:01:00+00:00",
    )
    newer = _tradeable_fsr(
        solo_city,
        "snap-newer",
        available_at="2026-06-11T11:00:00+00:00",
        received_at="2026-06-11T11:01:00+00:00",
    )
    store.insert_or_ignore(older)
    store.insert_or_ignore(newer)

    claimed = store.fetch_pending(decision_time=_DECISION_TIME, limit=2)
    assert [e.event_id for e in claimed] == [newer.event_id, older.event_id], (
        "within a single city the fresher (newer available_at) event must still be "
        "claimed first — round-robin must not invert intra-city freshness"
    )


# ---------------------------------------------------------------------------
# 2026-06-11 ~16:30Z operator follow-up: stale-target flooding antibodies.
# The within-(tier, city) order is target_date DESC FIRST, available_at DESC
# second — so the live-eligible day-ahead class always precedes the same-day
# (day0-shadow-scope) class no matter how frequently the same-day snapshots
# refresh with newer available_at.
# ---------------------------------------------------------------------------

def test_day_ahead_target_precedes_fresher_same_day_within_city():
    """ANTIBODY: a city holding a FRESH same-day-target FSR (decisions are
    day0-scope => deterministic no-submit under unsupported scopes) and an
    OLDER day-ahead FSR must yield the day-ahead FIRST. target_date DESC dominates
    available_at DESC within (tier, city) — the same-day refresh churn cannot
    starve the live-eligible day-ahead candidate."""
    conn = _world_conn()
    store = EventStore(conn)
    city = _REAL_CITIES[0]
    # Same-day target (2026-06-11 at the 12:00Z decision time) with the NEWEST
    # available_at — the flooding class.
    same_day_fresh = _tradeable_fsr(
        city,
        "snap-sameday",
        target_date="2026-06-11",
        available_at="2026-06-11T11:30:00+00:00",
        received_at="2026-06-11T11:31:00+00:00",
    )
    # Day-ahead target with an OLDER available_at — the class that must win.
    day_ahead_older = _tradeable_fsr(
        city,
        "snap-dayahead",
        target_date="2026-06-12",
        available_at="2026-06-11T00:00:00+00:00",
        received_at="2026-06-11T00:01:00+00:00",
    )
    store.insert_or_ignore(same_day_fresh)
    store.insert_or_ignore(day_ahead_older)

    claimed = store.fetch_pending(
        decision_time=_DECISION_TIME, limit=1, day0_is_tradeable=False
    )
    assert len(claimed) == 1
    assert claimed[0].event_id == day_ahead_older.event_id, (
        "the older day-ahead FSR must be claimed before the fresher same-day FSR "
        "(target_date DESC dominates available_at DESC within the city)"
    )


def test_fresh_day0_event_does_not_precede_older_day_ahead_fsr():
    """ANTIBODY (operator-specified shape): a city holding a FRESH day0 event and
    an OLDER day-ahead FSR yields the day-ahead first under unsupported scopes
    (day0_is_tradeable=False => day0 is Tier 2, below the tradeable FSR Tier 1).
    Day0 events still process AFTER the live ones — never dropped (the shadow
    evaluation needs the receipts)."""
    from src.events.event_priority import PRIORITY_DAY0_NON_TRADEABLE
    from src.events.opportunity_event import (
        Day0ExtremeUpdatedPayload,
        make_day0_extreme_updated_event,
    )

    conn = _world_conn()
    store = EventStore(conn)
    city = _REAL_CITIES[0]
    day0_payload = Day0ExtremeUpdatedPayload(
        city=city,
        target_date="2026-06-11",
        metric="high",
        settlement_source="metar",
        station_id="st0",
        observation_time="2026-06-11T11:45:00+00:00",
        observation_available_at="2026-06-11T11:45:00+00:00",
        raw_value=30.0,
        rounded_value=30,
        high_so_far=30.0,
        low_so_far=20.0,
    )
    day0_fresh = make_day0_extreme_updated_event(
        entity_key=f"{city}|2026-06-11|high|st0",
        source="day0_extreme_updated_trigger",
        observed_at="2026-06-11T11:45:00+00:00",
        received_at="2026-06-11T11:46:00+00:00",
        payload=day0_payload,
        causal_snapshot_id="ctx-st0",
        priority=PRIORITY_DAY0_NON_TRADEABLE,
    )
    day_ahead_older = _tradeable_fsr(
        city,
        "snap-dayahead-vs-day0",
        target_date="2026-06-12",
        available_at="2026-06-11T00:00:00+00:00",
        received_at="2026-06-11T00:01:00+00:00",
    )
    store.insert_or_ignore(day0_fresh)
    store.insert_or_ignore(day_ahead_older)

    claimed = store.fetch_pending(
        decision_time=_DECISION_TIME, limit=2, day0_is_tradeable=False
    )
    assert [e.event_id for e in claimed] == [day_ahead_older.event_id, day0_fresh.event_id], (
        "day-ahead FSR (Tier 1) must precede the fresh day0 shadow event (Tier 2); "
        "the day0 event must still be CLAIMABLE after it (processed, not dropped)"
    )
