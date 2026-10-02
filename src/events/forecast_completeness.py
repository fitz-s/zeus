"""Forecast temporal authority checks for EDLI redemption."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from src.data.forecast_target_contract import OPENDATA_MAX_STEP_HOURS
from src.data.replacement_fusion_upgrade_trigger import DECORRELATED_PROVIDER_FAMILIES


ForecastCompletenessStatus = Literal["COMPLETE", "PARTIAL_ALLOWED", "PARTIAL_BLOCKED"]

# Spine members a posterior needs unless its provenance certifies a complete
# carrier set: the adapter's non-source-clock ``len(models) < 3`` floor.
LEGACY_SPINE_MIN_MODELS = 3


def certified_carrier_ids(fusion: object) -> tuple[str, ...]:
    """Carrier ids a ``bayes_precision_fusion`` provenance certifies complete.

    The carriers are the current_value_serving rows of the served providers,
    named as the materializer counted them for decorrelated_providers_served
    (see served_provider_models). raw_model_forecast_ids is the fusion's whole
    dependency set (anchor and older-cycle rows included), so it never names
    the carriers.

    Certified only when every one of these holds; anything else, including any
    type mismatch, certifies nothing:
    - decorrelated_providers_complete is the JSON boolean true (the flag alone
      never certifies);
    - decorrelated_providers_served and decorrelated_providers_expected are
      integers with served == expected > 0;
    - exactly ``expected`` served providers are named, each by a serving entry
      whose raw_model_forecast_id is a positive integer, all distinct.
    """

    if not isinstance(fusion, Mapping):
        return ()
    served = fusion.get("decorrelated_providers_served")
    expected = fusion.get("decorrelated_providers_expected")
    serving = fusion.get("current_value_serving")
    if (
        fusion.get("decorrelated_providers_complete") is not True
        or type(served) is not int
        or type(expected) is not int
        or not served == expected > 0
        or not isinstance(serving, Mapping)
    ):
        return ()
    providers = served_provider_models(fusion.get("source_clock_one_scheme"), serving)
    if providers is None or len(providers) != expected:
        return ()
    ids = [
        serving[model].get("raw_model_forecast_id")
        if isinstance(serving.get(model), Mapping) else None
        for model in providers
    ]
    if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
        return ()
    return tuple(str(value) for value in sorted(ids))


def served_provider_models(scheme: object, serving: Mapping) -> tuple[str, ...] | None:
    """The models the materializer counted as served providers, or None if unreadable.

    With a source_clock_one_scheme the providers are its configured_sources less
    its missing_sources; without one, each decorrelated provider family
    (DECORRELATED_PROVIDER_FAMILIES) represented in ``serving`` is one provider,
    and a family with two serving models names no single carrier. The anchor
    and station sources belong to no family, so they are never providers.
    """

    if scheme is None:
        by_family = [
            [model for model in serving if model in members]
            for members in DECORRELATED_PROVIDER_FAMILIES.values()
        ]
        if any(len(models) > 1 for models in by_family):
            return None
        return tuple(models[0] for models in by_family if models)
    if not isinstance(scheme, Mapping):
        return None
    configured = scheme.get("configured_sources")
    missing = scheme.get("missing_sources")
    if (
        not isinstance(configured, list)
        or not isinstance(missing, list)
        or any(type(model) is not str for model in (*configured, *missing))
        or len(set(configured)) != len(configured)
    ):
        return None
    return tuple(model for model in configured if model not in missing)


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
