"""Forecast temporal authority checks for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


ForecastCompletenessStatus = Literal["COMPLETE", "PARTIAL_ALLOWED", "PARTIAL_BLOCKED"]


@dataclass(frozen=True)
class ForecastSnapshotEvidence:
    cycle_hour: int
    target_step: int
    expected_steps: tuple[int, ...]
    observed_steps: tuple[int, ...]
    observed_members: int
    expected_members: int
    min_members_floor: int
    source_available_at: str
    issue_time: str
    executable_reader_live_eligible: bool
    required_fields_present: bool = True


@dataclass(frozen=True)
class ForecastCompletenessResult:
    status: ForecastCompletenessStatus
    live_eligible: bool
    reason: str
    required_steps: tuple[int, ...]


def expected_steps_for_cycle(cycle_hour: int) -> tuple[int, ...]:
    if cycle_hour in {0, 12}:
        return tuple(list(range(0, 145, 3)) + list(range(150, 361, 6)))
    if cycle_hour in {6, 18}:
        return tuple(range(0, 145, 3))
    raise ValueError(f"unsupported ECMWF cycle_hour {cycle_hour!r}")


def classify_forecast_snapshot(evidence: ForecastSnapshotEvidence) -> ForecastCompletenessResult:
    source_available = _parse_utc(evidence.source_available_at, "source_available_at")
    issue_time = _parse_utc(evidence.issue_time, "issue_time")
    if source_available <= issue_time:
        return _blocked("issue_time_cannot_authorize_live", evidence)
    if not evidence.required_fields_present:
        return _blocked("required_fields_missing", evidence)
    try:
        required_steps = evidence.expected_steps or expected_steps_for_cycle(evidence.cycle_hour)
    except ValueError:
        return _blocked("EXPECTED_STEPS_UNKNOWN", evidence)
    if not required_steps:
        return _blocked("EXPECTED_STEPS_UNKNOWN", evidence)
    if evidence.target_step not in required_steps:
        return _blocked("target_step_not_required_for_cycle", evidence, required_steps)
    if not set(required_steps).issubset(set(evidence.observed_steps)):
        return _blocked("required_steps_missing", evidence, required_steps)
    if evidence.observed_members >= evidence.expected_members and evidence.executable_reader_live_eligible:
        return ForecastCompletenessResult(
            status="COMPLETE",
            live_eligible=True,
            reason="complete_executable_reader_live_eligible",
            required_steps=tuple(required_steps),
        )
    if evidence.observed_members >= evidence.min_members_floor:
        return ForecastCompletenessResult(
            status="PARTIAL_ALLOWED",
            live_eligible=False,
            reason="partial_evidence_only",
            required_steps=tuple(required_steps),
        )
    return _blocked("observed_members_below_floor", evidence, required_steps)


def assert_forecast_available_for_decision(
    *,
    source_available_at: str,
    decision_time: str | datetime,
) -> None:
    available = _parse_utc(source_available_at, "source_available_at")
    decision = _parse_utc(decision_time, "decision_time") if isinstance(decision_time, str) else decision_time
    if decision.tzinfo is None:
        raise ValueError("decision_time must include timezone")
    if available > decision.astimezone(timezone.utc):
        raise ValueError("forecast source availability is after decision_time")


def _blocked(
    reason: str,
    evidence: ForecastSnapshotEvidence,
    required_steps: tuple[int, ...] | None = None,
) -> ForecastCompletenessResult:
    return ForecastCompletenessResult(
        status="PARTIAL_BLOCKED",
        live_eligible=False,
        reason=reason,
        required_steps=tuple(required_steps or evidence.expected_steps),
    )


def _parse_utc(value: str | datetime, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include timezone")
    return parsed.astimezone(timezone.utc)
