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
        hours_to_settlement: float | None,
        fee_rate: float,
        daily_hurdle_rate: float,
        correlation_crowding: float = 0.0,
    ) -> "HoldValue":
        """T6.4 factory: compute HoldValue for exit-decision path with
        fee + time + (optional) correlation costs.

        gross_value = shares × p_posterior (expected settlement value)
        fee_cost    = shares × polymarket_fee(best_bid, fee_rate)
        time_cost   = (shares × best_bid) × (hours_to_settlement / 24) × daily_hurdle_rate
        correlation_crowding: optional D6-phase2 cost once portfolio
            context is threaded through ExitContext; default 0.0 means
            "not yet wired" per T6.4-minimal scope.

        When hours_to_settlement is None (unavailable), time_cost
        collapses to 0.0 — caller should treat that as a soft
        conservative default rather than an authority gap; the exit
        path's INCOMPLETE_EXIT_CONTEXT gate handles hard authority
        failures before this factory is called.

        Args:
            shares: notional share count (size_usd / entry_price).
            current_p_posterior: fresh posterior at held bin.
            best_bid: current best bid price on the held side.
            hours_to_settlement: hours until market resolves (None
                collapses time_cost to 0.0 as a soft default).
            fee_rate: fee_rate for polymarket_fee() formula; typically
                config.exit_fee_rate().
            daily_hurdle_rate: daily opportunity-cost rate; typically
                config.exit_daily_hurdle_rate().
            correlation_crowding: optional (T6.4-phase2) portfolio-level
                crowding cost; default 0.0 until ExitContext carries
                portfolio-position references.
        """
        # Import locally to avoid circular dependency
        # (hold_value → execution_price → … → hold_value).
        from src.contracts.execution_price import polymarket_fee

        gross_value = float(shares) * float(current_p_posterior)

        # Fee cost: polymarket fee formula is fee_per_share = rate * p * (1-p);
        # scale by shares to get total fee cost of the exit order.
        fee_per_share = polymarket_fee(float(best_bid), float(fee_rate))
        fee_cost = float(shares) * fee_per_share

        # Time cost: capital locked at (shares × best_bid) for the remaining
        # hold window, discounted at daily_hurdle_rate. If hours unknown,
        # collapse to 0.0 (soft default — not a semantic claim about zero
        # cost; the caller path's INCOMPLETE_EXIT_CONTEXT gate handles real
        # authority absence before this factory is reached).
        if hours_to_settlement is None or hours_to_settlement < 0.0:
            time_cost = 0.0
        else:
            capital_locked = float(shares) * float(best_bid)
            days = float(hours_to_settlement) / 24.0
            time_cost = capital_locked * days * float(daily_hurdle_rate)

        extra_costs = {}
        if correlation_crowding > 0.0:
            extra_costs["correlation_crowding"] = float(correlation_crowding)

        return cls.compute(
            gross_value=gross_value,
            fee_cost=fee_cost,
            time_cost=time_cost,
            extra_costs=extra_costs or None,
        )

    def is_worth_holding(self, min_net_threshold: float = 0.0) -> bool:
        """True if net_value exceeds the hold threshold."""
        return self.net_value > min_net_threshold


class HoldValueCostDeclarationError(Exception):
    """Raised when HoldValue is constructed without declaring required costs.
    This is the D6 runtime contract violation — undeclared costs are treated as
    zero, causing systematic underestimation of the true cost of holding.
    """
