# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: ALWAYS-DECIDABLE invariant (operator law 2026-06-12 verbatim:
#   "rule1在任何情况下都生效…从来都不应该有一个找不到机会的时间点出现。这就是系统设计问题").
#   RULE 1 — a tradeable opportunity exists at EVERY instant; a no-opportunity window is OUR
#   design defect. The reactor used to classify a transient SUBSTRATE block and requeue it forever
#   (until horizon) WITHOUT ever making the substrate fresh — the decision-time refresher lived
#   PAST the gate and was never reached for an event blocked AT the gate. These RELATIONSHIP TESTS
#   (cross-module: reactor classification -> refresher invocation -> next-cycle processing) pin the
#   K-decision: a transient substrate block triggers that substrate's refresh as part of the SAME
#   handling, so requeue-without-refresh-attempt is structurally impossible for refreshable classes.
"""Relationship tests for the ALWAYS-DECIDABLE invariant (Builds 1 + 2).

These verify a cross-module PROPERTY across the reactor->refresher boundary, not a single function:
when the reactor classifies an event as a transient substrate block, the SAME handling invokes the
substrate refresher BEFORE the event can be re-decided, and the refresh runs OUTSIDE any open world
write transaction (three-phase law).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import (
    EventSubmissionReceipt,
    OpportunityEventReactor,
    ReactorConfig,
    ReactorResult,
    _FAMILY_REFRESH_DEBOUNCE_SECONDS,
)
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


def _forecast_event(city: str = "Chicago", metric: str = "high", target_date: str = "2026-05-24"):
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        source_id="opendata",
        source_run_id="run-1",
        cycle="00",
        track="live",
        snapshot_id=f"snap-{city}-{metric}",
        snapshot_hash=f"hash-{city}-{metric}",
        captured_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{target_date}|{metric}",
        source="opendata",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:01:00+00:00",
        payload=payload,
        causal_snapshot_id=f"snap-{city}-{metric}",
    )


def _status(conn: sqlite3.Connection, event_id: str) -> str:
    return conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]


# _DT = 2026-05-24T18:10Z. The block-side bookkeeping tests (substrate-refresh /
# cycle-advance enqueue, recorded at the pre-submit block BEFORE any horizon
# disposition) are horizon-independent and use _DT directly. The two REQUEUE-
# asserting tests pair _DT with a 2026-05-25 target so the event is VENUE-OPEN at
# _DT (PRE_SETTLEMENT_DAY for 05-25) — otherwise the venue-close horizon
# (reactor._venue_market_closed_horizon, 2026-06-13 zero-order reactor-stall fix)
# correctly terminalizes the already-closed family and there is nothing to requeue.
_DT = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)


def _reactor(
    store,
    *,
    snapshot_present: dict,
    refresher=None,
    cycle_advance_enqueuer=None,
    held_family_provider=None,
    submit=lambda _e, _dt: None,
):
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: snapshot_present["v"],
        riskguard_gate=lambda _e: True,
        final_intent_submit=submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
        family_snapshot_refresher=refresher,
        cycle_advance_enqueuer=cycle_advance_enqueuer,
        held_family_provider=held_family_provider,
    )


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST (the invariant): block -> refresher invoked -> then processes
# ---------------------------------------------------------------------------
def test_snapshot_block_invokes_refresher_then_event_processes_next_cycle():
    """The cross-module invariant: an event blocked EXECUTABLE_SNAPSHOT-transient AT the reactor
    gate causes the family refresher to be INVOKED (with the blocked family's identity) as part of
    the SAME cycle's handling; on the NEXT cycle, with the refreshed substrate present, the event
    PROCESSES instead of requeueing forever."""
    conn, store = _store()
    # Target 2026-05-25 so the event (available 2026-05-24T18:01Z) is VENUE-OPEN at
    # the _DT decision time (PRE_SETTLEMENT_DAY for 05-25): the family is genuinely
    # tradeable, so a transient block must REQUEUE, not terminalize at the
    # venue-close horizon. (A 05-24 target would close at 12:00Z 05-24 — already
    # POST_TRADING at 18:10Z — and correctly dead-letter instead of requeue.)
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    present = {"v": False}
    calls: list[tuple] = []

    def _refresher(*, city, target_date, metric, **_kw):
        calls.append((city, target_date, metric))
        # The refresher's effect: the substrate becomes fresh for the next cycle.
        present["v"] = True
        return True

    reactor = _reactor(store, snapshot_present=present, refresher=_refresher)

    # Cycle 1: blocked -> requeued, and the refresher was invoked for THIS family.
    r1 = reactor.process_pending(decision_time=_DT)
    assert r1.retried == 1
    assert r1.processed == 0
    assert _status(conn, event.event_id) == "pending"
    assert calls == [("Chicago", "2026-05-25", "high")]
    assert r1.snapshot_refreshes == 1

    # Cycle 2 after the retry floor: substrate now fresh -> the event PROCESSES
    # (no longer a no-opportunity window).
    r2 = reactor.process_pending(decision_time=_DT + timedelta(seconds=61))
    assert r2.processed == 1
    assert _status(conn, event.event_id) == "processed"


def test_refresher_failure_event_still_requeues_failsoft_one_warning(caplog):
    """Fail-soft: a refresher that RAISES must not break the cycle — the event still requeues
    (horizon-bounded) and exactly ONE warning is logged for the failed refresh."""
    conn, store = _store()
    # Target 2026-05-25 -> venue-open at _DT (see the companion test): a transient
    # block must REQUEUE, not terminalize at the venue-close horizon.
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    present = {"v": False}

    def _refresher(*, city, target_date, metric, **_kw):
        raise RuntimeError("clob /book fetch boom")

    reactor = _reactor(store, snapshot_present=present, refresher=_refresher)
    with caplog.at_level(logging.WARNING, logger="zeus.events.reactor"):
        r = reactor.process_pending(decision_time=_DT)

    assert r.retried == 1
    assert r.snapshot_refreshes == 0  # the refresh failed -> not counted
    assert _status(conn, event.event_id) == "pending"  # still requeued, never dead-lettered here
    refresh_warnings = [
        rec for rec in caplog.records
        if "always-decidable" in rec.getMessage() and "refresh failed" in rec.getMessage()
    ]
    assert len(refresh_warnings) == 1


def test_debounce_two_blocks_same_family_within_window_one_refresh_call():
    """DEBOUNCE: two transient blocks of the SAME family within the debounce window (derived from
    the snapshot freshness window, NOT a magic number) trigger exactly ONE refresh call — the
    second is skipped because the first capture is still fresh."""
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    present = {"v": False}  # stays blocked so it re-blocks each cycle
    calls: list[tuple] = []

    def _refresher(*, city, target_date, metric, **_kw):
        calls.append((city, target_date, metric))
        return True  # capture succeeded but the gate fixture keeps it blocked

    reactor = _reactor(store, snapshot_present=present, refresher=_refresher)

    # The debounce key is monotonic-clock based; two cycles in immediate succession are well
    # within _FAMILY_REFRESH_DEBOUNCE_SECONDS, so the family is refreshed once.
    assert _FAMILY_REFRESH_DEBOUNCE_SECONDS >= 1.0
    reactor.process_pending(decision_time=_DT)
    reactor.process_pending(decision_time=_DT)
    assert calls == [("Chicago", "2026-05-24", "high")]


def test_gate_positive_event_does_not_call_family_refresher_before_decision():
    """A gate-positive event must not pay the full-family refresh cost before decision.

    Presence/family-identity freshness is proven by the executable snapshot gate. If the
    selected row later proves price-stale, the adapter owns the targeted refresh at the
    stale-price boundary. True gate misses refresh through the blocked-event drain.
    """

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    calls: list[tuple] = []

    def _refresher(*, city, target_date, metric, **_kw):
        calls.append((city, target_date, metric))
        return True

    reactor = _reactor(store, snapshot_present={"v": True}, refresher=_refresher)

    reactor.process_pending(decision_time=_DT)

    assert calls == []


def test_fanout_no_drop_cap_all_recorded_families_refreshed_in_one_drain():
    """FAN-OUT — NO DROP-CAP: when MANY families are recorded as blocked in a single cycle, the
    end-of-cycle drain refreshes EVERY one of them (no numeric cap on the candidate set). We drive
    the recorded set directly (the store's per-cycle round-robin is a separate concern tested
    elsewhere) and assert the drain covers all of them."""
    conn, store = _store()
    present = {"v": False}
    seen: list[tuple] = []

    def _refresher(*, city, target_date, metric, **_kw):
        seen.append((city, metric))
        return True

    reactor = _reactor(store, snapshot_present=present, refresher=_refresher)
    families = [
        ("Chicago", "2026-05-24", "high"),
        ("Denver", "2026-05-24", "high"),
        ("Miami", "2026-05-24", "low"),
        ("Seoul", "2026-05-24", "high"),
        ("Tokyo", "2026-05-24", "low"),
    ]
    reactor._pending_snapshot_refreshes = list(families)
    reactor._pending_cycle_advances = []
    res = ReactorResult()
    reactor._drain_substrate_refreshes(result=res)
    assert {(c, m) for c, _d, m in families} == {(c, m) for c, m in seen}
    assert res.snapshot_refreshes == len(families)


def test_fanout_cursor_rotates_order_across_cycles_no_family_always_first():
    """FAIR-CURSOR: the refresh ORDER rotates across cycles so no single family is permanently
    refreshed first (within value tiers, when ordering is enabled, the cursor rotates inside the
    tier). With a flat recorded set the head advances by one each drain."""
    conn, store = _store()
    present = {"v": False}
    order_log: list[list[str]] = []

    def _refresher(*, city, target_date, metric, **_kw):
        order_log[-1].append(city)
        return True

    reactor = _reactor(store, snapshot_present=present, refresher=_refresher)
    families = [
        ("Chicago", "2026-05-24", "high"),
        ("Denver", "2026-05-24", "high"),
        ("Miami", "2026-05-24", "low"),
    ]
    # Three drains; debounce would suppress repeats, so use a fresh last_at each drain to observe
    # pure ordering (the cursor advances regardless of debounce).
    firsts = []
    for _ in range(3):
        reactor._pending_snapshot_refreshes = list(families)
        reactor._pending_cycle_advances = []
        reactor._family_refresh_last_at.clear()
        order_log.append([])
        reactor._drain_substrate_refreshes(result=ReactorResult())
        firsts.append(order_log[-1][0])
    # The family refreshed FIRST changes across cycles (cursor rotation) — not the same every time.
    assert len(set(firsts)) > 1


def test_no_network_inside_world_savepoint_refresh_runs_with_no_open_txn():
    """NO-NETWORK-IN-TXN (three-phase law, structural): the refresher (which does /book network
    I/O in production) MUST be invoked with NO open world transaction on the store conn. We assert
    structurally: at the moment the refresher runs, store.conn.in_transaction is False (the
    per-event unit-of-work already committed + released its savepoint, and the end-of-cycle drain
    runs outside any txn)."""
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    present = {"v": False}
    txn_open_at_refresh: list[bool] = []

    def _refresher(*, city, target_date, metric, **_kw):
        txn_open_at_refresh.append(bool(getattr(store.conn, "in_transaction", False)))
        return True

    reactor = _reactor(store, snapshot_present=present, refresher=_refresher)
    reactor.process_pending(decision_time=_DT)
    assert txn_open_at_refresh == [False]  # refresh ran with NO open world txn


def test_held_position_families_refreshed_first_no_liquidity_ordering():
    """ORDERING (operator correction 2026-06-12): held-position families (money at risk) are
    refreshed FIRST; the rest follow in liquidity-BLIND fair rotation. No volume/liquidity signal
    participates in ordering at all."""
    conn, store = _store()
    present = {"v": False}
    order: list[tuple] = []

    def _refresher(*, city, target_date, metric, **_kw):
        order.append((city, metric))
        return True

    held = frozenset({("Tokyo", "2026-05-24", "low")})  # the LAST family alphabetically, but held
    reactor = _reactor(
        store,
        snapshot_present=present,
        refresher=_refresher,
        held_family_provider=lambda: held,
    )
    reactor._pending_snapshot_refreshes = [
        ("Chicago", "2026-05-24", "high"),
        ("Denver", "2026-05-24", "high"),
        ("Tokyo", "2026-05-24", "low"),  # held
    ]
    reactor._pending_cycle_advances = []
    reactor._drain_substrate_refreshes(result=ReactorResult())
    # Held family is refreshed FIRST despite being last in the recorded order.
    assert order[0] == ("Tokyo", "low")
    # All families still covered (held-first is ordering, never filtering).
    assert {(c, m) for c, m in order} == {("Chicago", "high"), ("Denver", "high"), ("Tokyo", "low")}


def test_held_provider_failsoft_absent_or_raising_yields_pure_rotation():
    """The held-position provider is fail-soft: a provider that RAISES yields no held bias (pure
    fair rotation) and never breaks the drain."""
    conn, store = _store()
    present = {"v": False}
    order: list[tuple] = []

    def _refresher(*, city, target_date, metric, **_kw):
        order.append((city, metric))
        return True

    def _bad_provider():
        raise RuntimeError("trades DB unreachable")

    reactor = _reactor(
        store,
        snapshot_present=present,
        refresher=_refresher,
        held_family_provider=_bad_provider,
    )
    reactor._pending_snapshot_refreshes = [
        ("Chicago", "2026-05-24", "high"),
        ("Denver", "2026-05-24", "high"),
    ]
    reactor._pending_cycle_advances = []
    reactor._drain_substrate_refreshes(result=ReactorResult())  # must not raise
    assert {(c, m) for c, m in order} == {("Chicago", "high"), ("Denver", "high")}


# ---------------------------------------------------------------------------
# BUILD 2: posterior-staleness block -> single-family cycle-advance enqueue
# ---------------------------------------------------------------------------
def test_posterior_stale_block_enqueues_single_family_cycle_advance():
    """A family blocked because its replacement posterior is STALE/absent (the adapter raises
    REPLACEMENT_0_1_LIVE_BUNDLE_BLOCKED) records a SINGLE-FAMILY cycle-advance enqueue
    for THAT family — the belief substrate gets re-materialized rather than requeueing forever
    against an unchanging posterior."""
    payload = json.loads(_forecast_event().payload_json)
    enqueues: list[tuple] = []

    def _submit(event, _dt):
        # Post-submit receipt carrying the bundle-blocked reason (the adapter's readiness/bundle
        # gate raise surfaces here as the receipt reason).
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            side_effect_status="NO_SUBMIT",
            reason="REPLACEMENT_0_1_LIVE_BUNDLE_BLOCKED:READINESS_STALE",
        )

    def _enqueuer(*, city, target_date, metric):
        enqueues.append((city, target_date, metric))
        return True

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor = _reactor(
        store,
        snapshot_present={"v": True},  # snapshot gate passes; the block is at the posterior
        cycle_advance_enqueuer=_enqueuer,
        submit=_submit,
    )
    r = reactor.process_pending(decision_time=_DT)
    assert enqueues == [("Chicago", "2026-05-24", "high")]
    assert r.cycle_advance_enqueues == 1


def test_posterior_readiness_missing_also_enqueues_cycle_advance():
    """READINESS_MISSING (no posterior at all yet) is also a posterior-substrate block -> enqueue."""
    payload = json.loads(_forecast_event().payload_json)
    enqueues: list[tuple] = []

    def _submit(event, _dt):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            side_effect_status="NO_SUBMIT",
            reason="REPLACEMENT_0_1_LIVE_READINESS_MISSING",
        )

    def _enqueuer(*, city, target_date, metric):
        enqueues.append((city, target_date, metric))
        return True

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor = _reactor(
        store,
        snapshot_present={"v": True},
        cycle_advance_enqueuer=_enqueuer,
        submit=_submit,
    )
    reactor.process_pending(decision_time=_DT)
    assert enqueues == [("Chicago", "2026-05-24", "high")]


def test_live_input_lag_enqueues_single_family_cycle_advance():
    """A served posterior that lags a newer raw live input is a posterior-substrate block.

    The adapter wraps this as a live-inference missing reason chain; the reactor must still
    identify the nested live-input-lag segment and enqueue the same single-family cycle advance.
    """
    payload = json.loads(_forecast_event().payload_json)
    enqueues: list[tuple] = []

    def _submit(event, _dt):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            side_effect_status="NO_SUBMIT",
            reason=(
                "LIVE_INFERENCE_INPUTS_MISSING:"
                "REPLACEMENT_0_1_LIVE_INPUT_LAG:"
                "raw_cycle_after_served_posterior"
            ),
        )

    def _enqueuer(*, city, target_date, metric):
        enqueues.append((city, target_date, metric))
        return True

    _conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor = _reactor(
        store,
        snapshot_present={"v": True},
        cycle_advance_enqueuer=_enqueuer,
        submit=_submit,
    )
    r = reactor.process_pending(decision_time=_DT)
    assert enqueues == [("Chicago", "2026-05-24", "high")]
    assert r.cycle_advance_enqueues == 1


def test_pre_cutover_posterior_staleness_reason_alias_enqueues_cycle_advance():
    payload = json.loads(_forecast_event().payload_json)
    enqueues: list[tuple] = []

    def _submit(event, _dt):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            side_effect_status="NO_SUBMIT",
            reason="REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_MISSING",
        )

    def _enqueuer(*, city, target_date, metric):
        enqueues.append((city, target_date, metric))
        return True

    _conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor = _reactor(
        store,
        snapshot_present={"v": True},
        cycle_advance_enqueuer=_enqueuer,
        submit=_submit,
    )
    reactor.process_pending(decision_time=_DT)
    assert enqueues == [("Chicago", "2026-05-24", "high")]


def test_non_substrate_reason_does_not_enqueue_cycle_advance():
    """A NON-substrate block (e.g. an honest trade-score reject) must NOT trigger a cycle-advance
    enqueue — only stale/absent posterior reasons do."""
    payload = json.loads(_forecast_event().payload_json)
    enqueues: list[tuple] = []

    def _submit(event, _dt):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            side_effect_status="NO_SUBMIT",
            reason="TRADE_SCORE_NON_POSITIVE",
        )

    def _enqueuer(*, city, target_date, metric):
        enqueues.append((city, target_date, metric))
        return True

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor = _reactor(
        store,
        snapshot_present={"v": True},
        cycle_advance_enqueuer=_enqueuer,
        submit=_submit,
    )
    reactor.process_pending(decision_time=_DT)
    assert enqueues == []
