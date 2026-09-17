"""The margin artifact must be measured against the source that settles.

The WU-era refitter compares WU's METAR mirror with IEM's METAR archive — two mirrors of one
feed, which agree byte-for-byte, which is why 47 of 54 cities currently admit a raw METAR
reading into the Day0 belief with a margin of exactly 0.0. Since 4f48d461e the settlement
product is the NOAA WRH page, and page-vs-mirror disagree on the settlement integer often
enough to matter (Denver 52 % of days).
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "measure_settlement_page_metar_divergence",
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "measure_settlement_page_metar_divergence.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_wmo_half_up_is_the_settlement_law_including_negative_halves():
    assert mod.wmo_half_up(32.5) == 33.0
    assert mod.wmo_half_up(32.4) == 32.0
    # floor(x+0.5), so a negative half rounds toward zero, not away from it
    assert mod.wmo_half_up(-3.5) == -3.0
    assert mod.wmo_half_up(-3.6) == -4.0


def test_identical_feeds_are_faithful_with_a_floor_threshold():
    stats = mod.city_stats([(20.0, 20.0)] * (mod.EMPIRICAL_MIN_PAIRS + 20))
    assert stats["disagree_rate_ge_1unit"] == 0.0
    assert stats["empirical_threshold"] == 1.0
    assert stats["settlement_faithful"] is True
    assert stats["threshold_provenance"] == "empirical"
    assert stats["unfaithful_proven_at_95"] is False


def test_a_one_degree_disagreement_half_the_time_is_not_faithful():
    """The Denver case: same tenths-level feed, different settlement integer."""
    half = mod.EMPIRICAL_MIN_PAIRS
    matched = [(20.0, 20.0)] * half + [(21.0, 20.0)] * half
    stats = mod.city_stats(matched)
    assert stats["disagree_rate_ge_1unit"] == 0.5
    assert stats["p99_abs_rounded_delta"] == 1.0
    assert stats["empirical_threshold"] == 2.0
    assert stats["settlement_faithful"] is False


def test_rounding_happens_on_both_sides_before_the_delta():
    """A sub-degree difference that lands in the same settlement bin is NOT a divergence.

    This is the whole reason the measurement rounds first: the question is whether the two
    sources name the same settlement integer, not whether they agree to a tenth.
    """
    stats = mod.city_stats([(20.4, 20.1)] * (mod.EMPIRICAL_MIN_PAIRS + 20))
    assert stats["disagree_rate_ge_1unit"] == 0.0
    assert stats["median_abs_raw_delta"] > 0.0, "the raw difference is still recorded"


def test_thin_sample_is_not_executable_evidence():
    """A city cannot earn a margin from a handful of days, however clean they look.

    The bar is `EMPIRICAL_MIN_PAIRS`, derived from the sample size at which the threshold
    stops moving (see the constant's own derivation), so this test asks for one pair fewer
    than the bar rather than pinning a number that the derivation may revise.
    """
    stats = mod.city_stats([(20.0, 20.0)] * (mod.EMPIRICAL_MIN_PAIRS - 1))
    assert stats["threshold_provenance"] == "thin_sample"
    assert stats["settlement_faithful"] is True, "the verdict is computable..."
    # ...but the consumer refuses any non-empirical provenance, so nothing is served.


def test_revocation_is_possible_at_a_thin_sample_but_permission_is_not():
    """The asymmetry the page's one-value-per-day cadence forces on us.

    At 50 pairs a high disagreement rate is already proven above the 2 % ceiling, so the
    sample can REVOKE a city's METAR permission. The same 50 pairs showing zero disagreement
    prove nothing, so they cannot GRANT one.
    """
    thin = mod.EMPIRICAL_MIN_PAIRS - 1
    unfaithful = mod.city_stats(
        [(21.0, 20.0)] * (thin // 2 + 1) + [(20.0, 20.0)] * (thin - thin // 2 - 1)
    )
    assert unfaithful["threshold_provenance"] == "thin_sample"
    assert unfaithful["unfaithful_proven_at_95"] is True
    assert unfaithful["disagree_rate_wilson_lower_95"] > mod.FAITHFUL_RATE_MAX

    clean = mod.city_stats([(20.0, 20.0)] * thin)
    assert clean["unfaithful_proven_at_95"] is False
    assert clean["disagree_rate_wilson_lower_95"] == 0.0


def test_wilson_lower_bound_is_below_the_rate_and_zero_without_trials():
    assert mod.wilson_lower_bound(0, 0) == 0.0
    assert mod.wilson_lower_bound(0, 50) == 0.0
    lo = mod.wilson_lower_bound(26, 50)
    assert 0.0 < lo < 26 / 50
    # more evidence at the same rate tightens the bound toward it
    assert mod.wilson_lower_bound(260, 500) > lo


def test_empty_measurement_has_no_verdict_rather_than_a_false_one():
    stats = mod.city_stats([])
    assert stats["matched_pairs"] == 0
    assert stats["threshold_provenance"] == "no_data"
    assert stats["settlement_faithful"] is None
    assert stats["empirical_threshold"] is None
    assert stats["unfaithful_proven_at_95"] is None


def test_thresholds_match_the_wu_era_refitter():
    """One margin mechanism, one formula — the pair measured is all that changes."""
    wu = Path(__file__).resolve().parents[2] / "scripts" / "measure_wu_metar_divergence.py"
    text = wu.read_text(encoding="utf-8")
    assert "QUANTUM = 1.0" in text and mod.QUANTUM == 1.0
    assert "FLOOR = 1.0" in text and mod.FLOOR == 1.0
    assert "FAITHFUL_P99_MAX = 1.0" in text and mod.FAITHFUL_P99_MAX == 1.0
    assert "FAITHFUL_RATE_MAX = 0.02" in text and mod.FAITHFUL_RATE_MAX == 0.02


def test_output_schema_matches_what_the_consumer_reads():
    """day0_oracle_anomaly reads these exact keys; a rename silently disables the margin."""
    stats = mod.city_stats([(20.0, 20.0)] * (mod.EMPIRICAL_MIN_PAIRS + 20))
    for key in (
        "matched_pairs",
        "empirical_threshold",
        "threshold_provenance",
        "settlement_faithful",
        "disagree_rate_ge_1unit",
    ):
        assert key in stats, key
