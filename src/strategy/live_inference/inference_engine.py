"""Event-bound live inference for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass

from src.strategy.live_inference.state import LiveBinState


@dataclass(frozen=True)
class InferenceInputs:
    prior: tuple[float, ...]
    day0_mask: tuple[float, ...] | None = None
    forecast_likelihoods: tuple[float, ...] | None = None
    forecast_complete: bool = True
    orderbook_event: bool = False


def evaluate_live_bins(inputs: InferenceInputs) -> LiveBinState:
    if not inputs.forecast_complete:
        raise ValueError("partial forecast cannot enter live inference")
    values = list(inputs.prior)
    if inputs.forecast_likelihoods is not None:
        if len(inputs.forecast_likelihoods) != len(values):
            raise ValueError("forecast likelihood length mismatch")
        values = [p * l for p, l in zip(values, inputs.forecast_likelihoods)]
    if inputs.day0_mask is not None:
        if len(inputs.day0_mask) != len(values):
            raise ValueError("Day0 mask length mismatch")
        values = [p * k for p, k in zip(values, inputs.day0_mask)]
    # Public orderbook evidence is deliberately ignored for q_live in v1.
    return LiveBinState(tuple(values)).normalized()
