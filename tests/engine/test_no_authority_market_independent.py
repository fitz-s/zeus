# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: no-order root diagnosis 2026-06-13
#   (docs/evidence/no_order_diagnosis/2026-06-13_liquid_bin_edge_split.md) — the
#   canonical builder zeroed q_lcb_no for every bin whose YES side had no executable
#   market (286/286 bins -> 0 buy_no ever cleared -> 0 orders). The native-NO
#   authority q_lcb_no = 1 - q_ucb_yes is a FORECAST quantity, defined for EVERY MECE
#   bin INDEPENDENT of the YES token's executability. A non-executable YES side gates
#   ONLY the buy_yes leg; it must NEVER zero the buy_no native-NO bound. Operator law:
#   no absolutist gate that cripples one side to force the other (the mirror of the
#   forbidden "do not buy YES on the forecast bin" hack).
"""Relationship test: native-NO authority is forecast-derived, market-INDEPENDENT.

Pins the de-hack of the q_lcb_no=0 zeroing. The forecast YES probability samples
(member resample + MAP Platt + posterior) exist for every bin regardless of whether
anyone is quoting the YES token, so the native-NO lower bound is positive for a far
bin even when its YES side is not executable.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
from src.engine.event_reactor_adapter import _side_q_lcb_from_yes_samples
from src.strategy.market_analysis import MarketAnalysis
from src.types.market import Bin


def _analysis_with_nonexecutable_far_bin():
    """A 2-bin family. Bin 0 (low far-tail) is NON-executable (no YES ask);
    members concentrate well below it so q_yes(bin0) is small => q_no large."""
    bins = [
        Bin(low=34.0, high=None, unit="C", label="34C or above"),  # far tail, unlikely
        Bin(low=None, high=34.0, unit="C", label="33C or below"),  # the favorite
    ]
    return MarketAnalysis(
        forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"),
        p_raw=np.array([0.04, 0.96]),
        p_cal=np.array([0.04, 0.96]),
        p_market=None,  # no executable YES-side market at all
        p_market_no=None,
        buy_no_quote_available=np.array([True, True]),
        alpha=0.0,
        bins=bins,
        member_maxes=np.asarray([28.0, 29.0, 30.0, 30.5, 31.0, 31.5], dtype=float),
        executable_mask=np.array([False, False]),
        rng_seed=11,
        representativeness_sigma=0.0,
    )


def test_bin_yes_probability_samples_keeps_executability_guard():
    """The buy_yes EDGE consumer's guard is preserved — a non-executable YES side
    still raises (the YES leg legitimately needs a market to subtract cost)."""
    a = _analysis_with_nonexecutable_far_bin()
    with pytest.raises(ValueError):
        a.bin_yes_probability_samples(0, 256)


def test_forecast_samples_are_market_independent():
    """The forecast sample engine works WITHOUT an executable market — the native-NO
    authority must not depend on YES executability."""
    a = _analysis_with_nonexecutable_far_bin()
    samples = a.forecast_yes_probability_samples(0, 2000)
    assert samples.shape == (2000,)
    assert np.all(samples >= 0.0) and np.all(samples <= 1.0)


def test_forecast_sample_matrix_is_one_coherent_zero_sum_draw_per_row():
    analysis = _analysis_with_nonexecutable_far_bin()
    matrix = analysis.forecast_yes_probability_sample_matrix(2000)

    assert matrix.shape == (2000, 2)
    assert np.allclose(matrix.sum(axis=1), 1.0, atol=1e-12)
    assert np.array_equal(
        analysis.forecast_yes_probability_samples(0, 2000), matrix[:, 0]
    )
    assert np.allclose(
        1.0 - analysis.forecast_yes_probability_samples(0, 2000), matrix[:, 1]
    )


def test_nonexecutable_yes_far_bin_has_positive_no_lower_bound():
    """THE de-hack invariant: a far bin with no executable YES market must carry a
    POSITIVE native-NO lower bound (q_lcb_no = 1 - q_ucb_yes), NOT the old
    absolutist 0 that structurally extinguished the favorite-longshot NO harvest."""
    a = _analysis_with_nonexecutable_far_bin()
    yes_point = float(a.p_posterior[0])
    samples = a.forecast_yes_probability_samples(0, 4000)
    _q_lcb_yes, q_lcb_no = _side_q_lcb_from_yes_samples(samples, q_yes_point=yes_point)
    # The far-tail bin is unlikely (q_yes small) so the NO win-mass is large; its
    # conservative lower bound must be materially above zero.
    assert q_lcb_no > 0.5, (
        f"q_lcb_no={q_lcb_no:.4f} — a far bin's native-NO bound collapsed toward the "
        "old absolutist zero; the NO harvest is structurally extinguished."
    )
    # And it never exceeds the NO point mass (1 - q_yes) — the proof-boundary invariant.
    assert q_lcb_no <= (1.0 - yes_point) + 1e-9
