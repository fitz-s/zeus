# Created: 2026-09-12
# Money-path fix: decision_certificates whole-day gaps (2026-09-08, 2026-09-11).
# See docs trace: an epoch that raises AFTER actuate_winner()/consume() already
# returned a receipt for the winner must carry that receipt's
# decision_proof_bundle forward into the POST_SUBMIT_UNKNOWN fallback receipt
# instead of dropping it to None -- the venue call already started, so the
# certificate must not depend on unrelated post-submit bookkeeping (which runs
# AFTER the actuator call, inside the same try block) also succeeding.
#
# Harness mirrors
# tests/integration/test_w3_solve_seam_g3.py::
# test_global_batch_claims_unpaged_cut_time_winner_and_continues_actuation
# (same PreparedGlobalAuctionResult / GlobalSingleOrderActuation construction),
# simplified to a single winning event so no claim_unpaged_winner rebind is
# needed.
from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.global_batch_runtime as global_batch_runtime
from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.strategy_capital_allocation import STRATEGY_LOG_UTILITY_BASIS
from src.decision_kernel import claims
from src.engine.global_auction_universe import current_global_auction_scope_from_events
from src.engine.global_single_order_auction import (
    GlobalSingleOrderActuation,
    GlobalSingleOrderCandidate,
    PreparedGlobalAuctionResult,
    global_single_order_actuation_identity,
    global_single_order_economic_identity,
)
import json

from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt


def _winner_event(*, city: str, source_run_id: str):
    captured_at = "2026-07-10T08:00:00+00:00"
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date="2026-07-11",
        metric="high",
        source_id="replacement_0_1",
        source_run_id=source_run_id,
        cycle="2026-07-10T00:00:00+00:00",
        track="replacement_0_1_openmeteo_bayes_fusion",
        snapshot_id=f"rmf-{city}|2026-07-11|high|2026-07-10",
        snapshot_hash=source_run_id,
        captured_at=captured_at,
        available_at=captured_at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=3,
        min_members_floor=3,
        completeness_status="COMPLETE",
        required_steps=[],
        observed_steps=[],
        expected_members=3,
        source_run_status="COMPLETE",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    payload_json = asdict(payload)
    payload_json["city_timezone"] = "UTC"
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|2026-07-11|high",
        source="global-auction-current-scope",
        observed_at=captured_at,
        available_at=captured_at,
        received_at=captured_at,
        payload=payload_json,
        causal_snapshot_id=payload.snapshot_id,
    )


def _family_key_for(event):
    from src.engine.global_auction_universe import current_global_auction_scope_from_events

    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    )
    return scope.family_keys[0]


def _build_selected(decision_at, event, family_key):
    curve = ExecutableCostCurve(
        token_id="token-cert",
        side="YES",
        snapshot_id="book-snapshot-cert",
        book_hash="book-cert",
        levels=(BookLevel(price=Decimal("0.40"), size=Decimal("10")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    witness = SimpleNamespace(
        family_key=family_key,
        witness_identity="probability-cert",
        posterior_identity_hash="run-cert",
        q_version="q-cert",
        family_binding_identity="family-binding-cert",
        sample_matrix_identity="sample-matrix-cert",
        band_alpha=0.05,
        band_basis="lower-tail",
        captured_at_utc=decision_at,
    )
    candidate = GlobalSingleOrderCandidate(
        candidate_id="candidate-cert",
        family_key=family_key,
        bin_id="20C",
        condition_id="condition-cert",
        side="YES",
        token_id="token-cert",
        probability_witness_identity=witness.witness_identity,
        book_snapshot_id="book-snapshot-cert",
        book_captured_at_utc=decision_at,
        execution_curve_identity=curve.book_hash,
        ledger_snapshot_id="ledger-cert",
        executable_cost_curve=curve,
        resolution_identity="resolution-cert",
        neg_risk=False,
    )
    decision = SimpleNamespace(
        candidate=candidate,
        shares=Decimal("10"),
        cost_usd=Decimal("4"),
        limit_price=Decimal("0.40"),
        expected_fill_price_before_fee=Decimal("0.40"),
        max_spend_usd=Decimal("4"),
        current_token_shares=Decimal("0"),
        full_kelly_target_shares=Decimal("40"),
        fractional_kelly_target_shares=Decimal("10"),
        robust_delta_log_wealth=0.01,
        ruin_probability_reduction=0.0,
        robust_ev_usd=2.0,
        capital_efficiency=0.25,
        no_trade_reason=None,
        buy_sizing_mode="FRACTIONAL_TARGET",
        buy_minimum_marketable_repair=None,
        expected_terminal_wealth=None,
        expected_growth=SimpleNamespace(
            probability_basis="POSTERIOR_PREDICTIVE_MEAN",
            probability_witness_identity=witness.witness_identity,
            utility_basis=STRATEGY_LOG_UTILITY_BASIS,
            ruin_probability_reduction=0.0,
            expected_delta_log_wealth=0.012,
            expected_ev_usd=2.4,
            capital_lock_hours=24.0,
            expected_log_growth_per_hour=0.0005,
            expected_capital_efficiency=0.003,
        ),
        terminal_wealth=SimpleNamespace(
            win_probability_lcb=0.60,
            loss_probability_ucb=0.40,
            loss_payoff_usd=Decimal("-4"),
            win_payoff_usd=Decimal("6"),
            median_payoff_usd=Decimal("6"),
            wealth_after_loss_usd=Decimal("96"),
            wealth_after_win_usd=Decimal("106"),
            expected_value_usd=2.0,
        ),
    )
    wealth_economic_identity = "wealth-economic-cert"
    economic_identity = global_single_order_economic_identity(
        decision=decision,
        probability_witness=witness,
        wealth_economic_identity=wealth_economic_identity,
    )
    actuation_identity = global_single_order_actuation_identity(
        decision=decision,
        winner_event_id=event.event_id,
        universe_witness_identity="universe",
        wealth_witness_identity="wealth-witness",
        selection_epoch_identity="selection-epoch",
        selection_cut_at_utc=decision_at,
        decision_at_utc=decision_at,
    )
    return PreparedGlobalAuctionResult(
        decision=decision,
        winner_event_id=event.event_id,
        actuation=GlobalSingleOrderActuation(
            decision=decision,
            winner_event_id=event.event_id,
            universe_witness_identity="universe",
            wealth_witness_identity="wealth-witness",
            selection_epoch_identity="selection-epoch",
            probability_witness=witness,
            selection_cut_at_utc=decision_at,
            decision_at_utc=decision_at,
            actuation_identity=actuation_identity,
            wealth_economic_identity=wealth_economic_identity,
            economic_identity=economic_identity,
        ),
    )


@dataclass(frozen=True)
class _Prepared:
    probability_witness: object
    day0_exit_authority_status: str = "not_applicable"
    day0_exit_authority_reason: str = "non_day0_family"
    sell_action_authority_identity: str = "non_day0_default_authority"


def _run(monkeypatch, *, event, selected, actuate_winner, venue_submit_count):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    world = sqlite3.connect(":memory:")
    trade = object()
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)

    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-witness",
            economic_identity=selected.actuation.wealth_economic_identity,
            strategy_capital_allocation=SimpleNamespace(
                remaining_buy_capacity_usd=Decimal("1000"),
            ),
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        lambda *_args, **_kwargs: selected,
    )

    prepared = {
        event.event_id: _Prepared(probability_witness=selected.actuation.probability_witness),
    }

    return global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=world,
        forecast_conn=object(),
        trade_conn=trade,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda item, _at: EventSubmissionReceipt(
            False,
            item.event_id,
            item.causal_snapshot_id,
            prepared_global_family=prepared[item.event_id],
        ),
        actuate_winner=actuate_winner,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=venue_submit_count,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )


def test_actuation_started_exception_carries_forward_the_receipt_proof_bundle(monkeypatch):
    """(a) actuation started, epoch then raises -> the receipt carries the
    bundle and the certificate becomes persistable (EXECUTION_RECEIPT present)."""
    event = _winner_event(city="CertCity", source_run_id="run-cert")
    family_key = _family_key_for(event)
    selected = _build_selected(
        _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
        event,
        family_key=family_key,
    )
    # A stand-in for the EXECUTION_RECEIPT certificate _submit_inner() would
    # already have built and persisted (pre-submit certs) before the venue
    # call -- _execution_receipt_certificate_bundle() only inspects
    # certificate_type, so this is enough to prove the carry-forward mechanism
    # without re-testing build_execution_receipt_certificate() itself (that is
    # covered by tests/decision_kernel/test_execution_command_certificate.py).
    receipt_cert = SimpleNamespace(certificate_type=claims.EXECUTION_RECEIPT)
    marker_bundle = (receipt_cert,)

    actuated = []

    def _actuate(evt, actuation, _at):
        actuated.append((evt, actuation))
        return EventSubmissionReceipt(
            True,
            evt.event_id,
            evt.causal_snapshot_id,
            reason="SUBMITTED:test",
            proof_accepted=True,
            decision_proof_bundle=marker_bundle,
        )

    # First call (pre-actuation before_calls capture) returns 0; the call
    # immediately after actuate_winner() has returned raises, simulating an
    # unrelated post-submit bookkeeping fault (continuation/wealth-redecision)
    # AFTER the venue call already completed and its receipt (with a real
    # proof bundle) already exists in winner_receipt.
    calls = {"n": 0}

    def _venue_submit_count():
        calls["n"] += 1
        if actuated:
            raise RuntimeError("SIMULATED_POST_ACTUATION_BOOKKEEPING_FAULT")
        return 0

    result = _run(
        monkeypatch,
        event=event,
        selected=selected,
        actuate_winner=_actuate,
        venue_submit_count=_venue_submit_count,
    )

    assert len(actuated) == 1
    receipt = result.receipts[event.event_id]
    assert receipt.side_effect_status == "POST_SUBMIT_UNKNOWN"
    assert "GLOBAL_ACTUATION_EXCEPTION" in receipt.reason
    assert receipt.decision_proof_bundle == marker_bundle
    from src.events.reactor import _execution_receipt_certificate_bundle

    assert _execution_receipt_certificate_bundle(receipt) == marker_bundle


def test_pre_actuation_exception_still_persists_no_certificate(monkeypatch):
    """(b) epoch raises before actuate_winner() is ever called (or returns)
    -> no certificate, compile failure recorded as today (unchanged)."""
    # source_run_id must match _build_selected()'s hard-coded witness
    # posterior_identity_hash ("run-cert") so _forecast_carrier_matches()
    # passes and the code reaches the venue_submit_count() call this test
    # actually wants to fault -- the pre-actuation carrier-mismatch reject is
    # a different, already-correct rejection path this test isn't targeting.
    event = _winner_event(city="CertCityPre", source_run_id="run-cert")
    selected = _build_selected(
        _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
        event,
        family_key=_family_key_for(event),
    )

    def _actuate(*_args, **_kwargs):
        raise AssertionError("actuate_winner must not be called on pre-actuation failure")

    def _venue_submit_count():
        raise RuntimeError("SIMULATED_PRE_ACTUATION_SCAN_FAULT")

    result = _run(
        monkeypatch,
        event=event,
        selected=selected,
        actuate_winner=_actuate,
        venue_submit_count=_venue_submit_count,
    )

    receipt = result.receipts[event.event_id]
    assert receipt.side_effect_status == "NO_SUBMIT"
    assert receipt.reason.startswith("GLOBAL_AUCTION_FAILED:")
    assert receipt.decision_proof_bundle is None
