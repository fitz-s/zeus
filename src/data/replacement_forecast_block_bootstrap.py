"""Cluster/block bootstrap diagnostics for replacement forecast blocked evidence."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal


BlockAxisName = Literal["target_date", "city", "temperature_metric", "guardrail_bucket"]
DEFAULT_BLOCK_AXES: tuple[BlockAxisName, ...] = ("target_date", "city", "temperature_metric", "guardrail_bucket")


@dataclass(frozen=True)
class ReplacementForecastBlockBootstrapRow:
    city: str
    target_date: str
    temperature_metric: Literal["high", "low"]
    guardrail_bucket: str
    replay_status: str
    truth_status: str
    replacement_delta_after_cost_pnl: float
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("city", "target_date", "guardrail_bucket", "replay_status", "truth_status"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")


@dataclass(frozen=True)
class ReplacementForecastBlockBootstrapResult:
    status: str
    reason_codes: tuple[str, ...]
    total_rows: int
    scored_rows: int
    excluded_rows: int
    block_axes: tuple[BlockAxisName, ...]
    block_count: int
    iterations: int
    seed: int
    observed_mean_delta_after_cost_pnl: float | None
    observed_total_delta_after_cost_pnl: float
    ci_lower_mean_delta_after_cost_pnl: float | None
    ci_upper_mean_delta_after_cost_pnl: float | None
    sampled_block_mean_deltas: tuple[float, ...]
    excluded_reason_counts: dict[str, int]

    @property
    def promotion_allowed(self) -> bool:
        return False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "total_rows": self.total_rows,
            "scored_rows": self.scored_rows,
            "excluded_rows": self.excluded_rows,
            "block_axes": list(self.block_axes),
            "block_count": self.block_count,
            "iterations": self.iterations,
            "seed": self.seed,
            "observed_mean_delta_after_cost_pnl": self.observed_mean_delta_after_cost_pnl,
            "observed_total_delta_after_cost_pnl": self.observed_total_delta_after_cost_pnl,
            "ci_lower_mean_delta_after_cost_pnl": self.ci_lower_mean_delta_after_cost_pnl,
            "ci_upper_mean_delta_after_cost_pnl": self.ci_upper_mean_delta_after_cost_pnl,
            "excluded_reason_counts": dict(self.excluded_reason_counts),
            "promotion_allowed": False,
        }


def _axis_value(row: ReplacementForecastBlockBootstrapRow, axis: BlockAxisName) -> str:
    if axis == "target_date":
        return row.target_date
    if axis == "city":
        return row.city
    if axis == "temperature_metric":
        return row.temperature_metric
    if axis == "guardrail_bucket":
        return row.guardrail_bucket
    raise ValueError(f"unsupported block axis: {axis}")


def _percentile(values: tuple[float, ...], quantile: float) -> float:
    if not values:
        raise ValueError("values are required")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def _exclusion_reason(row: ReplacementForecastBlockBootstrapRow) -> str | None:
    if row.truth_status != "VERIFIED":
        return "REPLACEMENT_BLOCK_BOOTSTRAP_EXCLUDED_NON_VERIFIED_TRUTH"
    if row.replay_status != "SCORED":
        return "REPLACEMENT_BLOCK_BOOTSTRAP_EXCLUDED_NON_SCORED_REPLAY"
    return None


def run_replacement_forecast_block_bootstrap(
    rows: Iterable[ReplacementForecastBlockBootstrapRow],
    *,
    block_axes: tuple[BlockAxisName, ...] = DEFAULT_BLOCK_AXES,
    iterations: int = 2000,
    seed: int = 20260606,
    confidence_level: float = 0.95,
    min_blocks: int = 5,
) -> ReplacementForecastBlockBootstrapResult:
    """Estimate blocked-evidence uncertainty with correlated replay rows kept in blocks."""

    if not block_axes:
        raise ValueError("block_axes must not be empty")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if min_blocks <= 0:
        raise ValueError("min_blocks must be positive")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")

    row_tuple = tuple(rows)
    for row in row_tuple:
        if not isinstance(row, ReplacementForecastBlockBootstrapRow):
            raise TypeError("rows must contain ReplacementForecastBlockBootstrapRow objects")

    exclusions: dict[str, int] = defaultdict(int)
    scored_rows: list[ReplacementForecastBlockBootstrapRow] = []
    for row in row_tuple:
        reason = _exclusion_reason(row)
        if reason is None:
            scored_rows.append(row)
        else:
            exclusions[reason] += 1

    if not row_tuple:
        return ReplacementForecastBlockBootstrapResult(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_BLOCK_BOOTSTRAP_NO_ROWS",),
            total_rows=0,
            scored_rows=0,
            excluded_rows=0,
            block_axes=block_axes,
            block_count=0,
            iterations=0,
            seed=seed,
            observed_mean_delta_after_cost_pnl=None,
            observed_total_delta_after_cost_pnl=0.0,
            ci_lower_mean_delta_after_cost_pnl=None,
            ci_upper_mean_delta_after_cost_pnl=None,
            sampled_block_mean_deltas=(),
            excluded_reason_counts={},
        )

    groups: dict[tuple[str, ...], list[ReplacementForecastBlockBootstrapRow]] = defaultdict(list)
    for row in scored_rows:
        groups[tuple(_axis_value(row, axis) for axis in block_axes)].append(row)

    block_values = tuple(tuple(group_rows) for _, group_rows in sorted(groups.items(), key=lambda item: item[0]))
    observed_total = sum(row.replacement_delta_after_cost_pnl for row in scored_rows)
    observed_mean = (observed_total / len(scored_rows)) if scored_rows else None
    reasons: list[str] = []
    if exclusions:
        reasons.append("REPLACEMENT_BLOCK_BOOTSTRAP_HAS_EXCLUDED_ROWS")
    if not scored_rows:
        reasons.append("REPLACEMENT_BLOCK_BOOTSTRAP_NO_SCORED_VERIFIED_ROWS")
    if len(block_values) < min_blocks:
        reasons.append("REPLACEMENT_BLOCK_BOOTSTRAP_INSUFFICIENT_BLOCKS")

    if not scored_rows or len(block_values) < min_blocks:
        return ReplacementForecastBlockBootstrapResult(
            status="BLOCKED",
            reason_codes=tuple(reasons),
            total_rows=len(row_tuple),
            scored_rows=len(scored_rows),
            excluded_rows=len(row_tuple) - len(scored_rows),
            block_axes=block_axes,
            block_count=len(block_values),
            iterations=0,
            seed=seed,
            observed_mean_delta_after_cost_pnl=observed_mean,
            observed_total_delta_after_cost_pnl=observed_total,
            ci_lower_mean_delta_after_cost_pnl=None,
            ci_upper_mean_delta_after_cost_pnl=None,
            sampled_block_mean_deltas=(),
            excluded_reason_counts=dict(exclusions),
        )

    rng = random.Random(seed)
    sampled_means: list[float] = []
    block_count = len(block_values)
    for _ in range(iterations):
        sample_rows: list[ReplacementForecastBlockBootstrapRow] = []
        for _ in range(block_count):
            sample_rows.extend(rng.choice(block_values))
        sampled_total = sum(row.replacement_delta_after_cost_pnl for row in sample_rows)
        sampled_means.append(sampled_total / len(sample_rows))

    alpha = 1.0 - confidence_level
    lower = _percentile(tuple(sampled_means), alpha / 2.0)
    upper = _percentile(tuple(sampled_means), 1.0 - alpha / 2.0)
    if lower <= 0.0 <= upper:
        reasons.append("REPLACEMENT_BLOCK_BOOTSTRAP_CI_OVERLAPS_ZERO")
    if lower < 0.0:
        reasons.append("REPLACEMENT_BLOCK_BOOTSTRAP_NEGATIVE_LOWER_BOUND")
    status = "DIAGNOSTIC_PASS" if not reasons else "BLOCKED"

    return ReplacementForecastBlockBootstrapResult(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_BLOCK_BOOTSTRAP_DIAGNOSTIC_PASS",)),
        total_rows=len(row_tuple),
        scored_rows=len(scored_rows),
        excluded_rows=len(row_tuple) - len(scored_rows),
        block_axes=block_axes,
        block_count=block_count,
        iterations=iterations,
        seed=seed,
        observed_mean_delta_after_cost_pnl=observed_mean,
        observed_total_delta_after_cost_pnl=observed_total,
        ci_lower_mean_delta_after_cost_pnl=lower,
        ci_upper_mean_delta_after_cost_pnl=upper,
        sampled_block_mean_deltas=tuple(sampled_means),
        excluded_reason_counts=dict(exclusions),
    )
