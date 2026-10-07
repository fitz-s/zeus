#!/usr/bin/env python3
# Created: 2026-09-22
# Last reused/audited: 2026-10-04
# Authority basis: current OpenData source contract; native 3h local-day extrema.
# Lifecycle: created=2026-09-22; last_reviewed=2026-10-04; last_reused=2026-10-04
# Purpose: Native ENS extrema JSON and offline 2t knot decode with original byte/point proof; no DB writes.
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


def _read_surface_geopotential(path: Path, proof_path: Path) -> dict[str, Any]:
    """Optional raw phi on the distributed surface grid, not a station height."""
    if not 100 <= path.stat().st_size <= 1024 * 1024:
        raise ValueError("ENS_SURFACE_GEOPOTENTIAL_BODY_BOUNDS_INVALID")
    raw = path.read_bytes()
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    audit_observed_at = datetime.now(timezone.utc)
    if (not isinstance(proof, dict)
            or proof.get("source") != "ecmwf_open_data_ifs_oper_fc_step0_z"
            or proof.get("raw_message_sha256") != hashlib.sha256(raw).hexdigest()
            or not 100 <= len(raw) <= 1024 * 1024
            or proof.get("source_index_length") != len(raw)
            or type(proof.get("source_index_offset")) is not int
            or proof["source_index_offset"] < 0
            or not str(proof.get("source_url", "")).startswith("https://")
            or proof.get("source_index_url") != str(proof["source_url"]).rsplit(".", 1)[0] + ".index"):
        raise ValueError("ENS_SURFACE_GEOPOTENTIAL_SOURCE_INVALID")
    with path.open("rb") as fh:
        gid = codes_grib_new_from_file(fh)
        if gid is None:
            raise ValueError("ENS_SURFACE_GEOPOTENTIAL_MESSAGE_MISSING")
        try:
            headers = {key: codes_get(gid, key) for key in (
                "edition", "centre", "paramId", "shortName", "units", "typeOfLevel",
                "level", "dataDate", "dataTime", "dataType", "step", *_GRID_KEYS,
            )}
            if (headers["paramId"] != 129 or headers["shortName"] != "z"
                    or headers["units"] != "m**2 s**-2"
                    or headers["typeOfLevel"] != "surface" or headers["level"] != 0
                    or headers["centre"] != "ecmf" or headers["dataType"] != "fc"
                    or headers["step"] != 0):
                raise ValueError("ENS_SURFACE_GEOPOTENTIAL_PARAMETER_INVALID")
            cycle = datetime.strptime(
                f'{int(headers["dataDate"]):08d}{int(headers["dataTime"]):04d}', "%Y%m%d%H%M",
            ).replace(tzinfo=timezone.utc)
            fetched = datetime.fromisoformat(str(proof["source_fetched_at"]))
            if cycle.isoformat() != proof.get("source_cycle_time"):
                raise ValueError("ENS_SURFACE_GEOPOTENTIAL_CYCLE_INVALID")
            if fetched.tzinfo is None or not cycle <= fetched <= audit_observed_at:
                raise ValueError("ENS_SURFACE_GEOPOTENTIAL_POSSESSION_CLOCK_INVALID")
            values = codes_get_values(gid)
            fields = {key: headers[key] for key in _GRID_KEYS}
            if len(values) != fields["Ni"] * fields["Nj"]:
                raise ValueError("ENS_SURFACE_GEOPOTENTIAL_GRID_SIZE_INVALID")
            extra = codes_grib_new_from_file(fh)
            if extra is not None:
                codes_release(extra)
                raise ValueError("ENS_SURFACE_GEOPOTENTIAL_NOT_SINGLE_MESSAGE")
            if codes_get_message(gid) != raw:
                raise ValueError("ENS_SURFACE_GEOPOTENTIAL_BODY_INVALID")
            return {"values": values, "fields": fields, "observed_headers": headers,
                    "proof": proof, "grid_identity_hash": _grid_identity(fields),
                    "audit_observed_at_utc": audit_observed_at.isoformat()}
        finally:
            codes_release(gid)


def _native_message_capture(gid: int, *, instantaneous: bool = False) -> dict[str, Any]:
    """Capture observed metadata and byte identity, not a full-grid replay body.

    Optional audit capture must not change the existing numeric/eligibility path.
    No configuration declaration or synthetic digest substitutes for missing bytes.
    """
    capture: dict[str, Any] = {"capture_status": "UNKNOWN", "observed_headers": {}}
    try:
        fields = (
            "edition", "centre", "tablesVersion", "discipline", "paramId",
            "shortName", "units", "typeOfLevel", "level", "dataDate", "dataTime",
            "dataType", "number", "perturbationNumber", "stepUnits", "stepType",
            "startStep", "endStep", "stepRange", "lengthOfTimeRange",
            "indicatorOfUnitForTimeRange", "productDefinitionTemplateNumber",
            "typeOfStatisticalProcessing", *_GRID_KEYS,
        )
        if instantaneous:
            fields += ("generatingProcessIdentifier", "typeOfGeneratingProcess", "validityDate", "validityTime")
        for field in fields:
            if codes_is_defined(gid, field):
                capture["observed_headers"][field] = codes_get(gid, field)
        required = (
            "paramId", "shortName", "units", "typeOfLevel", "level",
            "dataDate", "dataTime", "dataType", "startStep", "endStep",
            "stepRange", "lengthOfTimeRange", "indicatorOfUnitForTimeRange",
        )
        if instantaneous:
            required = ("edition", "centre", "paramId", "shortName", "units", "typeOfLevel", "level",
                        "dataDate", "dataTime", "dataType", "stepUnits", "stepType", "startStep",
                        "endStep", "stepRange", "generatingProcessIdentifier", "typeOfGeneratingProcess",
                        "validityDate", "validityTime", "productDefinitionTemplateNumber", *_GRID_KEYS)
        if not all(field in capture["observed_headers"] for field in required):
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


def _open_ens_source_binding(raw: bytes, evidence: dict[str, Any], headers: dict[str, Any],
                             *, param: str, member: int, step: int, run: datetime) -> dict[str, Any]:
    """Check supplied original index/range bytes, never a request/metadata echo.

    This proves byte consistency only. The caller must retain possession evidence;
    this offline decoder never grants forecast or trading authority.
    """
    index = evidence["original_index_bytes"]
    if (not isinstance(index, bytes) or not index
            or hashlib.sha256(index).hexdigest() != evidence["source_index_sha256"]
            or evidence["original_range_bytes"] != raw
            or hashlib.sha256(raw).hexdigest() != evidence["raw_message_sha256"]):
        raise ValueError("ENS_POINT_SOURCE_BYTES_MISMATCH")
    offset, length = evidence["source_index_offset"], evidence["source_index_length"]
    if type(offset) is not int or offset < 0 or type(length) is not int or length != len(raw):
        raise ValueError("ENS_POINT_SOURCE_RANGE_INVALID")
    matches = []
    for line in index.splitlines():
        row = json.loads(line)
        if row.get("_offset") == offset and row.get("_length") == length:
            matches.append((line, row))
    if len(matches) != 1:
        raise ValueError("ENS_POINT_SOURCE_INDEX_AMBIGUOUS")
    line, row = matches[0]
    if hashlib.sha256(line).hexdigest() != evidence["source_index_line_sha256"]:
        raise ValueError("ENS_POINT_SOURCE_INDEX_LINE_MISMATCH")
    stream, file_type, mars_type = ("oper", "fc", "fc") if member == 0 else ("enfo", "ef", "pf")
    if (row.get("param") != param or row.get("levtype") != "sfc" or row.get("class") != "od"
            or str(row.get("date")) != run.strftime("%Y%m%d")
            or str(row.get("time")).zfill(4) != run.strftime("%H%M")
            or str(row.get("step")) != str(step) or row.get("stream") != stream
            or row.get("type") != mars_type or headers["dataType"] != mars_type
            or (member != 0 and str(row.get("number")) != str(member))
            or (member == 0 and str(row.get("number", "0")) != "0")):
        raise ValueError("ENS_POINT_SOURCE_INDEX_IDENTITY_MISMATCH")
    url = str(evidence["source_url"])
    expected_suffix = (f"/{run:%Y%m%d}/{run:%H}z/ifs/0p25/{stream}/"
                       f"{run:%Y%m%d%H%M}00-{step}h-{stream}-{file_type}.grib2")
    if (not url.startswith("https://") or not url.endswith(expected_suffix)
            or evidence["source_index_url"] != url[:-6] + ".index"):
        raise ValueError("ENS_POINT_SOURCE_ENVELOPE_MISMATCH")
    fetched = datetime.fromisoformat(str(evidence["source_fetched_at"]))
    if fetched.tzinfo is None or not run <= fetched <= datetime.now(timezone.utc):
        raise ValueError("ENS_POINT_SOURCE_POSSESSION_CLOCK_INVALID")
    return {key: evidence[key] for key in ("source_url", "source_index_url", "source_index_sha256",
        "source_index_line_sha256", "source_index_offset", "source_index_length", "raw_message_sha256",
        "source_fetched_at")}


def _open_ens_original_surface_capture(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        gid = codes_grib_new_from_file(fh)
        if gid is None:
            raise ValueError("ENS_POINT_SURFACE_MESSAGE_UNAVAILABLE")
        try:
            capture = _native_message_capture(gid, instantaneous=True)
            if capture["capture_status"] != "OBSERVED":
                raise ValueError("ENS_POINT_ORIGINAL_SURFACE_METADATA_UNAVAILABLE")
            if capture["raw_message_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError("ENS_POINT_ORIGINAL_SURFACE_BODY_MISMATCH")
            return capture
        finally:
            codes_release(gid)


def _open_ens_original_grid(section: bytes) -> dict[str, Any]:
    """GRIB2 template 3.0 geometry in its original angular scale."""
    if len(section) != 72 or section[4] != 3 or int.from_bytes(section[12:14], "big") != 0:
        raise ValueError("ENS_POINT_ORIGINAL_GRID_LAYOUT_UNSUPPORTED")
    ni, nj = int.from_bytes(section[30:34], "big"), int.from_bytes(section[34:38], "big")
    basic_angle = int.from_bytes(section[38:42], "big")
    subdivisions = int.from_bytes(section[42:46], "big")
    if basic_angle == 0 and subdivisions in (0, 0xFFFFFFFF):
        scale = 1e-6
    elif 0 < basic_angle < 0xFFFFFFFF and 0 < subdivisions < 0xFFFFFFFF:
        scale = basic_angle / subdivisions
    else:
        raise ValueError("ENS_POINT_ORIGINAL_GRID_ANGLE_INVALID")
    def signed_angle(offset: int) -> float:
        value = int.from_bytes(section[offset:offset + 4], "big")
        return (-1 if value & 0x80000000 else 1) * (value & 0x7FFFFFFF) * scale
    if int.from_bytes(section[6:10], "big") != ni * nj:
        raise ValueError("ENS_POINT_ORIGINAL_GRID_SIZE_INVALID")
    return {"gridType": "regular_ll", "Ni": ni, "Nj": nj,
        "latitudeOfFirstGridPointInDegrees": signed_angle(46),
        "longitudeOfFirstGridPointInDegrees": signed_angle(50),
        "iDirectionIncrementInDegrees": int.from_bytes(section[63:67], "big") * scale,
        "jDirectionIncrementInDegrees": int.from_bytes(section[67:71], "big") * scale,
        "scanningMode": section[71]}


def decode_open_ens_temperature_knots(
    *, grib_path: Path, explicit_manifest: list[dict], expected_run_utc: datetime,
    required_steps: list[int], mask_grib_path: Path, mask_proof_path: Path,
    surface_geopotential_grib_path: Path | None = None,
    surface_geopotential_proof_path: Path | None = None,
    message_source_evidence: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read native IFS 50r1 2t point knots. No CLI, collection or DB writes.

    ``message_source_evidence`` maps each local message byte offset to supplied
    original index/range bytes and their source URL, hashes, offset, length and
    possession clock. A missing binding leaves transport UNKNOWN; an inconsistent
    supplied binding makes the complete run unavailable. Manifest coordinates
    request a cell; returned coordinates come from the original native grid.

    SCOPE: this supplied run/step/grid ensemble only. DRAIN: supply the complete
    original 51-member bytes and provenance. RESET: each independent decode
    recomputes the proof; no frozen receipt blocks a later valid input.
    Missing transport/phi stays UNKNOWN. Neither decoding nor supplied source
    metadata can promote these knots to LIVE_QUALIFIED or settlement extrema.
    """
    unavailable = {"decode_status": "UNAVAILABLE", "transport_status": "UNKNOWN",
        "qualification_status": "OFFLINE_ONLY", "live_qualification_status": "NOT_EVALUATED",
        "quantity_role": "native_2m_temperature_instantaneous_knots",
        "temporal_representation": "acquired_native_knots", "projection_status": "NOT_PERFORMED",
        "extrema_status": "NOT_COMPUTED", "native_knots": []}
    try:
        run = expected_run_utc
        if not isinstance(run, datetime) or run.tzinfo is None or run.utcoffset() != timedelta(0):
            raise ValueError("ENS_POINT_EXPECTED_RUN_INVALID")
        if run.minute or run.second or run.microsecond or run.hour not in (0, 6, 12, 18):
            raise ValueError("ENS_POINT_EXPECTED_RUN_INVALID")
        if run > datetime.now(timezone.utc):
            raise ValueError("ENS_POINT_EXPECTED_RUN_FUTURE")
        steps = list(required_steps)
        if not steps or len(set(steps)) != len(steps) or any(type(s) is not int for s in steps):
            raise ValueError("ENS_POINT_REQUIRED_STEPS_INVALID")
        control_horizon = 240 if run.hour in (0, 12) else 90
        if any(s < 0 or s > control_horizon or (s % 3 if s <= 144 else s % 6) for s in steps):
            raise ValueError("ENS_POINT_STEP_NOT_IN_NATIVE_CONTROL_PF_INTERSECTION")
        if not explicit_manifest or len({c["city"] for c in explicit_manifest}) != len(explicit_manifest):
            raise ValueError("ENS_POINT_CITY_MANIFEST_INVALID")
        mask = _read_land_mask(Path(mask_grib_path), Path(mask_proof_path))
        mask_capture = _open_ens_original_surface_capture(Path(mask_grib_path))
        mask_section3 = next(s["bytes_base64"] for s in mask_capture["metadata_sections"] if s["section_number"] == 3)
        if _open_ens_original_grid(base64.b64decode(mask_section3)) != mask["fields"]:
            raise ValueError("ENS_POINT_ORIGINAL_LSM_GRID_HEADER_MISMATCH")
        # Static fields keep their own original clock. A prior-run mask without
        # source-role/model-validity proof stays diagnostic, never renewed to this run.
        mask_validity = ("SAME_RUN_OBSERVED" if mask["proof"]["source_cycle_time"] == run.isoformat()
                         else "UNKNOWN")
        selected = _select_land_grid_points(mask["fields"], explicit_manifest, mask["values"].__getitem__)
        surface_receipt: dict[str, Any] = {"capture_status": "UNKNOWN", "transport_status": "UNKNOWN",
            "station_ground_status": "UNPROVEN", "sensor_agl_status": "UNPROVEN",
                                          "unavailable_reason": "SURFACE_GEOPOTENTIAL_UNAVAILABLE"}
        surface = None
        try:
            if surface_geopotential_grib_path is None or surface_geopotential_proof_path is None:
                raise ValueError("SURFACE_GEOPOTENTIAL_UNAVAILABLE")
            surface = _read_surface_geopotential(Path(surface_geopotential_grib_path),
                                                Path(surface_geopotential_proof_path))
            surface_capture = _open_ens_original_surface_capture(Path(surface_geopotential_grib_path))
            surface_section3 = next(s["bytes_base64"] for s in surface_capture["metadata_sections"] if s["section_number"] == 3)
            if (surface["fields"] != mask["fields"] or surface_section3 != mask_section3
                    or surface["proof"]["source_cycle_time"] != run.isoformat()):
                raise ValueError("ENS_POINT_PHI_GRID_OR_RUN_MISMATCH")
            if any(not math.isfinite(float(surface["values"][c["flat_index"]]))
                   for point in selected.values() for c in point["four_neighbors"]):
                raise ValueError("ENS_POINT_PHI_VALUE_INVALID")
            surface_receipt = {"capture_status": "OBSERVED", "transport_status": "UNKNOWN",
                "quantity_role": "model_surface_geopotential",
                "grid_identity_hash": surface["grid_identity_hash"], "proof": surface["proof"],
                "observed_headers": surface["observed_headers"], "original_message": surface_capture,
                "station_ground_status": "UNPROVEN", "sensor_agl_status": "UNPROVEN"}
        except (ValueError, KeyError, OSError, TypeError) as exc:
            surface = None
            surface_receipt["unavailable_reason"] = str(exc)
        for selection in selected.values():
            selection["surface_class"] = ("PURE_LAND" if selection["selected_land_fraction"] == 1.
                                          else "MIXED_LAND_WATER")
            for cell in selection["four_neighbors"]:
                phi = float(surface["values"][cell["flat_index"]]) if surface is not None else None
                cell["raw_phi_m2_s2"] = phi
        messages, knots, seen, product_grids, process_types = [], [], set(), set(), {}
        transport_complete = True
        consumed_offsets = set()
        with Path(grib_path).open("rb") as fh:
            while True:
                local_offset = fh.tell()
                gid = codes_grib_new_from_file(fh)
                if gid is None:
                    break
                try:
                    capture = _native_message_capture(gid, instantaneous=True)
                    if capture["capture_status"] != "OBSERVED":
                        raise ValueError("ENS_POINT_NATIVE_CAPTURE_UNAVAILABLE")
                    h = capture["observed_headers"]
                    raw = codes_get_message(gid)
                    sections = {s["section_number"]: base64.b64decode(s["bytes_base64"], validate=True)
                                for s in capture["metadata_sections"]}
                    s1, s4 = sections[1], sections[4]
                    template = int.from_bytes(s4[7:9], "big")
                    if (h["edition"] != 2 or h["centre"] != "ecmf" or h["paramId"] != 167
                            or h["shortName"] != "2t" or h["units"] != "K"
                            or h["typeOfLevel"] != "heightAboveGround" or h["level"] != 2
                            or h["stepType"] != "instant" or h["stepUnits"] != 1
                            or h["generatingProcessIdentifier"] != 161 or template not in (0, 1)):
                        raise ValueError("ENS_POINT_PHYSICAL_OR_50R1_PROCESS_INVALID")
                    # GRIB2 discipline 0 / category 0 / parameter 0 is temperature.
                    # The original 2m level below distinguishes 2t from other heights;
                    # dewpoint (parameter 6) cannot borrow an echoed 167/2t header.
                    if raw[6] != 0 or s4[9:11] != b"\x00\x00":
                        raise ValueError("ENS_POINT_ORIGINAL_PARAMETER_IDENTITY_MISMATCH")
                    step = int(h["endStep"])
                    if (step not in steps or h["startStep"] != step or str(h["stepRange"]) != str(step)
                            or (h["dataDate"], h["dataTime"]) != (int(run.strftime("%Y%m%d")), run.hour * 100)):
                        raise ValueError("ENS_POINT_RUN_OR_STEP_MISMATCH")
                    valid = run + timedelta(hours=step)
                    if (h["validityDate"], h["validityTime"]) != (int(valid.strftime("%Y%m%d")), valid.hour * 100):
                        raise ValueError("ENS_POINT_VALID_TIME_MISMATCH")
                    if (int.from_bytes(s1[5:7], "big") != 98
                            or int.from_bytes(s1[12:14], "big") != run.year
                            or tuple(s1[14:19]) != (run.month, run.day, run.hour, 0, 0)
                            or template != h["productDefinitionTemplateNumber"]
                            or s4[13] != h["generatingProcessIdentifier"]
                            or s4[11] != h["typeOfGeneratingProcess"] or s4[17] != 1
                            or int.from_bytes(s4[18:22], "big") != step
                            or s4[22] != 103 or s4[23] != 0 or int.from_bytes(s4[24:28], "big") != 2):
                        raise ValueError("ENS_POINT_ORIGINAL_SECTION_HEADER_MISMATCH")
                    grid = {key: h[key] for key in _GRID_KEYS}
                    if _open_ens_original_grid(sections[3]) != grid:
                        raise ValueError("ENS_POINT_ORIGINAL_GRID_HEADER_MISMATCH")
                    if grid != mask["fields"]:
                        raise ValueError("ENS_POINT_GRID_MISMATCH")
                    if base64.b64encode(sections[3]).decode("ascii") != mask_section3:
                        raise ValueError("ENS_POINT_ORIGINAL_LSM_GRID_MISMATCH")
                    product_grids.add(hashlib.sha256(sections[3]).hexdigest())
                    if len(product_grids) != 1:
                        raise ValueError("ENS_POINT_MIXED_ORIGINAL_GRID")
                    number = h.get("number", h.get("perturbationNumber"))
                    if h["dataType"] == "fc" and template == 0 and number in (None, 0):
                        member = 0
                    elif (h["dataType"] == "pf" and template == 1 and type(number) is int
                          and 1 <= number <= 50 and h.get("perturbationNumber", number) == number):
                        member = number
                    else:
                        raise ValueError("ENS_POINT_MEMBER_IDENTITY_INVALID")
                    if (s1[20] != (1 if member == 0 else 4)
                            or (member != 0 and s4[35] != member)):
                        raise ValueError("ENS_POINT_ORIGINAL_MEMBER_HEADER_MISMATCH")
                    previous = process_types.setdefault(h["dataType"], h["typeOfGeneratingProcess"])
                    if previous != h["typeOfGeneratingProcess"]:
                        raise ValueError("ENS_POINT_MIXED_GENERATING_PROCESS")
                    if (member, step) in seen:
                        raise ValueError("ENS_POINT_DUPLICATE_MEMBER_STEP")
                    seen.add((member, step))
                    evidence = (message_source_evidence or {}).get(local_offset)
                    if evidence is None:
                        transport_complete = False
                        capture["transport_status"] = "UNKNOWN"
                    else:
                        capture["source_binding"] = _open_ens_source_binding(raw, evidence, h,
                            param="2t", member=member, step=step, run=run)
                        capture["transport_status"] = "OBSERVED"
                        consumed_offsets.add(local_offset)
                    # A 50r1 fc is a control only with the official original envelope/index.
                    # Without it retain decoded bytes as unqualified diagnostics.
                    values = codes_get_values(gid)
                    if len(values) != int(grid["Ni"]) * int(grid["Nj"]):
                        raise ValueError("ENS_POINT_VALUES_GRID_SIZE_INVALID")
                    for city in explicit_manifest:
                        point = selected[city["city"]]
                        value = float(values[point["selected_flat_index"]])
                        if not math.isfinite(value) or value == codes_get(gid, "missingValue"):
                            raise ValueError("ENS_POINT_TEMPERATURE_UNAVAILABLE")
                        knots.append({"city": city["city"], "member": member, "step_hours": step,
                            "valid_time_utc": valid.isoformat(), "value_k": value,
                            "native_unit": city["unit"], "value_native_unit": kelvin_to_native(value, city["unit"]),
                            "selected_point": {"flat_index": point["selected_flat_index"],
                                "lat": point["selected_lat"], "lon": point["selected_lon"]},
                            "raw_message_sha256": capture["raw_message_sha256"]})
                    messages.append(capture)
                finally:
                    codes_release(gid)
        if seen != {(member, step) for member in range(51) for step in steps}:
            raise ValueError("ENS_POINT_MEMBER_STEP_SET_INCOMPLETE")
        if message_source_evidence is not None and set(message_source_evidence) != consumed_offsets:
            raise ValueError("ENS_POINT_SOURCE_BINDINGS_NOT_EXACT")
        return {**unavailable, "decode_status": "AVAILABLE", "transport_status": "OBSERVED" if transport_complete else "UNKNOWN",
            "native_step_hours": sorted(steps), "temporal_grid_hours": sorted(steps),
            "run_time_utc": run.isoformat(), "native_knots": knots, "messages": messages,
            "selected_cities": selected, "grid_identity_hash": mask["grid_identity_hash"],
            "land_mask_receipt": {"capture_status": "OBSERVED", "transport_status": "UNKNOWN", "proof": mask["proof"],
                "static_run_validity_status": mask_validity, "original_message": mask_capture,
                "raw_message_sha256": hashlib.sha256(Path(mask_grib_path).read_bytes()).hexdigest()},
            "surface_geopotential_receipt": surface_receipt}
    except Exception as exc:
        return {**unavailable, "unavailable_reason": str(exc) or type(exc).__name__}


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
    surface_geopotential_grib_path: Path | None = None,
    surface_geopotential_proof_path: Path | None = None,
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

    surface = None
    surface_unknown = {"capture_status": "UNKNOWN", "unavailable_reason": "SURFACE_GEOPOTENTIAL_UNAVAILABLE"}
    try:
        if surface_geopotential_grib_path is None or surface_geopotential_proof_path is None:
            raise ValueError("ENS_SURFACE_GEOPOTENTIAL_EXPLICIT_SOURCE_PROOF_REQUIRED")
        surface = _read_surface_geopotential(
            surface_geopotential_grib_path, surface_geopotential_proof_path,
        )
        if (surface["fields"] != mask["fields"]
                or surface["grid_identity_hash"] != scan["temperature_grid_identity_hash"]
                or surface["proof"]["source_cycle_time"] != issue_dt.isoformat()):
            raise ValueError("ENS_SURFACE_GEOPOTENTIAL_GRID_OR_CYCLE_MISMATCH")
    except Exception as exc:
        surface = None
        surface_unknown["unavailable_reason"] = str(exc)[:200] or type(exc).__name__

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
            terrain_receipt = dict(surface_unknown)
            if surface is not None:
                phi = float(surface["values"][selected["selected_flat_index"]])
                if math.isfinite(phi) and abs(phi) < 1e10:
                    terrain_receipt = {
                        "capture_status": "OBSERVED",
                        "quantity_role": "model_surface_geopotential_on_wire_distribution_grid",
                        "observed_headers": surface["observed_headers"],
                        "grid_identity_hash": surface["grid_identity_hash"],
                        "selected_point": dict(payload["native_capture_receipt"]["selected_point"]),
                        "raw_phi_m2_s2": phi,
                        **{key: surface["proof"][key] for key in (
                            "source", "source_url", "source_index_url", "source_cycle_time",
                            "source_fetched_at", "source_index_offset", "source_index_length",
                            "raw_message_sha256",
                        )},
                        "source_issued_at": None,
                        "audit_observed_at_utc": surface["audit_observed_at_utc"],
                        "audit_scope": "AUDIT_ONLY_NOT_DECISION_INPUT",
                        "source_fetched_at_role": "LOCAL_CACHE_POSSESSION",
                    }
                else:
                    terrain_receipt["unavailable_reason"] = "ENS_SURFACE_GEOPOTENTIAL_VALUE_INVALID"
            payload["native_capture_receipt"]["surface_geopotential_receipt_v1"] = terrain_receipt
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
    parser.add_argument("--surface-geopotential-grib-path", type=Path)
    parser.add_argument("--surface-geopotential-proof-path", type=Path)
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
        surface_geopotential_grib_path=args.surface_geopotential_grib_path,
        surface_geopotential_proof_path=args.surface_geopotential_proof_path,
        track_name=args.track,
        manifest_path=args.manifest_path,
        output_root=args.output_root,
        cities_filter=cities_filter,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
