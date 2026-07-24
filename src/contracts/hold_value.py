"""Hold value contract — D6 resolution.

D6 gap: exit_triggers EV gate computes net_hold = shares × p_posterior, assuming
free carry to settlement. Ignores opportunity cost of locked bankroll and
correlation crowding of other positions. A position looks profitable to hold when
it is actually eating into portfolio capacity below its hurdle rate.

Resolution: HoldValue contract must declare what costs are included. Default
minimum is fee + time-cost-to-settlement.

See: docs/zeus_FINAL_spec.md §P9.3 D6
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from src.contracts.execution_price import polymarket_fee


@dataclass(frozen=True)
class HoldValue:
    """Typed hold-value calculation declaring all cost components.

    Resolves D6: prevents exit_triggers from treating hold as free carry.
    Callers must declare which costs are included; undeclared costs are assumed
    zero, which risks systematic underestimation of hold cost.

    Attributes:
        gross_value: Expected value before any cost deduction,
            e.g. shares × p_posterior.
        fee_cost: Estimated fee cost of eventual exit (taker fee × size).
        time_cost: Opportunity cost of locked bankroll over remaining hold
            window (e.g. bankroll_fraction × days_to_settlement × daily_hurdle).
        net_value: gross_value - fee_cost - time_cost. Must equal the
            arithmetic result; validated in __post_init__.
        costs_declared: List of cost names included in the deduction. At
            minimum must include "fee" and "time". Callers adding correlation
            cost must add "correlation_crowding" to this list.
    """

    gross_value: float
    fee_cost: float
    time_cost: float
    net_value: float
    costs_declared: List[str]
    extra_costs_total: float = 0.0

    def __post_init__(self) -> None:
        if self.fee_cost < 0.0:
            raise ValueError(
                f"HoldValue.fee_cost must be >= 0, got {self.fee_cost}"
            )
        if self.time_cost < 0.0:
            raise ValueError(
                f"HoldValue.time_cost must be >= 0, got {self.time_cost}"
            )
        if self.extra_costs_total < 0.0:
            raise ValueError(
                f"HoldValue.extra_costs_total must be >= 0, got {self.extra_costs_total}"
            )

        # T6.4 plan-premise correction #22: original validator only checked
        # gross - fee - time, ignoring extra_costs folded into net_value
        # by HoldValue.compute(). That left a latent arithmetic gap for any
        # caller passing correlation_crowding or other extras — the record
        # would fail __post_init__ on construction. Validator now accounts
        # for extra_costs_total.
        expected_net = (
            self.gross_value
            - self.fee_cost
            - self.time_cost
            - self.extra_costs_total
        )
        if abs(self.net_value - expected_net) > 1e-9:
            raise ValueError(
                f"HoldValue.net_value={self.net_value} does not equal "
                f"gross - fee - time - extras = {expected_net:.10f}. "
                "Construct HoldValue using HoldValue.compute() to avoid arithmetic errors."
            )

        missing = [c for c in ("fee", "time") if c not in self.costs_declared]
        if missing:
            raise HoldValueCostDeclarationError(
                f"HoldValue.costs_declared is missing required cost categories: {missing}. "
                "Minimum required: ['fee', 'time']. "
                "If these costs are genuinely zero, declare them explicitly with value=0."
            )

    @classmethod
    def compute(
        cls,
        gross_value: float,
        fee_cost: float,
        time_cost: float,
        extra_costs: dict[str, float] | None = None,
    ) -> "HoldValue":
        """Factory: compute net_value and build costs_declared automatically.

        Args:
            gross_value: shares × p_posterior or equivalent gross EV.
            fee_cost: taker fee estimate for exit.
            time_cost: opportunity cost of locked capital to settlement.
            extra_costs: optional dict of additional cost name → value,
                e.g. {"correlation_crowding": 0.003}.
        """
        extra_costs = extra_costs or {}
        total_extra = sum(extra_costs.values())
        net = gross_value - fee_cost - time_cost - total_extra
        declared = ["fee", "time"] + list(extra_costs.keys())
        return cls(
            gross_value=gross_value,
            fee_cost=fee_cost,
            time_cost=time_cost,
            net_value=net,
            costs_declared=declared,
            extra_costs_total=total_extra,
        )

    @classmethod
    def compute_with_exit_costs(
        cls,
        shares: float,
        current_p_posterior: float,
        best_bid: float,
        fee_rate: float,
    ) -> "HoldValue":
        """Exit-path HoldValue with fee as the only forward friction cost.

        gross_value = shares × p_posterior (expected settlement value)
        fee_cost    = shares × polymarket_fee(best_bid, fee_rate)
        time_cost   = 0.0

        PR-1 (COLLISION.md §离场律 / KILL list): the static daily-hurdle
        time-cost and the correlation-crowding surcharge are RETIRED. A single
        static per-day hurdle is not the causal opportunity cost of locked
        capital — that is the joint allocator's cash shadow-value increment
        r_t = [J(F+L)−J(F)]/L, injected as the ΔJ term of the stopping law in
        PR-2. Until then the exit stop (predicted_bin_law.exit_decision) is the
        ΔJ≡0 special case and this contract carries fee only.
        # PR-2 SEAM: allocator ΔJ replaces the retired static hurdle/correlation.
        """
        gross_value = float(shares) * float(current_p_posterior)

        # polymarket_fee raises on price in {0.0, 1.0}, but a bid can legally sit
        # at either extreme near settlement. Clamp to (EPS, 1-EPS) so the fee
        # stays finite; the clamp delta is negligible in a fee-of-fee context.
        _BID_EPS = 1e-6
        clamped_bid = min(max(float(best_bid), _BID_EPS), 1.0 - _BID_EPS)
        fee_per_share = polymarket_fee(clamped_bid, float(fee_rate))
        fee_cost = float(shares) * fee_per_share

        return cls.compute(
            gross_value=gross_value,
            fee_cost=fee_cost,
            time_cost=0.0,
        )

    def is_worth_holding(self, min_net_threshold: float = 0.0) -> bool:
        """True if net_value exceeds the hold threshold."""
        return self.net_value > min_net_threshold


class HoldValueCostDeclarationError(Exception):
    """Raised when HoldValue is constructed without declaring required costs.
    This is the D6 runtime contract violation — undeclared costs are treated as
    zero, causing systematic underestimation of the true cost of holding.
    """
