# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5
"""Day0Nowcast — unified nowcast contract for HIGH and LOW Day0 markets.

Subsumes Day0LowNowcastSignal (refactored to thin shim in production pass).
Adds symmetric HIGH-side nowcast lane.

Horizon applicability: only valid when market.max_hours_to_resolution <= 6.
Raises NotApplicableHorizon otherwise — live mode MUST NOT relabel forecast
output as nowcast (fail-closed per INV-nowcast-horizon-bound).

Math (single horizon-aware Platt fit — NOT 6 separate fits):
    logit(P_nowcast) = α·logit(P_now_raw)
                     + β·hours_to_close
                     + γ·daypart_dummy
                     + δ·temperature_metric_indicator
                     + ε

Fusion when calibrated forecast is available:
    P_fused = w · P_nowcast + (1-w) · P_cal
    w = sigmoid(-(hours_to_close - 3))

day0_nowcast_context() from forecast_uncertainty is PRESERVED and called internally
to compute blend_weight and source freshness metadata. Production pass fills evaluate().
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from src.signal.forecast_uncertainty import day0_nowcast_context

if TYPE_CHECKING:
    pass


class NotApplicableHorizon(ValueError):
    """Raised by Day0Nowcast.evaluate() when market.max_hours_to_resolution > 6.

    Fail-closed guard: markets with >6h horizon must be served by the standard
    calibrated forecast pipeline, not the nowcast model. Callers must handle
    this exception and fall back to the calibrated forecast path.
    """


def _sigmoid(x: float) -> float:
    """Standard logistic sigmoid. Used for fusion weight computation."""
    return 1.0 / (1.0 + math.exp(-x))


class Day0Nowcast:
    """Unified Day0 nowcast model for HIGH and LOW temperature markets.

    Instantiate with temperature_metric='high' or 'low'. The same unified Platt
    model (HorizonPlattFit) runs for both metrics — the metric is a covariate (δ),
    not a routing dimension.

    Typical caller flow (production pass):
        nowcast = Day0Nowcast(temperature_metric='low', model=platt_fit)
        try:
            result = nowcast.evaluate(observation, daypart, market, p_cal=p_cal)
        except NotApplicableHorizon:
            # market too far out — use standard calibrated forecast
            result = calibrated_only

    Args:
        temperature_metric: 'high' or 'low' — which Day0 temperature target.
        model: HorizonPlattFit artifact from day0_horizon_calibration.fit_day0_horizon_platt().
               Optional at SCAFFOLD time (model=None raises NotImplementedError on evaluate).
    """

    def __init__(
        self,
        temperature_metric: Literal["high", "low"],
        model=None,
    ) -> None:
        if temperature_metric not in ("high", "low"):
            raise ValueError(
                f"temperature_metric must be 'high' or 'low', got {temperature_metric!r}"
            )
        self.temperature_metric = temperature_metric
        self._model = model

    def evaluate(
        self,
        observation,
        daypart: str,
        market,
        *,
        p_cal: float | None = None,
    ) -> dict:
        """Compute P_nowcast and P_fused for the given market + observation.

        Raises NotApplicableHorizon if market.max_hours_to_resolution > 6.

        Args:
            observation: observation object with .value, .source, .observation_time fields.
            daypart: e.g. 'morning', 'afternoon', 'evening' — categorical covariate.
            market: market object with .max_hours_to_resolution (float) and .market_slug.
            p_cal: calibrated forecast probability from standard pipeline.
                   If None, p_fused = p_nowcast (nowcast-only mode).

        Returns dict with keys:
            p_nowcast    : float — calibrated nowcast probability
            p_fused      : float — fusion result (w·P_nowcast + (1-w)·P_cal)
            blend_weight : float — w = sigmoid(-(hours_to_close - 3))
            nowcast_ctx  : dict  — output from day0_nowcast_context() helper
            temperature_metric : str — 'high' or 'low'

        Production pass implementation:
            1. horizon guard (raise NotApplicableHorizon if > 6h)
            2. call day0_nowcast_context() for blend_weight + freshness
            3. compute P_now_raw from empirical climatology
            4. apply HorizonPlattFit to get P_nowcast
            5. compute fusion weight w and P_fused
            6. return result dict for storage in day0_nowcast_runs
        """
        raise NotImplementedError(
            "Day0Nowcast.evaluate() is a SCAFFOLD stub — production pass required. "
            "First action in production: horizon guard (raise NotApplicableHorizon "
            "if market.max_hours_to_resolution > 6)."
        )

    def _fusion_weight(self, hours_to_close: float) -> float:
        """Sigmoid-centered blend weight: w = sigmoid(-(hours_to_close - 3)).

        w → 1 (full nowcast) as hours_to_close → 0
        w → 0 (full calibrated forecast) as hours_to_close → 6+
        """
        return _sigmoid(-(hours_to_close - 3.0))

    def _nowcast_context(
        self,
        *,
        hours_remaining: float,
        observation_source: str,
        observation_time: str | None,
        current_utc_timestamp: str | None,
    ) -> dict:
        """Delegate to preserved day0_nowcast_context() helper from forecast_uncertainty."""
        return day0_nowcast_context(
            hours_remaining=hours_remaining,
            observation_source=observation_source,
            observation_time=observation_time,
            current_utc_timestamp=current_utc_timestamp,
        )
