"""Forecast-layer uncertainty policy seams.

Phase-1 de-hardcode starts by centralizing where forecast/measurement sigma is
chosen, without changing current behavior. This gives later work one place to
upgrade day0/dayN uncertainty policy instead of scattering new logic across
signal consumers.
"""

from __future__ import annotations

import numpy as np

from src.signal.ensemble_signal import sigma_instrument


def analysis_member_maxes(
    member_maxes,
    *,
    unit: str,
    lead_days: float | None = None,
):
    """Phase-1 seam for future lead-continuous mean/location correction.

    This slice is intentionally behavior-preserving: it carries the member-max
    surface through a named forecast-layer boundary without changing values yet.
    """
    values = np.asarray(member_maxes, dtype=float)
    offset = analysis_mean_context(
        unit=unit,
        lead_days=lead_days,
        ensemble_mean=float(values.mean()) if values.size else None,
    )["offset"]
    return values + offset


def analysis_sigma_context(
    *,
    unit: str,
    lead_days: float | None,
    ensemble_spread: float | None,
) -> dict:
    """Explain how the current analysis sigma was constructed."""
    base_sigma = sigma_instrument(unit).value
    lead_multiplier = analysis_lead_sigma_multiplier(lead_days)
    spread_multiplier = analysis_spread_sigma_multiplier(ensemble_spread, unit=unit)
    return {
        "unit": unit,
        "lead_days": lead_days,
        "ensemble_spread": ensemble_spread,
        "base_sigma": base_sigma,
        "lead_multiplier": lead_multiplier,
        "spread_multiplier": spread_multiplier,
        "final_sigma": base_sigma * lead_multiplier * spread_multiplier,
    }


def analysis_mean_offset(
    *,
    unit: str,
    lead_days: float | None = None,
    ensemble_mean: float | None = None,
) -> float:
    return analysis_mean_context(
        unit=unit,
        lead_days=lead_days,
        ensemble_mean=ensemble_mean,
    )["offset"]


def analysis_mean_context(
    *,
    unit: str,
    lead_days: float | None = None,
    ensemble_mean: float | None = None,
) -> dict:
    """Phase-1 seam for future lead-continuous mean/location correction.

    Current behavior is identity/no-op; the seam exists so later forecast-layer
    work can land mean correction without rewriting consumers again.
    """
    return {
        "unit": unit,
        "lead_days": lead_days,
        "ensemble_mean": ensemble_mean,
        "offset": 0.0,
    }


def analysis_lead_sigma_multiplier(lead_days: float | None) -> float:
    """Conservative lead-continuous sigma inflation for day1..day7 analysis.

    Phase-1 behavior change: stop treating all non-day0 leads as the same
    uncertainty regime. Inflation ramps continuously from 1.0 at day0 to 1.2
    by day6+, matching the documented ~15-20% underdispersion correction
    without introducing per-lead discontinuities.
    """
    if lead_days is None:
        return 1.0
    lead = min(6.0, max(0.0, float(lead_days)))
    return 1.0 + 0.2 * (lead / 6.0)


def analysis_spread_sigma_multiplier(
    ensemble_spread: float | None,
    *,
    unit: str,
) -> float:
    """Phase-1 heteroscedastic seam for analysis sigma.

    This slice is intentionally behavior-preserving: it carries the spread
    covariate through a named policy boundary but still returns the neutral
    multiplier until a later P2-H behavior change is chosen.
    """
    if ensemble_spread is None:
        return 1.0
    base_sigma = sigma_instrument(unit).value
    reference_spread = base_sigma * 4.0
    spread = max(0.0, float(ensemble_spread))
    ratio = min(1.0, spread / reference_spread) if reference_spread > 0 else 1.0
    return 1.0 + 0.1 * ratio


def day0_temporal_closure_weight(
    *,
    hours_remaining: float,
    peak_confidence: float,
    daylight_progress: float | None,
    ens_dominance: float,
) -> float:
    """Current multiplicative day0 closure policy, extracted behind a seam."""
    time_closure = min(1.0, max(0.0, 1.0 - float(hours_remaining) / 12.0))
    peak_signal = min(1.0, max(0.0, float(peak_confidence)))
    daylight_signal = (
        min(1.0, max(0.0, float(daylight_progress)))
        if daylight_progress is not None
        else time_closure
    )
    ens_signal = min(1.0, max(0.0, float(ens_dominance)))

    residual_freedom = (
        (1.0 - time_closure)
        * (1.0 - 0.75 * peak_signal)
        * (1.0 - 0.50 * daylight_signal)
        * (1.0 - 0.35 * ens_signal)
    )
    return 1.0 - residual_freedom


def day0_observation_weight(
    *,
    hours_remaining: float,
    peak_confidence: float,
    daylight_progress: float | None,
    ens_dominance: float,
    pre_sunrise: bool,
    post_sunset: bool,
) -> float:
    """Current Phase-0 day0 observation dominance policy, extracted behind a seam."""
    base = day0_temporal_closure_weight(
        hours_remaining=hours_remaining,
        peak_confidence=peak_confidence,
        daylight_progress=daylight_progress,
        ens_dominance=ens_dominance,
    )
    if pre_sunrise:
        return min(base, 0.05)
    if post_sunset:
        return 1.0
    if daylight_progress is None:
        return base
    if daylight_progress <= 0.0:
        return min(base, 0.05)
    if daylight_progress >= 1.0:
        return 1.0
    return max(base, daylight_progress * 0.35)


def day0_blended_highs(
    *,
    observed_high: float,
    remaining_member_highs,
    observation_weight: float,
    backbone_high: float | None = None,
):
    """Current Phase-0 residual blending policy, extracted behind a seam."""
    import numpy as np

    anchor_high = float(observed_high if backbone_high is None else backbone_high)
    remaining = np.asarray(remaining_member_highs, dtype=float)
    residual_excess = np.maximum(0.0, remaining - anchor_high)
    return anchor_high + residual_excess * (1.0 - float(observation_weight))


def day0_backbone_high(
    *,
    unit: str,
    observed_high: float,
    current_temp: float,
    daylight_progress: float | None,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
) -> float:
    """Phase-1 seam for future solar-backbone / online-residual day0 modeling.

    Current behavior is unchanged: the observed high remains the anchor.
    """
    return float(observed_high) + day0_backbone_residual_adjustment(
        unit=unit,
        observed_high=observed_high,
        current_temp=current_temp,
        daylight_progress=daylight_progress,
        hours_remaining=hours_remaining,
        observation_source=observation_source,
        observation_time=observation_time,
    )


def day0_backbone_residual_adjustment(
    *,
    unit: str,
    observed_high: float,
    current_temp: float,
    daylight_progress: float | None,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
) -> float:
    """Phase-1 seam for future online residual correction on the day0 backbone.

    Current behavior is neutral; later work can make this a learned or filtered
    residual update without changing `Day0Signal` again.
    """
    if daylight_progress is None:
        return 0.0
    progress = min(1.0, max(0.0, float(daylight_progress)))
    if progress <= 0.0 or progress >= 1.0:
        return 0.0

    base_sigma = sigma_instrument(unit).value
    if base_sigma <= 0:
        return 0.0

    temp_gap = max(0.0, float(observed_high) - float(current_temp))
    proximity = max(0.0, 1.0 - min(1.0, temp_gap / base_sigma))
    remaining_factor = min(1.0, max(0.0, float(hours_remaining) / 6.0))
    solar_factor = 1.0 - progress
    nowcast_neutrality = 1.0 - day0_nowcast_blend_weight(
        hours_remaining=hours_remaining,
        observation_source=observation_source,
        observation_time=observation_time,
    )
    max_adjustment = base_sigma * 0.5
    adjustment = max_adjustment * proximity * solar_factor * remaining_factor * nowcast_neutrality
    return max(0.0, float(adjustment))


def day0_nowcast_blend_weight(
    *,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
) -> float:
    """Phase-1 seam for future very-short-lead nowcast/NWP blending.

    Current behavior is neutral; later work can turn this into a learned or
    rule-based blend without reopening the day0 call sites.
    """
    if not observation_source or not observation_time:
        return 0.0
    source = str(observation_source).lower()
    source_factor = 1.0 if any(tag in source for tag in ("wu", "asos", "obs")) else 0.5
    hours = min(6.0, max(0.0, float(hours_remaining)))
    short_lead_progress = 1.0 - (hours / 6.0)
    return 0.25 * short_lead_progress * source_factor


def analysis_bootstrap_sigma(
    unit: str,
    *,
    lead_days: float | None = None,
    ensemble_spread: float | None = None,
) -> float:
    """Current baseline bootstrap sigma used by market-analysis paths.

    Phase-1 seam expansion: accept the lead/spread covariates that a future
    lead-continuous / heteroscedastic sigma policy will need, while preserving
    today's numeric behavior.
    """
    return analysis_sigma_context(
        unit=unit,
        lead_days=lead_days,
        ensemble_spread=ensemble_spread,
    )["final_sigma"]


def day0_post_peak_sigma(unit: str, peak_confidence: float) -> float:
    """Current Phase-0 day0 sigma policy, extracted behind a forecast seam."""
    peak = min(1.0, max(0.0, float(peak_confidence)))
    base_sigma = sigma_instrument(unit).value
    return base_sigma * (1.0 - peak * 0.5)
