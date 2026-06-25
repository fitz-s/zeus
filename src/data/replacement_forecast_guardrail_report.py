"""Guardrail-bucket blocked reports for replacement forecast replay evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal


AxisName = Literal["guardrail_bucket", "city", "temperature_metric"]
DEFAULT_AXES: tuple[AxisName, ...] = ("guardrail_bucket", "city", "temperature_metric")


@dataclass(frozen=True)
class ReplacementForecastGuardrailReplayRow:
    city: str
    temperature_metric: Literal["high", "low"]
    guardrail_bucket: str
    replay_status: str
    replacement_delta_after_cost_pnl: float
    veto_applied: bool
    baseline_after_cost_pnl: float
    replacement_after_cost_pnl: float
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("city", "guardrail_bucket", "replay_status"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")


@dataclass(frozen=True)
class ReplacementForecastGuardrailBucket:
    axis: AxisName
    value: str
    scored_rows: int
    blocked_rows: int
    veto_count: int
    avoided_loss_pnl: float
    veto_regret_pnl: float
    net_delta_after_cost_pnl: float
    mean_delta_after_cost_pnl: float | None
    status: str
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "axis": self.axis,
            "value": self.value,
            "scored_rows": self.scored_rows,
            "blocked_rows": self.blocked_rows,
            "veto_count": self.veto_count,
            "avoided_loss_pnl": self.avoided_loss_pnl,
            "veto_regret_pnl": self.veto_regret_pnl,
            "net_delta_after_cost_pnl": self.net_delta_after_cost_pnl,
            "mean_delta_after_cost_pnl": self.mean_delta_after_cost_pnl,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ReplacementForecastGuardrailReport:
    status: str
    reason_codes: tuple[str, ...]
    total_rows: int
    scored_rows: int
    blocked_rows: int
    net_delta_after_cost_pnl: float
    veto_avoided_loss_pnl: float
    veto_regret_pnl: float
    unresolved_regression_clusters: tuple[ReplacementForecastGuardrailBucket, ...]
    buckets: tuple[ReplacementForecastGuardrailBucket, ...]

    @property
    def promotion_allowed(self) -> bool:
        return False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "total_rows": self.total_rows,
            "scored_rows": self.scored_rows,
            "blocked_rows": self.blocked_rows,
            "net_delta_after_cost_pnl": self.net_delta_after_cost_pnl,
            "veto_avoided_loss_pnl": self.veto_avoided_loss_pnl,
            "veto_regret_pnl": self.veto_regret_pnl,
            "unresolved_regression_clusters": [bucket.as_dict() for bucket in self.unresolved_regression_clusters],
            "buckets": [bucket.as_dict() for bucket in self.buckets],
            "promotion_allowed": False,
        }


def _axis_value(row: ReplacementForecastGuardrailReplayRow, axis: AxisName) -> str:
    if axis == "guardrail_bucket":
        return row.guardrail_bucket
    if axis == "city":
        return row.city
    if axis == "temperature_metric":
        return row.temperature_metric
    raise ValueError(f"unsupported guardrail axis: {axis}")


def _bucket(axis: AxisName, value: str, rows: tuple[ReplacementForecastGuardrailReplayRow, ...], *, min_scored_rows: int) -> ReplacementForecastGuardrailBucket:
    scored = tuple(row for row in rows if row.replay_status == "SCORED")
    blocked = len(rows) - len(scored)
    net_delta = sum(row.replacement_delta_after_cost_pnl for row in scored)
    avoided = sum(row.replacement_delta_after_cost_pnl for row in scored if row.veto_applied and row.replacement_delta_after_cost_pnl > 0.0)
    regret = -sum(row.replacement_delta_after_cost_pnl for row in scored if row.veto_applied and row.replacement_delta_after_cost_pnl < 0.0)
    reasons: list[str] = []
    if blocked:
        reasons.append("REPLACEMENT_GUARDRAIL_BUCKET_HAS_BLOCKED_REPLAY_ROWS")
    if len(scored) < min_scored_rows:
        reasons.append("REPLACEMENT_GUARDRAIL_BUCKET_INSUFFICIENT_ROWS")
    if net_delta < 0.0:
        reasons.append("REPLACEMENT_GUARDRAIL_BUCKET_NEGATIVE_AFTER_COST_DELTA")
    status = "PASS" if not reasons else ("REGRESSION" if net_delta < 0.0 and scored else "BLOCKED")
    return ReplacementForecastGuardrailBucket(
        axis=axis,
        value=value,
        scored_rows=len(scored),
        blocked_rows=blocked,
        veto_count=sum(1 for row in scored if row.veto_applied),
        avoided_loss_pnl=avoided,
        veto_regret_pnl=regret,
        net_delta_after_cost_pnl=net_delta,
        mean_delta_after_cost_pnl=(net_delta / len(scored)) if scored else None,
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_GUARDRAIL_BUCKET_PASS",)),
    )


def build_replacement_forecast_guardrail_report(
    rows: Iterable[ReplacementForecastGuardrailReplayRow],
    *,
    axes: tuple[AxisName, ...] = DEFAULT_AXES,
    min_scored_rows_per_bucket: int = 2,
) -> ReplacementForecastGuardrailReport:
    """Build a bucketed blocked report that cannot hide regression clusters."""

    if min_scored_rows_per_bucket <= 0:
        raise ValueError("min_scored_rows_per_bucket must be positive")
    row_tuple = tuple(rows)
    if not row_tuple:
        return ReplacementForecastGuardrailReport(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_GUARDRAIL_REPORT_NO_ROWS",),
            total_rows=0,
            scored_rows=0,
            blocked_rows=0,
            net_delta_after_cost_pnl=0.0,
            veto_avoided_loss_pnl=0.0,
            veto_regret_pnl=0.0,
            unresolved_regression_clusters=(),
            buckets=(),
        )
    groups: dict[tuple[AxisName, str], list[ReplacementForecastGuardrailReplayRow]] = defaultdict(list)
    for row in row_tuple:
        if not isinstance(row, ReplacementForecastGuardrailReplayRow):
            raise TypeError("rows must contain ReplacementForecastGuardrailReplayRow objects")
        for axis in axes:
            groups[(axis, _axis_value(row, axis))].append(row)
    buckets = tuple(
        _bucket(axis, value, tuple(group_rows), min_scored_rows=min_scored_rows_per_bucket)
        for (axis, value), group_rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1]))
    )
    scored_rows = tuple(row for row in row_tuple if row.replay_status == "SCORED")
    blocked_rows = len(row_tuple) - len(scored_rows)
    net_delta = sum(row.replacement_delta_after_cost_pnl for row in scored_rows)
    avoided = sum(row.replacement_delta_after_cost_pnl for row in scored_rows if row.veto_applied and row.replacement_delta_after_cost_pnl > 0.0)
    regret = -sum(row.replacement_delta_after_cost_pnl for row in scored_rows if row.veto_applied and row.replacement_delta_after_cost_pnl < 0.0)
    regressions = tuple(bucket for bucket in buckets if bucket.status == "REGRESSION")
    reasons: list[str] = []
    if blocked_rows:
        reasons.append("REPLACEMENT_GUARDRAIL_REPORT_HAS_BLOCKED_ROWS")
    if regressions:
        reasons.append("REPLACEMENT_GUARDRAIL_UNRESOLVED_REGRESSION_CLUSTERS")
    if not scored_rows:
        reasons.append("REPLACEMENT_GUARDRAIL_REPORT_NO_SCORED_ROWS")
    status = "PASS" if not reasons else "BLOCKED"
    if not scored_rows:
        status = "BLOCKED"
    return ReplacementForecastGuardrailReport(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_GUARDRAIL_REPORT_PASS",)),
        total_rows=len(row_tuple),
        scored_rows=len(scored_rows),
        blocked_rows=blocked_rows,
        net_delta_after_cost_pnl=net_delta,
        veto_avoided_loss_pnl=avoided,
        veto_regret_pnl=regret,
        unresolved_regression_clusters=regressions,
        buckets=buckets,
    )
