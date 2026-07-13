# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""build_wealth_by_atom — entry-side wealth-by-atom builder (W3.3 sub-slice 3).

Pure core with injected inputs: spendable cash (net of reservations) + per-atom holdings payout.
"""

from __future__ import annotations

import pytest

from src.solve.exits import (
    ExitPrecheckResult,
    ZeroWealthOutcomeError,
    build_wealth_by_atom,
    marginal_exit_condition,
)
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


def _exit_wealth(*, yes: float = 120.0, no: float = 100.0):
    return build_wealth_by_atom(
        family_key=F.FAMILY,
        atom_ids=(AY, AN),
        holdings_payout_by_atom_id={AY: yes - 100.0, AN: no - 100.0},
        spendable_cash_usd=100.0,
    )


def test_exit_precheck_force_decision_precedes_invalid_economics():
    assert marginal_exit_condition(
        precheck=ExitPrecheckResult(True, "RED", True),
        bid=float("nan"),
        held_atom_id="missing",
        q_by_atom_id={},
        wealth=_exit_wealth(),
    ) is True
    assert marginal_exit_condition(
        precheck=ExitPrecheckResult(True, "EVIDENCE_UNAVAILABLE", False),
        bid=float("nan"),
        held_atom_id="missing",
        q_by_atom_id={},
        wealth=_exit_wealth(),
    ) is False


def test_exit_marginal_sells_when_cash_utility_dominates_claim():
    assert marginal_exit_condition(
        precheck=ExitPrecheckResult(False, None, False),
        bid=0.50,
        held_atom_id=AY,
        q_by_atom_id={AY: 0.20, AN: 0.80},
        wealth=_exit_wealth(),
    ) is True


def test_exit_marginal_holds_when_claim_utility_dominates_cash():
    assert marginal_exit_condition(
        precheck=ExitPrecheckResult(False, None, False),
        bid=0.50,
        held_atom_id=AY,
        q_by_atom_id={AY: 0.80, AN: 0.20},
        wealth=_exit_wealth(),
    ) is False


def test_exit_marginal_day0_dead_claim_sells_at_any_positive_bid():
    assert marginal_exit_condition(
        precheck=ExitPrecheckResult(False, None, False),
        bid=0.01,
        held_atom_id=AY,
        q_by_atom_id={AY: 0.0, AN: 1.0},
        wealth=_exit_wealth(),
    ) is True


def test_exit_marginal_is_strict_at_indifference():
    wealth = build_wealth_by_atom(
        family_key=F.FAMILY,
        atom_ids=(AY,),
        holdings_payout_by_atom_id={},
        spendable_cash_usd=100.0,
    )
    assert marginal_exit_condition(
        precheck=ExitPrecheckResult(False, None, False),
        bid=1.0,
        held_atom_id=AY,
        q_by_atom_id={AY: 1.0},
        wealth=wealth,
    ) is False


@pytest.mark.parametrize(
    ("bid", "q", "wealth", "error"),
    (
        (float("nan"), {AY: 0.5, AN: 0.5}, _exit_wealth(), ValueError),
        (0.5, {AY: 0.5}, _exit_wealth(), ValueError),
        (0.5, {AY: 0.4, AN: 0.4}, _exit_wealth(), ValueError),
    ),
)
def test_exit_marginal_rejects_invalid_economic_authority(bid, q, wealth, error):
    with pytest.raises(error):
        marginal_exit_condition(
            precheck=ExitPrecheckResult(False, None, False),
            bid=bid,
            held_atom_id=AY,
            q_by_atom_id=q,
            wealth=wealth,
        )
