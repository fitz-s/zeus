# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 (~07:10Z) — third anchor transport
#   (direct S3 om-file read from open-meteo's open-data bucket) so a model run's
#   already-written timesteps can be consumed WITHOUT waiting for the provider's
#   run-completion flag. K4.0b(f) anchor transport ladder rung 3. The single-runs
#   (rung 1) and meta-stamped standard (rung 2) transports are unchanged; this rung
#   fires ONLY when both refuse AND the bucket's in-progress.json declares the wanted
#   run with every needed local-day timestep present (no extrapolation, no gap-fill).
"""Rung-3 anchor transport: direct partial-run read from the open-meteo S3 bucket.

The provider writes each hourly timestep of an in-progress ECMWF IFS 9km run to
``s3://openmeteo/data_spatial/ecmwf_ifs/<YYYY>/<MM>/<DD>/<HHHH>Z/<valid>.om`` as soon
as that step is computed — long before ``in-progress.json`` flips ``completed`` true.
Each ``.om`` file holds every variable for that one instant as a flat float32 array on
the ECMWF octahedral reduced-Gaussian **O1280** grid (6,599,680 points; NOT regridded
to a regular lat/lon grid). This module:

  1. reads ``in-progress.json`` / ``latest.json`` to learn the bucket's declared run
     identity (``reference_time``), its ``valid_times`` set, and the ``completed`` flag;
  2. enforces the partial-run admission rule (Fitz #4): a read for run R is admissible
     iff the bucket declares ``reference_time == R`` AND every hourly valid_time the
     caller needs is present in the declared ``valid_times`` — otherwise it REFUSES;
  3. maps each (city lat, lon) to its nearest O1280 grid index via the published
     octahedral grid definition (Gaussian latitudes = Legendre roots; per-row longitude
     count ``20 + 4*j``; scan north→south, each row west→east from 0°E) — verified
     against the single-runs API to ≤0.05C (docs/evidence/anchor_channels/
     2026-06-11_bucket_vs_api_grid_validation.md);
  4. returns a payload in the SAME shape the API serves —
     ``{"hourly": {"time": [...], "temperature_2m": [...]}, "utc_offset_seconds": ...}`` —
     so ``extract_openmeteo_ecmwf_ifs9_localday_anchor`` and the manifest builder consume
     it unchanged.

Until at least one VERIFIED bucket↔API cross-check exists for a cycle, bucket artifacts
carry ``run_authority = "bucket_partial_run_unverified"`` and the downloader prefers
rungs 1-2 whenever they can serve the same cycle.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc

BUCKET_HTTP_BASE = "https://openmeteo.s3.amazonaws.com"
BUCKET_S3_PREFIX = "openmeteo/data_spatial/ecmwf_ifs"
DATA_SPATIAL_PREFIX = "data_spatial/ecmwf_ifs"
IN_PROGRESS_KEY = f"{DATA_SPATIAL_PREFIX}/in-progress.json"
LATEST_KEY = f"{DATA_SPATIAL_PREFIX}/latest.json"

RUN_AUTHORITY_BUCKET_UNVERIFIED = "bucket_partial_run_unverified"
RUN_AUTHORITY_BUCKET_VERIFIED = "bucket_partial_run_verified"


class BucketTransportNotAdmissible(Exception):
    """Rung-3 cannot serve this (city, run): no admissible transport this cycle.

    Raised when the bucket transport's admission gate fails (run not declared, a needed
    timestep not yet written, or the city is not cross-check-whitelisted). It is a
    NON-ERROR control-flow signal — the caller skips this city for this cycle (it stays
    uncovered until a higher rung serves it next tick), distinct from a genuine defect
    (auth/5xx/schema) which must still raise loudly."""

# City whitelist (antibody, Fitz #4 + #3). MEASURED 2026-06-11 against the completed
# 06-10T06Z run (docs/evidence/anchor_channels/2026-06-11_bucket_vs_api_grid_validation.md):
# the raw nearest-O1280-grid-point read matches the single-runs API to ≤0.05C for flat /
# inland cities, but DIVERGES badly for coastal / complex-terrain cities (Tokyo −2.2C,
# Singapore +2.35C, Chongqing +0.95C, Cape Town +0.55C) because open-meteo's point API
# applies elevation / lapse-rate / land-sea-mask downscaling that a raw grid read does not.
# Therefore the bucket transport is CITY-WHITELISTED: it serves ONLY cities whose
# bucket↔API cross-check has been VERIFIED (≤0.05C). The whitelist is sourced from the
# cross-check receipts (state/anchor_cross_check.json) at call time and is EMPTY until the
# first VERIFIED receipt lands — so a brand-new deploy serves nothing via rung 3 until the
# antibody confirms a city, and biased anchors are impossible by construction.
CROSS_CHECK_RECEIPT_PATH = "state/anchor_cross_check.json"
# One API quantum (0.1C). The single-runs API rounds to 0.1C; a city whose bucket read
# matches the API to within rounding shows max|d| = 0.05C, while real coastal/terrain
# downscaling bias is ≥0.25C (measured 2026-06-11). Whitelist tolerance = the cross-check's
# BUCKET_VS_API_TOLERANCE_C so the whitelist admits exactly the VERIFIED set.
CITY_WHITELIST_TOLERANCE_C = 0.1

# Octahedral reduced-Gaussian O1280 grid (ECMWF). 2*N latitude rows; row j (0-based,
# counted from the nearest pole) carries 20 + 4*j longitudes. Total = 6,599,680.
O1280_N = 1280
O1280_TOTAL_POINTS = 6_599_680
TEMPERATURE_VARIABLE = "temperature_2m"

# ---------------------------------------------------------------------------
# DOWNSCALING constants (Open-Meteo point-query replication). MEASURED + VERIFIED
# 2026-06-11 against the open-meteo source (github.com/open-meteo/open-meteo) and
# the run-pinned single-runs API for the completed 06-10T06Z run
# (docs/evidence/anchor_channels/2026-06-11_bucket_downscaling_49city_parity.md).
#
# Open-Meteo's /v1/forecast does NOT return the raw nearest O1280 gridpoint for a
# (lat,lon). It applies the default `cell_selection=land` terrain-optimised selection
# (Sources/App/Domains/GaussianGrid.swift::findPointTerrainOptimised) over a 3x3 grid
# box, then statistical-downscaling elevation correction
# (Sources/App/Helper/Reader/GenericReader.swift::scale):
#     corrected_T = grid_T + (gridElevation - targetElevation) * LAPSE_RATE_K_PER_M
# where targetElevation is the requested point's 90m-DEM elevation (the API `elevation`
# field) and gridElevation is the model surface elevation (HSURF) at the chosen cell.
# A raw nearest read skips BOTH steps — which is why coastal cities whose nearest O1280
# cell is SEA (HSURF == SEA_SENTINEL_M) diverged 0.25..9.75C. The downscaled read matches
# the API to <=0.1C (Tokyo 0.03C, Singapore 0.05C — both >2C off raw).
LAPSE_RATE_K_PER_M = 0.0065  # GenericReader.swift scale(): data[i] += (modelElev - targetElev)*0.0065
SEA_SENTINEL_M = -999.0      # Gridable.swift readElevation(): elevation <= -999 => sea grid point
TERRAIN_SEARCH_RADIUS = 1    # GaussianGrid.searchRadius == 1 => 3x3 box
TERRAIN_CENTER_TOLERANCE_M = 100.0   # findPointTerrainOptimised: |centerElev-target|<=100 => use center
TERRAIN_DISTANCE_PENALTY_M_PER_KM = 30.0  # "for every 1km in distance, elevation must be 30m better"
TERRAIN_MAX_DISTANCE_KM = 50.0       # neighbor only considered if distanceKm < 50
TERRAIN_MAX_DELTA_FALLBACK_M = 1500.0  # minDelta > 1500 => fall back to center cell
DEG_TO_KM = 111.0                    # distanceKm = sqrt(distanceSquaredDeg) * 111 (open-meteo const)

# Open-Meteo's OWN internal regridded storage (the API serves from here). The static model
# surface elevation HSURF.om is INDEX-COMPATIBLE with the data_spatial temperature_2m flat
# array (both shape (1, 6599680) on the SAME octahedral O1280 indexing). 2.48 MB compressed.
HSURF_S3_URI = "s3://openmeteo/data/ecmwf_ifs/static/HSURF.om"
HSURF_LOCAL_CACHE = "state/static/ecmwf_ifs_o1280_hsurf.om"
# Per-city target elevation cache (the API-reported 90m-DEM elevation, captured once per
# city with provenance). The directive's authority: cities_by_name has no elevation field,
# so the API-reported elevation IS the target-elevation authority.
CITY_ELEVATION_CACHE_PATH = "state/anchor_city_elevation.json"
ELEVATION_API_URL = "https://api.open-meteo.com/v1/forecast"

# Downscaled-class whitelist receipt sub-key. A city is bucket-servable via downscaling when
# it has a VERIFIED receipt keyed ``<cycle>::bucket_downscaled::<city>``.
DOWNSCALED_RECEIPT_TOKEN = "bucket_downscaled"
RUN_AUTHORITY_BUCKET_DOWNSCALED_UNVERIFIED = "bucket_partial_run_downscaled_unverified"


# ---------------------------------------------------------------------------
# Octahedral O1280 grid geometry (pure, cached).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _O1280Grid:
    lats_north_to_south: Any  # numpy float64 (2N,)
    nlon_per_row: Any         # numpy int64 (2N,)
    row_start_offset: Any     # numpy int64 (2N,) flat-array start index of each row


@lru_cache(maxsize=1)
def _o1280_grid() -> _O1280Grid:
    import numpy as np

    n = O1280_N
    # Gaussian latitudes are the latitudes whose sines are the roots of the
    # Legendre polynomial P_{2N}; numpy's leggauss returns those roots in [-1, 1].
    nodes, _weights = np.polynomial.legendre.leggauss(2 * n)
    lats = np.degrees(np.arcsin(nodes))
    lats_ns = lats[np.argsort(-lats)]  # ECMWF GRIB scan order: north -> south

    rows = np.arange(2 * n)
    # distance-from-nearest-pole index j (0-based): north cap rows count up, south cap mirror
    j = np.where(rows < n, rows, (2 * n - 1) - rows)
    nlons = (20 + 4 * j).astype(np.int64)
    if int(nlons.sum()) != O1280_TOTAL_POINTS:
        raise ValueError(
            f"O1280 grid point count mismatch: built {int(nlons.sum())}, "
            f"expected {O1280_TOTAL_POINTS}"
        )
    starts = np.concatenate([[0], np.cumsum(nlons)])[:-1].astype(np.int64)
    return _O1280Grid(lats_north_to_south=lats_ns, nlon_per_row=nlons, row_start_offset=starts)


@dataclass(frozen=True)
class O1280GridPoint:
    flat_index: int
    grid_latitude: float
    grid_longitude_east: float
    row_longitude_count: int
    nearest_distance_km: float


def map_lat_lon_to_o1280_index(latitude: float, longitude: float) -> O1280GridPoint:
    """Nearest-neighbour map of (lat, lon) to the flat O1280 array index.

    Longitudes are stored 0..360 east from 0°E; latitudes scan north→south. The
    nearest-neighbour pick is: nearest Gaussian latitude row, then nearest longitude
    bucket within that row's ``nlon`` evenly-spaced points."""
    import numpy as np

    if not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude out of range")
    if not -180.0 <= longitude <= 360.0:
        raise ValueError("longitude out of range")
    grid = _o1280_grid()
    i = int(np.argmin(np.abs(grid.lats_north_to_south - latitude)))
    nl = int(grid.nlon_per_row[i])
    lon360 = float(longitude) % 360.0
    step = 360.0 / nl
    k = int(round(lon360 / step)) % nl
    flat_index = int(grid.row_start_offset[i]) + k
    grid_lat = float(grid.lats_north_to_south[i])
    grid_lon = k * step
    # great-circle distance city -> grid point (informational provenance only)
    dist_km = _haversine_km(latitude, lon360, grid_lat, grid_lon)
    return O1280GridPoint(
        flat_index=flat_index,
        grid_latitude=grid_lat,
        grid_longitude_east=grid_lon,
        row_longitude_count=nl,
        nearest_distance_km=dist_km,
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(((lon2 - lon1) + 180.0) % 360.0 - 180.0)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------------
# Bucket manifest (in-progress.json / latest.json).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BucketRunManifest:
    reference_time: datetime
    completed: bool
    valid_times: tuple[datetime, ...]
    last_modified_time: datetime | None
    source_key: str  # which json declared this (in-progress or latest)
    raw_variables: tuple[str, ...]

    @property
    def valid_time_set(self) -> frozenset[datetime]:
        return frozenset(self.valid_times)


def _parse_bucket_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_bucket_manifest(raw: Mapping[str, Any], *, source_key: str) -> BucketRunManifest:
    if not isinstance(raw, Mapping):
        raise ValueError("bucket manifest must be a JSON object")
    ref_raw = raw.get("reference_time")
    if not ref_raw:
        raise ValueError("bucket manifest missing reference_time")
    valid_raw = raw.get("valid_times")
    if not isinstance(valid_raw, Sequence) or isinstance(valid_raw, (str, bytes)):
        raise ValueError("bucket manifest valid_times must be a list")
    valid_times = tuple(_parse_bucket_time(str(v)) for v in valid_raw)
    last_mod_raw = raw.get("last_modified_time")
    last_mod = _parse_bucket_time(str(last_mod_raw)) if last_mod_raw else None
    variables = raw.get("variables") or ()
    return BucketRunManifest(
        reference_time=_parse_bucket_time(str(ref_raw)),
        completed=bool(raw.get("completed", False)),
        valid_times=valid_times,
        last_modified_time=last_mod,
        source_key=source_key,
        raw_variables=tuple(str(v) for v in variables),
    )


def fetch_bucket_run_manifest(
    *,
    http_get: Any = None,
    timeout: float = 20.0,
) -> dict[str, BucketRunManifest]:
    """Fetch in-progress.json AND latest.json; return both parsed (keyed by name).

    Either may be missing/transient — a missing one is simply absent from the result."""
    out: dict[str, BucketRunManifest] = {}
    getter = http_get or _default_http_get
    for name, key in (("in_progress", IN_PROGRESS_KEY), ("latest", LATEST_KEY)):
        try:
            raw = getter(f"{BUCKET_HTTP_BASE}/{key}", timeout=timeout)
        except Exception:  # noqa: BLE001 — transient bucket read; caller decides
            continue
        if raw is None:
            continue
        out[name] = parse_bucket_manifest(raw, source_key=key)
    return out


def select_declaring_manifest(
    manifests: Mapping[str, BucketRunManifest],
    *,
    wanted_run: datetime,
) -> BucketRunManifest | None:
    """Return whichever manifest declares EXACTLY ``wanted_run`` (in-progress preferred)."""
    wanted = wanted_run.astimezone(UTC)
    for name in ("in_progress", "latest"):
        manifest = manifests.get(name)
        if manifest is not None and manifest.reference_time == wanted:
            return manifest
    return None


def _default_http_get(url: str, *, timeout: float = 20.0) -> Any:
    import httpx

    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Admission rule (partial-run): every needed hourly timestep must be present.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BucketAdmissionResult:
    admissible: bool
    needed_valid_times: tuple[datetime, ...]
    missing_valid_times: tuple[datetime, ...]
    reason: str


def local_day_hourly_valid_times(
    *,
    run: datetime,
    city_timezone: str,
    target_local_date,
    forecast_hours: int = 120,
) -> tuple[datetime, ...]:
    """The hourly UTC valid_times inside the city's local day for ``target_local_date``.

    Bounded by the run horizon (``run`` .. ``run + forecast_hours``)."""
    zone = ZoneInfo(city_timezone)
    if hasattr(target_local_date, "isoformat") and not isinstance(target_local_date, str):
        local_date = target_local_date
    else:
        from datetime import date as _date

        local_date = _date.fromisoformat(str(target_local_date))
    start_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=zone)
    start_utc = start_local.astimezone(UTC)
    end_utc = (start_local + timedelta(days=1)).astimezone(UTC)
    run_utc = run.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    horizon_end = run_utc + timedelta(hours=forecast_hours)
    needed: list[datetime] = []
    cursor = start_utc
    while cursor < end_utc:
        if run_utc <= cursor <= horizon_end:
            needed.append(cursor)
        cursor += timedelta(hours=1)
    return tuple(needed)


def check_partial_run_admission(
    manifest: BucketRunManifest,
    *,
    wanted_run: datetime,
    needed_valid_times: Sequence[datetime],
) -> BucketAdmissionResult:
    """Admissible iff the manifest declares ``wanted_run`` AND contains every needed step."""
    wanted = wanted_run.astimezone(UTC)
    needed = tuple(v.astimezone(UTC) for v in needed_valid_times)
    if manifest.reference_time != wanted:
        return BucketAdmissionResult(
            admissible=False,
            needed_valid_times=needed,
            missing_valid_times=needed,
            reason=(
                f"bucket declares run {manifest.reference_time.isoformat()} "
                f"!= wanted {wanted.isoformat()}"
            ),
        )
    if not needed:
        return BucketAdmissionResult(
            admissible=False,
            needed_valid_times=needed,
            missing_valid_times=(),
            reason="no needed valid_times inside requested local-day window",
        )
    present = manifest.valid_time_set
    missing = tuple(v for v in needed if v not in present)
    if missing:
        return BucketAdmissionResult(
            admissible=False,
            needed_valid_times=needed,
            missing_valid_times=missing,
            reason=(
                f"{len(missing)} of {len(needed)} needed timesteps not yet written "
                f"(first missing {missing[0].isoformat()})"
            ),
        )
    return BucketAdmissionResult(
        admissible=True,
        needed_valid_times=needed,
        missing_valid_times=(),
        reason="all needed timesteps present in bucket valid_times",
    )


# ---------------------------------------------------------------------------
# Per-timestep om read + payload assembly.
# ---------------------------------------------------------------------------
def _spatial_key_for(run: datetime, valid_time: datetime) -> str:
    run_utc = run.astimezone(UTC)
    vt = valid_time.astimezone(UTC)
    return (
        f"{DATA_SPATIAL_PREFIX}/{run_utc:%Y}/{run_utc:%m}/{run_utc:%d}/"
        f"{run_utc:%H%M}Z/{vt:%Y-%m-%dT%H%M}.om"
    )


def _read_om_point(s3_uri: str, flat_index: int, *, cache_dir: str) -> float:
    """Read a single O1280 grid point of temperature_2m from one spatial om file.

    Uses fsspec blockcache so only the chunks covering ``flat_index`` are downloaded
    (cloud-native partial read), not the whole ~110MB file."""
    import fsspec
    from omfiles import OmFileReader

    backend = fsspec.open(
        f"blockcache::{s3_uri}",
        mode="rb",
        s3={"anon": True, "default_block_size": 65536},
        blockcache={"cache_storage": cache_dir},
    )
    with OmFileReader(backend) as root:
        var = root.get_child_by_name(TEMPERATURE_VARIABLE)
        # var.shape == (1, 6599680); slice the single point on the spatial axis.
        value = var[0:1, flat_index : flat_index + 1]
    import numpy as np

    arr = np.asarray(value).reshape(-1)
    if arr.size != 1:
        raise ValueError(f"expected one point, got {arr.size} from {s3_uri}")
    return float(arr[0])


@dataclass(frozen=True)
class BucketAnchorPayloadResult:
    payload: dict[str, Any]
    provenance: dict[str, Any]


def fetch_bucket_anchor_payload(
    *,
    latitude: float,
    longitude: float,
    run: datetime,
    timezone_name: str,
    needed_valid_times: Sequence[datetime],
    manifest: BucketRunManifest,
    cache_dir: str = "/tmp/zeus_om_bucket_cache",
    read_point: Any = None,
) -> BucketAnchorPayloadResult:
    """Assemble an API-shaped hourly payload from per-timestep bucket om reads.

    PRECONDITION: ``check_partial_run_admission`` returned admissible for the SAME
    ``needed_valid_times`` and ``run``. This function re-verifies admission as a guard
    (never extrapolates / gap-fills); a missing step ⇒ ValueError."""
    admission = check_partial_run_admission(
        manifest, wanted_run=run, needed_valid_times=needed_valid_times
    )
    if not admission.admissible:
        raise ValueError(f"bucket anchor payload refused: {admission.reason}")

    point = map_lat_lon_to_o1280_index(latitude, longitude)
    reader = read_point or (lambda uri, idx: _read_om_point(uri, idx, cache_dir=cache_dir))

    zone = ZoneInfo(timezone_name)
    times_local: list[str] = []
    temps: list[float] = []
    per_step_keys: list[str] = []
    ordered = sorted(admission.needed_valid_times)
    for vt in ordered:
        key = _spatial_key_for(run, vt)
        s3_uri = f"s3://{BUCKET_S3_PREFIX.split('/', 1)[0]}/{key}"
        value = reader(s3_uri, point.flat_index)
        if value is None or not math.isfinite(float(value)):
            raise ValueError(f"non-finite bucket temperature at {vt.isoformat()} ({key})")
        local = vt.astimezone(zone)
        # API time format: local wall-clock without offset, minute resolution.
        times_local.append(local.strftime("%Y-%m-%dT%H:%M"))
        temps.append(round(float(value), 2))
        per_step_keys.append(key)

    sample_local = ordered[0].astimezone(zone)
    utc_offset_seconds = int(sample_local.utcoffset().total_seconds()) if sample_local.utcoffset() else 0

    payload: dict[str, Any] = {
        "latitude": float(point.grid_latitude),
        "longitude": float(point.grid_longitude_east if point.grid_longitude_east <= 180 else point.grid_longitude_east - 360.0),
        "utc_offset_seconds": utc_offset_seconds,
        "timezone": timezone_name,
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {"time": times_local, "temperature_2m": temps},
    }
    provenance: dict[str, Any] = {
        "openmeteo_endpoint": "s3_bucket_data_spatial_partial_run",
        "run_authority": RUN_AUTHORITY_BUCKET_UNVERIFIED,
        "bucket_run_reference_time": manifest.reference_time.isoformat(),
        "bucket_completed_flag": manifest.completed,
        "bucket_valid_times_count_at_read": len(manifest.valid_times),
        "bucket_last_modified_time": (
            manifest.last_modified_time.isoformat() if manifest.last_modified_time else None
        ),
        "bucket_source_manifest_key": manifest.source_key,
        "bucket_needed_valid_times_count": len(ordered),
        "bucket_step_keys": per_step_keys,
        "o1280_flat_index": point.flat_index,
        "o1280_grid_latitude": point.grid_latitude,
        "o1280_grid_longitude_east": point.grid_longitude_east,
        "o1280_nearest_distance_km": round(point.nearest_distance_km, 3),
        "cross_check_status": "PENDING_BUCKET_VS_API_VERIFICATION",
    }
    return BucketAnchorPayloadResult(payload=payload, provenance=provenance)


# ---------------------------------------------------------------------------
# City whitelist gate (antibody): only cities with a VERIFIED bucket↔API cross-check
# may be served by the bucket transport.
# ---------------------------------------------------------------------------
def load_verified_city_whitelist(
    *,
    receipt_path: str = CROSS_CHECK_RECEIPT_PATH,
    tolerance_c: float = CITY_WHITELIST_TOLERANCE_C,
) -> frozenset[str]:
    """Cities with at least one VERIFIED bucket cross-check receipt within tolerance.

    Reads state/anchor_cross_check.json. Bucket receipts are keyed ``<cycle>::bucket`` and
    carry ``verdict``, ``city`` and ``max_abs_delta_c``. A city is whitelisted iff it has a
    receipt with verdict VERIFIED and ``max_abs_delta_c <= tolerance``. Missing / unreadable
    receipts ⇒ EMPTY whitelist (fail-closed: serve nothing via the bucket transport)."""
    from pathlib import Path as _Path

    try:
        receipts = json.loads(_Path(receipt_path).read_text())
    except Exception:  # noqa: BLE001 — missing receipts ⇒ empty whitelist (fail-closed)
        return frozenset()
    verified: set[str] = set()
    if not isinstance(receipts, Mapping):
        return frozenset()
    for key, rec in receipts.items():
        # Bucket receipts are keyed ``<cycle>::bucket`` or ``<cycle>::bucket::<city>``.
        if "::bucket" not in str(key) or not isinstance(rec, Mapping):
            continue
        if rec.get("verdict") != "VERIFIED":
            continue
        city = rec.get("city")
        delta = rec.get("max_abs_delta_c")
        if not city:
            continue
        if delta is None or float(delta) <= float(tolerance_c):
            verified.add(str(city))
    return frozenset(verified)


def city_is_bucket_whitelisted(
    city: str,
    *,
    receipt_path: str = CROSS_CHECK_RECEIPT_PATH,
    tolerance_c: float = CITY_WHITELIST_TOLERANCE_C,
) -> bool:
    return city in load_verified_city_whitelist(
        receipt_path=receipt_path, tolerance_c=tolerance_c
    )


def resolve_bucket_serve_method(
    city: str,
    *,
    receipt_path: str = CROSS_CHECK_RECEIPT_PATH,
    tolerance_c: float = CITY_WHITELIST_TOLERANCE_C,
) -> str | None:
    """How may the bucket transport serve ``city`` — "raw", "downscaled", or None?

    The whitelist now has TWO admission classes, both keyed in state/anchor_cross_check.json:
      * ``<cycle>::bucket::<city>``            — the RAW nearest-O1280-gridpoint read verified
        against the API (flat/inland cities whose nearest cell already is the API value);
      * ``<cycle>::bucket_downscaled::<city>`` — the DOWNSCALED read (terrain-optimised land
        cell + lapse-rate elevation correction) verified against the API (coastal/terrain
        cities whose raw read is biased but whose downscaled read matches).

    A city verified RAW is served raw (cheapest, one read, no static field needed). A city
    verified ONLY via downscaling is served downscaled. A city verified by neither stays
    non-admitted (None) — honest, never weakened. Missing receipts ⇒ None (fail-closed)."""
    from pathlib import Path as _Path

    try:
        receipts = json.loads(_Path(receipt_path).read_text())
    except Exception:  # noqa: BLE001 — missing receipts ⇒ no admission (fail-closed)
        return None
    if not isinstance(receipts, Mapping):
        return None
    raw_ok = False
    downscaled_ok = False
    for key, rec in receipts.items():
        skey = str(key)
        if not isinstance(rec, Mapping) or rec.get("verdict") != "VERIFIED":
            continue
        if rec.get("city") != city:
            continue
        delta = rec.get("max_abs_delta_c")
        if delta is not None and float(delta) > float(tolerance_c):
            continue
        if f"::{DOWNSCALED_RECEIPT_TOKEN}::" in skey:
            downscaled_ok = True
        elif "::bucket::" in skey or skey.endswith("::bucket"):
            raw_ok = True
    if raw_ok:
        return "raw"  # raw read is verified — cheapest path, prefer it
    if downscaled_ok:
        return "downscaled"
    return None


# ---------------------------------------------------------------------------
# DOWNSCALING — Open-Meteo point-query replication (cell_selection=land + lapse-rate
# elevation correction). NEW path; the raw nearest-gridpoint read above is unchanged.
#
# Algorithm provenance (github.com/open-meteo/open-meteo, read 2026-06-11):
#   * grid geometry — Sources/App/Domains/GaussianGrid.swift getCoordinates / findPointXY /
#     getSurroundingGridpoints. Open-Meteo's O1280 uses an EQUIDISTANT latitude approximation
#     dy = 180/(2N+0.5) and lon = x*360/nx(y) — NOT the exact Legendre-root Gaussian latitudes.
#     The HSURF / temperature flat arrays are indexed by THIS scheme, so the downscaled path
#     MUST use it (the raw path's Legendre map_lat_lon_to_o1280_index is a separate, verified
#     approximation that lands on the same nearest cell for inland points but is not used here).
#   * cell selection — Sources/App/Domains/GaussianGrid.swift findPointTerrainOptimised
#     (cell_selection=land default).
#   * elevation correction — Sources/App/Helper/Reader/GenericReader.swift scale().
# ---------------------------------------------------------------------------
def _om_nx_of(y: int) -> int:
    """Longitudes in octahedral row ``y`` (0=north pole row). GaussianGrid.GridType.nxOf."""
    n = O1280_N
    return (20 + y * 4) if y < n else ((2 * n - y - 1) * 4 + 20)


def _om_integral(y: int) -> int:
    """Flat start index of row ``y`` (cumulative point count). GaussianGrid.GridType.integral."""
    n = O1280_N
    total = O1280_TOTAL_POINTS
    if y < n:
        return 2 * y * y + 18 * y
    return total - (2 * (2 * n - y) * (2 * n - y) + 18 * (2 * n - y))


def _om_dy() -> float:
    return 180.0 / (2.0 * O1280_N + 0.5)


@dataclass(frozen=True)
class OmGridPoint:
    """A point on Open-Meteo's equidistant-approx O1280 indexing (downscaled path)."""

    flat_index: int
    grid_latitude: float
    grid_longitude_east: float


def om_get_coordinates(flat_index: int) -> OmGridPoint:
    """Coordinates of a flat O1280 index per Open-Meteo's GaussianGrid.getCoordinates."""
    n = O1280_N
    total = O1280_TOTAL_POINTS
    if not 0 <= flat_index < total:
        raise ValueError("flat_index out of range")
    if flat_index < total // 2:
        y = int((math.sqrt(2 * flat_index + 81) - 9) / 2)
    else:
        y = 2 * n - 1 - int((math.sqrt(2 * (total - flat_index - 1) + 81) - 9) / 2)
    x = flat_index - _om_integral(y)
    nx = _om_nx_of(y)
    dx = 360.0 / nx
    dy = _om_dy()
    lon = x * dx
    lat = (n - y - 1) * dy + dy / 2.0
    return OmGridPoint(
        flat_index=flat_index,
        grid_latitude=lat,
        grid_longitude_east=lon,
    )


def om_get_surrounding_gridpoints(
    latitude: float, longitude: float
) -> tuple[list[int], list[float], int]:
    """3x3 surrounding gridpoints + squared-degree distances + nearest index.

    Verbatim port of GaussianGrid.getSurroundingGridpoints: the box spans rows centerY-1..+1
    (staggered octahedral), each row's nearest x +/-1, wrapping at 0deg longitude."""
    import numpy as np

    n = O1280_N
    dy = _om_dy()
    center_y = max(1, min(2 * n - 2, int(round(n - 1 - ((latitude - dy / 2.0) / dy)))))
    lon360 = float(longitude) % 360.0
    gridpoints: list[int] = []
    distances: list[float] = []
    for j in range(3):
        y = center_y + j - 1
        nx = _om_nx_of(y)
        dx = 360.0 / nx
        x_center = int(round(lon360 / dx))
        point_lat = (n - y - 1) * dy + dy / 2.0
        start = max(0, 1 - x_center)
        for i in range(3):
            i_wrapped = (i + start) % 3
            x = x_center + i_wrapped - 1
            gp = _om_integral(y) + (x + 2 * nx) % nx
            point_lon = x * dx
            dist = (point_lat - latitude) ** 2 + (point_lon - lon360) ** 2
            gridpoints.append(gp)
            distances.append(dist)
    min_idx = int(np.argmin(distances))
    return gridpoints, distances, min_idx


# ---------------------------------------------------------------------------
# Model surface elevation (HSURF.om) — cached local read, index-compatible.
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _hsurf_reader(local_cache: str = HSURF_LOCAL_CACHE):
    """Open the model surface-elevation field once.

    Prefers a one-time local copy under state/static/; if absent, reads (and caches) the
    chunks it needs directly from S3 via fsspec blockcache (anon). The file is 2.48 MB so a
    full local copy is cheap, but the blockcache path keeps this read-only-friendly and never
    forces a download in a read-only deploy."""
    import fsspec
    from omfiles import OmFileReader

    from pathlib import Path as _Path

    local = _Path(local_cache)
    if local.exists():
        return OmFileReader(fsspec.open(str(local), mode="rb"))
    backend = fsspec.open(
        f"blockcache::{HSURF_S3_URI}",
        mode="rb",
        s3={"anon": True, "default_block_size": 65536},
        blockcache={"cache_storage": "/tmp/zeus_om_static_cache"},
    )
    return OmFileReader(backend)


def read_model_elevation(flat_index: int, *, local_cache: str = HSURF_LOCAL_CACHE) -> float:
    """Model surface elevation (HSURF, metres) at a flat O1280 index. ``SEA_SENTINEL_M`` = sea."""
    import numpy as np

    reader = _hsurf_reader(local_cache)
    value = reader[0:1, flat_index : flat_index + 1]
    arr = np.asarray(value).reshape(-1)
    if arr.size != 1:
        raise ValueError(f"expected one HSURF point, got {arr.size}")
    return float(arr[0])


def download_hsurf_static_field(*, local_cache: str = HSURF_LOCAL_CACHE) -> str:
    """One-time copy of HSURF.om to ``state/static/`` (small, 2.48 MB). Returns the path.

    Sanctioned writer: writes a STATIC model field under state/, not a live DB. Idempotent —
    a present local copy is left untouched."""
    from pathlib import Path as _Path

    import fsspec

    dest = _Path(local_cache)
    if dest.exists():
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with fsspec.open(HSURF_S3_URI, mode="rb", s3={"anon": True}) as src, open(dest, "wb") as out:
        out.write(src.read())
    return str(dest)


# ---------------------------------------------------------------------------
# Terrain-optimised land cell selection (cell_selection=land).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TerrainOptimisedPoint:
    flat_index: int
    grid_latitude: float
    grid_longitude_east: float
    model_elevation_m: float
    is_center: bool
    is_sea: bool


def select_terrain_optimised_point(
    latitude: float,
    longitude: float,
    target_elevation_m: float,
    *,
    local_cache: str = HSURF_LOCAL_CACHE,
    read_elevation: Any = None,
) -> TerrainOptimisedPoint:
    """Open-Meteo cell_selection=land cell choice. Verbatim findPointTerrainOptimised.

    1. nearest cell (center). If |centerElev - target| <= 100m -> center (no search).
    2. else over the 3x3 land neighbors, minimise |elev-target| + distanceKm*30, distanceKm<50.
    3. if best is sea / NaN or minDelta > 1500 -> fall back to center cell.
    The returned cell's ``model_elevation_m`` is what the elevation correction uses."""
    elev_reader = read_elevation or (lambda idx: read_model_elevation(idx, local_cache=local_cache))
    gridpoints, distances, center_idx = om_get_surrounding_gridpoints(latitude, longitude)
    center_gp = gridpoints[center_idx]
    center_elev = elev_reader(center_gp)
    center_coord = om_get_coordinates(center_gp)

    if not math.isnan(center_elev) and abs(center_elev - target_elevation_m) <= TERRAIN_CENTER_TOLERANCE_M:
        # CRITICAL (verbatim findPointTerrainOptimised): when |centerElev - target| <= 100m the
        # source returns ``(centerPoint, .elevation(elevation))`` where ``elevation`` is the
        # TARGET parameter, NOT centerElevation. The cell is "close enough" so its effective
        # elevation IS the target → the downstream correction (gridElev - target)*lapse = 0.
        # Returning the cell's true elevation here would over/under-correct every center-branch
        # city (measured: Busan +0.53C, Cape Town +0.19C spurious until this was fixed).
        return TerrainOptimisedPoint(
            flat_index=center_gp,
            grid_latitude=center_coord.grid_latitude,
            grid_longitude_east=center_coord.grid_longitude_east,
            model_elevation_m=target_elevation_m,
            is_center=True,
            is_sea=center_elev <= SEA_SENTINEL_M,
        )

    min_delta = math.inf
    min_pos = -1
    min_elev = math.nan
    for i, gp in enumerate(gridpoints):
        elev = elev_reader(gp)
        if math.isnan(elev) or elev <= SEA_SENTINEL_M:
            continue
        distance_km = math.sqrt(distances[i]) * DEG_TO_KM
        delta = abs(elev - target_elevation_m) + distance_km * TERRAIN_DISTANCE_PENALTY_M_PER_KM
        if delta < min_delta and distance_km < TERRAIN_MAX_DISTANCE_KM:
            min_delta = delta
            min_pos = gp
            min_elev = elev

    if math.isnan(min_elev) or min_delta > TERRAIN_MAX_DELTA_FALLBACK_M:
        # only sea points around, or elevation hugely off -> use the nearest (center) cell.
        min_pos = center_gp
        min_elev = center_elev

    coord = om_get_coordinates(min_pos)
    return TerrainOptimisedPoint(
        flat_index=min_pos,
        grid_latitude=coord.grid_latitude,
        grid_longitude_east=coord.grid_longitude_east,
        model_elevation_m=min_elev,
        is_center=(min_pos == center_gp),
        is_sea=min_elev <= SEA_SENTINEL_M,
    )


def apply_elevation_correction(
    grid_temperature_c: float, *, model_elevation_m: float, target_elevation_m: float
) -> float:
    """Statistical-downscaling temperature correction (GenericReader.swift scale()).

    corrected = grid_T + (modelElevation - targetElevation) * LAPSE_RATE_K_PER_M.
    Higher target than model => cooler (correct sign). No-op when either elevation is NaN or
    the chosen cell is sea (modelElevation <= SEA_SENTINEL_M)."""
    if math.isnan(model_elevation_m) or math.isnan(target_elevation_m):
        return grid_temperature_c
    if model_elevation_m <= SEA_SENTINEL_M:
        return grid_temperature_c
    return grid_temperature_c + (model_elevation_m - target_elevation_m) * LAPSE_RATE_K_PER_M


# ---------------------------------------------------------------------------
# Per-city target elevation cache (the API-reported 90m-DEM elevation).
# ---------------------------------------------------------------------------
def load_city_target_elevation(
    city: str, *, cache_path: str = CITY_ELEVATION_CACHE_PATH
) -> float | None:
    """The cached API-reported target elevation (90m DEM) for ``city``, or None if absent."""
    from pathlib import Path as _Path

    try:
        cache = json.loads(_Path(cache_path).read_text())
    except Exception:  # noqa: BLE001 — missing cache ⇒ not yet captured
        return None
    rec = cache.get(city) if isinstance(cache, Mapping) else None
    if isinstance(rec, Mapping) and rec.get("elevation_m") is not None:
        return float(rec["elevation_m"])
    return None


def capture_city_target_elevation(
    city: str,
    latitude: float,
    longitude: float,
    *,
    cache_path: str = CITY_ELEVATION_CACHE_PATH,
    http_get: Any = None,
) -> float:
    """Fetch + cache the API's 90m-DEM elevation for ``city`` (authority for downscaling target).

    The directive's authority rule: cities_by_name has no elevation field, so the API-reported
    ``elevation`` IS the target-elevation authority. Captured ONCE per city with provenance
    (source URL, captured_at, the grid lat/lon the API reported). Sanctioned writer under
    state/. Returns the elevation; a present cache entry is reused (no re-fetch)."""
    from pathlib import Path as _Path

    cached = load_city_target_elevation(city, cache_path=cache_path)
    if cached is not None:
        return cached
    getter = http_get or _default_http_get
    raw = getter(
        f"{ELEVATION_API_URL}?"
        + "&".join(
            f"{k}={v}"
            for k, v in {
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m",
                "models": "ecmwf_ifs",
                "forecast_hours": 1,
            }.items()
        )
    )
    if not isinstance(raw, Mapping) or raw.get("elevation") is None:
        raise ValueError(f"API did not report an elevation for {city}")
    elevation = float(raw["elevation"])
    dest = _Path(cache_path)
    try:
        cache = json.loads(dest.read_text())
        if not isinstance(cache, dict):
            cache = {}
    except Exception:  # noqa: BLE001 — fresh cache
        cache = {}
    cache[city] = {
        "elevation_m": elevation,
        "source": ELEVATION_API_URL,
        "authority": "openmeteo_90m_dem_api_reported",
        "api_grid_latitude": raw.get("latitude"),
        "api_grid_longitude": raw.get("longitude"),
        "request_latitude": latitude,
        "request_longitude": longitude,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return elevation


# ---------------------------------------------------------------------------
# Downscaled bucket payload assembly (NEW; raw fetch_bucket_anchor_payload unchanged).
# ---------------------------------------------------------------------------
def fetch_bucket_anchor_payload_downscaled(
    *,
    latitude: float,
    longitude: float,
    target_elevation_m: float,
    run: datetime,
    timezone_name: str,
    needed_valid_times: Sequence[datetime],
    manifest: BucketRunManifest,
    cache_dir: str = "/tmp/zeus_om_bucket_cache",
    hsurf_cache: str = HSURF_LOCAL_CACHE,
    read_point: Any = None,
    read_elevation: Any = None,
) -> BucketAnchorPayloadResult:
    """API-shaped payload assembled via Open-Meteo's point downscaling (cell_selection=land).

    Differs from ``fetch_bucket_anchor_payload`` ONLY in the spatial extraction: the
    terrain-optimised land cell is chosen (not the raw nearest), temperature is read at THAT
    cell for every step, and the lapse-rate elevation correction to ``target_elevation_m`` is
    applied. Re-verifies partial-run admission as a guard (never extrapolates / gap-fills)."""
    admission = check_partial_run_admission(
        manifest, wanted_run=run, needed_valid_times=needed_valid_times
    )
    if not admission.admissible:
        raise ValueError(f"bucket downscaled payload refused: {admission.reason}")

    cell = select_terrain_optimised_point(
        latitude, longitude, target_elevation_m,
        local_cache=hsurf_cache, read_elevation=read_elevation,
    )
    reader = read_point or (lambda uri, idx: _read_om_point(uri, idx, cache_dir=cache_dir))
    correction_c = (
        0.0 if cell.is_sea else (cell.model_elevation_m - target_elevation_m) * LAPSE_RATE_K_PER_M
    )

    zone = ZoneInfo(timezone_name)
    times_local: list[str] = []
    temps: list[float] = []
    per_step_keys: list[str] = []
    ordered = sorted(admission.needed_valid_times)
    for vt in ordered:
        key = _spatial_key_for(run, vt)
        s3_uri = f"s3://{BUCKET_S3_PREFIX.split('/', 1)[0]}/{key}"
        raw_value = reader(s3_uri, cell.flat_index)
        if raw_value is None or not math.isfinite(float(raw_value)):
            raise ValueError(f"non-finite bucket temperature at {vt.isoformat()} ({key})")
        corrected = apply_elevation_correction(
            float(raw_value),
            model_elevation_m=cell.model_elevation_m,
            target_elevation_m=target_elevation_m,
        )
        local = vt.astimezone(zone)
        times_local.append(local.strftime("%Y-%m-%dT%H:%M"))
        temps.append(round(corrected, 2))
        per_step_keys.append(key)

    sample_local = ordered[0].astimezone(zone)
    utc_offset_seconds = (
        int(sample_local.utcoffset().total_seconds()) if sample_local.utcoffset() else 0
    )

    payload: dict[str, Any] = {
        "latitude": float(cell.grid_latitude),
        "longitude": float(
            cell.grid_longitude_east
            if cell.grid_longitude_east <= 180
            else cell.grid_longitude_east - 360.0
        ),
        "elevation": float(target_elevation_m),
        "utc_offset_seconds": utc_offset_seconds,
        "timezone": timezone_name,
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {"time": times_local, "temperature_2m": temps},
    }
    provenance: dict[str, Any] = {
        "openmeteo_endpoint": "s3_bucket_data_spatial_partial_run_downscaled",
        "run_authority": RUN_AUTHORITY_BUCKET_DOWNSCALED_UNVERIFIED,
        "downscaling_method": "cell_selection_land_terrain_optimised_plus_lapse_rate",
        "downscaling_algorithm_provenance": (
            "open-meteo GaussianGrid.findPointTerrainOptimised + GenericReader.scale "
            "(github.com/open-meteo/open-meteo, read 2026-06-11)"
        ),
        "lapse_rate_k_per_m": LAPSE_RATE_K_PER_M,
        "target_elevation_m": float(target_elevation_m),
        "model_elevation_m": cell.model_elevation_m,
        "elevation_correction_c": round(correction_c, 4),
        "cell_is_center": cell.is_center,
        "cell_is_sea": cell.is_sea,
        "bucket_run_reference_time": manifest.reference_time.isoformat(),
        "bucket_completed_flag": manifest.completed,
        "bucket_valid_times_count_at_read": len(manifest.valid_times),
        "bucket_last_modified_time": (
            manifest.last_modified_time.isoformat() if manifest.last_modified_time else None
        ),
        "bucket_source_manifest_key": manifest.source_key,
        "bucket_needed_valid_times_count": len(ordered),
        "bucket_step_keys": per_step_keys,
        "o1280_flat_index": cell.flat_index,
        "o1280_grid_latitude": cell.grid_latitude,
        "o1280_grid_longitude_east": cell.grid_longitude_east,
        "cross_check_status": "PENDING_BUCKET_VS_API_VERIFICATION",
    }
    return BucketAnchorPayloadResult(payload=payload, provenance=provenance)
