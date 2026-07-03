# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""build_wealth_by_atom — entry-side wealth-by-atom builder (W3.3 sub-slice 3).

Pure core with injected inputs: spendable cash (net of reservations) + per-atom holdings payout.
"""

from __future__ import annotations

import pytest

from src.solve.exits import ZeroWealthOutcomeError, build_wealth_by_atom
from tests.solve import support as F

AY = F.atom_id("y")
AN = F.atom_id("n")


def test_cash_only_endowment():
    w = build_wealth_by_atom(
        family_key=F.FAMILY, atom_ids=(AY, AN), holdings_payout_by_atom_id={},
        spendable_cash_usd=100.0, ledger_snapshot_id="snap1",
    )
    assert w.wealth_by_atom == {AY: 100.0, AN: 100.0}
    assert w.cash_usd == 100.0
    assert w.ledger_snapshot_id == "snap1"


def test_holdings_add_per_atom_payout():
    # a held YES on bin y pays $50 in atom y only
    w = build_wealth_by_atom(
        family_key=F.FAMILY, atom_ids=(AY, AN), holdings_payout_by_atom_id={AY: 50.0},
        spendable_cash_usd=100.0,
    )
    assert w.wealth_by_atom == {AY: 150.0, AN: 100.0}


def test_reservations_reduce_spendable_and_are_recorded():
    # the caller passes cash already NET of reservations; the raw reservation is recorded
    w = build_wealth_by_atom(
        family_key=F.FAMILY, atom_ids=(AY, AN), holdings_payout_by_atom_id={},
        spendable_cash_usd=70.0, reservations_usd=30.0, ledger_snapshot_id="snap2",
    )
    assert w.cash_usd == 70.0          # net of the $30 reservation
    assert w.reservations_usd == 30.0
    assert w.wealth_by_atom == {AY: 70.0, AN: 70.0}


def test_ledger_snapshot_id_round_trips():
    w = build_wealth_by_atom(
        family_key=F.FAMILY, atom_ids=(AY,), holdings_payout_by_atom_id={AY: 5.0},
        spendable_cash_usd=1.0, ledger_snapshot_id="ledger@abc123", source_positions=("pos1", "pos2"),
    )
    assert w.ledger_snapshot_id == "ledger@abc123"
    assert w.source_positions == ("pos1", "pos2")


def test_zero_wealth_atom_raises():
    # spendable 0 and no holdings in atom n -> W(n)=0 -> fail closed
    with pytest.raises(ZeroWealthOutcomeError, match="non-positive endowment"):
        build_wealth_by_atom(
            family_key=F.FAMILY, atom_ids=(AY, AN), holdings_payout_by_atom_id={AY: 10.0},
            spendable_cash_usd=0.0,
        )


def test_negative_wealth_atom_raises():
    with pytest.raises(ZeroWealthOutcomeError):
        build_wealth_by_atom(
            family_key=F.FAMILY, atom_ids=(AY,), holdings_payout_by_atom_id={AY: -5.0},
            spendable_cash_usd=1.0,
        )
