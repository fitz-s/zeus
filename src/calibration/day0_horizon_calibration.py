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

fit_day0_horizon_platt() uses scipy.optimize.minimize (L-BFGS-B) to fit the
logistic regression model via log-loss minimization.  sklearn is not a
declared dependency in requirements.txt so we use scipy which is already
imported by the signal stack.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit


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
                Clipped to [1e-7, 1-1e-7] to avoid infinite logit.
            hours_remaining: continuous horizon covariate (0 <= x <= 6).
            daypart: one of 'pre_sunrise', 'morning', 'afternoon', 'post_peak'.
            temperature_metric_indicator: 0=low, 1=high.

        Returns:
            logit(P_nowcast) — caller applies expit() to recover P_nowcast.
        """
        p_clipped = float(np.clip(p_now_raw, 1e-7, 1.0 - 1e-7))
        logit_raw = float(logit(p_clipped))
        gamma = (
            self.gamma_morning * (daypart == "morning")
            + self.gamma_afternoon * (daypart == "afternoon")
            + self.gamma_post_peak * (daypart == "post_peak")
        )
        return (
            self.alpha * logit_raw
            + self.beta * float(hours_remaining)
            + gamma
            + self.delta * float(temperature_metric_indicator)
            + self.epsilon
        )

    def predict_proba(
        self,
        p_now_raw: float,
        hours_remaining: float,
        daypart: str,
        temperature_metric_indicator: float,
    ) -> float:
        """Return P_nowcast in [0, 1]. Convenience wrapper over predict_logit."""
        return float(expit(self.predict_logit(
            p_now_raw, hours_remaining, daypart, temperature_metric_indicator,
        )))


_VALID_DAYPARTS = frozenset({"pre_sunrise", "morning", "afternoon", "post_peak"})


def fit_day0_horizon_platt(
    observations: list,
    outcomes: list,
    *,
    fit_date: str = "",
    sample_period_start: str = "",
    sample_period_end: str = "",
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
        fit_date: ISO date string for the fit record (optional, defaults to today).
        sample_period_start: earliest training sample date (optional).
        sample_period_end: latest training sample date (optional).

    Returns:
        HorizonPlattFit with fitted (α, β, γ_morning, γ_afternoon, γ_post_peak, δ, ε)
        and fit_run_id (uuid4) + fit_version ("hpf_v1").

    Raises:
        ValueError: if observations and outcomes have different lengths or contain
            invalid daypart values.
    """
    if len(observations) != len(outcomes):
        raise ValueError(
            f"observations and outcomes must have the same length, "
            f"got {len(observations)} and {len(outcomes)}"
        )
    if not observations:
        raise ValueError("observations must be non-empty")

    # Validate and extract features
    n = len(observations)
    logit_raw = np.zeros(n)
    hrs = np.zeros(n)
    is_morning = np.zeros(n)
    is_afternoon = np.zeros(n)
    is_post_peak = np.zeros(n)
    metric_ind = np.zeros(n)

    for i, obs in enumerate(observations):
        dp = obs["daypart"]
        if dp not in _VALID_DAYPARTS:
            raise ValueError(f"Invalid daypart {dp!r} at index {i}; expected one of {sorted(_VALID_DAYPARTS)}")
        p_raw = float(np.clip(obs["p_now_raw"], 1e-7, 1.0 - 1e-7))
        logit_raw[i] = float(logit(p_raw))
        hrs[i] = float(obs["hours_remaining"])
        is_morning[i] = 1.0 if dp == "morning" else 0.0
        is_afternoon[i] = 1.0 if dp == "afternoon" else 0.0
        is_post_peak[i] = 1.0 if dp == "post_peak" else 0.0
        metric_ind[i] = float(obs["temperature_metric_indicator"])

    y = np.asarray(outcomes, dtype=np.float64)

    # Design matrix: [logit_raw, hrs, is_morning, is_afternoon, is_post_peak, metric_ind, 1]
    X = np.column_stack([logit_raw, hrs, is_morning, is_afternoon, is_post_peak, metric_ind, np.ones(n)])

    def neg_log_loss(w: np.ndarray) -> float:
        logit_pred = X @ w
        p = expit(logit_pred)
        p_clip = np.clip(p, 1e-12, 1.0 - 1e-12)
        return -float(np.mean(y * np.log(p_clip) + (1.0 - y) * np.log(1.0 - p_clip)))

    def grad(w: np.ndarray) -> np.ndarray:
        p = expit(X @ w)
        return -(X.T @ (y - p)) / n

    w0 = np.zeros(X.shape[1])
    result = minimize(neg_log_loss, w0, jac=grad, method="L-BFGS-B")
    if not result.success or not np.all(np.isfinite(result.x)):
        raise ValueError(
            f"fit_day0_horizon_platt: L-BFGS-B failed to converge. "
            f"message={result.message!r}, success={result.success}, "
            f"fun={result.fun:.6g}, x_finite={np.all(np.isfinite(result.x))}"
        )
    alpha, beta, g_morning, g_afternoon, g_post_peak, delta, epsilon = result.x

    if not fit_date:
        from datetime import date as _date
        fit_date = _date.today().isoformat()

    return HorizonPlattFit(
        alpha=float(alpha),
        beta=float(beta),
        gamma_morning=float(g_morning),
        gamma_afternoon=float(g_afternoon),
        gamma_post_peak=float(g_post_peak),
        delta=float(delta),
        epsilon=float(epsilon),
        fit_version="hpf_v1",
        fit_run_id=str(uuid.uuid4()),
        fit_date=fit_date,
        n_obs=n,
        sample_period_start=sample_period_start,
        sample_period_end=sample_period_end,
    )
