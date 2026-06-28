# Created: 2026-06-12
# Last reused or audited: 2026-06-19
# Authority basis: operator law 2026-06-12 ("no caps of any kind"; "重试次数不是市场
#   事实" — a retry count is not a market fact) + Wave 1 items 1 and 13 of
#   docs/archive/2026-Q2/operations_historical/overengineering_simplification_plan_2026-06-12.md + external
#   consult verdict (BLOCKER: the attempt-cap is a cap disguised as a safety check).
#
#   The reactor used to dead-letter a transient money-path block after
#   MAX_EXECUTABLE_SNAPSHOT_RETRIES=8 attempts with MONEY_PATH_TRANSIENT_EXHAUSTED.
#   An attempt count is NOT a market fact: a live-positive-EV event could be burned
#   because the substrate was unlucky 8 times while the event itself was still
#   timely. This file pins EVENT-HORIZON terminalization: a transient event
#   requeues INDEFINITELY until a semantic terminal fires (timeliness floor past /
#   operator disarm), and never dead-letters by count.
"""RELATIONSHIP tests across the boundary

    reactor transient disposition (_EXECUTABLE_SNAPSHOT_RETRY)
    -> _transient_horizon_terminal (reuses EventStore._is_timely authority)
    -> requeue (no cap) vs MONEY_PATH_HORIZON_EXPIRED terminal

Cross-module invariants:
  * INV(reactor<->event_store): the reactor's transient terminal MUST agree with
    the store's timeliness floor — a transient event terminalizes EXACTLY when
    fetch_pending would stop returning it (same _is_timely authority, no 2nd clock).
  * INV(reactor<->queue): infinite requeue must not starve the queue — a
    perpetually-transient event cannot preempt a fresh event from another city
    (fetch_pending's per-city round-robin is the primary cross-city order).
"""
from __future__ import annotations

import logging
import pathlib
import sqlite3
from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.events.reactor import (
    EventSubmissionReceipt,
    OpportunityEventReactor,
    _is_transient_money_path_reason,
    _regret_bucket_for,
    _substrate_sidecar_owns_broad_refresh,
)
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

# Chicago 2026-06-05 target: strictly-past-in-tz boundary is 2026-06-06T05:00Z
# (city-local midnight of 2026-06-06, CDT=UTC-5). A decision_time BEFORE the
# boundary is TIMELY (requeues forever); AT/AFTER it the timeliness horizon has
# expired.
_DT_TIMELY = datetime(2026, 6, 4, 18, 10, tzinfo=timezone.utc)
_DT_HORIZON_PAST = datetime(2026, 6, 7, 0, 0, tzinfo=timezone.utc)

_PRICE_MOVED_REASON = (
    "SUBMIT_ABORTED_PRICE_MOVED: recaptured all-in cost 0.513552 exceeds "
    "max_acceptable_price 0.502495 + bounded tolerance 0.010000"
)

_SNAPSHOT_STALE_REASON = (
    "EXECUTABLE_SNAPSHOT_STALE:freshness_deadline=2026-06-13T11:28:56+00:00:"
    "decision_time=2026-06-13T14:00:00+00:00"
)

_SELECTION_DEADLINE_STALE_REASON = (
    "EXECUTABLE_SNAPSHOT_STALE:selection_deadline=2026-06-26T01:10:00.180078+00:00:"
    "decision_time=2026-06-26T14:49:42.119056+00:00"
)

# Day0/venue horizon regression (corrected 2026-06-26). Gamma endDate/F1 12:00Z
# is resolution timing, not proof the CLOB is closed. Manila (Asia/Manila, UTC+8)
# target 2026-06-13 remains local-day active until 2026-06-13T16:00:00Z.
_DT_VENUE_CLOSED_NOT_LOCAL_PAST = datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc)
# Before 12:00Z is also local-day active — must NOT terminalize.
_DT_VENUE_OPEN = datetime(2026, 6, 13, 11, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _event(snapshot_id: str, *, city: str = "Chicago", target_date: str = "2026-06-05"):
    payload = _payload(snapshot_id)
    # Allow a different city/target for the fairness test without re-templating
    # the whole payload helper.
    if city != "Chicago" or target_date != "2026-06-05":
        from dataclasses import replace as _replace

        payload = _replace(payload, city=city, target_date=target_date)
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{target_date}|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-04T04:10:00+00:00",
        available_at="2026-06-04T04:15:00+00:00",
        received_at="2026-06-04T04:16:00+00:00",
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=100,
    )


def _manila_event(snapshot_id: str):
    """A Manila 2026-06-13 high forecast-decision event (venue closes 12:00Z, the
    target local day ends at 16:00Z) for the venue-close horizon tests."""
    return _event(snapshot_id, city="Manila", target_date="2026-06-13")


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn, EventStore(conn)


def _status(conn, event_id: str) -> str:
    return conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]


def _reactor_with_reason(
    conn,
    store,
    reason: str,
    *,
    family_market_absence_provider=None,
) -> OpportunityEventReactor:
    """Reactor whose submit always returns a money-path-blocking receipt with
    ``reason`` — the exact route a live PRICE_MOVED receipt takes."""

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-06-05",
            metric="high",
            trade_score_positive=False,
            reason=reason,
        )

    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
        family_market_absence_provider=family_market_absence_provider,
    )


# ---------------------------------------------------------------------------
# 1. No attempt cap: 20+ transient cycles then fresh substrate -> processes
# ---------------------------------------------------------------------------

def test_no_attempt_cap_many_transient_cycles_then_processes():
    """RELATIONSHIP: the substrate is unlucky for 25 consecutive cycles (far past
    the old cap of 8). The event must STILL be pending (never dead-lettered by
    count); when the substrate finally yields a clean submit, it processes.

    This is the operator law made testable: a retry count is not a market fact,
    so 25 unlucky cycles on a still-timely event burn nothing."""
    conn, store = _store()
    event = _event("snap-nocap")
    store.insert_or_ignore(event)

    transient = {"v": True}

    def _submit(ev, _dt):
        if transient["v"]:
            return EventSubmissionReceipt(
                submitted=False,
                proof_accepted=False,
                event_id=ev.event_id,
                causal_snapshot_id=ev.causal_snapshot_id,
                city="Chicago",
                target_date="2026-06-05",
                metric="high",
                trade_score_positive=False,
                reason=_PRICE_MOVED_REASON,
            )
        return None  # clean submit -> proof_accepted

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    for i in range(25):
        result = reactor.process_pending(decision_time=_DT_TIMELY, limit=10)
        assert result.dead_lettered == 0, f"cycle {i}: never dead-letter a timely event by count"
        assert result.retried == 1, f"cycle {i}: timely transient must requeue"
        assert _status(conn, event.event_id) == "pending", (
            f"cycle {i}: 25 unlucky cycles (>3x the old cap) must NOT consume a timely event"
        )

    # Substrate clears -> the SAME event is CONSUMED (it was never burned by the
    # 25 unlucky cycles). A clean legacy submit (None) marks the event terminal
    # processed/accepted; the load-bearing assertion is that the event survived
    # 25 cycles to reach this clean outcome instead of being dead-lettered.
    transient["v"] = False
    result = reactor.process_pending(decision_time=_DT_TIMELY, limit=10)
    assert result.dead_lettered == 0
    assert result.retried == 0
    assert _status(conn, event.event_id) != "pending", (
        "after the substrate clears, the long-requeued event must finally be consumed"
    )


# ---------------------------------------------------------------------------
# 2. Timeliness horizon past -> MONEY_PATH_HORIZON_EXPIRED (not a count label)
# ---------------------------------------------------------------------------

def test_horizon_terminalizes_with_horizon_label():
    """RELATIONSHIP (reactor<->market_phase/event_store): a transient event past its
    market horizon terminalizes with MONEY_PATH_HORIZON_EXPIRED carrying the last
    honest transient cause; never a count.

    For Chicago 2026-06-05 at _DT_HORIZON_PAST (2026-06-07), the local-day
    timeliness horizon is past. Static F1/Gamma endDate timing must not relabel
    it as a venue close."""
    conn, store = _store()
    event = _event("snap-horizon")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    # First, a timely cycle: it must requeue (the event is still in-window when
    # claimed, so the reactor reaches it; the horizon only fires once past).
    reactor.process_pending(decision_time=_DT_TIMELY, limit=10)
    assert _status(conn, event.event_id) == "pending"

    # Now decision_time is past the market horizon. fetch_pending's read floor
    # would drop the event, but it is still 'pending' from the requeue; we drive
    # the reactor directly through _finalize_disposition to prove the explicit
    # horizon terminal fires with the right label.
    reactor._transient_requeue_reasons[event.event_id] = _PRICE_MOVED_REASON
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_HORIZON_PAST,
        result=res,
    )

    assert res.dead_lettered == 1
    assert res.retried == 0
    assert _status(conn, event.event_id) == "dead_letter"

    row = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    failure_stage, error_message = row[0], row[1]
    assert failure_stage == "MONEY_PATH_HORIZON_EXPIRED", (
        f"horizon terminal must be labeled MONEY_PATH_HORIZON_EXPIRED, got {failure_stage!r}"
    )
    assert "TIMELINESS_FLOOR_PAST" in (error_message or ""), error_message
    assert "SUBMIT_ABORTED_PRICE_MOVED" in (error_message or ""), (
        "the horizon dead-letter must carry the last honest transient cause"
    )
    # The regret reason carries the horizon, never an attempt count.
    regret = conn.execute(
        "SELECT rejection_reason FROM no_trade_regret_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert regret is not None
    assert regret[0].startswith("MONEY_PATH_HORIZON_EXPIRED:TIMELINESS_FLOOR_PAST:")
    assert "attempt" not in regret[0].lower()


def test_selection_deadline_past_requeues_stale_snapshot_retry():
    """A selected executable snapshot's deadline is price-evidence freshness, not
    an event horizon. Stale price evidence must requeue for fresh substrate until
    the market/day horizon itself expires."""
    conn, store = _store()
    event = _day0_event(city="New York", target_date="2026-06-26", suffix="stale-deadline")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _SELECTION_DEADLINE_STALE_REASON)

    reactor._transient_requeue_reasons[event.event_id] = _SELECTION_DEADLINE_STALE_REASON
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=datetime(2026, 6, 26, 14, 49, 42, tzinfo=timezone.utc),
        result=res,
    )

    assert res.dead_lettered == 0
    assert res.retried == 1
    assert _status(conn, event.event_id) == "pending"
    row = conn.execute(
        "SELECT last_error, claimed_at FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert str(row[0]).startswith("EXECUTABLE_SNAPSHOT_STALE:")
    assert row[1] is not None
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM event_dead_letters WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        == 0
    )


def test_fresh_substrate_sidecar_owns_broad_refresh(monkeypatch, tmp_path):
    """When the substrate observer sidecar is alive, broad substrate warmup is
    owned outside the reactor. The reactor keeps targeted selected-row refresh,
    but must not duplicate the sidecar's broad writer and lock the live DB."""

    heartbeat = tmp_path / "daemon-heartbeat-substrate-observer.json"
    heartbeat.write_text(
        '{"daemon":"substrate-observer","alive_at":"2026-06-26T15:43:34+00:00","pid":123}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZEUS_SUBSTRATE_OBSERVER_HEARTBEAT_PATH", str(heartbeat))
    monkeypatch.delenv("ZEUS_REACTOR_FORCE_BROAD_SUBSTRATE_DRAIN", raising=False)
    monkeypatch.delenv("ZEUS_SUBSTRATE_SIDECAR_OWNS_BROAD_REFRESH", raising=False)

    assert _substrate_sidecar_owns_broad_refresh(
        now=datetime(2026, 6, 26, 15, 44, 0, tzinfo=timezone.utc)
    )
    assert not _substrate_sidecar_owns_broad_refresh(
        now=datetime(2026, 6, 26, 15, 46, 0, tzinfo=timezone.utc)
    )

    monkeypatch.setenv("ZEUS_REACTOR_FORCE_BROAD_SUBSTRATE_DRAIN", "1")
    assert not _substrate_sidecar_owns_broad_refresh(
        now=datetime(2026, 6, 26, 15, 44, 0, tzinfo=timezone.utc)
    )


def test_sidecar_broad_ownership_still_drains_targeted_blocked_family(monkeypatch, tmp_path):
    """The sidecar owns broad universe warming, not blocked-event targeted recapture."""

    heartbeat = tmp_path / "daemon-heartbeat-substrate-observer.json"
    heartbeat.write_text(
        '{"daemon":"substrate-observer","alive_at":"2026-06-26T15:43:34+00:00","pid":123}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZEUS_SUBSTRATE_OBSERVER_HEARTBEAT_PATH", str(heartbeat))
    monkeypatch.delenv("ZEUS_REACTOR_FORCE_BROAD_SUBSTRATE_DRAIN", raising=False)
    monkeypatch.delenv("ZEUS_SUBSTRATE_SIDECAR_OWNS_BROAD_REFRESH", raising=False)
    monkeypatch.setattr(
        "src.events.reactor.datetime",
        type(
            "FixedDateTime",
            (datetime,),
            {
                "now": classmethod(
                    lambda cls, tz=None: datetime(2026, 6, 26, 15, 44, 0, tzinfo=timezone.utc)
                )
            },
        ),
    )

    conn, store = _store()
    calls: list[tuple[str, str, str]] = []

    def _refresher(*, city, target_date, metric, **_kw):
        calls.append((city, target_date, metric))
        return True

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _dt: None,
        reject=lambda *_a: None,
        regret_ledger=NoTradeRegretLedger(conn),
        family_snapshot_refresher=_refresher,
    )
    reactor._pending_snapshot_refreshes = [("Chicago", "2026-06-26", "high")]
    reactor._pending_cycle_advances = []
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._drain_substrate_refreshes(result=res)

    assert calls == [("Chicago", "2026-06-26", "high")]
    assert res.snapshot_refreshes == 1


def test_timeliness_floor_is_backstop_when_venue_phase_unresolvable(monkeypatch):
    """RELATIONSHIP (reactor<->event_store): TIMELINESS_FLOOR_PAST remains the
    backstop horizon. When the venue-close phase authority cannot resolve (here:
    forced to raise — e.g. an unresolvable tz/date), horizon (b) fails soft and the
    local-day timeliness floor (a) still terminalizes a strictly-past event with the
    honest TIMELINESS_FLOOR_PAST label. The two horizons are independent terminals,
    not one masking the other."""
    conn, store = _store()
    event = _event("snap-timeliness-backstop")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    # Force the venue-close horizon to fail-soft (returns None) so the timeliness
    # floor is exercised as the sole terminal at a strictly-past decision_time.
    monkeypatch.setattr(reactor, "_venue_market_closed_horizon", lambda *_a, **_k: None)

    reactor._transient_requeue_reasons[event.event_id] = _PRICE_MOVED_REASON
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_HORIZON_PAST,
        result=res,
    )

    assert res.dead_lettered == 1
    assert _status(conn, event.event_id) == "dead_letter"
    regret = conn.execute(
        "SELECT rejection_reason FROM no_trade_regret_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert regret is not None
    assert regret[0].startswith("MONEY_PATH_HORIZON_EXPIRED:TIMELINESS_FLOOR_PAST:"), regret[0]
    assert "attempt" not in regret[0].lower()


def test_old_count_based_label_is_gone_from_reactor():
    """ANTIBODY (grep-style): the count-based MONEY_PATH_TRANSIENT_EXHAUSTED label
    and the MAX_EXECUTABLE_SNAPSHOT_RETRIES cap are GONE from the reactor source.
    A re-introduction (someone re-adds an attempt cap) trips this test."""
    import src.events.reactor as reactor_mod

    src = pathlib.Path(reactor_mod.__file__).read_text()
    assert "MONEY_PATH_TRANSIENT_EXHAUSTED" not in src, (
        "the count-based exhaustion label must not appear in the reactor; "
        "terminalization is by EVENT HORIZON, not attempt count"
    )
    assert "MAX_EXECUTABLE_SNAPSHOT_RETRIES" not in src, (
        "the attempt-cap constant must be gone (operator law: a retry count is "
        "not a market fact)"
    )
    assert not hasattr(reactor_mod, "MAX_EXECUTABLE_SNAPSHOT_RETRIES")


# ---------------------------------------------------------------------------
# 3. Unknown reason -> classified TRANSIENT + loud log
# ---------------------------------------------------------------------------

def test_unknown_reason_fails_open_transient_with_loud_log(caplog):
    """RELATIONSHIP: a renamed/never-seen money-path reason must NOT silently
    terminal-burn a live event. The classifier fails OPEN to TRANSIENT and logs
    LOUDLY (ERROR) — the loud log is the antibody that gets the table updated."""
    # Reset the per-process dedup so the loud log fires in this test.
    from src.events.reactor import _UNREGISTERED_REJECTION_BASES_WARNED

    _UNREGISTERED_REJECTION_BASES_WARNED.discard("SUBMIT_ABORTED_NEWLY_RENAMED_RACE")

    novel = "SUBMIT_ABORTED_NEWLY_RENAMED_RACE: someone renamed PRICE_MOVED"
    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        verdict = _is_transient_money_path_reason(novel)

    assert verdict is True, "an unknown reason must fail open to TRANSIENT (requeue), never burn"
    assert any(
        "UNKNOWN money-path reason base" in rec.message and rec.levelno >= logging.ERROR
        for rec in caplog.records
    ), "the unknown reason must produce a LOUD (ERROR) log"


def test_known_terminal_reason_stays_terminal_no_log(caplog):
    """The fail-open default must NOT flip a KNOWN terminal (e.g. KELLY_TOO_SMALL)
    into a requeue: enumerated terminals stay terminal and emit no unknown-reason
    log."""
    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert _is_transient_money_path_reason("KELLY_TOO_SMALL") is False
        assert _is_transient_money_path_reason("FDR_REJECTED") is False
        assert (
            _is_transient_money_path_reason("ADMISSION_CAPITAL_EFFICIENCY:inputs=missing")
            is False
        )
        assert (
            _is_transient_money_path_reason(
                "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED:"
                "condition_id=0xabc:token_id=123:direction=buy_no"
            )
            is False
        )
        assert _is_transient_money_path_reason("FILL_UP_NO_SUBMIT:BELIEF_NOT_STRENGTHENED") is False
    assert not any("UNKNOWN money-path reason" in r.message for r in caplog.records)


def test_known_transient_reason_classifies_without_log(caplog):
    """Known transient races classify TRANSIENT with no unknown-reason log."""
    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert _is_transient_money_path_reason(_PRICE_MOVED_REASON) is True
        assert _is_transient_money_path_reason("EXECUTABLE_SNAPSHOT_STALE") is True
        assert (
            _is_transient_money_path_reason(
                "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PreSubmitRevalidated requires "
                "would_cross_book=false"
            )
            is True
        )
        assert (
            _is_transient_money_path_reason(
                "SHIFT_BIN_NO_SUBMIT:SHIFT_BIN_CONCURRENT_FAMILY_LEASE"
            )
            is True
        )
        assert _is_transient_money_path_reason("SHIFT_BIN_EXIT_OLD_LEG_PENDING") is True
    assert not any("UNKNOWN money-path reason" in r.message for r in caplog.records)


def test_registered_redecision_reasons_do_not_fall_into_unknown_bucket():
    assert _regret_bucket_for("FILL_UP_NO_SUBMIT:BELIEF_NOT_STRENGTHENED") == "DESIGNED_GATE"
    assert (
        _regret_bucket_for(
            "SUBMIT_ABORTED_ENTRY_PRICE_BELOW_STRATEGY_FLOOR:"
            "PreSubmitRevalidated entry price below strategy floor"
        )
        == "DESIGNED_GATE"
    )


# ---------------------------------------------------------------------------
# 4. Requeue fairness: a perpetually-transient event cannot starve a fresh one
# ---------------------------------------------------------------------------

def test_requeue_fairness_perpetual_transient_does_not_starve_fresh_event():
    """RELATIONSHIP (reactor<->queue): with one perpetually-transient event and one
    fresh valid event in the queue, the fresh event STILL processes — infinite
    requeue cannot monopolize the cycle.

    DESIGN NOTE (verified here, not changed): fetch_pending already orders by a
    per-(tier, city) round-robin rank (_city_round) as the PRIMARY cross-city
    key, so a perpetually-requeued event in one city can never preempt the fresh
    event in another city. The retry-debt tiebreak only interleaves within the
    same (target_date, available_at) of the SAME round — it does not starve.
    Requeue fairness is therefore a property of the existing store ordering; this
    test pins that the reactor's no-cap requeue does not break it."""
    conn, store = _store()
    # City A: a forecast event that ALWAYS blocks transiently (PRICE_MOVED).
    bad = _event("snap-bad", city="Chicago", target_date="2026-06-05")
    # City B: a forecast event whose submit is always clean.
    good = _event("snap-good", city="Tokyo", target_date="2026-06-05")
    store.insert_or_ignore(bad)
    store.insert_or_ignore(good)

    def _submit(ev, _dt):
        if ev.event_id == bad.event_id:
            return EventSubmissionReceipt(
                submitted=False,
                proof_accepted=False,
                event_id=ev.event_id,
                causal_snapshot_id=ev.causal_snapshot_id,
                city="Chicago",
                target_date="2026-06-05",
                metric="high",
                trade_score_positive=False,
                reason=_PRICE_MOVED_REASON,
            )
        return None  # good event -> clean submit

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    # Run several cycles. The bad event requeues every cycle; the good event must
    # be reached and processed within the first cycle (one event per city, fair).
    reactor.process_pending(decision_time=_DT_TIMELY, limit=10)

    assert _status(conn, good.event_id) == "processed", (
        "the fresh valid event must be processed despite the perpetually-transient "
        "event sharing the queue (no starvation)"
    )
    assert _status(conn, bad.event_id) == "pending", (
        "the perpetually-transient event requeues (no cap), it does not block the queue"
    )

    # And it keeps being reachable across cycles (fairness holds, not just cycle 1).
    for _ in range(5):
        reactor.process_pending(decision_time=_DT_TIMELY, limit=10)
    assert _status(conn, bad.event_id) == "pending"


def test_operator_disarm_horizon_terminalizes(monkeypatch):
    """Horizon (c): the operator env kill-switch terminalizes in-flight transients
    with MONEY_PATH_HORIZON_EXPIRED:OPERATOR_DISARM (not a count)."""
    conn, store = _store()
    event = _event("snap-disarm")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _PRICE_MOVED_REASON)

    # Armed: requeues.
    reactor.process_pending(decision_time=_DT_TIMELY, limit=10)
    assert _status(conn, event.event_id) == "pending"

    # Operator disarms.
    monkeypatch.setenv("ZEUS_REACTOR_TRANSIENT_DISARM", "1")
    reactor.process_pending(decision_time=_DT_TIMELY, limit=10)

    assert _status(conn, event.event_id) == "dead_letter"
    row = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row[0] == "MONEY_PATH_HORIZON_EXPIRED"
    assert "OPERATOR_DISARM" in (row[1] or "")


# ---------------------------------------------------------------------------
# 5. Gamma endDate/F1 12:00Z is not a venue-close horizon. A forecast family
#    whose target LOCAL day has not ended must requeue unless explicit venue
#    evidence says closed and not accepting orders.
# ---------------------------------------------------------------------------

def test_gamma_enddate_does_not_terminalize_before_local_day_end():
    """RELATIONSHIP: a transient EXECUTABLE_SNAPSHOT_STALE block after Gamma
    endDate but before local-day end must requeue. Static timing is not closure
    evidence."""
    conn, store = _store()
    event = _manila_event("snap-venue-closed")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _SNAPSHOT_STALE_REASON)

    # Drive the disposition directly at a decision_time inside the stuck window:
    # 14:00Z is AFTER the 12:00Z venue close (POST_TRADING) but BEFORE the 16:00Z
    # local-day-end (Asia/Manila). The horizon MUST fire on the venue close.
    reactor._transient_requeue_reasons[event.event_id] = _SNAPSHOT_STALE_REASON
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_VENUE_CLOSED_NOT_LOCAL_PAST,
        result=res,
    )

    assert res.dead_lettered == 0
    assert res.retried == 1
    assert _status(conn, event.event_id) == "pending"


def test_venue_open_before_close_still_requeues_no_premature_terminal():
    """RELATIONSHIP (no over-termination): the SAME Manila family before 12:00Z is
    local-day active and MUST requeue, never terminalize."""
    conn, store = _store()
    event = _manila_event("snap-venue-open")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _SNAPSHOT_STALE_REASON)

    reactor._transient_requeue_reasons[event.event_id] = _SNAPSHOT_STALE_REASON
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_VENUE_OPEN,
        result=res,
    )

    assert res.dead_lettered == 0, (
        "a pre-venue-close (SETTLEMENT_DAY) family is still tradeable — it must "
        "requeue, never terminalize one cycle early"
    )
    assert res.retried == 1
    assert _status(conn, event.event_id) == "pending"


# ---------------------------------------------------------------------------
# 6. DAY0 local-day horizon. DAY0_EXTREME_UPDATED events must not be terminated
#    by static Gamma endDate/F1 timing while the target local day is still active.
# ---------------------------------------------------------------------------

def _day0_payload(*, city: str, target_date: str, metric: str = "high"):
    return Day0ExtremeUpdatedPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        settlement_source="ogimet",
        station_id="STN-1",
        observation_time=f"{target_date}T06:00:00+00:00",
        observation_available_at=f"{target_date}T06:05:00+00:00",
        raw_value=21.4,
        rounded_value=21,
    )


def _day0_event(*, city: str, target_date: str, metric: str = "high", suffix: str = "d0"):
    payload = _day0_payload(city=city, target_date=target_date, metric=metric)
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"{city}|{target_date}|{metric}|{suffix}",
        source="day0",
        observed_at=f"{target_date}T06:00:00+00:00",
        available_at=f"{target_date}T06:05:00+00:00",
        received_at=f"{target_date}T06:06:00+00:00",
        causal_snapshot_id=None,
        payload=payload,
        priority=120,
    )


def test_day0_after_gamma_enddate_requeues_until_local_day_past():
    """A DAY0_EXTREME_UPDATED family after Gamma endDate but before local-day end
    requeues. Venue closure requires explicit venue evidence, not static timing."""
    conn, store = _store()
    event = _day0_event(city="Manila", target_date="2026-06-13")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _SNAPSHOT_STALE_REASON)

    reactor._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_VENUE_CLOSED_NOT_LOCAL_PAST,
        result=res,
    )

    assert res.dead_lettered == 0
    assert res.retried == 1
    assert _status(conn, event.event_id) == "pending"


def test_day0_live_future_close_family_does_not_terminalize():
    """RELATIONSHIP (no over-termination): a local-day active DAY0 family MUST NOT
    terminalize."""
    conn, store = _store()
    event = _day0_event(city="Manila", target_date="2026-06-13", suffix="d0-live")
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(conn, store, _SNAPSHOT_STALE_REASON)

    reactor._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_VENUE_OPEN,
        result=res,
    )

    assert res.dead_lettered == 0, (
        "a live future-close DAY0 family must requeue, never terminalize one cycle early"
    )
    assert res.retried == 1
    assert _status(conn, event.event_id) == "pending"


def test_day0_gamma_empty_market_absence_terminalizes_snapshot_block():
    """RELATIONSHIP (reactor<->Gamma warm lane): once live venue discovery proves a
    family has no listed Polymarket market, its EXECUTABLE_SNAPSHOT_BLOCKED event
    must terminalize instead of retrying forever. This uses a provider because the
    reactor must not import venue/Gamma code directly."""
    conn, store = _store()
    event = _day0_event(
        city="Auckland",
        target_date="2026-06-20",
        metric="low",
        suffix="gamma-empty",
    )
    store.insert_or_ignore(event)

    calls = []

    def _absence_provider(*, city, target_date, metric):
        calls.append((city, target_date, metric))
        return (city, target_date, metric) == ("Auckland", "2026-06-20", "low")

    reactor = _reactor_with_reason(
        conn,
        store,
        _SNAPSHOT_STALE_REASON,
        family_market_absence_provider=_absence_provider,
    )
    reactor._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_VENUE_OPEN,
        result=res,
    )

    assert calls == [("Auckland", "2026-06-20", "low")]
    assert res.dead_lettered == 1
    assert res.retried == 0
    assert _status(conn, event.event_id) == "dead_letter"
    row = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "MONEY_PATH_HORIZON_EXPIRED"
    assert "VENUE_MARKET_NOT_LISTED" in (row[1] or "")
    assert "EXECUTABLE_SNAPSHOT_BLOCKED" in (row[1] or "")


def test_day0_market_absence_provider_false_keeps_retrying():
    """No over-termination: a cache miss or failed discovery proof must not be treated
    as a market fact. Provider false keeps the normal transient requeue path."""
    conn, store = _store()
    event = _day0_event(
        city="Auckland",
        target_date="2026-06-20",
        metric="low",
        suffix="gamma-retry",
    )
    store.insert_or_ignore(event)
    reactor = _reactor_with_reason(
        conn,
        store,
        _SNAPSHOT_STALE_REASON,
        family_market_absence_provider=lambda **_kw: False,
    )
    reactor._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
    from src.events.reactor import ReactorResult

    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=_DT_VENUE_OPEN,
        result=res,
    )

    assert res.dead_lettered == 0
    assert res.retried == 1
    assert _status(conn, event.event_id) == "pending"
