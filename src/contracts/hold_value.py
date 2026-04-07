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

from dataclasses import dataclass, field
from typing import List


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

    def __post_init__(self) -> None:
        if self.fee_cost < 0.0:
            raise ValueError(
                f"HoldValue.fee_cost must be >= 0, got {self.fee_cost}"
            )
        if self.time_cost < 0.0:
            raise ValueError(
                f"HoldValue.time_cost must be >= 0, got {self.time_cost}"
            )

        expected_net = self.gross_value - self.fee_cost - self.time_cost
        if abs(self.net_value - expected_net) > 1e-9:
            raise ValueError(
                f"HoldValue.net_value={self.net_value} does not equal "
                f"gross - fee - time = {expected_net:.10f}. "
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
        # Adjust net to include extra costs in the canonical net_value field.
        # We store only fee+time in the named fields and treat extra as implicitly
        # folded — caller should pass fee_cost + time_cost + sum(extra) for full net.
        # Simpler: recompute net incorporating extras directly.
        return cls(
            gross_value=gross_value,
            fee_cost=fee_cost,
            time_cost=time_cost,
            net_value=net,
            costs_declared=declared,
        )

    def is_worth_holding(self, min_net_threshold: float = 0.0) -> bool:
        """True if net_value exceeds the hold threshold."""
        return self.net_value > min_net_threshold


class HoldValueCostDeclarationError(Exception):
    """Raised when HoldValue is constructed without declaring required costs.
    This is the D6 runtime contract violation — undeclared costs are treated as
    zero, causing systematic underestimation of the true cost of holding.
    """
