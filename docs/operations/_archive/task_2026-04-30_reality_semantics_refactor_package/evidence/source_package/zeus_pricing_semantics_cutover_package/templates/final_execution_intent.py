"""Illustrative skeleton only. Adapt to Zeus contracts after topology admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class FinalExecutionIntent:
    hypothesis_id: str
    selected_token_id: str
    direction: Literal["BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"]
    size_kind: Literal["shares", "notional_usd"]
    size_value: Decimal
    final_limit_price: Decimal
    fee_adjusted_execution_price: Decimal
    order_policy: str
    order_type: Literal["GTC", "GTD", "FOK", "FAK"]
    post_only: bool
    cancel_after: datetime | None
    snapshot_id: str
    snapshot_hash: str
    cost_basis_id: str
    cost_basis_hash: str
    max_slippage_bps: int
    tick_size: Decimal
    min_order_size: Decimal
    fee_rate: Decimal
    neg_risk: bool

    def assert_no_recompute_inputs(self) -> None:
        """Marker method: corrected executor should not need posterior/VWMP/BinEdge."""
        pass

    def assert_submit_ready(self) -> None:
        missing = [
            name for name in (
                "hypothesis_id",
                "selected_token_id",
                "snapshot_id",
                "snapshot_hash",
                "cost_basis_id",
                "cost_basis_hash",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"FinalExecutionIntent missing fields: {missing}")
        if self.final_limit_price <= 0 or self.final_limit_price >= 1:
            raise ValueError("final_limit_price must be in (0, 1)")
        if self.post_only and self.order_type in {"FOK", "FAK"}:
            raise ValueError("post_only cannot be combined with market order types")
