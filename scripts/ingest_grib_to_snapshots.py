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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.ensemble_snapshot_provenance import (
    assert_data_version_allowed,
    validate_members_unit,
)
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
    return json.dumps(prov, ensure_ascii=False)


def _extract_causality_status(payload: dict) -> str:
    """Extract causality_status; default 'OK' for high track (low track uses JSON field)."""
    causality = payload.get("causality")
    if isinstance(causality, dict):
        status = str(causality.get("status", "OK"))
        allowed = {
            "OK",
            "N/A_CAUSAL_DAY_ALREADY_STARTED",
            "N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON",
            "REJECTED_BOUNDARY_AMBIGUOUS",
            "RUNTIME_ONLY_FALLBACK",
            "UNKNOWN",
        }
        return status if status in allowed else "UNKNOWN"
    return "OK"


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


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest_json_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    metric: MetricIdentity,
    model_version: str,
    overwrite: bool,
) -> str:
    """Ingest one extracted JSON file into ensemble_snapshots_v2. Returns status string."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not parse %s: %s", path, exc)
        return "parse_error"

    data_version = str(payload.get("data_version", ""))
    # NC-12: guard must fire before INSERT
    assert_data_version_allowed(data_version, context="ingest_grib_to_snapshots")

    raw_unit = str(payload.get("unit", ""))
    members_unit = _normalize_unit(raw_unit)
    validate_members_unit(members_unit, context=str(path))

    city = str(payload.get("city", ""))
    target_date = str(payload.get("target_date_local", ""))
    issue_time = str(payload.get("issue_time_utc", ""))

    if not overwrite:
        existing = conn.execute(
            "SELECT 1 FROM ensemble_snapshots_v2 WHERE city=? AND target_date=? "
            "AND temperature_metric=? AND issue_time=? AND data_version=?",
            (city, target_date, metric.temperature_metric, issue_time, data_version),
        ).fetchone()
        if existing:
            return "skipped_exists"

    members = _members_list(payload)
    training_allowed = 1 if payload.get("training_allowed") else 0
    causality_status = _extract_causality_status(payload)
    boundary_ambiguous, ambiguous_member_count = _extract_boundary_fields(payload)
    manifest_hash = _manifest_hash_from_payload(payload)
    prov_json = _provenance_json(payload, metric)
    lead_hours = _lead_hours(payload)
    now = _now_utc_iso()
    # R-L: new provenance fields from local-calendar-day extractor (Phase 4.5)
    local_day_start_utc = payload.get("local_day_start_utc") or None
    step_horizon_hours = payload.get("step_horizon_hours")
    step_horizon_hours = float(step_horizon_hours) if step_horizon_hours is not None else None

    row = dict(
        city=city,
        target_date=target_date,
        temperature_metric=metric.temperature_metric,
        physical_quantity=metric.physical_quantity,
        observation_field=metric.observation_field,
        issue_time=issue_time,
        valid_time=target_date,
        available_at=issue_time,
        fetch_time=now,
        lead_hours=lead_hours,
        members_json=json.dumps(members),
        model_version=model_version,
        data_version=data_version,
        training_allowed=training_allowed,
        causality_status=causality_status,
        boundary_ambiguous=boundary_ambiguous,
        ambiguous_member_count=ambiguous_member_count,
        manifest_hash=manifest_hash,
        provenance_json=prov_json,
        members_unit=members_unit,
        local_day_start_utc=local_day_start_utc,
        step_horizon_hours=step_horizon_hours,
    )

    insert_verb = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"

    def _db_op() -> None:
        conn.execute(
            f"""
            {insert_verb} INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, physical_quantity, observation_field,
             issue_time, valid_time, available_at, fetch_time, lead_hours,
             members_json, model_version, data_version, training_allowed, causality_status,
             boundary_ambiguous, ambiguous_member_count, manifest_hash, provenance_json,
             members_unit, local_day_start_utc, step_horizon_hours)
            VALUES
            (:city, :target_date, :temperature_metric, :physical_quantity, :observation_field,
             :issue_time, :valid_time, :available_at, :fetch_time, :lead_hours,
             :members_json, :model_version, :data_version, :training_allowed, :causality_status,
             :boundary_ambiguous, :ambiguous_member_count, :manifest_hash, :provenance_json,
             :members_unit, :local_day_start_utc, :step_horizon_hours)
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
) -> dict:
    # MODERATE-6: low track is Phase 5 scope — boundary quarantine logic not yet implemented.
    if track == "mn2t6_low":
        raise NotImplementedError(
            "Phase 5 scope — mn2t6_low track requires boundary quarantine logic "
            "not yet implemented. Use track='mx2t6_high' for Phase 4B ingest."
        )

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

        status = ingest_json_file(conn, path, metric=metric, model_version=model_version, overwrite=overwrite)
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
        conn = get_world_connection()
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
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("errors", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
