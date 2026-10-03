# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: INV-47 allocation gate scope — SELL/HOLD never depend on positive room
"""Negative spendable cash vetoes every BUY, never the held SELL/HOLD cut."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

import src.engine.global_auction_universe as universe
import src.engine.qkernel_spine_bridge as bridge
from src.contracts.executable_cost_curve import (
    BidBookLevel,
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.contracts.strategy_capital_allocation import StrategyCapitalAllocationWitness
from src.engine.global_auction_universe import (
    CurrentGlobalBookAsset,
    CurrentGlobalBookEpoch,
    CurrentGlobalSellAsset,
    current_global_auction_scope_from_events,
    current_global_book_epoch_identity,
    current_portfolio_wealth_witness,
)
from src.engine.global_single_order_auction import select_prepared_global_auction
from src.engine.native_holdings import NativeHolding, NativeHoldingsSnapshot
from src.execution.executor import _current_global_increment_wealth_component
from src.solve.solver import (
    CurrentFamilyProbabilityAuthority,
    ExecutableSellCurve,
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    PortfolioWealthWitness,
    joint_probability_witness_identity,
    portfolio_wealth_identity,
)
from src.state.portfolio import PortfolioState
from tests.integration.test_w3_solve_seam_g3 import (
    _global_scope_event,
    _wealth_test_conn,
)

REASON = "CURRENT_WEALTH_SPENDABLE_CASH_INVALID"


def _over_reserved_conn(at: _dt.datetime):
    # 25 pUSD on chain; a stale OPEN obligation for a filled BUY claims 30.
    conn = _wealth_test_conn(captured_at=at)
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)",
        ("cmd", "position-1", "token-1", "BUY", 100.0, 0.30, "ENTRY", "FILLED"),
    )
    conn.execute(
        "INSERT INTO entry_exposure_obligations VALUES (?,?,?,?,?,?,?)",
        ("cmd", "OPEN", "token-1", 100.0, 30.0, 0, at.isoformat()),
    )
    return conn


def _witness(conn, at):
    return current_portfolio_wealth_witness(
        conn,
        decision_at_utc=at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=PortfolioState(
            authority="canonical_db", authority_scope="runtime_exposure"
        ),
    )


def _wealth(at, *, reason, spendable=Decimal("0")):
    floor = Decimal("27")
    policy = StrategyCapitalAllocationWitness.build(
        capital_basis_usd=floor,
        committed_capital_usd=Decimal("0"),
        venue_spendable_cash_usd=spendable,
        allocation={"mode": "wallet_total"},
    )
    fields = dict(
        ledger_snapshot_id="ledger",
        position_set_hash="positions",
        wealth_floor_usd=floor,
        wealth_ceiling_usd=floor + Decimal("10"),
        spendable_cash_usd=spendable,
        reservations_usd=Decimal("30"),
        collateral_authority="CHAIN",
        captured_at_utc=at,
        buy_cash_unavailable_reason=reason,
    )
    return PortfolioWealthWitness(
        **fields,
        strategy_capital_allocation=policy,
        max_age=_dt.timedelta(seconds=30),
        witness_identity=portfolio_wealth_identity(
            **fields,
            strategy_capital_allocation_identity=policy.witness_identity,
        ),
        native_holdings_micro=(("yes-token", 10_000_000),),
    )


def test_negative_spendable_is_typed_buy_state_not_exception():
    at = _dt.datetime(2026, 10, 2, 23, 50, tzinfo=_dt.timezone.utc)
    witness = _witness(_over_reserved_conn(at), at)

    assert witness.buy_cash_unavailable_reason == REASON
    assert witness.spendable_cash_usd == Decimal("0")
    assert witness.strategy_capital_allocation.remaining_buy_capacity_usd == 0
    # Floor stays the observed chain balance (25 pUSD + 2 legacy USDC.e).
    assert witness.wealth_floor_usd == Decimal("27")
    # Deterministic and bound to the typed state.
    assert (
        witness.witness_identity
        == _witness(_over_reserved_conn(at), at).witness_identity
    )
    with pytest.raises(ValueError, match="identity does not bind"):
        PortfolioWealthWitness(
            **{
                **{f: getattr(witness, f) for f in witness.__dataclass_fields__},
                "buy_cash_unavailable_reason": None,
            }
        )


def test_buy_cash_state_changes_economic_identity_and_requires_zero_cash():
    at = _dt.datetime(2026, 10, 2, 23, 50, tzinfo=_dt.timezone.utc)
    available = _wealth(at, reason=None)
    unavailable = _wealth(at, reason=REASON)
    assert available.economic_identity != unavailable.economic_identity
    assert available.witness_identity != unavailable.witness_identity
    with pytest.raises(ValueError, match="zero spendable cash"):
        _wealth(at, reason=REASON, spendable=Decimal("1"))


def test_executor_increment_binding_rejects_buy_with_typed_reason(monkeypatch):
    witness = SimpleNamespace(buy_cash_unavailable_reason=REASON, economic_identity="w")
    monkeypatch.setattr(
        universe, "current_portfolio_wealth_witness", lambda *_a, **_k: witness
    )
    component = _current_global_increment_wealth_component(
        object(), {"global_wealth_economic_identity": "w"}
    )
    assert component["allowed"] is False
    assert component["reason"] == REASON


def test_executor_buy_pre_submit_reads_typed_reason(monkeypatch):
    from src.execution import executor

    witness = SimpleNamespace(buy_cash_unavailable_reason=REASON)
    monkeypatch.setattr(
        universe, "current_portfolio_wealth_witness", lambda *_a, **_k: witness
    )
    assert executor._current_wealth_buy_cash_unavailable_reason(object()) == REASON

    def broken(*_a, **_k):
        raise ValueError("CURRENT_WEALTH_CHAIN_POSITION_SET_MISMATCH")

    monkeypatch.setattr(universe, "current_portfolio_wealth_witness", broken)
    # A witness that cannot be built stays owned by the existing capital gates.
    assert executor._current_wealth_buy_cash_unavailable_reason(object()) is None


def test_reactor_preflight_vetoes_buy_but_not_sell(monkeypatch):
    import src.engine.event_reactor_adapter as era

    current = SimpleNamespace(buy_cash_unavailable_reason=REASON, economic_identity="w")
    monkeypatch.setattr(
        universe, "current_portfolio_wealth_witness", lambda *_a, **_k: current
    )
    now = _dt.datetime(2026, 10, 2, 23, 50, tzinfo=_dt.timezone.utc)

    def actuation(action):
        candidate = SimpleNamespace(action=action) if action else SimpleNamespace()
        return SimpleNamespace(
            wealth_economic_identity="w",
            decision=SimpleNamespace(candidate=candidate),
        )

    assert (
        era._global_actuation_current_wealth_block_reason(
            object(), global_actuation=actuation(None), decision_time=now
        )
        == f"GLOBAL_PREFLIGHT_{REASON}"
    )
    assert (
        era._global_actuation_current_wealth_block_reason(
            object(), global_actuation=actuation("SELL"), decision_time=now
        )
        is None
    )


def test_held_sell_selectable_and_no_buy_candidate_in_same_cut():
    at = _dt.datetime(2026, 10, 2, 23, 50, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="buy-cash-unavailable")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=at)
    family = scope.family_keys[0]
    # Held YES on "bin" likely loses (q=0.05) against a 0.60 bid: SELL beats
    # HOLD. The "other" YES ask at 0.10 vs q=0.95 is a strong BUY when funded.
    fields = dict(
        family_key=family,
        bindings=(
            OutcomeTokenBinding("bin", "condition", "yes-token", "no-token"),
            OutcomeTokenBinding("other", "other-condition", "other-yes", "other-no"),
        ),
        q_version="q", resolution_identity="resolution", topology_identity="topology",
        posterior_identity_hash="posterior", source_truth_identity="source",
        authority_certificate_hash="certificate", band_alpha=0.05,
        band_basis="current-evidence",
        yes_point_q=np.asarray((0.05, 0.95)),
        yes_q_samples=np.tile((0.05, 0.95), (400, 1)), captured_at_utc=at,
    )
    probability = JointOutcomeProbabilityWitness(
        **fields, max_age=_dt.timedelta(seconds=30),
        witness_identity=joint_probability_witness_identity(**fields),
    )
    holding = NativeHolding("held", family, "bin", "YES", "yes-token", Decimal("10"))
    prepared = bridge.PreparedGlobalFamily(
        decision_id="decision", probability_witness=probability, candidate_seeds=(),
        holdings_snapshot=NativeHoldingsSnapshot(family, "ledger", (holding,)),
    )
    fee = FeeModel(fee_rate=Decimal("0"))
    sell_curve = ExecutableSellCurve(
        token_id="yes-token", side="YES", snapshot_id="snapshot", book_hash="book",
        levels=(BidBookLevel(Decimal("0.60"), Decimal("10")),),
        fee_model=fee, min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"), quote_ttl=_dt.timedelta(seconds=30),
    )
    sell_asset = CurrentGlobalSellAsset(
        family, "bin", "condition", "gamma", "market-event", "YES", "yes-token",
        sell_curve, at, False,
    )
    buy_curve = ExecutableCostCurve(
        token_id="other-yes", side="YES", snapshot_id="snapshot-2", book_hash="book-2",
        levels=(BookLevel(Decimal("0.10"), Decimal("100")),),
        fee_model=fee, min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"), quote_ttl=_dt.timedelta(seconds=30),
    )
    buy_asset = CurrentGlobalBookAsset(
        family, "other", "other-condition", "gamma", "market-event", "YES",
        "other-yes", buy_curve, at, False,
    )
    states = (
        (family, "bin", "condition", "YES", "yes-token", "EXECUTABLE", "book",
         "market-event", "gamma", "False"),
        (family, "other", "other-condition", "YES", "other-yes", "EXECUTABLE",
         "book-2", "market-event", "gamma", "False"),
    )
    epoch = CurrentGlobalBookEpoch(
        assets=(buy_asset,), sell_assets=(sell_asset,), asset_states=states,
        captured_at_utc=at, max_age=_dt.timedelta(seconds=30),
        witness_identity=current_global_book_epoch_identity(
            asset_states=states, captured_at_utc=at
        ),
    )

    def select(wealth):
        return select_prepared_global_auction(
            {event.event_id: prepared}, selection_epoch_identity="selection",
            selection_cut_at_utc=at, current_scope=scope,
            current_scope_identity_resolver=lambda: scope.scope_identity,
            venue_universe_identity=epoch.witness_identity,
            current_venue_universe_identity_resolver=lambda: epoch.witness_identity,
            universe_max_age=_dt.timedelta(seconds=30),
            current_probability_resolver=lambda _f: (
                CurrentFamilyProbabilityAuthority.from_witness(probability)
            ),
            current_execution_resolver=lambda c: epoch.execution_authority(
                c, checked_at_utc=at
            ),
            current_wealth_identity_resolver=lambda: wealth.economic_identity,
            wealth_witness=wealth,
            capital_limit_usd=(
                wealth.strategy_capital_allocation.remaining_buy_capacity_usd
            ),
            decision_at_utc=at, book_epoch=epoch,
        )

    # Control: funded, the same cut mints a BUY candidate.
    funded = select(_wealth(at, reason=None, spendable=Decimal("27")))
    funded_decision = (
        funded.actuation.decision if funded.actuation is not None else funded.decision
    )
    assert "BUY" in {row.action for row in funded_decision.candidate_evaluations}

    result = select(_wealth(at, reason=REASON))
    assert result.actuation is not None, result.decision.no_trade_reason
    decision = result.actuation.decision
    assert decision.candidate.action == "SELL"
    assert decision.candidate.token_id == "yes-token"
    assert {row.action for row in decision.candidate_evaluations} == {"SELL"}
