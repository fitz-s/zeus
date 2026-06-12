# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: external deep code review 2026-06-12 FINDING-E (operator NO-CAPS law).
#   The hidden 2%-of-bankroll cap on the bump-to-venue-minimum refused positive-EV
#   candidates whose venue minimum exceeded 2% of the wallet -- an artificial throttle,
#   not an honest gate. DELETE the cap; admit on honest economics only (positive q_lcb EV
#   at the venue minimum + free-cash affordability).
"""FINDING-E relationship invariant: when the venue minimum exceeds the raw fractional-
Kelly stake, trade the minimum iff the robust q_lcb edge at the minimum is positive AND
the minimum fits the free-cash bound -- with NO percentage-of-bankroll cap. Otherwise
reject with the honest economic reason (EV non-positive at the venue minimum, or the
free-cash bound).
"""

from __future__ import annotations

import pytest

import src.engine.event_reactor_adapter as era

# Reuse the proven kernel harness + fixtures.
from tests.test_stake_min_order_not_edge_reversed import (  # noqa: E402
    _proof_from_row,
    _snapshot_row,
)


def _kernel(proof, *, bankroll, mult, floor_out=None, free_cash=None):
    return era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=proof,
        all_proofs=(proof,),
        extra_exposure_by_bin_id={},
        bankroll_usd=bankroll,
        kelly_multiplier=mult,
        stake_floor_out=floor_out,
        free_cash_usd=free_cash,
    )


def test_no_min_order_bump_cap_constant_deleted():
    """The artificial percentage-cap constant must no longer exist on the module."""
    assert not hasattr(era, "_MIN_ORDER_BUMP_MAX_BANKROLL_PCT")


def test_venue_minimum_above_raw_kelly_but_below_free_cash_trades_minimum():
    """q_lcb robustly positive, venue minimum ($30) > raw fractional-Kelly stake, EV at
    the minimum positive, and the minimum < free cash ($100): the order goes out AT THE
    MINIMUM. The minimum is ~3.3% of the $900 bankroll — above the deleted 2% cap — and
    still trades, proving the artificial cap is gone."""
    row = _snapshot_row(yes_asks=(("0.03", "100000000"),), min_order="1000")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.08, q_lcb_5pct=0.05,
    )
    floor: dict[str, object] = {}
    stake, price = _kernel(proof, bankroll=900.0, mult=0.125, floor_out=floor, free_cash=100.0)

    assert stake == 30.0
    assert price is not None
    assert floor.get("stake_floor") == "VENUE_MIN_ORDER"
    assert float(floor.get("stake_floor_delta_u_at_min_order")) > 0.0


def test_venue_minimum_exceeds_free_cash_is_honest_cash_block():
    """Same positive-EV candidate, but only $10 free cash: the $30 venue minimum is not
    affordable. Honest economic rejection (free-cash bound), not a percentage cap."""
    row = _snapshot_row(yes_asks=(("0.03", "100000000"),), min_order="1000")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.08, q_lcb_5pct=0.05,
    )
    with pytest.raises(era._StakeBelowMinOrder, match="free_cash"):
        _kernel(proof, bankroll=900.0, mult=0.125, free_cash=10.0)


def test_truly_reversed_edge_at_minimum_is_not_a_cap_reject():
    """A candidate with q_lcb (0.02) BELOW the all-in cost (0.03) has no positive edge at
    ANY size: it is a genuine no-trade (stake 0.0), the honest economic outcome — never an
    artificial-cap reject and never a min-order bump."""
    row = _snapshot_row(yes_asks=(("0.03", "100000000"),), min_order="1000")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.025, q_lcb_5pct=0.02,
    )
    stake, price = _kernel(proof, bankroll=900.0, mult=0.125, free_cash=1000.0)
    assert stake == 0.0
    assert price is None
