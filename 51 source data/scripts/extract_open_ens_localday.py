#!/usr/bin/env python3
# Created: 2026-05-01
# Last reused/audited: 2026-05-29
# Authority basis (2026-05-29 D1-LOW patch — PROPOSED COPY, not yet live):
#   The aggregation window was a TIGGE-era scalar STEP_HOURS=6 imported from
#   tigge_local_calendar_day_common.py and applied to the 3h Open Data product
#   (mx2t3/mn2t3). ECMWF defines mn2t3/mx2t3 as "min/max 2m temp in the LAST 3
#   HOURS" (architecture/data_sources_registry_2026_05_08.yaml:86,91); verified
#   empirically on the 20260528 00z step144 mn2t3 field: lengthOfTimeRange=3,
#   indicatorOfUnitForTimeRange=1 (hours), stepRange=141-144. The 6h window
#   over-stated each field's coverage by 3h and mis-classified near-day-start
#   fields inner<->boundary. FIX: the aggregation window is now PRODUCT-DERIVED
#   from the track's open_data_param token (m[xn]2t3 -> 3h, m[xn]2t6 -> 6h) AND
#   cross-checked against each GRIB message's own lengthOfTimeRange (fail-closed)
#   so the wrong window is unconstructable. Mirrors the in-repo contract fix in
#   src/data/forecast_target_contract.aggregation_window_hours_for_data_version.
# Authority basis: Operator directive 2026-05-01 — same-day forecasts must
#   land in ensemble_snapshots_v2 via the same local-calendar-day algorithm
#   as TIGGE so calibration / day0 / opening_hunt readers can consume both
#   sources interchangeably with a data_version preference. This module is
#   the Open-Data sibling of tigge_local_calendar_day_extract.py.
#
# 2026-05-11 audit/fix (fix/harvester-paginator-bound-2026-05-11):
#   _scan_grib_with_city_values previously called codes_grib_find_nearest
#   per (message × city) — 2448 messages × 52 cities × ~5 ms = ~25 min on
#   the 1.5 GB partial mx2t3 file, exceeding the 900s extract timeout.
#   eccodes' nearest-grid lookup is implemented as a per-call k-d/tree
#   build against the GRIB message's grid, which on a 1440×721 (0.25°)
#   grid is the dominant cost; the values array decode is ~10 ms.
#   Antibody: compute city → flat-grid-index ONCE from the first message's
#   regular-lat-lon metadata (lat1/lon1/dx/dy/Ni/Nj), then
#   codes_get_values()[idx] per (message, city). Verified bit-identical
#   to codes_grib_find_nearest for all 52 cities on the 20260511 12z
#   mx2t3 file (52/52 match, float64 equality). New wall-clock: 19s for
#   the full 48-step × 51-member partial; projects to ~28s for a full
#   71-step happy-path cycle. ECMWF Open Data ships regular_ll only
#   (lon_first=180, dx=dy=0.25, scanningMode=0), so the regular-grid
#   index formula is safe; we assert grid kind and scanning mode below.
"""Local-calendar-day max/min extractor for ECMWF Open Data ENS GRIB files.

Open Data delivers ``mx2t6``/``mn2t6`` (6-hour aggregations) at steps
3, 6, 9, ..., 360 hours from the run hour. For each city we:

  1. Resolve city local-day bounds (UTC) for each target_local_date covered
     by the issue + step set.
  2. For every (member, step) tuple, compute the 6-hour aggregation window
     ``[issue + step - 6h, issue + step]``.
  3. Select windows that *fully* fall within the target local day (inner)
     versus those that straddle the day boundary (boundary). For HIGH this
     mirrors TIGGE — we just take the max over inner windows. For LOW we
     also surface boundary windows so the boundary-leakage law (R-AH/R-AJ)
     can quarantine ambiguous members downstream.
  4. Emit a JSON record per (city, target_local_date, lead_day) following
     the TiggeSnapshotPayload schema so the existing ingest_grib_to_snapshots
     pipeline accepts the rows verbatim.

Differences from the TIGGE version
----------------------------------
- Single GRIB file per (run_date, run_hour, param-set) — no region splits.
  Open Data ships globally on a 0.25 deg grid so every city resolves from
  the same file via ``codes_grib_find_nearest``.
- ``data_version`` is ``ecmwf_opendata_mx2t6_local_calendar_day_max_v1``
  (and ``_min_v1``) — distinct from the TIGGE archive's
  ``tigge_*_v1`` so calibration/day0 readers can prefer the fresher
  source and fall back to TIGGE for older issue dates.
- ``causality.status = "OK"`` is emitted unconditionally for HIGH (pure
  forecast, same as TIGGE high). For LOW we emit the same boundary-policy
  block the TIGGE low extractor produces so the contract validator accepts
  both interchangeably.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from eccodes import (
    codes_get,
    codes_get_values,
    codes_grib_find_nearest,
    codes_grib_new_from_file,
    codes_is_defined,
    codes_release,
)

from tigge_local_calendar_day_common import (
    DEFAULT_MANIFEST,
    ROOT,
    # STEP_HOURS (TIGGE-era 6h scalar) intentionally NOT imported — the
    # aggregation window is now product-derived per track (see
    # _aggregation_window_hours_for_param). D1-LOW fix 2026-05-29.
    city_slug,
    kelvin_to_native,
    local_day_bounds_utc,
    manifest_sha256,
    now_utc_iso,
    overlap_seconds,
)

logger = logging.getLogger(__name__)


def _haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    radius_km = 6371.0088
    phi_a = math.radians(float(lat_a))
    phi_b = math.radians(float(lat_b))
    d_phi = math.radians(float(lat_b) - float(lat_a))
    d_lambda = math.radians(float(lon_b) - float(lon_a))
    h = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2.0) ** 2
    )
    return radius_km * 2.0 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1.0 - h)))


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
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
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
        data_version="ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
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
) -> tuple[list[int], list[tuple[float, float, float]]]:
    """Compute (flat_index, (grid_lat, grid_lon, distance_km)) per city for a regular_ll
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
    grids: list[tuple[float, float, float]] = []
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
        grid_lat = lat1 - j * dy
        grids.append((grid_lat, grid_lon, _haversine_km(lat, lon, grid_lat, grid_lon)))
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
    with grib_path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, "shortName"))
                if short_name != track.short_name:
                    continue
                if codes_is_defined(gid, "endStep"):
                    step_hours = int(codes_get(gid, "endStep"))
                else:
                    step_hours = int(codes_get(gid, "step"))
                # Fail-closed provenance guard (D1-LOW): the per-message aggregation
                # window declared by the GRIB itself MUST equal the product-derived
                # window. Makes a wrong (e.g. 6h-on-3h) window unconstructable even
                # if the param token were ever mislabeled. lengthOfTimeRange is in
                # the unit given by indicatorOfUnitForTimeRange (1 = hours).
                if codes_is_defined(gid, "lengthOfTimeRange"):
                    grib_window_hours = int(codes_get(gid, "lengthOfTimeRange"))
                    if codes_is_defined(gid, "indicatorOfUnitForTimeRange"):
                        unit_indicator = int(codes_get(gid, "indicatorOfUnitForTimeRange"))
                        if unit_indicator != 1:
                            raise ValueError(
                                f"{grib_path.name}: lengthOfTimeRange unit "
                                f"indicatorOfUnitForTimeRange={unit_indicator} != 1 (hours); "
                                f"cannot trust aggregation window."
                            )
                    if grib_window_hours != track.aggregation_window_hours:
                        raise ValueError(
                            f"{grib_path.name} step {step_hours}: GRIB lengthOfTimeRange="
                            f"{grib_window_hours}h disagrees with product-derived window "
                            f"{track.aggregation_window_hours}h for param {track.open_data_param!r}. "
                            f"Refusing to extract with an ambiguous aggregation window."
                        )
                if codes_is_defined(gid, "number"):
                    member = int(codes_get(gid, "number"))
                elif codes_is_defined(gid, "perturbationNumber"):
                    member = int(codes_get(gid, "perturbationNumber"))
                else:
                    member = 0
                if issue_dt is None:
                    data_date = int(codes_get(gid, "dataDate"))
                    data_time = int(codes_get(gid, "dataTime"))
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
                if key not in bucket:
                    bucket[key] = {
                        "member": member,
                        "step_hours": step_hours,
                        "city_values_k": {},
                    }
                values = codes_get_values(gid)
                for city, idx, (g_lat, g_lon, g_distance_km) in zip(cities, city_indices, city_grids):
                    bucket[key]["city_values_k"][city["city"]] = {
                        "value_k": float(values[idx]),
                        "nearest_grid_lat": g_lat,
                        "nearest_grid_lon": g_lon,
                        "nearest_grid_distance_km": g_distance_km,
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
        grid_provenance: dict[str, float | None] = {
            "nearest_grid_lat": None,
            "nearest_grid_lon": None,
            "nearest_grid_distance_km": None,
        }
        for entry in entries.values():
            city_value = entry.get("city_values_k", {}).get(city_name)
            if isinstance(city_value, dict):
                grid_provenance = {
                    "nearest_grid_lat": city_value.get("nearest_grid_lat"),
                    "nearest_grid_lon": city_value.get("nearest_grid_lon"),
                    "nearest_grid_distance_km": city_value.get("nearest_grid_distance_km"),
                }
                break
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
                step_label = f"{step_hours - agg_window_hours}-{step_hours}"
                if fully:
                    members_inner.setdefault(member, []).append(value_native)
                    selected_step_ranges_inner.add(step_label)
                else:
                    members_boundary.setdefault(member, []).append(value_native)
                    selected_step_ranges_boundary.add(step_label)

            if not members_inner and not members_boundary:
                continue

            # Build payload — distinct shapes per track mode.
            if track.mode == "high":
                members_out = []
                missing_members: list[int] = []
                for m in range(51):
                    vals = members_inner.get(m)
                    if not vals:
                        members_out.append({"member": m, "value_native_unit": None})
                        missing_members.append(m)
                    else:
                        members_out.append({"member": m, "value_native_unit": max(vals)})
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
                    "nearest_grid_lat": grid_provenance["nearest_grid_lat"],
                    "nearest_grid_lon": grid_provenance["nearest_grid_lon"],
                    "nearest_grid_distance_km": grid_provenance["nearest_grid_distance_km"],
                    "selected_step_ranges": sorted(selected_step_ranges_inner),
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
                    "nearest_grid_lat": grid_provenance["nearest_grid_lat"],
                    "nearest_grid_lon": grid_provenance["nearest_grid_lon"],
                    "nearest_grid_distance_km": grid_provenance["nearest_grid_distance_km"],
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
