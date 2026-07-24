# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator delta-package v2 (zeus_delta_only_upgrade_package_v2/
#   zeus_day0_low_physical_law_patch.diff) — Day0 LOW physical settlement preimage.
#   Mirror of the already-correct HIGH law (settle(max(H_obs, future_member_max))).
"""Day0 LOW physical distribution builder.

Physical law (LOW), for a target local day D at decision time tau:
    L_D            = settle(min_{t in D} T(t))                 -- the realized daily minimum
    L_obs(tau)     = min_{t <= tau, t in D} T(t)               -- observed low so far (a CEILING)
    L_future,j(tau)= min_{t > tau,  t in D} T_j(t)             -- model/member j's remaining min
    Y_j            = settle(min(L_obs(tau), L_future,j(tau)))  -- one settlement sample

The observed low is an UPPER BOUND on the day's minimum only. ``current_temp`` must NEVER enter
the settlement-value path of an extreme statistic — a daily minimum is a path extremum, not a
convex blend of the current temperature with the remaining forecast. ``current_temp`` may be
telemetry / uncertainty context elsewhere, never a daily-min sample.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import numpy as np

from src.contracts.settlement_semantics import apply_settlement_rounding

if TYPE_CHECKING:
    from src.types import Bin

PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION = "pre_day0_low_current_temp_residual_v2"
DEFAULT_PRE_DAY0_LOW_EMPIRICAL_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "state" / "pre_day0_low_carryover_empirical.json"
)
_MODEL_SENTINEL = object()


@dataclass(frozen=True)
class PreDay0LowEmpiricalConditioning:
    """LOW conditioning from an empirical T-1-night -> Day0-early residual model.

    ``conditioned_member_mins`` is the full sample space:
    min(forecast_member_low, late_window_low + empirical_residual_quantile).
    The residuals are fitted from historical hourly current-temperature
    observations with holdout evidence, so no arbitrary blend weight is needed.
    """

    conditioned_member_mins: np.ndarray
    residual_quantiles: np.ndarray
    lead_bucket_hours: int
    lead_hours_to_target_start: float
    window_low: float
    residual_sample_count: int
    residual_scope: str
    residual_source: str
    model_version: str
    unit: str
    trailing_lookback_hours: float
    model_policy_basis: str
    holdout_nll: float | None = None


def build_day0_low_distribution(
    *,
    observed_low_so_far: float,
    future_member_mins: np.ndarray,
    round_fn: "Callable | None" = None,
    precision: float = 1.0,
    provenance: str = "",
) -> np.ndarray:
    """Physically-correct settlement samples for a Day0 LOW market.

    Each sample i = settle(min(observed_low_so_far, future_member_mins[i])). The observation is a
    ceiling: no sample may exceed observed_low_so_far. ``current_temp`` is deliberately absent.
    """
    _ = provenance  # carried for receipt provenance; not part of the value path
    arr = np.asarray(future_member_mins, dtype=np.float64)
    if arr.size == 0:
        raise ValueError("build_day0_low_distribution: future_member_mins must be non-empty")
    obs = float(observed_low_so_far)
    samples = np.minimum(obs, arr)
    return apply_settlement_rounding(samples, round_fn, precision)


@lru_cache(maxsize=4)
def _load_pre_day0_low_empirical_model_cached(path_str: str) -> Optional[dict[str, Any]]:
    try:
        path = Path(path_str)
        if not path.exists():
            return None
        model = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if model.get("model_version") != PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION:
        return None
    return model


def load_pre_day0_low_empirical_model(path: str | Path | None = None) -> Optional[dict[str, Any]]:
    """Load the deployed pre-Day0 LOW empirical residual model, if present."""
    effective = DEFAULT_PRE_DAY0_LOW_EMPIRICAL_MODEL_PATH if path is None else Path(path)
    return _load_pre_day0_low_empirical_model_cached(str(effective))


def pre_day0_low_empirical_live_policy(
    model: Optional[dict[str, Any]],
) -> tuple[float, float, str] | None:
    """Return ``(max_lead_hours, trailing_lookback_hours, basis)`` for a model."""
    if not model:
        return None
    policy = model.get("live_policy") or {}
    try:
        max_lead = float(policy.get("max_lead_hours", 3.0))
        trailing = float(policy.get("trailing_lookback_hours", 1.0))
    except (TypeError, ValueError):
        return None
    if not np.isfinite(max_lead) or not np.isfinite(trailing):
        return None
    if max_lead <= 0.0 or trailing <= 0.0:
        return None
    basis = str(policy.get("basis") or "empirical_residual_holdout")
    return max_lead, trailing, basis


def _pre_day0_low_lead_bucket(
    lead_hours_to_target_start: float,
    *,
    max_lead_hours: float,
) -> Optional[int]:
    try:
        lead = float(lead_hours_to_target_start)
    except (TypeError, ValueError):
        return None
    try:
        max_lead = float(max_lead_hours)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(lead) or not np.isfinite(max_lead):
        return None
    if lead <= 0.0 or max_lead <= 0.0 or lead > max_lead:
        return None
    return max(1, min(int(math.ceil(max_lead)), int(math.ceil(lead - 1e-12))))


def _residual_entry(
    model: dict[str, Any],
    *,
    city_name: str,
    unit: str,
    lead_bucket_hours: int,
    min_samples: int,
) -> tuple[str, dict[str, Any]] | None:
    bucket = str(int(lead_bucket_hours))
    city_entry = (
        (model.get("by_city") or {})
        .get(str(city_name), {})
        .get("lead_buckets", {})
        .get(bucket)
    )
    if city_entry is not None and int(city_entry.get("n") or 0) >= int(min_samples):
        return f"city:{city_name}", city_entry
    unit_entry = (
        (model.get("by_unit") or {})
        .get(str(unit).upper(), {})
        .get("lead_buckets", {})
        .get(bucket)
    )
    if unit_entry is not None and int(unit_entry.get("n") or 0) >= int(min_samples):
        return f"unit:{str(unit).upper()}", unit_entry
    return None


def build_pre_day0_low_empirical_conditioning(
    *,
    member_mins: np.ndarray,
    window_low: float,
    lead_hours_to_target_start: float,
    unit: str,
    city_name: str,
    model: Any = _MODEL_SENTINEL,
    min_samples: int = 120,
) -> Optional[PreDay0LowEmpiricalConditioning]:
    """Build empirical pre-Day0 LOW samples from verified historical residuals.

    The fitted residual is:
        min(local target-day hours 00..03 low) - min(T-1 observed night window low)

    Runtime sample law:
        L_sample = min(forecast_member_low, observed_T_minus_1_low + residual_q)

    This preserves same-day LOW causality: a later target-day trough still comes
    from the forecast member low, while the midnight carryover component comes
    from historical station behavior rather than a hand-tuned blend weight.
    """
    arr = np.asarray(member_mins, dtype=np.float64)
    if arr.ndim != 1 or arr.size == 0 or not np.all(np.isfinite(arr)):
        return None
    try:
        low = float(window_low)
        lead = float(lead_hours_to_target_start)
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite(v) for v in (low, lead)):
        return None
    effective_model = load_pre_day0_low_empirical_model() if model is _MODEL_SENTINEL else model
    if not effective_model:
        return None
    policy = pre_day0_low_empirical_live_policy(effective_model)
    if policy is None:
        return None
    max_lead, trailing_lookback, policy_basis = policy
    bucket = _pre_day0_low_lead_bucket(lead, max_lead_hours=max_lead)
    if bucket is None:
        return None

    u = str(unit or "").upper()
    found = _residual_entry(
        effective_model,
        city_name=str(city_name),
        unit=u,
        lead_bucket_hours=bucket,
        min_samples=int(min_samples),
    )
    if found is None:
        return None
    scope, entry = found

    residuals = np.asarray(entry.get("residual_quantiles") or (), dtype=np.float64)
    if residuals.ndim != 1 or residuals.size == 0 or not np.all(np.isfinite(residuals)):
        return None
    conditioned = np.minimum(arr[:, np.newaxis], low + residuals[np.newaxis, :]).reshape(-1)
    if conditioned.size == 0 or not np.all(np.isfinite(conditioned)):
        return None
    return PreDay0LowEmpiricalConditioning(
        conditioned_member_mins=conditioned,
        residual_quantiles=residuals,
        lead_bucket_hours=int(bucket),
        lead_hours_to_target_start=float(lead),
        window_low=float(low),
        residual_sample_count=int(entry.get("n") or 0),
        residual_scope=scope,
        residual_source=str(effective_model.get("source_table") or "unknown"),
        model_version=str(effective_model.get("model_version") or ""),
        unit=u or "UNKNOWN",
        trailing_lookback_hours=float(trailing_lookback),
        model_policy_basis=policy_basis,
        holdout_nll=(
            float(entry["holdout_nll"])
            if entry.get("holdout_nll") is not None
            else None
        ),
    )


def p_vector(
    bins: "list[Bin]",
    *,
    observed_low_so_far: float,
    future_member_mins: np.ndarray,
    round_fn: "Callable | None" = None,
    precision: float = 1.0,
    provenance: str = "",
) -> np.ndarray:
    """Normalized categorical probability vector over LOW bins from the physical samples.

    Pure helper (no signal construction). Open-low bins count samples at/below their finite high;
    open-high bins count samples at/above their finite low; interior bins count [low, high].
    """
    samples = build_day0_low_distribution(
        observed_low_so_far=observed_low_so_far,
        future_member_mins=future_member_mins,
        round_fn=round_fn,
        precision=precision,
        provenance=provenance,
    )
    probs = np.zeros(len(bins), dtype=np.float64)
    for i, b in enumerate(bins):
        if getattr(b, "is_open_low", False):
            probs[i] = float(np.sum(samples <= float(b.high)))
        elif getattr(b, "is_open_high", False):
            probs[i] = float(np.sum(samples >= float(b.low)))
        else:
            probs[i] = float(np.sum((samples >= float(b.low)) & (samples <= float(b.high))))
    total = probs.sum()
    if total == 0.0 or not np.isfinite(total):
        raise ValueError(
            "p_vector: no finite mass in any bin — distribution is degenerate. "
            f"samples={samples!r}"
        )
    return probs / total
