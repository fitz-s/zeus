"""Before/after report envelope for replacement forecast evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


REPORT_SCHEMA_VERSION = "replacement_forecast_before_after_v1"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


@dataclass(frozen=True)
class ReplacementForecastBeforeAfterRow:
    official_date: str
    city: str
    temperature_metric: str
    guardrail_bucket: str
    baseline_brier: float
    replacement_brier: float
    baseline_log_loss: float
    replacement_log_loss: float
    baseline_after_cost_pnl: float
    replacement_after_cost_pnl: float
    truth_authority: str = "VERIFIED"
    replay_status: str = "SCORED"

    def __post_init__(self) -> None:
        for field_name in ("official_date", "city", "guardrail_bucket", "truth_authority", "replay_status"):
            text = str(getattr(self, field_name) or "")
            if not text:
                raise ValueError(f"{field_name} is required")
            _reject_alias(text, field_name=field_name)
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")
        for field_name in (
            "baseline_brier",
            "replacement_brier",
            "baseline_log_loss",
            "replacement_log_loss",
            "baseline_after_cost_pnl",
            "replacement_after_cost_pnl",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")

    @property
    def official_scored(self) -> bool:
        return self.truth_authority == "VERIFIED" and self.replay_status == "SCORED"


@dataclass(frozen=True)
class ReplacementForecastBeforeAfterReport:
    schema_version: str
    status: str
    reason_codes: tuple[str, ...]
    official_days: int
    official_rows: int
    baseline_brier: float | None
    replacement_brier: float | None
    brier_delta: float | None
    baseline_log_loss: float | None
    replacement_log_loss: float | None
    log_loss_delta: float | None
    baseline_after_cost_pnl: float | None
    replacement_after_cost_pnl: float | None
    after_cost_delta: float | None
    bucket_regressions: dict[str, float]
    row_exclusion_count: int
    promotion_allowed: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "official_days": self.official_days,
            "official_rows": self.official_rows,
            "baseline_brier": self.baseline_brier,
            "replacement_brier": self.replacement_brier,
            "brier_delta": self.brier_delta,
            "baseline_log_loss": self.baseline_log_loss,
            "replacement_log_loss": self.replacement_log_loss,
            "log_loss_delta": self.log_loss_delta,
            "baseline_after_cost_pnl": self.baseline_after_cost_pnl,
            "replacement_after_cost_pnl": self.replacement_after_cost_pnl,
            "after_cost_delta": self.after_cost_delta,
            "bucket_regressions": dict(self.bucket_regressions),
            "row_exclusion_count": self.row_exclusion_count,
            "promotion_allowed": self.promotion_allowed,
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_replacement_forecast_before_after_report(
    rows: Iterable[ReplacementForecastBeforeAfterRow],
    *,
    min_official_days: int = 5,
    min_official_rows: int = 250,
) -> ReplacementForecastBeforeAfterReport:
    """Aggregate official multi-day before/after evidence without promotion power."""

    row_tuple = tuple(rows)
    official_rows = tuple(row for row in row_tuple if row.official_scored)
    official_days = len({row.official_date for row in official_rows})
    row_exclusion_count = len(row_tuple) - len(official_rows)
    reasons: list[str] = []
    if not official_rows:
        reasons.append("REPLACEMENT_BEFORE_AFTER_NO_OFFICIAL_SCORED_ROWS")
    if official_days < min_official_days:
        reasons.append("REPLACEMENT_BEFORE_AFTER_INSUFFICIENT_OFFICIAL_DAYS")
    if len(official_rows) < min_official_rows:
        reasons.append("REPLACEMENT_BEFORE_AFTER_INSUFFICIENT_OFFICIAL_ROWS")
    if row_exclusion_count:
        reasons.append("REPLACEMENT_BEFORE_AFTER_HAS_ROW_EXCLUSIONS")
    baseline_brier = _mean([row.baseline_brier for row in official_rows])
    replacement_brier = _mean([row.replacement_brier for row in official_rows])
    baseline_log_loss = _mean([row.baseline_log_loss for row in official_rows])
    replacement_log_loss = _mean([row.replacement_log_loss for row in official_rows])
    baseline_pnl = sum((row.baseline_after_cost_pnl for row in official_rows), 0.0) if official_rows else None
    replacement_pnl = sum((row.replacement_after_cost_pnl for row in official_rows), 0.0) if official_rows else None
    bucket_delta: dict[str, float] = {}
    for bucket in sorted({row.guardrail_bucket for row in official_rows}):
        bucket_rows = [row for row in official_rows if row.guardrail_bucket == bucket]
        delta = sum(row.replacement_after_cost_pnl - row.baseline_after_cost_pnl for row in bucket_rows)
        if delta < 0.0:
            bucket_delta[bucket] = delta
    if bucket_delta:
        reasons.append("REPLACEMENT_BEFORE_AFTER_BUCKET_REGRESSIONS_PRESENT")
    status = "REPORT_READY" if not reasons else "SHADOW_REPORT_ONLY"
    if not official_rows:
        status = "BLOCKED"
    return ReplacementForecastBeforeAfterReport(
        schema_version=REPORT_SCHEMA_VERSION,
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_BEFORE_AFTER_MULTI_DAY_REPORT_READY",)),
        official_days=official_days,
        official_rows=len(official_rows),
        baseline_brier=baseline_brier,
        replacement_brier=replacement_brier,
        brier_delta=None if baseline_brier is None or replacement_brier is None else replacement_brier - baseline_brier,
        baseline_log_loss=baseline_log_loss,
        replacement_log_loss=replacement_log_loss,
        log_loss_delta=None
        if baseline_log_loss is None or replacement_log_loss is None
        else replacement_log_loss - baseline_log_loss,
        baseline_after_cost_pnl=baseline_pnl,
        replacement_after_cost_pnl=replacement_pnl,
        after_cost_delta=None if baseline_pnl is None or replacement_pnl is None else replacement_pnl - baseline_pnl,
        bucket_regressions=bucket_delta,
        row_exclusion_count=row_exclusion_count,
        promotion_allowed=False,
    )
