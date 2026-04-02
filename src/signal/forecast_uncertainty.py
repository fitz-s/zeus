"""Forecast-layer uncertainty policy seams.

Phase-1 de-hardcode starts by centralizing where forecast/measurement sigma is
chosen, without changing current behavior. This gives later work one place to
upgrade day0/dayN uncertainty policy instead of scattering new logic across
signal consumers.
"""

from __future__ import annotations

from src.signal.ensemble_signal import sigma_instrument


def analysis_bootstrap_sigma(unit: str) -> float:
    """Current baseline bootstrap sigma used by market-analysis paths."""
    return sigma_instrument(unit).value


def day0_post_peak_sigma(unit: str, peak_confidence: float) -> float:
    """Current Phase-0 day0 sigma policy, extracted behind a forecast seam."""
    peak = min(1.0, max(0.0, float(peak_confidence)))
    base_sigma = sigma_instrument(unit).value
    return base_sigma * (1.0 - peak * 0.5)
