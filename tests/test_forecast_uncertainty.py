from src.signal.ensemble_signal import sigma_instrument
from src.signal.forecast_uncertainty import (
    analysis_mean_context,
    analysis_mean_offset,
    analysis_sigma_context,
    day0_backbone_residual_adjustment,
    day0_backbone_high,
    day0_blended_highs,
    analysis_member_maxes,
    analysis_bootstrap_sigma,
    analysis_lead_sigma_multiplier,
    analysis_spread_sigma_multiplier,
    day0_observation_weight,
    day0_post_peak_sigma,
    day0_temporal_closure_weight,
)


def test_analysis_bootstrap_sigma_matches_current_instrument_sigma():
    assert analysis_bootstrap_sigma("F") == sigma_instrument("F").value
    assert analysis_bootstrap_sigma("C") == sigma_instrument("C").value
    assert analysis_bootstrap_sigma("F", lead_days=6.0, ensemble_spread=7.5) == sigma_instrument("F").value * 1.2 * 1.1


def test_day0_post_peak_sigma_matches_existing_formula_endpoints():
    base_f = sigma_instrument("F").value
    assert day0_post_peak_sigma("F", 0.0) == base_f
    assert day0_post_peak_sigma("F", 1.0) == base_f * 0.5


def test_day0_post_peak_sigma_clamps_peak_confidence():
    base_c = sigma_instrument("C").value
    assert day0_post_peak_sigma("C", -1.0) == base_c
    assert day0_post_peak_sigma("C", 2.0) == base_c * 0.5


def test_analysis_lead_sigma_multiplier_is_continuous_and_bounded():
    assert analysis_lead_sigma_multiplier(None) == 1.0
    assert analysis_lead_sigma_multiplier(0.0) == 1.0
    assert analysis_lead_sigma_multiplier(3.0) == 1.1
    assert analysis_lead_sigma_multiplier(6.0) == 1.2
    assert analysis_lead_sigma_multiplier(10.0) == 1.2


def test_analysis_spread_sigma_multiplier_is_bounded_and_monotone():
    assert analysis_spread_sigma_multiplier(None, unit="F") == 1.0
    assert analysis_spread_sigma_multiplier(0.0, unit="F") == 1.0
    assert analysis_spread_sigma_multiplier(1.0, unit="F") == 1.05
    assert analysis_spread_sigma_multiplier(3.0, unit="F") == 1.1


def test_analysis_member_maxes_is_identity_for_now():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(raw, unit="F", lead_days=5.0)
    assert list(adjusted) == raw


def test_analysis_mean_offset_is_zero_for_now():
    assert analysis_mean_offset(unit="F", lead_days=5.0, ensemble_mean=42.0) == 0.0


def test_analysis_mean_context_explains_offset():
    ctx = analysis_mean_context(unit="F", lead_days=5.0, ensemble_mean=42.0)
    assert ctx["unit"] == "F"
    assert ctx["lead_days"] == 5.0
    assert ctx["ensemble_mean"] == 42.0
    assert ctx["offset"] == 0.0


def test_analysis_sigma_context_explains_components():
    ctx = analysis_sigma_context(unit="F", lead_days=3.0, ensemble_spread=1.0)
    assert ctx["unit"] == "F"
    assert ctx["lead_days"] == 3.0
    assert ctx["ensemble_spread"] == 1.0
    assert ctx["base_sigma"] == sigma_instrument("F").value
    assert ctx["lead_multiplier"] == 1.1
    assert ctx["spread_multiplier"] == 1.05
    assert ctx["final_sigma"] == sigma_instrument("F").value * 1.1 * 1.05


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


def test_day0_backbone_high_is_observed_high_for_now():
    assert day0_backbone_high(
        observed_high=45.0,
        current_temp=43.0,
        daylight_progress=0.5,
        hours_remaining=4.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
    ) == 45.0


def test_day0_backbone_residual_adjustment_is_neutral_for_now():
    assert day0_backbone_residual_adjustment(
        observed_high=45.0,
        current_temp=43.0,
        daylight_progress=0.5,
        hours_remaining=4.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
    ) == 0.0
