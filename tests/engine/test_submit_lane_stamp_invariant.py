# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: silent-trade-kill antibody — submit_lane stamp + persist-boundary
#   invariant. Root cause /tmp/allpass_nosubmit_rootcause.md (32 full-pass candidates
#   consumed on the no-submit adapter during a live-arm crash-loop, receipts byte-
#   identical to genuine decision-declined no-submits). RELATIONSHIP tests: they assert
#   the cross-module invariant that holds when an adapter's receipt flows into the
#   reactor's persist boundary — not a single function's input/output.
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.events.reactor import (
    EventSubmissionReceipt,
    LiveLaneDarkInvariantError,
    OpportunityEventReactor,
    ReactorConfig,
)
from src.engine.event_reactor_adapter import (
    SUBMIT_LANE_LIVE,
    SUBMIT_LANE_NO_SUBMIT_ADAPTER,
    SUBMIT_LANE_SUBMIT_DISABLED,
    _stamp_live_adapter_lane,
    _stamp_no_submit_adapter_lane,
)
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

_DECISION_TIME = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
_TARGET_DATE = "2026-05-25"


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


def _forecast_event(key_suffix: str = "a"):
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date=_TARGET_DATE,
        metric="high",
        source_id="opendata",
        source_run_id="run-1",
        cycle="00",
        track="live",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
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
        entity_key=f"Chicago|{_TARGET_DATE}|high|{key_suffix}",
        source="forecast_live",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-1",
    )


def _full_pass_receipt(event, *, submit_lane, reason, proof_accepted=True):
    """A full-pass NO_SUBMIT receipt with a valid proof bundle (the shape that the
    no-submit adapter / live adapter produce after fdr/kelly/score/proof all pass)."""
    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=proof_accepted,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city="Chicago",
        target_date=_TARGET_DATE,
        metric="high",
        condition_id="condition-1",
        token_id="yes-1",
        executable_snapshot_id="snapshot-exec-1",
        family_id="family-1",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=16.0,
        kelly_cost_basis_id="cost-1",
        kelly_decision_id="kelly-1",
        risk_decision_id="risk-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
        reason=reason,
        submit_lane=submit_lane,
    )
    return replace(
        receipt,
        decision_proof_bundle=build_test_no_submit_proof_bundle(
            event, receipt, decision_time=_DECISION_TIME
        ),
    )


def _reactor(store, *, receipt, config):
    submitted: list[str] = []

    def _submit(event, _decision_time):
        submitted.append(event.event_id)
        return receipt

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _event, _stage, _reason: None,
        config=config,
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    return reactor, submitted


def _no_submit_rows(conn, event_id):
    return conn.execute(
        "SELECT receipt_json FROM edli_no_submit_receipts WHERE event_id = ?",
        (event_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Layer-1 stamp helpers (adapter side)
# ---------------------------------------------------------------------------


def test_no_submit_adapter_lane_full_pass_default_reason_named_degrade():
    """Incident shape at the ADAPTER: a full-pass receipt carrying the DEFAULT
    no-submit reason on the no-submit (degrade) lane is stamped NO_SUBMIT_ADAPTER and
    its reason is rewritten to name the degrade cause."""
    event = _forecast_event()
    raw = _full_pass_receipt(
        event,
        submit_lane=None,
        reason="event_bound_final_intent_no_submit",
    )
    stamped = _stamp_no_submit_adapter_lane(raw, degrade_cause="operator_arm_none")
    assert stamped.submit_lane == SUBMIT_LANE_NO_SUBMIT_ADAPTER
    assert stamped.reason == "NO_SUBMIT_ADAPTER_LANE:operator_arm_none"
    assert stamped.proof_accepted is True


def test_no_submit_adapter_lane_honest_reject_keeps_reason():
    """An honest gate-reject (FDR_REJECTED etc.) on the degrade lane keeps its specific
    reason — it is a real no-edge decline, not a lane degrade — and is only stamped."""
    event = _forecast_event()
    raw = _full_pass_receipt(
        event,
        submit_lane=None,
        reason="FDR_REJECTED",
        proof_accepted=False,
    )
    stamped = _stamp_no_submit_adapter_lane(raw, degrade_cause="operator_arm_none")
    assert stamped.submit_lane == SUBMIT_LANE_NO_SUBMIT_ADAPTER
    assert stamped.reason == "FDR_REJECTED"


def test_no_submit_adapter_lane_unconstructable_without_cause():
    """Unconstructable: a NO_SUBMIT_ADAPTER-lane stamp without a degrade cause raises."""
    event = _forecast_event()
    raw = _full_pass_receipt(
        event,
        submit_lane=None,
        reason="event_bound_final_intent_no_submit",
    )
    with pytest.raises(ValueError):
        _stamp_no_submit_adapter_lane(raw, degrade_cause="")


def test_live_adapter_lane_stamps_live_and_submit_disabled():
    event = _forecast_event()
    base = _full_pass_receipt(event, submit_lane=None, reason="SUBMITTED")
    assert (
        _stamp_live_adapter_lane(base, real_order_submit_enabled=True).submit_lane
        == SUBMIT_LANE_LIVE
    )
    assert (
        _stamp_live_adapter_lane(base, real_order_submit_enabled=False).submit_lane
        == SUBMIT_LANE_SUBMIT_DISABLED
    )


# ---------------------------------------------------------------------------
# Layer-2 persist-boundary invariant (reactor side — RELATIONSHIP tests)
# ---------------------------------------------------------------------------


def test_armed_live_daemon_full_pass_degrade_requeues_not_consumed():
    """Incident shape end-to-end: an armed live daemon + the no-submit (degrade) adapter
    selected for a FULL-PASS candidate → the receipt carries NO_SUBMIT_ADAPTER + a named
    degrade cause, and the reactor REQUEUES it (transient) rather than terminally
    consuming a tradeable entry as an 'accepted' no-submit.

    This is the strongest form of the antibody: a full-pass entry is NEVER silently
    booked while the live lane is dark — it survives to be re-decided (on the live lane,
    if recovered) next cycle. The live-lane-dark cause is intrinsically transient."""
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    receipt = _full_pass_receipt(
        event,
        submit_lane=SUBMIT_LANE_NO_SUBMIT_ADAPTER,
        reason="NO_SUBMIT_ADAPTER_LANE:operator_arm_none",
    )
    reactor, _submitted = _reactor(
        store,
        receipt=receipt,
        config=ReactorConfig(
            reactor_mode="live",
            real_order_submit_enabled=True,
            edli_live_operator_authorized=True,
        ),
    )
    result = reactor.process_pending(decision_time=_DECISION_TIME)
    # Requeued, not consumed: the tradeable full-pass entry is preserved.
    assert result.retried == 1
    assert result.proof_accepted == 0
    # NOT silently booked as an accepted no-submit.
    assert _no_submit_rows(conn, event.event_id) == []


def test_no_submit_adapter_lane_reason_classified_transient_not_failopen():
    """The new NO_SUBMIT_ADAPTER_LANE reason base is EXPLICITLY registered transient —
    it must not rely on the classifier's fail-open default (operator law: the table is
    exhaustive; a known reason is never an UNKNOWN-base fail-open)."""
    from src.events.reactor import (
        TRANSIENT_MONEY_PATH_REASONS,
        _is_transient_money_path_reason,
    )

    assert "NO_SUBMIT_ADAPTER_LANE" in TRANSIENT_MONEY_PATH_REASONS
    assert _is_transient_money_path_reason("NO_SUBMIT_ADAPTER_LANE:operator_arm_none")
    # An honest no-edge decline on the same adapter stays terminal (keeps its reason).
    assert not _is_transient_money_path_reason("TRADE_SCORE_NON_POSITIVE")
    # Structural event-type bugs must terminate loudly; re-running the same
    # payload cannot create trade value or a held-position decision.
    assert not _is_transient_money_path_reason(
        "unsupported live candidate event type: EDLI_REDECISION_PENDING"
    )


def test_persist_boundary_raises_on_live_stamped_full_pass_no_submit():
    """Layer-2 unit: the persist boundary method itself RAISES the typed invariant on
    the impossible (armed live + proof_accepted + NO_SUBMIT + LIVE-stamped) shape."""
    conn, store = _store()
    event = _forecast_event()
    receipt = _full_pass_receipt(event, submit_lane=SUBMIT_LANE_LIVE, reason="")
    reactor, _submitted = _reactor(
        store,
        receipt=receipt,
        config=ReactorConfig(
            reactor_mode="live",
            real_order_submit_enabled=True,
            edli_live_operator_authorized=True,
        ),
    )
    with pytest.raises(LiveLaneDarkInvariantError):
        reactor._assert_no_submit_lane_invariant(receipt)


def test_armed_live_daemon_never_silently_books_live_stamped_full_pass_no_submit():
    """Invariant end-to-end: a proof_accepted NO_SUBMIT receipt stamped LIVE on an armed
    live daemon is the silent-kill signature. The persist boundary raises; the reactor's
    event-envelope converts that to a loud DEAD-LETTER (UNKNOWN_REVIEW_REQUIRED carrying
    the typed cause) — the cycle survives, but the kill is NEVER silently booked as an
    accepted no-submit. (The live lane never legitimately produces this shape.)

    Empty reason makes the receipt TERMINAL so it reaches the persist boundary; the
    defense-in-depth net then catches the LIVE stamp."""
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    receipt = _full_pass_receipt(event, submit_lane=SUBMIT_LANE_LIVE, reason="")
    reactor, _submitted = _reactor(
        store,
        receipt=receipt,
        config=ReactorConfig(
            reactor_mode="live",
            real_order_submit_enabled=True,
            edli_live_operator_authorized=True,
        ),
    )
    result = reactor.process_pending(decision_time=_DECISION_TIME)
    # Never silently booked.
    assert result.proof_accepted == 0
    assert _no_submit_rows(conn, event.event_id) == []
    # Surfaced loudly: dead-lettered with the typed invariant cause.
    assert result.dead_lettered == 1
    dl = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert dl is not None
    assert "LIVE_LANE_DARK_FULL_PASS_NO_SUBMIT" in dl[1]


def test_unarmed_daemon_does_not_trip_invariant_on_live_stamp():
    """The invariant only fires on a NOMINALLY ARMED live daemon. With the operator arm
    OFF (or reactor_mode != live) a LIVE-stamped no-submit is not the incident shape and
    persists normally (back-compat: unarmed lanes are unchanged)."""
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    receipt = _full_pass_receipt(
        event,
        submit_lane=SUBMIT_LANE_LIVE,
        reason="",
    )
    reactor, _submitted = _reactor(
        store,
        receipt=receipt,
        config=ReactorConfig(
            reactor_mode="live",
            real_order_submit_enabled=True,
            edli_live_operator_authorized=False,  # NOT armed
        ),
    )
    result = reactor.process_pending(decision_time=_DECISION_TIME)
    assert result.proof_accepted == 1
    assert len(_no_submit_rows(conn, event.event_id)) == 1


def test_legacy_receipt_without_submit_lane_still_persists():
    """Backward compatibility: a receipt with submit_lane=None (legacy / pre-stamp) is
    readable and persists on an armed live daemon — only a LIVE stamp trips the
    invariant, never absence of the field."""
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    receipt = _full_pass_receipt(
        event,
        submit_lane=None,
        reason="",
    )
    reactor, _submitted = _reactor(
        store,
        receipt=receipt,
        config=ReactorConfig(
            reactor_mode="live",
            real_order_submit_enabled=True,
            edli_live_operator_authorized=True,
        ),
    )
    result = reactor.process_pending(decision_time=_DECISION_TIME)
    assert result.proof_accepted == 1
    rows = _no_submit_rows(conn, event.event_id)
    assert len(rows) == 1
    # submit_lane omitted from receipt_json when None (byte-stable legacy hash).
    assert "submit_lane" not in json.loads(rows[0][0])
