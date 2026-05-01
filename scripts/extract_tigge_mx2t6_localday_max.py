# Lifecycle: created=2026-04-17; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: GRIB→JSON extractor for mx2t6 local-calendar-day max (high track);
#          emits canonical payload consumed by scripts/ingest_grib_to_snapshots.py;
#          implements Phase 4.5 R-Q..R-U (step horizon, causality, manifest hash,
#          Kelvin fail-closed, boundary=False) and Phase 4.6 R-AA (cities cross-validate).
# Reuse: Before running, confirm (1) config/cities.json is canonical source of
#        city identity, (2) 51 source data/docs/tigge_city_coordinate_manifest_full_latest.json
#        aligns (runs through _cross_validate_city_manifests, tolerance ±0.01°),
#        (3) DEFAULT_MANIFEST path exists for manifest_sha256, (4) eccodes system
#        dep installed (brew install eccodes). See docs/authority/zeus_dual_track_architecture.md
#        §2/§5/§6/§8 for track semantics; tests at tests/test_phase4_5_extractor.py +
#        tests/test_phase4_6_cities_drift.py are the contract. Phase 7B-followup
#        (2026-04-18): 13 private helpers + CityManifestDriftError + 2 compute_*
#        functions relocated to scripts/_tigge_common.py; imported below.
#!/usr/bin/env python3
"""GRIB→JSON extractor for mx2t6 local-calendar-day max (high track only).

Reads raw TIGGE ECMWF ensemble GRIB files (param=121.128, mx2t6, 6h max) and
produces one JSON file per (city, issue_date, target_local_date, lead_day).

Output contract matches ingest_grib_to_snapshots.py:ingest_json_file expectations:
- data_version  = "tigge_mx2t6_local_calendar_day_max_v1"
- unit          = "C" or "F" (single char; ingest maps C→degC, F→degF)
- members       = 51 elements, member 0 = control forecast, 1..50 = perturbed
- training_allowed = True only if all 51 members have non-None value_native_unit
- causality / boundary_policy are OMITTED for high track (ingest defaults to OK/0)

Phase 5 (low track / mn2t6) is NOT implemented here. Any call with a non-high
track argument raises NotImplementedError immediately.

Dependencies: eccodes (system library; must be installed: brew install eccodes)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from eccodes import (
    codes_get,
    codes_grib_find_nearest,
    codes_grib_new_from_file,
    codes_release,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.tigge_snapshot_payload import TiggeSnapshotPayload  # noqa: E402
from src.types.metric_identity import HIGH_LOCALDAY_MAX  # noqa: E402

# Phase 7B-followup: shared helpers relocated to scripts/_tigge_common.py
from scripts._tigge_common import (  # noqa: E402
    AGGREGATION_WINDOW_HOURS,
    CityManifestDriftError,
    DEFAULT_MANIFEST,
    FIFTY_ONE_ROOT,
    MEMBER_COUNT,
    RAW_ROOT,
    _city_slug,
    _cross_validate_city_manifests,
    _file_sha256,
    _get_city_config,
    _issue_utc_from_fields,
    _iter_overlap_local_dates,
    _load_cities_config,
    _local_day_bounds_utc,
    _now_utc_iso,
    _overlap_seconds,
    _parse_dates_from_dirname,
    _parse_steps_from_filename,
    compute_manifest_hash,
    compute_required_max_step,
)

logger = logging.getLogger(__name__)

# Per-extractor divergent constants (stay local — differ between high/low tracks)
GRIB_SUBDIR = "tigge_ecmwf_ens_regions_mx2t6"
OUTPUT_SUBDIR = "tigge_ecmwf_ens_mx2t6_localday_max"
OUTPUT_FILENAME_PREFIX = "tigge_ecmwf_mx2t6_localday_max"

# LOW-4: derive from MetricIdentity — eliminates silent drift if metric_identity.py changes
DATA_VERSION = HIGH_LOCALDAY_MAX.data_version
PHYSICAL_QUANTITY = HIGH_LOCALDAY_MAX.physical_quantity
PARAM = "121.128"
PARAM_ID = 121
SHORT_NAME = "mx2t6"
STEP_TYPE = "max"


# ---------------------------------------------------------------------------
# High-track-specific public surface
# ---------------------------------------------------------------------------


def compute_causality(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> dict:
    """Compute causality dict for a (city, issue_utc, target_date) slot.

    pure_forecast_valid = True when issue_utc <= local_day_start_utc.
    High track default status: 'OK'.
    Low track non-causal status: 'N/A_CAUSAL_DAY_ALREADY_STARTED' (Phase 5).
    """
    fixed_tz = timezone(timedelta(hours=city_utc_offset_hours))
    local_day_start_local = datetime.combine(target_date, dt_time.min, tzinfo=fixed_tz)
    local_day_start_utc = local_day_start_local.astimezone(timezone.utc)
    pure_forecast_valid = issue_utc <= local_day_start_utc
    if pure_forecast_valid:
        status = "OK"
    else:
        status = "N/A_CAUSAL_DAY_ALREADY_STARTED"
    return {"pure_forecast_valid": pure_forecast_valid, "status": status}


def extract_one_grib_file(
    path: str | Path,
    city: str,
    issue_utc: datetime,
    target_date: date,
    lead_day: int,
    *,
    cities_config: list[dict] | None = None,
    manifest_sha256_value: str = "",
    track: str = "mx2t6_high",
) -> dict:
    """Extract local-calendar-day max for one city from one GRIB file.

    Returns the full JSON payload dict for this (city, issue_utc, target_date).
    Raises NotImplementedError if track != "mx2t6_high".
    Raises FileNotFoundError / ValueError on bad GRIB path.

    NOTE: This function reads a single GRIB file; caller is responsible for
    combining control + perturbed files for the full 51-member set.
    For smoke-testing a single file is sufficient to confirm correctness.
    """
    if track != "mx2t6_high":
        raise NotImplementedError(
            f"track={track!r} is not implemented. "
            "Phase 4.5 is high track only (mx2t6_high). "
            "Phase 5 will implement low track (mn2t6_low)."
        )

    grib_path = Path(path)
    if not grib_path.exists():
        raise FileNotFoundError(f"GRIB file not found: {grib_path}")

    city_cfg = _get_city_config(city, cities_config)
    city_tz = city_cfg["timezone"]
    city_unit = city_cfg["unit"]
    city_lat = float(city_cfg["lat"])
    city_lon = float(city_cfg["lon"])
    city_name = city_cfg.get("city") or city_cfg.get("name") or city

    day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, city_tz)

    # member → max value across selected inner buckets
    member_values: dict[int, float | None] = {}
    nearest_lat: float | None = None
    nearest_lon: float | None = None
    nearest_dist: float | None = None
    selected_steps: list[str] = []

    is_control = "control" in grib_path.name

    with grib_path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                param_id = int(codes_get(gid, "paramId"))
                short_name = str(codes_get(gid, "shortName"))
                step_type_grib = str(codes_get(gid, "stepType"))
                if param_id != PARAM_ID or short_name != SHORT_NAME or step_type_grib != STEP_TYPE:
                    continue

                data_date = int(codes_get(gid, "dataDate"))
                data_time = int(codes_get(gid, "dataTime"))
                msg_issue_utc = _issue_utc_from_fields(data_date, data_time)
                if msg_issue_utc.date() != issue_utc.date():
                    continue

                start_step = int(codes_get(gid, "startStep"))
                end_step = int(codes_get(gid, "endStep"))
                step_range = f"{start_step}-{end_step}"

                window_start = msg_issue_utc + timedelta(hours=start_step)
                window_end = msg_issue_utc + timedelta(hours=end_step)

                overlap = _overlap_seconds(window_start, window_end, day_start_utc, day_end_utc)
                if overlap <= 0:
                    continue

                if is_control:
                    member = 0
                else:
                    member = int(codes_get(gid, "number"))

                try:
                    nearest = codes_grib_find_nearest(gid, city_lat, city_lon)[0]
                except Exception:
                    # City outside this regional GRIB bounding box — skip.
                    continue
                value_k = float(nearest["value"])
                value_native = _kelvin_to_native(value_k, city_unit)

                if nearest_lat is None:
                    nearest_lat = float(nearest["lat"])
                    nearest_lon = float(nearest["lon"])
                    nearest_dist = float(nearest["distance"])
                if step_range not in selected_steps:
                    selected_steps.append(step_range)

                if member not in member_values or member_values[member] is None:
                    member_values[member] = value_native
                else:
                    member_values[member] = max(member_values[member], value_native)

            finally:
                codes_release(gid)

    members_out = []
    missing = []
    for m in range(MEMBER_COUNT):
        val = member_values.get(m)
        if val is None:
            missing.append(m)
        members_out.append({"member": m, "value_native_unit": val})

    # CRITICAL-1: DST-aware step horizon
    dst_offset_h = int(ZoneInfo(city_tz).utcoffset(issue_utc).total_seconds() / 3600)
    step_horizon_hours = compute_required_max_step(issue_utc, target_date, dst_offset_h)
    max_present_step = 0
    for sr in selected_steps:
        try:
            end_part = int(sr.split("-")[1])
            max_present_step = max(max_present_step, end_part)
        except (IndexError, ValueError):
            pass
    horizon_satisfied = (max_present_step >= step_horizon_hours) if selected_steps else False
    step_horizon_deficit_hours = (step_horizon_hours - max_present_step) if not horizon_satisfied else 0

    # CRITICAL-2: wire causality; training_allowed requires pure_forecast_valid
    causality = compute_causality(issue_utc, target_date, dst_offset_h)
    training_allowed = (len(missing) == 0) and horizon_satisfied and causality["pure_forecast_valid"]

    provenance_fields = {
        "data_version": DATA_VERSION,
        "physical_quantity": PHYSICAL_QUANTITY,
        "manifest_sha256": manifest_sha256_value,
        "issue_time_utc": issue_utc.isoformat(),
        "city": city_name,
        "target_date_local": target_date.isoformat(),
    }
    manifest_hash = compute_manifest_hash(provenance_fields)

    payload = TiggeSnapshotPayload.make_high_track(
        generated_at=_now_utc_iso(),
        data_version=DATA_VERSION,
        physical_quantity=PHYSICAL_QUANTITY,
        param=PARAM,
        paramId=PARAM_ID,
        short_name=SHORT_NAME,
        step_type=STEP_TYPE,
        aggregation_window_hours=AGGREGATION_WINDOW_HOURS,
        city=city_name,
        lat=city_lat,
        lon=city_lon,
        unit=city_unit,
        timezone=city_tz,
        manifest_sha256=manifest_sha256_value,
        manifest_hash=manifest_hash,
        issue_time_utc=issue_utc.isoformat(),
        target_date_local=target_date.isoformat(),
        lead_day=lead_day,
        lead_day_anchor="issue_utc.date()",
        local_day_start_utc=day_start_utc.isoformat(),
        local_day_end_utc=day_end_utc.isoformat(),
        local_day_window={
            "start": day_start_utc.isoformat(),
            "end": day_end_utc.isoformat(),
        },
        step_horizon_hours=float(step_horizon_hours),
        step_horizon_deficit_hours=step_horizon_deficit_hours,
        causality=causality,
        nearest_grid_lat=nearest_lat,
        nearest_grid_lon=nearest_lon,
        nearest_grid_distance_km=nearest_dist,
        selected_step_ranges=sorted(selected_steps),
        member_count=MEMBER_COUNT,
        missing_members=missing,
        training_allowed=training_allowed,
        members=members_out,
    )
    return payload.to_json_dict()


# ---------------------------------------------------------------------------
# Batch extraction
# ---------------------------------------------------------------------------


def extract_track(
    *,
    raw_root: Path,
    output_root: Path,
    manifest_path: Path | None,
    cities_config: list[dict],
    cities: set[str] | None,
    date_from: date | None,
    date_to: date | None,
    max_target_lead_day: int,
    max_pairs: int | None,
    overwrite: bool,
) -> dict:
    """Extract all GRIB files in raw_root/GRIB_SUBDIR → JSON in output_root/OUTPUT_SUBDIR."""
    manifest_sha = _file_sha256(manifest_path) if manifest_path else ""
    if not manifest_sha:
        raise ValueError(
            "manifest_path required; empty manifest_sha256 breaks reproducibility. "
            f"Pass --manifest-path or ensure DEFAULT_MANIFEST={DEFAULT_MANIFEST} exists."
        )

    # MAJOR-4 / Phase 4.6: fail-closed cross-validation before any extraction begins
    if manifest_path is not None:
        _cross_validate_city_manifests(cities_config, manifest_path)

    city_map = {c["name"]: c for c in cities_config}
    if cities:
        city_map = {k: v for k, v in city_map.items() if k in cities}

    grib_subdir = raw_root / GRIB_SUBDIR
    pairs = _find_region_pairs(grib_subdir)
    if max_pairs is not None:
        pairs = pairs[: max(0, int(max_pairs))]

    written = 0
    skipped = 0
    errors = 0

    for pair in pairs:
        issue_dates = pair["dates"]
        if date_from and max(issue_dates) < date_from:
            continue
        if date_to and min(issue_dates) > date_to:
            continue

        for issue_date in issue_dates:
            issue_utc = datetime.combine(issue_date, dt_time.min, tzinfo=timezone.utc)

            # Accumulate member values across control + perturbed for each city/target
            # Key: (city_name, target_date_str)
            accum: dict[tuple[str, str], dict[str, Any]] = {}

            for file_path in [pair["cf_path"], pair["pf_path"]]:
                is_control = "control" in file_path.name
                _collect_grib_file(
                    file_path=file_path,
                    is_control=is_control,
                    issue_utc=issue_utc,
                    city_map=city_map,
                    max_target_lead_day=max_target_lead_day,
                    manifest_sha=manifest_sha,
                    accum=accum,
                )

            for (city_name, target_date_str), record in sorted(accum.items()):
                target_date = date.fromisoformat(target_date_str)
                lead_day = (target_date - issue_date).days
                output_path = _output_path(output_root, city_name, issue_date, target_date, lead_day)

                if output_path.exists() and not overwrite:
                    skipped += 1
                    continue

                try:
                    payload = _finalize_record(record, manifest_sha)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    written += 1
                except Exception as exc:
                    logger.warning("Error writing %s: %s", output_path, exc)
                    errors += 1

    return {
        "data_version": DATA_VERSION,
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "pair_count": len(pairs),
        "output_root": str(output_root / OUTPUT_SUBDIR),
    }


# ---------------------------------------------------------------------------
# Internal helpers (high-track-specific; shared helpers live in _tigge_common)
# ---------------------------------------------------------------------------


def _kelvin_to_native(value_k: float, unit: str) -> float:
    u = str(unit).strip().upper()
    if u not in {"C", "F"}:
        raise ValueError(f"Unknown unit {unit!r}; expected 'C' or 'F' (never 'K' or 'degC')")
    value_c = value_k - 273.15
    if u == "F":
        return value_c * 9.0 / 5.0 + 32.0
    return value_c


def _output_path(
    output_root: Path,
    city_name: str,
    issue_date: date,
    target_date: date,
    lead_day: int,
) -> Path:
    slug = _city_slug(city_name)
    issue_compact = issue_date.strftime("%Y%m%d")
    target_str = target_date.isoformat()
    filename = f"{OUTPUT_FILENAME_PREFIX}_target_{target_str}_lead_{lead_day}.json"
    return output_root / OUTPUT_SUBDIR / slug / issue_compact / filename


def _find_region_pairs(grib_subdir: Path) -> list[dict]:
    param_slug = PARAM.replace(".", "_")
    control_pattern = f"tigge_ecmwf_control_param_{param_slug}_steps_*.grib"
    pairs: list[dict] = []
    for cf_path in sorted(grib_subdir.rglob(control_pattern)):
        pf_path = cf_path.with_name(cf_path.name.replace("control", "perturbed", 1))
        if not pf_path.exists():
            continue
        steps = _parse_steps_from_filename(cf_path)
        pairs.append(
            {
                "region": cf_path.parent.parent.name,
                "date_compact": cf_path.parent.name,
                "dates": _parse_dates_from_dirname(cf_path.parent.name),
                "cf_path": cf_path,
                "pf_path": pf_path,
                "steps": steps,
            }
        )
    return pairs


def _collect_grib_file(
    *,
    file_path: Path,
    is_control: bool,
    issue_utc: datetime,
    city_map: dict[str, dict],
    max_target_lead_day: int,
    manifest_sha: str,
    accum: dict[tuple[str, str], dict[str, Any]],
) -> None:
    with file_path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                if int(codes_get(gid, "paramId")) != PARAM_ID:
                    continue
                if str(codes_get(gid, "shortName")) != SHORT_NAME:
                    continue
                if str(codes_get(gid, "stepType")) != STEP_TYPE:
                    continue

                data_date = int(codes_get(gid, "dataDate"))
                data_time = int(codes_get(gid, "dataTime"))
                msg_issue_utc = _issue_utc_from_fields(data_date, data_time)
                if msg_issue_utc.date() != issue_utc.date():
                    continue

                start_step = int(codes_get(gid, "startStep"))
                end_step = int(codes_get(gid, "endStep"))
                step_range = f"{start_step}-{end_step}"
                window_start = msg_issue_utc + timedelta(hours=start_step)
                window_end = msg_issue_utc + timedelta(hours=end_step)

                member = 0 if is_control else int(codes_get(gid, "number"))

                for city_name, city_cfg in city_map.items():
                    city_tz = city_cfg["timezone"]
                    city_unit = city_cfg["unit"]
                    city_lat = float(city_cfg["lat"])
                    city_lon = float(city_cfg["lon"])

                    for target_date in _iter_overlap_local_dates(window_start, window_end, city_tz):
                        day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, city_tz)
                        overlap = _overlap_seconds(window_start, window_end, day_start_utc, day_end_utc)
                        if overlap <= 0:
                            continue
                        lead_day = (target_date - issue_utc.date()).days
                        if lead_day < 0 or lead_day > max_target_lead_day:
                            continue

                        key = (city_name, target_date.isoformat())
                        if key not in accum:
                            accum[key] = {
                                "city": city_cfg,
                                "issue_utc": msg_issue_utc,
                                "target_date": target_date,
                                "lead_day": lead_day,
                                "manifest_sha": manifest_sha,
                                "nearest_lat": None,
                                "nearest_lon": None,
                                "nearest_dist": None,
                                "member_values": {},
                                "selected_steps": [],
                            }
                        rec = accum[key]

                        try:
                            nearest = codes_grib_find_nearest(gid, city_lat, city_lon)[0]
                        except Exception:
                            # City outside this regional GRIB bounding box — skip silently.
                            continue
                        value_k = float(nearest["value"])
                        value_native = _kelvin_to_native(value_k, city_unit)

                        if rec["nearest_lat"] is None:
                            rec["nearest_lat"] = float(nearest["lat"])
                            rec["nearest_lon"] = float(nearest["lon"])
                            rec["nearest_dist"] = float(nearest["distance"])

                        if step_range not in rec["selected_steps"]:
                            rec["selected_steps"].append(step_range)

                        mv = rec["member_values"]
                        if member not in mv or mv[member] is None:
                            mv[member] = value_native
                        else:
                            mv[member] = max(mv[member], value_native)

            finally:
                codes_release(gid)


def _finalize_record(record: dict[str, Any], manifest_sha: str) -> dict:
    city_cfg = record["city"]
    city_name = city_cfg.get("city") or city_cfg.get("name") or ""
    city_unit = city_cfg["unit"]
    city_tz = city_cfg["timezone"]
    issue_utc: datetime = record["issue_utc"]
    target_date: date = record["target_date"]
    lead_day: int = record["lead_day"]
    mv: dict[int, float | None] = record["member_values"]

    members_out = []
    missing = []
    for m in range(MEMBER_COUNT):
        val = mv.get(m)
        if val is None:
            missing.append(m)
        members_out.append({"member": m, "value_native_unit": val})

    day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, city_tz)

    # CRITICAL-1: DST-aware step horizon
    dst_offset_h = int(ZoneInfo(city_tz).utcoffset(issue_utc).total_seconds() / 3600)
    step_horizon_hours = compute_required_max_step(issue_utc, target_date, dst_offset_h)
    selected_steps: list[str] = record.get("selected_steps", [])
    max_present_step = 0
    for sr in selected_steps:
        try:
            end_part = int(sr.split("-")[1])
            max_present_step = max(max_present_step, end_part)
        except (IndexError, ValueError):
            pass
    horizon_satisfied = (max_present_step >= step_horizon_hours) if selected_steps else False
    step_horizon_deficit_hours = (step_horizon_hours - max_present_step) if not horizon_satisfied else 0

    # CRITICAL-2: wire causality; training_allowed requires pure_forecast_valid
    causality = compute_causality(issue_utc, target_date, dst_offset_h)
    training_allowed = (len(missing) == 0) and horizon_satisfied and causality["pure_forecast_valid"]

    provenance_fields = {
        "data_version": DATA_VERSION,
        "physical_quantity": PHYSICAL_QUANTITY,
        "manifest_sha256": manifest_sha,
        "issue_time_utc": issue_utc.isoformat(),
        "city": city_name,
        "target_date_local": target_date.isoformat(),
    }
    mhash = compute_manifest_hash(provenance_fields)

    payload = TiggeSnapshotPayload.make_high_track(
        generated_at=_now_utc_iso(),
        data_version=DATA_VERSION,
        physical_quantity=PHYSICAL_QUANTITY,
        param=PARAM,
        paramId=PARAM_ID,
        short_name=SHORT_NAME,
        step_type=STEP_TYPE,
        aggregation_window_hours=AGGREGATION_WINDOW_HOURS,
        city=city_name,
        lat=float(city_cfg["lat"]),
        lon=float(city_cfg["lon"]),
        unit=city_unit,
        timezone=city_tz,
        manifest_sha256=manifest_sha,
        manifest_hash=mhash,
        issue_time_utc=issue_utc.isoformat(),
        target_date_local=target_date.isoformat(),
        lead_day=lead_day,
        lead_day_anchor="issue_utc.date()",
        local_day_start_utc=day_start_utc.isoformat(),
        local_day_end_utc=day_end_utc.isoformat(),
        local_day_window={
            "start": day_start_utc.isoformat(),
            "end": day_end_utc.isoformat(),
        },
        step_horizon_hours=float(step_horizon_hours),
        step_horizon_deficit_hours=step_horizon_deficit_hours,
        causality=causality,
        nearest_grid_lat=record["nearest_lat"],
        nearest_grid_lon=record["nearest_lon"],
        nearest_grid_distance_km=record["nearest_dist"],
        selected_step_ranges=sorted(record["selected_steps"]),
        member_count=MEMBER_COUNT,
        missing_members=missing,
        training_allowed=training_allowed,
        members=members_out,
    )
    return payload.to_json_dict()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--output-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--max-target-lead-day", type=int, default=7)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    cities_config = _load_cities_config()

    summary = extract_track(
        raw_root=args.raw_root,
        output_root=args.output_root,
        manifest_path=args.manifest_path,
        cities_config=cities_config,
        cities=set(args.cities) if args.cities else None,
        date_from=date.fromisoformat(args.date_from) if args.date_from else None,
        date_to=date.fromisoformat(args.date_to) if args.date_to else None,
        max_target_lead_day=args.max_target_lead_day,
        max_pairs=args.max_pairs,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("errors", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
