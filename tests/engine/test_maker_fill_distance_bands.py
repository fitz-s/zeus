# Created: 2026-09-17
# Last reused/audited: 2026-09-30
# Authority basis: distance-conditioned maker fill bands; thin early market
#   maker price menu (one far-edge price per band, operator law 2026-09-30).
"""A maker fill probability that ignores distance turns the objective into an edge sort.

EV = p_fill x edge. With p_fill constant the ranking depends only on edge, so the winner is
always the most extreme longshot — the quote least likely to ever fill. Measured on the live
book that is exactly what happened: every winner rested in a book whose spread was 22x the
market median, and 46 of 46 entries went unfilled.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.contracts.probability_arithmetic import Z_ONE_SIDED_95, Z_TWO_SIDED_95, wilson_lower_bound
from src.engine.global_batch_runtime import (
    _MAKER_FILL_BAND_MIN_SAMPLE_SIZE,
    _CurrentMakerFillSample,
    _maker_fill_distance_band,
)


def test_bands_order_by_distance():
    assert _maker_fill_distance_band(Decimal("0.01")) == 0
    assert _maker_fill_distance_band(Decimal("0.02")) == 0
    assert _maker_fill_distance_band(Decimal("0.03")) == 1
    assert _maker_fill_distance_band(Decimal("0.10")) == 2
    assert _maker_fill_distance_band(Decimal("0.30")) == 3
    assert _maker_fill_distance_band(Decimal("0.90")) == 4


def test_wilson_preserves_the_ordering_dkw_erased():
    """The measured band counts, whose ordering the pooled DKW radius destroyed."""
    bounds = [
        wilson_lower_bound(43, 182, z=Z_TWO_SIDED_95),
        wilson_lower_bound(25, 112, z=Z_TWO_SIDED_95),
        wilson_lower_bound(7, 51, z=Z_TWO_SIDED_95),
        wilson_lower_bound(2, 71, z=Z_TWO_SIDED_95),
        wilson_lower_bound(0, 62, z=Z_TWO_SIDED_95),
    ]
    assert bounds == sorted(bounds, reverse=True), bounds
    assert bounds[0] > 0.1, "the nearest band must keep a usable rate"
    assert bounds[-1] == 0.0, "62 rests with no fill is a measurement, not noise"


def test_a_band_that_never_filled_reads_as_zero_not_as_the_pooled_rate():
    sample = _CurrentMakerFillSample(
        action="BUY",
        fill_fractions=tuple(Decimal("1") for _ in range(40)),
        fill_probability_lcb=Decimal("0.0699"),
        sample_identity="test",
        training_cutoff_at_utc=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        rest_deadline_minutes=20.0,
        fill_probability_lcb_by_band=((0, Decimal("0.1775")), (4, Decimal("0"))),
    )
    assert sample.band_fill_probability_lcb(Decimal("0.01")) == Decimal("0.1775")
    assert sample.band_fill_probability_lcb(Decimal("0.90")) == Decimal("0")


def test_a_band_without_its_own_evidence_falls_back_to_the_pooled_bound():
    """Absence of a band means too few rests to speak, not a rate of zero."""
    sample = _CurrentMakerFillSample(
        action="BUY",
        fill_fractions=tuple(Decimal("1") for _ in range(40)),
        fill_probability_lcb=Decimal("0.0699"),
        sample_identity="test",
        training_cutoff_at_utc=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        rest_deadline_minutes=20.0,
        fill_probability_lcb_by_band=((0, Decimal("0.1775")),),
    )
    assert sample.band_fill_probability_lcb(Decimal("0.30")) == Decimal("0.0699")


@pytest.mark.parametrize("trials", [0, -1])
def test_no_trials_is_no_bound(trials):
    assert wilson_lower_bound(0, trials, z=Z_TWO_SIDED_95) == 0.0


def test_min_band_sample_size_is_enforced_as_a_constant():
    assert _MAKER_FILL_BAND_MIN_SAMPLE_SIZE >= 20


def _sample(bands):
    import datetime as _dt

    return _CurrentMakerFillSample(
        action="BUY",
        fill_fractions=tuple(Decimal("1") for _ in range(40)),
        fill_probability_lcb=Decimal("0.0699"),
        sample_identity="test",
        training_cutoff_at_utc=_dt.datetime.now(_dt.timezone.utc),
        rest_deadline_minutes=20.0,
        fill_probability_lcb_by_band=bands,
    )


def test_a_zero_rate_band_withdraws_the_maker_proposal_instead_of_stating_zero():
    """A zero-probability outcome is rejected by MakerFillOutcome and would fail the auction.

    The honest statement for a distance that never filled is that no maker witness exists, so
    the taker competes alone. Live 2026-09-17: emitting the zero instead produced 32
    `GLOBAL_AUCTION_FAILED:ValueError:maker fill outcome is invalid` in 30 minutes.
    """
    from src.engine.global_batch_runtime import _maker_fill_outcomes

    sample = _sample(((0, Decimal("0.1775")), (4, Decimal("0"))))
    assert _maker_fill_outcomes(
        sample, limit_price=Decimal("0.09"), counterparty_price=Decimal("0.99")
    ) == ()


def test_a_reachable_band_still_states_its_distribution():
    from src.engine.global_batch_runtime import _maker_fill_outcomes

    sample = _sample(((0, Decimal("0.1775")), (4, Decimal("0"))))
    outcomes = _maker_fill_outcomes(
        sample, limit_price=Decimal("0.50"), counterparty_price=Decimal("0.51")
    )
    assert outcomes
    assert sum(row.probability for row in outcomes) == Decimal("1")
    assert all(row.probability > 0 for row in outcomes)


def test_without_a_counterparty_price_the_pooled_bound_still_applies():
    from src.engine.global_batch_runtime import _maker_fill_outcomes

    sample = _sample(((4, Decimal("0")),))
    outcomes = _maker_fill_outcomes(sample, limit_price=Decimal("0.50"))
    assert outcomes
    assert sum(row.probability for row in outcomes) == Decimal("1")


@pytest.mark.parametrize("trials", [24, 25, 28, 35, 48, 50, 63, 100, 250])
def test_a_band_with_no_fill_is_exactly_zero_not_float_residue(trials):
    """0/48 read as 6.938893903907228e-18 live and minted a maker witness from it."""
    assert wilson_lower_bound(0, trials, z=Z_TWO_SIDED_95) == 0.0


def _menu(bid, ask, tick="0.01"):
    from src.engine.global_batch_runtime import _MAKER_FILL_DISTANCE_BAND_EDGES
    from src.solve.solver import maker_buy_price_menu

    return maker_buy_price_menu(
        best_bid=None if bid is None else Decimal(bid),
        best_ask=Decimal(ask),
        tick=Decimal(tick),
        band_edges=_MAKER_FILL_DISTANCE_BAND_EDGES,
        band_of=_maker_fill_distance_band,
    )


@pytest.mark.parametrize(
    ("bid", "ask", "tick", "expected"),
    (
        # Each band's far edge ceil_tick(ask - edge), clipped into (bid, ask).
        ("0.30", "0.40", "0.01", ("0.38", "0.35", "0.31")),
        # Thin early book with no bid: down to the lowest in-band price.
        (None, "0.40", "0.01", ("0.38", "0.35", "0.25", "0.05")),
        # The live band ceiling 0.95 binds hi; 0.92 / 0.82 / 0.47 are far edges.
        (None, "0.97", "0.01", ("0.95", "0.92", "0.82", "0.47")),
        # A bid below the band floor never lowers lo under 0.05.
        ("0.02", "0.90", "0.001", ("0.88", "0.85", "0.75", "0.40")),
        # Clipping to lo can leave a band: 0.05 on a 0.06 ask is band 0 only.
        (None, "0.06", "0.01", ("0.05",)),
        # One-tick spread: no price strictly inside it.
        ("0.39", "0.40", "0.01", ()),
    ),
)
def test_maker_menu_is_one_far_edge_price_per_reachable_band(bid, ask, tick, expected):
    menu = _menu(bid, ask, tick)
    assert menu == tuple(Decimal(price) for price in expected)
    assert len(set(menu)) == len(menu)
    for price in menu:
        assert Decimal("0.05") <= price <= Decimal("0.95")
        assert price < Decimal(ask)
        assert bid is None or price > Decimal(bid)
    bands = [_maker_fill_distance_band(Decimal(ask) - price) for price in menu]
    assert bands == sorted(set(bands))


def test_maker_menu_far_edge_is_the_cheapest_price_with_the_same_fill_band():
    """Within a band the fill model is constant, so one tick lower would leave it."""

    ask = Decimal("0.40")
    for price in _menu(None, "0.40"):
        band = _maker_fill_distance_band(ask - price)
        lower = price - Decimal("0.01")
        if lower >= Decimal("0.05"):
            assert _maker_fill_distance_band(ask - lower) != band
