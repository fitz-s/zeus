"""Empirical-Bayes style hierarchical Platt calibrator for Zeus.

This is a practical v2a upgrade:
- still uses the familiar Extended Platt feature space
- but shrinks small buckets toward parent/global parameters
- uses decision-group sample weights to avoid over-counting bin rows

It is intended as a realistic bridge between today's hard fallback and a future full Bayesian model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

try:
    from sklearn.linear_model import LogisticRegression
except Exception as exc:  # pragma: no cover - dependency can vary per environment
    LogisticRegression = None


EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1.0 - EPS)
    return np.log(p / (1.0 - p))


@dataclass(frozen=True)
class ExtendedPlattParams:
    slope: float
    lead_coef: float
    intercept: float
    effective_groups: int
    source_level: str  # local / parent / global / shrunk

    def predict(self, p_raw: np.ndarray, lead_days: np.ndarray) -> np.ndarray:
        z = self.slope * _logit(np.asarray(p_raw)) + self.lead_coef * np.asarray(lead_days) + self.intercept
        return 1.0 / (1.0 + np.exp(-z))


def fit_extended_platt(
    df: pd.DataFrame,
    *,
    raw_col: str = "p_raw",
    lead_col: str = "lead_days",
    y_col: str = "outcome",
    group_col: str = "group_id",
    l2_strength: float = 1.0,
) -> ExtendedPlattParams:
    """Fit a local Extended Platt model using group-aware row weights."""
    if LogisticRegression is None:
        raise RuntimeError("scikit-learn is required for fit_extended_platt().")

    required = {raw_col, lead_col, y_col, group_col}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    work = df.copy()
    work = work[(work[raw_col].notna()) & (work[lead_col].notna()) & (work[y_col].notna())]
    if work.empty:
        raise ValueError("No valid rows after filtering null inputs.")

    # Each decision group should contribute total weight ~1.0, not 11.0.
    group_sizes = work.groupby(group_col)[group_col].transform("size").clip(lower=1)
    sample_weight = 1.0 / group_sizes

    X = np.column_stack([_logit(work[raw_col].to_numpy(dtype=float)), work[lead_col].to_numpy(dtype=float)])
    y = work[y_col].to_numpy(dtype=int)

    # C is inverse regularization strength in sklearn.
    clf = LogisticRegression(
        penalty="l2",
        C=1.0 / max(l2_strength, 1e-6),
        fit_intercept=True,
        solver="lbfgs",
        max_iter=2000,
    )
    clf.fit(X, y, sample_weight=sample_weight)

    n_groups = int(work[group_col].nunique())
    return ExtendedPlattParams(
        slope=float(clf.coef_[0, 0]),
        lead_coef=float(clf.coef_[0, 1]),
        intercept=float(clf.intercept_[0]),
        effective_groups=n_groups,
        source_level="local",
    )


def shrink_params(
    local: ExtendedPlattParams,
    parent: ExtendedPlattParams,
    *,
    tau_groups: float = 50.0,
    min_local_groups: int = 1,
) -> ExtendedPlattParams:
    """Empirical-Bayes style shrinkage toward parent params.

    lambda = n_eff / (n_eff + tau)
    """
    n_eff = max(local.effective_groups, min_local_groups)
    lam = n_eff / (n_eff + tau_groups)

    return ExtendedPlattParams(
        slope=lam * local.slope + (1.0 - lam) * parent.slope,
        lead_coef=lam * local.lead_coef + (1.0 - lam) * parent.lead_coef,
        intercept=lam * local.intercept + (1.0 - lam) * parent.intercept,
        effective_groups=local.effective_groups,
        source_level="shrunk",
    )


def fit_hierarchy(
    df: pd.DataFrame,
    *,
    bucket_col: str = "bucket_key",
    parent_col: str = "parent_bucket_key",
    raw_col: str = "p_raw",
    lead_col: str = "lead_days",
    y_col: str = "outcome",
    group_col: str = "group_id",
    tau_groups: float = 50.0,
) -> dict[str, ExtendedPlattParams]:
    """Fit a two-level hierarchy: local bucket -> parent bucket -> global."""
    if df.empty:
        return {}

    models: dict[str, ExtendedPlattParams] = {}

    global_params = fit_extended_platt(
        df,
        raw_col=raw_col,
        lead_col=lead_col,
        y_col=y_col,
        group_col=group_col,
        l2_strength=5.0,
    )
    global_params = ExtendedPlattParams(
        global_params.slope,
        global_params.lead_coef,
        global_params.intercept,
        global_params.effective_groups,
        source_level="global",
    )

    parent_models: dict[str, ExtendedPlattParams] = {}
    if parent_col in df.columns:
        for parent_key, parent_df in df.groupby(parent_col):
            if pd.isna(parent_key):
                continue
            try:
                parent_models[str(parent_key)] = fit_extended_platt(
                    parent_df,
                    raw_col=raw_col,
                    lead_col=lead_col,
                    y_col=y_col,
                    group_col=group_col,
                    l2_strength=3.0,
                )
            except ValueError:
                parent_models[str(parent_key)] = global_params

    for bucket_key, bucket_df in df.groupby(bucket_col):
        try:
            local = fit_extended_platt(
                bucket_df,
                raw_col=raw_col,
                lead_col=lead_col,
                y_col=y_col,
                group_col=group_col,
                l2_strength=2.0,
            )
        except ValueError:
            local = global_params

        if parent_col in bucket_df.columns:
            parent_key = str(bucket_df[parent_col].iloc[0]) if bucket_df[parent_col].notna().any() else None
            parent = parent_models.get(parent_key, global_params)
        else:
            parent = global_params

        models[str(bucket_key)] = shrink_params(local, parent, tau_groups=tau_groups)

    models["__global__"] = global_params
    return models


def predict_bucket_vector(
    params: Mapping[str, ExtendedPlattParams],
    bucket_key: str,
    p_raw: Iterable[float],
    lead_days: Iterable[float],
) -> np.ndarray:
    model = params.get(bucket_key, params["__global__"])
    return model.predict(np.asarray(list(p_raw), dtype=float), np.asarray(list(lead_days), dtype=float))
