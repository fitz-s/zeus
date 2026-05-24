"""Markov smoothing for EDLI live bin inference."""

from __future__ import annotations

from collections.abc import Mapping

from src.strategy.live_inference.state import LiveBinState, normalize_probabilities


def apply_markov_transition(
    state: LiveBinState,
    transition: Mapping[str, Mapping[str, float]],
) -> LiveBinState:
    """Apply a declared transition matrix without learning from PnL."""

    next_prob = {key: 0.0 for key in state.probabilities}
    for from_key, prior_prob in state.probabilities.items():
        row = transition.get(from_key, {})
        for to_key, weight in row.items():
            if to_key in next_prob:
                next_prob[to_key] += prior_prob * float(weight)
    return LiveBinState(normalize_probabilities(next_prob), state.as_of)
