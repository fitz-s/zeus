# Created: prior; restructured 2026-05-01
# Last reused or audited: 2026-05-15
# Authority basis: architect D1 (ECMWF throttle), AGENTS.md money path
#   Prior: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md
#   ECMWF Open Data has ~6-8h latency (vs. TIGGE's 48h public embargo) so it
#   is the live-trading source for same-day forecasts. Rows must land in
#   ensemble_snapshots with the canonical local-calendar-day data_version
#   so calibration / day0 / opening_hunt readers can consume them alongside
#   TIGGE archive rows via the data_version priority list.
"""Collect ECMWF Open Data ENS member vectors into ensemble_snapshots.

Replaces the legacy 2t-instantaneous + ensemble_snapshots (v1) write path.

Pipeline
--------
1. Download single GRIB containing all 51 members × 71 step hours for the
   requested run (mx2t6 OR mn2t6 per call) via in-process parallel SDK
   fetches at per-step file granularity (``_fetch_one_step`` +
   ``ThreadPoolExecutor(max_workers=5)``), concatenated on success.
   Refactored 2026-05-11 per PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md.
2. Run ``51 source data/scripts/extract_open_ens_localday.py`` to produce
   per-(city, target_local_date, lead_day) JSON records that conform to the
   TiggeSnapshotPayload contract.
3. Reuse the zeus repo's ``scripts/ingest_grib_to_snapshots.ingest_track``
   ingester (importable) which validates against the canonical contract,
   asserts the dataset_id is allow-listed, and writes the row to
   ``ensemble_snapshots`` with manifest_hash + provenance_json + members_unit.

Data version
------------
HIGH: ``ecmwf_opendata_mx2t6_local_calendar_day_max_v1``
LOW : ``ecmwf_opendata_mn2t6_local_calendar_day_min_v1``

Note on params (2026-05-07)
---------------------------
ECMWF Open Data ``enfo`` stream deprecated ``mx2t6``/``mn2t6`` (6h aggregations).
Fetch now uses ``mx2t3``/``mn2t3`` (3h native) per authority doc
``architecture/zeus_grid_resolution_authority_2026_05_07.yaml`` A1+3h.
Step list is 3h-stride (3, 6, 9 … 240). Data versions are unchanged;
calibration learns the 3h→6h envelope mapping downstream.

These data_versions are added to ``CANONICAL_ENSEMBLE_DATA_VERSIONS`` in
``src/contracts/ensemble_snapshot_provenance.py``. The TIGGE archive
``tigge_*_v1`` data_versions remain valid alongside.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import hashlib
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import requests

from src.config import PROJECT_ROOT, runtime_cities_by_name
from src.contracts.availability_time import proof_of_possession_available_at
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
)
from src.data.forecast_target_contract import (
    OPENDATA_MAX_STEP_HOURS,
    build_forecast_target_scope,
    evaluate_horizon_coverage,
    evaluate_producer_coverage,
)
from src.data.forecast_extrema_authority import POSITIVE_ATTRIBUTION_STATUSES
from src.data.producer_readiness import build_producer_readiness_for_scope
from src.data.forecast_source_registry import gate_source, gate_source_role
from src.data.release_calendar import FetchDecision, get_entry, select_source_run_for_target_horizon
from src.state.db import (
    ZEUS_FORECASTS_DB_PATH,
    assert_schema_current_forecasts,
    get_forecasts_connection as get_connection,
)
from src.state.db_writer_lock import WriteClass, db_writer_lock
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT / "51 source data"
# DOWNLOAD_SCRIPT deleted 2026-05-11: replaced by in-process parallel SDK fetch
# (see PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md)
EXTRACT_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "extract_open_ens_localday.py"
EXTRACT_MANIFEST_PATH = FIFTY_ONE_ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
INGEST_SCRIPT_DIR = PROJECT_ROOT / "scripts"

# ECMWF hang antibody #1 (2026-05-13) — eager-import ingest_grib_to_snapshots
# at module load so the first ``collect_open_ens_cycle`` call cannot block
# on first-time module init while holding the forecasts.db BULK writer-lock.
# Witnessed 2026-05-12 13:31 PDT: daemon held BULK flock for 12h with WAL=0
# bytes (no SQL write ever opened) — see /tmp/zeus_ecmwf_critic_review.md.
if str(INGEST_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(INGEST_SCRIPT_DIR))
import ingest_grib_to_snapshots as _ingest_grib_module  # type: ignore  # noqa: E402
from ingest_grib_to_snapshots import (  # type: ignore  # noqa: E402
    SourceRunContext as _ingest_grib_SourceRunContext,
    ingest_track as _ingest_grib_ingest_track,
)

# ECMWF Open Data ENS dissemination grid (enfo cf/pf, mx2t3/mn2t3/2t):
#   0–144h by 3h, then 150–360h by 6h.
# Note: the underlying IFS model produces hourly steps 0–90h and 3h steps
#       93–144h (per https://www.ecmwf.int/en/forecasts/datasets/set-iii),
#       but Open Data subsamples to the 3h/6h grid above. Hourly steps are
#       only available via MARS, which Zeus does not use.
# Period-aligned params: mx2t3/mn2t3 valid at every disseminated step;
#       mx2t6/mn2t6 (deprecated 2026-05-07) were valid only at 6h multiples.
# We request 3h-native steps through OPENDATA_MAX_STEP_HOURS (144h) and NO further.
#
# 5-day cap (2026-05-29): Polymarket retired all weather markets beyond 5 days.
# D+5 plus the largest trading-city UTC offset lands at ≤144h, so steps 150-282
# are never traded. The previous 282h tail (fix/#134, LOW D+10 authority) is
# RETIRED — fetching it wasted bandwidth and left a fail-closed >144h coverage
# path that no live market exercised. STEP_HOURS is now DERIVED from the cap
# constant so the tail is unconstructable: re-adding it would break the coupling
# antibody in tests/test_ecmwf_open_data_step_hours.py.
#
# Authority: src/data/forecast_target_contract.OPENDATA_MAX_STEP_HOURS (=144),
#            architecture/zeus_grid_resolution_authority_2026_05_07.yaml A1+3h (stride).
# ECMWF Open Data `enfo` stream serves mx2t3/mn2t3 (3h aggregations) at 3h stride
# through 144h. We fetch 3h-native and let calibration learn the 3h→6h envelope
# downstream. We do NOT re-aggregate to 6h at fetch time (forbidden_patterns).
STEP_HOURS = list(range(3, OPENDATA_MAX_STEP_HOURS + 3, 3))  # 3, 6, …, 144 (A1+3h native grid)

# Track config — local to this module so the daemon's ingest knob is one
# clean dict rather than two parallel param lists.
TRACKS: dict[str, dict] = {
    "mx2t6_high": {
        "open_data_param": "mx2t3",   # was mx2t6; deprecated — API returns ValueError
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        "ingest_track": "mx2t6_high",
        "extract_subdir": "open_ens_mx2t6_localday_max",
    },
    "mn2t6_low": {
        "open_data_param": "mn2t3",   # was mn2t6; deprecated — API returns ValueError
        "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
        "ingest_track": "mn2t6_low",
        "extract_subdir": "open_ens_mn2t6_localday_min",
    },
}

SOURCE_ID = "ecmwf_open_data"
FORECAST_SOURCE_ROLE = "entry_primary"
MODEL_VERSION = "ecmwf_open_data"

# ECMWF Open Data is replicated across multiple mirrors. AWS is fastest but
# returns S3 SlowDown when byte-range requests burst. Google rejects multi-range
# GETs, but supports the single-range GETs emitted by Zeus' controlled downloader
# below. The ECMWF origin does not reliably serve byte ranges, so it is opt-in.
_DOWNLOAD_SOURCES: tuple[str, ...] = tuple(
    source.strip()
    for source in os.environ.get("ZEUS_ECMWF_SOURCES", "aws,google").split(",")
    if source.strip()
)

# ---------------------------------------------------------------------------
# Token-bucket rate limiter (D1 throttle antibody — 2026-05-12)
# ---------------------------------------------------------------------------
# AWS S3 / ECMWF multiurl returns HTTP 503 Slow Down when request burst rate
# exceeds provider limits. The token bucket is a module-level singleton shared
# across ALL worker threads and BOTH tracks (mx2t6_high + mn2t6_low run at
# minute=30 and minute=35 respectively via ingest_main.py, so up to
# 2 × _DOWNLOAD_MAX_WORKERS fetches can be in flight simultaneously).
#
# The token bucket caps sustained throughput at ZEUS_ECMWF_RPS regardless of
# worker count. ZEUS_ECMWF_BURST bounds the startup burst independently, while
# 429/503 responses reduce the live rate and successful responses recover it.

class _TokenBucket:
    """Adaptive token-bucket rate limiter (thread-safe).

    Fills at ``rate`` tokens/sec; each ``acquire()`` consumes one token,
    sleeping until a token is available.  Implemented as a leaky-bucket
    gate (refill on demand) rather than a background thread so there is no
    daemon thread to manage across fork/test boundaries.
    """

    def __init__(self, rate: float, *, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity is not None and capacity <= 0:
            raise ValueError("capacity must be positive")
        self._max_rate = float(rate)
        self._min_rate = min(1.0, self._max_rate)
        self._rate = self._max_rate
        self._capacity = max(1.0, float(capacity if capacity is not None else rate))
        self._lock = threading.Lock()
        self._tokens = self._capacity
        self._last_refill: float = time.monotonic()

    def _refill_locked(self, now: float) -> None:
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        while True:
            with self._lock:
                self._refill_locked(time.monotonic())
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)

    def observe(self, status_code: int) -> None:
        """Apply AIMD feedback from a completed provider request."""
        with self._lock:
            self._refill_locked(time.monotonic())
            if status_code in {429, 503}:
                self._rate = max(self._min_rate, self._rate * 0.5)
            elif status_code < 500 and self._rate < self._max_rate:
                self._rate = min(
                    self._max_rate,
                    self._rate + 1.0 / max(self._rate, 1.0),
                )


_DOWNLOAD_RPS: float = float(os.environ.get("ZEUS_ECMWF_RPS", "4.0"))
_DOWNLOAD_BURST: float = float(
    os.environ.get("ZEUS_ECMWF_BURST", str(min(8.0, _DOWNLOAD_RPS)))
)
_fetch_bucket: _TokenBucket = _TokenBucket(_DOWNLOAD_RPS, capacity=_DOWNLOAD_BURST)

# ---------------------------------------------------------------------------
# Parallel-fetch constants (antibody-style: no call-site kwargs).
# Single-writer antibody: SQLite writes are PROHIBITED inside worker threads —
# HTTP fetch only; all DB writes happen on the main thread after futures complete.
# ---------------------------------------------------------------------------
# Env override: ZEUS_ECMWF_MAX_WORKERS (operator-set; survives linter audits).
_DOWNLOAD_MAX_WORKERS: int = int(os.environ.get("ZEUS_ECMWF_MAX_WORKERS", "2"))
_PER_STEP_TIMEOUT_SECONDS: int = int(os.environ.get("ZEUS_ECMWF_STEP_TIMEOUT_SECONDS", "180"))
_PER_STEP_MAX_RETRIES: int = int(os.environ.get("ZEUS_ECMWF_PER_STEP_RETRIES", "2"))
_PER_STEP_RETRY_AFTER: int = int(os.environ.get("ZEUS_ECMWF_PER_STEP_RETRY_AFTER", "5"))
# 404 → NOT_RELEASED (no retry); all others below trigger retry then failover.
_RETRYABLE_HTTP: frozenset[int] = frozenset({500, 502, 503, 504, 408, 429})


def _is_ecmwf_download_url(url: str) -> bool:
    return (
        "ecmwf-forecasts" in url
        or "ecmwf-open-data" in url
        or "data.ecmwf.int/forecasts" in url
    )


class _RateLimitedSession(requests.Session):
    """requests.Session that rate-limits ECMWF download HEAD/GET calls."""

    def request(self, method: str, url: str, *args, **kwargs):  # type: ignore[override]
        limited = method.upper() in {"GET", "HEAD"} and _is_ecmwf_download_url(str(url))
        if limited:
            _fetch_bucket.acquire()
        response = super().request(method, url, *args, **kwargs)
        if limited:
            _fetch_bucket.observe(response.status_code)
        return response


def _part_offset_length(part: Any) -> tuple[int, int]:
    """Return an ECMWF index part as ``(offset, length)``.

    ecmwf-opendata currently returns plain tuples; multiurl wraps them as part
    objects internally. Supporting both keeps this helper stable across package
    updates without delegating download control back to multiurl.
    """

    if hasattr(part, "offset") and hasattr(part, "length"):
        return int(part.offset), int(part.length)
    offset, length = part
    return int(offset), int(length)


def _http_error_for_response(response: requests.Response, message: str) -> requests.HTTPError:
    err = requests.HTTPError(message)
    err.response = response
    return err


def _resolve_index_parts(client: Any, result: Any) -> list[tuple[str, tuple[tuple[int, int], ...]]]:
    """Resolve ECMWF ``.index`` parts without multiurl's 120-second retry loop."""

    for_index = getattr(result, "for_index", {}) or {}
    if not for_index:
        return []

    verify = getattr(client, "verify", True)
    resolved: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    for url in result.urls:
        base, _ = os.path.splitext(str(url))
        index_url = f"{base}.index"
        response = client.session.get(
            index_url,
            stream=True,
            timeout=_PER_STEP_TIMEOUT_SECONDS,
            verify=verify,
        )
        try:
            if response.status_code != 200:
                response.raise_for_status()
            parts: list[tuple[int, int]] = []
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = json.loads(raw_line)
                if all(line.get(name) in values for name, values in for_index.items()):
                    parts.append((int(line["_offset"]), int(line["_length"])))
            if parts:
                resolved.append((str(url), tuple(sorted(parts))))
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    if not resolved:
        raise ValueError(f"Cannot find index entries matching {for_index!r}")
    return resolved


def _retrieve_step_with_controlled_ranges(client: Any, *, target: Path, **kwargs: Any) -> Any:
    """Retrieve one indexed OpenData step with Zeus-owned single Range GETs.

    ecmwf-opendata's default ``Client.retrieve`` delegates indexed GRIB assembly
    to multiurl. In live forecast runs multiurl was the broken boundary: it can
    emit bursty multi-range/internal-retry traffic outside Zeus' token bucket,
    producing AWS S3 SlowDown / HTTP 429 followed by 120-second sleeps. This
    downloader keeps index resolution in ecmwf-opendata but owns every HTTP GET.
    Outer retry/failover remains in ``_fetch_one_step``.
    """

    if not hasattr(client, "_get_urls"):
        _fetch_bucket.acquire()
        return client.retrieve(target=str(target), **kwargs)

    client.session = _RateLimitedSession()
    result = client._get_urls(target=str(target), use_index=False, **kwargs)
    indexed_parts = _resolve_index_parts(client, result)
    if indexed_parts:
        result.urls = indexed_parts

    target_path = Path(result.target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    verify = getattr(client, "verify", True)
    session = client.session

    with target_path.open("wb") as out:
        for item in result.urls:
            if isinstance(item, tuple) and len(item) == 2 and not isinstance(item[1], (str, bytes)):
                url, parts = item
                for part in parts:
                    offset, length = _part_offset_length(part)
                    end = offset + length - 1
                    response = session.get(
                        url,
                        stream=True,
                        headers={"Range": f"bytes={offset}-{end}"},
                        timeout=_PER_STEP_TIMEOUT_SECONDS,
                        verify=verify,
                    )
                    try:
                        if response.status_code != 206:
                            if response.status_code >= 400:
                                response.raise_for_status()
                            raise _http_error_for_response(
                                response,
                                f"Expected HTTP 206 for range GET, got {response.status_code}",
                            )
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                out.write(chunk)
                                bytes_written += len(chunk)
                    finally:
                        close = getattr(response, "close", None)
                        if callable(close):
                            close()
            else:
                response = session.get(
                    item,
                    stream=True,
                    timeout=_PER_STEP_TIMEOUT_SECONDS,
                    verify=verify,
                )
                try:
                    if response.status_code != 200:
                        response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            out.write(chunk)
                            bytes_written += len(chunk)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()

    result.size = bytes_written
    return result


def _conda_python() -> str:
    """Path to the Python interpreter with ecmwf.opendata + eccodes installed.

    Resolution order:
      1. ZEUS_ECMWF_PYTHON env var (explicit deployment config)
      2. /Users/leofitz/miniconda3/bin/python (dev-machine fallback if it exists)
      3. sys.executable (test environments that already carry ecmwf deps)
    """
    import os as _os
    from_env = _os.environ.get("ZEUS_ECMWF_PYTHON")
    if from_env:
        return from_env
    candidate = Path("/Users/leofitz/miniconda3/bin/python")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _step_hours_signature() -> str:
    """Compact filename-safe signature of STEP_HOURS.

    Encodes range + count + sha8 to stay under NAME_MAX (255 bytes on macOS,
    HFS+/APFS) regardless of grid size. Joining all 70+ steps with '-'
    produced a ~280-byte filename and crashed the download with OSError 63
    "File name too long" at write time — every Open Data fetch from 2026-05-08
    onward failed at byte 0 because of this. The signature stays stable per
    STEP_HOURS configuration so cached files are reusable across restarts.
    """
    import hashlib
    sig = ",".join(str(value) for value in STEP_HOURS)
    digest = hashlib.sha256(sig.encode()).hexdigest()[:8]
    return f"{min(STEP_HOURS)}to{max(STEP_HOURS)}_n{len(STEP_HOURS)}_h{digest}"


def _download_output_path(*, run_date: date, run_hour: int, param: str) -> Path:
    steps_sig = _step_hours_signature()
    return (
        FIFTY_ONE_ROOT
        / "raw"
        / "ecmwf_open_ens"
        / "ecmwf"
        / run_date.strftime("%Y%m%d")
        / f"open_ens_{run_date.strftime('%Y%m%d')}_{run_hour:02d}z_steps_{steps_sig}_params_{param}.grib2"
    )


def _step_cache_path(output_dir: Path, *, run_date: date, run_hour: int, step: int, param: str) -> Path:
    """Per-step cache for the full executable member set.

    Older ``.stepNNN_<param>.grib2`` cache files can contain only 50
    perturbed ``enfo/ef`` members when ECMWF omits ``cf`` from that file. The
    explicit suffix prevents resume from reusing those pf-only artifacts after
    the control/oper merge fix.

    The cycle identity is part of the cache key. Reusing 00Z step bytes for a
    later 12Z source_run would preserve file bytes while corrupting forecast
    provenance.
    """

    return output_dir / f".{run_date.strftime('%Y%m%d')}_{run_hour:02d}z_step{step:03d}_{param}_ens51.grib2"


def _cycle_extract_dir_name(*, run_date: date, run_hour: int) -> str:
    base = run_date.strftime("%Y%m%d")
    if run_hour == 0:
        return base
    return f"{base}_cycle{run_hour:02d}z"


def _build_cycle_scoped_json_root(
    *,
    raw_root: Path,
    extract_subdir: str,
    run_date: date,
    run_hour: int,
    tmp_root: Path,
) -> tuple[Path, str, int]:
    """Build an ingest view containing only the selected source cycle's JSON."""

    cycle_dir_name = _cycle_extract_dir_name(run_date=run_date, run_hour=run_hour)
    source_subdir = raw_root / extract_subdir
    view_subdir = tmp_root / extract_subdir
    view_subdir.mkdir(parents=True, exist_ok=True)
    if not source_subdir.exists():
        return tmp_root, cycle_dir_name, 0

    linked = 0
    for city_dir in source_subdir.iterdir():
        if not city_dir.is_dir():
            continue
        source_cycle_dir = city_dir / cycle_dir_name
        if not source_cycle_dir.is_dir():
            continue
        view_cycle_dir = view_subdir / city_dir.name / cycle_dir_name
        view_cycle_dir.mkdir(parents=True, exist_ok=True)
        for source_json in sorted(source_cycle_dir.glob("*.json")):
            target_json = view_cycle_dir / source_json.name
            try:
                target_json.symlink_to(source_json.resolve())
            except OSError:
                shutil.copy2(source_json, target_json)
            linked += 1
    return tmp_root, cycle_dir_name, linked


def _select_cycle_for_track(*, track: str, now_utc: datetime) -> tuple[FetchDecision, dict[str, object]]:
    """Select a release-calendar-approved source run for the configured horizon."""
    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    return select_source_run_for_target_horizon(
        now_utc=now_utc,
        source_id=SOURCE_ID,
        track=track,
        required_max_step_hours=max(STEP_HOURS),
    )


def _status_for_ingest_summary(summary: dict) -> str:
    written = int(summary.get("written", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)
    if written == 0 and skipped == 0:
        return "empty_ingest"
    return "ok"


def _stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _source_cycle_expires_at(source_cycle_time: datetime, forecast_track: str) -> datetime:
    """Coverage/readiness expiry ANCHORED TO THE CYCLE, never to the write wall-clock (M3 fix).

    The prior ``computed_at + 24h`` was a GUESS: re-stamping computed_at on a re-ingest granted a
    fresh 24h TTL and the expiry clock disagreed with the source's real staleness law (the same
    twin-clock disease ``replacement_readiness_expires_at`` killed for the replacement path). The
    lawful lifetime of a forecast cycle's data is ``max_source_lag_seconds`` after the CYCLE time —
    the calendar's own publication tolerance — not after we happened to write the row. That bound
    is the single source of truth in the release calendar, keyed by the source's ingest track
    (``TRACKS`` keys ARE the calendar track keys); we read it and anchor expiry to the CYCLE.

    ``forecast_track`` is the horizon-suffixed label (e.g. "mx2t6_high_full_horizon"); the calendar
    is keyed by the base ingest track ("mx2t6_high"). A missing calendar entry is a real config
    error on a Tier-0 money path and raises (fail-loud) rather than substituting a guessed lag.
    """
    cycle = source_cycle_time if source_cycle_time.tzinfo else source_cycle_time.replace(tzinfo=timezone.utc)
    cycle = cycle.astimezone(timezone.utc)
    base_track = next(
        (t for t in TRACKS if forecast_track == t or forecast_track.startswith(f"{t}_")),
        None,
    )
    entry = get_entry(SOURCE_ID, base_track) if base_track is not None else None
    if entry is None:
        raise ValueError(
            f"release calendar has no entry for {SOURCE_ID!r} track derived from "
            f"{forecast_track!r}; cannot derive a cycle-anchored expiry (refusing a guessed TTL)"
        )
    return cycle + timedelta(seconds=int(entry.max_source_lag_seconds))


def _horizon_profile_for_cycle(
    *,
    cycle_hour: int,
    selection_metadata: dict[str, object],
    manual_cycle_override: bool,
) -> str:
    if not manual_cycle_override:
        profile = selection_metadata.get("horizon_profile")
        if isinstance(profile, str) and profile:
            return profile
    if cycle_hour in (0, 12):
        return "full"
    if cycle_hour in (6, 18):
        return "short"
    return "manual"


def _forecast_track_for_profile(*, ingest_track: str, horizon_profile: str) -> str:
    if horizon_profile in {"full", "short"}:
        return f"{ingest_track}_{horizon_profile}_horizon"
    return f"{ingest_track}_{horizon_profile}"


def _source_run_outcome(summary: dict, status: str) -> tuple[str, str, bool, str | None]:
    written = int(summary.get("written", 0) or 0)
    errors = int(summary.get("errors", 0) or 0)
    if status == "ok" and written > 0 and errors == 0:
        return "SUCCESS", "COMPLETE", False, None
    if written > 0:
        return "PARTIAL", "PARTIAL", True, status.upper()
    return "FAILED", "MISSING", False, status.upper()


def _json_list(value: object) -> list[Any]:
    if not isinstance(value, str) or not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def _usable_member_count(value: object) -> int:
    """Count finite member values, not placeholder slots."""

    count = 0
    for item in _json_list(value):
        if isinstance(item, dict):
            item = item.get("value_native_unit")
        if item is None or isinstance(item, bool):
            continue
        try:
            numeric = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            count += 1
    return count


def _effective_expected_members(row: Mapping[str, Any], *, full_ensemble: int = 51) -> int:
    """51 minus lawfully boundary-quarantined members, for a minority-ambiguous row.

    LOW-track members individually nulled by the boundary-quarantine rule
    (extract_open_ens_localday.py:574-580) are a lawful exclusion, not a missing
    observation, once the snapshot-level majority rule
    (ambiguous_member_count < majority threshold) has already decided the day is
    usable -- ``row['boundary_ambiguous']`` is 0 in that case. Those quarantined
    members must not count against the 51-member floor, or a lawful minority
    exclusion reads identically to a genuine ingest gap
    (MISSING_EXPECTED_MEMBERS). A majority-ambiguous row (``boundary_ambiguous``
    == 1) keeps the full expectation: it is embargoed regardless
    (contributes_to_target_extrema=0), so any member shortfall must still
    surface undiminished.
    """
    if int(row.get("boundary_ambiguous") or 0):
        return full_ensemble
    return max(0, full_ensemble - int(row.get("ambiguous_member_count") or 0))


def _run_level_observed_members(
    row: Mapping[str, Any],
    *,
    full_ensemble: int = 51,
) -> int:
    """Normalize lawful member exclusions onto the source-run's fixed scale."""

    usable = _usable_member_count(row.get("members_json"))
    lawful_exclusions = full_ensemble - _effective_expected_members(
        row,
        full_ensemble=full_ensemble,
    )
    return min(full_ensemble, usable + lawful_exclusions)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _snapshot_coordinate_manifest_sha(row: dict[str, Any]) -> str:
    raw = row.get("provenance_json")
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        provenance = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(provenance, dict):
        return ""
    return str(provenance.get("manifest_sha256") or "").strip()


def _snapshot_provenance(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("provenance_json")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        provenance = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return provenance if isinstance(provenance, dict) else {}


def _is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _station_grid_provenance_reason(row: dict[str, Any]) -> str | None:
    provenance = _snapshot_provenance(row)
    contract = provenance.get("contract_outcome_evidence")
    if not isinstance(contract, dict):
        contract = {}
    source_type = str(
        row.get("settlement_source_type")
        or contract.get("settlement_source_type")
        or ""
    ).strip().lower()
    if source_type != "wu_icao":
        return None
    required = (
        provenance.get("nearest_grid_lat"),
        provenance.get("nearest_grid_lon"),
        provenance.get("nearest_grid_distance_km"),
    )
    if not all(_is_finite_number(value) for value in required):
        return "EXECUTABLE_FORECAST_STATION_GRID_PROVENANCE_MISSING"
    return None


def _snapshot_rows_for_source_run(conn, *, source_run_id: str, data_version: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM ensemble_snapshots
            WHERE source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
              AND dataset_id = ?
            ORDER BY city, target_date, temperature_metric, snapshot_id
            """,
            (SOURCE_ID, "ensemble_snapshots_db_reader", source_run_id, data_version),
        ).fetchall()
    ]


def _clear_source_run_authority(conn, *, source_run_id: str) -> dict[str, int]:
    """Clear small derived rows before rebuilding a source_run.

    Snapshot rows are intentionally not pre-deleted. The ingester uses the
    canonical unique key with ``INSERT OR REPLACE``, so pre-deleting the same
    run turns a small deterministic overwrite into a slow table/index rewrite
    on the multi-GB forecasts DB. Residual rows that were not replaced by the
    new JSON set are removed after ingest by fetch_time.
    """

    coverage_ids = [
        str(row[0])
        for row in conn.execute(
            "SELECT coverage_id FROM source_run_coverage WHERE source_run_id = ?",
            (source_run_id,),
        ).fetchall()
    ]
    readiness_deleted = conn.execute(
        """
        DELETE FROM readiness_state
        WHERE strategy_key = 'producer_readiness'
          AND source_run_id = ?
        """,
        (source_run_id,),
    ).rowcount
    if coverage_ids:
        readiness_ids = [f"producer_readiness:{coverage_id}" for coverage_id in coverage_ids]
        for readiness_id in readiness_ids:
            readiness_deleted += conn.execute(
                """
            DELETE FROM readiness_state
            WHERE strategy_key = 'producer_readiness'
              AND readiness_id = ?
                """,
                (readiness_id,),
            ).rowcount

    coverage_deleted = conn.execute(
        "DELETE FROM source_run_coverage WHERE source_run_id = ?",
        (source_run_id,),
    ).rowcount
    source_run_deleted = conn.execute(
        "DELETE FROM source_run WHERE source_run_id = ?",
        (source_run_id,),
    ).rowcount
    return {
        "snapshots_deleted": 0,
        "coverage_deleted": int(coverage_deleted),
        "producer_readiness_deleted": int(readiness_deleted),
        "source_run_deleted": int(source_run_deleted),
    }


def _delete_stale_source_run_snapshots(
    conn,
    *,
    source_run_id: str,
    replace_started_at_iso: str,
) -> int:
    """Delete same-run snapshots not refreshed by this ingest attempt."""

    stale_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM ensemble_snapshots
            WHERE source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
              AND fetch_time < ?
            """,
            (
                SOURCE_ID,
                "ensemble_snapshots_db_reader",
                source_run_id,
                replace_started_at_iso,
            ),
        ).fetchone()[0]
    )
    if stale_count <= 0:
        return 0
    deleted = conn.execute(
        """
        DELETE FROM ensemble_snapshots
        WHERE source_id = ?
          AND source_transport = ?
          AND source_run_id = ?
          AND fetch_time < ?
        """,
        (
            SOURCE_ID,
            "ensemble_snapshots_db_reader",
            source_run_id,
            replace_started_at_iso,
        ),
    ).rowcount
    return int(deleted)


def _observed_steps_for_snapshot(
    *,
    required_steps: tuple[int, ...],
    step_horizon_hours: object,
    downloaded_steps: list[int] | None = None,
) -> tuple[int, ...]:
    try:
        horizon = float(step_horizon_hours)
    except (TypeError, ValueError):
        return ()
    horizon_steps = tuple(step for step in required_steps if step <= horizon)
    if downloaded_steps is None:
        return horizon_steps
    downloaded = {int(step) for step in downloaded_steps}
    return tuple(step for step in horizon_steps if step in downloaded)


def _coverage_reason(reason_codes: list[str]) -> str:
    preferred = (
        "MISSING_EXPECTED_MEMBERS",
        "MISSING_REQUIRED_STEPS",
        "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA",
        "EXECUTABLE_FORECAST_STATION_GRID_PROVENANCE_MISSING",
        "SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH",
        "SOURCE_RUN_PARTIAL",
    )
    for reason in preferred:
        if reason in reason_codes:
            return reason
    return next(
        (reason for reason in reason_codes if reason != "FUTURE_TARGET_DATE_COVERED"),
        "FUTURE_TARGET_DATE_COVERAGE_PARTIAL",
    )


def _write_source_authority_chain(
    conn,
    *,
    summary: dict,
    status: str,
    source_run_id: str,
    source_cycle_time: datetime,
    source_release_time: datetime,
    release_calendar_key: str,
    forecast_track: str,
    data_version: str,
    computed_at: datetime,
    fetch_started_at: datetime | None = None,
    fetch_finished_at: datetime | None = None,
    captured_at: datetime | None = None,
    download_observed_steps: list[int] | None = None,
    download_partial_run: bool | None = None,
    download_reason_code: str | None = None,
) -> dict[str, int | str | None]:
    """Write source_run + coverage rows for a completed ingest cycle.

    download_observed_steps: when provided (PARTIAL cycles), records the
    source_run-level ground-truth step list from the download phase. Per-target
    coverage still derives observed_steps from each snapshot's local-day
    horizon so the readiness row describes that exact target window.
    """
    rows = _snapshot_rows_for_source_run(
        conn,
        source_run_id=source_run_id,
        data_version=data_version,
    )
    source_run_status, source_run_completeness, partial_run, reason_code = _source_run_outcome(summary, status)
    snapshot_coordinate_manifest_shas = {
        manifest_sha
        for row in rows
        if (manifest_sha := _snapshot_coordinate_manifest_sha(row))
    }
    source_run_manifest_sha = (
        next(iter(snapshot_coordinate_manifest_shas))
        if len(snapshot_coordinate_manifest_shas) == 1
        else None
    )
    if rows and source_run_manifest_sha is None:
        source_run_status = "FAILED"
        source_run_completeness = "MISSING"
        partial_run = False
        reason_code = (
            "SNAPSHOT_COORDINATE_MANIFEST_SHA_MISSING"
            if not snapshot_coordinate_manifest_shas
            else "SNAPSHOT_COORDINATE_MANIFEST_SHA_MISMATCH"
        )
    # source_run.observed_members is the run-level "did we ingest the ensemble"
    # signal that gates the decision certificate (compiler
    # _validate_forecast_authority_payload reads source_run_completeness_status).
    # It MUST aggregate over only the snapshots that contribute to a target
    # extrema window. Boundary-ambiguous / far-horizon-overflow snapshots are
    # written as all-null placeholders (contributes_to_target_extrema=0,
    # forecast_window_attribution_status=AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY);
    # including them in a min() over ALL rows let a single unfillable Southern-
    # hemisphere / D+5 window zero out observed_members for the entire global
    # run, vetoing every per-city certificate even though the contributing
    # windows each carried the full 51-member ensemble. Per-target member
    # adequacy is still enforced per-scope below (observed_members_for_scope)
    # and again by the executable forecast reader's member floor.
    contributing_rows = [
        row for row in rows if int(row.get("contributes_to_target_extrema") or 0) == 1
    ]
    member_count_rows = contributing_rows if contributing_rows else rows
    observed_member_counts = [
        _run_level_observed_members(row) for row in member_count_rows
    ]
    observed_members = min(observed_member_counts) if observed_member_counts else 0
    if rows and observed_members < 51 and source_run_status == "SUCCESS":
        source_run_status = "PARTIAL"
        source_run_completeness = "PARTIAL"
        partial_run = True
        reason_code = "MISSING_EXPECTED_MEMBERS"
    # PR 6: capture member timing chain fields for DecisionSourceContext.
    # min/max use default="" so empty filtered generators never raise ValueError
    # (all rows could legitimately have NULL source_available_at on degraded ingest).
    _avail_times = [str(row["source_available_at"]) for row in rows if row.get("source_available_at")]
    first_member_observed_time_iso: str = min(_avail_times, default="")
    observed_step_horizons = [
        float(row["step_horizon_hours"])
        for row in rows
        if row.get("step_horizon_hours") is not None
    ]
    # download_observed_steps (from parallel fetch) takes precedence over the
    # ingest-derived approximation: ingest computes steps from step_horizon_hours
    # (a per-row high-water mark), which can overstate when far-horizon steps
    # are absent. The download phase knows exactly which steps were fetched.
    if download_observed_steps is not None:
        observed_steps = list(download_observed_steps)
        if download_partial_run is not None:
            partial_run = partial_run or download_partial_run
            if download_partial_run:
                source_run_completeness = "PARTIAL"
                source_run_status = "PARTIAL"
        if download_reason_code is not None:
            reason_code = download_reason_code
    else:
        observed_steps = [step for step in STEP_HOURS if observed_step_horizons and step <= min(observed_step_horizons)]
    run_complete_time_iso: str = "" if partial_run else max(_avail_times, default="")

    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id=SOURCE_ID,
        track=forecast_track,
        release_calendar_key=release_calendar_key,
        source_cycle_time=source_cycle_time,
        source_issue_time=source_cycle_time,
        source_release_time=source_release_time,
        # C1-AVAIL-CLOCK (2026-06-16): source_available_at is PROOF OF POSSESSION = the real
        # authority-write wall-clock (computed_at), routed through the canonical antibody producer.
        # It is NOT source_release_time, which falls back to the raw model cycle (~8h early) and is
        # the safe-fetch GATE, not a publish event — never credited as a nominal availability.
        source_available_at=proof_of_possession_available_at(computed_at),
        # M5-COLLECTION-CLOCK (2026-06-16): each collection-plane instant is the REAL wall-clock at
        # its own code point in collect_open_ens_cycle, NOT computed_at (the model run-init, which
        # fabricated collection latency=0). fetch_started/finished are stamped around the parallel
        # download loop; captured is snapshot_possession_at (taken right before the snapshot write);
        # imported is now() at this persist write. None where the caller could not observe the event.
        fetch_started_at=fetch_started_at,
        fetch_finished_at=fetch_finished_at,
        captured_at=captured_at,
        imported_at=datetime.now(timezone.utc),
        valid_time_start=min((str(row["target_date"]) for row in rows), default=None),
        valid_time_end=max((str(row["target_date"]) for row in rows), default=None),
        data_version=data_version,
        expected_members=51,
        observed_members=observed_members,
        expected_steps_json=STEP_HOURS,
        observed_steps_json=observed_steps,
        expected_count=len(rows),
        observed_count=len(rows),
        completeness_status=source_run_completeness,
        partial_run=partial_run,
        manifest_hash=source_run_manifest_sha,
        status=source_run_status,
        reason_code=reason_code,
    )

    cities_by_name = runtime_cities_by_name()
    coverage_written = 0
    readiness_written = 0
    # M3 (2026-06-16): expiry anchors to the CYCLE + the calendar's max source lag, never to the
    # write wall-clock. The old computed_at+24h was a guess that re-stamped a fresh TTL on every
    # re-ingest and disagreed with the source's real staleness bound. See _source_cycle_expires_at.
    expires_at = _source_cycle_expires_at(source_cycle_time, forecast_track)
    for row in rows:
        city = cities_by_name.get(str(row["city"]))
        if city is None:
            logger.warning("ecmwf_open_data authority chain: city not configured: %s", row["city"])
            continue
        target_local_date = date.fromisoformat(str(row["target_date"]))
        scope = build_forecast_target_scope(
            city_id=city.name.upper().replace(" ", "_"),
            city_name=city.name,
            city_timezone=city.timezone,
            target_local_date=target_local_date,
            temperature_metric=str(row["temperature_metric"]),
            source_cycle_time=source_cycle_time,
            data_version=data_version,
        )
        observed_steps_for_scope = _observed_steps_for_snapshot(
            required_steps=scope.required_step_hours,
            step_horizon_hours=row.get("step_horizon_hours"),
            downloaded_steps=download_observed_steps,
        )
        observed_members_for_scope = _usable_member_count(row.get("members_json"))
        expected_members_for_scope = _effective_expected_members(row)
        horizon_decision = evaluate_horizon_coverage(
            required_steps=scope.required_step_hours,
            live_max_step_hours=int(float(row.get("step_horizon_hours") or 0)),
        )
        coverage_decision = evaluate_producer_coverage(
            city_id=scope.city_id,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            source_id=SOURCE_ID,
            source_transport="ensemble_snapshots_db_reader",
            source_run_status=source_run_status,
            source_run_completeness=source_run_completeness,
            snapshot_target_date=target_local_date,
            snapshot_metric=str(row["temperature_metric"]),
            expected_steps=scope.required_step_hours,
            observed_steps=observed_steps_for_scope,
            expected_members=expected_members_for_scope,
            observed_members=observed_members_for_scope,
            has_source_linkage=all(
                row.get(field)
                for field in (
                    "source_id",
                    "source_transport",
                    "source_run_id",
                    "release_calendar_key",
                    "source_cycle_time",
                    "source_release_time",
                    "source_available_at",
                )
            ),
        )
        reason_codes = list(
            horizon_decision.reason_codes
            if horizon_decision.status != "LIVE_ELIGIBLE"
            else coverage_decision.reason_codes
        )
        snapshot_window_start = _parse_utc(row.get("local_day_start_utc"))
        if snapshot_window_start != scope.target_window_start_utc:
            reason_codes.append("SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH")
        contributes_to_target_extrema = int(row.get("contributes_to_target_extrema") or 0) == 1
        attribution_status = str(row.get("forecast_window_attribution_status") or "")
        positive_attribution = attribution_status in POSITIVE_ATTRIBUTION_STATUSES
        if not (contributes_to_target_extrema and positive_attribution):
            reason_codes.append("EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA")
        grid_reason = _station_grid_provenance_reason(row)
        if grid_reason is not None:
            reason_codes.append(grid_reason)
        live_eligible = (
            source_run_status in {"SUCCESS", "PARTIAL"}
            and source_run_completeness in {"COMPLETE", "PARTIAL"}
            and horizon_decision.status == "LIVE_ELIGIBLE"
            and coverage_decision.status == "LIVE_ELIGIBLE"
            and snapshot_window_start == scope.target_window_start_utc
            and contributes_to_target_extrema
            and positive_attribution
            and grid_reason is None
        )
        if live_eligible:
            completeness_status = "COMPLETE"
            readiness_status = "LIVE_ELIGIBLE"
            coverage_reason = None
        elif "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in reason_codes:
            completeness_status = "HORIZON_OUT_OF_RANGE"
            readiness_status = "BLOCKED"
            coverage_reason = "SOURCE_RUN_HORIZON_OUT_OF_RANGE"
        else:
            completeness_status = "PARTIAL"
            readiness_status = "BLOCKED"
            coverage_reason = _coverage_reason(reason_codes)

        coverage_id = _stable_id(
            "source_run_coverage",
            source_run_id,
            forecast_track,
            scope.city_id,
            scope.city_timezone,
            scope.target_local_date.isoformat(),
            scope.temperature_metric,
            data_version,
        )
        write_source_run_coverage(
            conn,
            coverage_id=coverage_id,
            source_run_id=source_run_id,
            source_id=SOURCE_ID,
            source_transport="ensemble_snapshots_db_reader",
            release_calendar_key=release_calendar_key,
            track=forecast_track,
            city_id=scope.city_id,
            city=scope.city_name,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            physical_quantity=str(row["physical_quantity"]),
            observation_field=str(row["observation_field"]),
            data_version=data_version,
            expected_members=expected_members_for_scope,
            observed_members=observed_members_for_scope,
            expected_steps_json=scope.required_step_hours,
            observed_steps_json=observed_steps_for_scope,
            snapshot_ids_json=[int(row["snapshot_id"])],
            target_window_start_utc=scope.target_window_start_utc,
            target_window_end_utc=scope.target_window_end_utc,
            completeness_status=completeness_status,
            readiness_status=readiness_status,
            reason_code=coverage_reason,
            computed_at=computed_at,
            expires_at=expires_at if readiness_status == "LIVE_ELIGIBLE" else None,
        )
        coverage_written += 1
        build_producer_readiness_for_scope(
            conn,
            scope=scope,
            source_id=SOURCE_ID,
            source_transport="ensemble_snapshots_db_reader",
            track=forecast_track,
            computed_at=computed_at,
            release_calendar_key=release_calendar_key,
        )
        readiness_written += 1

    return {
        "source_run_status": source_run_status,
        "source_run_completeness": source_run_completeness,
        "coverage_written": coverage_written,
        "producer_readiness_written": readiness_written,
        "first_member_observed_time": first_member_observed_time_iso,
        "run_complete_time": run_complete_time_iso,
    }


def _fetch_one_step(
    *,
    cycle_date: date,
    cycle_hour: int,
    param: str,
    step: int,
    output_dir: Path,
    mirrors: tuple[str, ...],
) -> tuple[str, Any]:
    """Fetch a single step for one param into a per-step canonical file.

    Returns (status, detail) where status is one of:
      "OK"           — file written and atomic-renamed; detail = Path
      "NOT_RELEASED" — 404 on all mirrors; detail = None
      "FAILED"       — retry budget exhausted; detail = error string

    Per-step file naming uses param to avoid cross-track collision when
    mx2t6_high (param=mx2t3) and mn2t6_low (param=mn2t3) run concurrently
    (src/ingest_main.py:1133-1142, minute=30 vs minute=35; worst-case
    2 × _DOWNLOAD_MAX_WORKERS in flight on the same output_dir).

    Single-writer antibody: NO SQLite writes in this function — HTTP only.
    All DB writes occur on the main thread after all futures complete.
    """
    canonical = _step_cache_path(
        output_dir,
        run_date=cycle_date,
        run_hour=cycle_hour,
        step=step,
        param=param,
    )
    partial   = canonical.with_suffix(".grib2.partial")
    if canonical.exists() and canonical.stat().st_size > 0:
        return ("OK", canonical)   # resume: already fetched in a prior attempt

    from ecmwf.opendata import Client  # imported here: conda env only on main interpreter

    last_err: str | None = None
    for mirror in mirrors:
        for attempt in range(_PER_STEP_MAX_RETRIES):
            try:
                client = Client(source=mirror)
                pf_partial = partial.with_suffix(".pf.partial")
                cf_partial = partial.with_suffix(".cf.partial")
                _retrieve_step_with_controlled_ranges(
                    client,
                    date=int(cycle_date.strftime("%Y%m%d")),
                    time=cycle_hour,
                    stream="enfo",
                    type=["pf"],
                    step=[step],
                    param=[param],
                    target=pf_partial,
                )
                try:
                    _retrieve_step_with_controlled_ranges(
                        client,
                        date=int(cycle_date.strftime("%Y%m%d")),
                        time=cycle_hour,
                        stream="enfo",
                        type=["cf"],
                        step=[step],
                        param=[param],
                        target=cf_partial,
                    )
                except ValueError as exc:
                    if "Cannot find index entries matching" not in str(exc):
                        raise
                    _retrieve_step_with_controlled_ranges(
                        client,
                        date=int(cycle_date.strftime("%Y%m%d")),
                        time=cycle_hour,
                        stream="oper",
                        type=["fc"],
                        step=[step],
                        param=[param],
                        target=cf_partial,
                    )
                with partial.open("wb") as out:
                    out.write(cf_partial.read_bytes())
                    out.write(pf_partial.read_bytes())
                os.replace(str(partial), str(canonical))   # atomic rename
                pf_partial.unlink(missing_ok=True)
                cf_partial.unlink(missing_ok=True)
                return ("OK", canonical)
            except requests.HTTPError as exc:
                code = getattr(exc.response, "status_code", None)
                if code == 404:
                    # 404 means upstream has not published this step yet.
                    # All mirrors sync within ~5 s of origin, so rotating
                    # mirrors won't help — return immediately.
                    return ("NOT_RELEASED", None)
                if code in _RETRYABLE_HTTP:
                    last_err = f"HTTP_{code}_mirror_{mirror}_attempt_{attempt}"
                    if attempt + 1 < _PER_STEP_MAX_RETRIES:
                        time.sleep(_PER_STEP_RETRY_AFTER)
                    continue
                last_err = f"HTTP_{code}_mirror_{mirror}"
                break   # non-retryable; try next mirror
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = f"NET_{type(exc).__name__}_mirror_{mirror}_attempt_{attempt}"
                if attempt + 1 < _PER_STEP_MAX_RETRIES:
                    time.sleep(_PER_STEP_RETRY_AFTER)
                continue
            except OSError as exc:
                # disk/path errors during atomic rename or partial-file write
                last_err = f"OS_{type(exc).__name__}_mirror_{mirror}"
                break   # unexpected at filesystem layer; try next mirror
            except ValueError as exc:
                # SDK raises ValueError("Cannot find index entries matching ...")
                # when the requested step is absent from the .index file
                # (step not yet published). All mirrors sync from the same
                # index — rotating won't help. PLAN v3 §5.1 expected HTTP 404
                # here, but multiurl resolves the index BEFORE the byte-range
                # GET, so a missing step manifests as ValueError, not HTTPError.
                if "Cannot find index entries matching" in str(exc):
                    return ("NOT_RELEASED", None)
                raise   # Unknown ValueError — propagate
            # ImportError, AttributeError, TypeError, etc. propagate to the
            # ThreadPoolExecutor future; main thread surfaces them in logs.
            # Antibody 2026-05-11: silent-swallow of ModuleNotFoundError caused
            # post-deploy 23ms-fast-fail with no traceback.
    return ("FAILED", last_err or "EXHAUSTED")


def _concat_steps(
    ok_steps: list[int],
    param: str,
    output_dir: Path,
    output_path: Path,
    *,
    run_date: date,
    run_hour: int,
) -> None:
    """Concatenate per-step GRIB2 files into the canonical output_path.

    GRIB2 is self-delimiting; step order does not affect extractor correctness
    (REL-1, REL-6). We write in ascending step order for determinism.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as out:
        for step in sorted(ok_steps):
            step_file = _step_cache_path(
                output_dir,
                run_date=run_date,
                run_hour=run_hour,
                step=step,
                param=param,
            )
            if step_file.exists():
                out.write(step_file.read_bytes())


def _subprocess_env_with_script_dir(args: list[str]) -> dict:
    """Child env that injects the launched .py script's own directory FIRST on
    PYTHONPATH, so sibling-module imports resolve even when the parent process
    sets PYTHONSAFEPATH=1 (Python 3.11+ then suppresses the default script-dir
    injection into sys.path[0]).

    Antibody (2026-06-22): the forecast-live launchd plist sets PYTHONSAFEPATH=1,
    which broke `from tigge_local_calendar_day_common import ...` inside the
    extract subprocess → ecmwf extraction rc=1 → fusion capture failed → 12h of
    zero posteriors → stale belief → blind exits. See
    tests/test_ecmwf_open_data_subprocess_hardening.py.
    """
    import os as _os

    env = dict(_os.environ)
    script = next(
        (a for a in args[1:] if isinstance(a, str) and a.endswith(".py")), None
    )
    if script:
        script_dir = _os.path.dirname(_os.path.abspath(script))
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            script_dir + (_os.pathsep + existing if existing else "")
        )
    return env


def _run_subprocess(args: list[str], *, label: str, timeout: int) -> dict:
    logger.info("ecmwf_open_data %s: %s", label, " ".join(args[:6]) + " ...")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_subprocess_env_with_script_dir(args),
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("ecmwf_open_data %s: TIMEOUT after %ds", label, timeout)
        partial_stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return {"label": label, "ok": False, "error": f"timeout after {timeout}s",
                "stderr_tail": partial_stderr[-4096:]}
    except FileNotFoundError as exc:
        return {"label": label, "ok": False, "error": f"script not found: {exc}",
                "stderr_tail": ""}
    stderr_full = result.stderr or ""
    if result.returncode != 0:
        logger.warning("ecmwf_open_data %s: rc=%d stderr_tail=%s",
                       label, result.returncode, stderr_full[-4096:])
    return {
        "label": label,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-4096:],
        "stderr_tail": stderr_full[-4096:],
    }


def _write_stderr_dump(dump_path: Path, stderr: str) -> None:
    """Write stderr tail (up to 4096 chars) to a postmortem file under tmp/. Silently no-ops on error."""
    try:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(stderr, encoding="utf-8")
        logger.info("ecmwf_open_data: stderr dump written to %s", dump_path)
    except OSError as exc:
        logger.warning("ecmwf_open_data: could not write stderr dump to %s: %s", dump_path, exc)


def collect_open_ens_cycle(
    *,
    track: str = "mx2t6_high",
    run_date: Optional[date] = None,
    run_hour: Optional[int] = None,
    download_timeout_seconds: int = 1500,  # kept for API compat; parallel fetch uses _PER_STEP_TIMEOUT_SECONDS
    extract_timeout_seconds: int = 900,
    skip_download: bool = False,
    skip_extract: bool = False,
    conn=None,
    _runner=None,
    _fetch_impl=None,  # test seam: replaces _fetch_one_step; callable with same signature
    now_utc: datetime | None = None,
) -> dict:
    """Download + extract + ingest one Open Data ENS run for one track.

    Parameters
    ----------
    track : "mx2t6_high" | "mn2t6_low"
        Which physical-quantity track to fetch. The daemon calls this twice
        per cycle (once per track) so each track has independent failure
        semantics.
    run_date / run_hour :
        Optional override of the auto-selected run. Used for boot-time
        catch-up.
    skip_download / skip_extract :
        Test seams. The daemon never sets these.
    conn :
        Optional pre-opened world DB connection. Tests pass an in-memory
        sqlite connection.
    _runner :
        Test seam to swap subprocess execution.
    """
    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    cfg = TRACKS[track]
    runner = _runner or _run_subprocess

    source_spec = gate_source(SOURCE_ID)
    gate_source_role(source_spec, FORECAST_SOURCE_ROLE)

    now = now_utc or datetime.now(timezone.utc)
    manual_cycle_override = run_date is not None or run_hour is not None
    selection_metadata: dict[str, object] = {}
    if run_date is None or run_hour is None:
        selection, selection_metadata = _select_cycle_for_track(track=track, now_utc=now)
        if selection is not FetchDecision.FETCH_ALLOWED:
            return {
                "status": selection.value.lower(),
                "track": track,
                "data_version": cfg["data_version"],
                "source_id": SOURCE_ID,
                "forecast_source_role": FORECAST_SOURCE_ROLE,
                "selection": selection_metadata,
                "stages": [],
                "snapshots_inserted": 0,
            }
        selected_cycle = selection_metadata["selected_cycle_time"]
        if not isinstance(selected_cycle, datetime):
            raise TypeError("release calendar selected_cycle_time must be datetime")
        cycle_date, cycle_hour = selected_cycle.date(), selected_cycle.hour
    else:
        cycle_date, cycle_hour = run_date, run_hour
    if run_date is not None:
        cycle_date = run_date
    if run_hour is not None:
        cycle_hour = run_hour
    source_cycle_time = datetime.combine(cycle_date, datetime.min.time(), tzinfo=timezone.utc).replace(hour=cycle_hour)
    horizon_profile = _horizon_profile_for_cycle(
        cycle_hour=cycle_hour,
        selection_metadata=selection_metadata,
        manual_cycle_override=manual_cycle_override,
    )
    forecast_track = _forecast_track_for_profile(
        ingest_track=cfg["ingest_track"],
        horizon_profile=horizon_profile,
    )
    source_release_time = selection_metadata.get("next_safe_fetch_at")
    if not isinstance(source_release_time, datetime):
        source_release_time = source_cycle_time
    source_run_id = f"{SOURCE_ID}:{track}:{cycle_date.isoformat()}T{cycle_hour:02d}Z"
    release_calendar_key = f"{SOURCE_ID}:{track}:{horizon_profile}"

    output_path = _download_output_path(
        run_date=cycle_date, run_hour=cycle_hour, param=cfg["open_data_param"],
    )
    stages: list[dict] = []

    # download_observed_steps / _partial_cycle track which steps were actually
    # fetched so _write_source_authority_chain can set the authoritative
    # observed_steps_json and partial_run flag on the source_run row.
    download_observed_steps: list[int] | None = None
    _partial_cycle: bool = False
    _download_reason_code: str | None = None

    # M5-COLLECTION-CLOCK (2026-06-16): real fetch-plane instants. Stamped at the actual code
    # points around the parallel download loop below — left None on the skip_download test seam
    # (no HTTP issued, so no real fetch instant to record).
    _fetch_started_at: datetime | None = None
    _fetch_finished_at: datetime | None = None

    if not skip_download:
        fetch_fn = _fetch_impl or _fetch_one_step
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Dispatch bounded batches.  _DOWNLOAD_MAX_WORKERS is a module
        # constant; no call-site kwarg (antibody: makes per-step parallelism
        # category structurally module-owned, not caller-configured).
        # Single-writer antibody: fetch_fn does HTTP only; no SQLite writes.
        # Do not submit the entire step grid at once: a single hung SDK fetch
        # must fail its batch after _PER_STEP_TIMEOUT_SECONDS instead of making
        # as_completed wait timeout * len(tasks) before live readiness can move.
        tasks = [(s, cfg["open_data_param"]) for s in STEP_HOURS]
        results: dict[int, tuple[str, Any]] = {}
        # M5-COLLECTION-CLOCK: fetch_started = the real wall-clock immediately before the first
        # HTTP GET is dispatched (the batch loop below submits fetch_fn → session.get).
        _fetch_started_at = datetime.now(timezone.utc)
        for offset in range(0, len(tasks), _DOWNLOAD_MAX_WORKERS):
            batch = tasks[offset:offset + _DOWNLOAD_MAX_WORKERS]
            ex = ThreadPoolExecutor(max_workers=len(batch))
            try:
                fut2step = {
                    ex.submit(
                        fetch_fn,
                        cycle_date=cycle_date,
                        cycle_hour=cycle_hour,
                        param=p,
                        step=s,
                        output_dir=output_dir,
                        mirrors=_DOWNLOAD_SOURCES,
                    ): s
                    for s, p in batch
                }
                done, not_done = wait(fut2step, timeout=_PER_STEP_TIMEOUT_SECONDS)
                for fut in done:
                    step = fut2step[fut]
                    try:
                        results[step] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        results[step] = ("FAILED", f"UNCAUGHT_{type(exc).__name__}: {exc}")
                for fut in not_done:
                    step = fut2step[fut]
                    fut.cancel()
                    results[step] = ("FAILED", "STEP_TIMEOUT")
            finally:
                ex.shutdown(wait=False, cancel_futures=True)

        # M5-COLLECTION-CLOCK: fetch_finished = the real wall-clock once every batch future has
        # resolved (bytes received, timed out, or failed) — the moment the download phase ends.
        _fetch_finished_at = datetime.now(timezone.utc)

        ok_steps       = sorted(s for s, (st, _) in results.items() if st == "OK")
        released_404   = sorted(s for s, (st, _) in results.items() if st == "NOT_RELEASED")
        failed_steps   = sorted(s for s, (st, _) in results.items() if st == "FAILED")

        logger.info(
            "ecmwf_open_data parallel_fetch %s: ok=%d not_released=%d failed=%d mirror_first_try=aws",
            track, len(ok_steps), len(released_404), len(failed_steps),
        )

        # --- Early-return branches: FAILED and pure-NOT_RELEASED only ---
        # SUCCESS and PARTIAL fall through to extract+ingest below.

        if failed_steps:
            reason = ";".join(
                f"step{s}:{results[s][1]}" for s in failed_steps[:5]
            )
            _write_stderr_dump(
                PROJECT_ROOT / "tmp"
                / f"ecmwf_open_data_{cycle_date.isoformat()}_{cycle_hour:02d}z_{track}.stderr.txt",
                reason,
            )
            # Write source_run FAILED row directly (no ingest will run).
            computed_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
            try:
                _sr_conn = conn
                _sr_own = _sr_conn is None
                if _sr_own:
                    from src.state.db import get_forecasts_connection as _gfc
                    _sr_conn = _gfc()
                _sr_lock = (
                    db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.BULK)
                    if _sr_own else None
                )
                with (_sr_lock if _sr_lock is not None else nullcontext()):
                    write_source_run(
                        _sr_conn,
                        source_run_id=source_run_id,
                        source_id=SOURCE_ID,
                        track=forecast_track,
                        release_calendar_key=release_calendar_key,
                        source_cycle_time=source_cycle_time,
                        source_issue_time=source_cycle_time,
                        source_release_time=source_release_time,
                        # C1-AVAIL-CLOCK (2026-06-16): proof of possession = computed_at (the real
                        # wall-clock), via the canonical producer — never the cycle-fallback
                        # source_release_time (the safe-fetch gate, not a publish event).
                        source_available_at=proof_of_possession_available_at(computed_at),
                        # M5-COLLECTION-CLOCK (2026-06-16): the download WAS attempted on this branch,
                        # so fetch_started/finished are real (stamped around the loop above). No decode
                        # ran and no forecast data was persisted (this is a FAILED-status row only), so
                        # captured_at / imported_at are honestly NULL — never re-stamped with computed_at.
                        fetch_started_at=_fetch_started_at,
                        fetch_finished_at=_fetch_finished_at,
                        captured_at=None,
                        imported_at=None,
                        data_version=cfg["data_version"],
                        expected_members=51,
                        observed_members=0,
                        expected_steps_json=STEP_HOURS,
                        observed_steps_json=ok_steps,
                        expected_count=0,
                        observed_count=0,
                        completeness_status="MISSING",
                        partial_run=False,
                        status="FAILED",
                        reason_code=reason[:500],
                    )
                    if _sr_own:
                        _sr_conn.commit()
                        _sr_conn.close()
            except Exception as _sr_exc:  # noqa: BLE001
                logger.warning("ecmwf_open_data: could not write FAILED source_run: %s", _sr_exc)
            stages.append({
                "label": f"download_parallel_{track}",
                "ok": False,
                "status": "FAILED",
                "ok_steps": ok_steps,
                "failed_steps": failed_steps,
                "not_released_steps": released_404,
            })
            return {
                "status": "download_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

        if not ok_steps and released_404:
            # Pure NOT_RELEASED: no usable steps at all.
            reason = f"NOT_RELEASED_STEPS={released_404}"
            computed_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
            try:
                _sr_conn = conn
                _sr_own = _sr_conn is None
                if _sr_own:
                    from src.state.db import get_forecasts_connection as _gfc
                    _sr_conn = _gfc()
                _sr_lock = (
                    db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.BULK)
                    if _sr_own else None
                )
                with (_sr_lock if _sr_lock is not None else nullcontext()):
                    write_source_run(
                        _sr_conn,
                        source_run_id=source_run_id,
                        source_id=SOURCE_ID,
                        track=forecast_track,
                        release_calendar_key=release_calendar_key,
                        source_cycle_time=source_cycle_time,
                        source_issue_time=source_cycle_time,
                        source_release_time=source_release_time,
                        # C1-AVAIL-CLOCK (2026-06-16): proof of possession = computed_at (the real
                        # wall-clock), via the canonical producer — never the cycle-fallback
                        # source_release_time (the safe-fetch gate, not a publish event).
                        source_available_at=proof_of_possession_available_at(computed_at),
                        # M5-COLLECTION-CLOCK (2026-06-16): the download WAS attempted, so fetch_started/
                        # finished are real (stamped around the loop above). Nothing was released, so no
                        # decode ran and no forecast data was persisted — captured_at / imported_at are
                        # honestly NULL rather than re-stamped with computed_at.
                        fetch_started_at=_fetch_started_at,
                        fetch_finished_at=_fetch_finished_at,
                        captured_at=None,
                        imported_at=None,
                        data_version=cfg["data_version"],
                        expected_members=51,
                        observed_members=0,
                        expected_steps_json=STEP_HOURS,
                        observed_steps_json=[],
                        expected_count=0,
                        observed_count=0,
                        completeness_status="NOT_RELEASED",
                        partial_run=False,
                        status="SKIPPED_NOT_RELEASED",
                        reason_code=reason[:500],
                    )
                    if _sr_own:
                        _sr_conn.commit()
                        _sr_conn.close()
            except Exception as _sr_exc:  # noqa: BLE001
                logger.warning("ecmwf_open_data: could not write SKIPPED_NOT_RELEASED source_run: %s", _sr_exc)
            stages.append({
                "label": f"download_parallel_{track}",
                "ok": False,
                "status": "SKIPPED_NOT_RELEASED",
                "ok_steps": [],
                "failed_steps": [],
                "not_released_steps": released_404,
            })
            return {
                "status": "skipped_not_released",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

        # SUCCESS (no released_404, no failed) OR PARTIAL (some OK + some 404).
        # Both fall through to extract+ingest.  _write_source_authority_chain
        # will receive download_observed_steps so it can set partial_run correctly.
        _partial_cycle = bool(released_404)
        _download_reason_code = f"NOT_RELEASED_STEPS={released_404}" if _partial_cycle else None
        download_observed_steps = ok_steps

        # Concat per-step files into the canonical output_path for the extractor.
        _concat_steps(
            ok_steps,
            cfg["open_data_param"],
            output_dir,
            output_path,
            run_date=cycle_date,
            run_hour=cycle_hour,
        )

        stages.append({
            "label": f"download_parallel_{track}",
            "ok": True,
            "status": "PARTIAL" if _partial_cycle else "SUCCESS",
            "ok_steps": ok_steps,
            "failed_steps": [],
            "not_released_steps": released_404,
        })

    if not skip_extract:
        extract = runner(
            [
                _conda_python(),
                str(EXTRACT_SCRIPT),
                "--grib-path", str(output_path),
                "--track", cfg["ingest_track"],
                "--output-root", str(FIFTY_ONE_ROOT / "raw"),
                "--manifest-path", str(EXTRACT_MANIFEST_PATH),
            ],
            label=f"extract_{track}",
            timeout=extract_timeout_seconds,
        )
        stages.append(extract)
        if not extract["ok"]:
            _write_stderr_dump(
                PROJECT_ROOT / "tmp"
                / f"ecmwf_open_data_{cycle_date.isoformat()}_{cycle_hour:02d}z_{track}.extract_stderr.txt",
                extract.get("stderr_tail", ""),
            )
            return {
                "status": "extract_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

    # Ingest stage — import in-process, share a single connection so the
    # caller's test fixture (in-memory sqlite) is honored. Production
    # caller passes ``conn=None`` and we open the forecasts DB (K1 split).
    own_conn = conn is None
    if own_conn:
        _lock_ctx = db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.BULK)
    else:
        # Injected connection (test seam with in-memory sqlite) — skip file lock.
        _lock_ctx = nullcontext()
    cleared_authority = {
        "snapshots_deleted": 0,
        "coverage_deleted": 0,
        "producer_readiness_deleted": 0,
        "source_run_deleted": 0,
    }
    with _lock_ctx:
        # ECMWF hang antibody #3 (2026-05-13) — boundary INFO logs at every
        # transition inside the BULK lock so the next 12h hang has a log
        # line pinpointing the failing stage. Witnessed 2026-05-12 13:31
        # PDT silence; see /tmp/zeus_ecmwf_critic_review.md.
        _ingest_t0 = time.monotonic()
        logger.info(
            "ingest_stage: lock_acquired track=%s source_run_id=%s",
            track,
            source_run_id,
        )
        if own_conn:
            conn = get_connection()
        try:
            assert_schema_current_forecasts(conn)
            logger.info(
                "ingest_stage: schema_ok track=%s elapsed_ms=%d",
                track,
                int((time.monotonic() - _ingest_t0) * 1000),
            )
            cleared_authority = _clear_source_run_authority(
                conn,
                source_run_id=source_run_id,
            )
            logger.info(
                "ingest_stage: cleared_prior_source_run track=%s source_run_id=%s %s",
                track,
                source_run_id,
                cleared_authority,
            )
            # The opendata extract writes JSON files to a different subdir than
            # TIGGE — reuse the same ingester by passing the parent directory and
            # the matching track name, and override the json_subdir lookup via the
            # _TRACK_CONFIGS dict. Cleanest in-process integration: temporarily
            # rebind the json_subdir for this call.
            # NOTE 2026-05-13: ingest_grib_to_snapshots is now eager-imported at
            # module top (antibody #1); we reference _ingest_grib_module rather
            # than re-running the import inside the BULK lock.
            original_subdir = _ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"]
            _ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = cfg["extract_subdir"]
            cycle_json_files = 0
            cycle_extract_dir = _cycle_extract_dir_name(run_date=cycle_date, run_hour=cycle_hour)
            try:
                with tempfile.TemporaryDirectory(prefix="zeus_opendata_cycle_") as scoped_tmp:
                    scoped_json_root, cycle_extract_dir, cycle_json_files = _build_cycle_scoped_json_root(
                        raw_root=FIFTY_ONE_ROOT / "raw",
                        extract_subdir=cfg["extract_subdir"],
                        run_date=cycle_date,
                        run_hour=cycle_hour,
                        tmp_root=Path(scoped_tmp),
                    )
                    # Boundary marker — rglob happens inside ingest_track. The
                    # temporary view is the selected source cycle only, so stale
                    # raw directories cannot satisfy a new source_run.
                    logger.info(
                        "ingest_stage: rglob_start track=%s subdir=%s cycle_dir=%s cycle_json_files=%d",
                        track,
                        cfg["extract_subdir"],
                        cycle_extract_dir,
                        cycle_json_files,
                    )
                    # Real possession wall-clock captured immediately before the snapshot write.
                    # Used both for stale-row cleanup (ISO string below) and as the proof-of-
                    # possession basis for the snapshots' source_available_at (C1-AVAIL-CLOCK).
                    # Honors the injected clock: in production now_utc is None so this is a fresh
                    # now() taken right before the write (true possession); under an injected
                    # now_utc (tests, deterministic replay) it MUST equal that clock so every
                    # wall-clock in collect_open_ens_cycle (computed_at / authority_computed_at /
                    # this) shares one time base — otherwise the snapshot's available_at floats to
                    # real-now while decision_time is the injected clock.
                    snapshot_possession_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
                    snapshot_replace_started_at = snapshot_possession_at.isoformat()
                    summary = _ingest_grib_ingest_track(
                        track=cfg["ingest_track"],
                        json_root=scoped_json_root,
                        conn=conn,
                        date_from=None,
                        date_to=None,
                        cities=None,
                        overwrite=True,
                        require_files=False,
                        source_run_context=_ingest_grib_SourceRunContext(
                            source_id=SOURCE_ID,
                            source_transport="ensemble_snapshots_db_reader",
                            source_run_id=source_run_id,
                            release_calendar_key=release_calendar_key,
                            source_cycle_time=source_cycle_time,
                            source_release_time=source_release_time,
                            # C1-AVAIL-CLOCK (2026-06-16): the snapshots' source_available_at /
                            # available_at must be PROOF OF POSSESSION, not source_release_time
                            # (the raw cycle, ~8h early — the exact lie that stamped
                            # ensemble_snapshots.available_at == cycle in 5000/5000 rows). The real
                            # possession wall-clock is snapshot_possession_at (captured just above,
                            # immediately before the write); routed through the canonical producer.
                            # SourceRunContext requires a datetime, so we parse the canonical ISO
                            # string back. No nominal is credited (no real publish estimate exists).
                            source_available_at=datetime.fromisoformat(
                                proof_of_possession_available_at(snapshot_possession_at)
                            ),
                        ),
                    )
                logger.info(
                    "ingest_stage: rglob_end track=%s cycle_dir=%s written=%s skipped_exists=%s parse_error=%s",
                    track,
                    cycle_extract_dir,
                    summary.get("written"),
                    summary.get("skipped_exists"),
                    summary.get("parse_error"),
                )
                stale_snapshots_deleted = _delete_stale_source_run_snapshots(
                    conn,
                    source_run_id=source_run_id,
                    replace_started_at_iso=snapshot_replace_started_at,
                )
                cleared_authority["snapshots_deleted"] += stale_snapshots_deleted
                logger.info(
                    "ingest_stage: stale_snapshot_cleanup track=%s source_run_id=%s deleted=%d",
                    track,
                    source_run_id,
                    stale_snapshots_deleted,
                )
            finally:
                _ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = original_subdir
            status = _status_for_ingest_summary(summary)
            authority_computed_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
            authority_summary = _write_source_authority_chain(
                conn,
                summary=summary,
                status=status,
                source_run_id=source_run_id,
                source_cycle_time=source_cycle_time,
                source_release_time=source_release_time,
                release_calendar_key=release_calendar_key,
                forecast_track=forecast_track,
                data_version=cfg["data_version"],
                computed_at=authority_computed_at,
                # M5-COLLECTION-CLOCK (2026-06-16): real collection-plane instants threaded from their
                # actual code points. fetch_started/finished bracket the parallel download loop above;
                # captured = snapshot_possession_at, the now() taken immediately before the snapshot
                # write (decode-into-memory complete). imported is stamped inside the chain at the
                # source_run persist. None of these is computed_at (the model run-init).
                fetch_started_at=_fetch_started_at,
                fetch_finished_at=_fetch_finished_at,
                captured_at=snapshot_possession_at,
                # Pass download ground-truth so source_run.observed_steps_json
                # reflects actual fetched steps, not an ingest-derived approximation.
                # evaluate_producer_coverage:184 uses this for per-step MISSING detection.
                download_observed_steps=download_observed_steps,
                download_partial_run=_partial_cycle if download_observed_steps is not None else None,
                download_reason_code=_download_reason_code,
            )
            logger.info(
                "ingest_stage: commit_start track=%s status=%s",
                track,
                status,
            )
            conn.commit()
            logger.info(
                "ingest_stage: commit_end track=%s status=%s total_ms=%d",
                track,
                status,
                int((time.monotonic() - _ingest_t0) * 1000),
            )
        finally:
            if own_conn:
                conn.close()

    stages = [
        *stages,
        {"label": "ingest", "ok": status == "ok", "error": status if status != "ok" else None},
    ]
    return {
        "status": status,
        "track": track,
        "data_version": cfg["data_version"],
        "run_date": cycle_date.isoformat(),
        "run_hour": cycle_hour,
        "source_run_id": source_run_id,
        "release_calendar_key": release_calendar_key,
        "forecast_track": forecast_track,
        "source_id": SOURCE_ID,
        "forecast_source_role": FORECAST_SOURCE_ROLE,
        "degradation_level": source_spec.degradation_level,
        "cycle_extract_dir": cycle_extract_dir,
        "cycle_json_files": cycle_json_files,
        "cleared_authority": cleared_authority,
        "download_path": str(output_path),
        "snapshots_inserted": int(summary.get("written", 0)),
        "snapshots_skipped": int(summary.get("skipped", 0)),
        **authority_summary,
        "stages": stages,
    }


def data_version_priority_for_metric(temperature_metric: str) -> tuple[str, ...]:
    """Return read-priority tuple for a given metric.

    HIGH keeps the original OpenData → TIGGE ordering.  LOW prefers rows with
    contract-window evidence first, then falls back to legacy OpenData/TIGGE
    rows.  All entries remain in the same HIGH/LOW metric family.

    Use this in any reader that wants "freshest source first, fall back to
    archive". Equivalent SQL pattern::

        SELECT ... FROM ensemble_snapshots
         WHERE temperature_metric = ?
           AND dataset_id IN (<one placeholder per priority entry>)
         ORDER BY CASE dataset_id WHEN ? THEN 0 ELSE 1 END, available_at DESC

    where the bound parameters are the priority tuple followed by the
    priority tuple's first element again.
    """
    if temperature_metric == "high":
        return (ECMWF_OPENDATA_HIGH_DATA_VERSION, "tigge_mx2t6_local_calendar_day_max")
    if temperature_metric == "low":
        return (
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            "tigge_mn2t6_local_calendar_day_min",
        )
    raise ValueError(f"Unknown temperature_metric {temperature_metric!r}; expected 'high' or 'low'.")


# Back-compat shim — pre-2026-05-01 callers imported ``DATA_VERSION`` from this
# module assuming a single legacy v1 data_version. The structural fix splits
# the path into mx2t6 / mn2t6 tracks; the alias points at the high-track
# opendata data_version so existing imports keep working but new code should
# use ECMWF_OPENDATA_HIGH_DATA_VERSION / _LOW_DATA_VERSION explicitly.
DATA_VERSION = ECMWF_OPENDATA_HIGH_DATA_VERSION

__all__ = [
    "TRACKS",
    "STEP_HOURS",
    "SOURCE_ID",
    "MODEL_VERSION",
    "DATA_VERSION",
    "collect_open_ens_cycle",
    "data_version_priority_for_metric",
]
