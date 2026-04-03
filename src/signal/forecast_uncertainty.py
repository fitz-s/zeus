"""Forecast-layer uncertainty policy seams.

Phase-1 de-hardcode starts by centralizing where forecast/measurement sigma is
chosen, without changing current behavior. This gives later work one place to
upgrade day0/dayN uncertainty policy instead of scattering new logic across
signal consumers.
"""

from __future__ import annotations

import math
import numpy as np

from src.signal.ensemble_signal import sigma_instrument


def _finite_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _normalized_bias_reference(bias_reference: dict | None) -> dict:
    raw = dict(bias_reference or {})
    normalized: dict = {}

    source = raw.get("source")
    if source:
        normalized["source"] = str(source)

    bias = _finite_float(raw.get("bias"))
    if bias is not None:
        normalized["bias"] = bias

    mae = _finite_float(raw.get("mae"))
    if mae is not None and mae >= 0.0:
        normalized["mae"] = mae

    discount = _finite_float(raw.get("discount_factor"))
    if discount is not None and discount >= 0.0:
        normalized["discount_factor"] = discount

    try:
        n_samples = int(raw.get("n_samples"))
    except (TypeError, ValueError):
        n_samples = None
    if n_samples is not None and n_samples >= 0:
        normalized["n_samples"] = n_samples

    return normalized


def analysis_member_maxes(
    member_maxes,
    *,
    unit: str,
    lead_days: float | None = None,
    bias_corrected: bool | None = None,
    bias_reference: dict | None = None,
):
    """Phase-1 seam for future lead-continuous mean/location correction.

    This slice is intentionally behavior-preserving: it carries the member-max
    surface through a named forecast-layer boundary without changing values yet.
    """
    values = np.asarray(member_maxes, dtype=float)
    offset = analysis_mean_offset(
        unit=unit,
        lead_days=lead_days,
        ensemble_mean=float(values.mean()) if values.size else None,
        bias_corrected=bias_corrected,
        bias_reference=bias_reference,
    )
    return values + offset


def analysis_sigma_context(
    *,
    unit: str,
    lead_days: float | None,
    ensemble_spread: float | None,
    city_name: str | None = None,
    season: str | None = None,
    forecast_source: str | None = None,
) -> dict:
    """Explain how the current analysis sigma was constructed."""
    spread_context = analysis_spread_context(ensemble_spread, unit=unit)
    base_sigma = spread_context["base_sigma"]
    lead_multiplier = analysis_lead_sigma_multiplier(lead_days)
    spread_multiplier = spread_context["spread_multiplier"]
    return {
        "unit": unit,
        "city_name": city_name,
        "season": season,
        "forecast_source": forecast_source,
        "lead_days": lead_days,
        "ensemble_spread": ensemble_spread,
        "base_sigma": base_sigma,
        "lead_multiplier": lead_multiplier,
        "spread_multiplier": spread_multiplier,
        "reference_spread": spread_context["reference_spread"],
        "spread_ratio": spread_context["spread_ratio"],
        "final_sigma": base_sigma * lead_multiplier * spread_multiplier,
    }


def analysis_mean_offset(
    *,
    unit: str,
    lead_days: float | None = None,
    ensemble_mean: float | None = None,
    bias_corrected: bool | None = None,
    bias_reference: dict | None = None,
) -> float:
    return analysis_mean_context(
        unit=unit,
        lead_days=lead_days,
        ensemble_mean=ensemble_mean,
        bias_corrected=bias_corrected,
        bias_reference=bias_reference,
    )["offset"]


def analysis_mean_context(
    *,
    unit: str,
    lead_days: float | None = None,
    ensemble_mean: float | None = None,
    city_name: str | None = None,
    season: str | None = None,
    forecast_source: str | None = None,
    bias_corrected: bool | None = None,
    bias_reference: dict | None = None,
) -> dict:
    """Phase-1 seam for future lead-continuous mean/location correction.

    Current behavior is identity/no-op; the seam exists so later forecast-layer
    work can land mean correction without rewriting consumers again.
    """
    base_sigma = sigma_instrument(unit).value
    lead = 0.0 if lead_days is None else min(6.0, max(0.0, float(lead_days)))
    lead_factor = lead / 6.0
    bias_reference = _normalized_bias_reference(bias_reference)
    raw_offset = 0.0
    sample_factor = 1.0
    n_samples = None
    mae = None
    mae_factor = 1.0
    if "n_samples" in bias_reference and bias_reference.get("n_samples") is not None:
        n_samples = int(bias_reference.get("n_samples"))
        if n_samples < 20:
            sample_factor = 0.0
    if "mae" in bias_reference and bias_reference.get("mae") is not None:
        mae = float(bias_reference.get("mae"))
        if mae > 0 and base_sigma > 0:
            if mae <= base_sigma:
                mae_factor = 1.0
            elif mae >= base_sigma * 4.0:
                mae_factor = 0.0
            else:
                mae_factor = 1.0 - ((mae - base_sigma) / (base_sigma * 3.0))
    if not bias_corrected and bias_reference:
        bias = float(bias_reference.get("bias", 0.0))
        discount = float(bias_reference.get("discount_factor", 0.7))
        raw_offset = -bias * discount * lead_factor * sample_factor * mae_factor
    max_abs_offset = base_sigma * 2.0
    offset = max(-max_abs_offset, min(max_abs_offset, raw_offset))
    return {
        "unit": unit,
        "city_name": city_name,
        "season": season,
        "forecast_source": forecast_source,
        "bias_corrected": bias_corrected,
        "bias_reference": bias_reference or {},
        "n_samples": n_samples,
        "sample_factor": sample_factor,
        "mae": mae,
        "mae_factor": mae_factor,
        "lead_days": lead_days,
        "lead_factor": lead_factor,
        "ensemble_mean": ensemble_mean,
        "raw_offset": raw_offset,
        "max_abs_offset": max_abs_offset,
        "offset": offset,
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


def analysis_spread_context(
    ensemble_spread: float | None,
    *,
    unit: str,
) -> dict:
    base_sigma = sigma_instrument(unit).value
    reference_spread = base_sigma * 4.0
    spread = None if ensemble_spread is None else max(0.0, float(ensemble_spread))
    ratio = (
        0.0
        if spread is None or reference_spread <= 0
        else min(1.0, spread / reference_spread)
    )
    return {
        "base_sigma": base_sigma,
        "reference_spread": reference_spread,
        "ensemble_spread": ensemble_spread,
        "spread_ratio": ratio,
        "spread_multiplier": 1.0 + 0.1 * ratio if spread is not None else 1.0,
    }


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
    return analysis_spread_context(ensemble_spread, unit=unit)["spread_multiplier"]


def day0_temporal_closure_weight(
    *,
    hours_remaining: float,
    peak_confidence: float,
    daylight_progress: float | None,
    ens_dominance: float,
) -> float:
    """Bounded day0 closure policy driven by the strongest active signal."""
    time_closure = min(1.0, max(0.0, 1.0 - float(hours_remaining) / 12.0))
    peak_signal = min(1.0, max(0.0, float(peak_confidence)))
    daylight_signal = (
        min(1.0, max(0.0, float(daylight_progress)))
        if daylight_progress is not None
        else time_closure
    )
    ens_signal = min(1.0, max(0.0, float(ens_dominance)))
    return max(
        time_closure,
        0.75 * peak_signal,
        0.50 * daylight_signal,
        0.35 * ens_signal,
    )


def day0_observation_weight(
    *,
    hours_remaining: float,
    peak_confidence: float,
    daylight_progress: float | None,
    ens_dominance: float,
    pre_sunrise: bool,
    post_sunset: bool,
    observation_source: str = "",
    observation_time: str | None = None,
    current_utc_timestamp: str | None = None,
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
    nowcast = day0_nowcast_context(
        hours_remaining=hours_remaining,
        observation_source=observation_source,
        observation_time=observation_time,
        current_utc_timestamp=current_utc_timestamp,
    )
    finality_ready = nowcast["trusted_source"] and nowcast["fresh_observation"]
    if post_sunset:
        return 1.0 if finality_ready else base
    if daylight_progress is None:
        return base
    if daylight_progress <= 0.0:
        return min(base, 0.05)
    if daylight_progress >= 1.0:
        return 1.0 if finality_ready else base
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
    current_utc_timestamp: str | None,
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
        current_utc_timestamp=current_utc_timestamp,
    )


def day0_backbone_context(
    *,
    unit: str,
    observed_high: float,
    current_temp: float,
    daylight_progress: float | None,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
    current_utc_timestamp: str | None,
) -> dict:
    nowcast_context = day0_nowcast_context(
        hours_remaining=hours_remaining,
        observation_source=observation_source,
        observation_time=observation_time,
        current_utc_timestamp=current_utc_timestamp,
    )
    residual_adjustment = day0_backbone_residual_adjustment(
        unit=unit,
        observed_high=observed_high,
        current_temp=current_temp,
        daylight_progress=daylight_progress,
        hours_remaining=hours_remaining,
        observation_source=observation_source,
        observation_time=observation_time,
        current_utc_timestamp=current_utc_timestamp,
    )
    return {
        "unit": unit,
        "observed_high": observed_high,
        "current_temp": current_temp,
        "daylight_progress": daylight_progress,
        "hours_remaining": hours_remaining,
        "observation_source": observation_source,
        "observation_time": observation_time,
        "current_utc_timestamp": current_utc_timestamp,
        "nowcast": nowcast_context,
        "nowcast_blend_weight": nowcast_context["blend_weight"],
        "residual_adjustment": residual_adjustment,
        "backbone_high": float(observed_high) + residual_adjustment,
    }


def day0_backbone_residual_adjustment(
    *,
    unit: str,
    observed_high: float,
    current_temp: float,
    daylight_progress: float | None,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
    current_utc_timestamp: str | None,
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
        current_utc_timestamp=current_utc_timestamp,
    )
    max_adjustment = base_sigma * 0.5
    adjustment = max_adjustment * proximity * solar_factor * remaining_factor * nowcast_neutrality
    return max(0.0, float(adjustment))


def day0_nowcast_blend_weight(
    *,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
    current_utc_timestamp: str | None,
) -> float:
    """Phase-1 seam for future very-short-lead nowcast/NWP blending.

    Current behavior is neutral; later work can turn this into a learned or
    rule-based blend without reopening the day0 call sites.
    """
    return day0_nowcast_context(
        hours_remaining=hours_remaining,
        observation_source=observation_source,
        observation_time=observation_time,
        current_utc_timestamp=current_utc_timestamp,
    )["blend_weight"]


def day0_nowcast_context(
    *,
    hours_remaining: float,
    observation_source: str,
    observation_time: str | None,
    current_utc_timestamp: str | None,
) -> dict:
    from datetime import datetime, timezone

    source = str(observation_source or "")
    source_lower = source.lower()
    trusted = any(tag in source_lower for tag in ("wu", "asos", "obs"))
    source_factor = 1.0 if trusted else 0.0
    hours = min(6.0, max(0.0, float(hours_remaining)))
    short_lead_progress = 1.0 - (hours / 6.0)
    age_hours = None
    freshness_factor = 0.0

    if observation_time and current_utc_timestamp:
        try:
            observed_at = datetime.fromisoformat(str(observation_time).replace("Z", "+00:00"))
            current_at = datetime.fromisoformat(str(current_utc_timestamp).replace("Z", "+00:00"))
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            if current_at.tzinfo is None:
                current_at = current_at.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (current_at - observed_at).total_seconds() / 3600.0)
            freshness_factor = max(0.0, 1.0 - min(1.0, age_hours / 3.0))
        except ValueError:
            age_hours = None
            freshness_factor = 0.0
    trusted_source = source_factor >= 1.0
    fresh_observation = age_hours is not None and age_hours <= 1.0 and freshness_factor > 0.0

    return {
        "hours_remaining": float(hours_remaining),
        "observation_source": source,
        "observation_time": observation_time,
        "current_utc_timestamp": current_utc_timestamp,
        "source_factor": source_factor,
        "short_lead_progress": short_lead_progress,
        "age_hours": age_hours,
        "freshness_factor": freshness_factor,
        "trusted_source": trusted_source,
        "fresh_observation": fresh_observation,
        "blend_weight": 0.25 * short_lead_progress * source_factor * freshness_factor,
    }


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
