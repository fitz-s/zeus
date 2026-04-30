"""Illustrative skeleton only. Adapt to Zeus contracts after topology admission."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


OrderPolicy = Literal[
    "LIMIT_MAY_TAKE_CONSERVATIVE",
    "POST_ONLY_PASSIVE_LIMIT",
    "MARKETABLE_LIMIT_DEPTH_BOUND",
]


@dataclass(frozen=True)
class ExecutableCostBasis:
    selected_token_id: str
    selected_outcome_label: Literal["YES", "NO"]
    direction: Literal["BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"]
    order_policy: OrderPolicy
    requested_size_kind: Literal["shares", "notional_usd"]
    requested_size_value: Decimal
    final_limit_price: Decimal
    expected_fill_price_before_fee: Decimal
    fee_adjusted_execution_price: Decimal
    worst_case_fee_rate: Decimal
    fee_source: str
    tick_size: Decimal
    min_order_size: Decimal
    tick_status: Literal["PASS", "FAIL"]
    min_order_status: Literal["PASS", "FAIL"]
    depth_status: str
    quote_snapshot_id: str
    quote_snapshot_hash: str
    cost_basis_hash: str

    def assert_live_safe(self) -> None:
        if not self.selected_token_id:
            raise ValueError("missing selected_token_id")
        if not self.quote_snapshot_id or not self.quote_snapshot_hash:
            raise ValueError("missing snapshot lineage")
        if not self.cost_basis_hash:
            raise ValueError("missing cost_basis_hash")
        if self.tick_status != "PASS":
            raise ValueError("tick validation failed")
        if self.min_order_status != "PASS":
            raise ValueError("min-order validation failed")
        if self.final_limit_price <= 0 or self.final_limit_price >= 1:
            raise ValueError("final_limit_price must be in (0, 1)")
        if self.fee_adjusted_execution_price <= 0 or self.fee_adjusted_execution_price >= 1:
            raise ValueError("fee_adjusted_execution_price must be in (0, 1)")
