from src.signal.ensemble_signal import sigma_instrument
from src.signal.forecast_uncertainty import (
    day0_blended_highs,
    analysis_bootstrap_sigma,
    day0_observation_weight,
    day0_post_peak_sigma,
    day0_temporal_closure_weight,
)


def test_analysis_bootstrap_sigma_matches_current_instrument_sigma():
    assert analysis_bootstrap_sigma("F") == sigma_instrument("F").value
    assert analysis_bootstrap_sigma("C") == sigma_instrument("C").value
    assert (
        analysis_bootstrap_sigma("F", lead_days=5.0, ensemble_spread=7.5)
        == sigma_instrument("F").value
    )


def test_day0_post_peak_sigma_matches_existing_formula_endpoints():
    base_f = sigma_instrument("F").value
    assert day0_post_peak_sigma("F", 0.0) == base_f
    assert day0_post_peak_sigma("F", 1.0) == base_f * 0.5


def test_day0_post_peak_sigma_clamps_peak_confidence():
    base_c = sigma_instrument("C").value
    assert day0_post_peak_sigma("C", -1.0) == base_c
    assert day0_post_peak_sigma("C", 2.0) == base_c * 0.5


def test_day0_temporal_closure_weight_matches_existing_endpoints():
    assert day0_temporal_closure_weight(
        hours_remaining=12.0,
        peak_confidence=0.0,
        daylight_progress=None,
        ens_dominance=0.0,
    ) == 0.0
    assert day0_temporal_closure_weight(
        hours_remaining=0.0,
        peak_confidence=1.0,
        daylight_progress=1.0,
        ens_dominance=1.0,
    ) == 1.0


def test_day0_observation_weight_preserves_pre_sunrise_and_post_sunset_behavior():
    assert day0_observation_weight(
        hours_remaining=10.0,
        peak_confidence=0.1,
        daylight_progress=0.0,
        ens_dominance=0.2,
        pre_sunrise=True,
        post_sunset=False,
    ) <= 0.05
    assert day0_observation_weight(
        hours_remaining=3.0,
        peak_confidence=0.8,
        daylight_progress=1.0,
        ens_dominance=0.9,
        pre_sunrise=False,
        post_sunset=True,
    ) == 1.0


def test_day0_blended_highs_preserves_hard_floor_and_weight_endpoints():
    highs = day0_blended_highs(
        observed_high=45.0,
        remaining_member_highs=[44.0, 46.0, 48.0],
        observation_weight=0.0,
    )
    assert list(highs) == [45.0, 46.0, 48.0]

    dominated = day0_blended_highs(
        observed_high=45.0,
        remaining_member_highs=[44.0, 46.0, 48.0],
        observation_weight=1.0,
    )
    assert list(dominated) == [45.0, 45.0, 45.0]
