# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=2026-04-18
# Purpose: Phase 7B-followup — shared helpers/constants/class for
#          extract_tigge_mx2t6_localday_max.py + extract_tigge_mn2t6_localday_min.py.
#          Consolidates 13 identical private helpers + CityManifestDriftError +
#          compute_required_max_step + compute_manifest_hash. Per-extractor
#          divergent constants (GRIB_SUBDIR, OUTPUT_SUBDIR, OUTPUT_FILENAME_PREFIX,
#          PARAM, PARAM_ID, SHORT_NAME, STEP_TYPE, DATA_VERSION, PHYSICAL_QUANTITY)
#          stay in each extractor.
# Reuse: Leaf utility module imported by extract_tigge_* scripts only. No Zeus
#        runtime path depends on it. If you need more helpers, verify
#        parallel-identical between both extractors first — the planner's pre-P7B
#        "15 safe helpers" claim was inaccurate because _output_path uses
#        OUTPUT_FILENAME_PREFIX/OUTPUT_SUBDIR (differ) and _find_region_pairs
#        uses PARAM (differs).
"""Shared utilities for TIGGE GRIB→JSON extractors.

Invariants preserved by this module:
- Zero dependency on per-extractor module-level constants (GRIB_SUBDIR,
  OUTPUT_SUBDIR, OUTPUT_FILENAME_PREFIX, PARAM, PARAM_ID, SHORT_NAME, STEP_TYPE,
  DATA_VERSION, PHYSICAL_QUANTITY). Caller passes any needed value explicitly.
- Shared constants here (PROJECT_ROOT, FIFTY_ONE_ROOT, RAW_ROOT,
  DEFAULT_MANIFEST, AGGREGATION_WINDOW_HOURS, MEMBER_COUNT, _STEP_HOURS,
  _CEIL_EPSILON_HOURS, _CITY_COORDINATE_TOLERANCE_DEG) are identical across
  extractors — extraction is refactor-only, no semantic change.
- Backward-compat: extractors re-export symbols under their historical names
  (including private aliases like _compute_required_max_step for
  test_phase5_fixpack.py).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Shared constants (identical across both extractors)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
RAW_ROOT = FIFTY_ONE_ROOT / "raw"
DEFAULT_MANIFEST = FIFTY_ONE_ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"

AGGREGATION_WINDOW_HOURS = 6
MEMBER_COUNT = 51

_STEP_HOURS = 6
_CEIL_EPSILON_HOURS = 1e-9
_CITY_COORDINATE_TOLERANCE_DEG = 0.01


# ---------------------------------------------------------------------------
# Shared exception class
# ---------------------------------------------------------------------------


class CityManifestDriftError(RuntimeError):
    """Raised when Zeus cities.json and TIGGE manifest disagree on city names or coordinates.

    Prevents silent wrong-grid extraction when canonical city config diverges from
    the TIGGE manifest used to define bounding boxes.
    """


# ---------------------------------------------------------------------------
# Public compute_* surface (used by both extractors + test_phase4_5_extractor.py)
# ---------------------------------------------------------------------------


def compute_required_max_step(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> int:
    """Return minimum step hours to cover end of target_date in city's local day.

    Uses fixed offset (not ZoneInfo) because tests pass a numeric offset.
    Actual extraction uses ZoneInfo for precision. Result is 6h-aligned.

    # 2026-05-05 architect Fitz #1 K=1 structural cleanup target:
    # compute_required_max_step should be `max(required_period_end_steps(...))`.
    # Phase α adds the invariant test below; unification is a follow-up PR
    # touching extractor callsites + tz semantics audit.
    # NOTE: This function uses a fixed UTC offset (not ZoneInfo) which means it
    # will NOT produce the DST-aware result for cities with summer/winter offset
    # changes. The extractor snapshots the UTC offset at issue_utc before calling
    # here (extract_tigge_mx2t6_localday_max.py:241 pattern), which is correct but
    # differs from required_period_end_steps() which uses ZoneInfo throughout.
    # The invariant test in tests/test_extractor_period_step_contract.py
    # documents where the two implementations agree and where they diverge.
    """
    fixed_tz = timezone(timedelta(hours=city_utc_offset_hours))
    # local midnight at start of day after target_date = end of target_date local day
    next_day = target_date + timedelta(days=1)
    local_day_end_local = datetime.combine(next_day, dt_time.min, tzinfo=fixed_tz)
    local_day_end_utc = local_day_end_local.astimezone(timezone.utc)
    delta_hours = (local_day_end_utc - issue_utc).total_seconds() / 3600.0
    return _ceil_to_next_6h(delta_hours)


def predicted_step_set_for_target(
    issue_utc: datetime,
    target_date: date,
    city_tz: "ZoneInfo | str",
    period_hours: int = 6,
) -> set[int]:
    """Pure-Python prediction of which forecast steps the extractor would
    emit for a given (issue cycle, target local-day, city tz) tuple.

    Mirrors the extractor's overlap logic but takes no GRIB I/O. Used by
    the period-step contract test to verify subset relation against
    ``forecast_target_contract.required_period_end_steps``.

    The extractor selects a GRIB message (step range ``start_step-end_step``)
    when ``_overlap_seconds(window_start, window_end, day_start_utc, day_end_utc) > 0``.
    For a 6h aggregation window, each message covers ``[issue + start_step,
    issue + end_step]`` = ``[issue + (N-6)h, issue + Nh]`` for step N.
    This function emits step N for every 6h period whose UTC window overlaps
    the target local day.

    Scans far enough to include all steps that could overlap the target day
    (up to 360h = 15 days forward, well beyond any forecast horizon in use).
    """
    tz = ZoneInfo(str(city_tz)) if not isinstance(city_tz, ZoneInfo) else city_tz
    day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, str(tz))

    result: set[int] = set()
    # Scan 15 days = 360 6h steps; stops as soon as window_start >= day_end_utc
    for n in range(1, 361):
        step = n * period_hours
        window_end = issue_utc + timedelta(hours=step)
        window_start = window_end - timedelta(hours=period_hours)
        if window_start >= day_end_utc:
            break
        if _overlap_seconds(window_start, window_end, day_start_utc, day_end_utc) > 0:
            result.add(step)
    return result


def compute_manifest_hash(fields: dict) -> str:
    """SHA-256 of sorted-JSON of fields dict. Stable regardless of key order."""
    canon = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Private helpers (identical across both extractors)
# ---------------------------------------------------------------------------


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


def _iter_overlap_local_dates(
    window_start_utc: datetime,
    window_end_utc: datetime,
    timezone_name: str,
) -> list[date]:
    tz = ZoneInfo(str(timezone_name))
    local_start = window_start_utc.astimezone(tz)
    local_end_excl = (window_end_utc - timedelta(microseconds=1)).astimezone(tz)
    return sorted({local_start.date(), local_end_excl.date()})
