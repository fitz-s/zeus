# Created: prior; restructured 2026-05-01
# Last reused or audited: 2026-09-14
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
   TiggeSnapshotPayload contract, using a content-addressed runtime station
   coordinate manifest instead of the external package's city coordinates.
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
import re
import shutil
import subprocess
import sys
import hashlib
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import requests

from src.config import PROJECT_ROOT, runtime_cities_by_name, runtime_coordinate_manifest_json
from src.contracts.availability_time import proof_of_possession_available_at
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    coordinate_bound_data_version,
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


@dataclass(frozen=True)
class OpenDataPaths:
    raw_root: Path
    asset_root: Path
    extract_script: Path
    manifest_path: Path
    origin: str


def _write_runtime_coordinate_manifest(raw_root: Path, *, manifest_json: str | None = None) -> Path:
    """Bind extraction to the same station locations as current provider forecasts."""
    content = (
        manifest_json if manifest_json is not None else runtime_coordinate_manifest_json()
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    directory = raw_root / "docs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"opendata_coordinates_{digest}.json"
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("runtime extraction manifest content hash mismatch")
        return path
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def _has_extract_assets(root: Path) -> bool:
    return (
        (root / "scripts" / "extract_open_ens_localday.py").is_file()
        and (root / "docs" / "tigge_city_coordinate_manifest_full_latest.json").is_file()
    )


def _resolve_opendata_paths(
    *,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    legacy_external_root: Path | None = None,
) -> OpenDataPaths:
    """Bind raw storage and extractor assets once for one collection cycle.

    The home-repo migration left the active OpenData cache under the new repo
    while the extractor package remained in the external source-data checkout.
    An explicit ZEUS_51_SOURCE_ROOT is a complete-root assertion and is never
    silently bypassed. The unset-env migration bridge keeps current raw bytes
    in place while selecting the external asset package only when both required
    assets exist. The returned immutable bundle prevents per-stage path drift.
    """
    env = os.environ if environ is None else environ
    configured = str(env.get("ZEUS_51_SOURCE_ROOT", "")).strip()
    raw_root = Path(
        configured or source_root or FIFTY_ONE_ROOT
    ).expanduser().resolve()
    if configured or _has_extract_assets(raw_root):
        asset_root = raw_root
        origin = "env_complete_root" if configured else "source_root_complete"
    else:
        fallback = (
            legacy_external_root
            if legacy_external_root is not None
            else Path.home() / ".openclaw" / "workspace-venus" / "51 source data"
        ).expanduser().resolve()
        if _has_extract_assets(fallback):
            asset_root = fallback
            origin = "home_repo_migration_split"
        else:
            asset_root = raw_root
            origin = "source_root_missing_assets"
    return OpenDataPaths(
        raw_root=raw_root,
        asset_root=asset_root,
        extract_script=asset_root / "scripts" / "extract_open_ens_localday.py",
        manifest_path=asset_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json",
        origin=origin,
    )


FIFTY_ONE_ROOT = (PROJECT_ROOT / "51 source data").resolve()
# DOWNLOAD_SCRIPT deleted 2026-05-11: replaced by in-process parallel SDK fetch
# (see PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md)
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

# Raw GRIB is a decode input, not an archive: it is deleted as soon as its own
# cycle+track has durable COMPLETE source-run evidence AND an equal count of
# VERIFIED canonical snapshots (the proof gate in
# _plan_decoded_open_data_raw_retention is unchanged and remains the only thing
# that authorizes a delete). Any GRIB still needed is re-fetchable from ECMWF,
# so retaining a calendar window buys nothing a re-fetch does not.
#
# 2026-09-17 (operator directive, twice restated): was 2, which retained ~46 GB
# across two day-dirs on a host at 96% full — for the only one of 13 sources that
# keeps raw payloads at all. 0 means "no calendar grace": eligibility is decided
# purely by the per-cycle proof gate, so an unproven cycle is still retained no
# matter how old. Set to 1 to keep the current UTC day, 2 for the prior behaviour.
_RAW_RETENTION_CALENDAR_DAYS = 0
_RAW_STEP_NAME = re.compile(
    r"^\.(?P<date>\d{8})_(?P<hour>\d{2})z_step\d{3}_"
    r"(?P<param>mx2t3|mn2t3)_ens51\.grib2$"
)
_RAW_CONCAT_NAME = re.compile(
    r"^open_ens_(?P<date>\d{8})_(?P<hour>\d{2})z_steps_.*_params_"
    r"(?P<param>mx2t3|mn2t3)\.grib2$"
)
_RAW_PARAM_AUTHORITY = {
    "mx2t3": ("mx2t6_high", "high"),
    "mn2t3": ("mn2t6_low", "low"),
}


@dataclass(frozen=True)
class _RawRetentionPlan:
    root: Path
    files: tuple[Path, ...]
    eligible_group_count: int
    retained_group_count: int
    unrecognized_file_count: int
    planned_bytes: int


def _raw_file_identity(path: Path) -> tuple[str, int, str] | None:
    match = _RAW_STEP_NAME.fullmatch(path.name) or _RAW_CONCAT_NAME.fullmatch(path.name)
    if match is None:
        return None
    hour = int(match.group("hour"))
    if hour not in {0, 6, 12, 18}:
        return None
    return match.group("date"), hour, match.group("param")


def _canonical_for_transport_sidecar(path: Path) -> Path | None:
    """Return the completed ``.grib2`` a ``.partial``/``.ranges.json`` belongs to.

    Transport names are ``<canonical>.<pf|cf>.<hash>.partial`` and that name plus
    ``.ranges.json``. Returns None for anything that is not one of those, so an
    unrecognised file is never treated as a sidecar.
    """

    name = path.name
    if name.endswith(_RANGE_RESUME_MANIFEST_SUFFIX):
        name = name[: -len(_RANGE_RESUME_MANIFEST_SUFFIX)]
    if not name.endswith(".partial"):
        return None
    name = name[: -len(".partial")]
    # Strip the ``.<pf|cf>.<hexhash>`` transport namespace, when present.
    match = re.fullmatch(r"(?P<base>.+?)\.(?:pf|cf)\.[0-9a-f]+", name)
    if match is not None:
        name = match.group("base")
    return path.with_name(name)


def _orphaned_transport_sidecars(
    root: Path,
    *,
    planned_canonical: set[Path],
) -> list[Path]:
    """Plan `.partial` / `.ranges.json` files whose download is demonstrably over.

    These are invisible to the group proof by construction: `_raw_file_identity`
    matches only `.grib2` names, so the calendar cutoff never reaches a sidecar at
    any age. Measured 2026-09-18: 350 regrew in one cycle after a manual sweep, and
    every one of 1,104 swept earlier had a completed `.grib2` sibling.

    A sidecar is planned ONLY when its canonical target already exists (the step
    finished, so the resume state is spent) or that canonical is being evicted in
    this same plan. A sidecar whose canonical is absent is left alone — that is an
    in-flight or resumable download, and deleting its manifest would discard
    recoverable transport progress.
    """

    orphans: list[Path] = []
    for day_dir in sorted(root.iterdir()):
        if day_dir.is_symlink() or not day_dir.is_dir():
            continue
        for candidate in day_dir.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                continue
            canonical = _canonical_for_transport_sidecar(candidate)
            if canonical is None:
                continue
            if canonical in planned_canonical or canonical.exists():
                orphans.append(candidate)
    return orphans


def _sql_like_escape(value: str) -> str:
    """Escape LIKE wildcards so a literal prefix cannot match more than itself.

    Source-run ids are built from a date and a track name, so they carry no
    wildcards today — but a `_` in a future track label would silently match any
    character and widen an eviction probe, which is the one direction this gate
    must never fail in.
    """

    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _plan_decoded_open_data_raw_retention(
    conn,
    *,
    raw_root: Path,
    reference_date: date,
    retention_days: int = _RAW_RETENTION_CALENDAR_DAYS,
) -> _RawRetentionPlan:
    """Plan deletion only for raw groups reproduced by canonical DB truth.

    Planning happens after the canonical commit while the forecasts connection
    is still authoritative. Applying the plan happens after releasing the BULK
    writer lock so multi-gigabyte filesystem cleanup cannot stall probability
    writers. Unknown files and symlinks are never candidates.
    """
    if retention_days < 0:
        raise ValueError("OpenData raw retention days must not be negative")
    root = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf"
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return _RawRetentionPlan(root, (), 0, 0, 0, 0)

    cutoff = reference_date - timedelta(days=retention_days - 1)
    groups: dict[tuple[str, int, str], list[Path]] = {}
    blocked_groups: set[tuple[str, int, str]] = set()
    unrecognized = 0
    for day_dir in sorted(root.iterdir()):
        if day_dir.is_symlink() or not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y%m%d").date()
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for candidate in day_dir.iterdir():
            identity = _raw_file_identity(candidate)
            if identity is None or identity[0] != day_dir.name:
                if candidate.name.endswith(".grib2"):
                    unrecognized += 1
                continue
            if candidate.is_symlink() or not candidate.is_file():
                blocked_groups.add(identity)
                continue
            groups.setdefault(identity, []).append(candidate)

    planned: list[Path] = []
    eligible_groups = 0
    retained_groups = 0
    for (day_text, hour, param), paths in sorted(groups.items()):
        identity = (day_text, hour, param)
        if identity in blocked_groups:
            retained_groups += 1
            continue
        track, metric = _RAW_PARAM_AUTHORITY[param]
        # The writer stamps ``…:<cycle>Z:coordsha:<manifest_sha>`` (see
        # collect_open_ens_cycle), so an equality probe on the bare cycle prefix
        # matches nothing and every group reads as MISSING -> retained forever.
        # 2026-08-21 (0afd62b16) wrote this lookup before the coordinate-manifest
        # identity existed; 2026-09-09 (160b5ff40) added the suffix to the writer
        # without updating this reader, silently disabling raw eviction. Match the
        # cycle+track prefix and require EVERY run recorded for that cycle to be
        # proven: one unproven coordsha retains the group, so a re-pin under a new
        # manifest can never authorize deleting raw that a prior pin still needs.
        source_run_prefix = (
            f"{SOURCE_ID}:{track}:"
            f"{datetime.strptime(day_text, '%Y%m%d').date().isoformat()}T{hour:02d}Z"
        )
        source_runs = conn.execute(
            """
            SELECT status, completeness_status, partial_run,
                   expected_count, observed_count
              FROM source_run
             WHERE source_id = ?
               AND (source_run_id = ? OR source_run_id LIKE ? ESCAPE '\\')
            """,
            (
                SOURCE_ID,
                source_run_prefix,
                _sql_like_escape(source_run_prefix) + ":%",
            ),
        ).fetchall()
        if not source_runs:
            retained_groups += 1
            continue
        observed_counts: set[int] = set()
        source_complete = True
        for source_run in source_runs:
            expected = source_run["expected_count"]
            observed = source_run["observed_count"]
            if not (
                source_run["status"] == "SUCCESS"
                and source_run["completeness_status"] == "COMPLETE"
                and int(source_run["partial_run"]) == 0
                and expected is not None
                and observed is not None
                and int(expected) > 0
                and int(expected) == int(observed)
            ):
                source_complete = False
                break
            observed_counts.add(int(observed))
        # Two pins disagreeing on how many snapshots the cycle owes leaves the
        # snapshot proof below no single number to check against; retain.
        if not source_complete or len(observed_counts) != 1:
            retained_groups += 1
            continue
        observed = next(iter(observed_counts))
        # Same prefix match as the source_run probe above: the snapshots carry the
        # writer's full coordsha-suffixed id, so an equality probe on the bare
        # cycle prefix would count zero and retain even a fully proven cycle.
        snapshot_proof = conn.execute(
            """
            SELECT COUNT(*) AS snapshot_count,
                   SUM(CASE WHEN authority = 'VERIFIED' THEN 1 ELSE 0 END)
                       AS verified_count
              FROM ensemble_snapshots
             WHERE source_id = ?
               AND temperature_metric = ?
               AND (source_run_id = ? OR source_run_id LIKE ? ESCAPE '\\')
            """,
            (
                SOURCE_ID,
                metric,
                source_run_prefix,
                _sql_like_escape(source_run_prefix) + ":%",
            ),
        ).fetchone()
        snapshot_count = int(snapshot_proof["snapshot_count"] or 0)
        verified_count = int(snapshot_proof["verified_count"] or 0)
        if snapshot_count != int(observed) or verified_count != snapshot_count:
            retained_groups += 1
            continue
        eligible_groups += 1
        planned.extend(paths)

    planned.extend(_orphaned_transport_sidecars(root, planned_canonical=set(planned)))

    return _RawRetentionPlan(
        root=root,
        files=tuple(sorted(planned)),
        eligible_group_count=eligible_groups,
        retained_group_count=retained_groups,
        unrecognized_file_count=unrecognized,
        planned_bytes=sum(path.stat().st_size for path in planned),
    )


def _apply_decoded_open_data_raw_retention(plan: _RawRetentionPlan) -> dict[str, object]:
    """Apply a proof-built plan without recursion or symlink traversal."""
    deleted_files = 0
    deleted_bytes = 0
    errors: list[str] = []
    parents: set[Path] = set()
    if plan.root.is_symlink():
        errors.append("raw_root_became_symlink")
    else:
        for path in plan.files:
            parents.add(path.parent)
            if path.parent.parent != plan.root:
                errors.append(f"path_outside_raw_root:{path}")
                continue
            try:
                if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
                    errors.append(f"path_changed:{path}")
                    continue
                size = path.stat().st_size
                path.unlink()
                deleted_files += 1
                deleted_bytes += size
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(f"{path}:{type(exc).__name__}")
        for parent in sorted(parents):
            try:
                parent.rmdir()
            except OSError:
                pass
    status = "ERROR" if errors else ("APPLIED" if plan.files else "NO_ELIGIBLE_RAW")
    return {
        "status": status,
        "eligible_group_count": plan.eligible_group_count,
        "retained_group_count": plan.retained_group_count,
        "unrecognized_file_count": plan.unrecognized_file_count,
        "planned_file_count": len(plan.files),
        "planned_bytes": plan.planned_bytes,
        "deleted_file_count": deleted_files,
        "deleted_bytes": deleted_bytes,
        "errors": errors[:10],
    }

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

    def acquire(self, *, deadline: float | None = None) -> None:
        """Block until one token is available, then consume it."""
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise requests.Timeout("STEP_DEADLINE_EXCEEDED")
            with self._lock:
                self._refill_locked(time.monotonic())
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
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
# A release probe is one metadata/index handoff, not a GRIB fetch. Reuse the
# existing retry handoff interval so an unresponsive newest cycle cannot spend
# the older cycle's entire one-minute continuity window.
OPENDATA_AVAILABILITY_PROBE_SECONDS: int = _PER_STEP_RETRY_AFTER
_PROBE_SAFE_CLIENT_SOURCES = frozenset({"aws", "google"})
# 404 → NOT_RELEASED (no retry); all others below trigger retry then failover.
_RETRYABLE_HTTP: frozenset[int] = frozenset({500, 502, 503, 504, 408, 429})


def _remaining_step_timeout(deadline: float | None) -> float:
    """Return the request timeout remaining inside one step-owned deadline."""

    if deadline is None:
        return float(_PER_STEP_TIMEOUT_SECONDS)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout("STEP_DEADLINE_EXCEEDED")
    return max(0.001, min(float(_PER_STEP_TIMEOUT_SECONDS), remaining))


def _sleep_step_retry(deadline: float) -> bool:
    """Sleep only inside the step budget; false means retries are exhausted by time."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(float(_PER_STEP_RETRY_AFTER), remaining))
    return time.monotonic() < deadline


def _deadline_failure_reason(*, cycle_deadline: float | None) -> str:
    if cycle_deadline is not None and time.monotonic() >= cycle_deadline:
        return "CYCLE_DEADLINE_EXCEEDED"
    return "STEP_DEADLINE_EXCEEDED"


def _clamp_request_timeout(timeout: Any, *, deadline: float) -> float | tuple[float, ...]:
    """Apply the remaining cycle budget to every requests timeout shape."""
    remaining = _remaining_step_timeout(deadline)
    if timeout is None:
        return remaining
    if isinstance(timeout, tuple):
        return tuple(
            remaining if value is None else min(float(value), remaining)
            for value in timeout
        )
    try:
        return min(float(timeout), remaining)
    except (TypeError, ValueError):
        return remaining


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
        deadline = getattr(self, "_zeus_deadline", None)
        if limited:
            _fetch_bucket.acquire(deadline=deadline)
        if deadline is not None:
            # Do this after a token wait: a caller-supplied None/long timeout
            # cannot outlive the same absolute continuity budget.
            kwargs["timeout"] = _clamp_request_timeout(
                kwargs.get("timeout"), deadline=deadline
            )
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


_RANGE_RESUME_VERSION = 1
# An index entry can represent a whole field collection, so its natural length
# is not a safe progress unit for the 180-second per-step deadline.
_RANGE_RESUME_CHUNK_BYTES = 8 * 1024 * 1024


_RANGE_RESUME_MANIFEST_SUFFIX = ".ranges.json"


def _range_resume_manifest_path(target: Path) -> Path:
    """Return the sidecar that proves which byte-range prefix is reusable."""

    return target.with_name(f"{target.name}{_RANGE_RESUME_MANIFEST_SUFFIX}")


def _resume_source_namespace(source: str) -> str:
    """Return a short, deterministic namespace for one configured source."""

    return hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]


def _source_partial_path(legacy_target: Path, *, kind: str, source: str) -> Path:
    """Return the source-specific PF/CF partial path.

    ``legacy_target`` is the pre-namespace ``*.grib2.partial`` path. Keeping
    the source suffix before ``.partial`` leaves the canonical step filename
    unchanged while preventing mirror rotation from sharing range sidecars.
    """

    if kind not in {"pf", "cf"}:
        raise ValueError(f"Unknown ECMWF partial kind {kind!r}")
    return legacy_target.with_name(
        f"{legacy_target.stem}.{kind}.{_resume_source_namespace(source)}"
        f"{legacy_target.suffix}"
    )


def _path_present(path: Path) -> bool:
    """Treat broken symlinks as existing destinations during adoption."""

    return path.exists() or path.is_symlink()


def _url_is_under_source(url: object, source_base: object) -> bool:
    """Return whether a persisted URL belongs to one exact source prefix."""

    if not isinstance(url, str) or not url:
        return False
    base = str(source_base or "").strip().rstrip("/")
    if not base:
        return False
    return url == base or url.startswith(f"{base}/")


def _legacy_checkpoint_matches_source(target: Path, *, source_base: object) -> bool:
    """Validate enough legacy metadata to move it without cross-source reuse."""

    manifest = _range_resume_manifest_path(target)
    if (
        not target.is_file()
        or target.is_symlink()
        or not manifest.is_file()
        or manifest.is_symlink()
    ):
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        ranges = payload.get("ranges")
        entity_tags = payload.get("entity_tags")
        completed = payload.get("completed_ranges")
        completed_bytes = payload.get("completed_bytes")
        if (
            payload.get("version") != _RANGE_RESUME_VERSION
            or not isinstance(ranges, list)
            or not ranges
            or not isinstance(entity_tags, dict)
            or not isinstance(completed, int)
            or completed < 0
            or completed > len(ranges)
            or not isinstance(completed_bytes, int)
            or completed_bytes < 0
            or target.stat().st_size < completed_bytes
        ):
            return False
        for item in ranges:
            if not isinstance(item, list) or len(item) != 3:
                return False
            if (
                not _url_is_under_source(item[0], source_base)
                or isinstance(item[1], bool)
                or not isinstance(item[1], int)
                or item[1] < 0
                or isinstance(item[2], bool)
                or not isinstance(item[2], int)
                or item[2] <= 0
            ):
                return False
        if any(
            not _url_is_under_source(url, source_base)
            or not _is_strong_etag(tag)
            for url, tag in entity_tags.items()
        ):
            return False
        expected_bytes = sum(item[2] for item in ranges[:completed])
        expected_urls = {item[0] for item in ranges[:completed]}
        if completed_bytes != expected_bytes or set(entity_tags) != expected_urls:
            return False
    except (OSError, TypeError, ValueError, AttributeError, json.JSONDecodeError, KeyError):
        return False
    return True


def _adopt_legacy_checkpoint(
    legacy_target: Path,
    destination: Path,
    *,
    source_base: object,
) -> bool:
    """Move one proven legacy body+sidecar pair into a vacant source namespace.

    Hardlinks followed by source unlink provide a same-directory, pair-safe
    move: an interruption before both links exist leaves the legacy pair
    intact, while a partially-created destination is never accepted without
    the existing loader's full plan/ETag validation.
    """

    legacy_manifest = _range_resume_manifest_path(legacy_target)
    destination_manifest = _range_resume_manifest_path(destination)
    if _path_present(destination) or _path_present(destination_manifest):
        return False
    if not _legacy_checkpoint_matches_source(legacy_target, source_base=source_base):
        return False

    created: list[Path] = []
    try:
        os.link(str(legacy_manifest), str(destination_manifest))
        created.append(destination_manifest)
        os.link(str(legacy_target), str(destination))
        created.append(destination)
    except FileExistsError:
        for path in created:
            path.unlink(missing_ok=True)
        return False
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        return False

    try:
        legacy_manifest.unlink()
        legacy_target.unlink()
    except OSError:
        # The destination is complete and remains valid; leaving a duplicate
        # legacy pair is safer than deleting a checkpoint after partial cleanup.
        logger.warning("ECMWF legacy range checkpoint cleanup incomplete for %s", legacy_target)
    return True


def _is_strong_etag(value: Any) -> bool:
    """Return whether an HTTP ETag can identify byte-for-byte entity content."""

    return isinstance(value, str) and bool(value) and not value.lstrip().startswith("W/")


def _range_resume_plan(urls: Any) -> tuple[tuple[str, int, int], ...] | None:
    """Flatten indexed URLs into the exact ordered range identity we assemble."""

    plan: list[tuple[str, int, int]] = []
    for item in urls:
        if not (
            isinstance(item, tuple)
            and len(item) == 2
            and not isinstance(item[1], (str, bytes))
        ):
            return None
        url, parts = item
        for part in parts:
            offset, length = _part_offset_length(part)
            if offset < 0 or length <= 0:
                raise ValueError(f"Invalid indexed range offset={offset} length={length}")
            remaining = length
            chunk_offset = offset
            while remaining:
                chunk_length = min(remaining, _RANGE_RESUME_CHUNK_BYTES)
                plan.append((str(url), chunk_offset, chunk_length))
                chunk_offset += chunk_length
                remaining -= chunk_length
    return tuple(plan) if plan else None


def _reset_range_resume(target: Path) -> None:
    """Discard an unverified partial prefix; it must never reach canonical GRIB."""

    target.unlink(missing_ok=True)
    _range_resume_manifest_path(target).unlink(missing_ok=True)


def _write_range_resume_manifest(
    target: Path,
    *,
    plan: tuple[tuple[str, int, int], ...],
    completed_ranges: int,
    entity_tags: Mapping[str, str],
) -> None:
    """Atomically checkpoint only a fully received, fsync'd range prefix."""

    completed_bytes = sum(length for _, _, length in plan[:completed_ranges])
    payload = {
        "version": _RANGE_RESUME_VERSION,
        "ranges": [list(part) for part in plan],
        "completed_ranges": completed_ranges,
        "completed_bytes": completed_bytes,
        "entity_tags": dict(entity_tags),
    }
    manifest = _range_resume_manifest_path(target)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=manifest.parent,
        prefix=f".{manifest.name}.",
        suffix=".tmp",
        delete=False,
    ) as out:
        temp_path = Path(out.name)
        json.dump(payload, out, sort_keys=True, separators=(",", ":"))
        out.flush()
        os.fsync(out.fileno())
    try:
        os.replace(temp_path, manifest)
    finally:
        temp_path.unlink(missing_ok=True)


def _load_range_resume(
    target: Path,
    *,
    plan: tuple[tuple[str, int, int], ...],
    session: Any,
    verify: Any,
    deadline: float | None,
) -> tuple[int, dict[str, str]]:
    """Return a verified reusable prefix, resetting malformed or changed cache.

    A partial body is not a completed range.  The sidecar is written only after
    the range bytes have been fsync'd, and an existing prefix is accepted only
    when the current index plan and the source entity ETags still agree.
    """

    manifest = _range_resume_manifest_path(target)
    if not target.exists() and not manifest.exists():
        return 0, {}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        completed = payload["completed_ranges"]
        completed_bytes = payload["completed_bytes"]
        entity_tags = payload["entity_tags"]
        expected_ranges = [list(part) for part in plan]
        if (
            payload.get("version") != _RANGE_RESUME_VERSION
            or payload.get("ranges") != expected_ranges
            or not isinstance(completed, int)
            or not 0 <= completed <= len(plan)
            or not isinstance(completed_bytes, int)
            or completed_bytes != sum(length for _, _, length in plan[:completed])
            or not isinstance(entity_tags, dict)
            or target.stat().st_size < completed_bytes
        ):
            raise ValueError("range resume sidecar does not match current source identity")
        expected_urls = {url for url, _, _ in plan[:completed]}
        if set(entity_tags) != expected_urls or any(
            not _is_strong_etag(tag) for tag in entity_tags.values()
        ):
            raise ValueError("range resume sidecar has incomplete entity identity")
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        logger.warning("Discarding invalid ECMWF range resume cache for %s", target)
        _reset_range_resume(target)
        return 0, {}

    for url in sorted(expected_urls):
        response = session.head(
            url,
            timeout=_remaining_step_timeout(deadline),
            verify=verify,
        )
        try:
            if response.status_code != 200:
                if response.status_code == 429 or response.status_code >= 500:
                    # A transient validation failure says nothing about the
                    # entity. Preserve the verified prefix so the caller's
                    # retry/failover loop can resume it later.
                    raise _http_error_for_response(
                        response,
                        f"ECMWF resume entity validation transiently unavailable "
                        f"(HTTP {response.status_code}) for {url}",
                    )
                logger.info(
                    "ECMWF resume entity validation is unavailable (HTTP %s) for %s; restarting full partial",
                    response.status_code,
                    url,
                )
                _reset_range_resume(target)
                return 0, {}
            entity_tag = getattr(response, "headers", {}).get("ETag")
            if not _is_strong_etag(entity_tag):
                logger.info(
                    "ECMWF resume entity validation has no strong ETag for %s; restarting full partial",
                    url,
                )
                _reset_range_resume(target)
                return 0, {}
            if entity_tag != entity_tags[url]:
                logger.warning("Discarding ECMWF range resume cache after entity change for %s", url)
                _reset_range_resume(target)
                return 0, {}
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    with target.open("r+b") as out:
        out.truncate(completed_bytes)
    return completed, dict(entity_tags)


def _validate_range_response(
    response: Any,
    *,
    offset: int,
    length: int,
) -> str | None:
    """Prove a range response is exactly the requested bytes before checkpointing."""

    end = offset + length - 1
    if response.status_code != 206:
        if response.status_code >= 400:
            response.raise_for_status()
        raise _http_error_for_response(
            response,
            f"Expected HTTP 206 for range GET, got {response.status_code}",
        )
    headers = getattr(response, "headers", {})
    content_range = headers.get("Content-Range")
    if content_range is None:
        raise _http_error_for_response(response, "Range GET omitted Content-Range")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(?:\d+|\*)", content_range.strip())
    if not match or (int(match.group(1)), int(match.group(2))) != (offset, end):
        raise _http_error_for_response(
            response,
            f"Range GET Content-Range {content_range!r} does not match bytes={offset}-{end}",
        )
    content_length = headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) != length:
                raise ValueError
        except ValueError:
            raise _http_error_for_response(
                response,
                f"Range GET Content-Length {content_length!r} does not match {length}",
            )
    return headers.get("ETag")


def _resolve_index_parts(
    client: Any,
    result: Any,
    *,
    deadline: float | None = None,
) -> list[tuple[str, tuple[tuple[int, int], ...]]]:
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
            timeout=_remaining_step_timeout(deadline),
            verify=verify,
        )
        try:
            if response.status_code != 200:
                response.raise_for_status()
            parts: list[tuple[int, int]] = []
            for raw_line in response.iter_lines():
                _remaining_step_timeout(deadline)
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


def _probe_index_member_count(
    client: Any,
    *,
    cycle_date: date,
    cycle_hour: int,
    param: str,
    stream: str,
    ensemble_type: str,
    step: int,
    deadline_monotonic: float,
) -> int:
    """Read one exact ensemble index without downloading any GRIB bytes."""
    session = _RateLimitedSession()
    session._zeus_deadline = deadline_monotonic
    client.session = session
    try:
        target = Path(tempfile.gettempdir()) / "zeus_opendata_availability_probe.grib2"
        result = client._get_urls(
            target=str(target),
            use_index=False,
            date=int(cycle_date.strftime("%Y%m%d")),
            time=cycle_hour,
            stream=stream,
            type=[ensemble_type],
            step=[step],
            param=[param],
        )
        _remaining_step_timeout(deadline_monotonic)
        return sum(
            len(parts)
            for _url, parts in _resolve_index_parts(
                client,
                result,
                deadline=deadline_monotonic,
            )
        )
    finally:
        session.close()


def probe_open_ens_cycle_release(
    *,
    track: str,
    run_date: date,
    run_hour: int,
    deadline_monotonic: float,
) -> dict[str, object]:
    """Classify exact-cycle release from index evidence without source writes.

    ``released`` means the exact track parameter's nearest step has the full
    51-member ENS shape (50 PF + 1 CF/oper fallback). It is only permission to
    run the normal collector, never source-run or readiness truth itself.
    """
    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    if time.monotonic() >= deadline_monotonic:
        return {"status": "unknown", "reason": "AVAILABILITY_PROBE_DEADLINE"}
    source_spec = gate_source(SOURCE_ID)
    gate_source_role(source_spec, FORECAST_SOURCE_ROLE)

    cfg = TRACKS[track]
    probe_step = min(STEP_HOURS)
    absent_pf_mirrors = 0
    # Azure's Client constructor obtains an SAS token before the
    # deadline-aware session is installed. Do not create that network path.
    if any(mirror not in _PROBE_SAFE_CLIENT_SOURCES for mirror in _DOWNLOAD_SOURCES):
        return {
            "status": "unknown",
            "reason": f"UNSAFE_PROBE_SOURCE:{next(mirror for mirror in _DOWNLOAD_SOURCES if mirror not in _PROBE_SAFE_CLIENT_SOURCES)}",
        }
    from ecmwf.opendata import Client  # imported here: conda env only on daemon worker

    for mirror in _DOWNLOAD_SOURCES:
        if time.monotonic() >= deadline_monotonic:
            return {"status": "unknown", "reason": "AVAILABILITY_PROBE_DEADLINE"}
        try:
            client = Client(source=mirror)
            pf_members = _probe_index_member_count(
                client,
                cycle_date=run_date,
                cycle_hour=run_hour,
                param=cfg["open_data_param"],
                stream="enfo",
                ensemble_type="pf",
                step=probe_step,
                deadline_monotonic=deadline_monotonic,
            )
        except requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) == 404:
                absent_pf_mirrors += 1
                continue
            return {"status": "unknown", "reason": f"HTTP_{getattr(exc.response, 'status_code', 'UNKNOWN')}"}
        except ValueError as exc:
            if "Cannot find index entries matching" in str(exc):
                absent_pf_mirrors += 1
                continue
            return {"status": "unknown", "reason": f"INDEX_ERROR:{type(exc).__name__}"}
        except (requests.RequestException, OSError) as exc:
            return {"status": "unknown", "reason": f"NETWORK:{type(exc).__name__}"}

        # Any PF index evidence means the newest cycle has begun publishing.
        # Missing/incomplete CF must therefore retain newest-cycle priority.
        if pf_members != 50:
            return {"status": "unknown", "reason": f"INCOMPLETE_PF_MEMBERS:{pf_members}"}
        try:
            try:
                cf_members = _probe_index_member_count(
                    client,
                    cycle_date=run_date,
                    cycle_hour=run_hour,
                    param=cfg["open_data_param"],
                    stream="enfo",
                    ensemble_type="cf",
                    step=probe_step,
                    deadline_monotonic=deadline_monotonic,
                )
            except ValueError as exc:
                if "Cannot find index entries matching" not in str(exc):
                    raise
                try:
                    cf_members = _probe_index_member_count(
                        client,
                        cycle_date=run_date,
                        cycle_hour=run_hour,
                        param=cfg["open_data_param"],
                        stream="oper",
                        ensemble_type="fc",
                        step=probe_step,
                        deadline_monotonic=deadline_monotonic,
                    )
                except (requests.HTTPError, ValueError):
                    return {"status": "unknown", "reason": "INCOMPLETE_CF"}
            if cf_members == 1:
                return {
                    "status": "released",
                    "mirror": mirror,
                    "pf_members": pf_members,
                    "cf_members": cf_members,
                    "step": probe_step,
                }
            return {"status": "unknown", "reason": f"INCOMPLETE_CF_MEMBERS:{cf_members}"}
        except (requests.RequestException, OSError) as exc:
            return {"status": "unknown", "reason": f"NETWORK:{type(exc).__name__}"}

    if absent_pf_mirrors == len(_DOWNLOAD_SOURCES):
        return {"status": "not_released", "reason": "NOT_RELEASED_PF_INDEX"}
    return {"status": "unknown", "reason": "AVAILABILITY_PROBE_INCOMPLETE"}


def _retrieve_step_with_controlled_ranges(
    client: Any,
    *,
    target: Path,
    _deadline: float | None = None,
    _cycle_deadline_enforced: bool = False,
    **kwargs: Any,
) -> Any:
    """Retrieve one indexed OpenData step with Zeus-owned single Range GETs.

    ecmwf-opendata's default ``Client.retrieve`` delegates indexed GRIB assembly
    to multiurl. In live forecast runs multiurl was the broken boundary: it can
    emit bursty multi-range/internal-retry traffic outside Zeus' token bucket,
    producing AWS S3 SlowDown / HTTP 429 followed by 120-second sleeps. This
    downloader keeps index resolution in ecmwf-opendata but owns every HTTP GET.
    Outer retry/failover remains in ``_fetch_one_step``.
    """

    if not hasattr(client, "_get_urls"):
        if _cycle_deadline_enforced:
            # The legacy SDK path does not expose request timeouts or chunk
            # boundaries, so it cannot honor a bounded continuity retry.
            raise requests.Timeout("CYCLE_DEADLINE_EXCEEDED")
        _fetch_bucket.acquire(deadline=_deadline)
        return client.retrieve(target=str(target), **kwargs)

    client.session = _RateLimitedSession()
    client.session._zeus_deadline = _deadline
    result = client._get_urls(target=str(target), use_index=False, **kwargs)
    _remaining_step_timeout(_deadline)
    indexed_parts = _resolve_index_parts(client, result, deadline=_deadline)
    if indexed_parts:
        result.urls = indexed_parts

    target_path = Path(result.target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    verify = getattr(client, "verify", True)
    session = client.session
    plan = _range_resume_plan(result.urls)
    if plan is None:
        # Plain-object downloads have no byte-range identity to prove.  Do not
        # reuse a stale indexed prefix if an upstream response shape changes.
        _reset_range_resume(target_path)
        bytes_written = 0
        with target_path.open("wb") as out:
            for item in result.urls:
                response = session.get(
                    item,
                    stream=True,
                    timeout=_remaining_step_timeout(_deadline),
                    verify=verify,
                )
                try:
                    if response.status_code != 200:
                        response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        _remaining_step_timeout(_deadline)
                        if chunk:
                            out.write(chunk)
                            bytes_written += len(chunk)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
        result.size = bytes_written
        return result

    completed_ranges, entity_tags = _load_range_resume(
        target_path,
        plan=plan,
        session=session,
        verify=verify,
        deadline=_deadline,
    )
    resumable = True
    with target_path.open("ab") as out:
        for range_index, (url, offset, length) in enumerate(plan[completed_ranges:], completed_ranges):
            end = offset + length - 1
            response = session.get(
                url,
                stream=True,
                headers={"Range": f"bytes={offset}-{end}"},
                timeout=_remaining_step_timeout(_deadline),
                verify=verify,
            )
            try:
                entity_tag = _validate_range_response(response, offset=offset, length=length)
                if not _is_strong_etag(entity_tag):
                    # A fresh transfer can still complete without a resume
                    # identity.  A reused prefix cannot: discard it and use
                    # the existing retry loop to restart from byte zero.
                    resumable = False
                    _range_resume_manifest_path(target_path).unlink(missing_ok=True)
                    if completed_ranges:
                        raise requests.ConnectionError(
                            "ECMWF range response lacks a strong ETag after resuming a prefix"
                        )
                known_tag = entity_tags.get(url)
                if _is_strong_etag(entity_tag) and known_tag is not None and entity_tag != known_tag:
                    raise _http_error_for_response(
                        response,
                        f"Range GET entity changed while downloading {url}",
                    )
                range_bytes = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    _remaining_step_timeout(_deadline)
                    if chunk:
                        if range_bytes + len(chunk) > length:
                            raise _http_error_for_response(
                                response,
                                f"Range GET body exceeds requested length {length}",
                            )
                        out.write(chunk)
                        range_bytes += len(chunk)
                if range_bytes != length:
                    raise _http_error_for_response(
                        response,
                        f"Range GET body length {range_bytes} does not match {length}",
                    )
                if _is_strong_etag(entity_tag):
                    entity_tags[url] = entity_tag
                out.flush()
                os.fsync(out.fileno())
                if resumable:
                    _write_range_resume_manifest(
                        target_path,
                        plan=plan,
                        completed_ranges=range_index + 1,
                        entity_tags=entity_tags,
                    )
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

    result.size = sum(length for _, _, length in plan)
    return result


_CONDA_PYTHON_PROBE_MODULE = "eccodes"
_CONDA_PYTHON_PROBE_TIMEOUT_SECONDS = 8.0


def _interpreter_has_dependency(candidate: str, *, probe_module: str) -> bool:
    """Return True iff `candidate` is executable and can `import probe_module`.

    Existence alone does not prove the interpreter is the right one — a
    ~/miniconda3/bin/python that exists but predates the ecmwf deps (or
    belongs to an unrelated env) would otherwise be silently accepted and
    every extract subprocess launched under it would fail. A dependency
    probe is the only cheap proof that matters.
    """
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        result = subprocess.run(
            [candidate, "-c", f"import {probe_module}"],
            capture_output=True,
            timeout=_CONDA_PYTHON_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _conda_python() -> str:
    """Path to the Python interpreter with ecmwf.opendata + eccodes installed.

    Resolution order:
      1. ZEUS_ECMWF_PYTHON env var (explicit deployment config; trusted
         as-is — an operator who sets this is asserting it is correct).
      2. ~/miniconda3/bin/python (conda default install location, portable
         across machines/usernames via Path.home()), ACCEPTED ONLY if it is
         executable and can import eccodes.
      3. `python3`, then `python`, resolved on PATH (covers non-default
         conda install dirs) — `python3` first because a bare `python` can
         resolve to Python 2 on some systems; same executability +
         dependency-import validation either way (belt-and-suspenders: the
         import probe alone already rejects a Python 2 interpreter, since
         these deps aren't installed there).
      4. sys.executable, with a logged warning — no candidate proved it has
         the ecmwf deps, so the extract subprocess launched under it is
         expected to fail; this keeps the daemon alive to log the failure
         rather than crash outright, and test/dry-run environments that
         already carry the deps on sys.executable are unaffected.
    """
    from_env = os.environ.get("ZEUS_ECMWF_PYTHON")
    if from_env:
        return from_env
    candidate = str(Path.home() / "miniconda3" / "bin" / "python")
    if _interpreter_has_dependency(candidate, probe_module=_CONDA_PYTHON_PROBE_MODULE):
        return candidate
    for path_candidate in (shutil.which("python3"), shutil.which("python")):
        if path_candidate and _interpreter_has_dependency(
            path_candidate, probe_module=_CONDA_PYTHON_PROBE_MODULE
        ):
            return path_candidate
    logger.warning(
        "_conda_python: no interpreter with '%s' importable found (checked "
        "ZEUS_ECMWF_PYTHON, %s, PATH `python3`/`python`); falling back to "
        "sys.executable (%s) — the extract subprocess will fail if it lacks "
        "ecmwf deps.",
        _CONDA_PYTHON_PROBE_MODULE,
        candidate,
        sys.executable,
    )
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


def _download_output_path(
    *,
    run_date: date,
    run_hour: int,
    param: str,
    raw_root: Path | None = None,
) -> Path:
    steps_sig = _step_hours_signature()
    return (
        (raw_root or FIFTY_ONE_ROOT)
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


def _parse_cycle_extract_dir_name(name: str) -> tuple[date, int] | None:
    """Inverse of `_cycle_extract_dir_name`; None when the name is not one of ours.

    Keeps the pruner from ever treating an unrecognised directory as a spent cycle.
    """

    match = re.fullmatch(r"(?P<date>\d{8})(?:_cycle(?P<hour>\d{2})z)?", name)
    if match is None:
        return None
    try:
        day = datetime.strptime(match.group("date"), "%Y%m%d").date()
    except ValueError:
        return None
    hour = int(match.group("hour") or 0)
    if hour not in {0, 6, 12, 18}:
        return None
    return day, hour


def _prune_superseded_coordinate_manifest_cycles(
    *,
    coordinate_raw_root: Path,
    extract_subdir: str,
    keep_run_date: date,
    keep_run_hour: int,
) -> dict[str, object]:
    """Delete decoded-JSON cycle views the ingest can no longer consume.

    `_build_cycle_scoped_json_root` assembles a temp view of the SELECTED cycle only
    ("stale raw directories cannot satisfy a new source_run"), so once a newer cycle
    is ingested every older `<city>/<cycle>` tree under the manifest sha is
    write-only. Nothing pruned them: the dirs inherited none of the grib cache's
    retention, and measured 2026-09-18 they held 33 cycles per city across 54
    cities spanning 2026-09-09..09-17 — 234 MB, ~26 MB/day, unbounded (~9.5 GB/yr).

    Only cycles STRICTLY OLDER than the one just ingested are removed, and only
    directories whose name parses as one of ours. The kept cycle is never touched,
    so a retry of the same cycle still finds its view. Best-effort by design: a
    failure here must never fail an ingest that already committed its canonical
    truth, so errors are counted and returned rather than raised.
    """

    source_subdir = coordinate_raw_root / extract_subdir
    summary: dict[str, object] = {
        "status": "NO_SUPERSEDED_CYCLES",
        "removed_cycle_dirs": 0,
        "removed_bytes": 0,
        "errors": [],
    }
    if source_subdir.is_symlink() or not source_subdir.is_dir():
        return summary

    keep = (keep_run_date, keep_run_hour)
    removed = 0
    removed_bytes = 0
    errors: list[str] = []
    for city_dir in sorted(source_subdir.iterdir()):
        if city_dir.is_symlink() or not city_dir.is_dir():
            continue
        for cycle_dir in sorted(city_dir.iterdir()):
            if cycle_dir.is_symlink() or not cycle_dir.is_dir():
                continue
            parsed = _parse_cycle_extract_dir_name(cycle_dir.name)
            if parsed is None or parsed >= keep:
                continue
            try:
                size = sum(
                    path.stat().st_size
                    for path in cycle_dir.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
                shutil.rmtree(cycle_dir)
            except OSError as exc:
                errors.append(f"{cycle_dir.name}:{type(exc).__name__}")
                continue
            removed += 1
            removed_bytes += size

    summary["status"] = "PRUNED" if removed else "NO_SUPERSEDED_CYCLES"
    summary["removed_cycle_dirs"] = removed
    summary["removed_bytes"] = removed_bytes
    summary["errors"] = errors
    return summary


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
        allow_partial=True,
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
    # Twin of executable_forecast_reader._station_grid_provenance_reason; both
    # families settle off an ICAO airport station and so both owe the proof.
    if source_type not in {"wu_icao", "noaa"}:
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
        "TARGET_LOCAL_DAY_BOUNDARY_AMBIGUOUS",
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
            if (
                attribution_status == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
                and int(row.get("boundary_ambiguous") or 0) == 1
            ):
                reason_codes.append("TARGET_LOCAL_DAY_BOUNDARY_AMBIGUOUS")
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
    _deadline: float | None = None,
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
    deadline = min(
        time.monotonic() + float(_PER_STEP_TIMEOUT_SECONDS),
        _deadline if _deadline is not None else float("inf"),
    )
    if time.monotonic() >= deadline:
        return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
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
            if time.monotonic() >= deadline:
                return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
            try:
                client = Client(source=mirror)
                source_base = getattr(client, "url", None)
                pf_partial = _source_partial_path(partial, kind="pf", source=mirror)
                cf_partial = _source_partial_path(partial, kind="cf", source=mirror)
                _adopt_legacy_checkpoint(
                    partial.with_suffix(".pf.partial"),
                    pf_partial,
                    source_base=source_base,
                )
                _adopt_legacy_checkpoint(
                    partial.with_suffix(".cf.partial"),
                    cf_partial,
                    source_base=source_base,
                )
                _retrieve_step_with_controlled_ranges(
                    client,
                    date=int(cycle_date.strftime("%Y%m%d")),
                    time=cycle_hour,
                    stream="enfo",
                    type=["pf"],
                    step=[step],
                    param=[param],
                    target=pf_partial,
                    _deadline=deadline,
                    _cycle_deadline_enforced=_deadline is not None,
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
                        _deadline=deadline,
                        _cycle_deadline_enforced=_deadline is not None,
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
                        _deadline=deadline,
                        _cycle_deadline_enforced=_deadline is not None,
                    )
                with partial.open("wb") as out:
                    out.write(cf_partial.read_bytes())
                    out.write(pf_partial.read_bytes())
                os.replace(str(partial), str(canonical))   # atomic rename
                pf_partial.unlink(missing_ok=True)
                cf_partial.unlink(missing_ok=True)
                # The range-resume manifests must go with their partials. Only
                # _reset_range_resume (checksum/ETag mismatch mid-retry) and the
                # weak-ETag discard path unlinked these, never the success path, so
                # every completed step left one behind — and because
                # _raw_file_identity recognises neither `.partial` nor
                # `.ranges.json`, the retention plan can never reach them at any
                # age. Measured 2026-09-18: 350 sidecar files regrew within a single
                # cycle after a manual sweep. Unbounded in file count.
                _range_resume_manifest_path(pf_partial).unlink(missing_ok=True)
                _range_resume_manifest_path(cf_partial).unlink(missing_ok=True)
                _range_resume_manifest_path(partial).unlink(missing_ok=True)
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
                    if attempt + 1 < _PER_STEP_MAX_RETRIES and not _sleep_step_retry(deadline):
                        return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
                    continue
                last_err = f"HTTP_{code}_mirror_{mirror}"
                break   # non-retryable; try next mirror
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = f"NET_{type(exc).__name__}_mirror_{mirror}_attempt_{attempt}"
                if time.monotonic() >= deadline:
                    return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
                if attempt + 1 < _PER_STEP_MAX_RETRIES and not _sleep_step_retry(deadline):
                    return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
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
    _paths: OpenDataPaths | None = None,
    now_utc: datetime | None = None,
    coordinate_manifest_json: str | None = None,
    cycle_deadline_monotonic: float | None = None,
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
    cfg = dict(TRACKS[track])
    manifest_json = (
        runtime_coordinate_manifest_json()
        if coordinate_manifest_json is None else coordinate_manifest_json
    )
    manifest_sha = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    cfg["data_version"] = coordinate_bound_data_version(cfg["data_version"], manifest_sha)
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
    source_run_id = (
        f"{SOURCE_ID}:{track}:{cycle_date.isoformat()}T{cycle_hour:02d}Z"
        f":coordsha:{manifest_sha}"
    )
    release_calendar_key = f"{SOURCE_ID}:{track}:{horizon_profile}"

    paths = _paths or _resolve_opendata_paths()
    coordinate_raw_root = paths.raw_root / "raw" / "coordinate_manifests" / manifest_sha
    output_path = _download_output_path(
        run_date=cycle_date,
        run_hour=cycle_hour,
        param=cfg["open_data_param"],
        raw_root=paths.raw_root,
    )
    stages: list[dict] = []

    if not skip_extract:
        missing_extract_assets = [
            str(path)
            for path in (paths.extract_script, paths.manifest_path)
            if not path.is_file()
        ]
        if missing_extract_assets:
            reason = "MISSING_EXTRACT_ASSETS:" + ",".join(missing_extract_assets)
            stage = {
                "label": f"extract_assets_preflight_{track}",
                "ok": False,
                "status": "MISSING_EXTRACT_ASSETS",
                "stderr_tail": reason,
            }
            logger.error("ecmwf_open_data: %s", reason)
            return {
                "status": "extract_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "reason": reason,
                "stages": [stage],
                "snapshots_inserted": 0,
            }
        if paths.asset_root != paths.raw_root:
            logger.info(
                "ecmwf_open_data: path_bundle origin=%s raw_root=%s asset_root=%s",
                paths.origin,
                paths.raw_root,
                paths.asset_root,
            )
        coordinate_manifest = _write_runtime_coordinate_manifest(
            paths.raw_root, manifest_json=manifest_json,
        )

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

        # Keep only the configured number of steps outstanding. Refill a free
        # slot immediately: a slow step must not idle the rest of its wave.
        # Workers own HTTP deadlines; the collecting thread consumes every
        # result before any SQLite write, without a second timeout.
        tasks = [(s, cfg["open_data_param"]) for s in STEP_HOURS]
        results: dict[int, tuple[str, Any]] = {}
        deadline_exceeded = False
        # M5-COLLECTION-CLOCK: fetch_started = the real wall-clock immediately before the first
        # HTTP GET is dispatched (the batch loop below submits fetch_fn → session.get).
        _fetch_started_at = datetime.now(timezone.utc)
        remaining_tasks = iter(tasks)
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_MAX_WORKERS) as ex:
            def submit_step(task):
                step, param = task
                if cycle_deadline_monotonic is not None and time.monotonic() >= cycle_deadline_monotonic:
                    return None
                kwargs = {
                    "cycle_date": cycle_date,
                    "cycle_hour": cycle_hour,
                    "param": param,
                    "step": step,
                    "output_dir": output_dir,
                    "mirrors": _DOWNLOAD_SOURCES,
                }
                if _fetch_impl is None:
                    kwargs["_deadline"] = cycle_deadline_monotonic
                return ex.submit(
                    fetch_fn,
                    **kwargs,
                )

            pending = {}
            for _ in range(min(_DOWNLOAD_MAX_WORKERS, len(tasks))):
                task = next(remaining_tasks)
                future = submit_step(task)
                if future is None:
                    deadline_exceeded = True
                    results[task[0]] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")
                else:
                    pending[future] = task[0]
            while pending:
                timeout = None
                if cycle_deadline_monotonic is not None:
                    timeout = max(0.0, cycle_deadline_monotonic - time.monotonic())
                completed, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                if not completed:
                    deadline_exceeded = True
                    for future, step in pending.items():
                        future.cancel()
                        results[step] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")
                    break
                for fut in completed:
                    step = pending.pop(fut)
                    try:
                        results[step] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        results[step] = ("FAILED", f"UNCAUGHT_{type(exc).__name__}: {exc}")
                    task = next(remaining_tasks, None)
                    if task is not None:
                        future = submit_step(task)
                        if future is None:
                            deadline_exceeded = True
                            results[task[0]] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")
                        else:
                            pending[future] = task[0]

            if deadline_exceeded:
                for task in remaining_tasks:
                    results[task[0]] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")

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
                "reason": "CYCLE_DEADLINE_EXCEEDED" if deadline_exceeded else reason,
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
        if (
            cycle_deadline_monotonic is not None
            and time.monotonic() >= cycle_deadline_monotonic
        ):
            stages.append({
                "label": f"concat_{track}",
                "ok": False,
                "status": "CYCLE_DEADLINE_EXCEEDED",
            })
            return {
                "status": "download_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "reason": "CYCLE_DEADLINE_EXCEEDED",
                "stages": stages,
                "snapshots_inserted": 0,
            }
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
        if cycle_deadline_monotonic is not None:
            remaining = cycle_deadline_monotonic - time.monotonic()
            if remaining <= 0:
                return {
                    "status": "extract_failed",
                    "track": track,
                    "data_version": cfg["data_version"],
                    "reason": "CYCLE_DEADLINE_EXCEEDED",
                    "stages": stages,
                    "snapshots_inserted": 0,
                }
            extract_timeout_seconds = min(float(extract_timeout_seconds), remaining)
        extract = runner(
            [
                _conda_python(),
                str(paths.extract_script),
                "--grib-path", str(output_path),
                "--track", cfg["ingest_track"],
                "--output-root", str(coordinate_raw_root),
                "--manifest-path", str(coordinate_manifest),
            ],
            label=f"extract_{track}",
            timeout=extract_timeout_seconds,
        )
        extract["coordinate_manifest_path"] = str(coordinate_manifest)
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
    if (
        cycle_deadline_monotonic is not None
        and time.monotonic() >= cycle_deadline_monotonic
    ):
        return {
            "status": "extract_failed",
            "track": track,
            "data_version": cfg["data_version"],
            "reason": "CYCLE_DEADLINE_EXCEEDED",
            "stages": stages,
            "snapshots_inserted": 0,
        }
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
    retention_plan: _RawRetentionPlan | None = None
    retention_summary: dict[str, object] = {"status": "NOT_PLANNED"}
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
                        raw_root=coordinate_raw_root,
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
                            dataset_id=cfg["data_version"],
                            coordinate_manifest_sha=manifest_sha,
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
            try:
                retention_plan = _plan_decoded_open_data_raw_retention(
                    conn,
                    raw_root=paths.raw_root,
                    reference_date=now.date(),
                )
            except Exception as exc:  # noqa: BLE001 - retention must fail closed
                retention_summary = {
                    "status": "PLAN_ERROR",
                    "error": f"{type(exc).__name__}:{exc}",
                }
                logger.warning("ecmwf_open_data raw retention plan failed: %s", exc)
        finally:
            if own_conn:
                conn.close()

    # Large raw files are removed only after the canonical transaction commits
    # and the BULK writer lock is released. A retention failure never changes
    # the already-truthful source-run result or blocks the next probability job.
    if retention_plan is not None:
        retention_summary = _apply_decoded_open_data_raw_retention(retention_plan)
        logger.info("ecmwf_open_data raw_retention=%s", retention_summary)

    # Same placement rationale as the raw retention above: after the canonical
    # commit and outside the BULK writer lock, so filesystem cleanup can never
    # stall probability writers, and a cleanup failure never rewrites an
    # already-truthful source_run result. Only runs for an ok ingest — a failed
    # cycle's predecessor view is the one a retry would still want.
    if status == "ok":
        try:
            manifest_prune_summary = _prune_superseded_coordinate_manifest_cycles(
                coordinate_raw_root=coordinate_raw_root,
                extract_subdir=cfg["extract_subdir"],
                keep_run_date=cycle_date,
                keep_run_hour=cycle_hour,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must fail soft
            logger.warning(
                "ecmwf_open_data coordinate_manifest_prune failed: %s: %s",
                type(exc).__name__,
                exc,
            )
        else:
            logger.info(
                "ecmwf_open_data coordinate_manifest_prune=%s", manifest_prune_summary
            )

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
        "coordinate_manifest_sha": manifest_sha,
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
        "raw_retention": retention_summary,
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
