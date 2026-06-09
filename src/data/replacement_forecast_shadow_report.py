"""Shadow daily and truth-gap reports for replacement forecast evaluation.

The report is a read-model payload only. It preserves official/provisional/
missing/quarantined row status and row-exclusion reasons so forecast-skill or
veto evidence cannot silently promote from incomplete settlement truth.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal


TruthAuthority = Literal["VERIFIED", "PROVISIONAL", "MISSING", "QUARANTINED"]
Metric = Literal["high", "low"]
REPORT_STATUS_PASS = "SHADOW_REPORT_COMPLETE"
REPORT_STATUS_SHADOW_ONLY = "SHADOW_ONLY"
REPORT_STATUS_BLOCKED = "BLOCKED"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
_SCORABLE_TRUTH = "VERIFIED"


@dataclass(frozen=True)
class ReplacementForecastExpectedRow:
    city: str
    target_date: str
    temperature_metric: Metric
    condition_id: str
    token_id: str
    expected_reason: str = "expected_replacement_shadow_row"

    def __post_init__(self) -> None:
        for field_name in ("city", "target_date", "condition_id", "token_id", "expected_reason"):
            value = str(getattr(self, field_name) or "")
            if not value:
                raise ValueError(f"{field_name} is required")
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full replacement identity")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")

    @property
    def row_key(self) -> str:
        return "|".join((self.city, self.target_date, self.temperature_metric, self.condition_id, self.token_id))


@dataclass(frozen=True)
class ReplacementForecastShadowReportRow:
    city: str
    target_date: str
    temperature_metric: Metric
    market_snapshot_id: str
    condition_id: str
    token_id: str
    baseline_direction: str
    replacement_direction: str
    veto_applied: bool
    truth_authority: TruthAuthority
    guardrail_bucket: str = "standard"
    replay_status: str = "SCORED"
    replacement_delta_after_cost_pnl: float | None = None
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("city", "target_date", "market_snapshot_id", "condition_id", "token_id", "baseline_direction", "replacement_direction", "guardrail_bucket", "replay_status"):
            value = str(getattr(self, field_name) or "")
            if not value:
                raise ValueError(f"{field_name} is required")
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full replacement identity")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")
        if self.truth_authority not in {"VERIFIED", "PROVISIONAL", "MISSING", "QUARANTINED"}:
            raise ValueError("truth_authority is unsupported")
        if self.truth_authority != _SCORABLE_TRUTH and not self.exclusion_reason:
            raise ValueError("non-VERIFIED replacement report rows require exclusion_reason")
        if self.replay_status != "SCORED" and not self.exclusion_reason:
            raise ValueError("non-SCORED replacement report rows require exclusion_reason")

    @property
    def row_key(self) -> str:
        return "|".join((self.city, self.target_date, self.temperature_metric, self.condition_id, self.token_id))

    @property
    def is_official_scored(self) -> bool:
        return self.truth_authority == _SCORABLE_TRUTH and self.replay_status == "SCORED"

    @property
    def effective_exclusion_reason(self) -> str | None:
        if self.is_official_scored:
            return None
        if self.exclusion_reason:
            return self.exclusion_reason
        if self.truth_authority != _SCORABLE_TRUTH:
            return f"truth_authority_{self.truth_authority.lower()}"
        return f"replay_status_{self.replay_status.lower()}"

    def as_dict(self) -> dict[str, object]:
        return {
            "city": self.city,
            "target_date": self.target_date,
            "temperature_metric": self.temperature_metric,
            "market_snapshot_id": self.market_snapshot_id,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "baseline_direction": self.baseline_direction,
            "replacement_direction": self.replacement_direction,
            "veto_applied": self.veto_applied,
            "truth_authority": self.truth_authority,
            "guardrail_bucket": self.guardrail_bucket,
            "replay_status": self.replay_status,
            "replacement_delta_after_cost_pnl": self.replacement_delta_after_cost_pnl,
            "exclusion_reason": self.effective_exclusion_reason,
            "official_scored": self.is_official_scored,
            "row_key": self.row_key,
        }


@dataclass(frozen=True)
class ReplacementForecastRowExclusion:
    row_key: str
    city: str
    target_date: str
    temperature_metric: str
    truth_authority: str
    replay_status: str
    exclusion_reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row_key": self.row_key,
            "city": self.city,
            "target_date": self.target_date,
            "temperature_metric": self.temperature_metric,
            "truth_authority": self.truth_authority,
            "replay_status": self.replay_status,
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass(frozen=True)
class ReplacementForecastShadowReport:
    status: str
    reason_codes: tuple[str, ...]
    total_rows: int
    expected_rows: int
    absent_expected_rows: int
    official_scored_rows: int
    provisional_rows: int
    missing_rows: int
    quarantined_rows: int
    blocked_replay_rows: int
    veto_count: int
    net_official_after_cost_delta: float
    truth_authority_counts: dict[str, int]
    exclusion_reason_counts: dict[str, int]
    guardrail_bucket_counts: dict[str, int]
    row_exclusions: tuple[ReplacementForecastRowExclusion, ...]

    @property
    def promotion_allowed(self) -> bool:
        return False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "total_rows": self.total_rows,
            "expected_rows": self.expected_rows,
            "absent_expected_rows": self.absent_expected_rows,
            "official_scored_rows": self.official_scored_rows,
            "provisional_rows": self.provisional_rows,
            "missing_rows": self.missing_rows,
            "quarantined_rows": self.quarantined_rows,
            "blocked_replay_rows": self.blocked_replay_rows,
            "veto_count": self.veto_count,
            "net_official_after_cost_delta": self.net_official_after_cost_delta,
            "truth_authority_counts": dict(self.truth_authority_counts),
            "exclusion_reason_counts": dict(self.exclusion_reason_counts),
            "guardrail_bucket_counts": dict(self.guardrail_bucket_counts),
            "row_exclusions": [item.as_dict() for item in self.row_exclusions],
            "promotion_allowed": False,
        }


def build_replacement_forecast_shadow_report(
    rows: Iterable[ReplacementForecastShadowReportRow],
    *,
    expected_rows: Iterable[ReplacementForecastExpectedRow] | None = None,
) -> ReplacementForecastShadowReport:
    row_tuple = tuple(rows)
    expected_tuple = tuple(expected_rows or ())
    if not row_tuple:
        absent_expected = len(expected_tuple)
        return ReplacementForecastShadowReport(
            status=REPORT_STATUS_BLOCKED,
            reason_codes=(
                "REPLACEMENT_SHADOW_REPORT_NO_ROWS",
                *(() if absent_expected == 0 else ("REPLACEMENT_SHADOW_REPORT_HAS_ABSENT_EXPECTED_ROWS",)),
            ),
            total_rows=0,
            expected_rows=len(expected_tuple),
            absent_expected_rows=absent_expected,
            official_scored_rows=0,
            provisional_rows=0,
            missing_rows=absent_expected,
            quarantined_rows=0,
            blocked_replay_rows=0,
            veto_count=0,
            net_official_after_cost_delta=0.0,
            truth_authority_counts={} if absent_expected == 0 else {"MISSING": absent_expected},
            exclusion_reason_counts={} if absent_expected == 0 else {"expected_row_absent_from_shadow_report": absent_expected},
            guardrail_bucket_counts={},
            row_exclusions=tuple(
                ReplacementForecastRowExclusion(
                    row_key=row.row_key,
                    city=row.city,
                    target_date=row.target_date,
                    temperature_metric=row.temperature_metric,
                    truth_authority="MISSING",
                    replay_status="NOT_RUN",
                    exclusion_reason="expected_row_absent_from_shadow_report",
                )
                for row in expected_tuple
            ),
        )
    duplicate_keys = [key for key, count in Counter(row.row_key for row in row_tuple).items() if count > 1]
    if duplicate_keys:
        raise ValueError("replacement shadow report rows must be unique by city/date/metric/condition/token")
    duplicate_expected = [key for key, count in Counter(row.row_key for row in expected_tuple).items() if count > 1]
    if duplicate_expected:
        raise ValueError("replacement expected rows must be unique by city/date/metric/condition/token")

    truth_counts = Counter(row.truth_authority for row in row_tuple)
    bucket_counts = Counter(row.guardrail_bucket for row in row_tuple)
    exclusions: list[ReplacementForecastRowExclusion] = []
    exclusion_counts: Counter[str] = Counter()
    for row in row_tuple:
        if not isinstance(row, ReplacementForecastShadowReportRow):
            raise TypeError("rows must contain ReplacementForecastShadowReportRow objects")
        reason = row.effective_exclusion_reason
        if reason is not None:
            exclusion_counts[reason] += 1
            exclusions.append(
                ReplacementForecastRowExclusion(
                    row_key=row.row_key,
                    city=row.city,
                    target_date=row.target_date,
                    temperature_metric=row.temperature_metric,
                    truth_authority=row.truth_authority,
                    replay_status=row.replay_status,
                    exclusion_reason=reason,
                )
            )
    present_keys = {row.row_key for row in row_tuple}
    absent_expected_rows = tuple(row for row in expected_tuple if row.row_key not in present_keys)
    for row in absent_expected_rows:
        truth_counts["MISSING"] += 1
        exclusion_counts["expected_row_absent_from_shadow_report"] += 1
        exclusions.append(
            ReplacementForecastRowExclusion(
                row_key=row.row_key,
                city=row.city,
                target_date=row.target_date,
                temperature_metric=row.temperature_metric,
                truth_authority="MISSING",
                replay_status="NOT_RUN",
                exclusion_reason="expected_row_absent_from_shadow_report",
            )
        )

    official_rows = tuple(row for row in row_tuple if row.is_official_scored)
    net_delta = sum(float(row.replacement_delta_after_cost_pnl or 0.0) for row in official_rows)
    blocked_replay_rows = sum(1 for row in row_tuple if row.replay_status != "SCORED")
    reasons: list[str] = []
    if truth_counts.get("PROVISIONAL", 0):
        reasons.append("REPLACEMENT_SHADOW_REPORT_HAS_PROVISIONAL_TRUTH")
    if truth_counts.get("MISSING", 0):
        reasons.append("REPLACEMENT_SHADOW_REPORT_HAS_MISSING_TRUTH")
    if truth_counts.get("QUARANTINED", 0):
        reasons.append("REPLACEMENT_SHADOW_REPORT_HAS_QUARANTINED_TRUTH")
    if absent_expected_rows:
        reasons.append("REPLACEMENT_SHADOW_REPORT_HAS_ABSENT_EXPECTED_ROWS")
    if blocked_replay_rows:
        reasons.append("REPLACEMENT_SHADOW_REPORT_HAS_BLOCKED_REPLAY_ROWS")
    if not official_rows:
        reasons.append("REPLACEMENT_SHADOW_REPORT_NO_OFFICIAL_SCORED_ROWS")

    status = REPORT_STATUS_PASS if not reasons else REPORT_STATUS_SHADOW_ONLY
    if not official_rows:
        status = REPORT_STATUS_BLOCKED
    return ReplacementForecastShadowReport(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_SHADOW_REPORT_OFFICIAL_ROWS_COMPLETE",)),
        total_rows=len(row_tuple),
        expected_rows=len(expected_tuple) if expected_tuple else len(row_tuple),
        absent_expected_rows=len(absent_expected_rows),
        official_scored_rows=len(official_rows),
        provisional_rows=truth_counts.get("PROVISIONAL", 0),
        missing_rows=truth_counts.get("MISSING", 0),
        quarantined_rows=truth_counts.get("QUARANTINED", 0),
        blocked_replay_rows=blocked_replay_rows,
        veto_count=sum(1 for row in row_tuple if row.veto_applied),
        net_official_after_cost_delta=net_delta,
        truth_authority_counts=dict(sorted(truth_counts.items())),
        exclusion_reason_counts=dict(sorted(exclusion_counts.items())),
        guardrail_bucket_counts=dict(sorted(bucket_counts.items())),
        row_exclusions=tuple(exclusions),
    )
