"""Day0 absorbing boundary logic for EDLI live inference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.events.triggers.day0_extreme_updated import Day0HardFactGate

Metric = Literal["high", "low"]
BinKind = Literal["finite_range", "open_shoulder"]


@dataclass(frozen=True)
class BoundaryResult:
    rounded_extreme: int
    killed: bool
    fact_true: bool
    reason: str


def evaluate_day0_absorbing_boundary(
    *,
    metric: Metric,
    raw_extreme_so_far: float,
    bin_kind: BinKind,
    lower: int | None,
    upper: int | None,
    settlement_semantics: Any,
    hard_fact_gate: Day0HardFactGate,
) -> BoundaryResult:
    """Evaluate absorbing high/low Day0 facts using settlement semantics."""

    rounded = int(settlement_semantics.round_single(raw_extreme_so_far))
    if not hard_fact_gate.live_eligible():
        return BoundaryResult(rounded, killed=False, fact_true=False, reason="HARD_FACT_GATE_BLOCKED")

    if metric == "high" and bin_kind == "finite_range":
        if upper is None:
            raise ValueError("high finite_range requires upper")
        if rounded > upper:
            return BoundaryResult(rounded, killed=True, fact_true=False, reason="HIGH_EXCEEDED_FINITE_BIN")
        return BoundaryResult(rounded, killed=False, fact_true=False, reason="HIGH_FINITE_BIN_STILL_POSSIBLE")

    if metric == "low" and bin_kind == "finite_range":
        if lower is None:
            raise ValueError("low finite_range requires lower")
        if rounded < lower:
            return BoundaryResult(rounded, killed=True, fact_true=False, reason="LOW_BREACHED_FINITE_BIN")
        return BoundaryResult(rounded, killed=False, fact_true=False, reason="LOW_FINITE_BIN_STILL_POSSIBLE")

    if metric == "high" and bin_kind == "open_shoulder":
        if lower is None:
            raise ValueError("upper high shoulder requires lower threshold")
        if rounded >= lower:
            return BoundaryResult(rounded, killed=False, fact_true=True, reason="UPPER_HIGH_SHOULDER_TRUE")
        return BoundaryResult(rounded, killed=False, fact_true=False, reason="UPPER_HIGH_SHOULDER_NOT_YET_TRUE")

    if metric == "low" and bin_kind == "open_shoulder":
        if upper is None:
            raise ValueError("lower low shoulder requires upper threshold")
        if rounded <= upper:
            return BoundaryResult(rounded, killed=False, fact_true=True, reason="LOWER_LOW_SHOULDER_TRUE")
        return BoundaryResult(rounded, killed=False, fact_true=False, reason="LOWER_LOW_SHOULDER_NOT_YET_TRUE")

    raise ValueError(f"unsupported boundary configuration: {metric}/{bin_kind}")
