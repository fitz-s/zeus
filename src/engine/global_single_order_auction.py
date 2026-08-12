"""Pure cross-family coordinator for one current executable order.

This module has no venue or DB imports.  It joins already-prepared complete
family simplexes into one auction, materializes every native full-depth order,
and delegates the terminal-wealth objective to ``select_global_single_order``.
Actuation and JIT recapture remain outside this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from src.contracts.global_auction_receipt import GlobalAuctionReceiptRef
from src.engine.global_auction_universe import (
    CurrentGlobalBookEpoch,
    CurrentGlobalAuctionScope,
    global_universe_witness_from_scope,
)
from src.solve.solver import (
    CandidatePortfolioEndowment,
    CurrentExecutionAuthority,
    CurrentFamilyProbabilityAuthority,
    CurrentMakerFillWitness,
    DeterministicBinPayoffWitness,
    FamilyPortfolioEndowment,
    GlobalSingleOrderAnyCandidate,
    GlobalSingleOrderDecision,
    GlobalSingleOrderSellCandidate,
    PortfolioWealthWitness,
    global_candidates_from_native,
    global_sell_candidate_from_holding,
    executable_curve_identity,
    select_global_single_order,
)


@dataclass(frozen=True)
class GlobalHoldingAuctionCoverage:
    """One exact held-position obligation in a current global auction."""

    position_id: str
    family_key: str
    bin_id: str | None
    condition_id: str
    side: str
    token_id: str
    held_shares: Decimal
    ledger_snapshot_id: str
    probability_witness_identity: str | None
    probability_content_identity: str | None
    wealth_economic_identity: str
    selection_epoch_identity: str
    book_epoch_identity: str
    selection_cut_at_utc: datetime
    decision_at_utc: datetime
    book_deadline_at_utc: datetime
    status: str
    candidate_id: str | None = None
    candidate_ids: tuple[str, ...] = ()
    reason: str | None = None
    bin_label: str | None = None
    canonical_bin_identity: str | None = None
    sell_book_witness_identity: str | None = None
    book_state: str = "UNKNOWN"
    sell_exit_authority_status: str = "not_applicable"
    sell_exit_authority_reason: str = "non_day0_family"
    sell_action_authority_identity: str = "non_day0_default_authority"

    def __post_init__(self) -> None:
        evaluated = self.status == "EVALUATED"
        excluded = self.status == "EXCLUDED"
        book_state = str(self.book_state or "").strip().upper()
        canonical_bin_identity = str(
            self.canonical_bin_identity or f"condition:{self.condition_id}"
        ).strip()
        object.__setattr__(
            self,
            "canonical_bin_identity",
            canonical_bin_identity,
        )
        object.__setattr__(self, "book_state", book_state)
        candidate_ids = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (
                    self.candidate_ids
                    or ((self.candidate_id,) if self.candidate_id else ())
                )
                if str(value or "").strip()
            )
        )
        object.__setattr__(self, "candidate_ids", candidate_ids)
        if candidate_ids and self.candidate_id is None:
            object.__setattr__(self, "candidate_id", candidate_ids[0])
        if (
            not (evaluated or excluded)
            or not all(
                str(value or "").strip()
                for value in (
                    self.position_id,
                    self.family_key,
                    self.condition_id,
                    self.token_id,
                    self.ledger_snapshot_id,
                    self.wealth_economic_identity,
                    self.selection_epoch_identity,
                    self.book_epoch_identity,
                    canonical_bin_identity,
                    self.sell_exit_authority_status,
                    self.sell_exit_authority_reason,
                    self.sell_action_authority_identity,
                )
            )
            or not (
                str(self.bin_id or "").strip()
                or str(self.bin_label or "").strip()
            )
            or self.side not in {"YES", "NO"}
            or book_state not in {
                "UNKNOWN",
                "NO_EXECUTABLE_BOOK",
                "STALE",
                "EXECUTABLE",
            }
            or (evaluated and book_state != "EXECUTABLE")
            or not Decimal(self.held_shares).is_finite()
            or Decimal(self.held_shares) <= 0
            or any(
                value.tzinfo is None
                for value in (
                    self.selection_cut_at_utc,
                    self.decision_at_utc,
                    self.book_deadline_at_utc,
                )
            )
            or self.selection_cut_at_utc > self.decision_at_utc
            or self.decision_at_utc > self.book_deadline_at_utc
            or evaluated
            != bool(
                candidate_ids
                and str(self.probability_witness_identity or "").strip()
                and str(self.probability_content_identity or "").strip()
                and str(self.sell_book_witness_identity or "").strip()
                and not str(self.reason or "").strip()
            )
            or excluded
            != bool(
                not candidate_ids
                and str(self.reason or "").strip()
            )
        ):
            raise ValueError("GLOBAL_HOLDING_AUCTION_COVERAGE_INVALID")


def global_sell_book_witness_identity(curve: object) -> str:
    """Hash current executable SELL content without stale capture-time identity."""

    digest = hashlib.sha256()
    for value in (
        getattr(curve, "token_id", ""),
        getattr(curve, "side", ""),
        getattr(curve, "book_hash", ""),
        getattr(getattr(curve, "fee_model", None), "fee_rate", ""),
        getattr(curve, "min_tick", ""),
        getattr(curve, "min_order_size", ""),
    ):
        if not str(value).strip():
            raise ValueError("GLOBAL_SELL_BOOK_WITNESS_INCOMPLETE")
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    levels = tuple(getattr(curve, "levels", ()) or ())
    if not levels:
        raise ValueError("GLOBAL_SELL_BOOK_WITNESS_INCOMPLETE")
    for level in levels:
        digest.update(str(getattr(level, "price", "")).encode("utf-8"))
        digest.update(b"\x1e")
        digest.update(str(getattr(level, "size", "")).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def _non_executable_sell_coverage(
    held_shares: object,
    curve: object,
) -> tuple[str, str, str]:
    """Classify a positive holding after both SELL modes fail materialization."""

    sellable_shares = Decimal(held_shares).quantize(
        Decimal("0.01"),
        rounding=ROUND_FLOOR,
    )
    reason = (
        "SELL_BOOK_NO_EXECUTABLE_UNIT_PRICE"
        if sellable_shares > 0
        else "SELLABLE_SHARES_BELOW_PRECISION"
    )
    return reason, "NO_EXECUTABLE_BOOK", global_sell_book_witness_identity(curve)


def global_sell_book_unavailability_witness_identity(
    *,
    asset_state: tuple[str, ...],
    book_epoch_identity: str,
    native_bid_level_count: int,
) -> str:
    """Bind one fresh token-level no-SELL-book fact to its complete epoch."""

    state = tuple(str(value) for value in asset_state)
    epoch_identity = str(book_epoch_identity or "").strip()
    if (
        len(state) < 7
        or not epoch_identity
        or state[5] not in {"EXECUTABLE", "NO_ASK", "VENUE_NOT_EXECUTABLE"}
        or native_bid_level_count != 0
        or not all(state[index].strip() for index in (0, 1, 2, 3, 4, 6))
    ):
        raise ValueError("GLOBAL_SELL_BOOK_UNAVAILABILITY_WITNESS_INCOMPLETE")
    digest = hashlib.sha256()
    digest.update(epoch_identity.encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(str(native_bid_level_count).encode("utf-8"))
    digest.update(b"\x1f")
    for value in state:
        digest.update(value.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparedGlobalAuctionResult:
    """One selection result plus the event that may own later actuation."""

    decision: GlobalSingleOrderDecision
    winner_event_id: str | None
    actuation: "GlobalSingleOrderActuation | None" = None
    holding_coverage: tuple[GlobalHoldingAuctionCoverage, ...] = ()

    def __post_init__(self) -> None:
        if (self.decision.candidate is None) != (self.winner_event_id is None):
            raise ValueError("global auction winner event must match the trade decision")
        if (self.winner_event_id is None) != (self.actuation is None):
            raise ValueError("global auction actuation must match the unique winner")
        if self.actuation is not None and (
            self.actuation.decision != self.decision
            or self.actuation.winner_event_id != self.winner_event_id
        ):
            raise ValueError("global auction result and actuation disagree")
        position_ids = tuple(row.position_id for row in self.holding_coverage)
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("global auction holding coverage is not position-unique")


def _global_decision_economics_identity(
    decision: GlobalSingleOrderDecision,
) -> tuple[object, ...]:
    growth = decision.expected_growth
    if growth is None:
        raise ValueError("global decision lacks common expected-growth economics")
    if decision.expected_terminal_wealth is not None:
        terminal = decision.expected_terminal_wealth
        if getattr(decision.candidate, "action", "BUY") == "BUY":
            action_economics = (
                "EXPECTED_BUY",
                terminal.probability_basis,
                repr(terminal.win_probability_mean),
                repr(terminal.loss_probability_mean),
                terminal.loss_payoff_usd,
                terminal.win_payoff_usd,
                terminal.wealth_after_loss_usd,
                terminal.wealth_after_win_usd,
                repr(terminal.expected_delta_log_wealth),
                repr(terminal.expected_ev_usd),
                repr(terminal.ruin_probability_reduction),
            )
        else:
            action_economics = (
                "EXPECTED_SELL",
                terminal.probability_basis,
                repr(terminal.held_probability_mean),
                repr(terminal.favorable_sell_probability_mean),
                terminal.loss_payoff_usd,
                terminal.win_payoff_usd,
                terminal.wealth_after_loss_usd,
                terminal.wealth_after_win_usd,
                repr(terminal.expected_delta_log_wealth),
                repr(terminal.expected_ev_usd),
                repr(terminal.ruin_probability_reduction),
            )
    elif decision.terminal_wealth is not None:
        terminal = decision.terminal_wealth
        action_economics = (
            "ROBUST_ACTION",
            repr(decision.robust_delta_log_wealth),
            repr(decision.robust_ev_usd),
            repr(decision.capital_efficiency),
            repr(terminal.win_probability_lcb),
            repr(terminal.loss_probability_ucb),
            terminal.loss_payoff_usd,
            terminal.win_payoff_usd,
            terminal.median_payoff_usd,
            terminal.wealth_after_loss_usd,
            terminal.wealth_after_win_usd,
            repr(terminal.expected_value_usd),
        )
    else:
        raise ValueError("global decision lacks action economics")
    return (
        *action_economics,
        "COMMON_EXPECTED_GROWTH",
        growth.probability_basis,
        growth.probability_witness_identity,
        growth.utility_basis,
        repr(growth.ruin_probability_reduction),
        repr(growth.expected_delta_log_wealth),
        repr(growth.expected_ev_usd),
        repr(growth.capital_lock_hours),
        repr(growth.expected_log_growth_per_hour),
        repr(growth.expected_capital_efficiency),
    )


def global_single_order_actuation_identity(
    *,
    decision: GlobalSingleOrderDecision,
    winner_event_id: str,
    universe_witness_identity: str,
    wealth_witness_identity: str,
    selection_epoch_identity: str,
    selection_cut_at_utc: datetime,
    decision_at_utc: datetime,
) -> str:
    candidate = decision.candidate
    if candidate is None:
        raise ValueError("global actuation requires a trade decision")
    economics_identity = _global_decision_economics_identity(decision)
    action = str(getattr(candidate, "action", "BUY") or "BUY")
    curve = (
        candidate.economic_sell_curve
        if action == "SELL"
        else candidate.executable_cost_curve
    )
    economic_curve = (
        candidate.economic_sell_curve
        if action == "SELL"
        else candidate.economic_cost_curve
    )
    maker_witness = getattr(candidate, "maker_fill_witness", None)
    execution_identity = (
        candidate.execution_mode,
        candidate.fill_probability,
        candidate.fill_probability_source,
        candidate.rest_deadline_minutes,
        candidate.neg_risk,
        executable_curve_identity(economic_curve),
        candidate.asset_epoch_identity,
        maker_witness.witness_identity if maker_witness is not None else None,
        maker_witness.candidate_binding_identity if maker_witness is not None else None,
        maker_witness.asset_epoch_identity if maker_witness is not None else None,
    )
    sell_identity = (
        (
            action,
            candidate.position_id,
            candidate.held_shares,
            candidate.execution_mode,
            candidate.fill_probability,
            candidate.fill_probability_source,
            candidate.rest_deadline_minutes,
            candidate.economic_sell_curve.levels[0].price,
            candidate.economic_sell_curve.levels[0].size,
            candidate.probability_functional,
            candidate.exit_authority_status,
            candidate.exit_authority_reason,
            candidate.sell_action_authority_identity,
            decision.cash_proceeds_usd,
        )
        if action == "SELL"
        else ()
    )
    repair = decision.buy_minimum_marketable_repair
    buy_sizing_identity = (
        (decision.buy_sizing_mode,)
        if repair is None
        else (
            decision.buy_sizing_mode,
            repair.minimum_fractional_kelly_multiplier,
            repair.continuous_full_kelly_target_shares,
            repair.continuous_fractional_kelly_target_shares,
            repr(repair.continuous_full_robust_delta_log_wealth),
            repr(repair.continuous_full_robust_ev_usd),
        )
    )
    digest = hashlib.sha256()
    for value in (
        winner_event_id,
        universe_witness_identity,
        wealth_witness_identity,
        selection_epoch_identity,
        selection_cut_at_utc.isoformat(),
        decision_at_utc.isoformat(),
        candidate.candidate_id,
        candidate.family_key,
        candidate.condition_id,
        candidate.side,
        candidate.token_id,
        *execution_identity,
        *sell_identity,
        candidate.probability_witness_identity,
        candidate.book_snapshot_id,
        candidate.execution_curve_identity,
        curve.book_hash,
        decision.shares,
        decision.cost_usd,
        decision.limit_price,
        decision.expected_fill_price_before_fee,
        decision.max_spend_usd,
        decision.current_token_shares,
        decision.full_kelly_target_shares,
        decision.fractional_kelly_target_shares,
        *buy_sizing_identity,
        *economics_identity,
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def global_single_order_economic_identity(
    *,
    decision: GlobalSingleOrderDecision,
    probability_witness: Any,
    wealth_economic_identity: str,
) -> str:
    """Bind one order's economics without observation or epoch clocks."""

    candidate = decision.candidate
    if (
        candidate is None
        or not str(wealth_economic_identity or "").strip()
    ):
        raise ValueError("global economic identity requires a trade and current wealth")
    economics_identity = _global_decision_economics_identity(decision)
    if getattr(probability_witness, "family_key", None) != candidate.family_key:
        raise ValueError("global economic identity probability family mismatch")
    action = str(getattr(candidate, "action", "BUY") or "BUY")
    curve = (
        candidate.economic_sell_curve
        if action == "SELL"
        else candidate.executable_cost_curve
    )
    economic_curve = (
        candidate.economic_sell_curve
        if action == "SELL"
        else candidate.economic_cost_curve
    )
    maker_witness = getattr(candidate, "maker_fill_witness", None)
    execution_identity = (
        candidate.execution_mode,
        candidate.fill_probability,
        candidate.fill_probability_source,
        candidate.rest_deadline_minutes,
        candidate.neg_risk,
        executable_curve_identity(economic_curve),
        candidate.asset_epoch_identity,
        maker_witness.witness_identity if maker_witness is not None else None,
        maker_witness.candidate_binding_identity if maker_witness is not None else None,
        maker_witness.asset_epoch_identity if maker_witness is not None else None,
    )
    sell_identity = (
        (
            action,
            candidate.position_id,
            candidate.held_shares,
            candidate.execution_mode,
            candidate.fill_probability,
            candidate.fill_probability_source,
            candidate.rest_deadline_minutes,
            candidate.probability_functional,
            candidate.exit_authority_status,
            candidate.exit_authority_reason,
            candidate.sell_action_authority_identity,
            decision.cash_proceeds_usd,
        )
        if action == "SELL"
        else ()
    )
    repair = decision.buy_minimum_marketable_repair
    buy_sizing_identity = (
        (decision.buy_sizing_mode,)
        if repair is None
        else (
            decision.buy_sizing_mode,
            repair.minimum_fractional_kelly_multiplier,
            repair.continuous_full_kelly_target_shares,
            repair.continuous_fractional_kelly_target_shares,
            repr(repair.continuous_full_robust_delta_log_wealth),
            repr(repair.continuous_full_robust_ev_usd),
        )
    )
    digest = hashlib.sha256()
    for value in (
        candidate.family_key,
        candidate.bin_id,
        candidate.condition_id,
        candidate.side,
        candidate.token_id,
        *execution_identity,
        *sell_identity,
        candidate.resolution_identity,
        probability_witness.family_binding_identity,
        probability_witness.sample_matrix_identity,
        probability_witness.q_version,
        probability_witness.band_alpha,
        probability_witness.band_basis,
        wealth_economic_identity,
        curve.book_hash,
        curve.fee_model.fee_rate,
        curve.min_tick,
        curve.min_order_size,
        curve.quote_ttl.total_seconds(),
        decision.shares,
        decision.cost_usd,
        decision.limit_price,
        decision.expected_fill_price_before_fee,
        decision.max_spend_usd,
        decision.current_token_shares,
        decision.full_kelly_target_shares,
        decision.fractional_kelly_target_shares,
        *buy_sizing_identity,
        *economics_identity,
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    for level in curve.levels:
        digest.update(str(level.price).encode("utf-8"))
        digest.update(b"\x1e")
        digest.update(str(level.size).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


@dataclass(frozen=True)
class GlobalSingleOrderActuation:
    """Certificate-bound handoff from the global selector to one live event."""

    decision: GlobalSingleOrderDecision
    winner_event_id: str
    universe_witness_identity: str
    wealth_witness_identity: str
    selection_epoch_identity: str
    probability_witness: Any
    selection_cut_at_utc: datetime
    decision_at_utc: datetime
    actuation_identity: str
    wealth_economic_identity: str
    economic_identity: str
    auction_receipt_ref: GlobalAuctionReceiptRef | None = None

    def __post_init__(self) -> None:
        if (
            self.decision.candidate is None
            or not self.winner_event_id
            or not self.universe_witness_identity
            or not self.wealth_witness_identity
            or not self.selection_epoch_identity
            or self.selection_cut_at_utc.tzinfo is None
            or self.decision_at_utc.tzinfo is None
            or self.selection_cut_at_utc > self.decision_at_utc
            or getattr(self.probability_witness, "witness_identity", None)
            != self.decision.candidate.probability_witness_identity
        ):
            raise ValueError("global actuation authority is incomplete")
        expected = global_single_order_actuation_identity(
            decision=self.decision,
            winner_event_id=self.winner_event_id,
            universe_witness_identity=self.universe_witness_identity,
            wealth_witness_identity=self.wealth_witness_identity,
            selection_epoch_identity=self.selection_epoch_identity,
            selection_cut_at_utc=self.selection_cut_at_utc,
            decision_at_utc=self.decision_at_utc,
        )
        if self.actuation_identity != expected:
            raise ValueError("global actuation identity mismatch")
        economic = global_single_order_economic_identity(
            decision=self.decision,
            probability_witness=self.probability_witness,
            wealth_economic_identity=self.wealth_economic_identity,
        )
        if self.economic_identity != economic:
            raise ValueError("global actuation economic identity mismatch")
        if self.auction_receipt_ref is not None:
            self.auction_receipt_ref.assert_matches_actuation(
                winner_event_id=self.winner_event_id,
                winner_candidate_id=self.decision.candidate.candidate_id,
                winner_actuation_identity=self.actuation_identity,
                selection_epoch_identity=self.selection_epoch_identity,
            )


def _no_trade(reason: str) -> PreparedGlobalAuctionResult:
    return PreparedGlobalAuctionResult(
        decision=GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
        ),
        winner_event_id=None,
        actuation=None,
    )


def _candidate_portfolio_endowment(
    candidate: GlobalSingleOrderAnyCandidate,
    *,
    probability_witness: Any,
    holdings_snapshot: Any,
    wealth_witness: PortfolioWealthWitness,
) -> CandidatePortfolioEndowment:
    """Project exact same-family holdings onto one native action's branches."""
    outcomes = tuple(str(bin_id) for bin_id in probability_witness.bin_ids)
    if (
        len(outcomes) < 2
        or len(set(outcomes)) != len(outcomes)
        or candidate.bin_id not in outcomes
        or str(getattr(holdings_snapshot, "family_key", "") or "")
        != candidate.family_key
        or str(getattr(holdings_snapshot, "ledger_snapshot_id", "") or "")
        != wealth_witness.ledger_snapshot_id
    ):
        raise ValueError("candidate holdings topology is not ledger aligned")

    payout_by_outcome = {outcome: Decimal("0") for outcome in outcomes}
    current_token_shares = Decimal("0")
    claims = getattr(holdings_snapshot, "endowment_claims", None)
    if claims is None:
        claims = getattr(holdings_snapshot, "holdings", ())
    for holding in tuple(claims or ()):
        shares = Decimal(holding.shares)
        holding_bin = str(holding.bin_id)
        holding_side = str(holding.side)
        holding_token = str(holding.token_id)
        if (
            not shares.is_finite()
            or shares <= 0
            or holding_bin not in payout_by_outcome
            or holding_side not in {"YES", "NO"}
        ):
            raise ValueError("candidate family holding is invalid")
        if holding_side == "YES":
            payout_by_outcome[holding_bin] += shares
        else:
            for outcome in outcomes:
                if outcome != holding_bin:
                    payout_by_outcome[outcome] += shares
        if holding_token == candidate.token_id:
            if holding_bin != candidate.bin_id or holding_side != candidate.side:
                raise ValueError("native token maps to conflicting family claim")
            current_token_shares += shares

    def candidate_wins(outcome: str) -> bool:
        is_own_bin = outcome == candidate.bin_id
        return is_own_bin if candidate.side == "YES" else not is_own_bin

    loss_payouts = tuple(
        payout
        for outcome, payout in payout_by_outcome.items()
        if not candidate_wins(outcome)
    )
    win_payouts = tuple(
        payout
        for outcome, payout in payout_by_outcome.items()
        if candidate_wins(outcome)
    )
    if not loss_payouts or not win_payouts:
        raise ValueError("candidate payoff branches are incomplete")
    utility_liquid = (
        wealth_witness.strategy_capital_allocation.utility_liquid_cash_usd
    )
    return CandidatePortfolioEndowment(
        loss_wealth_floor_usd=(
            utility_liquid + min(loss_payouts)
        ),
        win_wealth_floor_usd=(
            utility_liquid + min(win_payouts)
        ),
        current_token_shares=current_token_shares,
        ledger_snapshot_id=wealth_witness.ledger_snapshot_id,
    )


def _family_portfolio_endowment(
    *,
    probability_witness: Any,
    holdings_snapshot: Any,
    wealth_witness: PortfolioWealthWitness,
) -> FamilyPortfolioEndowment:
    """Project claims and cumulative capital ownership onto one MECE family."""

    family_key = str(getattr(probability_witness, "family_key", "") or "")
    outcomes = tuple(str(bin_id) for bin_id in probability_witness.bin_ids)
    if (
        len(outcomes) < 2
        or len(set(outcomes)) != len(outcomes)
        or str(getattr(holdings_snapshot, "family_key", "") or "") != family_key
        or str(getattr(holdings_snapshot, "ledger_snapshot_id", "") or "")
        != wealth_witness.ledger_snapshot_id
    ):
        raise ValueError("family holdings topology is not ledger aligned")
    payout = {outcome: Decimal("0") for outcome in outcomes}
    shares_by_token: dict[str, Decimal] = {}
    claims = getattr(holdings_snapshot, "endowment_claims", None)
    if claims is None:
        claims = getattr(holdings_snapshot, "holdings", ())
    for holding in tuple(claims or ()):
        shares = Decimal(holding.shares)
        bin_id = str(holding.bin_id)
        side = str(holding.side)
        token_id = str(holding.token_id)
        if (
            not shares.is_finite()
            or shares <= 0
            or bin_id not in payout
            or side not in {"YES", "NO"}
            or not token_id
        ):
            raise ValueError("family holding is invalid")
        if side == "YES":
            payout[bin_id] += shares
        else:
            for outcome in outcomes:
                if outcome != bin_id:
                    payout[outcome] += shares
        shares_by_token[token_id] = shares_by_token.get(token_id, Decimal("0")) + shares
    commitments = {
        str(token): Decimal(int(amount)) / Decimal("1000000")
        for token, amount in wealth_witness.native_commitments_micro
    }
    family_tokens = {
        str(token)
        for binding in probability_witness.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
        if str(token)
    }
    family_committed = sum(
        (commitments.get(token, Decimal("0")) for token in family_tokens),
        Decimal("0"),
    )
    capital_allocation = wealth_witness.strategy_capital_allocation
    return FamilyPortfolioEndowment(
        family_key=family_key,
        payout_by_bin_usd=tuple((outcome, payout[outcome]) for outcome in outcomes),
        current_token_shares=tuple(sorted(shares_by_token.items())),
        wealth_floor_usd=capital_allocation.utility_liquid_cash_usd,
        spendable_cash_usd=capital_allocation.remaining_buy_capacity_usd,
        portfolio_capital_usd=capital_allocation.allocated_equity_usd,
        committed_capital_usd=family_committed,
        ledger_snapshot_id=wealth_witness.ledger_snapshot_id,
    )


def select_prepared_global_auction(
    prepared_by_event: Mapping[str, Any],
    *,
    selection_epoch_identity: str,
    selection_cut_at_utc: datetime,
    current_scope: CurrentGlobalAuctionScope,
    current_scope_identity_resolver: Callable[[], str | None],
    venue_universe_identity: str,
    current_venue_universe_identity_resolver: Callable[[], str | None],
    universe_max_age: timedelta,
    current_probability_resolver: Callable[
        [str], CurrentFamilyProbabilityAuthority | None
    ],
    current_execution_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], CurrentExecutionAuthority | None
    ],
    current_wealth_identity_resolver: Callable[[], str | None],
    wealth_witness: PortfolioWealthWitness,
    capital_limit_usd: Decimal,
    fractional_kelly_multiplier: Decimal = Decimal("1"),
    decision_at_utc: datetime,
    book_epoch: CurrentGlobalBookEpoch | None = None,
    current_capital_limit_resolver: Callable[
        [GlobalSingleOrderAnyCandidate, str, str, str], Decimal
    ]
    | None = None,
    candidate_policy_rejection_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], str | None
    ]
    | None = None,
    preflight_excluded_by_family: Mapping[str, str] | None = None,
    buy_disabled_family_keys: frozenset[str] | None = None,
    payoff_q_lcb_by_candidate: Mapping[tuple[str, str, str, str], float]
    | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedGlobalAuctionResult:
    """Compare every prepared family and return at most one current winner.

    A family is the probability authority, while an event is only the later
    actuation owner.  Therefore exactly one prepared event may represent each
    family in an epoch.  A malformed seed is fatal to the epoch: silently
    dropping it would shrink the feasible set and make ``global`` false.  The
    only permitted candidate removal is a typed, non-empty preflight rejection
    from this same epoch; its family remains in the probability/book universe.
    """

    if (
        not str(selection_epoch_identity or "").strip()
        or selection_cut_at_utc.tzinfo is None
        or selection_cut_at_utc > decision_at_utc
    ):
        return _no_trade("GLOBAL_SELECTION_EPOCH_IDENTITY_MISSING")
    if book_epoch is None:
        # SCOPE: this selection epoch; candidate negRisk is venue-asset authority.
        # DRAIN: the upstream current-book capture supplies a typed book epoch.
        # RESET: the next invocation includes that epoch and rematerializes candidates.
        return _no_trade("GLOBAL_BOOK_NEG_RISK_AUTHORITY_MISSING")
    excluded_by_family = {
        str(family_key or "").strip(): str(reason or "").strip()
        for family_key, reason in (preflight_excluded_by_family or {}).items()
    }
    if any(
        not family_key or not reason
        for family_key, reason in excluded_by_family.items()
    ):
        return _no_trade("GLOBAL_EXCLUDED_FAMILY_INVALID")
    excluded = frozenset(excluded_by_family)
    buy_disabled = frozenset(buy_disabled_family_keys or ())
    probability_witnesses = {}
    event_by_family: dict[str, str] = {}
    candidates: list[GlobalSingleOrderAnyCandidate] = []
    holdings_by_family = {}
    prepared_by_family = {}
    holding_coverage: list[GlobalHoldingAuctionCoverage] = []
    for event_id, prepared in sorted(prepared_by_event.items()):
        event_key = str(event_id or "").strip()
        probability = getattr(prepared, "probability_witness", None)
        family_key = str(getattr(probability, "family_key", "") or "").strip()
        if not event_key or probability is None or not family_key:
            return _no_trade("GLOBAL_PREPARED_FAMILY_INVALID")
        if family_key in event_by_family:
            return _no_trade("GLOBAL_FAMILY_EVENT_AMBIGUOUS")
        event_by_family[family_key] = event_key
        probability_witnesses[family_key] = probability
        prepared_by_family[family_key] = prepared
        holdings = getattr(prepared, "holdings_snapshot", None)
        if book_epoch is not None:
            if (
                holdings is None
                or str(getattr(holdings, "family_key", "") or "") != family_key
                or str(getattr(holdings, "ledger_snapshot_id", "") or "")
                != wealth_witness.ledger_snapshot_id
            ):
                return _no_trade("GLOBAL_HOLDINGS_SNAPSHOT_MISSING")
            holdings_by_family[family_key] = holdings
        if family_key in excluded:
            continue
    if not excluded.issubset(probability_witnesses):
        return _no_trade("GLOBAL_EXCLUDED_FAMILY_UNKNOWN")
    if not buy_disabled.issubset(probability_witnesses):
        return _no_trade("GLOBAL_BUY_DISABLED_FAMILY_UNKNOWN")

    if book_epoch is not None:
        if venue_universe_identity != book_epoch.witness_identity:
            return _no_trade("GLOBAL_BOOK_EPOCH_IDENTITY_MISMATCH")
        book_deadline_at_utc = book_epoch.captured_at_utc + book_epoch.max_age
        if decision_at_utc > book_deadline_at_utc:
            return _no_trade("GLOBAL_BOOK_EPOCH_EXPIRED")

        def holding_binding(holding: Any, probability: Any) -> Any:
            try:
                column = probability.bin_ids.index(str(holding.bin_id))
            except ValueError as exc:
                raise ValueError(
                    "holding bin is absent from the family probability witness"
                ) from exc
            binding = probability.bindings[column]
            side = str(holding.side)
            expected_token = (
                binding.yes_token_id if side == "YES" else binding.no_token_id
            )
            if (
                side not in {"YES", "NO"}
                or str(holding.family_key) != probability.family_key
                or not expected_token
                or str(holding.token_id) != expected_token
            ):
                raise ValueError(
                    "holding condition/token does not own the selected q column"
                )
            return binding

        def coverage_row(
            holding: Any,
            probability: Any,
            *,
            status: str,
            candidate_id: str | None = None,
            candidate_ids: tuple[str, ...] = (),
            reason: str | None = None,
            sell_book_witness_identity: str | None = None,
            book_state: str = "UNKNOWN",
        ) -> GlobalHoldingAuctionCoverage:
            binding = holding_binding(holding, probability)
            prepared = prepared_by_family[holding.family_key]
            return GlobalHoldingAuctionCoverage(
                position_id=str(holding.position_id),
                family_key=str(holding.family_key),
                bin_id=str(binding.bin_id),
                condition_id=str(binding.condition_id),
                side=str(holding.side),
                token_id=str(holding.token_id),
                held_shares=Decimal(holding.shares),
                ledger_snapshot_id=str(holdings_by_family[holding.family_key].ledger_snapshot_id),
                probability_witness_identity=str(probability.witness_identity),
                probability_content_identity=str(
                    probability.probability_content_identity
                ),
                wealth_economic_identity=wealth_witness.economic_identity,
                selection_epoch_identity=selection_epoch_identity,
                book_epoch_identity=book_epoch.witness_identity,
                selection_cut_at_utc=selection_cut_at_utc,
                decision_at_utc=decision_at_utc,
                book_deadline_at_utc=book_deadline_at_utc,
                status=status,
                candidate_id=candidate_id,
                candidate_ids=candidate_ids,
                reason=reason,
                sell_book_witness_identity=sell_book_witness_identity,
                book_state=book_state,
                sell_exit_authority_status=str(
                    prepared.day0_exit_authority_status
                ),
                sell_exit_authority_reason=str(
                    prepared.day0_exit_authority_reason
                ),
                sell_action_authority_identity=str(
                    prepared.sell_action_authority_identity
                ),
            )

        candidates = []

        def maker_witness_for(
            *,
            family_key: str,
            bin_id: str,
            condition_id: str,
            side: str,
            token_id: str,
            position_id: str | None,
        ) -> CurrentMakerFillWitness | None:
            """Read only an upstream typed current witness; never reinterpret priors."""

            witnesses = prepared_by_family[family_key].maker_fill_witnesses
            if not isinstance(witnesses, Mapping):
                return None
            key = (bin_id, condition_id, side, token_id, position_id)
            witness = witnesses.get(key)
            return witness if isinstance(witness, CurrentMakerFillWitness) else None

        book_state_row_by_key = {
            tuple(state[:5]): tuple(state) for state in book_epoch.asset_states
        }
        for asset in book_epoch.assets:
            probability = probability_witnesses.get(asset.family_key)
            if probability is None:
                return _no_trade("GLOBAL_BOOK_FAMILY_PROBABILITY_MISSING")
            if asset.family_key in excluded:
                continue
            native = SimpleNamespace(
                no_trade_reason=None,
                executable_cost_curve=asset.curve,
                family_key=asset.family_key,
                bin_id=asset.bin_id,
                condition_id=asset.condition_id,
                side=asset.side,
                token_id=asset.token_id,
                neg_risk=asset.neg_risk,
                hypothesis_id=(
                    f"GLOBAL_BOOK:{asset.family_key}:{asset.bin_id}:{asset.side}"
                ),
            )
            try:
                maker_witness = maker_witness_for(
                    family_key=asset.family_key,
                    bin_id=asset.bin_id,
                    condition_id=asset.condition_id,
                    side=asset.side,
                    token_id=asset.token_id,
                    position_id=None,
                )
                candidates.extend(
                    global_candidates_from_native(
                        native,
                        probability_witness=probability,
                        ledger_snapshot_id=wealth_witness.ledger_snapshot_id,
                        book_captured_at_utc=asset.captured_at_utc,
                        native_bid_levels=asset.bid_levels,
                        include_maker=maker_witness is not None,
                        maker_fill_witness=maker_witness,
                        asset_epoch_identity=book_epoch.witness_identity,
                        neg_risk=asset.neg_risk,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one malformed asset invalidates globality
                return _no_trade(
                    "GLOBAL_BOOK_CANDIDATE_MATERIALIZATION_FAILED:"
                    f"{type(exc).__name__}:{exc}"
                )
        for family_key, holdings in holdings_by_family.items():
            probability = probability_witnesses[family_key]
            prepared = prepared_by_family[family_key]
            for holding in holdings.holdings:
                if family_key in excluded:
                    holding_coverage.append(
                        coverage_row(
                            holding,
                            probability,
                            status="EXCLUDED",
                            reason=(
                                "FAMILY_PREFLIGHT_EXCLUDED:"
                                f"{excluded_by_family[family_key]}"
                            ),
                        )
                    )
                    continue
                if isinstance(probability, DeterministicBinPayoffWitness):
                    sell_functional = "DETERMINISTIC_PAYOFF"
                    exit_status = "deterministic"
                    exit_reason = "day0_deterministic_bin_payoff"
                else:
                    prepared_status = str(
                        getattr(
                            prepared,
                            "day0_exit_authority_status",
                            "not_applicable",
                        )
                        or ""
                    ).strip().lower()
                    prepared_reason = str(
                        getattr(
                            prepared,
                            "day0_exit_authority_reason",
                            "",
                        )
                        or ""
                    ).strip()
                    if prepared_status in {
                        "not_applicable",
                        "mature",
                        "immature",
                        "unavailable",
                    }:
                        sell_functional = "POSTERIOR_PREDICTIVE_MEAN"
                        exit_status = prepared_status
                        exit_reason = (
                            prepared_reason
                            or (
                                "non_day0_family"
                                if prepared_status == "not_applicable"
                                else "day0_temporal_status_reason_missing"
                            )
                        )
                    else:
                        return _no_trade(
                            "GLOBAL_DAY0_EXIT_AUTHORITY_STATUS_INVALID:"
                            f"{prepared_status or 'missing'}"
                        )
                asset = book_epoch.sell_asset_by_key.get(
                    (
                        family_key,
                        str(holding.bin_id),
                        str(holding.side),
                        str(holding.token_id),
                    )
                )
                if asset is None:
                    binding = holding_binding(holding, probability)
                    book_state_row = book_state_row_by_key.get(
                        (
                            family_key,
                            str(binding.bin_id),
                            str(binding.condition_id),
                            str(holding.side),
                            str(holding.token_id),
                        )
                    )
                    book_status = (
                        str(book_state_row[5]) if book_state_row is not None else None
                    )
                    native_asset = book_epoch.asset_by_key.get(
                        (
                            family_key,
                            str(binding.bin_id),
                            str(holding.side),
                            str(holding.token_id),
                        )
                    )
                    native_bid_level_count = len(
                        tuple(getattr(native_asset, "bid_levels", ()) or ())
                    )
                    reason = (
                        f"SELL_{book_status}"
                        if book_status in {
                            "VENUE_NOT_EXECUTABLE",
                            "VENUE_METADATA_STALE",
                        }
                        else "SELL_BOOK_NO_BID"
                    )
                    holding_coverage.append(
                        coverage_row(
                            holding,
                            probability,
                            status="EXCLUDED",
                            reason=reason,
                            book_state=(
                                "STALE"
                                if book_status == "VENUE_METADATA_STALE"
                                else "NO_EXECUTABLE_BOOK"
                            ),
                            sell_book_witness_identity=(
                                global_sell_book_unavailability_witness_identity(
                                    asset_state=book_state_row,
                                    book_epoch_identity=book_epoch.witness_identity,
                                    native_bid_level_count=native_bid_level_count,
                                )
                                if book_state_row is not None
                                and book_status
                                in {
                                    "EXECUTABLE",
                                    "NO_ASK",
                                    "VENUE_NOT_EXECUTABLE",
                                }
                                and native_bid_level_count == 0
                                else None
                            ),
                        )
                    )
                    continue
                try:
                    sell_candidates = tuple(
                        candidate
                        for mode in ("TAKER_LIMIT", "MAKER_REST")
                        if (
                            candidate := global_sell_candidate_from_holding(
                                holding,
                                probability_witness=probability,
                                ledger_snapshot_id=holdings.ledger_snapshot_id,
                                executable_sell_curve=asset.curve,
                                book_captured_at_utc=asset.captured_at_utc,
                                probability_functional=sell_functional,
                                exit_authority_status=exit_status,
                                exit_authority_reason=exit_reason,
                                sell_action_authority_identity=(
                                    prepared.sell_action_authority_identity
                                ),
                                execution_mode=mode,
                                maker_fill_witness=maker_witness_for(
                                    family_key=family_key,
                                    bin_id=str(holding.bin_id),
                                    condition_id=str(
                                        holding_binding(holding, probability).condition_id
                                    ),
                                    side=str(holding.side),
                                    token_id=str(holding.token_id),
                                    position_id=str(holding.position_id),
                                ),
                                asset_epoch_identity=book_epoch.witness_identity,
                                neg_risk=asset.neg_risk,
                            )
                        )
                        is not None
                    )
                    if sell_candidates:
                        candidates.extend(sell_candidates)
                        holding_coverage.append(
                            coverage_row(
                                holding,
                                probability,
                                status="EVALUATED",
                                candidate_id=sell_candidates[0].candidate_id,
                                candidate_ids=tuple(
                                    row.candidate_id for row in sell_candidates
                                ),
                                sell_book_witness_identity=(
                                    global_sell_book_witness_identity(
                                        sell_candidates[0].executable_sell_curve
                                    )
                                ),
                                book_state="EXECUTABLE",
                            )
                        )
                    else:
                        (
                            exclusion_reason,
                            exclusion_book_state,
                            exclusion_book_witness,
                        ) = _non_executable_sell_coverage(
                            holding.shares,
                            asset.curve,
                        )
                        holding_coverage.append(
                            coverage_row(
                                holding,
                                probability,
                                status="EXCLUDED",
                                reason=exclusion_reason,
                                # A positive holding that cannot materialize
                                # any venue-legal SELL is current exact
                                # non-executability, including a residual below
                                # the venue amount precision.  UNKNOWN would
                                # leave its durable completion debt immortal
                                # despite a complete q/book/holding cut.
                                book_state=exclusion_book_state,
                                sell_book_witness_identity=exclusion_book_witness,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - malformed holding invalidates globality
                    return _no_trade(
                        "GLOBAL_SELL_CANDIDATE_MATERIALIZATION_FAILED:"
                        f"{type(exc).__name__}:{exc}"
                    )

    try:
        universe_witness = global_universe_witness_from_scope(
            current_scope,
            probability_witnesses=probability_witnesses,
            venue_universe_identity=venue_universe_identity,
            max_age=universe_max_age,
        )
    except ValueError as exc:
        return _no_trade(str(exc))

    def _current_universe_identity() -> str | None:
        try:
            if current_scope_identity_resolver() != current_scope.scope_identity:
                return None
            if current_venue_universe_identity_resolver() != venue_universe_identity:
                return None
        except Exception:  # noqa: BLE001 - authority loss means no current universe
            return None
        return universe_witness.witness_identity

    def _candidate_capital_limit(candidate: GlobalSingleOrderAnyCandidate) -> Decimal:
        allocator_limit = Decimal(capital_limit_usd)
        if current_capital_limit_resolver is not None:
            if book_epoch is None:
                raise ValueError("GLOBAL_CAPITAL_LIMIT_BOOK_EPOCH_MISSING")
            asset = book_epoch.asset_by_key.get(
                (
                    candidate.family_key,
                    candidate.bin_id,
                    candidate.side,
                    candidate.token_id,
                )
            )
            owner_event_id = event_by_family.get(candidate.family_key)
            if asset is None or owner_event_id is None:
                raise ValueError("GLOBAL_CAPITAL_LIMIT_SCOPE_MISSING")
            allocator_limit = Decimal(
                current_capital_limit_resolver(
                    candidate,
                    asset.gamma_market_id,
                    asset.market_event_id,
                    owner_event_id,
                )
            )

        # The W3 selector owns the final executable BUY size, so the configured
        # single-position fraction must constrain this same capital envelope.
        # Leaving it only in the retired local sizing path let the global solver
        # spend well above max_single_position_pct while every upstream receipt
        # still advertised that setting. Use Zeus-owned utility equity, never
        # shared-wallet cash, as the fraction basis. Zero retains the documented
        # disabled posture.
        from src.config import sizing_defaults

        single_position_pct = Decimal(
            str(sizing_defaults()["max_single_position_pct"])
        )
        if (
            not single_position_pct.is_finite()
            or single_position_pct < 0
            or single_position_pct > 1
        ):
            raise ValueError("GLOBAL_SINGLE_POSITION_FRACTION_INVALID")
        if single_position_pct > 0:
            capital_allocation = wealth_witness.strategy_capital_allocation
            committed_micro = dict(
                wealth_witness.native_commitments_micro
            ).get(candidate.token_id, 0)
            committed_usd = Decimal(committed_micro) / Decimal("1000000")
            remaining_position_budget = max(
                Decimal("0"),
                single_position_pct
                * capital_allocation.allocated_equity_usd
                - committed_usd,
            )
            allocator_limit = min(
                allocator_limit,
                remaining_position_budget,
            )
        return allocator_limit

    def _candidate_payoff_q_lcb(
        candidate: GlobalSingleOrderAnyCandidate,
    ) -> float | None:
        if isinstance(candidate, GlobalSingleOrderSellCandidate):
            return None
        if payoff_q_lcb_by_candidate is None:
            return None
        return payoff_q_lcb_by_candidate.get(
            (
                candidate.family_key,
                candidate.bin_id,
                candidate.side,
                candidate.token_id,
            )
        )

    def _candidate_policy_rejection(
        candidate: GlobalSingleOrderAnyCandidate,
    ) -> str | None:
        # Solver-owned typed witness validation is deliberately before this
        # policy hook.  This hook must not turn a current witnessed maker
        # sibling back into a blanket historical-prior wall.
        if (
            str(getattr(candidate, "action", "BUY") or "BUY").upper() == "BUY"
            and candidate.family_key in buy_disabled
        ):
            return "GLOBAL_BUY_DISABLED_FAMILY"
        if candidate_policy_rejection_resolver is None:
            return None
        return candidate_policy_rejection_resolver(candidate)

    def _candidate_endowment(
        candidate: GlobalSingleOrderAnyCandidate,
    ) -> CandidatePortfolioEndowment:
        holdings = holdings_by_family.get(candidate.family_key)
        probability = probability_witnesses.get(candidate.family_key)
        if holdings is None or probability is None:
            raise ValueError("GLOBAL_CANDIDATE_ENDOWMENT_SCOPE_MISSING")
        return _candidate_portfolio_endowment(
            candidate,
            probability_witness=probability,
            holdings_snapshot=holdings,
            wealth_witness=wealth_witness,
        )

    def _family_endowment(family_key: str) -> FamilyPortfolioEndowment:
        holdings = holdings_by_family.get(family_key)
        probability = probability_witnesses.get(family_key)
        if holdings is None or probability is None:
            raise ValueError("GLOBAL_FAMILY_ENDOWMENT_SCOPE_MISSING")
        return _family_portfolio_endowment(
            probability_witness=probability,
            holdings_snapshot=holdings,
            wealth_witness=wealth_witness,
        )

    decision = select_global_single_order(
        tuple(candidates),
        probability_witnesses=probability_witnesses,
        universe_witness=universe_witness,
        current_universe_identity_resolver=_current_universe_identity,
        current_probability_resolver=current_probability_resolver,
        current_execution_resolver=current_execution_resolver,
        current_wealth_identity_resolver=current_wealth_identity_resolver,
        wealth_witness=wealth_witness,
        capital_limit_usd=capital_limit_usd,
        fractional_kelly_multiplier=fractional_kelly_multiplier,
        decision_at_utc=decision_at_utc,
        candidate_capital_limit_resolver=_candidate_capital_limit,
        candidate_portfolio_endowment_resolver=(
            _candidate_endowment if book_epoch is not None else None
        ),
        family_portfolio_endowment_resolver=(
            _family_endowment if book_epoch is not None else None
        ),
        candidate_payoff_q_lcb_resolver=_candidate_payoff_q_lcb,
        candidate_policy_rejection_resolver=_candidate_policy_rejection,
        cancelled=cancelled,
    )
    if (
        decision.candidate is None
        and decision.no_trade_reason == "GLOBAL_SELECTION_CANCELLED"
    ):
        return PreparedGlobalAuctionResult(
            decision=decision,
            winner_event_id=None,
        )
    evaluated = {
        candidate_id: row.position_id
        for row in holding_coverage
        if row.status == "EVALUATED"
        for candidate_id in row.candidate_ids
    }
    sell_evaluations = {
        str(evaluation.candidate_id): str(evaluation.position_id or "")
        for evaluation in tuple(decision.candidate_evaluations or ())
        if evaluation.action == "SELL"
    }
    if evaluated != sell_evaluations:
        return _no_trade("GLOBAL_HOLDING_COVERAGE_INCOMPLETE")
    if decision.candidate is None:
        return PreparedGlobalAuctionResult(
            decision=decision,
            winner_event_id=None,
            holding_coverage=tuple(holding_coverage),
        )
    winner_event_id = event_by_family.get(decision.candidate.family_key)
    if winner_event_id is None:
        return _no_trade("GLOBAL_WINNER_EVENT_BINDING_MISSING")
    return PreparedGlobalAuctionResult(
        decision=decision,
        winner_event_id=winner_event_id,
        actuation=GlobalSingleOrderActuation(
            decision=decision,
            winner_event_id=winner_event_id,
            universe_witness_identity=universe_witness.witness_identity,
            wealth_witness_identity=wealth_witness.witness_identity,
            selection_epoch_identity=selection_epoch_identity,
            probability_witness=probability_witnesses[
                decision.candidate.family_key
            ],
            selection_cut_at_utc=selection_cut_at_utc,
            decision_at_utc=decision_at_utc,
            actuation_identity=global_single_order_actuation_identity(
                decision=decision,
                winner_event_id=winner_event_id,
                universe_witness_identity=universe_witness.witness_identity,
                wealth_witness_identity=wealth_witness.witness_identity,
                selection_epoch_identity=selection_epoch_identity,
                selection_cut_at_utc=selection_cut_at_utc,
                decision_at_utc=decision_at_utc,
            ),
            wealth_economic_identity=wealth_witness.economic_identity,
            economic_identity=global_single_order_economic_identity(
                decision=decision,
                probability_witness=probability_witnesses[
                    decision.candidate.family_key
                ],
                wealth_economic_identity=wealth_witness.economic_identity,
            ),
        ),
        holding_coverage=tuple(holding_coverage),
    )
