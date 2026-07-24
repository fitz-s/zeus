from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.native_holdings import native_holdings_snapshot_from_positions


def _omega():
    return SimpleNamespace(
        bins=(
            SimpleNamespace(
                bin_id="y",
                condition_id="cond-y",
                yes_token_id="yes-y",
                no_token_id="no-y",
            ),
            SimpleNamespace(
                bin_id="n",
                condition_id="cond-n",
                yes_token_id="yes-n",
                no_token_id="no-n",
            ),
        )
    )


def test_native_holdings_bind_yes_no_and_pending_to_current_topology():
    positions = (
        SimpleNamespace(
            position_id="pos-yes",
            condition_id="cond-y",
            direction="buy_yes",
            token_id="yes-y",
            no_token_id="no-y",
            chain_shares=Decimal("3.5"),
        ),
        SimpleNamespace(
            position_id="pos-no",
            condition_id="cond-n",
            direction="buy_no",
            token_id="yes-n",
            no_token_id="no-n",
            chain_shares=Decimal("4.25"),
        ),
    )
    snapshot = native_holdings_snapshot_from_positions(
        family_key="fam",
        omega=_omega(),
        positions=positions,
        ledger_snapshot_id="ledger-current",
        pending_entry_endowments=(("ob-1", "yes-n", Decimal("2")),),
    )
    assert tuple(
        (holding.position_id, holding.bin_id, holding.side, holding.token_id, holding.shares)
        for holding in snapshot.holdings
    ) == (
        ("pos-yes", "y", "YES", "yes-y", Decimal("3.5")),
        ("pos-no", "n", "NO", "no-n", Decimal("4.25")),
    )
    assert snapshot.pending_endowments[0].token_id == "yes-n"


def test_native_holdings_reject_stale_token_binding():
    position = SimpleNamespace(
        trade_id="pos-y",
        condition_id="cond-y",
        direction="buy_no",
        token_id="yes-y",
        no_token_id="stale",
        chain_shares=Decimal("2"),
    )
    with pytest.raises(ValueError, match="token does not match current omega"):
        native_holdings_snapshot_from_positions(
            family_key="fam",
            omega=_omega(),
            positions=(position,),
            ledger_snapshot_id="ledger-current",
        )


@pytest.mark.parametrize("shares", [Decimal("-1"), Decimal("NaN")])
def test_native_holdings_reject_invalid_inventory(shares):
    position = SimpleNamespace(
        trade_id="corrupt",
        condition_id="cond-y",
        direction="buy_yes",
        token_id="yes-y",
        no_token_id="no-y",
        chain_shares=shares,
    )
    with pytest.raises(ValueError, match="invalid chain_shares"):
        native_holdings_snapshot_from_positions(
            family_key="fam",
            omega=_omega(),
            positions=(position,),
            ledger_snapshot_id="ledger-current",
        )


def test_native_holdings_uses_ledger_balance_and_skips_zero():
    position = SimpleNamespace(
        trade_id="pos-y",
        condition_id="cond-y",
        direction="buy_yes",
        token_id="yes-y",
        no_token_id="no-y",
        chain_shares=Decimal("99"),
    )
    snapshot = native_holdings_snapshot_from_positions(
        family_key="fam",
        omega=_omega(),
        positions=(position,),
        ledger_snapshot_id="ledger-current",
        token_shares_by_id={"yes-y": Decimal("0")},
    )
    assert snapshot.holdings == ()
