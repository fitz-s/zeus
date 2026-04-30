"""Illustrative skeleton only. Adapt to Zeus contracts after topology admission."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ExecutableTradeHypothesis:
    event_id: str
    bin_id: str
    direction: Literal["BUY_YES", "BUY_NO"]
    selected_token_id: str
    payoff_probability: Decimal
    posterior_distribution_id: str
    market_prior_id: str | None
    executable_snapshot_id: str
    executable_snapshot_hash: str
    executable_cost_basis_id: str
    executable_cost_basis_hash: str
    order_policy: str
    fdr_family_id: str
    fdr_hypothesis_id: str
    pricing_semantics_version: Literal["corrected_executable_cost_v1"]

    @property
    def identity_tuple(self) -> tuple[str, ...]:
        return (
            self.event_id,
            self.bin_id,
            self.direction,
            self.selected_token_id,
            self.executable_snapshot_id,
            self.executable_snapshot_hash,
            self.executable_cost_basis_id,
            self.executable_cost_basis_hash,
            self.order_policy,
        )

    def assert_identity_complete(self) -> None:
        for part in self.identity_tuple:
            if not part:
                raise ValueError("executable hypothesis identity is incomplete")
        if not (Decimal("0") <= self.payoff_probability <= Decimal("1")):
            raise ValueError("payoff_probability must be within [0, 1]")
