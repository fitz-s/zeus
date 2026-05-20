# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5.3 (Option B pivot)
"""Day0 horizon-aware Platt calibration — single fit with continuous horizon covariate.

Single fit across all horizons (NOT 6 separate horizon-bucket fits).
hours_remaining is a continuous covariate (β coefficient), preserving data density
and avoiding bin-boundary artifacts.

Model (one-hot daypart encoding — pre_sunrise is reference category):
    logit(P_nowcast) = α·logit(P_now_raw)
                     + β·hours_remaining
                     + γ_morning·is_morning
                     + γ_afternoon·is_afternoon
                     + γ_post_peak·is_post_peak
                     + δ·temperature_metric_indicator
                     + ε

daypart: from Day0ObservationContext.daypart (src/contracts/day0_observation_context.py:133)
    4 values: pre_sunrise (reference, no coefficient), morning, afternoon, post_peak.
    One-hot encoding avoids false ordinal assumption.

temperature_metric_indicator: 0=low, 1=high (single cross-metric fit)

Coefficients stored in day0_horizon_platt_fits table (forecasts DB, one row per fit run).
day0_nowcast_runs.fit_run_id FK references that table — avoids repeating coefficients
per nowcast row (coefficients change rarely; storing per-run = waste).
fit_version (semantic, e.g., "hpf_v1") is stable across re-runs of the same algorithm.

Training data source: calibration_pairs_v2 (forecasts DB), filtered to Day0 rows
with valid hours_remaining <= 6 and known daypart.

Production pass implements fit_day0_horizon_platt() using sklearn or scipy.optimize.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HorizonPlattFit:
    """Fitted coefficients for the horizon-aware Day0 Platt model.

    Stored in day0_horizon_platt_fits (forecasts DB) — one row per fit execution.
    day0_nowcast_runs references via fit_run_id FK.

    Model (one-hot daypart — pre_sunrise is reference category, no coefficient):
        logit(P_nowcast) = α·logit(P_now_raw)
                         + β·hours_remaining
                         + γ_morning·is_morning
                         + γ_afternoon·is_afternoon
                         + γ_post_peak·is_post_peak
                         + δ·temperature_metric_indicator
                         + ε

    Attributes:
        alpha: coefficient on logit(P_now_raw).
        beta:  coefficient on hours_remaining. Negative: more confident as horizon shrinks.
        gamma_morning: coefficient on is_morning (1 if morning, else 0).
        gamma_afternoon: coefficient on is_afternoon.
        gamma_post_peak: coefficient on is_post_peak.
        delta: coefficient on temperature_metric_indicator (0=low, 1=high).
        epsilon: intercept.
        fit_version: semantic version tag e.g. "hpf_v1". Stable across re-runs of
            the same algorithm. Stored in day0_horizon_platt_fits for cross-version
            validation queries.
        fit_run_id: per-execution UUID (uuid4). PK in day0_horizon_platt_fits.
            Referenced as FK by day0_nowcast_runs.fit_run_id.
        fit_date: ISO date string when produced.
        n_obs: number of (observation, outcome) pairs used.
        sample_period_start: ISO date — earliest training sample.
        sample_period_end: ISO date — latest training sample.
        extra: additional provenance (fit_source, etc.).
    """

    alpha: float
    beta: float
    gamma_morning: float
    gamma_afternoon: float
    gamma_post_peak: float
    delta: float
    epsilon: float

    fit_version: str = "hpf_v1"
    fit_run_id: str = ""
    fit_date: str = ""
    n_obs: int = 0
    sample_period_start: str = ""
    sample_period_end: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def predict_logit(
        self,
        p_now_raw: float,
        hours_remaining: float,
        daypart: str,
        temperature_metric_indicator: float,
    ) -> float:
        """Apply fitted model: logit(P_nowcast) linear combination.

        Args:
            p_now_raw: empirical climatology probability (raw, pre-calibration).
            hours_remaining: continuous horizon covariate (0 <= x <= 6).
            daypart: one of 'pre_sunrise', 'morning', 'afternoon', 'post_peak'.
            temperature_metric_indicator: 0=low, 1=high.

        Production pass uses scipy.special.logit for the input transform,
        then scipy.special.expit to recover P_nowcast from logit output.

        Raises NotImplementedError: SCAFFOLD stub.
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

    Training data source: calibration_pairs_v2 (forecasts DB), filtered to
    Day0 rows with hours_remaining <= 6 and known daypart.

    Args:
        observations: sequence of dicts, each with:
            - p_now_raw (float): empirical climatology probability
            - hours_remaining (float): continuous horizon covariate (<= 6)
            - daypart (str): one of 'pre_sunrise', 'morning', 'afternoon', 'post_peak'
            - temperature_metric_indicator (float): 0=low, 1=high
        outcomes: sequence of binary outcomes (1=resolved YES, 0=NO),
                  same length and order as observations.

    Returns:
        HorizonPlattFit with fitted (α, β, γ_morning, γ_afternoon, γ_post_peak, δ, ε)
        and fit_run_id (uuid4) + fit_version ("hpf_v1").

    Raises:
        ValueError: if observations and outcomes have different lengths.
        NotImplementedError: SCAFFOLD stub — production pass required.
    """
    if len(observations) != len(outcomes):
        raise ValueError(
            f"observations and outcomes must have the same length, "
            f"got {len(observations)} and {len(outcomes)}"
        )
    raise NotImplementedError(
        "fit_day0_horizon_platt() is a SCAFFOLD stub — production pass required."
    )
