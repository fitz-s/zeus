"""No-bypass reader for replacement forecast shadow posterior bundles."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    ReplacementForecastReadinessDecision,
)


HIGH_DATA_VERSION = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1"
LOW_DATA_VERSION = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastPosteriorBundle:
    posterior_id: int
    city: str
    target_date: str
    temperature_metric: str
    source_id: str
    product_id: str
    data_version: str
    q: Mapping[str, float]
    q_lcb: Mapping[str, float] | None
    posterior_method: str
    source_cycle_time: str
    source_available_at: str
    computed_at: str
    baseline_source_run_id: str
    dependency_json: Mapping[str, Any]
    provenance_json: Mapping[str, Any]
    trade_authority_status: str

    def __post_init__(self) -> None:
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id), ("data_version", self.data_version)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full product identity")
        if self.trade_authority_status not in {"SHADOW_ONLY", "SHADOW_VETO_ONLY"}:
            raise ValueError("replacement posterior bundle must remain shadow-only")
        _normalize_probability_map(self.q, field_name="q")
        if self.q_lcb is not None:
            _normalize_probability_map(self.q_lcb, field_name="q_lcb", require_sum=False)
            if set(self.q_lcb) != set(self.q):
                raise ValueError("q_lcb keys must exactly match q keys")


@dataclass(frozen=True)
class ReplacementForecastBundleReadResult:
    status: str
    reason_code: str
    bundle: ReplacementForecastPosteriorBundle | None = None

    @property
    def ok(self) -> bool:
        return self.status == READY_STATUS and self.bundle is not None


def _date_text(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        date.fromisoformat(value)
        return value
    raise ValueError("target_date must be a date or ISO date string")


def _metric(value: str) -> str:
    if value not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    return value


def _data_version_for_metric(metric: str) -> str:
    return HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION


def _json_mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be JSON text")
    parsed = json.loads(value)
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{field_name} must decode to an object")
    return parsed


def _normalize_probability_map(value: Mapping[str, Any], *, field_name: str, require_sum: bool = True) -> dict[str, float]:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    cleaned: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        number = float(raw)
        if number < 0.0 or not math.isfinite(number):
            raise ValueError(f"{field_name} values must be non-negative finite numbers")
        cleaned[key] = number
    if require_sum and abs(sum(cleaned.values()) - 1.0) > 1e-9:
        raise ValueError(f"{field_name} must sum to 1")
    return cleaned


def _baseline_source_run_id(baseline_bundle: object | None) -> str | None:
    if baseline_bundle is None:
        return None
    evidence = getattr(baseline_bundle, "evidence", None)
    source_run_id = getattr(evidence, "source_run_id", None)
    if isinstance(source_run_id, str) and source_run_id:
        return source_run_id
    return None


def _baseline_source_run_id_from_readiness(readiness: ReplacementForecastReadinessDecision) -> str | None:
    baseline_dependency = _readiness_dependency_by_role(readiness, "baseline_b0")
    if baseline_dependency is None:
        return None
    source_run_id = baseline_dependency.get("source_run_id")
    return source_run_id if isinstance(source_run_id, str) and source_run_id else None


def _readiness_dependency_by_role(readiness: ReplacementForecastReadinessDecision, role: str) -> Mapping[str, Any] | None:
    dependencies = readiness.dependency_json.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    for item in dependencies:
        if isinstance(item, Mapping) and item.get("role") == role:
            return item
    return None


def _dependency_source_run_mismatch(
    *,
    readiness: ReplacementForecastReadinessDecision,
    posterior_dependency_json: Mapping[str, Any],
) -> bool:
    for role in ("baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor"):
        readiness_dependency = _readiness_dependency_by_role(readiness, role)
        if readiness_dependency is None:
            return True
        readiness_source_run_id = readiness_dependency.get("source_run_id")
        posterior_source_run_id = posterior_dependency_json.get(role)
        if not isinstance(readiness_source_run_id, str) or not readiness_source_run_id:
            return True
        if posterior_source_run_id != readiness_source_run_id:
            return True
    return False


def _parse_utc(value: str, *, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def read_replacement_forecast_bundle(
    conn: sqlite3.Connection,
    *,
    baseline_bundle: object | None,
    readiness: ReplacementForecastReadinessDecision,
    city: str,
    target_date: date | str,
    temperature_metric: str,
    decision_time: datetime | str,
    require_baseline_bundle: bool = True,
) -> ReplacementForecastBundleReadResult:
    """Read a derived replacement posterior only after B0 executable proof exists."""

    baseline_run_id = _baseline_source_run_id(baseline_bundle)
    if baseline_run_id is None and not require_baseline_bundle:
        baseline_run_id = _baseline_source_run_id_from_readiness(readiness)
    if baseline_run_id is None:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_BASELINE_EXECUTABLE_FORECAST_REQUIRED")
    if not isinstance(readiness, ReplacementForecastReadinessDecision):
        raise TypeError("readiness must be ReplacementForecastReadinessDecision")
    if readiness.status != READY_STATUS:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_READINESS_NOT_READY")

    metric = _metric(temperature_metric)
    target_date_text = _date_text(target_date)
    data_version = _data_version_for_metric(metric)
    decision_utc = decision_time if isinstance(decision_time, datetime) else datetime.fromisoformat(decision_time.replace("Z", "+00:00"))
    if decision_utc.tzinfo is None or decision_utc.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    decision_utc = decision_utc.astimezone(timezone.utc)

    row = conn.execute(
        """
        SELECT * FROM forecast_posteriors
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND product_id = ?
          AND data_version = ?
          AND training_allowed = 0
          AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
        ORDER BY computed_at DESC, posterior_id DESC
        LIMIT 1
        """,
        (city, target_date_text, metric, SOURCE_ID, PRODUCT_ID, data_version),
    ).fetchone()
    if row is None:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_MISSING")
    row_map = dict(row)
    if _parse_utc(str(row_map["source_available_at"]), field_name="source_available_at") > decision_utc:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_AFTER_DECISION_TIME")
    if _parse_utc(str(row_map["computed_at"]), field_name="computed_at") > decision_utc:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_COMPUTED_AFTER_DECISION_TIME")

    posterior_dependency = _readiness_dependency_by_role(readiness, "soft_anchor_posterior")
    if posterior_dependency is None or int(posterior_dependency.get("posterior_id") or -1) != int(row_map["posterior_id"]):
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_READINESS_MISMATCH")
    baseline_dependency = _readiness_dependency_by_role(readiness, "baseline_b0")
    if baseline_dependency is None or baseline_dependency.get("source_run_id") != baseline_run_id:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_BASELINE_READINESS_MISMATCH")

    dependency_json = _json_mapping(row_map["dependency_source_run_ids_json"], field_name="dependency_source_run_ids_json")
    if _dependency_source_run_mismatch(readiness=readiness, posterior_dependency_json=dependency_json):
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_DEPENDENCY_SOURCE_RUN_MISMATCH")

    q = _normalize_probability_map(_json_mapping(row_map["q_json"], field_name="q_json"), field_name="q")
    q_lcb_raw = _json_mapping(row_map["q_lcb_json"], field_name="q_lcb_json") if row_map.get("q_lcb_json") else None
    q_lcb = _normalize_probability_map(q_lcb_raw, field_name="q_lcb", require_sum=False) if q_lcb_raw is not None else None
    provenance = _json_mapping(row_map["provenance_json"], field_name="provenance_json")
    if not str(provenance.get("bin_topology_hash") or "").strip():
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_BIN_TOPOLOGY_HASH_MISSING")
    bundle = ReplacementForecastPosteriorBundle(
        posterior_id=int(row_map["posterior_id"]),
        city=str(row_map["city"]),
        target_date=str(row_map["target_date"]),
        temperature_metric=str(row_map["temperature_metric"]),
        source_id=str(row_map["source_id"]),
        product_id=str(row_map["product_id"]),
        data_version=str(row_map["data_version"]),
        q=q,
        q_lcb=q_lcb,
        posterior_method=str(row_map["posterior_method"]),
        source_cycle_time=str(row_map["source_cycle_time"]),
        source_available_at=str(row_map["source_available_at"]),
        computed_at=str(row_map["computed_at"]),
        baseline_source_run_id=baseline_run_id,
        dependency_json=dependency_json,
        provenance_json=provenance,
        trade_authority_status=str(row_map["trade_authority_status"]),
    )
    return ReplacementForecastBundleReadResult(READY_STATUS, "REPLACEMENT_POSTERIOR_READY", bundle)
