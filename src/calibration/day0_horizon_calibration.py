# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5.3
"""Day0 horizon-aware Platt calibration — single fit with continuous horizon covariate.

Single fit across all horizons (NOT 6 separate horizon-bucket fits).
hours_to_close is a continuous covariate (β coefficient), preserving data density
and avoiding bin-boundary artifacts.

Model:
    logit(P_nowcast) = α·logit(P_now_raw)
                     + β·hours_to_close
                     + γ·daypart_dummy
                     + δ·temperature_metric_indicator
                     + ε

Production pass implements fit_day0_horizon_platt() using sklearn or scipy.optimize
on historical nowcast observations keyed by (market_slug, observation_time, daypart).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HorizonPlattFit:
    """Fitted coefficients for the horizon-aware Day0 Platt model.

    Coefficients correspond to the model:
        logit(P_nowcast) = α·logit(P_now_raw)
                         + β·hours_to_close
                         + γ·daypart_dummy
                         + δ·temperature_metric_indicator
                         + ε

    Attributes:
        alpha: coefficient on logit(P_now_raw) — how much the raw empirical
               climatology contributes to the calibrated output.
        beta:  coefficient on hours_to_close — horizon sensitivity.
               Negative beta → nowcast more confident as horizon shrinks.
        gamma: coefficient on daypart_dummy (morning=0, afternoon=1, evening=2).
        delta: coefficient on temperature_metric_indicator (low=0, high=1).
        epsilon: intercept term.
        fit_date: ISO date string when this fit was produced.
        n_obs: number of (observation, outcome) pairs used in fit.
        sample_period_start: ISO date string — earliest training sample.
        sample_period_end: ISO date string — latest training sample.
        extra: additional provenance metadata (version tag, fit_source, etc.).
    """

    alpha: float
    beta: float
    gamma: float
    delta: float
    epsilon: float

    fit_date: str = ""
    n_obs: int = 0
    sample_period_start: str = ""
    sample_period_end: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def predict_logit(
        self,
        p_now_raw: float,
        hours_to_close: float,
        daypart_dummy: float,
        temperature_metric_indicator: float,
    ) -> float:
        """Apply the fitted model to get logit(P_nowcast).

        Production pass calls scipy.special.logit / expit for the transforms.
        Stub: raises NotImplementedError (coefficients only valid once fit).
        """
        raise NotImplementedError(
            "HorizonPlattFit.predict_logit() is a SCAFFOLD stub — "
            "production pass fills the logit transform + linear combination."
        )


def fit_day0_horizon_platt(
    observations: list,
    outcomes: list,
) -> HorizonPlattFit:
    """Fit the horizon-aware Platt model to historical Day0 nowcast data.

    Args:
        observations: sequence of observation records, each containing:
            - p_now_raw (float): empirical climatology probability
            - hours_to_close (float): continuous horizon covariate
            - daypart_dummy (float): 0=morning, 1=afternoon, 2=evening
            - temperature_metric_indicator (float): 0=low, 1=high
        outcomes: sequence of binary outcomes (1=resolved YES, 0=NO),
                  same length and order as observations.

    Returns:
        HorizonPlattFit with fitted (α, β, γ, δ, ε).

    Production pass:
        Uses logistic regression (e.g. sklearn.linear_model.LogisticRegression
        or scipy.optimize.minimize on binary cross-entropy) on the 4-covariate
        design matrix. Single fit across all metrics and horizons.

    Raises:
        NotImplementedError: SCAFFOLD stub — production pass required.
        ValueError: if observations and outcomes have different lengths.
    """
    if len(observations) != len(outcomes):
        raise ValueError(
            f"observations and outcomes must have the same length, "
            f"got {len(observations)} and {len(outcomes)}"
        )
    raise NotImplementedError(
        "fit_day0_horizon_platt() is a SCAFFOLD stub — production pass required."
    )
