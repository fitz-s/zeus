"""A maker fill probability that ignores distance turns the objective into an edge sort.

EV = p_fill x edge. With p_fill constant the ranking depends only on edge, so the winner is
always the most extreme longshot — the quote least likely to ever fill. Measured on the live
book that is exactly what happened: every winner rested in a book whose spread was 22x the
market median, and 46 of 46 entries went unfilled.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.engine.global_batch_runtime import (
    _MAKER_FILL_BAND_MIN_SAMPLE_SIZE,
    _CurrentMakerFillSample,
    _maker_fill_band_wilson_lower_bound,
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
        _maker_fill_band_wilson_lower_bound(43, 182),
        _maker_fill_band_wilson_lower_bound(25, 112),
        _maker_fill_band_wilson_lower_bound(7, 51),
        _maker_fill_band_wilson_lower_bound(2, 71),
        _maker_fill_band_wilson_lower_bound(0, 62),
    ]
    assert bounds == sorted(bounds, reverse=True), bounds
    assert bounds[0] > Decimal("0.1"), "the nearest band must keep a usable rate"
    assert bounds[-1] == Decimal("0"), "62 rests with no fill is a measurement, not noise"


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
    assert _maker_fill_band_wilson_lower_bound(0, trials) == Decimal("0")


def test_min_band_sample_size_is_enforced_as_a_constant():
    assert _MAKER_FILL_BAND_MIN_SAMPLE_SIZE >= 20
