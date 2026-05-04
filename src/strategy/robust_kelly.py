# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/DESIGN_PHASE2_75_ROBUST_KELLY.md
#                  + may4math.md Finding 5 (CRITICAL_QUANT_RISK,
#                    ROBUST_KELLY_NEEDED_NOW)
"""Robust lower-bound Kelly sizing (Phase 2.75).

The legacy ``kelly_size`` in ``kelly.py`` applies the **point posterior**
to size. When ``p_posterior`` is biased (residual cycle drift, transfer
noise, oracle posterior wobble, slippage), Kelly **amplifies** the bias.

This module replaces that with a robust lower-bound Kelly:

  * Combine component uncertainties (Platt CI, DG bootstrap CI,
    validated_transfers OOS noise, oracle Beta-binomial posterior,
    execution slippage) into ``p_lower`` via Bonferroni-style min.
  * Compute Kelly on ``(p_lower, cost_eff_upper)`` instead of
    ``(p_point, cost_mid)``.
  * Apply a **domain-mismatch multiplier** that hard-zeros size when
    ``evaluate_calibration_transfer`` returned BLOCK or SHADOW_ONLY,
    and downweights by validated_transfers recency for LIVE_ELIGIBLE.

The legacy ``kelly_size`` is preserved for backward-compat callers that
have not yet been migrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SizingUncertaintyInputs:
    """Component uncertainty inputs to robust lower-bound Kelly.

    Each ``*_ci`` is a 90% confidence interval ``(low, high)`` in the
    probability domain (``[0,1]``). ``oracle_posterior_upper`` is the
    Beta-binomial posterior 95th percentile (per may4math.md F3).

    ``cost_point`` is the mid quote; ``cost_eff_upper`` adds the upper
    bound of slippage + tick + fee + queue jump (i.e., the worst-case
    execution price).
    """

    p_point: float
    platt_param_ci: tuple[float, float]
    decision_group_ci: tuple[float, float]
    transfer_ci: tuple[float, float]
    oracle_posterior_upper: float
    cost_point: float
    cost_eff_upper: float

    def __post_init__(self) -> None:
        for name in ("p_point", "cost_point", "cost_eff_upper", "oracle_posterior_upper"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)):
                raise TypeError(f"{name} must be numeric, got {type(v).__name__}")
            if not (0.0 <= float(v) <= 1.0):
                raise ValueError(f"{name} out of [0,1]: {v}")
        for name in ("platt_param_ci", "decision_group_ci", "transfer_ci"):
            ci = getattr(self, name)
            if not (isinstance(ci, tuple) and len(ci) == 2):
                raise TypeError(f"{name} must be a 2-tuple, got {ci!r}")
            lo, hi = ci
            if not (0.0 <= float(lo) <= float(hi) <= 1.0):
                raise ValueError(f"{name} not a valid [low, high] pair in [0,1]: {ci!r}")
        if self.cost_eff_upper < self.cost_point:
            raise ValueError(
                f"cost_eff_upper ({self.cost_eff_upper}) < cost_point ({self.cost_point}); "
                "effective execution cost cannot be cheaper than the mid quote"
            )


def compute_p_lower(inputs: SizingUncertaintyInputs) -> float:
    """Bonferroni-style lower bound across independent uncertainty sources.

    Returns the most conservative (smallest) of:
        * Platt parameter CI low
        * Decision-group bootstrap CI low
        * validated_transfers OOS CI low
        * p_point − 2 × oracle posterior upper (oracle penalty)

    Clamped to ``[0,1]``. Picking the min, not multiplying, prevents
    confidence-interval interpretation drift and keeps the bound
    conservative under unknown correlation.
    """
    oracle_penalty_lower = inputs.p_point - 2.0 * inputs.oracle_posterior_upper
    components = [
        float(inputs.platt_param_ci[0]),
        float(inputs.decision_group_ci[0]),
        float(inputs.transfer_ci[0]),
        oracle_penalty_lower,
    ]
    p_l = min(components)
    if p_l < 0.0:
        return 0.0
    if p_l > 1.0:
        return 1.0
    return p_l


def robust_kelly_size(
    inputs: SizingUncertaintyInputs,
    base_lambda: float = 0.25,
) -> float:
    """Lower-bound Kelly fraction (before downstream multipliers).

    Returns the fractional-Kelly position size in [0, base_lambda].
    Downstream callers multiply by ``m_strategy``, ``m_oracle``,
    ``m_cycle_domain``, ``m_liquidity``, and bankroll to land at final
    USD size — see ``DESIGN_PHASE2_75_ROBUST_KELLY.md §Step 3``.

    Returns 0.0 when ``p_lower ≤ cost_eff_upper`` (no robust edge).
    """
    if not (0.0 < base_lambda <= 1.0):
        raise ValueError(f"base_lambda must be in (0, 1], got {base_lambda!r}")
    p_l = compute_p_lower(inputs)
    cost_eff = float(inputs.cost_eff_upper)
    if p_l <= cost_eff:
        return 0.0
    if cost_eff >= 1.0:
        return 0.0
    f_robust = (p_l - cost_eff) / (1.0 - cost_eff)
    if f_robust <= 0.0:
        return 0.0
    return base_lambda * f_robust


def domain_mismatch_multiplier(
    transfer_status: str,
    *,
    days_since_validation: Optional[float] = None,
) -> float:
    """Hard gate + soft downweight on calibration domain mismatch.

    ``transfer_status`` is one of ``BLOCK | SHADOW_ONLY | LIVE_ELIGIBLE``
    (from ``evaluate_calibration_transfer``). Anything else → 0.0
    (fail-closed for unknown statuses).

    For ``LIVE_ELIGIBLE`` with no recency info (``days_since_validation``
    None) we treat as fresh (full weight). This is the case when the
    domain matched exactly (no transfer row consulted) — exact match is
    always full weight.

    For ``LIVE_ELIGIBLE`` matched via validated_transfers row:
        * <= 30 days  → 1.0
        * <= 90 days  → 0.5
        * > 90 days   → 0.25
    """
    if transfer_status == "BLOCK":
        return 0.0
    if transfer_status == "SHADOW_ONLY":
        return 0.0
    if transfer_status != "LIVE_ELIGIBLE":
        return 0.0
    if days_since_validation is None:
        return 1.0
    d = float(days_since_validation)
    if d < 0.0:
        return 1.0
    if d <= 30.0:
        return 1.0
    if d <= 90.0:
        return 0.5
    return 0.25


@dataclass(frozen=True)
class SizingEvidence:
    """Full sizing trace for a single sized order.

    Stored alongside ``opportunity_fact`` so post-trade analysis can
    replay alternative sizing policies.
    """

    p_point: float
    p_lower_5pct: float
    cost_point: float
    cost_eff_upper: float
    f_point_kelly: float
    f_robust_kelly: float
    base_lambda: float
    m_strategy: float
    m_oracle: float
    m_cycle_domain: float
    m_liquidity: float
    final_size_units: float
    components_uncertainty: dict = field(default_factory=dict)
    sizing_policy_id: str = "robust_kelly_v1_2026_05_04"


def build_sizing_evidence(
    *,
    inputs: SizingUncertaintyInputs,
    base_lambda: float,
    m_strategy: float,
    m_oracle: float,
    m_cycle_domain: float,
    m_liquidity: float,
    bankroll: float,
) -> SizingEvidence:
    """Convenience builder: compute both point and robust Kelly + assemble evidence.

    Final size in USD = ``f_robust × m_strategy × m_oracle × m_cycle_domain × m_liquidity × bankroll``.
    The point-Kelly leg ``f_point_kelly`` is recorded for shadow-comparison
    purposes (operator sees what old sizing would have placed).
    """
    p_l = compute_p_lower(inputs)
    cost_eff = float(inputs.cost_eff_upper)
    cost_mid = float(inputs.cost_point)
    f_robust_raw = robust_kelly_size(inputs, base_lambda=base_lambda)
    f_robust = f_robust_raw / base_lambda if base_lambda > 0 else 0.0
    if cost_mid >= 1.0 or cost_mid <= 0.0:
        f_point = 0.0
    elif inputs.p_point <= cost_mid:
        f_point = 0.0
    else:
        f_point = (inputs.p_point - cost_mid) / (1.0 - cost_mid)
    final_size = (
        f_robust_raw
        * float(m_strategy)
        * float(m_oracle)
        * float(m_cycle_domain)
        * float(m_liquidity)
        * float(bankroll)
    )
    if final_size < 0.0:
        final_size = 0.0
    components = {
        "platt_param_ci_low": float(inputs.platt_param_ci[0]),
        "platt_param_ci_high": float(inputs.platt_param_ci[1]),
        "decision_group_ci_low": float(inputs.decision_group_ci[0]),
        "decision_group_ci_high": float(inputs.decision_group_ci[1]),
        "transfer_ci_low": float(inputs.transfer_ci[0]),
        "transfer_ci_high": float(inputs.transfer_ci[1]),
        "oracle_posterior_upper": float(inputs.oracle_posterior_upper),
    }
    return SizingEvidence(
        p_point=float(inputs.p_point),
        p_lower_5pct=p_l,
        cost_point=cost_mid,
        cost_eff_upper=cost_eff,
        f_point_kelly=f_point,
        f_robust_kelly=f_robust,
        base_lambda=float(base_lambda),
        m_strategy=float(m_strategy),
        m_oracle=float(m_oracle),
        m_cycle_domain=float(m_cycle_domain),
        m_liquidity=float(m_liquidity),
        final_size_units=final_size,
        components_uncertainty=components,
    )
