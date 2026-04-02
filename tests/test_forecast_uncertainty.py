from src.signal.ensemble_signal import sigma_instrument
from src.signal.forecast_uncertainty import (
    analysis_bootstrap_sigma,
    day0_post_peak_sigma,
)


def test_analysis_bootstrap_sigma_matches_current_instrument_sigma():
    assert analysis_bootstrap_sigma("F") == sigma_instrument("F").value
    assert analysis_bootstrap_sigma("C") == sigma_instrument("C").value


def test_day0_post_peak_sigma_matches_existing_formula_endpoints():
    base_f = sigma_instrument("F").value
    assert day0_post_peak_sigma("F", 0.0) == base_f
    assert day0_post_peak_sigma("F", 1.0) == base_f * 0.5


def test_day0_post_peak_sigma_clamps_peak_confidence():
    base_c = sigma_instrument("C").value
    assert day0_post_peak_sigma("C", -1.0) == base_c
    assert day0_post_peak_sigma("C", 2.0) == base_c * 0.5
