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
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
    deadline_monotonic: float | None = None,
) -> dict[str, BucketRunManifest]:
    """Fetch in-progress.json AND latest.json; return both parsed (keyed by name).

    Either may be missing/transient — a missing one is simply absent from the result."""
    out: dict[str, BucketRunManifest] = {}
    getter = http_get or _default_http_get
    for name, key in (("in_progress", IN_PROGRESS_KEY), ("latest", LATEST_KEY)):
        _check_deadline(deadline_monotonic)
        request_timeout = timeout
        if deadline_monotonic is not None:
            request_timeout = max(
                0.001,
                min(timeout, deadline_monotonic - time.monotonic()),
            )
        try:
            raw = getter(f"{BUCKET_HTTP_BASE}/{key}", timeout=request_timeout)
        except Exception:  # noqa: BLE001 — transient bucket read; caller decides
            _check_deadline(deadline_monotonic)
            continue
        if raw is None:
            continue
        out[name] = parse_bucket_manifest(raw, source_key=key)
    _check_deadline(deadline_monotonic)
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
        return _read_point_from_om_reader(root, flat_index)


def _read_point_from_om_reader(root: Any, flat_index: int) -> float:
    var = root.get_child_by_name(TEMPERATURE_VARIABLE)
    # var.shape == (1, 6599680); slice the single point on the spatial axis.
    value = var[0:1, flat_index : flat_index + 1]
    import numpy as np

    arr = np.asarray(value).reshape(-1)
    if arr.size != 1:
        raise ValueError(f"expected one point, got {arr.size}")
    return float(arr[0])


class BucketPointReaderPool:
    """Reuse one open OM reader per valid-time object for one download wave."""

    def __init__(
        self,
        *,
        cache_dir: str = "/tmp/zeus_om_bucket_cache",
        open_reader: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._open_reader = open_reader or self._open
        self._readers: dict[str, Any] = {}
        self._values: dict[tuple[str, int], float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _open(s3_uri: str, cache_dir: str) -> Any:
        import fsspec
        from omfiles import OmFileReader

        backend = fsspec.open(
            f"blockcache::{s3_uri}",
            mode="rb",
            s3={"anon": True, "default_block_size": 65536},
            blockcache={"cache_storage": cache_dir},
        )
        return OmFileReader(backend)

    def __enter__(self) -> Callable[[str, int], float]:
        return self.read

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            readers = tuple(self._readers.values())
            self._readers.clear()
            self._values.clear()
        for reader in readers:
            try:
                reader.close()
            except Exception:  # noqa: BLE001 - best-effort handle cleanup
                pass

    def read(self, s3_uri: str, flat_index: int) -> float:
        value_key = (s3_uri, flat_index)
        with self._lock:
            cached = self._values.get(value_key)
            if cached is not None:
                return cached
            reader = self._readers.get(s3_uri)
            if reader is None:
                reader = self._open_reader(s3_uri, self._cache_dir)
                self._readers[s3_uri] = reader
        value = _read_point_from_om_reader(reader, flat_index)
        with self._lock:
            return self._values.setdefault(value_key, value)


def _check_deadline(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TimeoutError("bucket anchor payload deadline expired")


def bucket_payload_geometry(
    grid_latitude: float, grid_longitude_east: float, target_elevation_m: float
) -> dict[str, float]:
    """The response geometry every bucket rung writes, in the API's own terms.

    ``latitude``/``longitude`` name the O1280 cell the temperatures were read from;
    ``elevation`` is the requested point's 90 m DEM height the API reports (the
    downscaling target), never the cell's HSURF: the precision guard proves the
    cell's surface from the static field and demands this field equal the target DEM.
    """
    values = (grid_latitude, grid_longitude_east, target_elevation_m)
    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in values):
        raise ValueError("bucket payload geometry must be finite")
    lon = float(grid_longitude_east)
    return {
        "latitude": float(grid_latitude),
        "longitude": lon if lon <= 180 else lon - 360.0,
        "elevation": float(target_elevation_m),
    }


@dataclass(frozen=True)
class BucketAnchorPayloadResult:
    payload: dict[str, Any]
    provenance: dict[str, Any]


def fetch_bucket_anchor_payload(
    *,
    latitude: float,
    longitude: float,
    target_elevation_m: float,
    run: datetime,
    timezone_name: str,
    needed_valid_times: Sequence[datetime],
    manifest: BucketRunManifest,
    cache_dir: str = "/tmp/zeus_om_bucket_cache",
    read_point: Any = None,
    read_workers: int = 1,
    deadline_monotonic: float | None = None,
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

    _check_deadline(deadline_monotonic)
    point = map_lat_lon_to_o1280_index(latitude, longitude)
    reader = read_point or (lambda uri, idx: _read_om_point(uri, idx, cache_dir=cache_dir))

    zone = ZoneInfo(timezone_name)
    times_local: list[str] = []
    temps: list[float] = []
    per_step_keys: list[str] = []
    ordered = sorted(admission.needed_valid_times)

    def read_step(vt: datetime) -> tuple[datetime, str, float]:
        _check_deadline(deadline_monotonic)
        key = _spatial_key_for(run, vt)
        s3_uri = f"s3://{BUCKET_S3_PREFIX.split('/', 1)[0]}/{key}"
        value = reader(s3_uri, point.flat_index)
        _check_deadline(deadline_monotonic)
        if value is None or not math.isfinite(float(value)):
            raise ValueError(f"non-finite bucket temperature at {vt.isoformat()} ({key})")
        return vt, key, float(value)

    worker_count = min(max(1, int(read_workers)), 8, len(ordered))
    if worker_count == 1:
        step_results = tuple(read_step(vt) for vt in ordered)
    else:
        # Each valid time is a distinct OM object and therefore a distinct pooled reader.
        # Bounded fanout removes the 24-object serial waterfall without permitting partial
        # payloads: executor shutdown joins in-flight reads and assembly begins only after
        # every exact-run timestep succeeds.
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="zeus-bucket-step",
        ) as executor:
            step_results = tuple(executor.map(read_step, ordered))

    for vt, key, value in step_results:
        local = vt.astimezone(zone)
        # API time format: local wall-clock without offset, minute resolution.
        times_local.append(local.strftime("%Y-%m-%dT%H:%M"))
        temps.append(round(value, 2))
        per_step_keys.append(key)

    sample_local = ordered[0].astimezone(zone)
    utc_offset_seconds = int(sample_local.utcoffset().total_seconds()) if sample_local.utcoffset() else 0

    payload: dict[str, Any] = {
        **bucket_payload_geometry(
            point.grid_latitude, point.grid_longitude_east, target_elevation_m
        ),
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
        "target_elevation_m": float(target_elevation_m),
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
        try:
            return OmFileReader(fsspec.open(str(local), mode="rb"))
        except RuntimeError as exc:
            raise ValueError("invalid local HSURF .om") from exc
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


@lru_cache(maxsize=8)
def _static_geometry_frontier_valid(
    path: str, identity: tuple[int, int, int, int, int],
) -> bool:
    """Check both ends of the local surface before metered source acquisition."""
    _hsurf_reader.cache_clear()
    return all(
        math.isfinite(read_model_elevation(index, local_cache=path))
        for index in (0, O1280_TOTAL_POINTS - 1)
    )


def source_geometry_static_prerequisite_reason(
    *, local_cache: str = HSURF_LOCAL_CACHE,
) -> str | None:
    """A missing/broken local HSURF cannot be repaired by re-fetching temperature."""
    from pathlib import Path

    try:
        path = Path(local_cache).resolve(strict=True)
        stat = path.stat()
        if not path.is_file() or stat.st_size <= 0:
            return "OM9_SOURCE_STATIC_HSURF_UNAVAILABLE"
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if not _static_geometry_frontier_valid(str(path), identity):
            return "OM9_SOURCE_STATIC_HSURF_INVALID"
        after = path.stat()
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != identity:
            return "OM9_SOURCE_STATIC_HSURF_CHANGED"
    except FileNotFoundError:
        return "OM9_SOURCE_STATIC_HSURF_UNAVAILABLE"
    except Exception:  # noqa: BLE001 - malformed .om parsers raise RuntimeError
        return "OM9_SOURCE_STATIC_HSURF_INVALID"
    return None


@lru_cache(maxsize=8)
def _static_surface_sha256(path: str, identity: tuple[int, int, int, int, int]) -> str:
    """Bind a geometry certificate to the exact local O1280 surface artifact."""
    import hashlib
    from pathlib import Path

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    # A replaced file at the same path must not be paired with a cached old
    # OmFileReader. The key includes device/inode/size/mtime/ctime, so a
    # replacement or same-size rewrite revalidates rather than using old data.
    _hsurf_reader.cache_clear()
    return digest.hexdigest()


# The API serialises cell coordinates computed in Float32 (GaussianGrid.swift
# getCoordinates); they sit up to 1.8e-5 deg from our float64 recomputation
# (measured on all 51 cities, 2026-09-30) and one float32 ulp at |lon| >= 128 is
# 1.5e-5. Adjacent O1280 cells are >= 0.07 deg apart, so 1e-4 deg names exactly
# one cell: 5x headroom over the rounding, 700x under the spacing.
GRID_CELL_COORDINATE_TOLERANCE_DEG = 1e-4


def same_grid_cell(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> bool:
    """Whether two coordinate pairs name the same O1280 cell (any longitude convention)."""
    values = (lat_a, lon_a, lat_b, lon_b)
    if not all(math.isfinite(float(value)) for value in values):
        return False
    d_lon = (float(lon_a) - float(lon_b) + 180.0) % 360.0 - 180.0
    return (
        abs(float(lat_a) - float(lat_b)) <= GRID_CELL_COORDINATE_TOLERANCE_DEG
        and abs(d_lon) <= GRID_CELL_COORDINATE_TOLERANCE_DEG
    )


def source_cell_geometry_proof(
    *, latitude: float, longitude: float, target_elevation_m: float,
    local_cache: str = HSURF_LOCAL_CACHE,
) -> dict[str, object]:
    """Prove the selected API land cell against the existing static surface.

    Source coordinates and target DEM elevation must be supplied by the actual
    provider response. This does not manufacture a station height or an LSM.
    """
    from pathlib import Path

    surface = Path(local_cache).resolve(strict=True)
    stat = surface.stat()
    if not all(math.isfinite(float(value)) for value in (latitude, longitude, target_elevation_m)):
        raise ValueError("non-finite Open-Meteo geometry")
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    static_sha = _static_surface_sha256(str(surface), identity)
    cell = select_terrain_optimised_point(
        latitude, longitude, target_elevation_m, local_cache=str(surface),
    )
    raw_elevation = read_model_elevation(cell.flat_index, local_cache=str(surface))
    if not math.isfinite(raw_elevation):
        raise ValueError("non-finite O1280 surface elevation")
    nearby, _, _ = om_get_surrounding_gridpoints(latitude, longitude)
    nearby_elevations = [read_model_elevation(index, local_cache=str(surface)) for index in nearby]
    if not all(math.isfinite(value) for value in nearby_elevations):
        raise ValueError("non-finite O1280 neighbor surface elevation")
    after = surface.stat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != identity:
        raise ValueError("O1280 static surface changed during geometry proof")
    return {
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "static_hsurf_sha256": static_sha,
        "selected_flat_index": cell.flat_index,
        "selected_grid_lat": cell.grid_latitude,
        "selected_grid_lon": cell.grid_longitude_east,
        "raw_grid_elevation_m": raw_elevation,
        "effective_grid_elevation_m": cell.model_elevation_m,
        "target_dem_elevation_m": target_elevation_m,
        "cell_is_sea": cell.is_sea,
        "cell_is_center": cell.is_center,
        "nearby_sea": any(value <= SEA_SENTINEL_M for value in nearby_elevations),
    }


def _o1280_snapshot_cell(body: bytes, *, latitude: float, longitude: float,
        target_elevation_m: float) -> dict[str, object]:
    """Decode the same immutable byte snapshot that was hashed, never reopen a path."""
    import fsspec
    from fsspec.implementations.memory import MemoryFileSystem
    from omfiles import OmFileReader
    from uuid import uuid4

    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in
           (latitude, longitude, target_elevation_m)):
        raise ValueError("invalid O1280 source geometry")
    if not -90<=latitude<=90 or not -180<=longitude<=180 or not -500<=target_elevation_m<=9000:
        raise ValueError("invalid O1280 source geometry range")
    memory = MemoryFileSystem(skip_instance_cache=True)
    name = f"/zeus-o1280-snapshot/{uuid4().hex}.om"
    memory.pipe_file(name, body)
    try:
        with OmFileReader(fsspec.core.OpenFile(memory, name, mode="rb")) as reader:
            if reader.shape != (1, O1280_TOTAL_POINTS):
                raise ValueError("O1280 source static shape mismatch")
            def elevation(index):
                value = float(reader[0:1, index:index+1].reshape(-1)[0])
                if not math.isfinite(value):
                    raise ValueError("non-finite O1280 source elevation")
                return value
            cell = select_terrain_optimised_point(latitude, longitude, target_elevation_m,
                read_elevation=elevation)
            raw_elevation=elevation(cell.flat_index)
            if raw_elevation>=9999:
                raise ValueError("O1280 source native elevation unavailable")
            nearby, _, _ = om_get_surrounding_gridpoints(latitude, longitude)
            nearby_sea = [elevation(index)<=SEA_SENTINEL_M for index in nearby]
            facts = {"revision":"openmeteo_ifs9_o1280_source_cell_v1",
                "selected_flat_index":cell.flat_index, "selected_grid_lat":cell.grid_latitude,
                "selected_grid_lon":cell.grid_longitude_east,
                "raw_grid_elevation_m":raw_elevation,
                "effective_grid_elevation_m":cell.model_elevation_m,
                "target_dem_elevation_m":target_elevation_m, "cell_is_sea":cell.is_sea,
                "cell_is_center":cell.is_center, "nearby_sea":any(nearby_sea)}
            # Present only when true, so every land-cell proof keeps its frozen bytes.
            if all(nearby_sea):
                facts["all_sea_neighbourhood"] = True
            return facts
    finally:
        memory.rm_file(name)


def o1280_selected_cell_admissible(cell: Mapping[str, object]) -> bool:
    """A land cell, or the provider's only possible cell when its whole 3x3 is sea.

    findPointTerrainOptimised searches the 3x3 for land and falls back to the
    centre only when none exists, so an all-sea neighbourhood leaves the provider
    one selectable cell: the one it serves. Measured at Seoul RKSI (424 settled
    days), that served sea cell is the most faithful OM9 value (LOW MAE 0.65C,
    HIGH error SD 1.19C vs 1.66C for the nearest land cell 17.6 km away). A sea
    cell chosen while any neighbour is land is a selection we did not reproduce.
    ``cell`` must be facts decoded from the frozen static, never a claim.
    """
    if cell.get("cell_is_sea") is False:
        return True
    return (cell.get("cell_is_sea") is True and cell.get("all_sea_neighbourhood") is True
        and cell.get("cell_is_center") is True)


def _o1280_snapshot_now() -> datetime:
    return datetime.now(timezone.utc)


def _o1280_snapshot_write(path, body: bytes) -> None:
    """Complete a content-addressed regular file before publishing its reference."""
    import os
    import tempfile
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("O1280 immutable source path is a symlink")
    if path.exists():
        if not path.is_file():
            raise ValueError("O1280 immutable source path is not regular")
        if path.read_bytes() == body:
            return
        path.rename(path.with_name(f"{path.name}.damaged.{time.time_ns()}"))
    descriptor, temporary = tempfile.mkstemp(prefix=".o1280-source-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != body:
                raise ValueError("O1280 immutable source race")
    finally:
        Path(temporary).unlink(missing_ok=True)


def _o1280_snapshot_root():
    from src.config import state_path
    return state_path("static")


def _read_o1280_snapshot(audit: Mapping[str, object], *, decision_at: object) -> bytes:
    import hashlib
    from pathlib import Path
    root = _o1280_snapshot_root().resolve()
    path, manifest_path = Path(str(audit["asset_path"])), Path(str(audit["manifest_path"]))
    sha, manifest_sha = str(audit["whole_sha256"]), str(audit["manifest_sha256"])
    if any(len(value)!=64 or any(c not in "0123456789abcdef" for c in value) for value in (sha, manifest_sha)):
        raise ValueError("invalid O1280 immutable source hash")
    if (path.parent.resolve()!=root or manifest_path.parent.resolve()!=root or path.is_symlink() or manifest_path.is_symlink()
        or path.name!=f"ecmwf_ifs_o1280_hsurf.{sha}.om"
        or manifest_path.name!=f"ecmwf_ifs_o1280_hsurf.{sha}.{manifest_sha}.manifest.json"):
        raise ValueError("invalid O1280 immutable source identity")
    if not 0<path.stat().st_size<=16*1024*1024 or not 0<manifest_path.stat().st_size<=64*1024:
        raise ValueError("invalid O1280 immutable source size")
    manifest_body = manifest_path.read_bytes()
    if not 0<len(manifest_body)<=64*1024:
        raise ValueError("invalid O1280 immutable manifest size")
    if hashlib.sha256(manifest_body).hexdigest()!=manifest_sha:
        raise ValueError("invalid O1280 immutable manifest hash")
    manifest = json.loads(manifest_body)
    expected = {key:value for key,value in audit.items() if key not in ("asset_path","manifest_path","manifest_sha256")}
    if (manifest!=expected or manifest["revision"]!="openmeteo_ifs9_o1280_static_snapshot_v1"
        or manifest["model"]!="ecmwf_ifs" or manifest["native_grid"]!="O1280"
        or manifest["point_count"]!=O1280_TOTAL_POINTS
        or manifest["possession_role"]!="local_static_snapshot_not_http_capture"):
        raise ValueError("invalid O1280 immutable manifest contract")
    clocks = [datetime.fromisoformat(str(value).replace("Z","+00:00")) for value in
              (manifest["possessed_at"],manifest["recorded_at"],decision_at)]
    if any(value.tzinfo is None for value in clocks) or not clocks[0]<=clocks[1]<=clocks[2]:
        raise ValueError("O1280 immutable source not possessed at decision")
    body = path.read_bytes()
    if (not 0<len(body)<=16*1024*1024 or isinstance(manifest["byte_size"],bool) or len(body)!=manifest["byte_size"]
        or hashlib.sha256(body).hexdigest()!=sha):
        raise ValueError("invalid O1280 immutable entity body")
    return body


def capture_source_cell_geometry_proof(*, latitude: float, longitude: float,
        target_elevation_m: float, requested_latitude: float, requested_longitude: float,
        local_cache: str | None = None) -> dict[str, object]:
    """Normal producer freezes already-owned O1280 bytes; no HTTP or DB write.

    Its local possession clock is independent of the forecast's original issue
    and capture. A reader never calls this function or backdates a new snapshot.
    Same verified bytes reuse first possession; damaged evidence is retained and
    a new hash-sealed manifest can authorize only a genuinely new decision cut.
    """
    import hashlib
    from pathlib import Path
    original = Path(local_cache or HSURF_LOCAL_CACHE)
    if original.is_symlink():
        raise ValueError("O1280 local static is a symlink")
    if not original.exists():
        raise FileNotFoundError("O1280 local static unavailable")
    if not original.is_file() or not 0<original.stat().st_size<=16*1024*1024:
        raise ValueError("O1280 local static unavailable")
    body = original.read_bytes()
    if not 0<len(body)<=16*1024*1024:
        raise ValueError("O1280 local static size changed")
    geometry = _o1280_snapshot_cell(body, latitude=requested_latitude, longitude=requested_longitude,
        target_elevation_m=target_elevation_m)
    if not same_grid_cell(float(geometry["selected_grid_lat"]),
            float(geometry["selected_grid_lon"]), latitude, longitude):
        raise ValueError("O1280 response cell does not match actual requested terrain selection")
    geometry.update(requested_latitude=requested_latitude,requested_longitude=requested_longitude)
    sha = hashlib.sha256(body).hexdigest()
    root = _o1280_snapshot_root()
    asset_path = root/f"ecmwf_ifs_o1280_hsurf.{sha}.om"
    _o1280_snapshot_write(asset_path, body)
    possession = _o1280_snapshot_now()
    if possession.tzinfo is None:
        raise ValueError("O1280 local possession clock is naive")
    now = possession.astimezone(timezone.utc).isoformat()
    candidates = []
    damaged = []
    for candidate in root.glob(f"ecmwf_ifs_o1280_hsurf.{sha}.*.manifest.json"):
        try:
            encoded = candidate.read_bytes()
            audit = {**json.loads(encoded), "asset_path":str(asset_path), "manifest_path":str(candidate),
                     "manifest_sha256":hashlib.sha256(encoded).hexdigest()}
            if _read_o1280_snapshot(audit, decision_at=now)!=body:
                raise ValueError("O1280 source changed")
            candidates.append(audit)
        except (OSError,ValueError,KeyError,TypeError):
            if candidate.is_symlink():
                raise ValueError("O1280 immutable manifest is a symlink")
            damaged.append(candidate.name)
            candidate.rename(candidate.with_name(f"{candidate.name}.damaged.{time.time_ns()}"))
    if candidates and not damaged:
        audit = max(candidates,key=lambda value:str(value["recorded_at"]))
    else:
        manifest = {"revision":"openmeteo_ifs9_o1280_static_snapshot_v1", "model":"ecmwf_ifs",
            "native_grid":"O1280", "point_count":O1280_TOTAL_POINTS, "whole_sha256":sha,
            "byte_size":len(body), "possession_role":"local_static_snapshot_not_http_capture",
            "possessed_at":now, "recorded_at":now}
        if damaged:
            manifest["recovery_of"] = sorted(damaged)
        encoded = json.dumps(manifest,sort_keys=True,separators=(",",":")).encode()
        manifest_sha = hashlib.sha256(encoded).hexdigest()
        manifest_path = root/f"ecmwf_ifs_o1280_hsurf.{sha}.{manifest_sha}.manifest.json"
        _o1280_snapshot_write(manifest_path,encoded)
        audit = {**manifest,"asset_path":str(asset_path),"manifest_path":str(manifest_path),"manifest_sha256":manifest_sha}
    return {**geometry,"static_hsurf_sha256":sha,"static_asset_audit":audit}


def validate_source_cell_geometry_proof(proof: Mapping[str,object], *, latitude: float,
        longitude: float, target_elevation_m: float, requested_latitude: float,
        requested_longitude: float, decision_at: object) -> str | None:
    """Replay this frozen O1280 source's own bytes and complete cell facts, read-only."""
    try:
        body = _read_o1280_snapshot(proof["static_asset_audit"],decision_at=decision_at)
        if proof["static_hsurf_sha256"]!=proof["static_asset_audit"]["whole_sha256"]:
            return "OM9_FROZEN_SOURCE_STATIC_IDENTITY_MISMATCH"
        actual = _o1280_snapshot_cell(body,latitude=requested_latitude,longitude=requested_longitude,target_elevation_m=target_elevation_m)
        actual.update(requested_latitude=requested_latitude,requested_longitude=requested_longitude)
        # all_sea_neighbourhood is derived from these same replayed bytes. A proof
        # frozen before the producer recorded it claims nothing; only a claim that
        # contradicts the replay is a mismatch. Admission reads the replay, never the claim.
        if "all_sea_neighbourhood" in proof and proof["all_sea_neighbourhood"] is not actual.get("all_sea_neighbourhood", False):
            return "OM9_FROZEN_SOURCE_CELL_MISMATCH"
        for key,value in actual.items():
            if key == "all_sea_neighbourhood":
                continue
            claimed = proof.get(key)
            if isinstance(value,bool):
                if not isinstance(claimed,bool) or claimed!=value:
                    return "OM9_FROZEN_SOURCE_CELL_MISMATCH"
            elif isinstance(value,(int,float)):
                if (isinstance(claimed,bool) or not isinstance(claimed,(int,float))
                    or (isinstance(value,int) and not isinstance(claimed,int))
                    or not math.isfinite(float(claimed)) or claimed!=value):
                    return "OM9_FROZEN_SOURCE_CELL_MISMATCH"
            elif claimed!=value:
                return "OM9_FROZEN_SOURCE_CELL_MISMATCH"
        if not o1280_selected_cell_admissible(actual) or not same_grid_cell(float(actual["selected_grid_lat"]),
                float(actual["selected_grid_lon"]), latitude, longitude):
            return "OM9_FROZEN_SOURCE_SELECTED_CELL_MISMATCH"
        return None
    except (KeyError,IndexError,TypeError,ValueError,OSError,RuntimeError):
        return "OM9_FROZEN_SOURCE_STATIC_UNAVAILABLE"


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
    city: str, latitude: float, longitude: float, *,
    cache_path: str = CITY_ELEVATION_CACHE_PATH,
) -> float | None:
    """The cached API-reported 90m-DEM elevation for ``city`` at exactly this request point.

    The DEM is a property of the requested coordinates, not the city name: a record
    captured for other coordinates is a miss, never a stand-in."""
    from pathlib import Path as _Path

    try:
        cache = json.loads(_Path(cache_path).read_text())
    except Exception:  # noqa: BLE001 — missing cache ⇒ not yet captured
        return None
    rec = cache.get(city) if isinstance(cache, Mapping) else None
    if (
        isinstance(rec, Mapping)
        and rec.get("elevation_m") is not None
        and rec.get("request_latitude") == float(latitude)
        and rec.get("request_longitude") == float(longitude)
    ):
        return float(rec["elevation_m"])
    return None


def record_city_target_elevation(
    city: str, latitude: float, longitude: float, response: object, *,
    cache_path: str = CITY_ELEVATION_CACHE_PATH,
) -> float:
    """Cache the ``elevation`` an Open-Meteo forecast response reported for this request point.

    Any API answer for these exact coordinates carries the provider's own 90m-DEM height,
    so the API rungs keep the cache current at no extra request; the bucket rung reads it
    when the API is unreachable. Unchanged values are not rewritten."""
    import os
    import tempfile
    from pathlib import Path as _Path

    if not isinstance(response, Mapping) or response.get("elevation") is None:
        raise ValueError(f"API did not report an elevation for {city}")
    elevation = float(response["elevation"])
    if not math.isfinite(elevation):
        raise ValueError(f"API reported a non-finite elevation for {city}")
    if load_city_target_elevation(city, latitude, longitude, cache_path=cache_path) == elevation:
        return elevation
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
        "api_grid_latitude": response.get("latitude"),
        "api_grid_longitude": response.get("longitude"),
        "request_latitude": float(latitude),
        "request_longitude": float(longitude),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json.dumps(cache, indent=1, sort_keys=True))
        os.replace(temporary, dest)
    finally:
        _Path(temporary).unlink(missing_ok=True)
    return elevation


def capture_city_target_elevation(
    city: str,
    latitude: float,
    longitude: float,
    *,
    cache_path: str = CITY_ELEVATION_CACHE_PATH,
    http_get: Any = None,
    timeout: float = 20.0,
) -> float:
    """Fetch + cache the API's 90m-DEM elevation for this request point.

    The directive's authority rule: cities_by_name has no elevation field, so the API-reported
    ``elevation`` IS the target-elevation authority. Captured once per (city, request point)
    with provenance; a coordinate change misses and recaptures, superseding the record.
    Sanctioned writer under state/. Returns the elevation."""
    cached = load_city_target_elevation(city, latitude, longitude, cache_path=cache_path)
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
        ),
        timeout=timeout,
    )
    return record_city_target_elevation(
        city, latitude, longitude, raw, cache_path=cache_path
    )


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
    deadline_monotonic: float | None = None,
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

    _check_deadline(deadline_monotonic)
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
        _check_deadline(deadline_monotonic)
        key = _spatial_key_for(run, vt)
        s3_uri = f"s3://{BUCKET_S3_PREFIX.split('/', 1)[0]}/{key}"
        raw_value = reader(s3_uri, cell.flat_index)
        _check_deadline(deadline_monotonic)
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
        **bucket_payload_geometry(
            cell.grid_latitude, cell.grid_longitude_east, target_elevation_m
        ),
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
