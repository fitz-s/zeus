"""Forecast-layer uncertainty policy seams.

Phase-1 de-hardcode starts by centralizing where forecast/measurement sigma is
chosen, without changing current behavior. This gives later work one place to
upgrade day0/dayN uncertainty policy instead of scattering new logic across
signal consumers.
"""

from __future__ import annotations

from src.signal.ensemble_signal import sigma_instrument


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
):
    """Current Phase-0 residual blending policy, extracted behind a seam."""
    import numpy as np

    remaining = np.asarray(remaining_member_highs, dtype=float)
    residual_excess = np.maximum(0.0, remaining - float(observed_high))
    return float(observed_high) + residual_excess * (1.0 - float(observation_weight))


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
    _ = lead_days, ensemble_spread
    return sigma_instrument(unit).value


def day0_post_peak_sigma(unit: str, peak_confidence: float) -> float:
    """Current Phase-0 day0 sigma policy, extracted behind a forecast seam."""
    peak = min(1.0, max(0.0, float(peak_confidence)))
    base_sigma = sigma_instrument(unit).value
    return base_sigma * (1.0 - peak * 0.5)
