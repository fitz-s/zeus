from src.signal.ensemble_signal import sigma_instrument
from src.signal.forecast_uncertainty import (
    analysis_mean_context,
    analysis_mean_offset,
    analysis_sigma_context,
    analysis_spread_context,
    day0_backbone_context,
    day0_backbone_residual_adjustment,
    day0_backbone_high,
    day0_blended_highs,
    day0_nowcast_context,
    day0_nowcast_blend_weight,
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
    """Test sigma formula with quantization noise floor enforcement."""
    from src.signal.forecast_uncertainty import QUANTIZATION_NOISE_FLOOR_F
    base_f = sigma_instrument("F").value
    # At peak=0.0, raw_sigma = base_f = 0.5, which is above floor
    assert day0_post_peak_sigma("F", 0.0) == base_f
    # At peak=1.0, raw_sigma = base_f * 0.5 = 0.25, but floor is 0.35
    assert day0_post_peak_sigma("F", 1.0) == QUANTIZATION_NOISE_FLOOR_F


def test_day0_post_peak_sigma_clamps_peak_confidence():
    """Test peak confidence clamping with floor enforcement."""
    from src.signal.forecast_uncertainty import QUANTIZATION_NOISE_FLOOR_C
    base_c = sigma_instrument("C").value
    # At peak=-1.0 (clamped to 0), raw_sigma = base_c, above floor
    assert day0_post_peak_sigma("C", -1.0) == base_c
    # At peak=2.0 (clamped to 1.0), raw_sigma = 0.14, but floor is 0.20
    assert day0_post_peak_sigma("C", 2.0) == QUANTIZATION_NOISE_FLOOR_C


def test_day0_post_peak_sigma_expands_with_stale_data():
    """MATH-005: Sigma expands when data is stale (low freshness_factor)."""
    from src.signal.forecast_uncertainty import QUANTIZATION_NOISE_FLOOR_F
    base_f = sigma_instrument("F").value
    peak = 0.5  # Mid-confidence level

    # Fresh data: no expansion, raw_sigma = 0.5 * 0.75 = 0.375, above floor
    fresh_sigma = day0_post_peak_sigma("F", peak, freshness_factor=1.0)
    assert fresh_sigma == base_f * 0.75  # base * (1 - 0.5*0.5) * 1.0

    # Stale data (3h+): 1.5x expansion, raw_sigma = 0.5625, above floor
    stale_sigma = day0_post_peak_sigma("F", peak, freshness_factor=0.0)
    assert stale_sigma == base_f * 0.75 * 1.5  # 50% expansion

    # Verify expansion ratio
    assert stale_sigma / fresh_sigma == 1.5


def test_day0_post_peak_sigma_freshness_is_bounded():
    """MATH-005: Freshness factor is clamped to [0, 1]."""
    base_f = sigma_instrument("F").value

    # Freshness > 1 should be clamped to 1
    assert day0_post_peak_sigma("F", 0.0, freshness_factor=2.0) == base_f

    # Freshness < 0 should be clamped to 0 (maximum expansion)
    assert day0_post_peak_sigma("F", 0.0, freshness_factor=-1.0) == base_f * 1.5


def test_day0_post_peak_sigma_freshness_profile():
    """MATH-005: Verify freshness expansion profile is linear."""
    base_f = sigma_instrument("F").value
    peak = 0.0  # No peak shrinkage to isolate freshness effect

    # Profile: staleness_expansion = 1.0 + (1.0 - fresh) * 0.5
    test_cases = [
        (1.0, 1.0),    # fresh=1.0 → expansion=1.0
        (0.8, 1.1),    # fresh=0.8 → expansion=1.1
        (0.5, 1.25),   # fresh=0.5 → expansion=1.25
        (0.2, 1.4),    # fresh=0.2 → expansion=1.4
        (0.0, 1.5),    # fresh=0.0 → expansion=1.5
    ]

    for freshness, expected_expansion in test_cases:
        sigma = day0_post_peak_sigma("F", peak, freshness_factor=freshness)
        expected_sigma = base_f * expected_expansion
        assert abs(sigma - expected_sigma) < 0.001, (
            f"freshness={freshness}: got {sigma}, expected {expected_sigma}"
        )


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


def test_analysis_spread_context_explains_multiplier():
    ctx = analysis_spread_context(1.0, unit="F")
    assert ctx["base_sigma"] == sigma_instrument("F").value
    assert ctx["reference_spread"] == sigma_instrument("F").value * 4.0
    assert ctx["spread_ratio"] == 0.5
    assert ctx["spread_multiplier"] == 1.05


def test_analysis_member_maxes_is_identity_without_bias_reference():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(raw, unit="F", lead_days=5.0)
    assert list(adjusted) == raw


def test_analysis_mean_offset_is_zero_for_now():
    assert analysis_mean_offset(unit="F", lead_days=5.0, ensemble_mean=42.0) == 0.0


def test_analysis_member_maxes_applies_bounded_bias_offset_when_uncorrected():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(
        raw,
        unit="F",
        lead_days=5.0,
        bias_corrected=False,
        bias_reference={"source": "ecmwf", "bias": 1.5},
    )
    expected_offset = -0.875
    assert list(adjusted) == __import__("pytest").approx([v + expected_offset for v in raw])


def test_analysis_member_maxes_respects_bias_corrected_guard():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(
        raw,
        unit="F",
        lead_days=5.0,
        bias_corrected=True,
        bias_reference={"source": "ecmwf", "bias": 4.0, "discount_factor": 0.7},
    )
    assert list(adjusted) == raw


def test_analysis_member_maxes_suppresses_offset_for_thin_bias_reference():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(
        raw,
        unit="F",
        lead_days=5.0,
        bias_corrected=False,
        bias_reference={"source": "ecmwf", "bias": 4.0, "discount_factor": 0.7, "n_samples": 12},
    )
    assert list(adjusted) == raw


def test_analysis_mean_context_explains_offset():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=5.0,
        ensemble_mean=42.0,
        city_name="NYC",
        season="MAM",
        forecast_source="ecmwf_ifs025",
        bias_corrected=False,
        bias_reference={"source": "ecmwf", "bias": 1.5},
    )
    assert ctx["unit"] == "F"
    assert ctx["lead_days"] == 5.0
    assert ctx["ensemble_mean"] == 42.0
    assert ctx["lead_factor"] == 5.0 / 6.0
    assert ctx["offset"] == __import__("pytest").approx(-0.875)
    assert ctx["city_name"] == "NYC"
    assert ctx["season"] == "MAM"
    assert ctx["forecast_source"] == "ecmwf_ifs025"
    assert ctx["bias_corrected"] is False
    assert ctx["bias_reference"]["bias"] == 1.5


def test_analysis_mean_context_respects_bias_corrected_guard():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=5.0,
        ensemble_mean=42.0,
        city_name="NYC",
        season="MAM",
        forecast_source="ecmwf_ifs025",
        bias_corrected=True,
        bias_reference={"source": "ecmwf", "bias": 4.0, "discount_factor": 0.7},
    )
    assert ctx["raw_offset"] == 0.0
    assert ctx["offset"] == 0.0


def test_analysis_mean_context_caps_large_bias_offset():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=6.0,
        ensemble_mean=42.0,
        city_name="NYC",
        season="MAM",
        forecast_source="ecmwf_ifs025",
        bias_corrected=False,
        bias_reference={"source": "ecmwf", "bias": 10.0, "discount_factor": 0.7},
    )
    assert ctx["raw_offset"] == -7.0
    assert ctx["max_abs_offset"] == sigma_instrument("F").value * 2.0
    assert ctx["offset"] == -(sigma_instrument("F").value * 2.0)


def test_analysis_mean_context_suppresses_offset_below_sample_floor():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=5.0,
        ensemble_mean=42.0,
        city_name="NYC",
        season="MAM",
        forecast_source="ecmwf_ifs025",
        bias_corrected=False,
        bias_reference={"source": "ecmwf", "bias": 4.0, "discount_factor": 0.7, "n_samples": 12},
    )
    assert ctx["n_samples"] == 12
    assert ctx["sample_factor"] == 0.0
    assert ctx["raw_offset"] == 0.0
    assert ctx["offset"] == 0.0


def test_analysis_mean_context_attenuates_offset_for_high_mae():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=6.0,
        ensemble_mean=42.0,
        city_name="NYC",
        season="MAM",
        forecast_source="ecmwf_ifs025",
        bias_corrected=False,
        bias_reference={
            "source": "ecmwf",
            "bias": 2.0,
            "discount_factor": 0.7,
            "n_samples": 30,
            "mae": 1.5,
        },
    )
    assert ctx["sample_factor"] == 1.0
    assert ctx["mae"] == 1.5
    assert 0.0 < ctx["mae_factor"] < 1.0
    assert ctx["raw_offset"] == __import__("pytest").approx(-0.4666666666666667)
    assert ctx["offset"] == ctx["raw_offset"]


def test_analysis_mean_context_suppresses_offset_for_extreme_mae():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=6.0,
        ensemble_mean=42.0,
        city_name="NYC",
        season="MAM",
        forecast_source="ecmwf_ifs025",
        bias_corrected=False,
        bias_reference={
            "source": "ecmwf",
            "bias": 2.0,
            "discount_factor": 0.7,
            "n_samples": 30,
            "mae": sigma_instrument("F").value * 4.0,
        },
    )
    assert ctx["mae_factor"] == 0.0
    assert ctx["raw_offset"] == 0.0
    assert ctx["offset"] == 0.0


def test_analysis_member_maxes_reflects_mae_attenuation():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(
        raw,
        unit="F",
        lead_days=6.0,
        bias_corrected=False,
        bias_reference={
            "source": "ecmwf",
            "bias": 2.0,
            "discount_factor": 0.7,
            "n_samples": 30,
            "mae": 1.5,
        },
    )
    expected_offset = -0.4666666666666667
    assert list(adjusted) == __import__("pytest").approx([v + expected_offset for v in raw])


def test_analysis_mean_context_normalizes_non_finite_bias_provenance():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=6.0,
        ensemble_mean=42.0,
        bias_corrected=False,
        bias_reference={
            "source": "ecmwf",
            "bias": float("nan"),
            "discount_factor": float("inf"),
            "n_samples": -1,
            "mae": float("nan"),
        },
    )
    assert ctx["bias_reference"] == {"source": "ecmwf"}
    assert ctx["n_samples"] is None
    assert ctx["mae"] is None
    assert ctx["sample_factor"] == 1.0
    assert ctx["mae_factor"] == 1.0
    assert ctx["raw_offset"] == 0.0
    assert ctx["offset"] == 0.0


def test_analysis_mean_context_treats_negative_mae_as_missing():
    ctx = analysis_mean_context(
        unit="F",
        lead_days=6.0,
        ensemble_mean=42.0,
        bias_corrected=False,
        bias_reference={
            "source": "ecmwf",
            "bias": 2.0,
            "discount_factor": 0.7,
            "n_samples": 30,
            "mae": -1.0,
        },
    )
    assert ctx["mae"] is None
    assert ctx["mae_factor"] == 1.0
    assert ctx["raw_offset"] == __import__("pytest").approx(-1.4)


def test_analysis_member_maxes_ignore_invalid_bias_provenance():
    raw = [40.0, 42.0, 41.5]
    adjusted = analysis_member_maxes(
        raw,
        unit="F",
        lead_days=6.0,
        bias_corrected=False,
        bias_reference={
            "source": "ecmwf",
            "bias": float("nan"),
            "discount_factor": 0.7,
            "n_samples": 30,
            "mae": 1.0,
        },
    )
    assert list(adjusted) == raw


def test_analysis_sigma_context_explains_components():
    ctx = analysis_sigma_context(unit="F", lead_days=3.0, ensemble_spread=1.0, city_name="NYC", season="MAM", forecast_source="ecmwf_ifs025")
    assert ctx["unit"] == "F"
    assert ctx["city_name"] == "NYC"
    assert ctx["season"] == "MAM"
    assert ctx["forecast_source"] == "ecmwf_ifs025"
    assert ctx["lead_days"] == 3.0
    assert ctx["ensemble_spread"] == 1.0
    assert ctx["base_sigma"] == sigma_instrument("F").value
    assert ctx["lead_multiplier"] == 1.1
    assert ctx["spread_multiplier"] == 1.05
    assert ctx["reference_spread"] == sigma_instrument("F").value * 4.0
    assert ctx["spread_ratio"] == 0.5
    assert ctx["final_sigma"] == sigma_instrument("F").value * 1.1 * 1.05


def test_day0_temporal_closure_weight_matches_existing_endpoints():
    assert day0_temporal_closure_weight(
        hours_remaining=12.0,
        peak_confidence=0.0,
        daylight_progress=None,
        obs_exceeds_ens_fraction=0.0,
    ) == 0.0
    assert day0_temporal_closure_weight(
        hours_remaining=0.0,
        peak_confidence=1.0,
        daylight_progress=1.0,
        obs_exceeds_ens_fraction=1.0,
    ) == 1.0


def test_day0_temporal_closure_weight_uses_strongest_signal_instead_of_correlated_product():
    weight = day0_temporal_closure_weight(
        hours_remaining=6.0,
        peak_confidence=0.4,
        daylight_progress=0.5,
        obs_exceeds_ens_fraction=0.5,
    )
    assert weight == 0.5


def test_day0_observation_weight_preserves_pre_sunrise_and_post_sunset_behavior():
    assert day0_observation_weight(
        hours_remaining=10.0,
        peak_confidence=0.1,
        daylight_progress=0.0,
        obs_exceeds_ens_fraction=0.2,
        pre_sunrise=True,
        post_sunset=False,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T00:30:00+00:00",
    ) <= 0.05
    assert day0_observation_weight(
        hours_remaining=3.0,
        peak_confidence=0.8,
        daylight_progress=1.0,
        obs_exceeds_ens_fraction=0.9,
        pre_sunrise=False,
        post_sunset=True,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T00:30:00+00:00",
    ) == 1.0


def test_day0_observation_weight_does_not_force_full_finality_for_stale_post_sunset_observation():
    base = day0_temporal_closure_weight(
        hours_remaining=3.0,
        peak_confidence=0.8,
        daylight_progress=1.0,
        obs_exceeds_ens_fraction=0.9,
    )
    weight = day0_observation_weight(
        hours_remaining=3.0,
        peak_confidence=0.8,
        daylight_progress=1.0,
        obs_exceeds_ens_fraction=0.9,
        pre_sunrise=False,
        post_sunset=True,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T04:00:00+00:00",
    )
    assert weight == base
    assert weight < 1.0


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
        unit="F",
        observed_high=45.0,
        current_temp=43.0,
        daylight_progress=0.5,
        hours_remaining=4.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    ) == 45.0


def test_day0_backbone_residual_adjustment_is_neutral_for_now():
    assert day0_backbone_residual_adjustment(
        unit="F",
        observed_high=45.0,
        current_temp=43.0,
        daylight_progress=0.5,
        hours_remaining=4.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    ) == 0.0


def test_day0_backbone_residual_adjustment_is_small_positive_when_temp_is_near_high_before_peak():
    adj = day0_backbone_residual_adjustment(
        unit="F",
        observed_high=45.0,
        current_temp=44.8,
        daylight_progress=0.5,
        hours_remaining=4.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    )
    assert 0.0 < adj < 0.25


def test_day0_backbone_context_exposes_components():
    ctx = day0_backbone_context(
        unit="F",
        observed_high=45.0,
        current_temp=44.8,
        daylight_progress=0.5,
        hours_remaining=4.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    )
    assert ctx["unit"] == "F"
    assert ctx["observation_source"] == "wu_api"
    assert ctx["nowcast"]["observation_source"] == "wu_api"
    assert 0.0 < ctx["residual_adjustment"] < 0.25
    assert ctx["backbone_high"] == 45.0 + ctx["residual_adjustment"]


def test_day0_nowcast_blend_weight_requires_source_and_time():
    assert day0_nowcast_blend_weight(hours_remaining=2.0, observation_source="", observation_time=None, current_utc_timestamp=None) == 0.0
    assert day0_nowcast_blend_weight(hours_remaining=2.0, observation_source="wu_api", observation_time=None, current_utc_timestamp="2026-04-02T01:00:00+00:00") == 0.0


def test_day0_nowcast_blend_weight_increases_as_horizon_shortens():
    early = day0_nowcast_blend_weight(
        hours_remaining=6.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T00:00:00+00:00",
    )
    late = day0_nowcast_blend_weight(
        hours_remaining=1.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    )
    assert early == 0.0
    assert 0.0 < late <= 0.25


def test_day0_nowcast_blend_weight_decays_with_stale_observation():
    fresh = day0_nowcast_blend_weight(
        hours_remaining=1.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T00:30:00+00:00",
    )
    stale = day0_nowcast_blend_weight(
        hours_remaining=1.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T04:00:00+00:00",
    )
    assert fresh > stale
    assert stale == 0.0


def test_day0_nowcast_context_exposes_age_and_freshness():
    ctx = day0_nowcast_context(
        hours_remaining=1.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    )
    assert ctx["observation_source"] == "wu_api"
    assert ctx["age_hours"] == 1.0
    assert 0.0 < ctx["freshness_factor"] < 1.0
    assert ctx["trusted_source"] is True
    assert ctx["fresh_observation"] is True
    assert ctx["blend_weight"] == day0_nowcast_blend_weight(
        hours_remaining=1.0,
        observation_source="wu_api",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T01:00:00+00:00",
    )


def test_day0_nowcast_context_zeroes_untrusted_source_weight():
    ctx = day0_nowcast_context(
        hours_remaining=1.0,
        observation_source="personal_weather_station",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T00:30:00+00:00",
    )
    assert ctx["trusted_source"] is False
    assert ctx["source_factor"] == 0.0
    assert ctx["blend_weight"] == 0.0


def test_day0_nowcast_context_keeps_trusted_source_positive_weight():
    ctx = day0_nowcast_context(
        hours_remaining=1.0,
        observation_source="asos_station",
        observation_time="2026-04-02T00:00:00+00:00",
        current_utc_timestamp="2026-04-02T00:30:00+00:00",
    )
    assert ctx["trusted_source"] is True
    assert ctx["source_factor"] == 1.0
    assert ctx["blend_weight"] > 0.0
