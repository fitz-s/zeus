# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md §"Stress scenarios"

"""TailStressScenario — 6 stress scenarios for shoulder candidate vetting per dossier §7.5.

Six required scenarios (verbatim from 04_PHASE_3_SHOULDER.md §"Stress scenarios"):
  1. +2σ forecast error (perturb posterior in adverse direction)
  2. station anomaly (apply Paris-style sensor-spike to source temperature)
  3. late-day advection (apply afternoon temperature shock)
  4. source revision (assume official observation revises against position)
  5. model tail under-dispersion (assume ensemble underestimates tail mass)
  6. correlated city crash (assume all cities in cluster realize same-direction tail)

Rejection rule: a shoulder candidate that fails ANY stress scenario (i.e.,
posterior_stressed × payoff - fee_adjusted_cost > 0 is invalid) is rejected
with NoTradeReason.SHOULDER_STRESS_FAIL. Results written to tail_stress_scenarios table.

SCAFFOLD — run_stress_tests body raises NotImplementedError.
Production logic wired in T2 production pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TailStressScenario:
    """Single stress scenario descriptor for shoulder tail-risk testing.

    Each instance carries a scenario_id matching the dossier §7.5 name,
    a delta_sigma perturbation coefficient, and a human description.
    """

    scenario_id: str
    """Stable identifier — matches the dossier §7.5 enumeration label."""

    delta_sigma: float
    """Adverse perturbation magnitude in σ units (positive = adverse).
    For scenarios that are not σ-parametric, delta_sigma encodes the
    equivalent severity (e.g., 3.0 for correlated_city_crash)."""

    description: str
    """Human-readable scenario description for ledger display."""


# ── 6 canonical scenarios (dossier §7.5 verbatim order) ──────────────────────

FORECAST_PLUS_2SIGMA = TailStressScenario(
    scenario_id="forecast_plus_2sigma",
    delta_sigma=2.0,
    description="Perturb posterior +2σ in adverse direction relative to shoulder position.",
)

STATION_ANOMALY = TailStressScenario(
    scenario_id="station_anomaly",
    delta_sigma=3.0,
    description="Apply Paris-style sensor-spike anomaly to source temperature reading.",
)

LATE_DAY_ADVECTION = TailStressScenario(
    scenario_id="late_day_advection",
    delta_sigma=1.5,
    description="Apply afternoon temperature shock (late-day advection event).",
)

SOURCE_REVISION = TailStressScenario(
    scenario_id="source_revision",
    delta_sigma=1.0,
    description="Assume official observation revises against position at settlement.",
)

MODEL_TAIL_UNDERDISPERSION = TailStressScenario(
    scenario_id="model_tail_underdispersion",
    delta_sigma=2.0,
    description="Assume ensemble underestimates tail mass by dispersion factor.",
)

CORRELATED_CITY_CRASH = TailStressScenario(
    scenario_id="correlated_city_crash",
    delta_sigma=3.0,
    description="All cities in same weather-system cluster realize same-direction tail.",
)

# Canonical ordered sequence — all 6 required per §7.5.
ALL_SCENARIOS: tuple[TailStressScenario, ...] = (
    FORECAST_PLUS_2SIGMA,
    STATION_ANOMALY,
    LATE_DAY_ADVECTION,
    SOURCE_REVISION,
    MODEL_TAIL_UNDERDISPERSION,
    CORRELATED_CITY_CRASH,
)


def run_stress_tests(
    candidate,
    scenarios: Optional[tuple[TailStressScenario, ...]] = None,
) -> dict[str, float]:
    """Apply stress scenarios to a shoulder candidate; return max_loss_pct per scenario.

    Args:
        candidate: Shoulder candidate (ShoulderStrategyVNext or precursor context).
        scenarios: Tuple of TailStressScenario instances to apply.
                   Defaults to ALL_SCENARIOS (all 6 required per §7.5).

    Returns:
        dict[scenario_id -> max_loss_pct]: Maximum loss fraction under each scenario.
        Caller derives tail_probability_stressed as max(values) from this dict.

    Rejection: caller checks if tail_probability_stressed makes the trade
    non-viable; if so, emits NoTradeReason.SHOULDER_STRESS_FAIL.

    Fail-closed: if ENS member count < min_members, returns NaN for that
    scenario → SHOULDER_STRESS_FAIL per §5 R-4.

    T2 thin mode: tail_probability_calibrated is nan on all Pattern-B candidates
    (conn=None path or SHOULDER_NO_TRADE_GATE). Caller derives
    tail_probability_stressed = max(values.values()); nan propagates to
    SHOULDER_STRESS_FAIL per R-4. Full perturbation math deferred to T3.
    """
    _scenarios = scenarios if scenarios is not None else ALL_SCENARIOS
    # Thin fail-closed: if calibrated probability unavailable, all scenarios NaN.
    # R-4: NaN propagates → tail_probability_stressed = max(...) = NaN → SHOULDER_STRESS_FAIL.
    p_cal = getattr(candidate, "tail_probability_calibrated", float("nan"))
    if p_cal != p_cal:  # NaN check (math.isnan not imported here intentionally)
        return {s.scenario_id: float("nan") for s in _scenarios}
    # Full perturbation math (T3+: when sigma is plumbed from ensemble context).
    # Phase 3 T2 thin mode: sigma unavailable from BinEdge alone → fail-closed NaN.
    return {s.scenario_id: float("nan") for s in _scenarios}
