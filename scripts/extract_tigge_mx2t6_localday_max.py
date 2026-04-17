# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
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
#        tests/test_phase4_6_cities_drift.py are the contract.
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
import hashlib
import json
import logging
import math
import re
import sys
from dataclasses import dataclass
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

from src.types.metric_identity import HIGH_LOCALDAY_MAX  # noqa: E402

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
RAW_ROOT = FIFTY_ONE_ROOT / "raw"
DEFAULT_MANIFEST = FIFTY_ONE_ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
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
AGGREGATION_WINDOW_HOURS = 6
MEMBER_COUNT = 51

_STEP_HOURS = 6
_CEIL_EPSILON_HOURS = 1e-9
_CITY_COORDINATE_TOLERANCE_DEG = 0.01


class CityManifestDriftError(RuntimeError):
    """Raised when Zeus cities.json and TIGGE manifest disagree on city names or coordinates.

    Prevents silent wrong-grid extraction when canonical city config diverges from
    the TIGGE manifest used to define bounding boxes.
    """


# ---------------------------------------------------------------------------
# Public API — testeng-grace's 5-function surface
# ---------------------------------------------------------------------------


def compute_required_max_step(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> int:
    """Return minimum step hours to cover end of target_date in city's local day.

    Uses fixed offset (not ZoneInfo) because tests pass a numeric offset.
    Actual extraction uses ZoneInfo for precision. Result is 6h-aligned.
    """
    fixed_tz = timezone(timedelta(hours=city_utc_offset_hours))
    # local midnight at start of day after target_date = end of target_date local day
    next_day = target_date + timedelta(days=1)
    local_day_end_local = datetime.combine(next_day, dt_time.min, tzinfo=fixed_tz)
    local_day_end_utc = local_day_end_local.astimezone(timezone.utc)
    delta_hours = (local_day_end_utc - issue_utc).total_seconds() / 3600.0
    return _ceil_to_next_6h(delta_hours)


def compute_manifest_hash(fields: dict) -> str:
    """SHA-256 of sorted-JSON of fields dict. Stable regardless of key order."""
    canon = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()



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

    return {
        "generated_at": _now_utc_iso(),
        "data_version": DATA_VERSION,
        "physical_quantity": PHYSICAL_QUANTITY,
        "param": PARAM,
        "paramId": PARAM_ID,
        "short_name": SHORT_NAME,
        "step_type": STEP_TYPE,
        "aggregation_window_hours": AGGREGATION_WINDOW_HOURS,
        "city": city_name,
        "lat": city_lat,
        "lon": city_lon,
        "unit": city_unit,
        "manifest_sha256": manifest_sha256_value,
        "issue_time_utc": issue_utc.isoformat(),
        "target_date_local": target_date.isoformat(),
        "lead_day": lead_day,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": city_tz,
        # MAJOR-1: top-level provenance fields for ensemble_snapshots_v2
        "local_day_start_utc": day_start_utc.isoformat(),
        "local_day_end_utc": day_end_utc.isoformat(),
        "step_horizon_hours": float(step_horizon_hours),
        "step_horizon_deficit_hours": step_horizon_deficit_hours,
        "local_day_window": {
            "start": day_start_utc.isoformat(),
            "end": day_end_utc.isoformat(),
        },
        "causality": causality,
        "boundary_ambiguous": False,  # high track: no boundary quarantine (MAJOR-2)
        "nearest_grid_lat": nearest_lat,
        "nearest_grid_lon": nearest_lon,
        "nearest_grid_distance_km": nearest_dist,
        "selected_step_ranges": sorted(selected_steps),
        "member_count": MEMBER_COUNT,
        "missing_members": missing,
        "training_allowed": training_allowed,
        "manifest_hash": manifest_hash,
        "members": members_out,
    }


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
# Internal helpers
# ---------------------------------------------------------------------------


def _ceil_to_next_6h(hours: float) -> int:
    adjusted = float(hours) - _CEIL_EPSILON_HOURS
    return max(_STEP_HOURS, int(math.ceil(adjusted / _STEP_HOURS) * _STEP_HOURS))


def _kelvin_to_native(value_k: float, unit: str) -> float:
    u = str(unit).strip().upper()
    if u not in {"C", "F"}:
        raise ValueError(f"Unknown unit {unit!r}; expected 'C' or 'F' (never 'K' or 'degC')")
    value_c = value_k - 273.15
    if u == "F":
        return value_c * 9.0 / 5.0 + 32.0
    return value_c


def _local_day_bounds_utc(target_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(str(timezone_name))
    start_local = datetime.combine(target_date, dt_time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _overlap_seconds(
    window_start: datetime,
    window_end: datetime,
    target_start: datetime,
    target_end: datetime,
) -> int:
    latest_start = max(window_start, target_start)
    earliest_end = min(window_end, target_end)
    seconds = (earliest_end - latest_start).total_seconds()
    return max(0, int(seconds))


def _issue_utc_from_fields(data_date: int, data_time: int) -> datetime:
    d = datetime.strptime(str(data_date), "%Y%m%d").date()
    time_str = f"{int(data_time):04d}"
    hh = int(time_str[:2])
    mm = int(time_str[2:])
    return datetime.combine(d, dt_time(hour=hh, minute=mm), tzinfo=timezone.utc)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _city_slug(city_name: str) -> str:
    return str(city_name).strip().lower().replace(" ", "-")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _get_city_config(city_name: str, cities_config: list[dict] | None) -> dict:
    if cities_config is None:
        cities_config = _load_cities_config()
    for c in cities_config:
        if c.get("city") == city_name or c.get("name") == city_name:
            return c
    raise ValueError(f"City {city_name!r} not found in cities config")


def _cross_validate_city_manifests(cities_config: list[dict], manifest_path: Path) -> None:
    """Assert Zeus cities.json and TIGGE manifest agree on city names and coordinates.

    Authoritative source for city names: config/cities.json (Zeus canonical).
    Fail-closed: raises CityManifestDriftError on any mismatch.
    Tolerance: ±0.01° for lat/lon. Cities without lat/lon in Zeus cities.json
    (11 US °F cities) are skipped for coordinate check — manifest is authoritative
    for their grid coordinates.
    """
    zeus_map = {row.get("name") or row.get("city", ""): row for row in cities_config}
    zeus_names = set(zeus_map.keys())

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_map = {row["city"]: row for row in manifest_data["cities"]}
    manifest_names = set(manifest_map.keys())

    only_in_zeus = zeus_names - manifest_names
    only_in_manifest = manifest_names - zeus_names
    if only_in_zeus or only_in_manifest:
        raise CityManifestDriftError(
            f"City name sets differ between Zeus cities.json and TIGGE manifest. "
            f"Only in Zeus: {sorted(only_in_zeus)}. "
            f"Only in manifest: {sorted(only_in_manifest)}."
        )

    tol = _CITY_COORDINATE_TOLERANCE_DEG
    for city_name in zeus_names:
        zeus_row = zeus_map[city_name]
        if "lat" not in zeus_row or "lon" not in zeus_row:
            continue
        zeus_lat = float(zeus_row["lat"])
        zeus_lon = float(zeus_row["lon"])
        mfst_lat = float(manifest_map[city_name]["lat"])
        mfst_lon = float(manifest_map[city_name]["lon"])
        lat_drift = abs(zeus_lat - mfst_lat)
        lon_drift = abs(zeus_lon - mfst_lon)
        if lat_drift > tol or lon_drift > tol:
            raise CityManifestDriftError(
                f"Coordinate drift for {city_name!r}: "
                f"zeus=({zeus_lat}, {zeus_lon}) manifest=({mfst_lat}, {mfst_lon}) "
                f"drift=({lat_drift:.4f}°, {lon_drift:.4f}°) tolerance=±{tol}°"
            )


def _load_cities_config() -> list[dict]:
    """Load city configs from TIGGE coordinate manifest (has top-level lat/lon)."""
    data = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    rows = data["cities"]
    # Normalise: manifest uses key "city"; add "name" alias for callers that expect it.
    out = []
    for row in rows:
        entry = dict(row)
        entry.setdefault("name", entry.get("city", ""))
        out.append(entry)
    return out


def _parse_steps_from_filename(path: Path) -> list[int]:
    match = re.search(r"_steps_([0-9-]+)\.grib$", path.name)
    if not match:
        raise ValueError(f"Could not parse step slug from {path.name!r}")
    return [int(p) for p in match.group(1).split("-")]


def _parse_dates_from_dirname(dirname: str) -> list[date]:
    if "_" not in dirname:
        return [datetime.strptime(dirname, "%Y%m%d").date()]
    start_s, end_s = dirname.split("_", 1)
    start = datetime.strptime(start_s, "%Y%m%d").date()
    end = datetime.strptime(end_s, "%Y%m%d").date()
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


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


def _iter_overlap_local_dates(
    window_start_utc: datetime,
    window_end_utc: datetime,
    timezone_name: str,
) -> list[date]:
    tz = ZoneInfo(str(timezone_name))
    local_start = window_start_utc.astimezone(tz)
    local_end_excl = (window_end_utc - timedelta(microseconds=1)).astimezone(tz)
    return sorted({local_start.date(), local_end_excl.date()})


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

    return {
        "generated_at": _now_utc_iso(),
        "data_version": DATA_VERSION,
        "physical_quantity": PHYSICAL_QUANTITY,
        "param": PARAM,
        "paramId": PARAM_ID,
        "short_name": SHORT_NAME,
        "step_type": STEP_TYPE,
        "aggregation_window_hours": AGGREGATION_WINDOW_HOURS,
        "city": city_name,
        "lat": float(city_cfg["lat"]),
        "lon": float(city_cfg["lon"]),
        "unit": city_unit,
        "manifest_sha256": manifest_sha,
        "issue_time_utc": issue_utc.isoformat(),
        "target_date_local": target_date.isoformat(),
        "lead_day": lead_day,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": city_tz,
        # MAJOR-1: top-level provenance fields for ensemble_snapshots_v2
        "local_day_start_utc": day_start_utc.isoformat(),
        "local_day_end_utc": day_end_utc.isoformat(),
        "step_horizon_hours": float(step_horizon_hours),
        "step_horizon_deficit_hours": step_horizon_deficit_hours,
        "local_day_window": {
            "start": day_start_utc.isoformat(),
            "end": day_end_utc.isoformat(),
        },
        "causality": causality,
        "boundary_ambiguous": False,  # high track: no boundary quarantine (MAJOR-2)
        "nearest_grid_lat": record["nearest_lat"],
        "nearest_grid_lon": record["nearest_lon"],
        "nearest_grid_distance_km": record["nearest_dist"],
        "selected_step_ranges": sorted(record["selected_steps"]),
        "member_count": MEMBER_COUNT,
        "missing_members": missing,
        "training_allowed": training_allowed,
        "manifest_hash": mhash,
        "members": members_out,
    }


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
