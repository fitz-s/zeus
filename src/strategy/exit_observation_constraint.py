# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27) — D1 of K=5
#   structural decisions for the multi-bin family exit strategy.
#
# Purpose: encode WU/HKO observed extreme as a deterministic feasibility mask
#   over family bins. The reported high-so-far / low-so-far is an OBSERVATION
#   AUTHORITY (not a probability estimate); it produces hard impossibility for
#   any bin the final extreme can no longer reach. This object is the SOLE
#   bridge between the existing settlement_day_observation_authority row (built
#   by cycle_runtime.build_settlement_day_observation_authority_row) and the
#   exit math (D2 posterior truncation + D5 deterministic short-circuit).
#
# Math (HIGH market, observed = high_so_far, final = max_t Temp_t >= high_so_far):
#   bin.high < high_so_far          → impossible (final max is already above bin)
#   bin.low <= high_so_far <= bin.high → contains_current_record
#   bin.low > high_so_far           → feasible_above (max might rise into bin)
#
# Math (LOW market, observed = low_so_far, final = min_t Temp_t <= low_so_far):
#   bin.low > low_so_far            → impossible (final min is already below bin)
#   bin.low <= low_so_far <= bin.high → contains_current_record
#   bin.high < low_so_far           → feasible_below (min might fall into bin)
#
# Shoulders (Bin.low=None means "X or below"; Bin.high=None means "X or higher"):
#   handled by treating None as -inf / +inf so the inequalities still work.
#
# Authority gating (deterministic mode required for impossibility verdict):
#   source_authorized_for_settlement == 1
#   AND local_date_matches_target == 1
#   AND coverage_status == "OK"
#   AND freshness_status == "FRESH"
#   AND observed value is finite
#   AND temperature_metric in {"high","low"}
# If any gate fails, authority_status="ADVISORY_ONLY" and feasibility()
# returns "unknown" for every bin — D5 must NOT short-circuit on advisory.
#
# Purity: no DB, no CLOB, no logging. Total function over its inputs.
"""Settlement-progress constraint from observed extreme (D1).

This is the typed observation-authority object the exit math depends on. It
takes the runtime observation authority row already produced by cycle_runtime
and projects it into per-bin feasibility verdicts.

The verdict is a hard mask, not a probability shift. It is consumed by:
  - D2 constrain_family_posterior_by_observation (zero impossible-bin mass)
  - D5 deterministic impossibility short-circuit in Position.evaluate_exit
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping

from src.types.market import Bin

FeasibilityVerdict = Literal[
    "impossible",
    "contains_current_record",
    "feasible",
    "unknown",
]

AuthorityStatus = Literal["DETERMINISTIC", "ADVISORY_ONLY"]


@dataclass(frozen=True)
class SettlementProgressConstraint:
    """Typed projection of the settlement-day observation authority.

    Fields:
      metric            — "high" or "low" or None (None ⇒ advisory only).
      observed_value    — high_so_far or low_so_far in the metric direction.
      authority_status  — "DETERMINISTIC" iff every gate passed.
      gate_reasons      — diagnostic record of why authority_status is what it
                          is (kept for trace logging in D5; never branched on).
    """

    metric: Literal["high", "low"] | None
    observed_value: float | None
    authority_status: AuthorityStatus
    gate_reasons: tuple[str, ...] = ()

    # ----- consumers -----

    def is_deterministic(self) -> bool:
        return self.authority_status == "DETERMINISTIC"

    def feasibility(self, bin: Bin) -> FeasibilityVerdict:
        """Project the observed extreme onto one bin.

        Advisory mode always returns "unknown" — D5 must check
        is_deterministic() before treating any verdict as a hard mask.
        """
        if not self.is_deterministic():
            return "unknown"
        # Treat shoulder open ends as ±inf so the inequalities work uniformly.
        # Bin.low=None ⇒ "X or below"  ⇒ effective low = -inf
        # Bin.high=None ⇒ "X or higher" ⇒ effective high = +inf
        lo = float("-inf") if bin.low is None else float(bin.low)
        hi = float("inf") if bin.high is None else float(bin.high)
        obs = float(self.observed_value)  # type: ignore[arg-type]

        if self.metric == "high":
            if hi < obs:
                return "impossible"
            if lo <= obs <= hi:
                return "contains_current_record"
            # lo > obs
            return "feasible"
        # metric == "low"
        if lo > obs:
            return "impossible"
        if lo <= obs <= hi:
            return "contains_current_record"
        # hi < obs
        return "feasible"

    def mask(self, bins: Iterable[Bin]) -> tuple[FeasibilityVerdict, ...]:
        """Vectorised feasibility verdict over a family of bins."""
        return tuple(self.feasibility(b) for b in bins)


# ----- builder -----


_REQUIRED_METRICS = {"high", "low"}


def build_settlement_progress_constraint(
    row: Mapping[str, object] | None,
) -> SettlementProgressConstraint:
    """Build a constraint from a settlement_day_observation_authority row.

    The row is the dict produced by
    cycle_runtime.build_settlement_day_observation_authority_row. We do not
    couple to its construction site beyond reading the documented field set.

    Returns an ADVISORY_ONLY constraint whenever any gate fails, never raises.
    A None row (no observation available) also returns ADVISORY_ONLY.
    """
    if row is None:
        return SettlementProgressConstraint(
            metric=None,
            observed_value=None,
            authority_status="ADVISORY_ONLY",
            gate_reasons=("row_missing",),
        )

    failures: list[str] = []

    metric_raw = row.get("temperature_metric")
    metric: Literal["high", "low"] | None
    if isinstance(metric_raw, str) and metric_raw.strip().lower() in _REQUIRED_METRICS:
        metric = metric_raw.strip().lower()  # type: ignore[assignment]
    else:
        metric = None
        failures.append(f"temperature_metric_invalid:{metric_raw!r}")

    observed: float | None = None
    if metric is not None:
        key = "high_so_far" if metric == "high" else "low_so_far"
        raw = row.get(key)
        try:
            observed = float(raw)  # type: ignore[arg-type]
            if observed != observed or observed in (float("inf"), float("-inf")):
                failures.append(f"observed_value_non_finite:{key}={raw!r}")
                observed = None
        except (TypeError, ValueError):
            failures.append(f"observed_value_unset:{key}={raw!r}")
            observed = None

    if row.get("source_authorized_for_settlement") != 1:
        failures.append(
            f"source_not_authorized:{row.get('source_authorized_for_settlement')!r}"
        )
    if row.get("local_date_matches_target") != 1:
        failures.append(
            f"local_date_mismatch:{row.get('local_date_matches_target')!r}"
        )
    if str(row.get("coverage_status") or "").upper() != "OK":
        failures.append(f"coverage_not_ok:{row.get('coverage_status')!r}")
    if str(row.get("freshness_status") or "").upper() != "FRESH":
        failures.append(f"freshness_not_fresh:{row.get('freshness_status')!r}")

    if failures or metric is None or observed is None:
        return SettlementProgressConstraint(
            metric=metric,
            observed_value=observed,
            authority_status="ADVISORY_ONLY",
            gate_reasons=tuple(failures or ("unknown_gate_failure",)),
        )

    return SettlementProgressConstraint(
        metric=metric,
        observed_value=observed,
        authority_status="DETERMINISTIC",
        gate_reasons=("all_gates_passed",),
    )
