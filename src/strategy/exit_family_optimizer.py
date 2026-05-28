# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27) — D3 of
#   K=5 structural decisions for the multi-bin family exit strategy.
#
# The operator's spec §1: the real exit objective is family-level liquidation
# under a mutually-exclusive payoff Y ∈ {1..K} — not per-position edge
# reversal. This module implements the family decision as a deterministic
# pure function over typed leg inputs. The cycle_runtime monitor grouping
# step (D3 part 2 — wired in src/engine/cycle_runtime.py) calls this once
# per (city, target_date, temperature_metric, market_family_id) bucket.
#
# Math (operator §3 + §5):
#   For each held leg i with shares x_i, observation-constrained posterior
#   p_i^obs (YES-side mass at this bin from D2), held-side executable bid
#   b_i, fee rate, and hold-side costs:
#
#     sell_value_i = x_i · (b_i - polymarket_fee(b_i, fee_rate))
#                     # Polymarket maker fee is price-dependent (memory: PR #348
#                     # fee semantics audit); reuses the canonical
#                     # src/contracts/execution_price.py::polymarket_fee which
#                     # raises on invalid inputs — we clamp the {0,1} boundary
#                     # to zero fee, matching HoldValue.compute_with_exit_costs.
#     held_p_i =
#         p_i^obs              if direction == "buy_yes" (held side = YES)
#         (1 - p_i^obs)        if direction == "buy_no"  (held side = NO; if
#                                YES bin is impossible then p_i^obs == 0, so
#                                NO holder's true win probability is 1.0)
#     hold_value_i = x_i · held_p_i - hold_cost_extras_i
#                     # time-cost / correlation-crowding extras come from the
#                     # caller (cycle_runtime threads HoldValue inputs).
#
#   The DIRECTION FLIP is load-bearing: Zeus's entry-side invariant
#   (src/state/portfolio.py Position docstring: "For buy_no: P(NO) and NO
#   market price. This invariant is established once at entry and never
#   flipped.") MUST hold at the family-decision boundary too. A naive
#   `hold_value = shares × p_obs` for buy_no on an impossible YES bin
#   would liquidate a guaranteed-winner NO leg at the bid — a SEV-1
#   regression caught by the pre-merge critic on 2026-05-27.
#
#   Decision per leg:
#     (a) DETERMINISTIC impossibility short-circuit:
#         if D1 constraint marks bin impossible AND bid ≥ min_exit_bid
#         → SELL_FULL, reason OBSERVATION_IMPOSSIBLE_{HIGH|LOW}.
#         if impossible AND bid is None / < min_exit_bid
#         → HOLD, reason OBSERVATION_IMPOSSIBLE_NO_BID (market closed).
#     (b) CONTRADICTION fail-closed:
#         if D2 contradiction_flag is True AND bid ≥ min_exit_bid
#         → SELL_FULL, reason OBSERVATION_CONTRADICTION_FAIL_CLOSED.
#         else HOLD with reason OBSERVATION_CONTRADICTION_NO_BID.
#     (c) EV cash-out:
#         if sell_value_i > hold_value_i + hurdle → SELL_FULL EV_CASH_OUT.
#         else HOLD HOLD_DOMINANT.
#
#   Generic edge reversal, divergence panic, whale toxicity, near-settlement
#   forced-exit logic stays in Position.evaluate_exit. The family optimizer
#   covers ONLY the deterministic + EV-cash-out layer.
#
# Greedy-vs-joint note (operator §5 "Initial version can be greedy"):
#   Under risk-neutral expected value with linear payoff in y_i and disjoint
#   per-leg constraints (0 ≤ y_i ≤ x_i), the family optimum is per-leg
#   independent. The "joint" objective only matters under log-utility, which
#   is deferred (see operator §5 second paragraph).
#
# Purity: total function over typed inputs. No DB, no CLOB, no logging.
"""ExitFamilyDecision optimizer (D3 — pure math layer)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

import math

from src.contracts.execution_price import polymarket_fee as _polymarket_fee
from src.strategy.exit_constrained_posterior import (
    ObservationConstrainedPosterior,
)
from src.strategy.exit_observation_constraint import (
    SettlementProgressConstraint,
)


ExitAction = Literal["SELL_FULL", "HOLD"]


@dataclass(frozen=True)
class ExitLegInput:
    """One held position in a weather family, as the optimizer sees it.

    Hurdle / cost contract (locked by the pre-merge critic, 2026-05-27):
      `hold_cost_extras` is the TOTAL dollar deduction the caller has
      already computed for hold-side costs (fee_cost + time_cost +
      correlation_crowding from HoldValue.compute_with_exit_costs).
      The family-level `daily_hurdle_dollars` arg to
      ``optimize_exit_family`` is composable ADDITIVELY on top of
      `hold_cost_extras` (it is NOT a replacement / NOT a time-cost
      proxy). Putting time-cost in BOTH places double-counts.
    """

    leg_id: str  # trade_id or position id; opaque to the optimizer
    bin_index: int  # index into the family's bin list (matches p_family order)
    bin_label: str  # diagnostic only
    direction: Literal["buy_yes", "buy_no"]
    shares: float
    best_bid: float | None  # held-side executable bid. None ⇒ no liquidity.
                            # For buy_yes this is the YES-side bid; for buy_no
                            # this is the NO-side bid. The caller is
                            # responsible for the direction-correct bid (the
                            # entry-side / monitor_refresh convention already
                            # tracks `last_monitor_best_bid` as held-side).
    fee_rate: float = 0.0  # Polymarket maker fee rate (e.g., 0.05)
    hold_cost_extras: float = 0.0  # $ hold-side cost (fee+time+crowding) from HoldValue


@dataclass(frozen=True)
class ExitLegDecision:
    """One per-leg verdict from the family optimizer."""

    leg_id: str
    bin_index: int
    action: ExitAction
    sell_shares: float
    reason: str
    sell_value: float
    hold_value: float
    p_obs: float
    best_bid: float | None
    feasibility: str  # mirrors D1 verdict for trace


@dataclass(frozen=True)
class ExitFamilyDecision:
    """The optimizer's verdict over the whole family."""

    family_key: tuple
    constraint_authority: str  # "DETERMINISTIC" | "ADVISORY_ONLY"
    constraint_metric: str | None  # "high"|"low"|None
    constraint_observed_value: float | None
    contradiction: bool
    legs: tuple[ExitLegDecision, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def sells(self) -> tuple[ExitLegDecision, ...]:
        return tuple(d for d in self.legs if d.action == "SELL_FULL")

    def any_deterministic_exit(self) -> bool:
        return any(
            d.reason.startswith("OBSERVATION_IMPOSSIBLE")
            or d.reason == "OBSERVATION_CONTRADICTION_FAIL_CLOSED"
            for d in self.legs
            if d.action == "SELL_FULL"
        )


# ----- Fee + value primitives -----
# Fee uses the canonical src/contracts/execution_price.py::polymarket_fee
# (raises on price ∉ (0,1) or non-finite). Per-leg sell value validates the
# bid up-front and clamps the {0, 1} boundary to zero fee — matching the
# HoldValue.compute_with_exit_costs clamp pattern. Garbage inputs raise
# instead of returning silent zero (regression hole closed by F-2, 2026-05-27).


def _per_leg_sell_value(shares: float, bid: float, fee_rate: float) -> float:
    """Linear cash-out value for selling all ``shares`` at ``bid``.

    Raises ValueError on non-finite bid or bid ∉ [0, 1]; the {0.0, 1.0}
    boundary is treated as zero fee (no taker fee at degenerate prices),
    matching HoldValue's clamp.
    """
    if shares <= 0.0:
        return 0.0
    if not math.isfinite(bid):
        raise ValueError(f"_per_leg_sell_value: non-finite bid {bid!r}")
    if bid < 0.0 or bid > 1.0:
        raise ValueError(f"_per_leg_sell_value: bid {bid!r} outside [0, 1]")
    if fee_rate <= 0.0 or bid <= 0.0 or bid >= 1.0:
        return float(shares) * float(bid)
    return float(shares) * (float(bid) - _polymarket_fee(bid, fee_rate))


def _held_probability(p_obs_yes: float, direction: str) -> float:
    """Map the YES-side posterior mass at this bin to the HELD-SIDE win prob.

    For buy_yes the held side is YES, so held_p = p_obs.
    For buy_no the held side is NO, which pays iff YES does NOT settle in
    this bin (mutually exclusive family), so held_p = 1 - p_obs.

    The flip is the family-level mirror of the entry-side Position invariant
    ("For buy_no: P(NO) and NO market price ... never flipped"). Pre-merge
    critic F-1 (2026-05-27) caught a regression where this was skipped.
    """
    p = float(p_obs_yes)
    if direction == "buy_yes":
        return p
    if direction == "buy_no":
        return 1.0 - p
    raise ValueError(f"unknown direction {direction!r}")


def _per_leg_hold_value(
    shares: float, p_obs_yes: float, direction: str, hold_cost_extras: float,
) -> float:
    """Expected hold value under the OBSERVATION-CONSTRAINED HELD-SIDE prob.

    Always pass the direction here — passing raw p_obs without the flip is
    the F-1 regression class.
    """
    if shares <= 0.0:
        return 0.0
    return float(shares) * _held_probability(p_obs_yes, direction) - float(hold_cost_extras)


# ----- The optimizer -----


def _format_reason_for_impossibility(metric: str | None) -> str:
    if metric == "high":
        return "OBSERVATION_IMPOSSIBLE_HIGH"
    if metric == "low":
        return "OBSERVATION_IMPOSSIBLE_LOW"
    return "OBSERVATION_IMPOSSIBLE_UNKNOWN_METRIC"


def optimize_exit_family(
    *,
    family_key: tuple,
    constraint: SettlementProgressConstraint,
    constrained_posterior: ObservationConstrainedPosterior,
    legs: Sequence[ExitLegInput],
    daily_hurdle_dollars: float = 0.0,
    min_exit_bid: float = 0.01,
) -> ExitFamilyDecision:
    """Per-leg deterministic + EV cash-out decision for a family.

    Inputs
      family_key — opaque; logged for trace.
      constraint — D1 SettlementProgressConstraint (may be ADVISORY_ONLY).
      constrained_posterior — D2 result keyed by bin_index (its mask/p_obs
        arrays MUST cover every bin_index referenced by `legs`).
      legs — held positions in this family. Only buy_yes legs participate
        in the impossibility short-circuit AND the contradiction-fail-closed
        branch (a buy_no on an impossible YES bin is the WINNING side —
        held_p = 1 - p_obs = 1.0). buy_no legs are routed through the
        direction-aware EV cash-out (branch c), where hold_value uses the
        flipped probability; the per-position cash-out / settlement-imminent
        / panic gates run after, in Position.evaluate_exit.

    Behaviour vs the broader exit pipeline:
      The optimizer emits ONLY the deterministic + EV decisions. Generic
      edge reversal / panic gates / settlement-imminent / whale-toxicity stay
      in Position.evaluate_exit. A leg whose family verdict is HOLD here can
      still be exited by the per-position pipeline later in the same tick.

    Returns ExitFamilyDecision with one ExitLegDecision per input leg, in
    the same order.
    """
    is_deterministic = constraint.is_deterministic()
    contradiction = constrained_posterior.contradiction_flag
    mask = constrained_posterior.impossible_mask
    p_obs_vec = constrained_posterior.p_obs

    notes: list[str] = []
    if contradiction:
        notes.append("contradiction_flag_set")
    if not is_deterministic:
        notes.append(f"advisory_only:{constraint.gate_reasons!r}")

    leg_decisions: list[ExitLegDecision] = []
    for leg in legs:
        # N-1 (critic pass 2): fail closed on negative shares — upstream
        # Position should never produce these, but if it does we MUST NOT
        # emit a negative sell_shares verdict downstream.
        if leg.shares < 0.0:
            raise ValueError(
                f"optimize_exit_family: leg {leg.leg_id!r} has negative "
                f"shares {leg.shares!r}"
            )
        bid = leg.best_bid
        has_executable_bid = bid is not None and float(bid) >= min_exit_bid

        # Posterior lookup is positional — leg.bin_index must index into the
        # mask and p_obs vectors passed in.
        if leg.bin_index < 0 or leg.bin_index >= len(p_obs_vec):
            raise IndexError(
                f"leg bin_index {leg.bin_index} out of range "
                f"(p_obs len {len(p_obs_vec)})"
            )
        p_obs = float(p_obs_vec[leg.bin_index])
        is_impossible = bool(mask[leg.bin_index])
        feasibility = (
            "impossible" if is_impossible
            else ("unknown" if not is_deterministic else "feasible_or_current")
        )

        sell_value = _per_leg_sell_value(
            leg.shares, float(bid) if bid is not None else 0.0, leg.fee_rate
        )
        hold_value = _per_leg_hold_value(
            leg.shares, p_obs, leg.direction, leg.hold_cost_extras,
        )

        # (a) Deterministic impossibility short-circuit (buy_yes only — buy_no
        # on an impossible YES bin is the WINNING side, semantics flip; defer
        # to evaluate_exit + standard cash-out logic).
        if is_deterministic and is_impossible and leg.direction == "buy_yes":
            if has_executable_bid:
                leg_decisions.append(ExitLegDecision(
                    leg_id=leg.leg_id,
                    bin_index=leg.bin_index,
                    action="SELL_FULL",
                    sell_shares=float(leg.shares),
                    reason=_format_reason_for_impossibility(constraint.metric),
                    sell_value=sell_value,
                    hold_value=hold_value,
                    p_obs=p_obs,
                    best_bid=float(bid) if bid is not None else None,
                    feasibility=feasibility,
                ))
                continue
            # impossible but no bid → cannot sell; record HOLD with the
            # impossibility reason so the trace surfaces the gap.
            leg_decisions.append(ExitLegDecision(
                leg_id=leg.leg_id,
                bin_index=leg.bin_index,
                action="HOLD",
                sell_shares=0.0,
                reason="OBSERVATION_IMPOSSIBLE_NO_BID",
                sell_value=sell_value,
                hold_value=hold_value,
                p_obs=p_obs,
                best_bid=float(bid) if bid is not None else None,
                feasibility=feasibility,
            ))
            continue

        # (b) Contradiction fail-closed (buy_yes only): when the model and
        # observation disagree at the family level, the YES-side posterior
        # cannot be normalised; sell what we can. For buy_no, the NO side
        # MIGHT be the guaranteed winner (e.g., when the impossible YES
        # mass implies a NO payoff), so blindly selling buy_no here would
        # liquidate winners. Defer buy_no to the EV cash-out branch where
        # direction-aware hold_value will compare correctly (held_p = 1 -
        # p_obs = 1.0 in the all-impossible case, dominating any bid < 1).
        if is_deterministic and contradiction and leg.direction == "buy_yes":
            if has_executable_bid:
                leg_decisions.append(ExitLegDecision(
                    leg_id=leg.leg_id,
                    bin_index=leg.bin_index,
                    action="SELL_FULL",
                    sell_shares=float(leg.shares),
                    reason="OBSERVATION_CONTRADICTION_FAIL_CLOSED",
                    sell_value=sell_value,
                    hold_value=hold_value,
                    p_obs=p_obs,
                    best_bid=float(bid) if bid is not None else None,
                    feasibility=feasibility,
                ))
                continue
            leg_decisions.append(ExitLegDecision(
                leg_id=leg.leg_id,
                bin_index=leg.bin_index,
                action="HOLD",
                sell_shares=0.0,
                reason="OBSERVATION_CONTRADICTION_NO_BID",
                sell_value=sell_value,
                hold_value=hold_value,
                p_obs=p_obs,
                best_bid=float(bid) if bid is not None else None,
                feasibility=feasibility,
            ))
            continue

        # (c) EV cash-out. Skip when no executable bid — nothing to sell into.
        if not has_executable_bid:
            leg_decisions.append(ExitLegDecision(
                leg_id=leg.leg_id,
                bin_index=leg.bin_index,
                action="HOLD",
                sell_shares=0.0,
                reason="NO_EXECUTABLE_BID",
                sell_value=sell_value,
                hold_value=hold_value,
                p_obs=p_obs,
                best_bid=float(bid) if bid is not None else None,
                feasibility=feasibility,
            ))
            continue

        if sell_value > hold_value + daily_hurdle_dollars:
            leg_decisions.append(ExitLegDecision(
                leg_id=leg.leg_id,
                bin_index=leg.bin_index,
                action="SELL_FULL",
                sell_shares=float(leg.shares),
                reason="EV_CASH_OUT",
                sell_value=sell_value,
                hold_value=hold_value,
                p_obs=p_obs,
                best_bid=float(bid),
                feasibility=feasibility,
            ))
        else:
            leg_decisions.append(ExitLegDecision(
                leg_id=leg.leg_id,
                bin_index=leg.bin_index,
                action="HOLD",
                sell_shares=0.0,
                reason="HOLD_DOMINANT",
                sell_value=sell_value,
                hold_value=hold_value,
                p_obs=p_obs,
                best_bid=float(bid),
                feasibility=feasibility,
            ))

    return ExitFamilyDecision(
        family_key=family_key,
        constraint_authority=constraint.authority_status,
        constraint_metric=constraint.metric,
        constraint_observed_value=constraint.observed_value,
        contradiction=contradiction,
        legs=tuple(leg_decisions),
        notes=tuple(notes),
    )
