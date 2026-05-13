# Created: prior; restructured 2026-05-01
# Last reused or audited: 2026-05-12
# Authority basis: architect D1 (ECMWF throttle), AGENTS.md money path
#   Prior: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md
#   ECMWF Open Data has ~6-8h latency (vs. TIGGE's 48h public embargo) so it
#   is the live-trading source for same-day forecasts. Rows must land in
#   ensemble_snapshots_v2 with the canonical local-calendar-day data_version
#   so calibration / day0 / opening_hunt readers can consume them alongside
#   TIGGE archive rows via the data_version priority list.
"""Collect ECMWF Open Data ENS member vectors into ensemble_snapshots_v2.

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
   asserts the data_version is allow-listed, and writes the row to
   ``ensemble_snapshots_v2`` with manifest_hash + provenance_json + members_unit.

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
import os
import subprocess
import sys
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from src.config import PROJECT_ROOT, runtime_cities_by_name
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
)
from src.data.forecast_target_contract import (
    build_forecast_target_scope,
    evaluate_horizon_coverage,
    evaluate_producer_coverage,
)
from src.data.producer_readiness import build_producer_readiness_for_scope
from src.data.forecast_source_registry import gate_source, gate_source_role
from src.data.release_calendar import FetchDecision, select_source_run_for_target_horizon
from src.state.db import get_forecasts_connection as get_connection, ZEUS_FORECASTS_DB_PATH
from src.state.db_writer_lock import WriteClass, db_writer_lock
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
# DOWNLOAD_SCRIPT deleted 2026-05-11: replaced by in-process parallel SDK fetch
# (see PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md)
EXTRACT_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "extract_open_ens_localday.py"
INGEST_SCRIPT_DIR = PROJECT_ROOT / "scripts"

# ECMWF Open Data ENS dissemination grid (enfo cf/pf, mx2t3/mn2t3/2t):
#   0–144h by 3h, then 150–360h by 6h.
# Note: the underlying IFS model produces hourly steps 0–90h and 3h steps
#       93–144h (per https://www.ecmwf.int/en/forecasts/datasets/set-iii),
#       but Open Data subsamples to the 3h/6h grid above. Hourly steps are
#       only available via MARS, which Zeus does not use.
# Period-aligned params: mx2t3/mn2t3 valid at every disseminated step;
#       mx2t6/mn2t6 (deprecated 2026-05-07) were valid only at 6h multiples.
# We request 3h steps through 144h, then 6h steps through 282h.
# 282h is the LOW D+10 authority ceiling from
#   architecture/zeus_grid_resolution_authority_2026_05_07.yaml (LOW 282h horizon).
# Only requesting disseminated steps avoids silent "No index entries" fetch failures.
#
# Authority: architecture/zeus_grid_resolution_authority_2026_05_07.yaml A1+3h (stride)
#            LOW 282h horizon (covers UTC-positive cities at D+10 boundary)
# ECMWF Open Data `enfo` stream no longer serves mx2t6/mn2t6 (6h aggregations).
# The stream now serves mx2t3/mn2t3 (3h aggregations) as the native product.
# We fetch 3h-native and let calibration learn the 3h→6h envelope mapping
# downstream. We do NOT re-aggregate to 6h at fetch time (forbidden_patterns).
STEP_HOURS = (
    list(range(3, 147, 3))    # 3, 6, …, 144 — 3h stride (A1+3h native grid)
    + list(range(150, 285, 6))  # 150, 156, …, 282 — 6h stride (published ENS beyond 144h)
)
# Authority: source_release_calendar.yaml ecmwf_open_data live_max_step_hours=282.
# Grid: ECMWF Open Data ENS serves 3h steps 0–144h and 6h steps 150–360h for cf/pf.
# 282h covers D+10 for all cities including UTC+12 (max required step ≈ 252h).
# Raised from 276 → 282 (fix/#134) to unblock 100 BLOCKED readiness rows for
# 2026-05-13/14 requiring steps 228–252h. Closes #134.

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
FORECAST_SOURCE_ROLE = "diagnostic"
MODEL_VERSION = "ecmwf_open_data"

# ECMWF Open Data is replicated across multiple mirrors. AWS empirically 3× faster
# than ecmwf direct portal (which has a 500-simultaneous-connection limit per
# ECMWF docs). Order: aws (CDN, fastest), google (CDN, backup), ecmwf (origin, fallback).
# 404 on any mirror means upstream hasn't released — no point rotating (mirrors sync
# within 5s of origin, measured 2026-05-11).
_DOWNLOAD_SOURCES: tuple[str, ...] = ("aws", "google", "ecmwf")

# ---------------------------------------------------------------------------
# Token-bucket rate limiter (D1 throttle antibody — 2026-05-12)
# ---------------------------------------------------------------------------
# AWS S3 / ECMWF multiurl returns HTTP 503 Slow Down when request burst rate
# exceeds provider limits. The token bucket is a module-level singleton shared
# across ALL worker threads and BOTH tracks (mx2t6_high + mn2t6_low run at
# minute=30 and minute=35 respectively via ingest_main.py, so up to
# 2 × _DOWNLOAD_MAX_WORKERS fetches can be in flight simultaneously).
#
# Primary throttle mechanism: the token bucket caps sustained throughput at
# ZEUS_ECMWF_RPS requests/sec regardless of worker count. Worker reduction
# (2 vs 5) reduces BURST, not sustained rate — it is secondary.
#
# DO NOT revert workers to 1: 2 workers + 4 rps bucket is the tested and
# operator-approved operating point. The prior revert to 1 was a linter
# misread of the antibody comment. See architect D1 and commit message.

class _TokenBucket:
    """Simple token-bucket rate limiter (thread-safe).

    Fills at ``rate`` tokens/sec; each ``acquire()`` consumes one token,
    sleeping until a token is available.  Implemented as a leaky-bucket
    gate (refill on demand) rather than a background thread so there is no
    daemon thread to manage across fork/test boundaries.
    """

    def __init__(self, rate: float) -> None:
        self._rate = rate  # tokens per second
        self._lock = threading.Lock()
        self._tokens: float = rate  # start full so first request is instant
        self._last_refill: float = time.monotonic()

    def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate
        time.sleep(wait)
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now
            self._tokens = max(0.0, self._tokens - 1.0)


_DOWNLOAD_RPS: float = float(os.environ.get("ZEUS_ECMWF_RPS", "4.0"))
_fetch_bucket: _TokenBucket = _TokenBucket(_DOWNLOAD_RPS)

# ---------------------------------------------------------------------------
# Parallel-fetch constants (antibody-style: no call-site kwargs).
# Single-writer antibody: SQLite writes are PROHIBITED inside worker threads —
# HTTP fetch only; all DB writes happen on the main thread after futures complete.
# ---------------------------------------------------------------------------
# Workers=2 (not 1, not 5): 2 workers reduces concurrency burst while keeping
# pipeline throughput reasonable. The token bucket (_fetch_bucket, 4 rps) is
# the PRIMARY throttle against AWS S3 503 Slow Down; worker count is secondary.
# Env override: ZEUS_ECMWF_MAX_WORKERS (operator-set; survives linter audits).
_DOWNLOAD_MAX_WORKERS: int = int(os.environ.get("ZEUS_ECMWF_MAX_WORKERS", "2"))
_PER_STEP_TIMEOUT_SECONDS: int = 90
_PER_STEP_MAX_RETRIES: int = int(os.environ.get("ZEUS_ECMWF_PER_STEP_RETRIES", "2"))
_PER_STEP_RETRY_AFTER: int = int(os.environ.get("ZEUS_ECMWF_PER_STEP_RETRY_AFTER", "5"))
# 404 → NOT_RELEASED (no retry); all others below trigger retry then failover.
_RETRYABLE_HTTP: frozenset[int] = frozenset({500, 502, 503, 504, 408, 429})


def _conda_python() -> str:
    """Path to the conda interpreter that has ``ecmwf.opendata`` + eccodes installed.

    Falls back to ``sys.executable``; tests that mock the runner never invoke this.
    """
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


def _snapshot_rows_for_source_run(conn, *, source_run_id: str, data_version: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM ensemble_snapshots_v2
            WHERE source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
              AND data_version = ?
            ORDER BY city, target_date, temperature_metric, snapshot_id
            """,
            (SOURCE_ID, "ensemble_snapshots_v2_db_reader", source_run_id, data_version),
        ).fetchall()
    ]


def _observed_steps_for_snapshot(*, required_steps: tuple[int, ...], step_horizon_hours: object) -> tuple[int, ...]:
    try:
        horizon = float(step_horizon_hours)
    except (TypeError, ValueError):
        return ()
    return tuple(step for step in required_steps if step <= horizon)


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
    download_observed_steps: list[int] | None = None,
    download_partial_run: bool | None = None,
    download_reason_code: str | None = None,
) -> dict[str, int | str | None]:
    """Write source_run + coverage rows for a completed ingest cycle.

    download_observed_steps: when provided (PARTIAL cycles), overrides the
    ingest-derived observed_steps approximation (line 309 below) with the
    ground-truth step list from the download phase. This ensures
    forecast_target_contract.evaluate_producer_coverage:184
    (missing_steps = expected - observed) receives the accurate per-step set.
    """
    rows = _snapshot_rows_for_source_run(
        conn,
        source_run_id=source_run_id,
        data_version=data_version,
    )
    source_run_status, source_run_completeness, partial_run, reason_code = _source_run_outcome(summary, status)
    observed_member_counts = [len(_json_list(row.get("members_json"))) for row in rows]
    observed_members = min(observed_member_counts) if observed_member_counts else 0
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
            partial_run = download_partial_run
            source_run_completeness = "PARTIAL" if partial_run else source_run_completeness
            source_run_status = "PARTIAL" if partial_run else source_run_status
        if download_reason_code is not None:
            reason_code = download_reason_code
    else:
        observed_steps = [step for step in STEP_HOURS if observed_step_horizons and step <= min(observed_step_horizons)]

    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id=SOURCE_ID,
        track=forecast_track,
        release_calendar_key=release_calendar_key,
        source_cycle_time=source_cycle_time,
        source_issue_time=source_cycle_time,
        source_release_time=source_release_time,
        source_available_at=source_release_time,
        fetch_started_at=computed_at,
        fetch_finished_at=computed_at,
        captured_at=computed_at,
        imported_at=computed_at,
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
        status=source_run_status,
        reason_code=reason_code,
    )

    cities_by_name = runtime_cities_by_name()
    coverage_written = 0
    readiness_written = 0
    expires_at = computed_at + timedelta(hours=24)
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
        )
        observed_members_for_scope = len(_json_list(row.get("members_json")))
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
            source_transport="ensemble_snapshots_v2_db_reader",
            source_run_status=source_run_status,
            source_run_completeness=source_run_completeness,
            snapshot_target_date=target_local_date,
            snapshot_metric=str(row["temperature_metric"]),
            expected_steps=scope.required_step_hours,
            observed_steps=observed_steps_for_scope,
            expected_members=51,
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
        live_eligible = (
            source_run_status == "SUCCESS"
            and source_run_completeness == "COMPLETE"
            and horizon_decision.status == "LIVE_ELIGIBLE"
            and coverage_decision.status == "LIVE_ELIGIBLE"
            and snapshot_window_start == scope.target_window_start_utc
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
            coverage_reason = next(
                (reason for reason in reason_codes if reason != "FUTURE_TARGET_DATE_COVERED"),
                "FUTURE_TARGET_DATE_COVERAGE_PARTIAL",
            )

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
            source_transport="ensemble_snapshots_v2_db_reader",
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
            expected_members=51,
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
            source_transport="ensemble_snapshots_v2_db_reader",
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
    canonical = output_dir / f".step{step:03d}_{param}.grib2"
    partial   = canonical.with_suffix(".grib2.partial")
    if canonical.exists() and canonical.stat().st_size > 0:
        return ("OK", canonical)   # resume: already fetched in a prior attempt

    from ecmwf.opendata import Client  # imported here: conda env only on main interpreter

    last_err: str | None = None
    for mirror in mirrors:
        for attempt in range(_PER_STEP_MAX_RETRIES):
            try:
                _fetch_bucket.acquire()  # D1: token-bucket gate (4 rps shared across all workers)
                Client(source=mirror).retrieve(
                    date=int(cycle_date.strftime("%Y%m%d")),
                    time=cycle_hour,
                    stream="enfo",
                    type=["cf", "pf"],
                    step=[step],
                    param=[param],
                    target=str(partial),
                )
                os.replace(str(partial), str(canonical))   # atomic rename
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
                    time.sleep(_PER_STEP_RETRY_AFTER)
                    continue
                last_err = f"HTTP_{code}_mirror_{mirror}"
                break   # non-retryable; try next mirror
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = f"NET_{type(exc).__name__}_mirror_{mirror}_attempt_{attempt}"
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


def _concat_steps(ok_steps: list[int], param: str, output_dir: Path, output_path: Path) -> None:
    """Concatenate per-step GRIB2 files into the canonical output_path.

    GRIB2 is self-delimiting; step order does not affect extractor correctness
    (REL-1, REL-6). We write in ascending step order for determinism.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as out:
        for step in sorted(ok_steps):
            step_file = output_dir / f".step{step:03d}_{param}.grib2"
            if step_file.exists():
                out.write(step_file.read_bytes())


def _run_subprocess(args: list[str], *, label: str, timeout: int) -> dict:
    logger.info("ecmwf_open_data %s: %s", label, " ".join(args[:6]) + " ...")
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
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

    if not skip_download:
        fetch_fn = _fetch_impl or _fetch_one_step
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Dispatch one future per step.  _DOWNLOAD_MAX_WORKERS=5 is a module
        # constant; no call-site kwarg (antibody: makes per-step parallelism
        # category structurally module-owned, not caller-configured).
        # Single-writer antibody: fetch_fn does HTTP only; no SQLite writes.
        tasks = [(s, cfg["open_data_param"]) for s in STEP_HOURS]
        results: dict[int, tuple[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_MAX_WORKERS) as ex:
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
                for s, p in tasks
            }
            for fut in as_completed(fut2step):
                step = fut2step[fut]
                try:
                    results[step] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    results[step] = ("FAILED", f"UNCAUGHT_{type(exc).__name__}: {exc}")

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
                        source_available_at=source_release_time,
                        fetch_started_at=computed_at,
                        fetch_finished_at=computed_at,
                        captured_at=computed_at,
                        imported_at=computed_at,
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
                        source_available_at=source_release_time,
                        fetch_started_at=computed_at,
                        fetch_finished_at=computed_at,
                        captured_at=computed_at,
                        imported_at=computed_at,
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
        _concat_steps(ok_steps, cfg["open_data_param"], output_dir, output_path)

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
    with _lock_ctx:
        if own_conn:
            conn = get_connection()
        try:
            # Make scripts/ importable so we can call ingest_track.
            if str(INGEST_SCRIPT_DIR) not in sys.path:
                sys.path.insert(0, str(INGEST_SCRIPT_DIR))
            from ingest_grib_to_snapshots import SourceRunContext, ingest_track as _ingest  # type: ignore
            from src.state.db import assert_schema_current_forecasts

            assert_schema_current_forecasts(conn)
            # The opendata extract writes JSON files to a different subdir than
            # TIGGE — reuse the same ingester by passing the parent directory and
            # the matching track name, and override the json_subdir lookup via the
            # _TRACK_CONFIGS dict. Cleanest in-process integration: temporarily
            # rebind the json_subdir for this call.
            import ingest_grib_to_snapshots as _module  # type: ignore

            original_subdir = _module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"]
            _module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = cfg["extract_subdir"]
            try:
                summary = _ingest(
                    track=cfg["ingest_track"],
                    json_root=FIFTY_ONE_ROOT / "raw",
                    conn=conn,
                    date_from=None,
                    date_to=None,
                    cities=None,
                    overwrite=True,
                    require_files=False,
                    source_run_context=SourceRunContext(
                        source_id=SOURCE_ID,
                        source_transport="ensemble_snapshots_v2_db_reader",
                        source_run_id=source_run_id,
                        release_calendar_key=release_calendar_key,
                        source_cycle_time=source_cycle_time,
                        source_release_time=source_release_time,
                        source_available_at=source_release_time,
                    ),
                )
            finally:
                _module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = original_subdir
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
                # Pass download ground-truth so source_run.observed_steps_json
                # reflects actual fetched steps, not an ingest-derived approximation.
                # evaluate_producer_coverage:184 uses this for per-step MISSING detection.
                download_observed_steps=download_observed_steps,
                download_partial_run=_partial_cycle if download_observed_steps is not None else None,
                download_reason_code=_download_reason_code,
            )
            conn.commit()
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

        SELECT ... FROM ensemble_snapshots_v2
         WHERE temperature_metric = ?
           AND data_version IN (<one placeholder per priority entry>)
         ORDER BY CASE data_version WHEN ? THEN 0 ELSE 1 END, available_at DESC

    where the bound parameters are the priority tuple followed by the
    priority tuple's first element again.
    """
    if temperature_metric == "high":
        return (ECMWF_OPENDATA_HIGH_DATA_VERSION, "tigge_mx2t6_local_calendar_day_max_v1")
    if temperature_metric == "low":
        return (
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            "tigge_mn2t6_local_calendar_day_min_v1",
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
