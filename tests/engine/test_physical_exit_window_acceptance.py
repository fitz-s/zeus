# Lifecycle: created=2026-10-06; last_reviewed=2026-10-07; last_reused=2026-10-07
# Purpose: Prove offline lawful early exits, liquidity recovery and canonical churn fences.
# Reuse: Run when physical source-to-q, global exit selection or exit execution authority changes.
# Created: 2026-10-06
# Last reused/audited: 2026-10-07
# Authority basis: offline physical-exit investigation; preserve INV-01/06/28/41
# and the inclusive .05-.95 executable-price law.
"""Independent offline acceptance probes for the lawful early-exit window.

The HKO fixture runs real source writers, the current probability materializer,
HELD preparation and the actual global selector. The execution continuation uses
that selected decision and the real executor/command/fill boundaries with an
explicit fake SDK. It is not a daemon or a historical-liquidity replay. Standing
rest/selector probes below are explicitly boundary tests, not a claim that a
single unbroken production monitor/cancel/auction loop was exercised.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src.engine import event_reactor_adapter as adapter
from src.execution.exit_lifecycle import GlobalSellExecutionAuthority
from src.solve import solver as S
from tests.integration import test_w3_solve_seam_g3 as source_harness
from tests.integration.test_w3_solve_seam_g3 import (  # noqa: F401: pytest fixtures
    _hko_clock_native_sources,
    _normal_hko_concentrated_sell,
)
from tests.solve import test_solver_properties as solver_harness
from tests.execution import test_standing_entry_value as rest_harness


def test_current_positive_q_reaches_actual_global_taker_before_floor(
    _normal_hko_concentrated_sell, record_property,
):
    case = _normal_hko_concentrated_sell
    decision = case.ranked.decision
    assert 0 < case.q < 0.50
    assert isinstance(case.probability, S.JointOutcomeProbabilityWitness)
    assert not isinstance(case.probability, S.DeterministicBinPayoffWitness)
    assert case.selected.execution_mode == "TAKER_LIMIT"
    assert case.selected.probability_functional == "POSTERIOR_PREDICTIVE_MEAN"
    assert decision.shares == D("2") < case.selected.held_shares
    assert D(".05") <= case.authority.limit_price() == D(".50") <= D(".95")
    net = case.curve.proceeds_for_shares(decision.shares)[0]
    cash, held, sold = float(case.wealth.wealth_floor_usd), 5.0, float(decision.shares)
    expected_ev = float(net) - case.q * sold
    expected_log = ((1 - case.q) * math.log((cash + float(net)) / cash)
                    + case.q * math.log((cash + held - sold + float(net)) / (cash + held)))
    assert decision.expected_terminal_wealth.expected_ev_usd == pytest.approx(expected_ev)
    assert decision.expected_terminal_wealth.expected_delta_log_wealth == pytest.approx(expected_log)
    assert expected_ev > 0 and expected_log > 0
    assert case.ranked.actuation.auction_receipt_ref is not None
    # The deeper .30 level does not become lawful just because q < top bid.
    assert decision.shares < sum(level.size for level in case.curve.levels)
    record_property("source_to_selector", json.dumps({
        "metric": case.position.temperature_metric, "q": case.q,
        "shares": str(decision.shares), "net_proceeds": str(net),
        "probability_identity": case.probability.witness_identity,
        "actuation_identity": case.ranked.actuation.actuation_identity,
    }))


def test_current_source_hold_is_a_real_global_result(_normal_hko_concentrated_sell):
    case = _normal_hko_concentrated_sell
    batch, selections, actuations = case.below_q
    assert case.q > .30
    assert actuations == []
    assert selections[-1].decision.candidate is None
    assert {receipt.reason for receipt in batch.receipts.values()} == {
        "GLOBAL_AUCTION_NO_TRADE:NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
    }


@pytest.mark.parametrize("jit_bid", (".04", ".49", ".96"))
def test_jit_must_not_execute_an_unsafe_or_worsened_selected_sale(
    _normal_hko_concentrated_sell, jit_bid,
):
    case = _normal_hko_concentrated_sell
    with pytest.raises(ValueError):
        fresh = case.jit(jit_bid)
        GlobalSellExecutionAuthority.from_current(actuation=case.ranked.actuation, jit_candidate=fresh)
    assert case.trade.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0


def test_jit_economic_identity_refresh_does_not_cancel_a_lawful_sale(_normal_hko_concentrated_sell):
    case = _normal_hko_concentrated_sell
    fresh = case.jit(".51")
    authority = GlobalSellExecutionAuthority.from_current(actuation=case.ranked.actuation, jit_candidate=fresh)
    assert authority.limit_price() == D(".50")
    assert authority.actuation.decision.shares == case.ranked.decision.shares


def _execute_confirmed_source_sale(case, monkeypatch, tmp_path):
    """Real selector and executor; transport and host posture are controlled.

    ACK/MATCHED is not final venue confirmation, and a partial lawful sale must
    leave the remaining holding active rather than manufacture economic closure.
    """
    from src.execution import executor, exit_lifecycle
    from src.state import collateral_ledger
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot, init_collateral_schema
    from src.state.db import init_schema_world_only
    from src.state.portfolio import load_runtime_open_portfolio
    from src.state.venue_command_repo import append_trade_fact

    at = case.fixture.cut
    price = case.authority.limit_price()
    exit_intent, _ = source_harness._normal_hko_sell_intents(case)
    exit_intent = replace(exit_intent, best_bid=float(price), current_market_price=float(price),
        probability_receipt=adapter._global_sell_probability_receipt(
            candidate=case.selected, witness=case.probability, held_side_probability=case.q))
    monkeypatch.setattr(executor, "datetime", case.clock)
    monkeypatch.setattr(collateral_ledger, "datetime", case.clock)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: at)
    for name in ("_assert_cutover_allows_submit", "_assert_heartbeat_allows_submit",
                 "_assert_ws_gap_allows_submit", "_assert_risk_allocator_allows_exit_submit"):
        monkeypatch.setattr(executor, name, lambda *_a, **_k: {"allowed": True, "component": "offline_test"})
    monkeypatch.setattr(executor, "_select_risk_allocator_order_type", lambda *_: "FAK")
    monkeypatch.setattr(executor, "_refresh_exit_collateral_snapshot_for_submit",
                        lambda *_a, **_k: {"allowed": True, "component": "fake_chain"})
    init_collateral_schema(case.trade)
    CollateralLedger(case.trade).set_snapshot(CollateralSnapshot(
        pusd_balance_micro=858079, pusd_allowance_micro=858079, usdc_e_legacy_balance_micro=0,
        ctf_token_balances={case.selected.token_id: 5_000_000},
        ctf_token_allowances={case.selected.token_id: 5_000_000},
        reserved_pusd_for_buys_micro=0, reserved_tokens_for_sells={},
        captured_at=at, authority_tier="CHAIN"))
    audit = tmp_path / "acceptance-world.db"
    with sqlite3.connect(audit) as conn:
        init_schema_world_only(conn)
    monkeypatch.setattr("src.state.db.get_world_connection", lambda **_: sqlite3.connect(audit))
    calls = []

    class FakeVenue:
        def bind_submission_envelope(self, envelope):
            self.envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.persister = persister

        def place_limit_order(self, **order):
            assert case.trade.execute("SELECT state FROM venue_commands WHERE side='SELL'").fetchone()[0] == "SUBMITTING"
            assert order["side"] == "SELL" and order["order_type"] == "FAK"
            assert order["token_id"] == case.selected.token_id
            calls.append(order)
            if hasattr(case, "window_end"):
                assert at < case.window_end
            assert D(".05") <= D(str(order["price"])) <= D(".95")
            response = {"orderID": "acceptance-sale", "status": "MATCHED"}
            envelope = self.envelope.with_updates(order_id=response["orderID"],
                raw_response_json=json.dumps(response, sort_keys=True, separators=(",", ":")))
            return {**response, "matchedSize": str(case.ranked.decision.shares), "avgPrice": str(price),
                    "tradeIDs": ["acceptance-trade"], "_venue_submission_envelope": envelope.to_dict()}

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeVenue)
    monkeypatch.setattr("src.data.polymarket_client.resolve_funder_address",
                        lambda: "0x" + "12" * 20)
    case.trade.commit()
    from src.state.portfolio import ExitContext, PortfolioState
    held = load_runtime_open_portfolio(case.trade).positions[0]
    sold = case.ranked.decision.shares
    net = case.curve.proceeds_for_shares(sold)[0]
    fee_micro = int((sold * price - net) * 1_000_000)
    real_execute = executor.execute_exit_order
    confirmed = []

    def execute_in_private_trade_db(current_intent, **kwargs):
        # Supply the same private canonical connection the live composition root
        # owns; all executor economics, commands and final envelopes stay real.
        result = real_execute(current_intent, conn=case.trade, **kwargs)
        assert result.status == "filled", result.reason
        assert result.venue_call_started and result.venue_ack_received
        pending = case.trade.execute("SELECT phase, shares FROM position_current").fetchone()
        assert tuple(pending) == ("pending_exit", 5.0)
        append_trade_fact(case.trade, trade_id="acceptance-trade", venue_order_id=result.order_id,
            command_id=result.command_id, state="CONFIRMED", filled_size=str(sold), fill_price=str(price),
            fee_paid_micro=fee_micro, source="FAKE_VENUE", observed_at=at.isoformat(),
            venue_timestamp=at.isoformat(), raw_payload_hash=hashlib.sha256(b"acceptance-confirmed").hexdigest(),
            raw_payload_json={"status": "CONFIRMED", "test_only": True})
        case.trade.commit()
        confirmed.append(result)
        return result

    monkeypatch.setattr(exit_lifecycle, "execute_exit_order", execute_in_private_trade_db)
    outcome = exit_lifecycle.execute_exit(PortfolioState(positions=[held]), held,
        ExitContext(exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL", fresh_prob=case.q,
            fresh_prob_is_fresh=True, current_ci=(case.q, case.q),
            current_market_price=float(price), current_market_price_is_fresh=True,
            best_bid=float(price), best_ask=float(price + case.snapshot.min_tick_size),
            min_tick=float(case.snapshot.min_tick_size), hours_to_settlement=6,
            position_state="holding", probability_receipt={"q_version": case.probability.q_version}),
        clob=SimpleNamespace(get_order_status=lambda _: {"status": "CONFIRMED", "matched_size": str(sold),
                                                        "remaining_size": "0", "avgPrice": str(price)}),
        conn=case.trade, exit_intent=exit_intent, global_sell_authority=case.authority,
        global_sell_required_snapshot_id=case.snapshot.snapshot_id,
        global_sell_prefetched_orderbook={"asset_id": case.selected.token_id,
            "bids": [{"price": str(price), "size": str(sold)}], "asks": []})
    assert outcome.startswith("position_reduced:"), outcome
    assert len(calls) == len(confirmed) == 1
    row = case.trade.execute("SELECT phase, shares FROM position_current").fetchone()
    assert row["phase"] == "active" and D(str(row["shares"])) == D("5") - sold
    assert case.trade.execute("SELECT COUNT(*) FROM position_events WHERE event_type='ECONOMIC_CLOSE'").fetchone()[0] == 0
    return confirmed[0]


def test_real_selected_sale_persists_fake_confirmed_fill_before_reducing_holding(
    _normal_hko_concentrated_sell, monkeypatch, tmp_path,
):
    _execute_confirmed_source_sale(_normal_hko_concentrated_sell, monkeypatch, tmp_path)


def _statistical_sell(*, q=.03, bid=".10", fee="0"):
    return solver_harness._global_sell_candidate(
        candidate_id="physical-acceptance", family="Acceptance|2026-07-11|high", side="YES",
        held_q=q, bids=((bid, "10"),), shares="10", fee=fee, min_tick=".01", min_order="1",
        probability_functional="POSTERIOR_PREDICTIVE_MEAN",
        exit_authority_status="immature", exit_authority_reason="statistical_current_evidence")


@pytest.mark.parametrize("q", (0.0, 1e-12, .03))
def test_statistical_zero_or_tiny_q_never_becomes_exact_disproof(q):
    candidate = _statistical_sell(q=q)
    probability = solver_harness._global_probability_witness(candidate)
    decision = solver_harness._global_select((candidate,))
    assert isinstance(probability, S.JointOutcomeProbabilityWitness)
    assert not isinstance(probability, S.DeterministicBinPayoffWitness)
    assert decision.candidate is candidate
    assert decision.candidate.probability_functional == "POSTERIOR_PREDICTIVE_MEAN"
    assert decision.candidate.exit_authority_status == "immature"


def test_q_below_gross_bid_is_insufficient_after_real_fees():
    candidate = _statistical_sell(q=.099, fee=".20")
    assert .099 < .10
    decision = solver_harness._global_select((candidate,))
    assert decision.candidate is None


def test_positive_exit_may_lose_to_a_more_valuable_actual_global_proposal():
    sell = _statistical_sell(q=.099)
    buy = solver_harness._global_candidate(candidate_id="better-buy", family="Other|2026-07-11|high",
                                            side="YES", q=.90, levels=((".05", "1000"),))
    decision = solver_harness._global_select((sell, buy), cap="100")
    assert decision.candidate is buy
    assert len(decision.candidate_evaluations) >= 2


def test_stale_probability_cannot_churn_back_into_a_buy_after_value_cancel(monkeypatch):
    """Cancellation and stale-auction seams; not a whole monitor-loop replay."""
    conn, venue, result = rest_harness.TestStandingEntryTrace()._cycle(
        monkeypatch, q=.03, posterior="physical-deterioration")
    try:
        assert [v.action for v in result["valuations"]] == ["CANCEL"]
        assert venue.calls == [(["venue-1"], "CANCEL_PENDING")]
        assert conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd'").fetchone()[0] == "CANCELLED"
        stale_buy = solver_harness._global_candidate(candidate_id="stale-buy", family="Acceptance|2026-07-11|high",
                                                    side="YES", q=.75, levels=((".10", "100"),))
        stale = solver_harness._global_probability_witness(stale_buy)
        current = replace(S.CurrentFamilyProbabilityAuthority.from_witness(stale),
                          witness_identity="physically-corrected-current-q")
        rejected = solver_harness._global_select((stale_buy,), current_probabilities={stale.family_key: current})
        assert rejected.candidate is None
        # A genuinely refreshed current BUY still has to pass normal economics.
        refreshed = solver_harness._global_candidate(candidate_id="refreshed-buy", family=stale.family_key,
                                                     side="YES", q=.03, levels=((".10", "100"),))
        assert solver_harness._global_select((refreshed,)).candidate is None
    finally:
        conn.close()


def test_identity_only_refresh_keeps_a_valuable_entry_remainder(monkeypatch):
    conn, venue, result = rest_harness.TestStandingEntryTrace()._cycle(
        monkeypatch, q=.75, posterior="new-identity-unchanged-economics")
    try:
        assert venue.calls == []
        assert [v.action for v in result["valuations"]] == ["KEEP"]
        assert conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd'").fetchone()[0] == "ACKED"
    finally:
        conn.close()


@pytest.mark.parametrize("state", ("SUBMITTING", "POST_SUBMIT_UNKNOWN", "ACKED"))
def test_unresolved_exit_command_blocks_duplicate_global_reauction(state):
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("""
            CREATE TABLE venue_commands (command_id TEXT, position_id TEXT, token_id TEXT,
                side TEXT, intent_kind TEXT, state TEXT, venue_order_id TEXT,
                idempotency_key TEXT, updated_at TEXT, created_at TEXT);
            CREATE TABLE venue_command_events (command_id TEXT, event_type TEXT,
                payload_json TEXT, sequence_no INTEGER);
            CREATE TABLE position_events (position_id TEXT, event_type TEXT,
                sequence_no INTEGER, payload_json TEXT, occurred_at TEXT);
        """)
        conn.execute("INSERT INTO venue_commands VALUES ('cmd', 'held', 'token', 'SELL', 'EXIT', ?, NULL, 'idem', '2', '1')", (state,))
        position = SimpleNamespace(trade_id="held", state="pending_exit", direction="buy_yes",
                                   token_id="token", no_token_id="no-token",
                                   effective_exposure=lambda: SimpleNamespace(shares=10.0))
        assert adapter._global_sell_command_blocks_reauction(conn, position=position,
            position_id="held", token_id="token", legacy_order_id=None)


@pytest.mark.parametrize("fault", ("missing", "superseded", "stale"))
def test_invalid_current_probability_never_authorizes_statistical_sale(fault):
    candidate = _statistical_sell()
    witness = solver_harness._global_probability_witness(candidate)
    current = S.CurrentFamilyProbabilityAuthority.from_witness(witness)
    if fault == "missing":
        authorities = {}
    elif fault == "superseded":
        authorities = {witness.family_key: replace(current, source_truth_identity="new-physical-source")}
    else:
        witness = replace(witness, max_age=timedelta(milliseconds=1))
        authorities = {witness.family_key: current}
    decision = solver_harness._global_select((candidate,),
        probability_witnesses={witness.family_key: witness}, current_probabilities=authorities)
    assert decision.candidate is None


def test_current_calibration_can_change_apparent_exit_into_lawful_hold():
    candidate = _statistical_sell()
    correction = solver_harness._correction_for(candidate, raw_q=.03, corrected_q=.15, p0=.10)
    raw_decision = solver_harness._global_select((candidate,))
    calibrated = solver_harness._global_select((candidate,),
        payoff_q_correction_resolver=lambda *_: correction,
        family_portfolio_endowment_resolver=lambda _: solver_harness._family_endowment(candidate))
    assert raw_decision.candidate is candidate
    assert calibrated.candidate is None
    assert calibrated.rejection_reasons[candidate.candidate_id] == "NON_POSITIVE_EXPECTED_OBJECTIVE"


@pytest.mark.parametrize("p_fill,expected_mode", ((".1", "TAKER_LIMIT"), (".99", "MAKER_REST")))
def test_global_selector_compares_maker_and_taker_lawfully(p_fill, expected_mode):
    taker = solver_harness._global_sell_candidate(
        candidate_id="early-taker", family="Mode|2026-07-11|high", side="YES", held_q=.03,
        bids=((".10", "10"),), shares="10", min_tick=".01", quote_ttl_seconds=30,
        probability_functional="POSTERIOR_PREDICTIVE_MEAN")
    proposal = S.passive_sell_proposal_curve(taker.executable_sell_curve, capacity=D("10"))
    assert proposal is not None
    epoch = "acceptance-maker-epoch"
    provisional = replace(taker, candidate_id="early-maker", execution_mode="MAKER_REST",
        proposal_sell_curve=proposal, fill_probability=float(p_fill), fill_probability_source="fixture-current",
        rest_deadline_minutes=20.0, asset_epoch_identity=epoch)
    witness = solver_harness._current_maker_witness(provisional, proposal=proposal, asset_epoch=epoch,
        outcomes=(S.MakerFillOutcome(D(p_fill), D("1"), proposal.levels[0].price),
                  S.MakerFillOutcome(D("1") - D(p_fill), D("0"), D("0"))))
    maker = replace(provisional, maker_fill_witness=witness, fill_probability_source=witness.witness_identity)
    result = solver_harness._global_select((maker, taker))
    assert result.candidate.execution_mode == expected_mode
    assert {evaluation.execution_mode for evaluation in result.candidate_evaluations} == {"TAKER_LIMIT", "MAKER_REST"}


@pytest.mark.parametrize("q", (0.0, 1e-13, .03))
def test_local_reversal_stays_a_global_request_without_exact_proof(q):
    from src.engine import cycle_runtime
    from src.state.portfolio import ExitContext, Position
    position = Position(trade_id="local-reversal", market_id="local-market", city="Paris", cluster="Europe",
        target_date="2026-07-11", bin_label="20C", direction="buy_yes", shares=10, entry_price=.20,
        size_usd=2, cost_basis_usd=2, state="holding", token_id="yes", no_token_id="no",
        p_posterior=.75, strategy_key="forecast_qkernel_entry")
    context = ExitContext(fresh_prob=q, fresh_prob_is_fresh=True, current_ci=(q, max(q, .05)),
        current_market_price=.10, current_market_price_is_fresh=True, best_bid=.10, best_ask=.12,
        min_tick=.01, bid_size=10, hours_to_settlement=6, position_state="holding",
        day0_active=True, day0_exit_authority_status="immature", probability_receipt={
            "probability_functional": "POSTERIOR_PREDICTIVE_MEAN", "held_side_probability": q})
    position._current_global_held_probability_samples = (q, q)
    verdict = position.evaluate_exit(context)
    assert verdict.should_exit and verdict.trigger == "SELL_REVERSAL"
    assert cycle_runtime._global_auction_owns_statistical_sell(verdict, verdict.reason)
    assert not cycle_runtime._posterior_support_zero_sell_dominates(position, context)


@pytest.mark.parametrize("proof_kind", ("branchwise", "hard_fact"))
def test_monitor_redecides_exact_exit_when_liquidity_returns_inside_retry_delay(tmp_path, monkeypatch, proof_kind):
    """Monitor scheduling boundary: accepted hard fact and fresh book wake the
    ordinary exit organ at +10s, not after the old no-bid +120s timer.

    The hard-fact classifier is controlled; the branchwise receipt binds an
    accepted NOAA print through the real source reader. The gateway is captured
    here. Pending scan, quotes, canonical writers, exit law and retry are real.
    """
    import logging
    from zoneinfo import ZoneInfo
    import numpy as np
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime, monitor_refresh
    from src.execution import exit_lifecycle, day0_hard_fact_exit
    from src.state.portfolio import PortfolioState, Position
    from tests import test_day0_hard_fact_exit as monitor_harness
    from tests.test_exit_safety import _ensure_snapshot, _bind_exact_zero_source_receipt
    from src.state import write_coordinator

    # The real coordinator caches canonical DB paths process-wide. Each replay
    # has a different temporary TRADE/WORLD/FORECAST topology.
    monkeypatch.setattr(write_coordinator, "_DEFAULT_RUNTIME_COORDINATOR", None)
    initial = datetime.now(timezone.utc)
    later = initial + timedelta(seconds=10)
    clock = [initial]
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: clock[0])
    pos = Position(trade_id="liquidity-window", market_id="condition-test", condition_id="condition-test",
        city="London", cluster="Europe", target_date=initial.astimezone(ZoneInfo("Europe/London")).date().isoformat(), bin_label="30°C or below",
        direction="buy_yes", shares=10, chain_shares=10, chain_state="synced",
        entry_price=.40, size_usd=4, cost_basis_usd=4, state="day0_window",
        token_id="yes-token-001", no_token_id="yes-token-001-no", unit="C", env="live",
        strategy_key="forecast_qkernel_entry", entered_at=(initial - timedelta(days=1)).isoformat())
    conn, read_conn = monitor_harness._production_monitor_topology(tmp_path, monkeypatch, pos, phase="day0_window")
    # Real NOAA print and owner reader bind the full receipt in WORLD. The
    # guard is not mocked; canonical monitor/exit evidence remains in TRADE.
    with sqlite3.connect(tmp_path / "zeus-world.db") as source_conn:
        source_conn.row_factory = sqlite3.Row
        compact_receipt = _bind_exact_zero_source_receipt(source_conn, pos, initial)
        source_conn.commit()
    portfolio = PortfolioState(positions=[pos])
    exit_lifecycle._mark_exit_retry(pos, reason="POSTERIOR_SUPPORT_ZERO_SELL_DOMINATES",
                                  error="exit_no_executable_bid", conn=conn)
    deadline = datetime.fromisoformat(pos.next_exit_retry_at)
    assert deadline > later
    _ensure_snapshot(conn, snapshot_id="liquidity-missing-initial",
        selected_outcome_token_id=pos.token_id, captured_at=initial,
        freshness_deadline=initial + timedelta(seconds=180),
        orderbook_top_bid=None, orderbook_top_ask="0.12", min_order_size="5")
    conn.commit()
    clock[0] = later

    class Book(monitor_harness._DeepBookClob):
        def get_held_orderbook_snapshots_hard_deadline(self, token_ids, *, timeout_seconds):
            from src.data.polymarket_client import HeldOrderbookReadResult
            wanted = [str(token) for token in token_ids]
            return HeldOrderbookReadResult({token: self._book(token) for token in wanted},
                attempted_token_ids=wanted, terminal_reason="complete", captured_at=later)

    seen = []
    def refresh(read, clob, position, **kwargs):
        quote = monitor_refresh.monitor_quote_refresh(read, clob, position)
        assert quote is not None and quote.full_depth_action_authority
        position.last_monitor_at = later.isoformat()
        position.last_monitor_prob = 0.0
        position._current_global_held_probability_samples = (0.0, 0.0)
        position._monitor_probability_receipt = dict(compact_receipt)
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = .10
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = .10
        position.last_monitor_best_ask = .12
        setattr(position, monitor_refresh._HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR, True)
        return EdgeContext(p_raw=np.array([]), p_cal=np.array([]), p_market=np.array([.10]),
            p_posterior=0.0, forward_edge=-.10, alpha=0.0,
            confidence_band_lower=-.10, confidence_band_upper=-.10,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING, decision_snapshot_id="physical-current",
            n_edges_found=1, n_edges_after_fdr=1, market_velocity_1h=0, divergence_score=0)

    monkeypatch.setattr(monitor_refresh, "refresh_position", refresh)
    monkeypatch.setattr(day0_hard_fact_exit, "evaluate_hard_fact_exit", lambda **_: (
        day0_hard_fact_exit.HardFactVerdict(action="EXIT_DEAD_BIN",
            reason="current same-station exact running extreme", metric="high", rounded_extreme=26.0,
            source="same_station_fast_tail") if proof_kind == "hard_fact" else None))
    monkeypatch.setattr(exit_lifecycle, "handle_exit_pending_missing", lambda *_a, **_k: {"action": "none"})
    monkeypatch.setattr(exit_lifecycle, "execute_exit", lambda **kwargs: seen.append(kwargs) or "exit_retry:offline_probe")
    deps = SimpleNamespace(
        MonitorResult=lambda **kw: SimpleNamespace(**kw), logger=logging.getLogger("physical-exit-acceptance"),
        cities_by_name={"London": SimpleNamespace(timezone="Europe/London")}, _utcnow=lambda: later)
    results, summary = [], {"monitors": 0, "exits": 0}
    try:
        cycle_runtime.execute_monitoring_phase(conn, Book(.10, .12), portfolio,
            SimpleNamespace(add_monitor_result=results.append), SimpleNamespace(record_exit=lambda _: None),
            summary, deps=deps, read_conn=read_conn)
        assert len(seen) == 1, summary
        assert seen[0]["exit_context"].exit_reason.startswith(
            "DAY0_HARD_FACT_BIN_DEAD" if proof_kind == "hard_fact" else "POSTERIOR_SUPPORT_ZERO_SELL_DOMINATES")
        assert clock[0] < deadline
    finally:
        read_conn.close()
        conn.close()


def _apply_physical_observation(case, *, extreme=None):
    """Normal observation writer and materializer, followed by fresh HELD read.
    This proves an actual physical update reaches q, separately from submission.
    """
    from scripts import hko_ingest_tick
    from src.data.observation_instants_writer import insert_rows
    from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.events.triggers.day0_extreme_updated import (
        build_day0_extreme_updated_event, observation_instant_row_to_day0_observation,
    )
    from zoneinfo import ZoneInfo

    fixture = case.fixture
    metric = case.position.temperature_metric
    observed_at = fixture.cut + timedelta(minutes=1)
    fetched_at = observed_at + timedelta(seconds=5)
    decision_at = observed_at + timedelta(seconds=10)
    value = extreme if extreme is not None else (32.95 if metric == "high" else 27.2)
    high, low = (value, 27.0) if metric == "high" else (32.9, value)
    body = ("Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),"
            "Minimum Air Temperature Since Midnight(degree Celsius)\n"
            f"{observed_at.astimezone(ZoneInfo(fixture.city.timezone)).strftime('%Y%m%d%H%M')},"
            f"HK Observatory,{high},{low}\n")
    snapshot = hko_ingest_tick._parse_hko_extrema_csv(body.encode(), fetched_at_utc=fetched_at.isoformat(),
        capture_started_at_utc=observed_at.isoformat(), response_headers={"content-type": "text/csv"})
    row = hko_ingest_tick._build_hko_extrema_row(snapshot, temperature_c=33,
        accumulator_fetched_at=fetched_at.isoformat(), data_version="v1.wu-native", imported_at=fetched_at.isoformat())
    assert row.raw_response == body
    provenance = json.loads(row.provenance_json)
    assert provenance["raw_body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert provenance["capture_started_at_utc"] == observed_at.isoformat()
    assert provenance["capture_completed_at_utc"] == provenance["written_at_utc"] == fetched_at.isoformat()
    assert insert_rows(fixture.conn, [row]) == 1
    fixture.sql_clock[0] = decision_at
    new = materialize_replacement_forecast_live(fixture.conn, replace(fixture.request,
        computed_at=decision_at, day0_observed_extreme_c=value,
        day0_observed_extreme_observation_time=observed_at.isoformat(), day0_observed_extreme_sample_count=3))
    assert new.ok, new.reason_codes
    fixture.conn.commit()
    observation = observation_instant_row_to_day0_observation(dict(fixture.conn.execute(
        "SELECT * FROM observation_instants ORDER BY utc_timestamp DESC LIMIT 1").fetchone()), metric=metric)
    event = build_day0_extreme_updated_event(observation=observation,
        settlement_semantics=SettlementSemantics.for_city(fixture.city), decision_time=decision_at,
        received_at=decision_at.isoformat())
    adapter._GLOBAL_PROBABILITY_FAMILY_CACHE.clear()
    adapter._GLOBAL_PROBABILITY_FAMILY_INELIGIBLE_CACHE.clear()
    prepared = adapter._prepare_current_global_probability_family(event,
        forecast_conn=fixture.conn, topology_conn=fixture.conn, observation_conn=fixture.conn,
        decision_time=decision_at, max_age=timedelta(seconds=30),
        allow_unobserved_day0_replacement=False, allow_provisional_day0_replacement=True,
        probability_use=adapter._CurrentProbabilityUse.HELD_MONITOR, raw_input_hwm_conn=fixture.conn)
    witness = prepared.probability_witness
    after = S.family_payoff_point_q(witness, bin_id=case.selected.bin_id, side=case.selected.side)
    assert 0 < after < 1
    assert after != pytest.approx(case.q, abs=1e-8), (metric, case.q, after)
    assert witness.source_truth_identity != case.probability.source_truth_identity
    assert isinstance(witness, S.JointOutcomeProbabilityWitness)
    assert case.authority.limit_price() >= D(".05")
    return event, prepared, decision_at, after


def test_new_physical_observation_changes_positive_held_q_before_book_floor(
    _normal_hko_concentrated_sell, record_property,
):
    case = _normal_hko_concentrated_sell
    _, _, _, after = _apply_physical_observation(case)
    record_property("physical_update", json.dumps({
        "metric": case.position.temperature_metric, "before": case.q, "after": after}))


@pytest.fixture
def _high_source_case(tmp_path, monkeypatch, _hko_clock_native_sources):
    source = source_harness._normal_hko_concentrated_sell.__wrapped__(
        tmp_path, monkeypatch, SimpleNamespace(param="high"), _hko_clock_native_sources)
    try:
        yield next(source)
    finally:
        next(source, None)


def _current_source_cut(case, *, event, at, monkeypatch, bid):
    """Actual live adapter callbacks and batch selector, controlled wealth/book."""
    from src.engine import global_batch_runtime as runtime, global_auction_universe as universe
    from src.state.portfolio import PortfolioState
    from src.contracts.executable_cost_curve import BidBookLevel, FeeModel

    hooks = []
    with monkeypatch.context() as composition:
        composition.setattr(runtime, "process_current_global_batch", lambda events, **kwargs:
            hooks.append(kwargs) or SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={}))
        live = adapter.event_bound_live_adapter_from_trade_conn(case.trade,
            get_current_level=lambda: adapter.RiskLevel.GREEN, forecast_conn=case.fixture.conn,
            topology_conn=case.fixture.conn, calibration_conn=case.fixture.conn)
        live.process_global_batch((event,), at)
    callbacks = hooks[-1]
    fresh = callbacks["prepare_held_event"](event, at)
    assert fresh.prepared_global_family is not None, fresh.reason
    tokens = {b.condition_id: (b.yes_token_id, b.no_token_id) for b in case.probability.bindings}
    required = frozenset(token for pair in tokens.values() for token in pair)
    def rebind(witness):
        return universe._rebind_probability_witness_tokens(witness,
            token_map_by_condition=tokens, required_token_ids=required)
    probability = rebind(fresh.prepared_global_family.probability_witness)
    candidate = case.selected
    curve = S.ExecutableSellCurve(token_id=candidate.token_id, side=candidate.side,
        snapshot_id=f"finite-book-{at.isoformat()}", book_hash=f"finite-{bid}-{at.isoformat()}",
        levels=(BidBookLevel(D(bid), D("2")), BidBookLevel(D(".05"), D("3"))),
        fee_model=FeeModel(fee_rate=D(".05")), min_tick=D(".001"), min_order_size=D("1"),
        quote_ttl=timedelta(seconds=30))
    states = tuple((probability.family_key, b.bin_id, b.condition_id, side, token, "NO_ASK",
        curve.book_hash, event.event_id, f"gamma-{b.condition_id}", "False")
        for b in probability.bindings for side, token in (("YES", b.yes_token_id), ("NO", b.no_token_id)))
    book = universe.CurrentGlobalBookEpoch(assets=(), sell_assets=(universe.CurrentGlobalSellAsset(
        family_key=candidate.family_key, bin_id=candidate.bin_id, condition_id=candidate.condition_id,
        gamma_market_id=f"gamma-{candidate.condition_id}", market_event_id=event.event_id,
        side=candidate.side, token_id=candidate.token_id, curve=curve, captured_at_utc=at, neg_risk=False),),
        asset_states=states, captured_at_utc=at, max_age=timedelta(seconds=30),
        witness_identity=universe.current_global_book_epoch_identity(asset_states=states, captured_at_utc=at))
    scope = universe.current_global_auction_scope_from_events((event,), captured_at_utc=at)
    wealth = source_harness._test_wealth_witness(ledger_snapshot_id=case.wealth.ledger_snapshot_id,
        position_set_hash="finite-held-5", wealth_floor_usd=D(".858079"), wealth_ceiling_usd=D("5.858079"),
        spendable_cash_usd=D(".858079"), reservations_usd=D("0"), collateral_authority="CHAIN",
        captured_at_utc=at, max_age=timedelta(seconds=30), native_holdings_micro=((candidate.token_id, 5_000_000),))
    selections, actuations = [], []
    def capture(_event, actuation, _cut, _authority):
        actuations.append(actuation)
        return runtime.GlobalWinnerPreflight(status="BATCH_BLOCKED", reason="OFFLINE_EXECUTION_CONTINUATION")
    with monkeypatch.context() as inputs:
        inputs.setattr(runtime, "scan_current_global_auction_scope", lambda **_: scope)
        inputs.setattr(runtime, "current_portfolio_wealth_witness", lambda *_a, **_k: wealth)
        inputs.setattr(runtime, "current_venue_auction_identity", lambda *_a, **_k: book.witness_identity)
        batch = runtime.process_current_global_batch((event,), decision_time=at, world_conn=case.fixture.conn,
            forecast_conn=case.fixture.conn, trade_conn=case.trade, payload_reader=lambda e: json.loads(e.payload_json),
            prepare_event=callbacks["prepare_event"], prepare_held_event=callbacks["prepare_held_event"],
            actuate_winner=lambda *_: pytest.fail("only explicit offline continuation may submit"),
            stamp_receipt=lambda receipt: receipt, venue_submit_count=lambda: 0, current_execution=lambda *_: None,
            current_time_provider=lambda: at, portfolio_state_provider=lambda: PortfolioState(positions=[case.position],
                authority="canonical_db", authority_scope="runtime_exposure"),
            current_book_epoch_provider=lambda probabilities, cut: ({k: rebind(w) for k, w in probabilities.items()}, book),
            current_capital_limit_resolver=lambda *_: D("100"), preflight_winner=capture,
            actuate_preflighted_winner=lambda *_: pytest.fail("preflight is deliberately captured"),
            selection_telemetry_observer=lambda p, b, f, s, c: selections.append(s))
    assert selections, {key: value.reason for key, value in batch.receipts.items()}
    return SimpleNamespace(batch=batch, selection=selections[-1], actuations=actuations,
                           curve=curve, probability=probability, wealth=wealth)


def _finite_source_exit(case, monkeypatch, tmp_path, *, cancel_remainder=False):
    # One fixed synthetic book straddles the current native-source redecision:
    # .444 has a fragment-safe fee bound of .4193136, between the before/after
    # source probabilities. The old .44 book correctly remains HOLD at both cuts.
    initial = _current_source_cut(case, event=case.event, at=case.fixture.cut, monkeypatch=monkeypatch, bid=".444")
    assert initial.selection.decision.candidate is None
    assert initial.actuations == []
    new_event, current_prepared, now, after = _apply_physical_observation(case, extreme=32.999)
    assert 0 < after < case.q
    if cancel_remainder:
        _cancel_existing_source_remainder(case, monkeypatch, now=now, probability=current_prepared.probability_witness)
    changed = _current_source_cut(case, event=new_event, at=now, monkeypatch=monkeypatch, bid=".444")
    assert changed.actuations, (after, changed.selection.decision.rejection_reasons,
                                  changed.selection.decision.candidate_evaluations)
    selected = changed.selection.decision
    assert selected.candidate.action == "SELL"
    assert selected.candidate.execution_mode == "TAKER_LIMIT"
    assert selected.expected_terminal_wealth.expected_ev_usd > 0
    assert initial.curve.levels == changed.curve.levels
    assert changed.curve.min_tick == D(".001")
    assert changed.curve.levels[0].price % changed.curve.min_tick == 0
    assert selected.shares == D("2") >= changed.curve.min_order_size == D("1")
    assert selected.shares < D("5")
    safe_unit_proceeds = S._global_sell_rounding_safe_proceeds(selected.candidate, D("1"))
    assert safe_unit_proceeds == D(".4193136")
    assert D(str(after)) < safe_unit_proceeds < D(str(case.q))

    # The source arrives at +70s. This synthetic native book remains above the
    # absolute floor only until +120s; the actual selected FAK must finish in it.
    window_end = case.fixture.cut + timedelta(seconds=120)
    submitted_at = now + timedelta(seconds=1)
    actuation = changed.actuations[-1]
    candidate = actuation.decision.candidate
    market = source_harness._jit_market_authority(candidate, tick=".001", min_order_size="1")
    snapshot = replace(market.snapshot, captured_at=submitted_at,
                       freshness_deadline=submitted_at + timedelta(seconds=30))
    market = replace(market, snapshot=snapshot)
    def venue_book(at):
        bid = ".444" if at < window_end else ".04"
        return {"asset_id": candidate.token_id, "tick_size": ".001", "min_order_size": "1",
                "bids": [{"price": bid, "size": "2"}, {"price": ".05" if at < window_end else ".03", "size": "3"}],
                "asks": [{"price": ".45", "size": "5"}]}
    rebound = adapter._global_sell_candidate_from_raw_book(candidate, venue_book(submitted_at),
        captured_at_utc=submitted_at, market_authority=market)
    authority = GlobalSellExecutionAuthority.from_current(actuation=actuation, jit_candidate=rebound)
    snapshot = replace(snapshot, raw_orderbook_hash=rebound.executable_sell_curve.book_hash)
    source_harness.insert_snapshot(case.trade, snapshot)
    case.trade.commit()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return submitted_at.astimezone(tz) if tz else submitted_at.replace(tzinfo=None)
    finite_case = SimpleNamespace(fixture=SimpleNamespace(cut=submitted_at), trade=case.trade,
        ranked=replace(changed.selection, actuation=actuation), authority=authority, selected=candidate,
        rebound=rebound, snapshot=snapshot, curve=changed.curve, probability=actuation.probability_witness,
        q=after, clock=Clock, window_end=window_end)
    confirmed = _execute_confirmed_source_sale(finite_case, monkeypatch, tmp_path)
    assert confirmed.status == "filled"
    assert submitted_at < window_end
    with pytest.raises(ValueError):
        adapter._global_sell_candidate_from_raw_book(candidate, venue_book(window_end),
            captured_at_utc=window_end, market_authority=market)
    return new_event, submitted_at, finite_case


def test_event_clock_hold_then_physical_deterioration_selects_early_taker(_high_source_case, monkeypatch, tmp_path):
    _finite_source_exit(_high_source_case, monkeypatch, tmp_path)


def test_same_family_hedge_can_make_hold_win_despite_positive_cash_edge():
    """The held YES leg protects the low-wealth outcome of its NO-heavy family."""
    candidate = _statistical_sell(q=.10, bid=".20")
    naked = solver_harness._global_select((candidate,), floor="1", ceiling="11", cash="1")
    assert naked.candidate is candidate
    endowment = S.CandidatePortfolioEndowment(
        loss_wealth_floor_usd=D("1001"), win_wealth_floor_usd=D("11"),
        current_token_shares=D("10"), ledger_snapshot_id="ledger-current")
    hedged = solver_harness._global_select((candidate,), floor="1", ceiling="1001", cash="1",
        candidate_portfolio_endowment_resolver=lambda _: endowment)
    assert .10 < .20
    assert hedged.candidate is None
    assert hedged.rejection_reasons[candidate.candidate_id] == "NON_POSITIVE_EXPECTED_OBJECTIVE"


def _cancel_existing_source_remainder(case, monkeypatch, *, now, probability):
    """Revalue and cancel a pre-existing same-token BUY in this exact TRADE DB.

    The already-ACKED remainder is replay input. Its original admission is not
    fabricated or tested here. Its source, holding, cancel and later SELL use
    one token/condition/position and one canonical command ledger.
    """
    from src.execution import executor, staleness_cancel
    from src.execution.exit_safety import request_cancel_for_command
    from src.state.venue_command_repo import insert_submission_envelope
    from src.engine.global_auction_universe import _rebind_probability_witness_tokens
    from src.engine.global_batch_runtime import _bind_selection_holdings
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
    from src.state.portfolio import PortfolioState
    from src.control.cutover_guard import CutoverDecision, CutoverState
    candidate = case.selected
    binding = next(b for b in case.probability.bindings if b.bin_id == candidate.bin_id)
    tokens = {b.condition_id: (b.yes_token_id, b.no_token_id) for b in case.probability.bindings}
    current = _rebind_probability_witness_tokens(probability, token_map_by_condition=tokens,
        required_token_ids=frozenset(t for pair in tokens.values() for t in pair))
    snapshot = replace(case.snapshot, snapshot_id="preexisting-buy-remainder", condition_id=binding.condition_id,
        yes_token_id=binding.yes_token_id, no_token_id=binding.no_token_id,
        token_map_raw={"YES": binding.yes_token_id, "NO": binding.no_token_id},
        orderbook_top_bid=D(".444"), orderbook_top_ask=D(".60"), min_tick_size=D(".001"),
        captured_at=now - timedelta(minutes=2), freshness_deadline=now + timedelta(minutes=2))
    source_harness.insert_snapshot(case.trade, snapshot)
    monkeypatch.setattr("src.data.polymarket_client.resolve_funder_address", lambda: "0x" + "12" * 20)
    envelope = executor._build_pre_submit_envelope(case.trade, command_id="rest-before-deterioration",
        snapshot_id=snapshot.snapshot_id, token_id=candidate.token_id, side="BUY", price=.50, size=1.6,
        order_type="GTC", post_only=True, captured_at=(now - timedelta(minutes=1)).isoformat(), intent_kind="ENTRY")
    insert_submission_envelope(case.trade, envelope, envelope_id="rest-envelope")
    case.trade.execute("""INSERT INTO venue_commands (
        command_id,snapshot_id,envelope_id,position_id,decision_id,idempotency_key,
        intent_kind,market_id,token_id,side,size,price,venue_order_id,state,created_at,updated_at,q_version)
        VALUES ('rest-before-deterioration',?,'rest-envelope',?,'prior-entry','prior-entry-remainder',
        'ENTRY',?,?,'BUY',1.6,.50,'prior-rest-order','ACKED',?,?,?)""",
        (snapshot.snapshot_id,candidate.position_id,candidate.condition_id,candidate.token_id,
         (now - timedelta(minutes=1)).isoformat(),now.isoformat(),case.probability.q_version))
    case.trade.execute("""INSERT INTO venue_order_facts (
        venue_order_id,command_id,state,remaining_size,matched_size,source,observed_at,local_sequence,raw_payload_hash)
        VALUES ('prior-rest-order','rest-before-deterioration','LIVE','1.6','0','REST',?,0,?)""",
        (now.isoformat(),"f" * 64))
    case.trade.execute("INSERT INTO collateral_reservations (command_id,reservation_type,amount,created_at) VALUES ('rest-before-deterioration','PUSD_BUY',800000,?)", (now.isoformat(),))
    case.trade.commit()
    prepared = PreparedGlobalFamily(decision_id="current-rest-value", probability_witness=current, candidate_seeds=())
    wealth = source_harness._test_wealth_witness(ledger_snapshot_id=case.wealth.ledger_snapshot_id,
        position_set_hash="rest-own-reservation-released", wealth_floor_usd=D(".858079"),
        wealth_ceiling_usd=D("5.858079"), spendable_cash_usd=D(".858079"), reservations_usd=D("0"),
        collateral_authority="CHAIN", captured_at_utc=now, max_age=timedelta(seconds=30),
        native_holdings_micro=((candidate.token_id,5_000_000),))
    holdings = _bind_selection_holdings({"current": prepared},
        portfolio_state=PortfolioState(positions=[case.position]), wealth_witness=wealth)["current"].holdings_snapshot
    row = dict(case.trade.execute("SELECT * FROM executable_market_snapshots WHERE snapshot_id=?", (snapshot.snapshot_id,)).fetchone())
    # value_standing_entry reads the canonical serialized full-depth field.
    row["orderbook_depth_json"] = json.dumps({"asset_id": candidate.token_id,
        "asks": [{"price": ".60", "size": "5"}], "bids": [{"price": ".444", "size": "5"}]})
    value = staleness_cancel.value_standing_entry(
        {"command_id":"rest-before-deterioration","venue_order_id":"prior-rest-order",
         "token_id":candidate.token_id,"snapshot_id":snapshot.snapshot_id,"size":"1.6","matched_size":"0",
         "price":".50","q_version":case.probability.q_version},
        family=(case.position.city,case.position.target_date,case.position.temperature_metric),
        snapshot=row, prepared=prepared, wealth=wealth, holdings_snapshot=holdings,
        fractional_kelly_multiplier=D(".125"), capital_limit_usd=D("100"),
        payoff_q_correction_resolver=None, resolution_at=now + timedelta(hours=6), now=now)
    assert value.action == "CANCEL", value
    assert value.evidence.get("authority_valid") is True, value
    assert "AUTHORITY" not in value.reason and "IDENTITY" not in value.reason, value.reason
    monkeypatch.setattr("src.execution.exit_safety.gate_for_intent", lambda _: CutoverDecision(
        False, True, False, None, CutoverState.LIVE_ENABLED))
    cancellations = []
    def cancel(order_id):
        row = case.trade.execute("SELECT state FROM venue_commands WHERE command_id='rest-before-deterioration'").fetchone()
        assert row[0] == "CANCEL_PENDING"
        cancellations.append(order_id)
        return {"canceled": True, "orderID": order_id}
    result = request_cancel_for_command(case.trade, "rest-before-deterioration", cancel)
    assert result.status == "CANCELED", result
    assert cancellations == ["prior-rest-order"]
    case.trade.commit()
    assert case.trade.execute("SELECT state FROM venue_commands WHERE command_id='rest-before-deterioration'").fetchone()[0] == "CANCELLED"


def test_same_ledger_cancel_partial_exit_rejects_stale_reentry_and_duplicate_sell(
    _high_source_case, monkeypatch, tmp_path,
):
    case = _high_source_case
    new_event, now, current = _finite_source_exit(case, monkeypatch, tmp_path, cancel_remainder=True)
    # The old SELL cannot submit again after confirmed reduction. Until the
    # chain cut reflects it, the filled-command fence still owns those shares.
    with pytest.raises(ValueError, match="GLOBAL_SELL_POSITION_SHARES_SUPERSEDED|GLOBAL_SELL_POSITION_EXIT_ALREADY_ACTIVE"):
        adapter._current_global_sell_position(case.trade, current.selected)
    curve = source_harness.ExecutableCostCurve(token_id=case.selected.token_id, side=case.selected.side,
        snapshot_id="stale-reentry-book", book_hash="stale-reentry-book-hash",
        levels=(source_harness.BookLevel(D(".50"), D("5")),),
        fee_model=source_harness.FeeModel(fee_rate=D(".05")), min_tick=D(".001"),
        min_order_size=D("1"), quote_ttl=timedelta(seconds=30))
    stale_buy = S.GlobalSingleOrderCandidate(candidate_id="stale-reentry",
        condition_id=case.selected.condition_id, token_id=case.selected.token_id, side=case.selected.side,
        family_key=case.selected.family_key, bin_id=case.selected.bin_id,
        probability_witness_identity=case.probability.witness_identity,
        book_snapshot_id=curve.snapshot_id, book_captured_at_utc=now,
        execution_curve_identity=S.executable_curve_identity(curve),
        ledger_snapshot_id=case.wealth.ledger_snapshot_id, executable_cost_curve=curve,
        resolution_identity=case.probability.resolution_identity, neg_risk=False)
    stale = SimpleNamespace(probability_witness=case.probability,
                            decision=SimpleNamespace(candidate=stale_buy))
    with pytest.raises(ValueError, match="GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED|GLOBAL_ACTUATION_PROBABILITY_USE_DIVERGED"):
        adapter._current_global_actuation_prepared_family(new_event, global_actuation=stale,
            forecast_conn=case.fixture.conn, topology_conn=case.fixture.conn,
            observation_conn=case.fixture.conn, decision_time=now)
    commands = [tuple(row) for row in case.trade.execute("SELECT side,state FROM venue_commands ORDER BY side")]
    assert commands == [("BUY", "CANCELLED"), ("SELL", "FILLED")]
    assert case.trade.execute("SELECT COUNT(*) FROM venue_trade_facts WHERE state='CONFIRMED'").fetchone()[0] == 1


@pytest.fixture
def _residual_trade_conn():
    from tests.test_exit_safety import conn as connection_fixture
    yield from connection_fixture.__wrapped__()


@pytest.mark.parametrize("direction", ("buy_yes", "buy_no"))
def test_exact_proof_terminal_partial_reconciles_then_monitor_exits_residual(
    _residual_trade_conn, monkeypatch, direction, record_property,
):
    """Source-bound execution relationship; no provider transport acquisition.

    The shared fixture writes an accepted NOAA print and binds its real source
    receipt. Chain reconciliation, monitor decision/writer, lifecycle, executor
    and canonical fill folding compose two FAK orders on one holding. Venue
    transport and probability refresh are controlled; source-currentness is
    revalidated and must never be patched when its contract evolves.
    """
    import inspect
    import logging
    import numpy as np
    from tests import test_exit_safety as H
    conn = _residual_trade_conn
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime, monitor_refresh
    from src.execution import executor, exit_lifecycle, day0_hard_fact_exit
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger
    from src.state.fill_dedup import economic_exit_fills_for_position
    from src.state.portfolio import PortfolioState
    from src.state.venue_command_repo import append_trade_fact
    from tests.test_day0_hard_fact_exit import _DeepBookClob

    class LifecycleBookClob(_DeepBookClob):
        def __getattribute__(self, name):
            if name in {"get_open_orders", "get_order_status"}:
                return object.__getattribute__(self, name)
            return super().__getattribute__(name)
        def get_open_orders(self): return []
        def get_order_status(self, order_id):
            return {"status":"MATCHED", "remaining_size":"0", "size_matched":"1"}

    position, context, t0 = H._exact_zero_exit_case(conn, monkeypatch, direction=direction, shares=2.0)
    position.entered_at = (t0-timedelta(hours=1)).isoformat()
    token = H.YES_TOKEN if direction == "buy_yes" else H.NO_TOKEN
    portfolio = PortfolioState(positions=[position])
    clock, balance, calls = [t0], [2.0], []
    deadline = t0 + timedelta(seconds=30)
    class EventClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(H, "datetime", EventClock)
    monkeypatch.setattr("src.state.portfolio.datetime", EventClock)
    monkeypatch.setattr(executor, "datetime", EventClock)
    monkeypatch.setattr("src.state.collateral_ledger.datetime", EventClock)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: clock[0])
    H._enable_exit_submit_prereqs(conn, monkeypatch, ctf_shares=2.0)
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(H._snapshot(pusd=1_000_000_000, ctf={token:2.0}, captured_at=t0))
    configure_global_ledger(ledger)
    monkeypatch.setattr("src.data.polymarket_client.resolve_funder_address", lambda: "0x" + "12" * 20)
    submitted = []

    class FakeClient:
        def _ensure_v2_adapter(self): return self
        def get_ctf_collateral_payload(self, *, token_ids):
            assert token_ids == [token]
            return H._fresh_exit_collateral_payload(token_id=token, shares=balance[0])
        def get_collateral_payload(self):
            return H._fresh_exit_collateral_payload(token_id=token, shares=balance[0])
        def bind_submission_envelope(self, envelope): self.envelope = envelope
        def bind_signed_submission_identity_persister(self, persister): self.persister = persister
        def place_limit_order(self, **kwargs):
            assert clock[0] < deadline
            assert kwargs["token_id"] == token
            assert kwargs["side"] == "SELL" and kwargs["order_type"] == "FAK"
            assert kwargs["price"] == pytest.approx(.10)
            calls.append(kwargs)
            index = len(calls)
            assert kwargs["size"] == pytest.approx(2.0 if index == 1 else 1.0)
            balance[0] -= 1.0
            raw = {"success":True,"status":"MATCHED","orderID":f"residual-order-{index}",
                   "size_matched":"1","avgPrice":"0.10","remaining_size":"0",
                   "tradeIDs":[f"residual-trade-{index}"],"transactionHashes":[f"0xsynthetic{index}"]}
            final = self.envelope.with_updates(raw_response_json=json.dumps(raw, sort_keys=True, separators=(",",":")),
                                               order_id=raw["orderID"])
            return {**raw,"_venue_submission_envelope":final.to_dict()}

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    fields = inspect.signature(executor.create_exit_order_intent).parameters
    def private_submit(**kwargs):
        intent = executor.create_exit_order_intent(**{k:v for k,v in kwargs.items() if k in fields})
        result = executor.execute_exit_order(intent, conn=conn, decision_id=kwargs["decision_id"], q_version=kwargs["q_version"])
        submitted.append(result)
        if result.venue_ack_received:
            n = len(calls)
            append_trade_fact(conn, trade_id=f"residual-trade-{n}", venue_order_id=result.order_id,
                command_id=result.command_id, state="CONFIRMED", filled_size="1", fill_price=".10", fee_paid_micro=0,
                source="WS_USER", observed_at=clock[0].isoformat(), venue_timestamp=clock[0].isoformat(),
                raw_payload_hash=hashlib.sha256(f"confirm-{n}".encode()).hexdigest(),
                raw_payload_json={"status":"CONFIRMED","test_only":True})
            conn.commit()
        return result
    monkeypatch.setattr(exit_lifecycle, "place_sell_order", private_submit)
    try:
        authority = exit_lifecycle.BranchwiseDominantSellAuthority.from_current(position, context)
        first = exit_lifecycle.execute_exit(portfolio, position, context,
            clob=SimpleNamespace(get_order_status=lambda _: {"status":"OPEN"}),
            conn=conn, branchwise_sell_authority=authority)
        assert first.startswith("position_reduced:"), first
        assert position.effective_shares == pytest.approx(1.0)
        assert position.exit_state == "" and position.last_exit_order_id == ""
        assert len(calls) == 1
        clock[0] = t0 + timedelta(seconds=5)
        reconciliation = reconcile(portfolio, [ChainPosition(token_id=token, size=1.0, avg_price=.40,
            cost=.40, condition_id=position.condition_id, balance_authority="CHAIN", balance_source="synthetic")], conn=conn)
        conn.commit()
        assert position.effective_shares == pytest.approx(1.0), reconciliation
        ledger.set_snapshot(H._snapshot(pusd=1_000_100_000, ctf={token:1.0}, captured_at=clock[0]))
        H._ensure_snapshot(conn, snapshot_id="residual-fresh-book", selected_outcome_token_id=token,
            outcome_label="YES" if direction=="buy_yes" else "NO", captured_at=clock[0],
            freshness_deadline=deadline, orderbook_top_bid=".10", orderbook_top_ask=".12", min_order_size="5")
        conn.commit()
        clock[0] = t0 + timedelta(seconds=10)
        # The source proof is unchanged between fills. Preserve the helper's
        # full receipt and compact digest; fresh book/holding/monitor evidence
        # must revalidate it rather than inventing a new probability identity.
        receipt = dict(context.probability_receipt)
        def refresh(_conn, clob, pos, **kwargs):
            quote = monitor_refresh.monitor_quote_refresh(_conn, clob, pos)
            assert quote is not None and quote.full_depth_action_authority
            pos.last_monitor_prob = 0.0
            pos.last_monitor_prob_is_fresh = True
            pos.last_monitor_at = clock[0].isoformat()
            pos._current_global_held_probability_samples = (0.0,0.0)
            pos._monitor_probability_receipt = receipt
            pos.last_monitor_best_bid, pos.last_monitor_best_ask = quote.best_bid, quote.best_ask
            pos.last_monitor_market_price = quote.mark_price
            pos.last_monitor_market_price_is_fresh = True
            setattr(pos, monitor_refresh._HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR, True)
            return EdgeContext(p_raw=np.array([]),p_cal=np.array([]),p_market=np.array([.10]),
                p_posterior=0.0,forward_edge=-.10,alpha=0.0,confidence_band_lower=-.10,
                confidence_band_upper=-.10,entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
                decision_snapshot_id="residual-source",n_edges_found=1,n_edges_after_fdr=1,
                market_velocity_1h=0.0,divergence_score=0.0)
        monkeypatch.setattr(monitor_refresh, "refresh_position", refresh)
        monkeypatch.setattr(day0_hard_fact_exit, "evaluate_hard_fact_exit", lambda **_: None)
        summary = {"monitors":0,"exits":0}
        results = []
        deps = SimpleNamespace(MonitorResult=lambda **kw:SimpleNamespace(**kw),
            logger=logging.getLogger("residual-probe"),cities_by_name={"London":SimpleNamespace(timezone="UTC")},
            _utcnow=lambda:clock[0])
        cycle_runtime.execute_monitoring_phase(conn, LifecycleBookClob(.10,.12), portfolio,
            SimpleNamespace(add_monitor_result=results.append),SimpleNamespace(record_exit=lambda _:None),
            summary,deps=deps,read_conn=conn)
        final = conn.execute("SELECT phase,shares,chain_shares,realized_pnl_usd FROM position_current WHERE position_id=?",(position.trade_id,)).fetchone()
        detail = {"first": first, "reconciliation": reconciliation, "summary": summary,
                  "calls": calls, "submitted": [(r.status, r.reason, r.command_state) for r in submitted],
                  "final": dict(final)}
        record_property("two_order_residual", json.dumps(detail, default=str, sort_keys=True))
        assert len(calls) == 2, detail
        assert final["phase"] == "economically_closed", detail
        assert clock[0] < deadline
        assert balance[0] == 0.0
        assert final["realized_pnl_usd"] == pytest.approx(-.60)
        before = tuple(final)
        first_command = conn.execute("SELECT command_id FROM venue_commands WHERE venue_order_id='residual-order-1'").fetchone()[0]
        append_trade_fact(conn,trade_id="residual-trade-1",venue_order_id="residual-order-1",command_id=first_command,
            state="CONFIRMED",filled_size="1",fill_price=".10",fee_paid_micro=0,source="WS_USER",
            observed_at=(clock[0]+timedelta(seconds=1)).isoformat(),venue_timestamp=t0.isoformat(),
            raw_payload_hash=hashlib.sha256(b"late-same-confirmation").hexdigest(),raw_payload_json={"status":"CONFIRMED","late_duplicate":True})
        conn.commit()
        clock[0] += timedelta(seconds=1)
        recovery = exit_lifecycle.check_pending_exits(portfolio, LifecycleBookClob(.10,.12), conn=conn)
        assert recovery["filled"] == 0 and recovery["retried"] == 0
        economic = economic_exit_fills_for_position(conn,position_id=position.trade_id)
        assert sum(D(str(f.quantity)) for f in economic) == D("2")
        assert tuple(conn.execute("SELECT phase,shares,chain_shares,realized_pnl_usd FROM position_current WHERE position_id=?",(position.trade_id,)).fetchone()) == before
        assert len(calls) == 2
    finally:
        H._clear_exit_submit_prereqs()
