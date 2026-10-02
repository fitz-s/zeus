"""Forecast temporal authority checks for EDLI redemption."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from src.data.forecast_target_contract import OPENDATA_MAX_STEP_HOURS


ForecastCompletenessStatus = Literal["COMPLETE", "PARTIAL_ALLOWED", "PARTIAL_BLOCKED"]

# Spine members a posterior needs unless its provenance certifies a complete
# carrier set: the adapter's non-source-clock ``len(models) < 3`` floor.
LEGACY_SPINE_MIN_MODELS = 3


def certified_carrier_ids(fusion: object) -> tuple[str, ...]:
    """Carrier ids a ``bayes_precision_fusion`` provenance certifies complete.

    Certified means decorrelated_providers_complete, or served >= expected > 0,
    with the carriers named (raw_model_forecast_ids, else the current_value_serving
    ids). Anything else, including unreadable fields, certifies nothing.
    """

    if not isinstance(fusion, Mapping):
        return ()
    complete = fusion.get("decorrelated_providers_complete") in (True, 1)
    if not complete:
        try:
            served = int(fusion.get("decorrelated_providers_served") or 0)
            expected = int(fusion.get("decorrelated_providers_expected") or 0)
        except (TypeError, ValueError):
            served = expected = 0
        complete = expected > 0 and served >= expected
    if not complete:
        return ()
    raw_ids = fusion.get("raw_model_forecast_ids")
    if isinstance(raw_ids, list):
        unique_ids = {str(value) for value in raw_ids if value not in (None, "")}
        if unique_ids:
            return tuple(sorted(unique_ids))
    serving = fusion.get("current_value_serving")
    if isinstance(serving, Mapping):
        unique_ids = {
            str(details.get("raw_model_forecast_id"))
            for details in serving.values()
            if isinstance(details, Mapping) and details.get("raw_model_forecast_id") not in (None, "")
        }
        if unique_ids:
            return tuple(sorted(unique_ids))
    return ()


def spine_member_floor(fusion: object) -> int:
    """A certified carrier set sets its own floor; anything else keeps the legacy one.

    The certificate only lowers the floor, so it never rejects a posterior the
    legacy floor admitted.
    """

    certified = len(certified_carrier_ids(fusion))
    return min(LEGACY_SPINE_MIN_MODELS, certified) if certified else LEGACY_SPINE_MIN_MODELS


def posterior_admits_spine_members(
    conn: sqlite3.Connection,
    *,
    posterior_id: object,
    member_count: int,
) -> bool:
    """Whether ``member_count`` meets the floor of the posterior ``posterior_id``.

    The posterior's provenance is read only below the legacy floor, where its
    certificate can matter. An absent or unreadable certificate keeps the
    legacy floor (unknown authority fails closed).
    """

    if member_count >= LEGACY_SPINE_MIN_MODELS:
        return True
    if member_count <= 0 or posterior_id is None:
        return False
    try:
        row = conn.execute(
            "SELECT json_extract(provenance_json, '$.bayes_precision_fusion')"
            " FROM forecast_posteriors WHERE posterior_id = ?",
            (posterior_id,),
        ).fetchone()
        fusion = json.loads(row[0]) if row is not None and row[0] else None
    except (sqlite3.Error, TypeError, ValueError):
        fusion = None
    return member_count >= spine_member_floor(fusion)


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
    """Expected OpenData step grid per cycle, capped at the 5-day fetch horizon.

    5-day cap (2026-05-29): Polymarket retired markets beyond 5 days, so Zeus fetches
    only the 3h-native grid through OPENDATA_MAX_STEP_HOURS (144h). The former 0/12
    long tail (150-360h) is no longer fetched, so this fallback must not demand it —
    otherwise the fallback completeness path would be permanently fail-closed. All
    four cycles now share the same 0..144h grid.
    """
    if cycle_hour in {0, 12, 6, 18}:
        return tuple(range(0, OPENDATA_MAX_STEP_HOURS + 1, 3))
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
