# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: consolidated timeliness/tradeability fix (architect design) STEP 3 —
#                  EventStore.fetch_pending claim-floor + freshest-target-first ordering.
"""RED→GREEN T2/T3: fetch_pending never returns a past-target event, and
orders admissible events freshest-target-first.

These are the PRIMARY root: the reactor was draining its per-cycle budget on
already-settled June-4 events (oldest available_at first) and never reaching
fresh June-5 candidates. The claim floor + ordering fix the receipt starvation.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.state.db import init_schema


# Chicago tz: local 2026-06-05 begins at 2026-06-05T05:00Z.
# Decision time chosen so 2026-06-04 is a PAST local day and 2026-06-05 is FUTURE.
_DECISION_TIME = "2026-06-05T12:00:00+00:00"  # Chicago local 2026-06-05 07:00


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


def _event(target_date: str, snapshot_id: str, *, available_at: str, received_at: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|{target_date}|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-03T04:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=_payload(target_date, snapshot_id),
        priority=0,
    )


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_past_target_event_never_returned_even_with_oldest_available_at():
    """T2: a past-target (already-settled local day) event is NEVER returned by
    fetch_pending, even though it has the OLDEST available_at (which previously
    sorted it to the front and starved fresh candidates)."""
    conn = _world_conn()
    store = EventStore(conn)
    # Stale: target 2026-06-04 (past local day at decision time), oldest available.
    stale = _event(
        "2026-06-04", "snap-stale",
        available_at="2026-06-03T00:00:00+00:00",
        received_at="2026-06-03T00:01:00+00:00",
    )
    # Fresh: target 2026-06-06 (future local day at decision 2026-06-05),
    # deliberately NEWER available so the OLD ordering would still bury it
    # behind the stale one — proving the floor, not just the ordering.
    fresh = _event(
        "2026-06-06", "snap-fresh",
        available_at="2026-06-04T00:00:00+00:00",
        received_at="2026-06-04T00:01:00+00:00",
    )
    store.insert_or_ignore(stale)
    store.insert_or_ignore(fresh)

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=100)
    ids = [e.event_id for e in returned]
    assert stale.event_id not in ids, "past-target event must never be returned"
    assert fresh.event_id in ids, "fresh-target event must still be returned"


def test_freshest_target_returned_first():
    """T3: with one fresh + one stale-but-admissible event, the freshest
    target_date sorts FIRST so the budget reaches it before exhaustion."""
    conn = _world_conn()
    store = EventStore(conn)
    # Both admissible (future local days), but later available_at on the fresher.
    older_admissible = _event(
        "2026-06-06", "snap-d6",
        available_at="2026-06-03T00:00:00+00:00",  # oldest available
        received_at="2026-06-03T00:01:00+00:00",
    )
    fresher = _event(
        "2026-06-07", "snap-d7",
        available_at="2026-06-04T00:00:00+00:00",  # newer available
        received_at="2026-06-04T00:01:00+00:00",
    )
    store.insert_or_ignore(older_admissible)
    store.insert_or_ignore(fresher)

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=100)
    ids = [e.event_id for e in returned]
    assert ids[0] == fresher.event_id, (
        f"freshest target_date (2026-06-07) must be returned first; got order {ids}"
    )


def test_retry_debt_precedes_zero_attempt_redecision_with_same_target():
    """T3: bounded EDLI proof windows must not be refilled forever by newly
    emitted zero-attempt redecision rows while transient retries sit behind them.

    This preserves freshest-target-first across different target dates, but for
    the same target it lets a candidate whose prior attempt was blocked by a
    transient causality delay get another decision once time advances.
    """
    conn = _world_conn()
    store = EventStore(conn)
    retry_debt = _event(
        "2026-06-06", "snap-retry",
        available_at="2026-06-05T00:00:00+00:00",
        received_at="2026-06-05T00:01:00+00:00",
    )
    brand_new = _event(
        "2026-06-06", "snap-new",
        available_at="2026-06-05T00:00:00+00:00",
        received_at="2026-06-05T11:03:00+00:00",
    )
    store.insert_or_ignore(retry_debt)
    store.insert_or_ignore(brand_new)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET attempt_count = 6, updated_at = '2026-06-05T10:57:00+00:00'
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, retry_debt.event_id),
    )

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=2)
    ids = [e.event_id for e in returned]
    assert ids == [retry_debt.event_id, brand_new.event_id]
