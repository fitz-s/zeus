"""Producer-readiness builder for future target-local-date forecast coverage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.data.forecast_target_contract import ForecastTargetScope
from src.state.readiness_repo import write_readiness_state
from src.state.source_run_coverage_repo import get_latest_source_run_coverage
from src.types.metric_identity import MetricIdentity

PRODUCER_READINESS_STRATEGY_KEY = "producer_readiness"


@dataclass(frozen=True)
class ProducerReadinessDecision:
    readiness_id: str
    status: str
    reason_codes: tuple[str, ...]
    coverage_id: str | None


def _stable_readiness_id(*parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    return f"producer_readiness:{digest}"


def _metric_identity(temperature_metric: str) -> MetricIdentity:
    return MetricIdentity.from_raw(temperature_metric)


def _coverage_status_to_readiness(coverage: dict[str, Any]) -> tuple[str, tuple[str, ...], str | None]:
    completeness = coverage.get("completeness_status")
    readiness = coverage.get("readiness_status")
    row_reason = coverage.get("reason_code")

    if completeness == "COMPLETE" and readiness == "LIVE_ELIGIBLE":
        if not coverage.get("expires_at"):
            return "BLOCKED", ("PRODUCER_COVERAGE_EXPIRY_MISSING",), None
        return "LIVE_ELIGIBLE", ("PRODUCER_COVERAGE_READY",), str(coverage["expires_at"])
    if readiness == "SHADOW_ONLY":
        return "SHADOW_ONLY", (row_reason or "PRODUCER_COVERAGE_SHADOW_ONLY",), None
    if completeness == "HORIZON_OUT_OF_RANGE":
        return "BLOCKED", (row_reason or "SOURCE_RUN_HORIZON_OUT_OF_RANGE",), None
    if completeness == "NOT_RELEASED":
        return "BLOCKED", (row_reason or "SOURCE_RUN_NOT_RELEASED",), None
    if completeness == "MISSING":
        return "BLOCKED", (row_reason or "NO_FUTURE_TARGET_DATE_COVERAGE",), None
    if completeness == "PARTIAL":
        return "BLOCKED", (row_reason or "FUTURE_TARGET_DATE_COVERAGE_PARTIAL",), None
    if readiness == "UNKNOWN_BLOCKED":
        return "UNKNOWN_BLOCKED", (row_reason or "PRODUCER_COVERAGE_UNKNOWN_BLOCKED",), None
    return "BLOCKED", (row_reason or "PRODUCER_COVERAGE_BLOCKED",), None


def build_producer_readiness_for_scope(
    conn: sqlite3.Connection,
    *,
    scope: ForecastTargetScope,
    source_id: str,
    source_transport: str,
    track: str,
    computed_at: datetime,
) -> ProducerReadinessDecision:
    coverage = get_latest_source_run_coverage(
        conn,
        city_id=scope.city_id,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        source_id=source_id,
        source_transport=source_transport,
        data_version=scope.data_version,
    )
    identity = _metric_identity(scope.temperature_metric)
    if coverage is None:
        readiness_id = _stable_readiness_id(
            "missing",
            source_id,
            source_transport,
            track,
            scope.city_id,
            scope.city_timezone,
            scope.target_local_date,
            scope.temperature_metric,
            scope.data_version,
        )
        write_readiness_state(
            conn,
            readiness_id=readiness_id,
            scope_type="city_metric",
            status="BLOCKED",
            computed_at=computed_at,
            city_id=scope.city_id,
            city=scope.city_name,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            physical_quantity=identity.physical_quantity,
            observation_field=identity.observation_field,
            data_version=scope.data_version,
            source_id=source_id,
            track=track,
            strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
            reason_codes_json=["NO_FUTURE_TARGET_DATE_COVERAGE"],
            dependency_json={
                "coverage_id": None,
                "source_run_id": None,
                "source_transport": source_transport,
                "required_step_hours": scope.required_step_hours,
            },
            provenance_json={
                "contract": "LiveEntryForecastTargetContract.v1",
                "market_refs": scope.market_refs,
            },
        )
        return ProducerReadinessDecision(
            readiness_id=readiness_id,
            status="BLOCKED",
            reason_codes=("NO_FUTURE_TARGET_DATE_COVERAGE",),
            coverage_id=None,
        )

    status, reason_codes, expires_at = _coverage_status_to_readiness(coverage)
    coverage_id = str(coverage["coverage_id"])
    readiness_id = f"producer_readiness:{coverage_id}"
    write_readiness_state(
        conn,
        readiness_id=readiness_id,
        scope_type="city_metric",
        status=status,
        computed_at=computed_at,
        city_id=str(coverage["city_id"]),
        city=str(coverage["city"]),
        city_timezone=str(coverage["city_timezone"]),
        target_local_date=str(coverage["target_local_date"]),
        temperature_metric=str(coverage["temperature_metric"]),
        physical_quantity=str(coverage["physical_quantity"]),
        observation_field=str(coverage["observation_field"]),
        data_version=str(coverage["data_version"]),
        source_id=str(coverage["source_id"]),
        track=str(coverage["track"]),
        source_run_id=str(coverage["source_run_id"]),
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        reason_codes_json=list(reason_codes),
        expires_at=expires_at,
        dependency_json={
            "coverage_id": coverage_id,
            "source_run_id": coverage["source_run_id"],
            "source_transport": coverage["source_transport"],
            "release_calendar_key": coverage["release_calendar_key"],
            "expected_steps_json": coverage["expected_steps_json"],
            "observed_steps_json": coverage["observed_steps_json"],
        },
        provenance_json={
            "contract": "LiveEntryForecastTargetContract.v1",
            "market_refs": scope.market_refs,
        },
    )
    return ProducerReadinessDecision(
        readiness_id=readiness_id,
        status=status,
        reason_codes=reason_codes,
        coverage_id=coverage_id,
    )
