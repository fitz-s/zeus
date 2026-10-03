#!/usr/bin/env python3
# Created: 2026-09-22
# Last reused/audited: 2026-10-02
# Authority basis: current OpenData source contract; native 3h local-day extrema.
# Lifecycle: created=2026-09-22; last_reviewed=2026-10-02; last_reused=2026-10-02
# Purpose: Native ENS GRIB -> local-day JSON with optional byte/point capture; no DB writes.
# Reuse: Use the collector's explicit coordinate manifest and same-cycle land-mask proof.
"""Decode native ENS windows at settlement coordinates.

HIGH retains every member's inner and overlapping boundary windows so the
canonical ingester can independently prove each local-day maximum. LOW keeps
its existing boundary policy. Native GRIB intervals, units and member identities
are preserved; crossing-midnight extrema are never reassigned to a local day.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from eccodes import (
    codes_get,
    codes_get_message,
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
    GRID_SURFACE_EVIDENCE_REVISION,
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


def _select_land_grid_points(
    grid: dict[str, float | int | str],
    cities: list[dict],
    land_fraction_at,
) -> dict[str, dict[str, object]]:
    """ECMWF meteogram rule: nearest land cell among four surrounding a land site.

    The caller supplies values from an authenticated *same-grid* IFS LSM.
    Unknown fractions and no land cell are source gaps, never permission to
    silently reuse the geometrically nearest water-class temperature cell.
    """
    if grid.get("gridType") != "regular_ll" or grid.get("scanningMode") != 0:
        raise ValueError("ENS_LAND_GRID_LAYOUT_INVALID")
    ni, nj = int(grid["Ni"]), int(grid["Nj"])
    lat_first = float(grid["latitudeOfFirstGridPointInDegrees"])
    lon_first = float(grid["longitudeOfFirstGridPointInDegrees"])
    dx = float(grid["iDirectionIncrementInDegrees"])
    dy = float(grid["jDirectionIncrementInDegrees"])
    if ni < 2 or nj < 2 or dx <= 0 or dy <= 0:
        raise ValueError("ENS_LAND_GRID_LAYOUT_INVALID")

    def distance(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
        p1, p2 = math.radians(lat_a), math.radians(lat_b)
        delta_p = p2 - p1
        delta_l = math.radians((lon_b - lon_a + 180.0) % 360.0 - 180.0)
        h = math.sin(delta_p / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_l / 2) ** 2
        return 2.0 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))

    result: dict[str, dict[str, object]] = {}
    for city in cities:
        lat, lon = float(city["lat"]), float(city["lon"])
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("ENS_LAND_STATION_COORDINATES_INVALID")
        relative_i = ((lon - lon_first) % 360.0) / dx
        relative_j = (lat_first - lat) / dy
        i0, j0 = math.floor(relative_i), math.floor(relative_j)
        # Half-open surrounding cell: an exact gridline takes the east/south
        # adjacent cell, including the 180-degree longitude wrap. The point
        # on a polar endpoint uses the last valid adjacent latitude pair.
        i1 = i0 + 1
        if j0 == nj - 1:
            j0 = nj - 2
        j1 = j0 + 1
        if not 0 <= j0 < nj or not 0 <= j1 < nj:
            raise ValueError("ENS_LAND_STATION_OUTSIDE_GRID")
        neighbors = []
        for j in (j0, j1):
            for i in (i0 % ni, i1 % ni):
                index = j * ni + i
                fraction = float(land_fraction_at(index))
                if not math.isfinite(fraction) or not 0 <= fraction <= 1:
                    raise ValueError("ENS_LAND_MASK_FRACTION_INVALID")
                grid_lon = (lon_first + i * dx + 180.0) % 360.0 - 180.0
                grid_lat = lat_first - j * dy
                neighbors.append({
                    "flat_index": index,
                    "lat": round(grid_lat, 6),
                    "lon": round(grid_lon, 6),
                    "land_fraction": fraction,
                    "distance_km": distance(lat, lon, grid_lat, grid_lon),
                })
        if len(neighbors) != 4:
            raise ValueError("ENS_LAND_FOUR_NEIGHBORS_UNAVAILABLE")
        land = [cell for cell in neighbors if cell["land_fraction"] > 0.5]
        if not land:
            raise ValueError("ENS_LAND_NEIGHBOR_UNAVAILABLE")
        selected = min(land, key=lambda cell: (cell["distance_km"], cell["flat_index"]))
        result[str(city["city"])] = {
            "selected_flat_index": selected["flat_index"],
            "selected_lat": selected["lat"],
            "selected_lon": selected["lon"],
            "selected_land_fraction": selected["land_fraction"],
            "nearest_grid_distance_km": selected["distance_km"],
            "four_neighbors": neighbors,
        }
    return result


_GRID_KEYS = (
    "gridType", "Ni", "Nj", "latitudeOfFirstGridPointInDegrees",
    "longitudeOfFirstGridPointInDegrees", "iDirectionIncrementInDegrees",
    "jDirectionIncrementInDegrees", "scanningMode",
)


def _grid_identity(fields: dict[str, float | int | str]) -> str:
    return hashlib.sha256(json.dumps(
        {key: fields[key] for key in _GRID_KEYS},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _read_land_mask(mask_path: Path, proof_path: Path) -> dict[str, object]:
    """Bind the exact bounded Range message to its retrieved source envelope."""
    raw = mask_path.read_bytes()
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if not isinstance(proof, dict) or proof.get("mask_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("ENS_LAND_MASK_SOURCE_HASH_MISMATCH")
    if proof.get("source") != "ecmwf_open_data_ifs_oper_fc_step0_lsm":
        raise ValueError("ENS_LAND_MASK_SOURCE_INVALID")
    with mask_path.open("rb") as fh:
        gid = codes_grib_new_from_file(fh)
        if gid is None:
            raise ValueError("ENS_LAND_MASK_MESSAGE_MISSING")
        try:
            if (
                str(codes_get(gid, "shortName")) != "lsm"
                or int(codes_get(gid, "paramId")) != 172
                or str(codes_get(gid, "typeOfLevel")) != "surface"
                or str(codes_get(gid, "centre")) != "ecmf"
                or int(codes_get(gid, "step")) != 0
            ):
                raise ValueError("ENS_LAND_MASK_PARAMETER_INVALID")
            fields: dict[str, float | int | str] = {
                "gridType": str(codes_get(gid, "gridType")),
                "Ni": int(codes_get(gid, "Ni")),
                "Nj": int(codes_get(gid, "Nj")),
                "latitudeOfFirstGridPointInDegrees": float(codes_get(gid, "latitudeOfFirstGridPointInDegrees")),
                "longitudeOfFirstGridPointInDegrees": float(codes_get(gid, "longitudeOfFirstGridPointInDegrees")),
                "iDirectionIncrementInDegrees": float(codes_get(gid, "iDirectionIncrementInDegrees")),
                "jDirectionIncrementInDegrees": float(codes_get(gid, "jDirectionIncrementInDegrees")),
                "scanningMode": int(codes_get(gid, "scanningMode")),
            }
            cycle = datetime.strptime(
                f'{int(codes_get(gid, "dataDate")):08d}{int(codes_get(gid, "dataTime")):04d}',
                "%Y%m%d%H%M",
            ).replace(tzinfo=timezone.utc).isoformat()
            if cycle != proof.get("source_cycle_time"):
                raise ValueError("ENS_LAND_MASK_CYCLE_MISMATCH")
            values = codes_get_values(gid)
            if len(values) != fields["Ni"] * fields["Nj"]:
                raise ValueError("ENS_LAND_MASK_GRID_SIZE_MISMATCH")
            extra_gid = codes_grib_new_from_file(fh)
            if extra_gid is not None:
                codes_release(extra_gid)
                raise ValueError("ENS_LAND_MASK_NOT_SINGLE_MESSAGE")
            return {"fields": fields, "values": values, "proof": proof, "grid_identity_hash": _grid_identity(fields)}
        finally:
            codes_release(gid)


def _native_message_capture(gid: int) -> dict[str, Any]:
    """Capture observed metadata and byte identity, not a full-grid replay body.

    Optional audit capture must not change the existing numeric/eligibility path.
    No configuration declaration or synthetic digest substitutes for missing bytes.
    """
    capture: dict[str, Any] = {"capture_status": "UNKNOWN", "observed_headers": {}}
    try:
        for field in (
            "edition", "centre", "tablesVersion", "discipline", "paramId",
            "shortName", "units", "typeOfLevel", "level", "dataDate", "dataTime",
            "dataType", "number", "perturbationNumber", "stepUnits", "stepType",
            "startStep", "endStep", "stepRange", "lengthOfTimeRange",
            "indicatorOfUnitForTimeRange", "productDefinitionTemplateNumber",
            "typeOfStatisticalProcessing", *_GRID_KEYS,
        ):
            if codes_is_defined(gid, field):
                capture["observed_headers"][field] = codes_get(gid, field)
        if not all(field in capture["observed_headers"] for field in (
            "paramId", "shortName", "units", "typeOfLevel", "level",
            "dataDate", "dataTime", "dataType", "startStep", "endStep",
            "stepRange", "lengthOfTimeRange", "indicatorOfUnitForTimeRange",
        )):
            raise ValueError("native physical headers unavailable")
        raw = codes_get_message(gid)
        if not isinstance(raw, bytes) or not raw:
            raise ValueError("native message bytes unavailable")
        capture["raw_message_sha256"] = hashlib.sha256(raw).hexdigest()
        capture["raw_message_length"] = len(raw)
        # GRIB2 sections 0/1/3/4 describe identity/grid/product. Do not persist
        # section 7 or claim these metadata bytes can reconstruct its field.
        sections = []
        if raw[:4] != b"GRIB" or raw[7] != 2 or len(raw) < 20:
            raise ValueError("unsupported native metadata layout")
        if int.from_bytes(raw[8:16], "big") != len(raw) or raw[-4:] != b"7777":
            raise ValueError("invalid native message framing")
        sections.append({"section_number": 0, "offset": 0,
                         "bytes_base64": base64.b64encode(raw[:16]).decode("ascii")})
        offset = 16
        while offset < len(raw) - 4:
            length = int.from_bytes(raw[offset:offset + 4], "big")
            if length < 5 or offset + length > len(raw) - 4:
                raise ValueError("invalid native metadata section length")
            number = raw[offset + 4]
            if number in (1, 3, 4):
                sections.append({"section_number": number, "offset": offset,
                                 "bytes_base64": base64.b64encode(
                                     raw[offset:offset + length]).decode("ascii")})
            offset += length
        if offset != len(raw) - 4 or {row["section_number"] for row in sections} != {0, 1, 3, 4}:
            raise ValueError("native metadata sections incomplete")
        capture.update(capture_status="OBSERVED", metadata_sections=sections)
    except Exception as exc:
        capture["unavailable_reason"] = type(exc).__name__
    return capture


def _scan_grib_with_city_values(
    grib_path: Path,
    track: TrackConfig,
    cities: list[dict],
    *, mask: dict[str, object],
) -> dict[tuple[int, int], dict]:
    """Single pass over the GRIB extracting per-(member, step_hours) entries
    plus per-city values. Returns {(member, step): {meta+per_city_values}}.

    A single GRIB pass is far cheaper than re-opening per city.
    """
    bucket: dict[tuple[int, int], dict] = {}
    issue_dt: Optional[datetime] = None
    selected_cities: dict[str, dict[str, object]] | None = None
    rejected_cities: dict[str, str] = {}
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
                    if current_grid != mask["fields"]:
                        raise ValueError("ENS_LAND_MASK_TEMPERATURE_GRID_MISMATCH")
                    selected_cities = {}
                    for city in cities:
                        try:
                            selected_cities.update(_select_land_grid_points(
                                current_grid, [city], mask["values"].__getitem__,
                            ))
                        except (KeyError, ValueError) as exc:
                            rejected_cities[str(city["city"])] = str(exc)[:100]
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
                        "native_capture": _native_message_capture(gid),
                        "city_values_k": {},
                    }
                values = codes_get_values(gid)
                for city in cities:
                    if city["city"] in rejected_cities:
                        continue
                    selected = selected_cities[str(city["city"])]
                    idx = int(selected["selected_flat_index"])
                    bucket[key]["city_values_k"][city["city"]] = {
                        "value_k": float(values[idx]),
                        "nearest_grid_lat": selected["selected_lat"],
                        "nearest_grid_lon": selected["selected_lon"],
                        "nearest_grid_distance_km": selected["nearest_grid_distance_km"],
                    }
            finally:
                codes_release(gid)
    if issue_dt is not None and issue_dt.isoformat() != mask["proof"]["source_cycle_time"]:
        raise ValueError("ENS_LAND_MASK_TEMPERATURE_CYCLE_MISMATCH")
    return {"issue_dt": issue_dt, "entries": bucket, "selected_cities": selected_cities,
            "rejected_cities": rejected_cities,
            "temperature_grid_identity_hash": _grid_identity(grid_fields) if grid_fields is not None else None}


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
    mask_grib_path: Path | None = None,
    mask_proof_path: Path | None = None,
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

    if mask_grib_path is None or mask_proof_path is None:
        raise ValueError("ENS_LAND_MASK_SOURCE_PROOF_REQUIRED")
    mask = _read_land_mask(mask_grib_path, mask_proof_path)
    cities = _load_cities(manifest_path)
    if cities_filter is not None:
        cities = [c for c in cities if c["city"] in cities_filter]
    # One city with an unresolved station registry must not suppress all the
    # independent city sources. It receives no snapshot/coverage success.
    rejected_cities = [
        city["city"] for city in cities
        if not isinstance(city.get("station_geometry"), dict)
        or city["station_geometry"].get("validity_reason") is not None
    ]
    cities = [city for city in cities if city["city"] not in rejected_cities]
    if not cities:
        return {"status": "no_cities", "written": 0, "rejected_cities": rejected_cities}

    scan = _scan_grib_with_city_values(grib_path, track, cities, mask=mask)
    rejected_cities.extend(scan["rejected_cities"])
    cities = [city for city in cities if city["city"] not in scan["rejected_cities"]]
    entries: dict[tuple[int, int], dict] = scan["entries"]
    issue_dt: datetime = scan["issue_dt"]
    if issue_dt is None:
        return {"status": "no_matching_messages", "track": track_name, "written": 0}

    issue_date_compact = issue_dt.strftime("%Y%m%d")
    cycle_hour = issue_dt.hour
    manifest_hash = manifest_sha256(manifest_path)

    def surface_evidence(city: dict) -> dict[str, object]:
        selected = scan["selected_cities"][city["city"]]
        return {
            "revision": GRID_SURFACE_EVIDENCE_REVISION,
            "selection_rule": "nearest_land_of_surrounding_four_v1",
            "request_lat": float(city["lat"]),
            "request_lon": float(city["lon"]),
            "station_geometry": city["station_geometry"],
            "mask_source": mask["proof"]["source"],
            "mask_source_url": mask["proof"]["source_url"],
            "mask_source_index_url": mask["proof"]["source_index_url"],
            "mask_source_cycle_time": mask["proof"]["source_cycle_time"],
            "mask_source_fetched_at": mask["proof"]["source_fetched_at"],
            "mask_source_index_offset": mask["proof"]["source_index_offset"],
            "mask_source_index_length": mask["proof"]["source_index_length"],
            "mask_sha256": mask["proof"]["mask_sha256"],
            "mask_grid_identity_hash": mask["grid_identity_hash"],
            "temperature_grid_identity_hash": scan["temperature_grid_identity_hash"],
            "selected_flat_index": selected["selected_flat_index"],
            "selected_lat": selected["selected_lat"],
            "selected_lon": selected["selected_lon"],
            "selected_land_fraction": selected["selected_land_fraction"],
            "four_neighbors": selected["four_neighbors"],
        }

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
            native_messages: list[dict[str, Any]] = []
            point_windows: list[dict[str, Any]] = []
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
                members_native_windows.setdefault(member, []).append({
                    "start_step_hours": int(bucket["start_step"]),
                    "end_step_hours": int(bucket["end_step"]),
                    "value_native_unit": value_native,
                })
                capture = bucket.get("native_capture") or {
                    "capture_status": "UNKNOWN", "unavailable_reason": "CAPTURE_MISSING",
                }
                native_messages.append(capture)
                point_windows.append({
                    "member": member, "start_step_hours": int(bucket["start_step"]),
                    "end_step_hours": int(bucket["end_step"]), "value_k": value_k,
                    "value_native_unit": value_native,
                    "raw_message_sha256": capture.get("raw_message_sha256"),
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
                    "nearest_grid_lat": scan["selected_cities"][city_name]["selected_lat"],
                    "nearest_grid_lon": scan["selected_cities"][city_name]["selected_lon"],
                    "nearest_grid_distance_km": scan["selected_cities"][city_name]["nearest_grid_distance_km"],
                    "grid_surface_evidence": surface_evidence(city),
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
                        "native_windows": sorted(members_native_windows.get(m, []),
                                                 key=lambda row: (row["start_step_hours"], row["end_step_hours"])),
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
                    "nearest_grid_lat": scan["selected_cities"][city_name]["selected_lat"],
                    "nearest_grid_lon": scan["selected_cities"][city_name]["selected_lon"],
                    "nearest_grid_distance_km": scan["selected_cities"][city_name]["nearest_grid_distance_km"],
                    "grid_surface_evidence": surface_evidence(city),
                    "selected_step_ranges_inner": sorted(selected_step_ranges_inner),
                    "selected_step_ranges_boundary": sorted(selected_step_ranges_boundary),
                    "member_count": len(members_out),
                    "missing_members": missing_members,
                    "training_allowed": training_allowed,
                    "members": members_out,
                }

            selected = scan["selected_cities"][city_name]
            payload["native_capture_receipt"] = {
                "revision": "ens_native_capture_receipt_v1",
                "capture_kind": "grib_metadata_and_selected_point_decode",
                "capture_status": "OBSERVED" if all(
                    capture.get("capture_status") == "OBSERVED" for capture in native_messages
                ) else "UNKNOWN",
                "selected_point": {"flat_index": selected["selected_flat_index"],
                                   "lat": selected["selected_lat"], "lon": selected["selected_lon"]},
                "native_unit": unit,
                "messages": native_messages,
                "point_windows": point_windows,
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
        "rejected_cities": rejected_cities,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grib-path", type=Path, required=True)
    parser.add_argument("--mask-grib-path", type=Path, required=True)
    parser.add_argument("--mask-proof-path", type=Path, required=True)
    parser.add_argument("--track", choices=sorted(TRACKS), required=True)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--cities", nargs="*", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cities_filter = set(args.cities) if args.cities else None
    summary = extract_open_ens_localday(
        grib_path=args.grib_path,
        mask_grib_path=args.mask_grib_path,
        mask_proof_path=args.mask_proof_path,
        track_name=args.track,
        manifest_path=args.manifest_path,
        output_root=args.output_root,
        cities_filter=cities_filter,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
