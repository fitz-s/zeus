# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
# Purpose: GRIB→JSON extractor for mn2t6 local-calendar-day min (low track);
#          emits canonical payload per 08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md §5;
#          implements Phase 5B R-AG (boundary quarantine), R-AH (causality N/A), R-AI (data_version).
# Reuse: Before running, confirm (1) config/cities.json canonical source,
#        (2) 51 source data/docs/tigge_city_coordinate_manifest_full_latest.json aligns,
#        (3) eccodes installed. Test contract: tests/test_phase5b_low_historical_lane.py.
#!/usr/bin/env python3
"""GRIB→JSON extractor for mn2t6 local-calendar-day min (low track only).

Reads raw TIGGE ECMWF ensemble GRIB files (param=122.128, mn2t6, 6h min) and
produces one JSON file per (city, issue_date, target_local_date, lead_day).

Output contract matches ingest_grib_to_snapshots.py:ingest_json_file expectations:
- data_version      = "tigge_mn2t6_local_calendar_day_min_v1"
- temperature_metric = "low"
- members_unit      = "K"  (Kelvin; ingest normalises to degC/degF via city unit)
- members           = 51 elements, member 0 = control, 1..50 = perturbed
- causality         = always present; status="N/A_CAUSAL_DAY_ALREADY_STARTED" for
                      positive-offset cities where local day has already started at issue_utc
- boundary_policy   = always present; boundary_ambiguous=True quarantines whole snapshot
                      from calibration training (training_allowed=False)

Boundary-leakage law (§6 of TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md):
  For each member: boundary_ambiguous_member = boundary_min <= inner_min
  Snapshot: boundary_ambiguous = any(boundary_ambiguous_member); propagates training_allowed=False.

NOT a polarity swap of extract_tigge_mx2t6_localday_max.py. The boundary
semantics differ — MIN's sunrise window often lands in a boundary bucket,
making the conservative quarantine load-bearing (not just "close to edge").

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
from dataclasses import dataclass, field
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

from src.types.metric_identity import LOW_LOCALDAY_MIN  # noqa: E402

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
RAW_ROOT = FIFTY_ONE_ROOT / "raw"
DEFAULT_MANIFEST = FIFTY_ONE_ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
GRIB_SUBDIR = "tigge_ecmwf_ens_regions_mn2t6"
OUTPUT_SUBDIR = "tigge_ecmwf_ens_mn2t6_localday_min"
OUTPUT_FILENAME_PREFIX = "tigge_ecmwf_mn2t6_localday_min"

DATA_VERSION = LOW_LOCALDAY_MIN.data_version
PHYSICAL_QUANTITY = LOW_LOCALDAY_MIN.physical_quantity
TEMPERATURE_METRIC = LOW_LOCALDAY_MIN.temperature_metric
PARAM = "122.128"
PARAM_ID = 122
SHORT_NAME = "mn2t6"
STEP_TYPE = "min"
AGGREGATION_WINDOW_HOURS = 6
MEMBER_COUNT = 51
MEMBERS_UNIT = "K"

_STEP_HOURS = 6
_CEIL_EPSILON_HOURS = 1e-9
_CITY_COORDINATE_TOLERANCE_DEG = 0.01


class CityManifestDriftError(RuntimeError):
    """Raised when Zeus cities.json and TIGGE manifest disagree on city names or coordinates."""


@dataclass
class BoundaryClassification:
    """Per-member bucket classification result for one local-day window."""
    inner_values: list[float] = field(default_factory=list)
    boundary_values: list[float] = field(default_factory=list)
    inner_step_ranges: list[str] = field(default_factory=list)
    boundary_step_ranges: list[str] = field(default_factory=list)

    @property
    def inner_min(self) -> float | None:
        return min(self.inner_values) if self.inner_values else None

    @property
    def boundary_min(self) -> float | None:
        return min(self.boundary_values) if self.boundary_values else None

    @property
    def boundary_ambiguous(self) -> bool:
        """True when boundary bucket can determine the local-day minimum.

        MIN-specific rule: if boundary_min <= inner_min, the boundary zone
        could be the true local-day minimum, introducing look-ahead leakage.
        Ambiguity also applies when there are no inner buckets (boundary-only coverage).
        """
        if self.boundary_min is None:
            return False
        if self.inner_min is None:
            return True
        return self.boundary_min <= self.inner_min

    @property
    def effective_min(self) -> float | None:
        """The min value usable for training: inner_min only when not boundary_ambiguous."""
        if self.boundary_ambiguous:
            return None
        return self.inner_min


# ---------------------------------------------------------------------------
# Public API — testeng-grace's 5-function surface
# ---------------------------------------------------------------------------


def classify_boundary_low(
    step_values: dict[str, float],
    day_start_utc: datetime,
    day_end_utc: datetime,
    issue_utc: datetime,
) -> BoundaryClassification:
    """Classify 6h GRIB bucket values for one member into inner/boundary/outside.

    step_values: dict mapping step_range string (e.g. "6-12") to value in Kelvin.
    Returns BoundaryClassification with inner and boundary values separated.

    MIN-specific semantics: boundary_ambiguous = boundary_min <= inner_min.
    The sunrise hour frequently lands in a boundary bucket for some timezones,
    so this rule can cause high quarantine rates — that is expected and intentional.
    """
    result = BoundaryClassification()
    for step_range, value_k in step_values.items():
        try:
            start_h, end_h = (int(x) for x in step_range.split("-"))
        except ValueError:
            continue
        window_start = issue_utc + timedelta(hours=start_h)
        window_end = issue_utc + timedelta(hours=end_h)

        overlap = _overlap_seconds(window_start, window_end, day_start_utc, day_end_utc)
        if overlap <= 0:
            continue

        fully_inside = (window_start >= day_start_utc and window_end <= day_end_utc)
        if fully_inside:
            result.inner_values.append(value_k)
            if step_range not in result.inner_step_ranges:
                result.inner_step_ranges.append(step_range)
        else:
            result.boundary_values.append(value_k)
            if step_range not in result.boundary_step_ranges:
                result.boundary_step_ranges.append(step_range)

    return result


def extract_city_vectors_low(
    grib_path: Path,
    city_lat: float,
    city_lon: float,
    issue_utc: datetime,
    day_start_utc: datetime,
    day_end_utc: datetime,
    *,
    is_control: bool,
) -> dict[int, dict[str, float]]:
    """Extract per-member step→value_K dicts from one GRIB file for one city.

    Returns {member_id: {step_range: value_K}} for all overlapping buckets.
    Members outside the GRIB bounding box are silently skipped (empty dict).
    Values are raw Kelvin — no unit conversion here.
    """
    member_step_values: dict[int, dict[str, float]] = {}

    with grib_path.open("rb") as fh:
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
                overlap = _overlap_seconds(window_start, window_end, day_start_utc, day_end_utc)
                if overlap <= 0:
                    continue

                member = 0 if is_control else int(codes_get(gid, "number"))

                try:
                    nearest = codes_grib_find_nearest(gid, city_lat, city_lon)[0]
                except Exception:
                    continue
                value_k = float(nearest["value"])

                if member not in member_step_values:
                    member_step_values[member] = {}
                member_step_values[member][step_range] = min(
                    member_step_values[member].get(step_range, value_k), value_k
                )

            finally:
                codes_release(gid)

    return member_step_values


def build_low_snapshot_json(
    *,
    city_name: str,
    city_tz: str,
    issue_utc: datetime,
    target_date: date,
    lead_day: int,
    member_step_values: dict[int, dict[str, float]],
    manifest_sha256_value: str,
    nearest_lat: float | None = None,
    nearest_lon: float | None = None,
    nearest_dist: float | None = None,
) -> dict:
    """Assemble the canonical low-track JSON payload for one (city, issue, target) slot.

    member_step_values: {member_id: {step_range: value_K}} — raw Kelvin from extract_city_vectors_low.
    Applies boundary-leakage law per §6 of remediation plan.
    Applies causality law per §5: positive-offset cities emit N/A_CAUSAL_DAY_ALREADY_STARTED.
    """
    day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, city_tz)

    # R-AS: use offset at target-date local midnight, not at issue_utc.
    # For DST-boundary crossings (e.g. issue_utc=winter, target_date=summer), the offset
    # at issue_utc is stale and produces a 1h step-horizon drift.
    target_midnight_local = datetime.combine(target_date, dt_time.min, tzinfo=ZoneInfo(city_tz))
    dst_offset_h = int(ZoneInfo(city_tz).utcoffset(target_midnight_local).total_seconds() / 3600)
    causality = _compute_causality_low(issue_utc, target_date, dst_offset_h)

    # Per-member boundary classification
    member_classifications: dict[int, BoundaryClassification] = {}
    for m in range(MEMBER_COUNT):
        step_vals = member_step_values.get(m, {})
        member_classifications[m] = classify_boundary_low(
            step_vals, day_start_utc, day_end_utc, issue_utc
        )

    any_boundary_ambiguous = any(
        c.boundary_ambiguous for c in member_classifications.values()
    )
    ambiguous_member_count = sum(
        1 for c in member_classifications.values() if c.boundary_ambiguous
    )

    # Build members list — emit raw Kelvin (members_unit='K')
    members_out = []
    missing = []
    for m in range(MEMBER_COUNT):
        clf = member_classifications[m]
        if any_boundary_ambiguous:
            # R-AR: whole snapshot quarantined — emit None, not inner_min.
            # A non-null value would let downstream consumers read it without
            # checking training_allowed and get contaminated data.
            val_k = None
        else:
            val_k = clf.effective_min
        if val_k is None:
            missing.append(m)
        members_out.append({"member": m, "value_native_unit": val_k})

    step_horizon_hours = _compute_required_max_step(issue_utc, target_date, dst_offset_h)
    all_step_ranges: set[str] = set()
    for step_vals in member_step_values.values():
        all_step_ranges.update(step_vals.keys())
    max_present_step = _max_step_from_ranges(all_step_ranges)
    horizon_satisfied = (max_present_step >= step_horizon_hours) if all_step_ranges else False
    step_horizon_deficit_hours = (step_horizon_hours - max_present_step) if not horizon_satisfied else 0

    # training_allowed: all members present + horizon satisfied + causality OK + no boundary ambiguity
    training_allowed = (
        len(missing) == 0
        and horizon_satisfied
        and causality["pure_forecast_valid"]
        and not any_boundary_ambiguous
    )

    # Collect inner/boundary step ranges across all members for diagnostics
    all_inner_ranges: set[str] = set()
    all_boundary_ranges: set[str] = set()
    for clf in member_classifications.values():
        all_inner_ranges.update(clf.inner_step_ranges)
        all_boundary_ranges.update(clf.boundary_step_ranges)

    provenance_fields = {
        "data_version": DATA_VERSION,
        "physical_quantity": PHYSICAL_QUANTITY,
        "manifest_sha256": manifest_sha256_value,
        "issue_time_utc": issue_utc.isoformat(),
        "city": city_name,
        "target_date_local": target_date.isoformat(),
    }
    manifest_hash = _compute_manifest_hash(provenance_fields)

    return {
        "generated_at": _now_utc_iso(),
        "data_version": DATA_VERSION,
        "physical_quantity": PHYSICAL_QUANTITY,
        "temperature_metric": TEMPERATURE_METRIC,
        "param": PARAM,
        "paramId": PARAM_ID,
        "short_name": SHORT_NAME,
        "step_type": STEP_TYPE,
        "aggregation_window_hours": AGGREGATION_WINDOW_HOURS,
        "city": city_name,
        "lat": nearest_lat,
        "lon": nearest_lon,
        "unit": MEMBERS_UNIT,
        "members_unit": MEMBERS_UNIT,
        "manifest_sha256": manifest_sha256_value,
        "issue_time_utc": issue_utc.isoformat(),
        "target_date_local": target_date.isoformat(),
        "lead_day": lead_day,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": city_tz,
        "local_day_start_utc": day_start_utc.isoformat(),
        "local_day_end_utc": day_end_utc.isoformat(),
        "step_horizon_hours": float(step_horizon_hours),
        "step_horizon_deficit_hours": step_horizon_deficit_hours,
        "local_day_window": {
            "start": day_start_utc.isoformat(),
            "end": day_end_utc.isoformat(),
        },
        "causality": causality,
        "boundary_policy": {
            "training_rule": "use_inner_only_and_exclude_if_boundary_can_win",
            "boundary_ambiguous": any_boundary_ambiguous,
            "ambiguous_member_count": ambiguous_member_count,
        },
        "selected_step_ranges_inner": sorted(all_inner_ranges),
        "selected_step_ranges_boundary": sorted(all_boundary_ranges),
        "nearest_grid_lat": nearest_lat,
        "nearest_grid_lon": nearest_lon,
        "nearest_grid_distance_km": nearest_dist,
        "member_count": MEMBER_COUNT,
        "missing_members": missing,
        "training_allowed": training_allowed,
        "manifest_hash": manifest_hash,
        "members": members_out,
    }


def validate_low_extraction(payload: dict) -> list[str]:
    """Validate a low-track JSON payload for structural completeness.

    Returns a list of violation strings. Empty list = valid.
    Does NOT import from src.contracts — structural check only.
    """
    violations = []

    if payload.get("data_version") != DATA_VERSION:
        violations.append(f"data_version mismatch: {payload.get('data_version')!r} != {DATA_VERSION!r}")

    if payload.get("temperature_metric") != TEMPERATURE_METRIC:
        violations.append(f"temperature_metric mismatch: {payload.get('temperature_metric')!r}")

    if payload.get("members_unit") != MEMBERS_UNIT:
        violations.append(f"members_unit missing or wrong: {payload.get('members_unit')!r}")

    causality = payload.get("causality")
    if not isinstance(causality, dict):
        violations.append("causality field missing or not a dict")
    elif "status" not in causality:
        violations.append("causality.status field missing")

    bp = payload.get("boundary_policy")
    if not isinstance(bp, dict):
        violations.append("boundary_policy field missing or not a dict")
    elif "boundary_ambiguous" not in bp:
        violations.append("boundary_policy.boundary_ambiguous field missing")

    members = payload.get("members")
    if not isinstance(members, list) or len(members) != MEMBER_COUNT:
        violations.append(f"members must be list of {MEMBER_COUNT}; got {type(members)} len={len(members) if isinstance(members, list) else 'N/A'}")

    return violations


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    cities_config = _load_cities_config()

    manifest_path = args.manifest_path
    if manifest_path is None or not manifest_path.exists():
        logger.error("manifest_path required and must exist: %s", manifest_path)
        return 1

    if args.manifest_path is not None:
        _cross_validate_city_manifests(cities_config, manifest_path)

    manifest_sha = _file_sha256(manifest_path)

    city_map = {c["name"]: c for c in cities_config}
    if args.cities:
        city_map = {k: v for k, v in city_map.items() if k in args.cities}

    grib_subdir = args.raw_root / GRIB_SUBDIR
    pairs = _find_region_pairs(grib_subdir)
    if args.max_pairs is not None:
        pairs = pairs[: max(0, int(args.max_pairs))]

    date_from = date.fromisoformat(args.date_from) if args.date_from else None
    date_to = date.fromisoformat(args.date_to) if args.date_to else None

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
            accum: dict[tuple[str, str], dict[str, Any]] = {}

            for file_path in [pair["cf_path"], pair["pf_path"]]:
                is_control = "control" in file_path.name
                _collect_grib_file_low(
                    file_path=file_path,
                    is_control=is_control,
                    issue_utc=issue_utc,
                    city_map=city_map,
                    max_target_lead_day=args.max_target_lead_day,
                    manifest_sha=manifest_sha,
                    accum=accum,
                )

            for (city_name, target_date_str), record in sorted(accum.items()):
                target_date = date.fromisoformat(target_date_str)
                lead_day = (target_date - issue_date).days
                output_path = _output_path(args.output_root, city_name, issue_date, target_date, lead_day)

                if output_path.exists() and not args.overwrite:
                    skipped += 1
                    continue

                try:
                    payload = build_low_snapshot_json(
                        city_name=city_name,
                        city_tz=record["city_tz"],
                        issue_utc=issue_utc,
                        target_date=target_date,
                        lead_day=lead_day,
                        member_step_values=record["member_step_values"],
                        manifest_sha256_value=manifest_sha,
                        nearest_lat=record["nearest_lat"],
                        nearest_lon=record["nearest_lon"],
                        nearest_dist=record["nearest_dist"],
                    )
                    violations = validate_low_extraction(payload)
                    if violations:
                        logger.warning("Payload violations for %s %s: %s", city_name, target_date_str, violations)
                        errors += 1
                        continue
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    written += 1
                except Exception as exc:
                    logger.warning("Error writing %s: %s", output_path, exc)
                    errors += 1

    summary = {
        "data_version": DATA_VERSION,
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "pair_count": len(pairs),
        "output_root": str(args.output_root / OUTPUT_SUBDIR),
    }
    print(json.dumps(summary, indent=2))
    return 0 if errors == 0 else 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_required_max_step(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> int:
    """Return minimum step hours to cover end of target_date in city's local day."""
    fixed_tz = timezone(timedelta(hours=city_utc_offset_hours))
    next_day = target_date + timedelta(days=1)
    local_day_end_local = datetime.combine(next_day, dt_time.min, tzinfo=fixed_tz)
    local_day_end_utc = local_day_end_local.astimezone(timezone.utc)
    delta_hours = (local_day_end_utc - issue_utc).total_seconds() / 3600.0
    return _ceil_to_next_6h(delta_hours)


def _compute_manifest_hash(fields: dict) -> str:
    canon = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def _compute_causality_low(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> dict:
    """Compute causality dict for low track.

    positive-offset cities (UTC+N): local day starts before 00Z → N/A_CAUSAL_DAY_ALREADY_STARTED
    negative-offset cities (UTC-N): local midnight is after 00Z → day0 is forecastable
    """
    fixed_tz = timezone(timedelta(hours=city_utc_offset_hours))
    local_day_start_local = datetime.combine(target_date, dt_time.min, tzinfo=fixed_tz)
    local_day_start_utc = local_day_start_local.astimezone(timezone.utc)
    pure_forecast_valid = issue_utc <= local_day_start_utc
    status = "OK" if pure_forecast_valid else "N/A_CAUSAL_DAY_ALREADY_STARTED"
    return {"pure_forecast_valid": pure_forecast_valid, "status": status}


def _ceil_to_next_6h(hours: float) -> int:
    adjusted = float(hours) - _CEIL_EPSILON_HOURS
    return max(_STEP_HOURS, int(math.ceil(adjusted / _STEP_HOURS) * _STEP_HOURS))


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


def _max_step_from_ranges(step_ranges: set[str]) -> int:
    max_step = 0
    for sr in step_ranges:
        try:
            end_part = int(sr.split("-")[1])
            max_step = max(max_step, end_part)
        except (IndexError, ValueError):
            pass
    return max_step


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
    zeus_map = {row.get("name") or row.get("city", ""): row for row in cities_config}
    zeus_names = set(zeus_map.keys())

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_map = {row["city"]: row for row in manifest_data["cities"]}
    manifest_names = set(manifest_map.keys())

    only_in_zeus = zeus_names - manifest_names
    only_in_manifest = manifest_names - zeus_names
    if only_in_zeus or only_in_manifest:
        raise CityManifestDriftError(
            f"City name sets differ. Only in Zeus: {sorted(only_in_zeus)}. "
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
    data = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    rows = data["cities"]
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


def _collect_grib_file_low(
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
                                "city_tz": city_tz,
                                "manifest_sha": manifest_sha,
                                "nearest_lat": None,
                                "nearest_lon": None,
                                "nearest_dist": None,
                                "member_step_values": {},
                            }
                        rec = accum[key]

                        try:
                            nearest = codes_grib_find_nearest(gid, city_lat, city_lon)[0]
                        except Exception:
                            continue
                        value_k = float(nearest["value"])

                        if rec["nearest_lat"] is None:
                            rec["nearest_lat"] = float(nearest["lat"])
                            rec["nearest_lon"] = float(nearest["lon"])
                            rec["nearest_dist"] = float(nearest["distance"])

                        mv = rec["member_step_values"]
                        if member not in mv:
                            mv[member] = {}
                        mv[member][step_range] = min(mv[member].get(step_range, value_k), value_k)

            finally:
                codes_release(gid)


def _build_parser() -> argparse.ArgumentParser:
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
