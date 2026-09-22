#!/usr/bin/env python3
# Created: 2026-09-22
# Last reused/audited: 2026-09-22
# Authority basis: current OpenData source contract; native 3h local-day extrema.
# Lifecycle: long_lived; raw GRIB -> decoded JSON only; no DB writes.
"""Decode native ENS windows at settlement coordinates.

HIGH retains every member's inner and overlapping boundary windows so the
canonical ingester can independently prove each local-day maximum. LOW keeps
its existing boundary policy. Native GRIB intervals, units and member identities
are preserved; crossing-midnight extrema are never reassigned to a local day.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from eccodes import (
    codes_get,
    codes_get_values,
    codes_grib_new_from_file,
    codes_is_defined,
    codes_release,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Dataset identity is owned by the in-repo provenance contract.  Importing the
# constants keeps this producer aligned with the active HIGH boundary-evidence
# revision when the daemon and ingester advance together.
from src.contracts.ensemble_snapshot_provenance import (  # noqa: E402
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)

# Keep the producer self-contained.  The historical copy imported these
# helpers from an unversioned external ``51 source data`` checkout, which made
# the runtime path depend on a second repository.  Runtime collection passes
# an explicit manifest and output root; these defaults are only CLI conveniences.
ROOT = Path(__file__).resolve().parents[1] / "51 source data"
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"


def city_slug(city_name: str) -> str:
    return str(city_name).strip().lower().replace(" ", "-")


def kelvin_to_native(value_k: float, unit: str) -> float:
    value_c = value_k - 273.15
    if str(unit).upper() == "F":
        return value_c * 9.0 / 5.0 + 32.0
    if str(unit).upper() != "C":
        raise ValueError(f"Unsupported native temperature unit: {unit!r}")
    return value_c


def local_day_bounds_utc(*, target_local_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(str(timezone_name))
    start_local = datetime.combine(target_local_date, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def manifest_sha256(manifest_path: Path) -> str:
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

logger = logging.getLogger(__name__)


def _aggregation_window_hours_for_param(open_data_param: str) -> int:
    """Derive the physical aggregation window (hours) from the ECMWF param token.

    Fail-closed: ``mx2t3``/``mn2t3`` -> 3h (Open Data, "in the last 3 hours");
    ``mx2t6``/``mn2t6`` -> 6h (TIGGE archive). Any other token raises so a new
    product cannot silently inherit a wrong window. This is the producer-side
    twin of forecast_target_contract.aggregation_window_hours_for_data_version.
    """
    tok = str(open_data_param)
    if tok in ("mx2t3", "mn2t3"):
        return 3
    if tok in ("mx2t6", "mn2t6"):
        return 6
    raise ValueError(
        f"_aggregation_window_hours_for_param: unknown param {open_data_param!r}; "
        f"cannot derive aggregation window (expected m[xn]2t3 -> 3h, m[xn]2t6 -> 6h)."
    )


@dataclass(frozen=True)
class TrackConfig:
    name: str
    mode: str  # high | low
    open_data_param: str  # 'mx2t3' or 'mn2t3' (Open Data 3h native)
    short_name: str
    paramId: int
    step_type: str
    data_version: str
    physical_quantity: str
    output_subdir: str

    @property
    def aggregation_window_hours(self) -> int:
        """Product-derived aggregation window (3h for mx2t3/mn2t3)."""
        return _aggregation_window_hours_for_param(self.open_data_param)


TRACKS: dict[str, TrackConfig] = {
    "mx2t6_high": TrackConfig(
        name="mx2t6_high",
        mode="high",
        open_data_param="mx2t3",
        short_name="mx2t3",
        paramId=228026,
        step_type="max",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        physical_quantity="mx2t3_local_calendar_day_max",
        output_subdir="open_ens_mx2t6_localday_max",
    ),
    "mn2t6_low": TrackConfig(
        name="mn2t6_low",
        mode="low",
        open_data_param="mn2t3",
        short_name="mn2t3",
        paramId=228027,
        step_type="min",
        data_version=ECMWF_OPENDATA_LOW_DATA_VERSION,
        physical_quantity="mn2t3_local_calendar_day_min",
        output_subdir="open_ens_mn2t6_localday_min",
    ),
}


def _load_cities(manifest_path: Path) -> list[dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return list(manifest["cities"])


def _record_path(*, output_root: Path, output_subdir: str, city_name: str,
                 issue_date_compact: str, target_local_date: str, lead_day: int,
                 cycle_hour: int = 0) -> Path:
    cycle_suffix = f"_cycle{cycle_hour:02d}z" if cycle_hour != 0 else ""
    return (
        output_root
        / output_subdir
        / city_slug(city_name)
        / f"{issue_date_compact}{cycle_suffix}"
        / f"{output_subdir}_target_{target_local_date}_lead_{lead_day}.json"
    )


def _scan_grib_for_track(
    grib_path: Path,
    track: TrackConfig,
) -> list[dict]:
    """Return one entry per GRIB message matching the track's short_name.

    Each entry: {member, step_hours, data_date, data_time, lat, lon, value_k}.
    Both control (cf, member=0) and perturbed (pf, member=1..50) members are
    captured. Messages whose short_name does not match the track are skipped.
    """
    out: list[dict] = []
    with grib_path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                short_name = codes_get(gid, "shortName")
                if str(short_name) != track.short_name:
                    continue
                # step (in hours) — Open Data ships hourly steps.
                step_hours = int(codes_get(gid, "endStep") if codes_is_defined(gid, "endStep")
                                 else codes_get(gid, "step"))
                # member identification: cf has typeOfProcessedData=cf and number=0;
                # pf has number 1..50.
                if codes_is_defined(gid, "number"):
                    member = int(codes_get(gid, "number"))
                elif codes_is_defined(gid, "perturbationNumber"):
                    member = int(codes_get(gid, "perturbationNumber"))
                else:
                    member = 0
                data_date = int(codes_get(gid, "dataDate"))
                data_time = int(codes_get(gid, "dataTime"))
                # Defer per-city nearest lookup to the caller — we just record
                # the GRIB message handle's grid identification once.
                out.append({
                    "gid_replay_marker": True,
                    "short_name": short_name,
                    "step_hours": step_hours,
                    "member": member,
                    "data_date": data_date,
                    "data_time": data_time,
                    "_grib_path": str(grib_path),
                    "_byte_offset": int(codes_get(gid, "offset")) if codes_is_defined(gid, "offset") else None,
                })
            finally:
                codes_release(gid)
    return out


def _compute_city_grid_indices(
    gid: int,
    cities: list[dict],
) -> tuple[list[int], list[tuple[float, float]]]:
    """Compute (flat_index, (grid_lat, grid_lon)) per city for a regular_ll
    GRIB message. Computed ONCE from message #1 — every message in an ECMWF
    Open Data ENS file shares the same grid (regular 0.25° lat-lon, 1440×721,
    lon_first=180, scanningMode=0).

    Antibody (2026-05-11): replaces per-message codes_grib_find_nearest which
    was the dominant cost (~5 ms × 52 cities × 2448 msgs = ~10 min). Verified
    bit-identical to codes_grib_find_nearest on 20260511 12z mx2t3 (52/52).
    """
    grid_type = str(codes_get(gid, "gridType"))
    if grid_type != "regular_ll":
        raise ValueError(
            f"Open Data ENS extract fast-path requires gridType=regular_ll; got {grid_type!r}"
        )
    scanning_mode = int(codes_get(gid, "scanningMode"))
    if scanning_mode != 0:
        # scanningMode=0 means: i increasing (lon W→E), j decreasing (lat N→S),
        # row-major. The index math below assumes this.
        raise ValueError(
            f"Open Data ENS extract fast-path requires scanningMode=0; got {scanning_mode}"
        )
    ni = int(codes_get(gid, "Ni"))
    nj = int(codes_get(gid, "Nj"))
    lat1 = float(codes_get(gid, "latitudeOfFirstGridPointInDegrees"))
    lon1 = float(codes_get(gid, "longitudeOfFirstGridPointInDegrees"))
    dx = float(codes_get(gid, "iDirectionIncrementInDegrees"))
    dy = float(codes_get(gid, "jDirectionIncrementInDegrees"))
    indices: list[int] = []
    grids: list[tuple[float, float]] = []
    for city in cities:
        lat = float(city["lat"])
        lon = float(city["lon"])
        # Normalize lon onto [lon1, lon1+360). Modulo handles both signed
        # manifest lons (-180..180) and 0..360 cases against any lon1.
        lon_rel = (lon - lon1) % 360.0
        i = int(round(lon_rel / dx)) % ni
        j = int(round((lat1 - lat) / dy))
        if j < 0 or j >= nj:
            j = max(0, min(nj - 1, j))
        indices.append(j * ni + i)
        grid_lon = (lon1 + i * dx) % 360.0
        if grid_lon > 180.0:
            grid_lon -= 360.0
        grids.append((lat1 - j * dy, grid_lon))
    return indices, grids


def _scan_grib_with_city_values(
    grib_path: Path,
    track: TrackConfig,
    cities: list[dict],
) -> dict[tuple[int, int], dict]:
    """Single pass over the GRIB extracting per-(member, step_hours) entries
    plus per-city values. Returns {(member, step): {meta+per_city_values}}.

    A single GRIB pass is far cheaper than re-opening per city.
    """
    bucket: dict[tuple[int, int], dict] = {}
    issue_dt: Optional[datetime] = None
    city_indices: Optional[list[int]] = None
    city_grids: Optional[list[tuple[float, float]]] = None
    issue_fields: tuple[int, int] | None = None
    grid_fields: dict[str, float | int | str] | None = None
    seen_member_steps: set[tuple[int, int]] = set()
    with grib_path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, "shortName"))
                if short_name != track.short_name:
                    continue
                required_metadata = (
                    "dataDate", "dataTime", "dataType", "units", "typeOfLevel",
                    "level", "stepUnits", "stepType", "startStep", "endStep",
                    "stepRange", "lengthOfTimeRange", "indicatorOfUnitForTimeRange",
                    "gridType", "Ni", "Nj",
                    "latitudeOfFirstGridPointInDegrees",
                    "longitudeOfFirstGridPointInDegrees",
                    "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
                    "scanningMode",
                )
                missing_metadata = [
                    field for field in required_metadata
                    if not codes_is_defined(gid, field)
                ]
                if missing_metadata:
                    raise ValueError(
                        f"{grib_path.name}: {short_name} message missing required "
                        f"metadata {missing_metadata}"
                    )
                data_date = int(codes_get(gid, "dataDate"))
                data_time = int(codes_get(gid, "dataTime"))
                current_issue = (data_date, data_time)
                if issue_fields is None:
                    issue_fields = current_issue
                elif current_issue != issue_fields:
                    raise ValueError(
                        f"{grib_path.name}: mixed issue fields; expected "
                        f"dataDate/dataTime={issue_fields[0]}/{issue_fields[1]}, got "
                        f"{data_date}/{data_time}"
                    )
                current_grid: dict[str, float | int | str] = {
                    "gridType": str(codes_get(gid, "gridType")),
                    "Ni": int(codes_get(gid, "Ni")),
                    "Nj": int(codes_get(gid, "Nj")),
                    "latitudeOfFirstGridPointInDegrees": float(
                        codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                    ),
                    "longitudeOfFirstGridPointInDegrees": float(
                        codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                    ),
                    "iDirectionIncrementInDegrees": float(
                        codes_get(gid, "iDirectionIncrementInDegrees")
                    ),
                    "jDirectionIncrementInDegrees": float(
                        codes_get(gid, "jDirectionIncrementInDegrees")
                    ),
                    "scanningMode": int(codes_get(gid, "scanningMode")),
                }
                if grid_fields is None:
                    grid_fields = current_grid
                elif current_grid != grid_fields:
                    raise ValueError(
                        f"{grib_path.name}: mixed grid metadata; expected "
                        f"{grid_fields}, got {current_grid}"
                    )
                if current_grid["gridType"] != "regular_ll":
                    raise ValueError(
                        f"{grib_path.name}: Open Data ENS requires gridType=regular_ll; "
                        f"got {current_grid['gridType']!r}"
                    )
                if str(codes_get(gid, "units")).upper() != "K":
                    raise ValueError(
                        f"{grib_path.name}: {short_name} units must be K"
                    )
                if str(codes_get(gid, "typeOfLevel")) != "heightAboveGround":
                    raise ValueError(
                        f"{grib_path.name}: {short_name} typeOfLevel must be "
                        "heightAboveGround"
                    )
                if int(codes_get(gid, "level")) != 2:
                    raise ValueError(
                        f"{grib_path.name}: {short_name} level must be 2m "
                        f"(got {codes_get(gid, 'level')!r})"
                    )
                expected_step_type = "max" if track.mode == "high" else "min"
                if str(codes_get(gid, "stepType")) != expected_step_type:
                    raise ValueError(
                        f"{grib_path.name}: {short_name} stepType must be "
                        f"{expected_step_type!r}"
                    )
                if int(codes_get(gid, "stepUnits")) != 1:
                    raise ValueError(
                        f"{grib_path.name}: {short_name} stepUnits must be hours (1)"
                    )
                # Every accepted message must carry a real native window.  In
                # particular, do not synthesize ``endStep - 3``: the GRIB's
                # startStep/endStep/stepRange/lengthOfTimeRange are the
                # provenance that says which physical 3-hour interval the
                # field represents.
                required_step_fields = (
                    "startStep", "endStep", "stepRange", "lengthOfTimeRange",
                    "indicatorOfUnitForTimeRange",
                )
                missing_step_fields = [
                    field for field in required_step_fields
                    if not codes_is_defined(gid, field)
                ]
                if missing_step_fields:
                    raise ValueError(
                        f"{grib_path.name}: {short_name} message missing native "
                        f"step fields {missing_step_fields}; refusing unverifiable window"
                    )
                start_step = int(codes_get(gid, "startStep"))
                end_step = int(codes_get(gid, "endStep"))
                raw_step_range = str(codes_get(gid, "stepRange"))
                match = re.fullmatch(r"(\d+)-(\d+)", raw_step_range)
                if match is None:
                    raise ValueError(
                        f"{grib_path.name}: invalid native stepRange={raw_step_range!r}"
                    )
                range_start, range_end = (int(match.group(1)), int(match.group(2)))
                if (range_start, range_end) != (start_step, end_step):
                    raise ValueError(
                        f"{grib_path.name}: stepRange={raw_step_range!r} disagrees with "
                        f"startStep/endStep={start_step}-{end_step}"
                    )
                native_window_hours = end_step - start_step
                expected_window_hours = track.aggregation_window_hours
                if native_window_hours != expected_window_hours:
                    raise ValueError(
                        f"{grib_path.name}: stepRange={raw_step_range!r} is "
                        f"{native_window_hours}h, expected real {expected_window_hours}h "
                        f"for param {track.open_data_param!r}"
                    )
                grib_window_hours = int(codes_get(gid, "lengthOfTimeRange"))
                unit_indicator = int(codes_get(gid, "indicatorOfUnitForTimeRange"))
                if unit_indicator != 1 or grib_window_hours != native_window_hours:
                    raise ValueError(
                        f"{grib_path.name}: lengthOfTimeRange={grib_window_hours} "
                        f"indicatorOfUnitForTimeRange={unit_indicator} disagrees with "
                        f"native stepRange={raw_step_range!r}"
                    )
                step_hours = end_step
                data_type = str(codes_get(gid, "dataType")).lower()
                has_number = codes_is_defined(gid, "number")
                has_perturbation_number = codes_is_defined(gid, "perturbationNumber")
                if data_type in {"cf", "fc"}:
                    number = int(codes_get(gid, "number")) if has_number else 0
                    perturbation_number = (
                        int(codes_get(gid, "perturbationNumber"))
                        if has_perturbation_number else 0
                    )
                    if number != 0 or perturbation_number != 0:
                        raise ValueError(
                            f"{grib_path.name}: control dataType={data_type!r} "
                            "must have no/zero member number"
                        )
                    member = 0
                elif data_type == "pf":
                    if not (has_number or has_perturbation_number):
                        raise ValueError(
                            f"{grib_path.name}: perturbed pf message must carry "
                            "an explicit member number"
                        )
                    number = int(codes_get(gid, "number")) if has_number else None
                    perturbation_number = (
                        int(codes_get(gid, "perturbationNumber"))
                        if has_perturbation_number else None
                    )
                    if number is not None and perturbation_number is not None and number != perturbation_number:
                        raise ValueError(
                            f"{grib_path.name}: number and perturbationNumber disagree"
                        )
                    member = number if number is not None else perturbation_number
                    if member is None or not 1 <= int(member) <= 50:
                        raise ValueError(
                            f"{grib_path.name}: pf member must be explicitly in 1..50, "
                            f"got {member!r}"
                        )
                    member = int(member)
                else:
                    raise ValueError(
                        f"{grib_path.name}: unsupported dataType={data_type!r}; "
                        "expected cf/fc control or pf perturbed"
                    )
                if issue_dt is None:
                    issue_dt = datetime(
                        year=data_date // 10000,
                        month=(data_date // 100) % 100,
                        day=data_date % 100,
                        hour=data_time // 100,
                        minute=data_time % 100,
                        tzinfo=timezone.utc,
                    )
                if city_indices is None:
                    city_indices, city_grids = _compute_city_grid_indices(gid, cities)
                key = (member, step_hours)
                if key in seen_member_steps:
                    raise ValueError(
                        f"{grib_path.name}: duplicate member/step tuple "
                        f"member={member}, step={step_hours}; refusing overwrite"
                    )
                seen_member_steps.add(key)
                if key not in bucket:
                    bucket[key] = {
                        "member": member,
                        "step_hours": step_hours,
                        "start_step": start_step,
                        "end_step": end_step,
                        "step_range": raw_step_range,
                        "city_values_k": {},
                    }
                values = codes_get_values(gid)
                for city, idx, (g_lat, g_lon) in zip(cities, city_indices, city_grids):
                    bucket[key]["city_values_k"][city["city"]] = {
                        "value_k": float(values[idx]),
                        "nearest_grid_lat": g_lat,
                        "nearest_grid_lon": g_lon,
                        # nearest_grid_distance_km no longer computed
                        # (downstream payloads hardcode None anyway, lines 385-387/457-459).
                        "nearest_grid_distance_km": None,
                    }
            finally:
                codes_release(gid)
    return {"issue_dt": issue_dt, "entries": bucket}


def _windows_overlap(
    *,
    window_start: datetime,
    window_end: datetime,
    target_start: datetime,
    target_end: datetime,
) -> tuple[bool, bool]:
    """Return (fully_inside, has_overlap)."""
    if window_end <= target_start or window_start >= target_end:
        return (False, False)
    fully_inside = window_start >= target_start and window_end <= target_end
    return (fully_inside, True)


def extract_open_ens_localday(
    *,
    grib_path: Path,
    track_name: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_root: Path = ROOT / "raw",
    cities_filter: Optional[set[str]] = None,
) -> dict:
    """Single GRIB → per-city local-calendar-day JSONs (one per lead_day).

    Returns summary dict.
    """
    if track_name not in TRACKS:
        raise ValueError(f"Unknown track {track_name!r}; expected one of {sorted(TRACKS)}")
    track = TRACKS[track_name]
    # Product-derived aggregation window (3h for mx2t3/mn2t3) — replaces the
    # imported TIGGE-era scalar STEP_HOURS=6 at every window-math + payload site.
    agg_window_hours = track.aggregation_window_hours
    if not grib_path.exists():
        raise FileNotFoundError(f"GRIB not found: {grib_path}")

    cities = _load_cities(manifest_path)
    if cities_filter is not None:
        cities = [c for c in cities if c["city"] in cities_filter]
    if not cities:
        return {"status": "no_cities", "written": 0}

    scan = _scan_grib_with_city_values(grib_path, track, cities)
    entries: dict[tuple[int, int], dict] = scan["entries"]
    issue_dt: datetime = scan["issue_dt"]
    if issue_dt is None:
        return {"status": "no_matching_messages", "track": track_name, "written": 0}

    issue_date_compact = issue_dt.strftime("%Y%m%d")
    cycle_hour = issue_dt.hour
    manifest_hash = manifest_sha256(manifest_path)

    # Group by (city, target_local_date, lead_day): for each member, find
    # which (member, step_hours) windows overlap the local day fully and
    # take max (HIGH) or min (LOW).
    summary_written = 0
    summary_skipped = 0
    output_paths: list[str] = []

    for city in cities:
        city_name = city["city"]
        timezone_name = str(city["timezone"])
        unit = str(city["unit"])
        # Determine the lead-day range covered by the GRIB step set.
        step_set = sorted({step for (_m, step) in entries.keys()})
        if not step_set:
            continue
        max_step = max(step_set)
        # local-day-end at lead 0 is issue_dt.date() local-day end → we walk
        # lead_day = 0 .. ceil(max_step / 24) and emit a record per day where
        # at least one inner window fully fits.
        max_lead = max_step // 24 + 1
        for lead_day in range(0, max_lead + 1):
            target_local_date = (issue_dt + timedelta(days=lead_day)).date()
            local_start, local_end = local_day_bounds_utc(
                target_local_date=target_local_date, timezone_name=timezone_name,
            )
            # Aggregate per member across overlapping windows.
            members_inner: dict[int, list[float]] = {}
            members_boundary: dict[int, list[float]] = {}
            members_inner_ranges: dict[int, list[str]] = {}
            members_boundary_ranges: dict[int, list[str]] = {}
            members_native_windows: dict[int, list[dict[str, Any]]] = {}
            selected_step_ranges_inner: set[str] = set()
            selected_step_ranges_boundary: set[str] = set()
            for (member, step_hours), bucket in entries.items():
                window_end = issue_dt + timedelta(hours=step_hours)
                window_start = window_end - timedelta(hours=agg_window_hours)
                fully, any_overlap = _windows_overlap(
                    window_start=window_start, window_end=window_end,
                    target_start=local_start, target_end=local_end,
                )
                if not any_overlap:
                    continue
                value_k = bucket["city_values_k"][city_name]["value_k"]
                value_native = kelvin_to_native(value_k, unit)
                # Preserve the producer-declared native range verbatim.  This
                # is intentionally not reconstructed from endStep so the
                # ingester can prove boundary clipping per member.
                step_label = str(bucket["step_range"])
                if track.mode == "high":
                    members_native_windows.setdefault(member, []).append({
                        "start_step_hours": int(bucket["start_step"]),
                        "end_step_hours": int(bucket["end_step"]),
                        "value_native_unit": value_native,
                    })
                if fully:
                    members_inner.setdefault(member, []).append(value_native)
                    members_inner_ranges.setdefault(member, []).append(step_label)
                    selected_step_ranges_inner.add(step_label)
                else:
                    members_boundary.setdefault(member, []).append(value_native)
                    members_boundary_ranges.setdefault(member, []).append(step_label)
                    selected_step_ranges_boundary.add(step_label)

            if not members_inner and not members_boundary:
                continue

            # Build payload — distinct shapes per track mode.
            if track.mode == "high":
                members_out = []
                missing_members: list[int] = []
                for m in range(51):
                    inner = members_inner.get(m, [])
                    boundary = members_boundary.get(m, [])
                    inner_max = max(inner) if inner else None
                    boundary_max = max(boundary) if boundary else None
                    inner_ranges = sorted(set(members_inner_ranges.get(m, [])))
                    boundary_ranges = sorted(set(members_boundary_ranges.get(m, [])))
                    native_windows = sorted(
                        members_native_windows.get(m, []),
                        key=lambda row: (
                            int(row["start_step_hours"]),
                            int(row["end_step_hours"]),
                        ),
                    )
                    if inner_max is None:
                        members_out.append({
                            "member": m,
                            "value_native_unit": None,
                            "inner_max_native_unit": None,
                            "boundary_max_native_unit": boundary_max,
                            "inner_step_ranges": inner_ranges,
                            "boundary_step_ranges": boundary_ranges,
                            "native_windows": native_windows,
                        })
                        missing_members.append(m)
                    else:
                        members_out.append({
                            "member": m,
                            # HIGH qualification remains an ingester concern;
                            # the producer's candidate is the inner maximum.
                            "value_native_unit": inner_max,
                            "inner_max_native_unit": inner_max,
                            "boundary_max_native_unit": boundary_max,
                            "inner_step_ranges": inner_ranges,
                            "boundary_step_ranges": boundary_ranges,
                            "native_windows": native_windows,
                        })
                payload = {
                    "generated_at": now_utc_iso(),
                    "data_version": track.data_version,
                    "physical_quantity": track.physical_quantity,
                    "param": track.open_data_param,
                    "paramId": track.paramId,
                    "short_name": track.short_name,
                    "step_type": track.step_type,
                    "aggregation_window_hours": agg_window_hours,
                    "city": city_name,
                    "lat": float(city["lat"]),
                    "lon": float(city["lon"]),
                    "unit": unit,
                    "manifest_sha256": manifest_hash,
                    "manifest_hash": manifest_hash,
                    "issue_time_utc": issue_dt.isoformat(),
                    "target_date_local": target_local_date.isoformat(),
                    "lead_day": lead_day,
                    "lead_day_anchor": "issue_utc.date()",
                    "timezone": timezone_name,
                    "local_day_window": {
                        "start": local_start.isoformat(),
                        "end": local_end.isoformat(),
                    },
                    "local_day_start_utc": local_start.isoformat(),
                    "local_day_end_utc": local_end.isoformat(),
                    "step_horizon_hours": float(max_step),
                    "step_horizon_deficit_hours": 0.0,
                    "causality": {"status": "OK"},
                    "boundary_ambiguous": False,
                    "nearest_grid_lat": None,
                    "nearest_grid_lon": None,
                    "nearest_grid_distance_km": None,
                    # Keep the legacy alias while exposing both native planes.
                    "selected_step_ranges": sorted(selected_step_ranges_inner),
                    "selected_step_ranges_inner": sorted(selected_step_ranges_inner),
                    "selected_step_ranges_boundary": sorted(selected_step_ranges_boundary),
                    "member_count": len(members_out),
                    "missing_members": missing_members,
                    "training_allowed": len(missing_members) == 0,
                    "members": members_out,
                }
            else:
                members_out = []
                missing_members = []
                boundary_ambiguous_members: list[int] = []
                for m in range(51):
                    inner = members_inner.get(m, [])
                    boundary = members_boundary.get(m, [])
                    inner_min = min(inner) if inner else None
                    boundary_min = min(boundary) if boundary else None
                    boundary_ambiguous = (
                        boundary_min is not None
                        and (inner_min is None or boundary_min <= inner_min)
                    )
                    if boundary_ambiguous:
                        boundary_ambiguous_members.append(m)
                    value = inner_min if (inner_min is not None and not boundary_ambiguous) else None
                    if inner_min is None and boundary_min is None:
                        missing_members.append(m)
                    members_out.append({
                        "member": m,
                        "value_native_unit": value,
                        "inner_min_native_unit": inner_min,
                        "boundary_min_native_unit": boundary_min,
                        "boundary_ambiguous": boundary_ambiguous,
                        "inner_step_ranges": sorted(set(members_inner_ranges.get(m, []))),
                        "boundary_step_ranges": sorted(set(members_boundary_ranges.get(m, []))),
                    })
                training_allowed = len(missing_members) == 0 and len(boundary_ambiguous_members) == 0
                payload = {
                    "generated_at": now_utc_iso(),
                    "data_version": track.data_version,
                    "physical_quantity": track.physical_quantity,
                    "param": track.open_data_param,
                    "paramId": track.paramId,
                    "short_name": track.short_name,
                    "step_type": track.step_type,
                    "aggregation_window_hours": agg_window_hours,
                    "temperature_metric": "low",
                    "members_unit": "K",
                    "city": city_name,
                    "lat": float(city["lat"]),
                    "lon": float(city["lon"]),
                    "unit": unit,
                    "manifest_sha256": manifest_hash,
                    "manifest_hash": manifest_hash,
                    "issue_time_utc": issue_dt.isoformat(),
                    "target_date_local": target_local_date.isoformat(),
                    "lead_day": lead_day,
                    "lead_day_anchor": "issue_utc.date()",
                    "timezone": timezone_name,
                    "local_day_window": {
                        "start": local_start.isoformat(),
                        "end": local_end.isoformat(),
                    },
                    "local_day_start_utc": local_start.isoformat(),
                    "local_day_end_utc": local_end.isoformat(),
                    "step_horizon_hours": float(max_step),
                    "step_horizon_deficit_hours": 0.0,
                    "causality": {"status": "OK"},
                    "boundary_ambiguous": len(boundary_ambiguous_members) > 0,
                    "boundary_policy": {
                        "training_rule": "drop_ambiguous_members",
                        "boundary_ambiguous": len(boundary_ambiguous_members) > 0,
                        "ambiguous_member_count": len(boundary_ambiguous_members),
                    },
                    "nearest_grid_lat": None,
                    "nearest_grid_lon": None,
                    "nearest_grid_distance_km": None,
                    "selected_step_ranges_inner": sorted(selected_step_ranges_inner),
                    "selected_step_ranges_boundary": sorted(selected_step_ranges_boundary),
                    "member_count": len(members_out),
                    "missing_members": missing_members,
                    "training_allowed": training_allowed,
                    "members": members_out,
                }

            out_path = _record_path(
                output_root=output_root,
                output_subdir=track.output_subdir,
                city_name=city_name,
                issue_date_compact=issue_date_compact,
                target_local_date=target_local_date.isoformat(),
                lead_day=lead_day,
                cycle_hour=cycle_hour,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            output_paths.append(str(out_path))
            summary_written += 1

    return {
        "status": "ok",
        "track": track_name,
        "data_version": track.data_version,
        "issue_time_utc": issue_dt.isoformat(),
        "cycle_hour_utc": cycle_hour,
        "written": summary_written,
        "skipped": summary_skipped,
        "output_root": str(output_root / track.output_subdir),
        "sample_outputs": output_paths[:3],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grib-path", type=Path, required=True)
    parser.add_argument("--track", choices=sorted(TRACKS), required=True)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--cities", nargs="*", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cities_filter = set(args.cities) if args.cities else None
    summary = extract_open_ens_localday(
        grib_path=args.grib_path,
        track_name=args.track,
        manifest_path=args.manifest_path,
        output_root=args.output_root,
        cities_filter=cities_filter,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
