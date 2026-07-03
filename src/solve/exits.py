# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §1 exits row (C5 marginal rule b·Σq_j/W_j > q_i/W_i;
#   ExitContext plumbing REUSE, rule body replace); W3.EXIT brief (W_j state is greenfield,
#   evaluate_exit precedence-chain split, exit q_version gap at executor.py:4394).
"""Exits as the same solve — the C5 marginal interface.

The exit decision is not a separate rule lane: a held position is an ENDOWMENT the
solver re-evaluates every event. Selling holding h_i is menu item ``sell_holding``;
the C5 marginal condition is the mathematical form of "does the solve want to move
wealth out of outcome i":

    sell one unit of outcome-i holding at bid b iff   b · Σ_j q_j / W_j  >  q_i / W_i

(marginal log-utility of the cash proceeds spread over all outcomes exceeds the
marginal log-utility of the held claim on outcome i).

WHAT SURVIVES vs WHAT THIS REPLACES (W3.EXIT brief; portfolio.py:946-1467):
* REUSE: ExitContext/ExitDecision dataclasses, _build_exit_context threading,
  exit_lifecycle.py order mechanics, and the fail-closed PRECEDENCE TRIPWIRES that are
  axioms not economics — RED force-exit, EVIDENCE_UNAVAILABLE, missing-authority,
  day0-zero-probability, settlement-imminent routing. These run BEFORE the marginal
  rule, unchanged.
* REPLACE: the economic rule body — win-rate floor (LIVE_DIRECTION_WIN_RATE_FLOOR),
  forward_edge thresholds, 2-consecutive EDGE_REVERSAL counters, HoldValue EV gates
  (_buy_yes_exit/_buy_no_exit). NOTE the floor constant has 3 consumers OUTSIDE
  portfolio.py (entry-side admission) — exits stop READING it; deleting the constant
  itself is the W5 taker-quality-floors packet, not this one.

W_j STATE IS GREENFIELD: no per-outcome-bin holdings vector exists anywhere today.
``build_wealth_by_outcome`` derives it per evaluation from open positions grouped by
(family, bin) plus spendable cash — derive-don't-store.

EXIT q_version GAP (executor.py:4394 omits the stamp): once exits flow through the
solve, exit orders are SolutionPlan orders and inherit the mandatory q_version stamp —
the gap closes structurally, no separate patch of the legacy exit path needed while it
lives (its rests stay governed by rest_deadline_exceeded per W1.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from src.solve.types import WealthByOutcome

if TYPE_CHECKING:
    from decimal import Decimal


def build_wealth_by_outcome(
    *,
    family_key: str,
    open_positions: Any,          # PortfolioState positions slice — typed in sub-slice 3
    bin_ids: tuple[str, ...],
    spendable_cash_usd: float,
) -> WealthByOutcome:
    """Derive W_j from open positions grouped by outcome bin + spendable cash.

    Contract: W_j = cash + Σ over held claims paying in bin j (shares × payout);
    every bin in bin_ids present; wealth strictly positive in every bin (a zero-wealth
    outcome makes log-utility undefined — positions implying it must surface as a typed
    error, never a silent clamp).
    """
    raise NotImplementedError(
        "W3 sub-slice 2/3: group open positions by bin, add cash, validate positivity"
    )


def marginal_exit_condition(
    *,
    bid: float,
    held_bin_id: str,
    q_by_bin_id: Mapping[str, float],
    wealth: WealthByOutcome,
) -> bool:
    """The C5 marginal condition for ONE marginal unit: b·Σ_j q_j/W_j > q_i/W_i.

    Pure predicate — the solver applies it implicitly through the objective (a
    sell_holding menu item with positive marginal ΔU); this explicit form exists for
    the monitor lane's cheap screen and for property tests (the two must agree on
    marginal direction at the current holdings point).
    """
    raise NotImplementedError(
        "W3 sub-slice 2: direct transcription of the C5 inequality with validated inputs"
    )
