# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-position-model-refactor.md (Finding 4, PR A scaffold; flipped by PR D)
"""Antibody invariants: `get_open_positions(chain_view=...)` is PURE.

Finding 4 (P1 confirmed bug): src/state/portfolio.py:get_open_positions
mutates `pos.shares`, `pos.entry_price`, and `pos.chain_state = "synced"`
from a chain_view parameter, without appending a canonical
`position_events` row. This bypasses the "append canonical event before
projection mutation" law and means intra-process state can diverge from
durable projection silently.

Fix (PR D): make read helpers pure; route any chain-derived correction
through `chain_reconciliation` which emits a typed `CHAIN_SIZE_CORRECTED`
or `VENUE_POSITION_OBSERVED` event.

This test is STRICT-XFAIL until PR D.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from src.state.portfolio import Position, PortfolioState, get_open_positions


@dataclass
class _StubChainPosition:
    asset_id: str
    size: float
    avg_price: float


@dataclass
class _StubChainView:
    """Minimal duck-typed chain view exposing get_position(asset_id)."""

    positions: dict
    is_stale: bool = False

    def get_position(self, asset_id: str) -> Optional[_StubChainPosition]:
        return self.positions.get(asset_id)


def _make_pos() -> Position:
    return Position(
        trade_id="inv-D-001",
        market_id="mkt-D",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-06-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        edge=0.15,
        shares=25.0,
        cost_basis_usd=10.0,
        state="holding",
        token_id="tok_yes_D",
        no_token_id="tok_no_D",
        unit="F",
        env="live",
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PR A scaffold (Finding 4): get_open_positions(chain_view=...) at "
        "portfolio.py:~2057 mutates pos.shares, pos.entry_price, and "
        "pos.chain_state without a canonical event. PR D removes the mutation."
    ),
)
def test_get_open_positions_with_chain_view_does_not_mutate_position() -> None:
    pos = _make_pos()
    pre_shares = pos.shares
    pre_price = pos.entry_price
    pre_chain_state = pos.chain_state

    chain_view = _StubChainView(
        positions={"tok_yes_D": _StubChainPosition("tok_yes_D", size=7.0, avg_price=0.45)},
        is_stale=False,
    )
    state = PortfolioState(positions=[pos])

    # Drive the helper. We don't care about the returned list shape; we care
    # that the input `pos` is structurally unmodified.
    _ = get_open_positions(state, chain_view=chain_view)

    assert pos.shares == pre_shares, (
        f"shares mutated from {pre_shares} to {pos.shares} — chain_view must not "
        "alter Position without a canonical event."
    )
    assert pos.entry_price == pre_price, (
        f"entry_price mutated from {pre_price} to {pos.entry_price} — chain_view "
        "must not alter Position without a canonical event."
    )
    assert pos.chain_state == pre_chain_state, (
        f"chain_state mutated from {pre_chain_state!r} to {pos.chain_state!r} — "
        "chain_view must not alter Position without a canonical event."
    )
