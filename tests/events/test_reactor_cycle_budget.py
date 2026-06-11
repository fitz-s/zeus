# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: consolidated timeliness/tradeability fix (architect design) STEP 8 (E1) —
#                  per-cycle wall-clock budget in reactor.process_pending.
"""RED→GREEN T6: process_pending honors a per-cycle wall-clock budget.

Efficiency E1: a single reactor cycle must not run unbounded. With a per-event
delay greater than the budget, process_pending must break after the first event
once the budget is exceeded and leave the remaining events PENDING (not consumed,
not dropped) for the next cycle.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import OpportunityEventReactor, ReactorConfig
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger


def _payload(snapshot_id: str) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-05",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-04T04:10:00+00:00",
        available_at="2026-06-04T04:15:00+00:00",
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


def _event(snapshot_id: str, available_at: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-06-05|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-04T04:10:00+00:00",
        available_at=available_at,
        received_at=available_at,
        causal_snapshot_id=snapshot_id,
        payload=_payload(snapshot_id),
        priority=0,
    )


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn, EventStore(conn)


def test_process_pending_breaks_after_budget_and_leaves_rest_pending(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv("ZEUS_REACTOR_CYCLE_BUDGET_SECONDS", "0.2")

    conn, store = _store()
    for i in range(4):
        # Decreasing available_at so all are admissible at decision; distinct snapshot ids.
        store.insert_or_ignore(_event(f"snap-{i}", f"2026-06-04T0{i}:00:00+00:00"))

    processed_events: list[str] = []

    def _slow_submit(event, _decision_time):
        # Each event takes 0.3s (> 0.2 budget) — exactly one should run before break.
        time.sleep(0.3)
        processed_events.append(event.event_id)
        from src.events.reactor import EventSubmissionReceipt
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-06-05",
            metric="high",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _d: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_slow_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    decision_time = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    start = time.monotonic()
    result = reactor.process_pending(decision_time=decision_time)
    elapsed = time.monotonic() - start

    # The cycle must NOT process all 4 events (4 * 0.3 = 1.2s >> 0.2 budget).
    # After the first event exceeds the budget, it breaks; the rest stay pending.
    assert elapsed < 1.0, f"process_pending ran past its budget: {elapsed:.2f}s"
    pending_after = store.fetch_pending(
        decision_time=decision_time.astimezone(timezone.utc).isoformat(), limit=100
    )
    assert len(pending_after) >= 1, "remaining events must be left PENDING for the next cycle"
    assert result.processed + result.rejected + result.dead_lettered < 4, (
        "budget must stop the cycle before all events are consumed"
    )


def test_no_decision_path_calls_openmeteo_fetch_directly():
    """The decision path must not invoke openmeteo_client.fetch directly — that
    client's Retry-After handling can time.sleep on the mutex-held decision path.
    Mainstream points must come from a warm cache (STEP 7), never a synchronous
    fetch inside process_pending / the reactor adapter proof path."""
    import inspect

    import src.events.reactor as reactor_mod

    src = inspect.getsource(reactor_mod)
    assert "openmeteo_client.fetch" not in src and ".fetch(" not in src.replace(
        "fetch_pending", ""
    ).replace("fetch_mainstream", ""), (
        "reactor decision path must not call a blocking openmeteo fetch directly"
    )


def test_pre_event_budget_check_stops_before_claiming_next_event(monkeypatch):
    """CADENCE GUARD (2026-06-11): the budget is also checked BEFORE each event.

    The post-event check cannot interrupt a long event mid-flight (live: a
    22-candidate family decision ran p99=59s vs a 30s budget), and a slow
    fetch_pending can consume the budget before the first event even starts.
    When the budget is ALREADY exhausted at loop entry, process_pending must
    return WITHOUT processing any event — the old post-event-only check would
    have processed one full (possibly 60s+) event first, extending the overrun.
    """
    from datetime import datetime, timezone

    monkeypatch.setenv("ZEUS_REACTOR_CYCLE_BUDGET_SECONDS", "0.05")

    conn, store = _store()
    for i in range(3):
        store.insert_or_ignore(_event(f"snap-pre-{i}", f"2026-06-04T0{i}:00:00+00:00"))

    # Slow fetch: consumes the whole budget BEFORE the first event is processed.
    original_fetch = store.fetch_pending

    def _slow_fetch(**kwargs):
        time.sleep(0.1)  # > 0.05 budget
        return original_fetch(**kwargs)

    store.fetch_pending = _slow_fetch  # type: ignore[method-assign]

    processed_events: list[str] = []

    def _submit(event, _decision_time):
        processed_events.append(event.event_id)
        return None

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _d: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    decision_time = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    result = reactor.process_pending(decision_time=decision_time)

    assert processed_events == [], (
        "budget exhausted before the first event => the pre-event check must stop "
        "the cycle without claiming/processing ANY event"
    )
    assert result.processed == 0 and result.rejected == 0 and result.dead_lettered == 0
    # Every event stays PENDING for the next cycle.
    statuses = [
        row[0]
        for row in conn.execute(
            "SELECT processing_status FROM opportunity_event_processing"
        ).fetchall()
    ]
    assert statuses and all(s == "pending" for s in statuses)
