"""Bayesian factor helpers for EDLI live bin inference."""

from __future__ import annotations

import math
from collections.abc import Mapping

from src.strategy.live_inference.state import LiveBinState, LiveInferenceBlocked, normalize_probabilities


def apply_capped_llr(
    state: LiveBinState,
    llr_by_bin: Mapping[str, float],
    *,
    llr_cap: float,
) -> LiveBinState:
    if llr_cap <= 0:
        raise ValueError("llr_cap must be positive")
    weighted = {}
    for key, prior in state.probabilities.items():
        llr = max(-llr_cap, min(llr_cap, float(llr_by_bin.get(key, 0.0))))
        weighted[key] = prior * math.exp(llr)
    return LiveBinState(normalize_probabilities(weighted), state.as_of)


def apply_market_prior_if_validated(
    state: LiveBinState,
    prior_by_bin: Mapping[str, float],
    *,
    validated_for_live: bool,
    weight: float = 1.0,
) -> LiveBinState:
    if not validated_for_live:
        raise LiveInferenceBlocked("market prior is not validated for live")
    if weight < 0:
        raise ValueError("weight must be non-negative")
    weighted = {
        key: state.probabilities.get(key, 0.0) * (max(0.0, float(prior_by_bin.get(key, 0.0))) ** weight)
        for key in state.probabilities
    }
    return LiveBinState(normalize_probabilities(weighted), state.as_of)


def assert_forecast_complete_for_live(completeness_status: str) -> None:
    if completeness_status != "COMPLETE":
        raise LiveInferenceBlocked("partial forecast is evidence/no-trade, not live inference")
