# Created: 2026-06-14
# Last reused/audited: 2026-06-14
# Authority basis: shadow-candidate framework removal
#                  (extracted verbatim from src/strategy/candidates/__init__.py;
#                   §19.2/§19.3 deterministic-edge decision contracts retained
#                   for the live analysis path src/analysis/deterministic_edge_report.py)
"""Deterministic-edge decision contracts (§19.2 / §19.3).

These three frozen dataclasses were originally defined inside the
shadow-candidate framework (src/strategy/candidates/__init__.py). They are
consumed on the LIVE analysis path by src/analysis/deterministic_edge_report.py,
so they outlive the shadow-candidate framework and were extracted here when that
framework was deleted.

  - LegIntent: single-leg fill intent produced by a vector-edge strategy.
  - DeterministicEdgeDecision: §19.2 single-leg pathwise-certain decision.
  - VectorEdgeDecision: §19.3 multi-leg deterministic basket decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Tuple


@dataclass(frozen=True)
class LegIntent:
    """Single-leg fill intent produced by a vector-edge strategy.

    side: "buy_yes" or "buy_no".
    condition_id: token being bought.
    quantity: shares to fill at q*.
    price_limit: maximum acceptable fill price (= best ask used in sweep).
    """

    side: Literal["buy_yes", "buy_no"]
    condition_id: str
    quantity: Decimal
    price_limit: Decimal


@dataclass(frozen=True)
class DeterministicEdgeDecision:
    """§19.2 deterministic single-leg decision — pathwise-certain payoff.

    Authority: zeus_strategy_spec.md §19.2.

    Fields are the §19 superset so single-leg deterministic strategies (D2/D3
    settlement capture, resolution window) can carry side/token_id/executable_price
    to the executor without casting to CandidateDecision.
    """

    outcome: Literal["enter"] = field(default="enter", init=False)
    strategy_key: str                   # e.g. "settlement_capture"
    proof_type: str                     # e.g. "physical_interval_subset"
    side: Literal["buy_yes", "buy_no"]  # token being bought
    token_id: str                       # Polymarket condition ID
    executable_price: Decimal           # best-ask fill price
    fee: Decimal                        # phi(q, price, rate) for this leg
    deterministic_payoff: Decimal       # gross payoff if executed ($)
    deterministic_profit: Decimal       # payoff - executable_price - fee
    proof_inputs_hash: str              # SHA-256 hex of proof inputs

    def __post_init__(self) -> None:
        if self.deterministic_profit <= Decimal(0):
            raise ValueError(
                "DeterministicEdgeDecision requires deterministic_profit > 0; "
                f"got {self.deterministic_profit}"
            )


@dataclass(frozen=True)
class VectorEdgeDecision:
    """§19.3 vector decision — multi-leg deterministic basket.

    Authority: zeus_strategy_spec.md §19.3.

    basket_execution_id: nullable UUID until §11.8 multi-leg execution lands;
      pass empty string "" for shadow rows.
    vector_cost: Σ sweep notional across all legs at q* (excluding fees).
    vector_fee: Σ phi(q*, price, rate) across all legs.
    vector_payoff: deterministic payoff (q* for YES basket; (K-1)*q* for NO).
    vector_profit: vector_payoff - vector_cost - vector_fee.
    """

    outcome: Literal["enter"] = field(default="enter", init=False)
    strategy_key: str                    # "neg_risk_basket"
    proof_type: str                      # "complete_family_basket"
    basket_execution_id: str             # "" until multi-leg execution ships
    legs: Tuple[LegIntent, ...]
    q_star: Decimal
    vector_cost: Decimal                 # Σ sweep notional (price × qty per level)
    vector_fee: Decimal                  # Σ phi across all legs
    vector_payoff: Decimal               # deterministic payoff
    vector_profit: Decimal               # = payoff - cost - fee
    proof_inputs_hash: str               # SHA-256 of (family legs, q_star, fee_rate) — §19.3

    def __post_init__(self) -> None:
        if self.vector_profit <= Decimal(0):
            raise ValueError(
                "VectorEdgeDecision requires vector_profit > 0; "
                f"got {self.vector_profit}"
            )
