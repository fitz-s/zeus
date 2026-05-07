# Created: 2026-03-26
# Last reused/audited: 2026-05-03
# Authority basis: Phase 4B audited GRIB ingest + PLAN_v4 Phase 6 SourceRunContext linkage.
# Lifecycle: created=2026-03-26; last_reviewed=2026-05-03; last_reused=2026-05-03
# Purpose: Audited GRIB→ensemble_snapshots_v2 ingestor (Phase 4B / task #53);
#          applies INV-14 identity spine and Law 5 causality gate before INSERT.
# Reuse: Requires extracted local-calendar-day JSON files under FIFTY_ONE_ROOT
#        for the chosen --track. Contract enforcement via
#        src/contracts/snapshot_ingest_contract.py::validate_snapshot_contract.
#        Post-audit 2026-04-24 (M1 closure): causality is NOT defaulted — any
#        pre-Phase-5B payload without causality fails with MISSING_CAUSALITY_FIELD.
#        Test contract: tests/test_ingest_grib_law5_antibody.py.
"""Audited GRIB-to-ensemble_snapshots_v2 ingestor (Phase 4B, task #53).

Reads pre-extracted local-calendar-day JSON files produced by
  51 source data/scripts/extract_tigge_mx2t6_localday_max.py
and writes canonical rows to ensemble_snapshots_v2.

Phase 4B: high track only (mx2t6_local_calendar_day_max_v1).
Phase 5 will reuse this pipeline with --track mn2t6_low.

Contract
--------
- Calls assert_data_version_allowed before every INSERT (NC-12).
- Calls validate_members_unit before every INSERT (pre-mortem Kelvin guard).
- All 7 Phase 2 provenance fields populated explicitly (INV-14).
- Uses commit_then_export so DT#1 (DB before JSON) is structural (INV-17).
- members_unit is the city's native unit ('degC' or 'degF'), never 'K'.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.forecast_calibration_domain import (
    ContractOutcomeDomain,
    ContractOutcomeDomainMismatch,
    ForecastToBinEvidence,
    derive_source_id_from_data_version,
)
from src.config import runtime_cities_by_name
from src.contracts.calibration_bins import grid_for_city
from src.contracts.ensemble_snapshot_provenance import (
    assert_data_version_allowed,
    validate_members_unit,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.contracts.snapshot_ingest_contract import validate_snapshot_contract
from src.contracts.tigge_snapshot_payload import ProvenanceViolation, TiggeSnapshotPayload
from src.state.canonical_write import commit_then_export
from src.state.db import get_world_connection
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"

_TRACK_CONFIGS: dict[str, dict[str, Any]] = {
    "mx2t6_high": {
        "metric": HIGH_LOCALDAY_MAX,
        "json_subdir": "tigge_ecmwf_ens_mx2t6_localday_max",
        "model_version": "ecmwf_ens",
    },
    "mn2t6_low": {
        "metric": LOW_LOCALDAY_MIN,
        "json_subdir": "tigge_ecmwf_ens_mn2t6_localday_min",
        "model_version": "ecmwf_ens",
    },
}

_UNIT_MAP = {"C": "degC", "F": "degF"}


@dataclass(frozen=True)
class SourceRunContext:
    source_id: str
    source_transport: str
    source_run_id: str
    release_calendar_key: str
    source_cycle_time: datetime
    source_release_time: datetime
    source_available_at: datetime | None = None

    def available_at_iso(self) -> str:
        return (self.source_available_at or self.source_release_time).isoformat()


def _normalize_unit(raw_unit: str) -> str:
    """Map manifest 'C'/'F' to validate_members_unit-accepted 'degC'/'degF'."""
    mapped = _UNIT_MAP.get(str(raw_unit).strip())
    if mapped is None:
        raise ValueError(f"Unknown manifest unit {raw_unit!r}; expected 'C' or 'F'")
    return mapped


def _manifest_hash_from_payload(payload: dict) -> str:
    """Content-addressed hash of the JSON record's provenance fields."""
    provenance_fields = {
        "data_version": payload.get("data_version"),
        "physical_quantity": payload.get("physical_quantity"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "issue_time_utc": payload.get("issue_time_utc"),
        "city": payload.get("city"),
        "target_date_local": payload.get("target_date_local"),
    }
    canon = json.dumps(provenance_fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def _provenance_json(payload: dict, metric: MetricIdentity) -> str:
    prov = {
        "data_version": payload.get("data_version"),
        "physical_quantity": payload.get("physical_quantity"),
        "observation_field": metric.observation_field,
        "temperature_metric": metric.temperature_metric,
        "param": payload.get("param"),
        "short_name": payload.get("short_name"),
        "step_type": payload.get("step_type"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "issue_time_utc": payload.get("issue_time_utc"),
        "lead_day": payload.get("lead_day"),
        "city": payload.get("city"),
        "target_date_local": payload.get("target_date_local"),
        "nearest_grid_lat": payload.get("nearest_grid_lat"),
        "nearest_grid_lon": payload.get("nearest_grid_lon"),
        "nearest_grid_distance_km": payload.get("nearest_grid_distance_km"),
    }
    evidence = _contract_evidence_fields(
        payload,
        metric,
        source_id=derive_source_id_from_data_version(str(payload.get("data_version", ""))),
    )
    prov["contract_outcome_evidence"] = {
        key: value
        for key, value in evidence.items()
        if key
        in {
            "city_timezone",
            "settlement_source_type",
            "settlement_station_id",
            "settlement_unit",
            "settlement_rounding_policy",
            "bin_grid_id",
            "bin_schema_version",
            "forecast_window_start_utc",
            "forecast_window_end_utc",
            "forecast_window_start_local",
            "forecast_window_end_local",
            "forecast_window_attribution_status",
            "contributes_to_target_extrema",
            "forecast_window_block_reasons_json",
        }
    }
    return json.dumps(prov, ensure_ascii=False)


def _extract_boundary_fields(payload: dict) -> tuple[int, int]:
    """Return (boundary_ambiguous: 0|1, ambiguous_member_count: int)."""
    bp = payload.get("boundary_policy")
    if isinstance(bp, dict):
        ambiguous = 1 if bp.get("boundary_ambiguous") else 0
        count = int(bp.get("ambiguous_member_count", 0))
        return ambiguous, count
    return 0, 0


def _members_list(payload: dict) -> list[float | None]:
    """Extract member values from payload['members'] list."""
    members_raw = payload.get("members", [])
    return [m.get("value_native_unit") for m in members_raw]


def _lead_hours(payload: dict) -> float:
    """Compute lead_hours from lead_day (issue_utc to start of target local day)."""
    return float(payload.get("lead_day", 0)) * 24.0


def _parse_iso_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_step_range(value: object) -> tuple[int, int] | None:
    if isinstance(value, str):
        parts = value.strip().split("-")
        if len(parts) != 2:
            return None
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        return (start, end) if start < end else None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            start, end = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
        return (start, end) if start < end else None
    if isinstance(value, dict):
        start_raw = value.get("start_step_hours", value.get("start_step", value.get("start")))
        end_raw = value.get("end_step_hours", value.get("end_step", value.get("end")))
        try:
            start, end = int(start_raw), int(end_raw)
        except (TypeError, ValueError):
            return None
        return (start, end) if start < end else None
    return None


def _is_low_extrema_payload(payload: dict) -> bool:
    metric = str(payload.get("temperature_metric") or "").strip().lower()
    physical_quantity = str(payload.get("physical_quantity") or "").strip().lower()
    short_name = str(payload.get("short_name") or "").strip().lower()
    return metric == "low" or "mn2t6" in physical_quantity or short_name == "mn2t6"


def _parsed_ranges_from_payload_key(payload: dict, key: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    raw = payload.get(key)
    if not isinstance(raw, list):
        return ranges
    for item in raw:
        parsed = _parse_step_range(item)
        if parsed is not None:
            ranges.append(parsed)
    return ranges


def _boundary_policy(payload: dict) -> dict:
    raw = payload.get("boundary_policy")
    return raw if isinstance(raw, dict) else {}


def _selected_step_ranges(payload: dict) -> list[tuple[int, int]]:
    if _is_low_extrema_payload(payload):
        # LOW local-day minima are constructed from fully-inside windows.  The
        # boundary ranges are quarantine/proof windows used to decide whether a
        # boundary aggregate can win; they are not contributing extrema windows.
        ranges = _parsed_ranges_from_payload_key(payload, "selected_step_ranges_inner")
        if not ranges:
            ranges = _parsed_ranges_from_payload_key(payload, "selected_step_ranges")
        return list(dict.fromkeys(ranges))

    ranges: list[tuple[int, int]] = []
    for key in (
        "selected_step_ranges",
        "selected_step_ranges_inner",
    ):
        ranges.extend(_parsed_ranges_from_payload_key(payload, key))
    # Preserve order while removing duplicates; payloads can repeat a range
    # across diagnostic fields.
    return list(dict.fromkeys(ranges))


def _missing_forecast_window_reason(payload: dict) -> str:
    if _is_low_extrema_payload(payload):
        return "missing_low_inner_forecast_window_evidence"
    return "missing_explicit_forecast_window_evidence"


def _missing_contract_extrema_member_reasons(payload: dict) -> list[str]:
    reasons: list[str] = []
    missing_members = payload.get("missing_members")
    if isinstance(missing_members, list) and missing_members:
        reasons.append("missing_forecast_members_for_contract_extrema")
    members = payload.get("members")
    if isinstance(members, list) and any(
        not isinstance(member, dict) or member.get("value_native_unit") is None
        for member in members
    ):
        reasons.append("missing_member_value_for_contract_extrema")
    return reasons


def _forecast_window_from_payload(
    payload: dict,
    *,
    city_timezone: str,
) -> dict[str, Any]:
    explicit_start_utc = _parse_iso_datetime(
        payload.get("forecast_window_start_utc") or payload.get("window_start_utc")
    )
    explicit_end_utc = _parse_iso_datetime(
        payload.get("forecast_window_end_utc") or payload.get("window_end_utc")
    )
    explicit_start_local = _parse_iso_datetime(
        payload.get("forecast_window_start_local") or payload.get("window_start_local")
    )
    explicit_end_local = _parse_iso_datetime(
        payload.get("forecast_window_end_local") or payload.get("window_end_local")
    )
    if (
        explicit_start_utc is not None
        and explicit_end_utc is not None
        and explicit_start_local is not None
        and explicit_end_local is not None
    ):
        return {
            "forecast_window_start_utc": explicit_start_utc.isoformat(),
            "forecast_window_end_utc": explicit_end_utc.isoformat(),
            "forecast_window_start_local": explicit_start_local.isoformat(),
            "forecast_window_end_local": explicit_end_local.isoformat(),
            "block_reasons": [],
        }

    issue_time = _parse_iso_datetime(payload.get("issue_time_utc"))
    ranges = _selected_step_ranges(payload)
    if issue_time is None or not ranges:
        return {"block_reasons": [_missing_forecast_window_reason(payload)]}

    start_step = min(start for start, _ in ranges)
    end_step = max(end for _, end in ranges)
    start_utc = issue_time + timedelta(hours=start_step)
    end_utc = issue_time + timedelta(hours=end_step)
    try:
        tz = ZoneInfo(city_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return {"block_reasons": ["invalid_city_timezone"]}
    return {
        "forecast_window_start_utc": start_utc.isoformat(),
        "forecast_window_end_utc": end_utc.isoformat(),
        "forecast_window_start_local": start_utc.astimezone(tz).isoformat(),
        "forecast_window_end_local": end_utc.astimezone(tz).isoformat(),
        "block_reasons": [],
    }


def _contract_evidence_fields(
    payload: dict,
    metric: MetricIdentity,
    *,
    source_id: str | None,
) -> dict[str, Any]:
    """Build shadow contract/bin/window evidence columns for a snapshot row.

    These fields are evidence only. They do not change ``training_allowed`` and
    therefore cannot relax LOW Law 1 by themselves.
    """

    city_name = str(payload.get("city") or "")
    cities_by_name = runtime_cities_by_name()
    city = cities_by_name.get(city_name)
    if city is None:
        return {
            "city_timezone": payload.get("timezone"),
            "settlement_source_type": None,
            "settlement_station_id": None,
            "settlement_unit": None,
            "settlement_rounding_policy": None,
            "bin_grid_id": None,
            "bin_schema_version": None,
            "forecast_window_start_utc": None,
            "forecast_window_end_utc": None,
            "forecast_window_start_local": None,
            "forecast_window_end_local": None,
            "forecast_window_local_day_overlap_hours": None,
            "forecast_window_attribution_status": "UNKNOWN",
            "contributes_to_target_extrema": 0,
            "forecast_window_block_reasons_json": json.dumps(["unknown_city_for_contract_evidence"]),
        }

    sem = SettlementSemantics.for_city(city)
    grid = grid_for_city(city)
    city_timezone = str(payload.get("timezone") or city.timezone)
    station_id = city.wu_station or sem.resolution_source
    target_date = str(payload.get("target_date_local") or payload.get("target_date") or "")
    window_fields = _forecast_window_from_payload(payload, city_timezone=city_timezone)
    block_reasons = list(window_fields.pop("block_reasons", []))

    base = {
        "city_timezone": city_timezone,
        "settlement_source_type": city.settlement_source_type,
        "settlement_station_id": station_id,
        "settlement_unit": city.settlement_unit,
        "settlement_rounding_policy": sem.rounding_rule,
        "bin_grid_id": grid.label,
        "bin_schema_version": "canonical_bin_grid_v1",
        "forecast_window_start_utc": window_fields.get("forecast_window_start_utc"),
        "forecast_window_end_utc": window_fields.get("forecast_window_end_utc"),
        "forecast_window_start_local": window_fields.get("forecast_window_start_local"),
        "forecast_window_end_local": window_fields.get("forecast_window_end_local"),
        "forecast_window_local_day_overlap_hours": None,
        "forecast_window_attribution_status": "UNKNOWN",
        "contributes_to_target_extrema": 0,
        "forecast_window_block_reasons_json": json.dumps(block_reasons),
    }
    if block_reasons:
        if bool(_boundary_policy(payload).get("boundary_ambiguous", False)):
            block_reasons.append("boundary_ambiguous")
            base["forecast_window_attribution_status"] = "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
            base["forecast_window_block_reasons_json"] = json.dumps(list(dict.fromkeys(block_reasons)))
        return base

    try:
        contract_domain = ContractOutcomeDomain(
            city=city.name,
            target_local_date=datetime.fromisoformat(target_date).date(),
            city_timezone=city_timezone,
            temperature_metric=metric.temperature_metric,
            observation_field=metric.observation_field,
            settlement_source_type=city.settlement_source_type,
            settlement_station_id=station_id,
            settlement_unit=city.settlement_unit,  # type: ignore[arg-type]
            settlement_rounding_policy=sem.rounding_rule,
            bin_grid_id=grid.label,
            bin_schema_version="canonical_bin_grid_v1",
        )
        evidence_payload = dict(payload)
        evidence_payload.update(window_fields)
        evidence_payload["forecast_source_id"] = source_id or derive_source_id_from_data_version(
            str(payload.get("data_version", ""))
        )
        evidence_payload.setdefault("data_version", payload.get("data_version"))
        evidence_payload.setdefault("horizon_profile", "full")
        if payload.get("issue_time_utc"):
            issue = _parse_iso_datetime(payload.get("issue_time_utc"))
            if issue is not None:
                evidence_payload.setdefault("cycle_hour_utc", issue.hour)
        evidence = ForecastToBinEvidence.from_snapshot_payload(contract_domain, evidence_payload)
    except (ValueError, ContractOutcomeDomainMismatch) as exc:
        block_reasons.append(f"contract_evidence_error:{type(exc).__name__}")
        base["forecast_window_block_reasons_json"] = json.dumps(block_reasons)
        return base

    evidence_block_reasons = list(evidence.block_reasons)
    evidence_block_reasons.extend(_missing_contract_extrema_member_reasons(payload))
    if not evidence.contributes_to_target_extrema and not evidence_block_reasons:
        evidence_block_reasons.append(evidence.attribution_status.lower())
    contributes = evidence.contributes_to_target_extrema and not evidence_block_reasons
    return {
        **base,
        "forecast_window_local_day_overlap_hours": evidence.local_day_overlap_hours,
        "forecast_window_attribution_status": evidence.attribution_status,
        "contributes_to_target_extrema": 1 if contributes else 0,
        "forecast_window_block_reasons_json": json.dumps(evidence_block_reasons),
    }


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest_json_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    metric: MetricIdentity,
    model_version: str,
    overwrite: bool,
    source_run_context: SourceRunContext | None = None,
    ingest_backend: str = "unknown",
) -> str:
    """Ingest one extracted JSON file into ensemble_snapshots_v2. Returns status string.

    ``ingest_backend`` (TIGGE spec v3 §3 Phase 0 #5 / critic v2 A1 BLOCKER) records
    the live transport that produced the row: ``'ecds'`` (post-cutover ECDS lanes),
    ``'webapi'`` (legacy direct webapi route), or ``'unknown'`` (cannot infer).
    Historic rows pre-2026-05-07 carry ``'unknown'`` because their provenance is
    unverifiable; new writes MUST pass an explicit value.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not parse %s: %s", path, exc)
        return "parse_error"

    # TiggeSnapshotPayload.from_json_dict is the ONLY way to read extracted JSONs.
    # Fail-closed: missing required fields or malformed causality raises ProvenanceViolation.
    try:
        snapshot = TiggeSnapshotPayload.from_json_dict(raw)
    except ProvenanceViolation as exc:
        logger.error(
            "ingest_json_file provenance_violation: path=%s error=%s",
            path,
            exc,
        )
        return "contract_rejected: PROVENANCE_VIOLATION"

    # Use the validated dataclass's dict for downstream processing.
    payload = snapshot.to_json_dict()

    data_version = str(payload.get("data_version", ""))
    # NC-12: guard must fire before INSERT
    assert_data_version_allowed(data_version, context="ingest_grib_to_snapshots")

    raw_unit = str(payload.get("unit", ""))
    members_unit = _normalize_unit(raw_unit)
    validate_members_unit(members_unit, context=str(path))

    # Wire contract: enrich payload with authoritative metric fields before validation
    # so the contract can cross-check temperature_metric/physical_quantity against
    # data_version without requiring the JSON to self-report redundantly.
    # Causality is NOT defaulted — Law 5 (R-AJ) declares causality first-class and
    # any payload missing the field must be rejected, not silently accepted as OK.
    # Pre-Phase-5B high-track JSON that lacks causality must be quarantined or
    # re-extracted through extract_tigge_mx2t6_localday_max (which now emits the
    # field) before re-ingest; silent-default would let RUNTIME_ONLY_FALLBACK or
    # N/A_CAUSAL_DAY_ALREADY_STARTED rows masquerade as clean training data.
    contract_payload = dict(payload)
    contract_payload.setdefault("temperature_metric", metric.temperature_metric)
    contract_payload.setdefault("physical_quantity", metric.physical_quantity)
    contract_payload.setdefault("members_unit", members_unit)
    decision = validate_snapshot_contract(contract_payload)
    if not decision.accepted:
        logger.error(
            "ingest_json_file contract_rejected: path=%s reason=%s",
            path,
            decision.reason,
        )
        return f"contract_rejected: {decision.reason}"

    city = str(payload.get("city", ""))
    target_date = str(payload.get("target_date_local", ""))
    issue_time = str(payload.get("issue_time_utc", ""))

    # D1+D3 (TIGGE spec v3 §3 Phase 0 #8 / critic v2 D1+D3 BLOCKER):
    # manifest-hash-aware existence check. If the incoming payload's
    # ``manifest_sha256`` differs from the row already in the DB, the row was
    # produced under a different manifest (city set / coordinate drift /
    # spec change) and must be REPLACED, not skipped. Pure same-manifest
    # repeats keep the legacy IGNORE behaviour so re-ingest stays idempotent.
    payload_manifest_sha = str(payload.get("manifest_sha256", ""))
    drift_replace = False
    if not overwrite:
        existing = conn.execute(
            "SELECT manifest_hash, provenance_json FROM ensemble_snapshots_v2 "
            "WHERE city=? AND target_date=? "
            "AND temperature_metric=? AND issue_time=? AND data_version=?",
            (city, target_date, metric.temperature_metric, issue_time, data_version),
        ).fetchone()
        if existing:
            db_manifest_sha = ""
            try:
                prov = json.loads(existing["provenance_json"]) if existing["provenance_json"] else {}
                db_manifest_sha = str(prov.get("manifest_sha256", ""))
            except (json.JSONDecodeError, TypeError, KeyError):
                db_manifest_sha = ""
            if (
                payload_manifest_sha
                and db_manifest_sha
                and payload_manifest_sha != db_manifest_sha
            ):
                drift_replace = True
                logger.info(
                    "manifest_sha drift detected city=%s target=%s issue=%s "
                    "data_version=%s db_sha=%s payload_sha=%s — REPLACING",
                    city, target_date, issue_time, data_version,
                    db_manifest_sha[:12], payload_manifest_sha[:12],
                )
            else:
                return "skipped_exists"

    members = _members_list(payload)
    # Use contract-authoritative values — override payload's self-reported fields.
    training_allowed = 1 if decision.training_allowed else 0
    causality_status = decision.causality_status
    boundary_ambiguous, ambiguous_member_count = _extract_boundary_fields(payload)
    manifest_hash = _manifest_hash_from_payload(payload)
    contract_evidence = _contract_evidence_fields(
        payload,
        metric,
        source_id=source_run_context.source_id if source_run_context else None,
    )
    prov_json = _provenance_json(payload, metric)
    lead_hours = _lead_hours(payload)
    now = _now_utc_iso()
    # R-L: new provenance fields from local-calendar-day extractor (Phase 4.5)
    local_day_start_utc = payload.get("local_day_start_utc") or None
    step_horizon_hours = payload.get("step_horizon_hours")
    step_horizon_hours = float(step_horizon_hours) if step_horizon_hours is not None else None

    # 2026-05-07 Codex P2 fix: persist the physical_quantity reported by the
    # contract-validated payload, not the legacy ``metric.physical_quantity``
    # constant. Open Data post-cutover (mx2t3/mn2t3) writes a different
    # physical string than the TIGGE-archive 6h derived quantity; stamping the
    # row with the legacy 6h string would silently erase 3h identity even
    # though the contract validated correctly. Fall back to ``metric.physical_quantity``
    # for legacy paths whose payload omits the field.
    physical_quantity_for_row = (
        str(contract_payload.get("physical_quantity") or metric.physical_quantity)
    )

    available_at = source_run_context.available_at_iso() if source_run_context else issue_time
    row = dict(
        city=city,
        target_date=target_date,
        temperature_metric=metric.temperature_metric,
        physical_quantity=physical_quantity_for_row,
        observation_field=metric.observation_field,
        issue_time=issue_time,
        valid_time=target_date,
        available_at=available_at,
        fetch_time=now,
        lead_hours=lead_hours,
        members_json=json.dumps(members),
        model_version=model_version,
        data_version=data_version,
        source_id=source_run_context.source_id if source_run_context else None,
        source_transport=source_run_context.source_transport if source_run_context else None,
        source_run_id=source_run_context.source_run_id if source_run_context else None,
        release_calendar_key=source_run_context.release_calendar_key if source_run_context else None,
        source_cycle_time=(source_run_context.source_cycle_time.isoformat() if source_run_context else None),
        source_release_time=(source_run_context.source_release_time.isoformat() if source_run_context else None),
        source_available_at=(source_run_context.source_available_at.isoformat() if source_run_context and source_run_context.source_available_at else None),
        training_allowed=training_allowed,
        causality_status=causality_status,
        boundary_ambiguous=boundary_ambiguous,
        ambiguous_member_count=ambiguous_member_count,
        manifest_hash=manifest_hash,
        provenance_json=prov_json,
        members_unit=members_unit,
        local_day_start_utc=local_day_start_utc,
        step_horizon_hours=step_horizon_hours,
        ingest_backend=ingest_backend,
    )

    # D1+D3: drift_replace promotes the verb to REPLACE when manifest_sha
    # drift was detected against an existing row. ZEUS_INGEST_FORCE_REPLACE=1
    # gives the extractor a way to globally force REPLACE without per-row
    # drift-detect (used when extractor pre-flight has detected manifest drift
    # affecting any of the 51 cities; see critic v2 D1+D3 BLOCKER).
    import os as _os
    force_replace_env = _os.environ.get("ZEUS_INGEST_FORCE_REPLACE", "") == "1"
    insert_verb = (
        "INSERT OR REPLACE"
        if (overwrite or drift_replace or force_replace_env)
        else "INSERT OR IGNORE"
    )

    def _db_op() -> None:
        conn.execute(
            f"""
            {insert_verb} INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, physical_quantity, observation_field,
             issue_time, valid_time, available_at, fetch_time, lead_hours,
             members_json, model_version, data_version, source_id, source_transport,
             source_run_id, release_calendar_key, source_cycle_time,
             source_release_time, source_available_at, training_allowed, causality_status,
             boundary_ambiguous, ambiguous_member_count, manifest_hash, provenance_json,
             members_unit, local_day_start_utc, step_horizon_hours, ingest_backend)
            VALUES
            (:city, :target_date, :temperature_metric, :physical_quantity, :observation_field,
             :issue_time, :valid_time, :available_at, :fetch_time, :lead_hours,
             :members_json, :model_version, :data_version, :source_id, :source_transport,
             :source_run_id, :release_calendar_key, :source_cycle_time,
             :source_release_time, :source_available_at, :training_allowed, :causality_status,
             :boundary_ambiguous, :ambiguous_member_count, :manifest_hash, :provenance_json,
             :members_unit, :local_day_start_utc, :step_horizon_hours, :ingest_backend)
            """,
            row,
        )

    commit_then_export(conn, db_op=_db_op)
    return "written"


def ingest_track(
    *,
    track: str,
    json_root: Path,
    conn: sqlite3.Connection,
    date_from: str | None,
    date_to: str | None,
    cities: set[str] | None,
    overwrite: bool,
    require_files: bool = True,
    source_run_context: SourceRunContext | None = None,
    ingest_backend: str = "unknown",
) -> dict:
    cfg = _TRACK_CONFIGS[track]
    metric: MetricIdentity = cfg["metric"]
    model_version: str = cfg["model_version"]
    subdir = json_root / cfg["json_subdir"]

    if not subdir.exists():
        msg = f"JSON root not found: {subdir}. Run extract_tigge_mx2t6_localday_max.py first."
        if require_files:
            raise FileNotFoundError(msg)
        logger.warning(msg)
        return {"error": f"json_root_missing: {subdir}", "written": 0, "skipped": 0, "errors": 0}

    all_json = sorted(subdir.rglob("*.json"))

    # MAJOR-2: fail-loud if no JSON files found — silent zero-row runs mask missing extraction step.
    if require_files and not all_json:
        raise FileNotFoundError(
            f"No JSON files found under {subdir}. "
            "Run extract_tigge_mx2t6_localday_max.py first to produce extracted JSON, "
            "then re-run ingest. Pass --no-require-files to allow zero-file runs."
        )

    counters: dict[str, int] = {"written": 0, "skipped_exists": 0, "parse_error": 0, "other": 0}

    for path in all_json:
        # City filter: path structure is <subdir>/<city-slug>/<date>/<filename>
        if cities:
            city_slug_dir = path.parts[-3] if len(path.parts) >= 3 else ""
            if city_slug_dir not in {c.lower().replace(" ", "-") for c in cities}:
                continue

        # Date filter on target_date embedded in filename
        if date_from or date_to:
            name = path.stem
            try:
                # filename contains target_YYYY-MM-DD_lead_N
                target_part = [p for p in name.split("_") if "-" in p and len(p) == 10]
                if not target_part:
                    raise ValueError("no date in filename")
                tdate = target_part[0]
                if date_from and tdate < date_from:
                    continue
                if date_to and tdate > date_to:
                    continue
            except Exception:
                pass

        status = ingest_json_file(
            conn,
            path,
            metric=metric,
            model_version=model_version,
            overwrite=overwrite,
            source_run_context=source_run_context,
            ingest_backend=ingest_backend,
        )
        if status in counters:
            counters[status] += 1
        elif status == "written":
            counters["written"] += 1
        else:
            counters["other"] += 1

    return {
        "track": track,
        "data_version": metric.data_version,
        "json_root": str(subdir),
        "written": counters["written"],
        "skipped": counters["skipped_exists"],
        "errors": counters["parse_error"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--track",
        choices=sorted(_TRACK_CONFIGS),
        default="mx2t6_high",
        help="Which track to ingest (default: mx2t6_high)",
    )
    parser.add_argument(
        "--json-root",
        type=Path,
        default=FIFTY_ONE_ROOT / "raw",
        help="Root directory containing extracted JSON subdirs",
    )
    parser.add_argument("--date-from", default=None, help="Skip target_dates before YYYY-MM-DD")
    parser.add_argument("--date-to", default=None, help="Skip target_dates after YYYY-MM-DD")
    parser.add_argument("--cities", nargs="*", default=None, help="City names to include (default: all)")
    parser.add_argument("--overwrite", action="store_true", help="Re-ingest rows that already exist")
    parser.add_argument("--no-require-files", action="store_true",
                        help="Allow zero-file runs (default: fail if no JSON files found)")
    parser.add_argument("--db-path", type=Path, default=None, help="Override DB path")
    parser.add_argument(
        "--ingest-backend",
        choices=("ecds", "webapi", "unknown"),
        default="unknown",
        help=(
            "Live transport tag stored on each row "
            "(TIGGE spec v3 §3 Phase 0 #5 / critic v2 A1). "
            "Default 'unknown' for legacy/manual runs."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.db_path:
        conn = sqlite3.connect(str(args.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        from src.state.db import init_schema
        init_schema(conn)
    else:
        conn = get_world_connection(write_class="bulk")
    apply_v2_schema(conn)

    summary = ingest_track(
        track=args.track,
        json_root=args.json_root,
        conn=conn,
        date_from=args.date_from,
        date_to=args.date_to,
        cities=set(args.cities) if args.cities else None,
        overwrite=args.overwrite,
        require_files=not args.no_require_files,
        ingest_backend=args.ingest_backend,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("errors", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
