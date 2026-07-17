# Created: 2026-05-14
# Last reused/audited: 2026-07-16
# Authority basis: docs/archive/2026-Q2/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.1, section 6.2, and section 8 Phase 4; Phase 6 durable work journaling; docs/archive/2026-Q2/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md source-health gate; a0d51d480b507f324 root-cause + docs/operations/live_review_may23.md (ECMWF 00z ingest schedule fix).
"""Dedicated OpenData live forecast producer daemon.

This module owns the ECMWF OpenData live forecast scheduler and the OpenData-only
source-health heartbeat required to prove forecast freshness. It does not
schedule TIGGE archive backfill, calibration refit, settlement truth, market
scan, risk, execution, evaluator, or venue work.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("zeus.forecast_live")

_scheduler: Any | None = None

# SIGTERM-unif (WAVE-4): captured at module load so the forensic elapsed
# computed in _graceful_shutdown matches what src/main.py and
# src/riskguard/riskguard.py emit. See WAVE3_BATCH_C_PER_FINDING_ACCOUNTING.md
# carry-forward #5.
_PROCESS_START = time.monotonic()


def _git_head_at_boot() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


_PROCESS_GIT_HEAD = _git_head_at_boot()

_FORECAST_BOOT_REQUIRED_SCHEMA: dict[str, frozenset[str]] = {
    "forecast_posteriors": frozenset(
        {
            "city",
            "target_date",
            "temperature_metric",
            "source_cycle_time",
            "q_json",
            "q_lcb_json",
            "runtime_layer",
            "recorded_at",
        }
    ),
    "raw_forecast_artifacts": frozenset(
        {
            "source_id",
            "product_id",
            "data_version",
            "source_cycle_time",
            "captured_at",
            "artifact_path",
        }
    ),
    "raw_model_forecasts": frozenset(
        {
            "model",
            "city",
            "target_date",
            "metric",
            "source_cycle_time",
            "source_available_at",
            "forecast_value_c",
            "endpoint",
        }
    ),
    "readiness_state": frozenset({"strategy_key", "status", "dependency_json", "provenance_json"}),
    "source_run_coverage": frozenset(
        {"source_run_id", "source_id", "city", "target_local_date", "temperature_metric"}
    ),
    "job_run": frozenset({"job_run_id"}),
    "cycle_advance_enqueues": frozenset({"seed_file", "held_position", "enqueued_at"}),
}

_FORECAST_BOOT_REQUIRED_INDEXES: frozenset[str] = frozenset(
    {
        "idx_forecast_posteriors_live_family_cycle",
        "idx_raw_model_forecasts_endpoint_family_cycle_members",
    }
)

FORECAST_LIVE_DAILY_HIGH_JOB_ID = "forecast_live_opendata_daily_mx2t6"       # 00z trigger
FORECAST_LIVE_DAILY_HIGH_12Z_JOB_ID = "forecast_live_opendata_daily_mx2t6_12z"  # 12z trigger
FORECAST_LIVE_DAILY_LOW_JOB_ID = "forecast_live_opendata_daily_mn2t6"        # 00z trigger
FORECAST_LIVE_DAILY_LOW_12Z_JOB_ID = "forecast_live_opendata_daily_mn2t6_12z"   # 12z trigger
FORECAST_LIVE_STARTUP_JOB_ID = "forecast_live_opendata_startup_catch_up"
FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID = "forecast_live_opendata_safe_cycle_poll"
FORECAST_LIVE_HEARTBEAT_JOB_ID = "forecast_live_heartbeat"
FORECAST_LIVE_SOURCE_HEALTH_JOB_ID = "forecast_live_source_health_probe"
# Operator Point-1 directive 2026-06-08: BAYES_PRECISION_FUSION/replacement forecast PRODUCTION jobs moved here
# from the live-trading daemon. Current live production fetches OpenMeteo anchor inputs on
# this data daemon, never on the trading process. These ids are registered OUTSIDE the OpenData registry
# job-set (after the registry/cutover/legacy scheduler is built), on their own dedicated
# executor lane, so they ALWAYS run (they ARE the replacement production — under the OpenData
# cutover they MUST run, not be filtered out) and never contend with the heartbeat or OpenData
# lanes. The functions themselves are live-authority gated (no-op when trade authority is off).
REPLACEMENT_FORECAST_DOWNLOAD_JOB_ID = "replacement_forecast_download"
REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID = "replacement_forecast_live_materialize"
REPLACEMENT_FORECAST_DISCOVERY_JOB_ID = "replacement_forecast_live_discovery"
REPLACEMENT_FORECAST_STARTUP_JOB_ID = "replacement_forecast_download_startup_catch_up"
REPLACEMENT_AVAILABILITY_POLL_JOB_ID = "replacement_cycle_availability_poll"
ANCHOR_META_CROSS_CHECK_JOB_ID = "anchor_meta_stamp_cross_check"
REPLACEMENT_FORECAST_EXECUTOR_LANE = "replacement_production"
# SEPARATE lane for the heavy download (publish-time cron + boot catch-up). The download
# runs for tens of minutes (8 Open-Meteo models x all cities; slowed further by fail-soft
# 400-retries on short-range models). On a SHARED max_workers=1 lane it serialized AHEAD of
# the 5-min materialize, starving readiness production for the whole download (observed
# 2026-06-08: a 75-min download blocked the materialize so BAYES_PRECISION_FUSION readiness aged out -> the
# replacement-forecast hook BLOCKED every live FSR event -> zero trades). A dedicated
# download lane makes "a long download starves the materialize" structurally impossible:
# the materialize keeps its own worker and refreshes readiness every interval regardless of
# download duration.
REPLACEMENT_FORECAST_DOWNLOAD_EXECUTOR_LANE = "replacement_download"
_replacement_forecast_last_discovery_revision: tuple[object, ...] | None = None
FORECAST_LIVE_HEARTBEAT_SECONDS = 30
FORECAST_LIVE_SAFE_CYCLE_POLL_SECONDS = 5 * 60
FORECAST_LIVE_SOURCE_HEALTH_SECONDS = 10 * 60
FORECAST_LIVE_SOURCE_HEALTH_SOURCE_IDS = frozenset({"ecmwf_open_data"})
_CURRENT_SOURCE_CYCLE_STATUSES = frozenset({"SUCCESS"})
# Why SUCCESS-only: ECMWF Open Data disseminates a cycle incrementally over ~10h.
# A PARTIAL journal at T+8h means more steps may still publish; treating it as
# "already journaled" would lock the daemon out of those later steps and force
# every entry into MISSING_REQUIRED_STEPS until the next 00Z/12Z cycle —
# exactly the live blackout observed 2026-05-18→2026-05-19. Idempotent under
# refetch: job_run UPSERTs on `job_run_key` (job_name + scheduled_for + source_id
# + track + release_calendar_key), source_run on its `source_run_id` PK, and
# source_run_coverage / readiness_state on composite identity keys — all stable
# across refetches of the same cycle, so a no-op refetch just rewrites rows.

FORECAST_LIVE_JOB_IDS = frozenset(
    {
        FORECAST_LIVE_DAILY_HIGH_JOB_ID,
        FORECAST_LIVE_DAILY_HIGH_12Z_JOB_ID,
        FORECAST_LIVE_DAILY_LOW_JOB_ID,
        FORECAST_LIVE_DAILY_LOW_12Z_JOB_ID,
        FORECAST_LIVE_STARTUP_JOB_ID,
        FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID,
        FORECAST_LIVE_HEARTBEAT_JOB_ID,
        FORECAST_LIVE_SOURCE_HEALTH_JOB_ID,
    }
)
FORECAST_LIVE_WORK_JOB_NAME_BY_TRACK = {
    "mx2t6_high": "forecast_live_opendata_mx2t6_high",
    "mn2t6_low": "forecast_live_opendata_mn2t6_low",
}

_TRUTHFUL_FAIL_STATUSES = frozenset(
    {
        "download_failed",
        "empty_ingest",
        "extract_failed",
        "skipped_lock_held",
        "bad_target_date",
    }
)


def _active_forecast_live_job_ids() -> frozenset[str]:
    return FORECAST_LIVE_JOB_IDS


def _heartbeat_job_ids() -> frozenset[str]:
    if _scheduler is not None:
        try:
            job_ids = frozenset(str(job.id) for job in _scheduler.get_jobs())
        except Exception as exc:
            logger.warning("forecast-live heartbeat scheduler job snapshot failed: %s", exc)
        else:
            if job_ids:
                return job_ids
    return _active_forecast_live_job_ids()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write_forecast_live_heartbeat(
    *,
    heartbeat_path: Path | None = None,
    status: str = "alive",
    now_utc: datetime | None = None,
) -> None:
    """Write the forecast-live process heartbeat atomically."""
    from src.config import state_path

    now = (now_utc or _utcnow()).astimezone(timezone.utc)
    path = heartbeat_path or state_path("forecast-live-heartbeat.json")
    payload = {
        "daemon": "forecast-live",
        "status": status,
        "timestamp": now.isoformat(),
        "written_at": now.isoformat(),
        "pid": os.getpid(),
        "git_head": _PROCESS_GIT_HEAD,
        "jobs": sorted(_heartbeat_job_ids()),
        "cadence_seconds": FORECAST_LIVE_HEARTBEAT_SECONDS,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, sort_keys=True)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.error("forecast-live heartbeat write failed: %s", exc)


def _heartbeat_tick() -> None:
    _write_forecast_live_heartbeat()


def _forecast_boot_schema_ready(conn: Any) -> bool:
    """Fast live-boot schema admission for the forecast daemon.

    ``init_schema_forecasts`` remains the repair path for missing schema. On an
    already-migrated 36GB live forecast DB, running the full idempotent DDL
    sequence before heartbeat can block the daemon from reaching scheduler
    readiness. This check admits only the tables/columns the forecast-live jobs
    need before skipping the full ensure.
    """

    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        for table, required_columns in _FORECAST_BOOT_REQUIRED_SCHEMA.items():
            if table not in tables:
                return False
            columns = {
                str(row["name"] if hasattr(row, "keys") else row[1])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not required_columns.issubset(columns):
                return False
            if "trade_authority_status" in columns:
                return False
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        if not _FORECAST_BOOT_REQUIRED_INDEXES.issubset(indexes):
            return False
    except Exception:
        return False
    return True


def _graceful_shutdown(signum, frame) -> None:
    """SIGTERM handler.

    Emits two log lines:
    1. The legacy `received SIGTERM` line (INFO → .log) — preserves
       backward compat with operator grep tooling installed before
       WAVE-4 SIGTERM-unif.
    2. The unified `SIGTERM_RECEIVED pid=... ppid=... elapsed=...s`
       token (ERROR → .err) — same forensic shape that src/main.py,
       src/riskguard/riskguard.py and src/control/heartbeat_supervisor.py
       emit, so a single grep across all 5 daemons returns parity hits.
    """
    logger.info("forecast-live daemon received SIGTERM; shutting down scheduler")
    logger.error(
        "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
        os.getpid(), os.getppid(), int(time.monotonic() - _PROCESS_START),
    )
    _write_forecast_live_heartbeat(status="stopping")
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=True)
        except Exception as exc:
            logger.warning("forecast-live scheduler shutdown error: %s", exc)
    sys.exit(0)


def _classify_result(result) -> tuple[bool, str | None]:
    if not isinstance(result, dict):
        return False, None
    status = str(result.get("status", "")).lower()
    if status in _TRUTHFUL_FAIL_STATUSES:
        return True, status + (": " + str(result.get("error")) if result.get("error") else "")
    if status in {"paused_by_control_plane", "noop_no_dates"}:
        return False, None
    stages = result.get("stages") or []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("ok") is False:
            return True, f"stage_failed:{stage.get('label', '?')}:{stage.get('error', '?')}"
    tracks = result.get("tracks") or {}
    if isinstance(tracks, dict):
        for track_result in tracks.values():
            failed, reason = _classify_result(track_result)
            if failed:
                return True, reason
    return False, None


def _scheduler_job(job_name: str):
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                from src.observability.scheduler_health import _write_scheduler_health

                _write_scheduler_health(job_name, failed=False, started=True)
                result = fn(*args, **kwargs)
                failed, reason = _classify_result(result)
                _write_scheduler_health(job_name, failed=failed, reason=reason)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                try:
                    from src.observability.scheduler_health import _write_scheduler_health

                    _write_scheduler_health(job_name, failed=True, reason=str(exc))
                except Exception:
                    pass

        return _wrapper

    return _decorator


@_scheduler_job(FORECAST_LIVE_SOURCE_HEALTH_JOB_ID)
def _source_health_probe_tick(
    *,
    _locks_dir_override: Path | None = None,
    _probe_sources: Callable[..., dict] | None = None,
    _write_source_health: Callable[..., Path] | None = None,
    _state_path: Callable[[str], Path] | None = None,
) -> dict[str, object]:
    """Refresh source_health.json for the OpenData source owned by forecast-live."""
    from src.config import state_path
    from src.data.dual_run_lock import acquire_lock
    from src.data.source_health_probe import probe_sources, write_source_health

    resolve_state_path = _state_path or state_path
    with acquire_lock("source_health", _locks_dir_override=_locks_dir_override) as acquired:
        if not acquired:
            logger.info("forecast-live source_health_probe skipped_lock_held")
            return {"status": "skipped_lock_held", "source": "source_health"}

        prior_state: dict = {}
        try:
            existing = resolve_state_path("source_health.json")
            if Path(existing).exists():
                data = json.loads(Path(existing).read_text())
                prior_state = data.get("sources", {})
        except Exception:
            prior_state = {}

        probe = _probe_sources or probe_sources
        writer = _write_source_health or write_source_health
        updated_sources = probe(
            FORECAST_LIVE_SOURCE_HEALTH_SOURCE_IDS,
            10.0,
            _prior_state=prior_state,
        )
        results = dict(prior_state)
        results.update(updated_sources)
        out_path = writer(results)
        logger.info(
            "forecast-live source health probe complete: updated=%s preserved=%d",
            sorted(updated_sources),
            max(0, len(results) - len(updated_sources)),
        )
        return {
            "status": "ok",
            "source": "source_health",
            "sources": len(updated_sources),
            "updated_sources": sorted(updated_sources),
            "path": str(out_path),
        }


def _is_source_paused(source_id: str) -> bool:
    try:
        from src.control.control_plane import read_ingest_control_state

        state = read_ingest_control_state()
        return source_id in state.get("paused_sources", set())
    except Exception as exc:
        logger.warning("forecast-live pause check failed for %s: %s", source_id, exc)
        return False


def _forecast_work_identity(track: str, *, now_utc: datetime) -> dict[str, object]:
    from src.data.ecmwf_open_data import SOURCE_ID, STEP_HOURS, TRACKS
    from src.data.release_calendar import (
        cycle_profile_for_hour,
        get_entry,
        select_source_run_for_target_horizon,
    )

    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    decision, metadata = select_source_run_for_target_horizon(
        now_utc=now_utc,
        source_id=SOURCE_ID,
        track=track,
        required_max_step_hours=max(STEP_HOURS),
    )
    selected_cycle = metadata.get("selected_cycle_time")
    if not isinstance(selected_cycle, datetime):
        selected_cycle = now_utc
    horizon_profile = metadata.get("horizon_profile")
    if not horizon_profile:
        entry = get_entry(SOURCE_ID, track)
        profile = cycle_profile_for_hour(entry, selected_cycle.hour) if entry is not None else None
        horizon_profile = profile.horizon_profile if profile is not None else None
    horizon_profile = str(horizon_profile or "unknown")
    return {
        "decision": decision,
        "metadata": metadata,
        "job_name": FORECAST_LIVE_WORK_JOB_NAME_BY_TRACK[track],
        "source_id": SOURCE_ID,
        "track": track,
        "scheduled_for": selected_cycle.astimezone(timezone.utc),
        "release_calendar_key": f"{SOURCE_ID}:{track}:{horizon_profile}",
        "safe_fetch_not_before": metadata.get("next_safe_fetch_at"),
    }


def _job_run_id(identity: dict[str, object]) -> str:
    scheduled_for = identity["scheduled_for"]
    if isinstance(scheduled_for, datetime):
        scheduled = scheduled_for.isoformat()
    else:
        scheduled = str(scheduled_for)
    return (
        f"{identity['job_name']}:{identity['source_id']}:{identity['track']}:"
        f"{scheduled}:{identity['release_calendar_key']}"
    )


def _job_status_from_result(result: dict | None) -> tuple[str, str | None, int, int]:
    if not isinstance(result, dict):
        return "FAILED", "RESULT_MISSING", 0, 1
    status = str(result.get("status", "")).lower()
    source_run_status = str(result.get("source_run_status", "")).upper()
    source_run_completeness = str(result.get("source_run_completeness", "")).upper()
    rows_written = int(result.get("snapshots_inserted") or result.get("coverage_written") or 0)
    if source_run_status == "PARTIAL" or source_run_completeness == "PARTIAL":
        return "PARTIAL", str(result.get("reason") or result.get("reason_code") or "PARTIAL"), rows_written, 0
    if source_run_status == "FAILED" or source_run_completeness == "MISSING":
        return "FAILED", str(result.get("error") or result.get("reason_code") or "FAILED"), rows_written, 1
    if status == "ok":
        return "SUCCESS", None, rows_written, 0
    if status == "partial":
        return "PARTIAL", str(result.get("reason") or "PARTIAL"), rows_written, 0
    if status == "skipped_not_released":
        return "SKIPPED_NOT_RELEASED", str(result.get("reason") or "SKIPPED_NOT_RELEASED"), 0, 0
    if status == "skipped_lock_held":
        return "SKIPPED_LOCK_HELD", "SKIPPED_LOCK_HELD", 0, 0
    if status in _TRUTHFUL_FAIL_STATUSES:
        return "FAILED", str(result.get("error") or result.get("reason") or status), rows_written, 1
    failed, reason = _classify_result(result)
    if failed:
        return "FAILED", reason or status or "FAILED", rows_written, 1
    return "SUCCESS", None, rows_written, 0


def _write_job_run(
    conn,
    *,
    identity: dict[str, object],
    status: str,
    now_utc: datetime,
    result: dict | None = None,
    reason_code: str | None = None,
    started_at: datetime | None = None,
    lock_acquired_at: datetime | None = None,
    lock_key: str = "opendata_live_forecast",
) -> None:
    from src.state.job_run_repo import write_job_run

    if result is None:
        result = {}
    rows_written = int(result.get("snapshots_inserted") or result.get("coverage_written") or 0)
    rows_failed_raw = result.get("rows_failed")
    rows_failed = int(rows_failed_raw) if rows_failed_raw is not None else (1 if status == "FAILED" else 0)
    write_job_run(
        conn,
        job_run_id=_job_run_id(identity),
        job_name=str(identity["job_name"]),
        plane="forecast",
        scheduled_for=identity["scheduled_for"],
        started_at=started_at,
        finished_at=now_utc if status != "RUNNING" else None,
        lock_key=lock_key,
        lock_acquired_at=lock_acquired_at,
        status=status,
        reason_code=reason_code,
        rows_written=rows_written,
        rows_failed=rows_failed,
        source_run_id=result.get("source_run_id") if isinstance(result.get("source_run_id"), str) else None,
        source_id=str(identity["source_id"]),
        track=str(identity["track"]),
        release_calendar_key=(
            result.get("release_calendar_key")
            if isinstance(result.get("release_calendar_key"), str)
            else str(identity["release_calendar_key"])
        ),
        safe_fetch_not_before=identity.get("safe_fetch_not_before"),
        expected_scope_json={
            "selection": identity.get("metadata"),
            "release_calendar_key": identity.get("release_calendar_key"),
        },
        affected_scope_json={
            "track": identity.get("track"),
            "forecast_track": result.get("forecast_track"),
            "status": result.get("status"),
        },
        readiness_impacts_json={
            "coverage_written": result.get("coverage_written"),
            "producer_readiness_written": result.get("producer_readiness_written"),
        },
        readiness_recomputed_at=now_utc if result.get("producer_readiness_written") else None,
        meta_json={"daemon": "forecast-live", "collector_status": result.get("status")},
    )


def _collector_cycle_kwargs(identity: dict[str, object], *, now_utc: datetime) -> dict[str, object]:
    scheduled_for = identity.get("scheduled_for")
    if not isinstance(scheduled_for, datetime):
        raise TypeError("forecast-live identity scheduled_for must be datetime")
    selected_cycle = scheduled_for.astimezone(timezone.utc)
    return {
        "run_date": selected_cycle.date(),
        "run_hour": selected_cycle.hour,
        "now_utc": now_utc,
    }


def _expected_source_run_id(identity: dict[str, object]) -> str:
    scheduled_for = identity.get("scheduled_for")
    if not isinstance(scheduled_for, datetime):
        raise TypeError("forecast-live identity scheduled_for must be datetime")
    selected_cycle = scheduled_for.astimezone(timezone.utc)
    return (
        f"{identity['source_id']}:{identity['track']}:"
        f"{selected_cycle.date().isoformat()}T{selected_cycle.hour:02d}Z"
    )


def _collector_identity_mismatch(identity: dict[str, object], result: dict | None) -> str | None:
    if not isinstance(result, dict):
        return None
    observed_source_run_id = result.get("source_run_id")
    if isinstance(observed_source_run_id, str):
        expected_source_run_id = _expected_source_run_id(identity)
        if observed_source_run_id != expected_source_run_id:
            return (
                "SOURCE_RUN_IDENTITY_MISMATCH "
                f"expected_source_run_id={expected_source_run_id} "
                f"observed_source_run_id={observed_source_run_id}"
            )
    observed_release_key = result.get("release_calendar_key")
    expected_release_key = str(identity["release_calendar_key"])
    if isinstance(observed_release_key, str) and observed_release_key != expected_release_key:
        return (
            "RELEASE_CALENDAR_IDENTITY_MISMATCH "
            f"expected_release_calendar_key={expected_release_key} "
            f"observed_release_calendar_key={observed_release_key}"
        )
    return None


def _latest_job_run_current_for_identity(conn, identity: dict[str, object]) -> tuple[bool, dict[str, object]]:
    from src.state.job_run_repo import get_latest_job_run

    row = get_latest_job_run(conn, str(identity["job_name"]))
    if row is None:
        return False, {"reason": "NO_JOB_RUN"}

    scheduled_for = identity.get("scheduled_for")
    expected_scheduled_for = scheduled_for.isoformat() if isinstance(scheduled_for, datetime) else str(scheduled_for)
    expected_source_run_id = _expected_source_run_id(identity)
    status = str(row["status"]).upper()
    rows_written = int(row["rows_written"] or 0)
    metadata = {
        "reason": "JOB_RUN_NOT_CURRENT",
        "job_run_id": row["job_run_id"],
        "status": status,
        "rows_written": rows_written,
        "scheduled_for": row["scheduled_for"],
        "expected_scheduled_for": expected_scheduled_for,
        "source_run_id": row["source_run_id"],
        "expected_source_run_id": expected_source_run_id,
        "release_calendar_key": row["release_calendar_key"],
        "expected_release_calendar_key": str(identity["release_calendar_key"]),
    }
    if row["scheduled_for"] != expected_scheduled_for:
        return False, metadata
    if row["release_calendar_key"] != str(identity["release_calendar_key"]):
        return False, metadata
    if row["source_run_id"] != expected_source_run_id:
        return False, metadata
    if status not in _CURRENT_SOURCE_CYCLE_STATUSES:
        return False, metadata
    if rows_written <= 0:
        return False, metadata
    metadata["reason"] = "CURRENT_SOURCE_CYCLE_ALREADY_JOURNALED"
    return True, metadata


def run_opendata_track(
    track: str,
    *,
    _locks_dir_override: Path | None = None,
    _collector: Callable[..., dict] | None = None,
    _source_paused: Callable[[str], bool] | None = None,
    _job_conn=None,
    _now_utc: datetime | None = None,
) -> dict:
    from src.data.dual_run_lock import (
        acquire_opendata_track_lock,
        opendata_track_lock_key,
    )
    from src.data.ecmwf_open_data import SOURCE_ID, collect_open_ens_cycle

    source_paused = _source_paused or _is_source_paused
    if source_paused(SOURCE_ID):
        logger.info("forecast-live OpenData %s paused_by_control_plane", track)
        return {"status": "paused_by_control_plane", "source": SOURCE_ID, "track": track}

    from src.data.release_calendar import FetchDecision

    now = (_now_utc or _utcnow()).astimezone(timezone.utc)
    identity = _forecast_work_identity(track, now_utc=now)
    track_lock_key = opendata_track_lock_key(track)
    decision = identity["decision"]
    if _job_conn is not None and decision is not FetchDecision.FETCH_ALLOWED:
        status = "SKIPPED_NOT_RELEASED" if decision is FetchDecision.SKIPPED_NOT_RELEASED else "FAILED"
        result = {"status": decision.value.lower(), "selection": identity.get("metadata")}
        _write_job_run(
            _job_conn,
            identity=identity,
            status=status,
            now_utc=now,
            result=result,
            reason_code=getattr(decision, "value", str(decision)),
            lock_key=track_lock_key,
        )
        return {
            "status": decision.value.lower(),
            "source": SOURCE_ID,
            "track": track,
            "selection": identity.get("metadata"),
        }

    with acquire_opendata_track_lock(
        track,
        _locks_dir_override=_locks_dir_override,
    ) as (acquired, held_lock_key):
        if not acquired:
            logger.info(
                "forecast-live OpenData %s skipped_lock_held key=%s",
                track,
                held_lock_key,
            )
            if _job_conn is not None:
                _write_job_run(
                    _job_conn,
                    identity=identity,
                    status="SKIPPED_LOCK_HELD",
                    now_utc=now,
                    result={"status": "skipped_lock_held", "source": SOURCE_ID, "track": track},
                    reason_code="SKIPPED_LOCK_HELD",
                    lock_key=held_lock_key,
                )
            return {"status": "skipped_lock_held", "source": SOURCE_ID, "track": track}
        collector = _collector or collect_open_ens_cycle
        lock_acquired_at = _utcnow()
        if _job_conn is not None:
            _write_job_run(
                _job_conn,
                identity=identity,
                status="RUNNING",
                now_utc=now,
                started_at=now,
                lock_acquired_at=lock_acquired_at,
                lock_key=track_lock_key,
            )
            _job_conn.commit()
        collector_kwargs = _collector_cycle_kwargs(identity, now_utc=now)
        try:
            result = collector(track=track, **collector_kwargs)
        except Exception as exc:
            if _job_conn is not None:
                _write_job_run(
                    _job_conn,
                    identity=identity,
                    status="FAILED",
                    now_utc=_utcnow(),
                    result={"status": "exception"},
                    reason_code=f"EXCEPTION:{type(exc).__name__}:{exc}",
                    started_at=now,
                    lock_acquired_at=lock_acquired_at,
                    lock_key=track_lock_key,
                )
            raise
        identity_mismatch = _collector_identity_mismatch(identity, result)
        if identity_mismatch is not None:
            failed_result = {
                **(result if isinstance(result, dict) else {}),
                "status": "identity_mismatch",
                "rows_failed": 1,
            }
            if _job_conn is not None:
                _write_job_run(
                    _job_conn,
                    identity=identity,
                    status="FAILED",
                    now_utc=_utcnow(),
                    result=failed_result,
                    reason_code=identity_mismatch,
                    started_at=now,
                    lock_acquired_at=lock_acquired_at,
                    lock_key=track_lock_key,
                )
            raise RuntimeError(identity_mismatch)
        if _job_conn is not None:
            status, reason_code, rows_written, rows_failed = _job_status_from_result(result)
            enriched = {
                **result,
                "snapshots_inserted": result.get("snapshots_inserted", rows_written),
                "rows_failed": rows_failed,
            }
            _write_job_run(
                _job_conn,
                identity=identity,
                status=status,
                now_utc=_utcnow(),
                result=enriched,
                reason_code=reason_code,
                started_at=now,
                lock_acquired_at=lock_acquired_at,
                lock_key=track_lock_key,
            )
        return result


def _run_opendata_track_if_due(
    track: str,
    *,
    _job_conn,
    _locks_dir_override: Path | None = None,
    _collector: Callable[..., dict] | None = None,
    _source_paused: Callable[[str], bool] | None = None,
    _now_utc: datetime | None = None,
) -> dict:
    from src.data.release_calendar import FetchDecision

    now = (_now_utc or _utcnow()).astimezone(timezone.utc)
    identity = _forecast_work_identity(track, now_utc=now)
    if identity["decision"] is FetchDecision.FETCH_ALLOWED:
        is_current, current_metadata = _latest_job_run_current_for_identity(_job_conn, identity)
        if is_current:
            logger.info(
                "forecast-live OpenData %s current source cycle already journaled: %s",
                track,
                current_metadata.get("source_run_id"),
            )
            return {
                "status": "current_cycle_already_journaled",
                "source": identity["source_id"],
                "track": track,
                "source_run_id": current_metadata.get("source_run_id"),
                "scheduled_for": current_metadata.get("scheduled_for"),
                "selection": identity.get("metadata"),
                "journal": current_metadata,
            }

    return run_opendata_track(
        track,
        _locks_dir_override=_locks_dir_override,
        _collector=_collector,
        _source_paused=_source_paused,
        _job_conn=_job_conn,
        _now_utc=now,
    )


def _run_journaled_opendata_track(track: str) -> dict:
    from src.state.db import get_forecasts_connection

    conn = get_forecasts_connection(write_class="bulk")
    try:
        result = run_opendata_track(track, _job_conn=conn)
        conn.commit()
        return result
    except Exception:
        conn.commit()
        raise
    finally:
        conn.close()


def _run_journaled_opendata_track_if_due(track: str) -> dict:
    from src.state.db import get_forecasts_connection

    conn = get_forecasts_connection(write_class="bulk")
    try:
        result = _run_opendata_track_if_due(track, _job_conn=conn)
        conn.commit()
        return result
    except Exception:
        conn.commit()
        raise
    finally:
        conn.close()


def _run_due_opendata_tracks(
    *,
    _runner: Callable[[str], dict] | None = None,
) -> dict[str, dict]:
    runner = _runner or _run_journaled_opendata_track_if_due
    tracks = ("mx2t6_high", "mn2t6_low")
    with ThreadPoolExecutor(
        max_workers=len(tracks),
        thread_name_prefix="opendata-track",
    ) as executor:
        futures = {track: executor.submit(runner, track) for track in tracks}
        return {track: futures[track].result() for track in tracks}


@_scheduler_job(FORECAST_LIVE_DAILY_HIGH_JOB_ID)
def _opendata_mx2t6_cycle() -> dict:
    """00z cron trigger: fires at 08:10 UTC, after the 08:05 UTC safe_fetch window."""
    return _run_journaled_opendata_track_if_due("mx2t6_high")


@_scheduler_job(FORECAST_LIVE_DAILY_HIGH_12Z_JOB_ID)
def _opendata_mx2t6_cycle_12z() -> dict:
    """12z cron trigger: fires at 20:10 UTC, after the 20:05 UTC safe_fetch window."""
    return _run_journaled_opendata_track_if_due("mx2t6_high")


@_scheduler_job(FORECAST_LIVE_DAILY_LOW_JOB_ID)
def _opendata_mn2t6_cycle() -> dict:
    """00z cron trigger: fires at 08:15 UTC, after the 08:05 UTC safe_fetch window."""
    return _run_journaled_opendata_track_if_due("mn2t6_low")


@_scheduler_job(FORECAST_LIVE_DAILY_LOW_12Z_JOB_ID)
def _opendata_mn2t6_cycle_12z() -> dict:
    """12z cron trigger: fires at 20:15 UTC, after the 20:05 UTC safe_fetch window."""
    return _run_journaled_opendata_track_if_due("mn2t6_low")


@_scheduler_job(FORECAST_LIVE_STARTUP_JOB_ID)
def _opendata_startup_catch_up() -> dict:
    results = _run_due_opendata_tracks()
    failed, reason = _classify_result({"tracks": results})
    return {"status": "partial" if failed else "ok", "reason": reason, "tracks": results}


@_scheduler_job(FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID)
def _opendata_safe_cycle_poll() -> dict:
    results = _run_due_opendata_tracks()
    failed, reason = _classify_result({"tracks": results})
    return {"status": "partial" if failed else "ok", "reason": reason, "tracks": results}


def forecast_live_job_specs(
    *,
    startup_run_date: datetime | None = None,
) -> tuple[tuple[Callable[..., object], str, dict[str, object]], ...]:
    startup_at = startup_run_date or datetime.now(timezone.utc)
    specs = (
        (
            _heartbeat_tick,
            "interval",
            {
                "seconds": FORECAST_LIVE_HEARTBEAT_SECONDS,
                "id": FORECAST_LIVE_HEARTBEAT_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 10,
                "executor": "heartbeat",
            },
        ),
        # ECMWF OpenData 00z run: safe_fetch window opens at 08:05 UTC (00:00 + 485 min).
        # Trigger at 08:10 UTC (5-min margin).  The 5-min poll below covers transient
        # misses; idempotent via source_run_id dedup in _run_journaled_opendata_track_if_due.
        # Previous schedule was 07:30 UTC — 35 min before the safe_fetch window — which
        # caused evaluate_safe_fetch to return SKIPPED_NOT_RELEASED → fallback to
        # yesterday's 12z → ~20h staleness at the 14:00 UTC US open → all non-day0 blocked.
        # Authority: a0d51d480b507f324 root-cause + docs/operations/live_review_may23.md.
        (
            _opendata_mx2t6_cycle,
            "cron",
            {
                "hour": 8,
                "minute": 10,
                "id": FORECAST_LIVE_DAILY_HIGH_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            },
        ),
        # ECMWF OpenData 12z run: safe_fetch window opens at 20:05 UTC (12:00 + 485 min).
        # Trigger at 20:10 UTC (5-min margin).
        (
            _opendata_mx2t6_cycle_12z,
            "cron",
            {
                "hour": 20,
                "minute": 10,
                "id": FORECAST_LIVE_DAILY_HIGH_12Z_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            },
        ),
        # mn2t6 LOW track: same safe_fetch windows as mx2t6 HIGH.
        # 00z trigger at 08:15 UTC (5-min offset from HIGH to stagger downloads).
        (
            _opendata_mn2t6_cycle,
            "cron",
            {
                "hour": 8,
                "minute": 15,
                "id": FORECAST_LIVE_DAILY_LOW_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            },
        ),
        # 12z trigger at 20:15 UTC (5-min offset from HIGH).
        (
            _opendata_mn2t6_cycle_12z,
            "cron",
            {
                "hour": 20,
                "minute": 15,
                "id": FORECAST_LIVE_DAILY_LOW_12Z_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            },
        ),
        (
            _opendata_startup_catch_up,
            "date",
            {
                "run_date": startup_at,
                "id": FORECAST_LIVE_STARTUP_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": None,
                "executor": "fast",
            },
        ),
        (
            _opendata_safe_cycle_poll,
            "interval",
            {
                "seconds": FORECAST_LIVE_SAFE_CYCLE_POLL_SECONDS,
                "id": FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 120,
            },
        ),
        (
            _source_health_probe_tick,
            "interval",
            {
                "seconds": FORECAST_LIVE_SOURCE_HEALTH_SECONDS,
                "id": FORECAST_LIVE_SOURCE_HEALTH_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 120,
                "next_run_time": startup_at + timedelta(seconds=FORECAST_LIVE_SOURCE_HEALTH_SECONDS),
                "executor": "source_health",
            },
        ),
    )
    return specs


# ---------------------------------------------------------------------------
# Replacement-forecast PRODUCTION jobs (operator Point-1 directive 2026-06-08).
# Moved here from the live-trading daemon (src/main.py). Replacement raw-input
# download must run on THIS data daemon, on a dedicated lane, event-driven at
# publish time — never inline on the trading process.
# ---------------------------------------------------------------------------


def _replacement_forecast_live_runtime_enabled() -> bool:
    """Whether the registered replacement production jobs will do live runtime work."""
    try:
        from src.data.replacement_forecast_production import (
            _replacement_forecast_runtime_flags_from_settings,
        )
        from src.data.replacement_forecast_runtime_policy import LIVE_FLAG

        flags = _replacement_forecast_runtime_flags_from_settings()
        return bool(flags.get(LIVE_FLAG, False))
    except Exception as exc:  # noqa: BLE001 - never block boot on a flag read
        logger.warning("replacement-forecast live runtime flag read failed (treating as off): %s", exc)
        return False


@_scheduler_job(REPLACEMENT_FORECAST_DOWNLOAD_JOB_ID)
def _replacement_forecast_download_job() -> None:
    """EVENT-DRIVEN raw-input PRE-FETCH (OpenMeteo anchor download).

    Fires at each cycle's publish time (00Z/12Z + release_lag) plus once shortly after boot.
    Delegates to the shared production function, which is flag-gated and fail-soft."""
    from src.data.replacement_forecast_production import _replacement_forecast_download_cycle

    # Call the undecorated inner so the moved function's own @_scheduler_job (which records
    # health under its legacy job_name) does not double-write health alongside THIS wrapper.
    _replacement_forecast_download_cycle.__wrapped__()


@_scheduler_job(ANCHOR_META_CROSS_CHECK_JOB_ID)
def _anchor_meta_stamp_cross_check_job() -> None:
    """Hourly belt-and-suspenders: re-verify meta-stamped anchor artifacts against the
    run-pinned single-runs API once the run appears there (K4.0b(f))."""
    from src.data.replacement_forecast_production import (
        _anchor_meta_stamp_cross_check,
    )

    _anchor_meta_stamp_cross_check.__wrapped__()


@_scheduler_job(REPLACEMENT_AVAILABILITY_POLL_JOB_ID)
def _replacement_cycle_availability_poll_job() -> None:
    """PROBE-RESOLVED freshness owner (operator directive 2026-06-11: automatic download,
    ahead of need, no guessed numbers). Polls provider publication state and fetches any
    newly-published raw-input leg immediately; the publish-time cron above is backstop."""
    from src.data.replacement_forecast_production import (
        _replacement_cycle_availability_poll,
    )

    # Undecorated inner: same single-health-writer pattern as the download wrapper above.
    _replacement_cycle_availability_poll.__wrapped__()


@_scheduler_job(REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID)
def _replacement_forecast_materialize_job(
    *,
    discover: bool = True,
    limit: int | None = None,
    seed_limit: int | None = None,
) -> None:
    """LIGHT seed_discovery -> seed -> materialize on already-downloaded manifests (no download).

    Interval-driven; delegates to the shared production function, which is live-authority gated and
    fail-soft."""
    from src.data.replacement_forecast_production import (
        _replacement_forecast_live_materialize_cycle,
    )

    # Single health writer: the forecast-live scheduler wrapper owns this job's
    # health entry. Calling the production wrapper would swallow exceptions and
    # then let this outer wrapper overwrite FAILED with OK.
    _replacement_forecast_live_materialize_cycle.__wrapped__(
        discover=discover,
        limit=limit,
        seed_limit=seed_limit,
    )


def _replacement_forecast_queue_pending(
    cfg: dict[str, object], key: str
) -> bool:
    path = Path(str(cfg[key]))
    return path.exists() and next(path.glob("*.json"), None) is not None


def _replacement_forecast_discovery_revision(
    cfg: dict[str, object],
) -> tuple[object, ...] | None:
    """Return the cheap causal frontier for recovery discovery."""

    from src.state.db import ZEUS_WORLD_DB_PATH, _connect_read_only

    forecast_db = Path(str(cfg["forecast_db"]))
    if not forecast_db.exists() or not ZEUS_WORLD_DB_PATH.exists():
        return None
    try:
        forecast = _connect_read_only(forecast_db)
        try:
            forecast.execute("PRAGMA query_only=ON")
            revision = tuple(
                forecast.execute(
                    """
                    SELECT
                      (SELECT COALESCE(MAX(event_id), 0) FROM market_events),
                      (SELECT COALESCE(MAX(raw_model_forecast_id), 0)
                         FROM raw_model_forecasts),
                      (SELECT COALESCE(MAX(artifact_id), 0)
                         FROM raw_forecast_artifacts),
                      (SELECT COALESCE(MAX(rowid), 0) FROM source_run_coverage),
                      (SELECT COALESCE(MAX(expires_at), '')
                         FROM readiness_state
                        WHERE datetime(expires_at) <= datetime('now'))
                    """
                ).fetchone()
            )
        finally:
            forecast.close()
        world = _connect_read_only(ZEUS_WORLD_DB_PATH)
        try:
            world.execute("PRAGMA query_only=ON")
            observation_id = int(
                world.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM observation_instants"
                ).fetchone()[0]
            )
        finally:
            world.close()
    except Exception:  # noqa: BLE001 - unknown revision must run recovery discovery
        return None
    hour = datetime.now(tz=timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    ).isoformat()
    return (*revision, observation_id, hour)


def _replacement_forecast_materialize_poll_job() -> None:
    """Drain only source-committed work; global discovery has its own lane."""

    global _replacement_forecast_last_discovery_revision

    from src.data.replacement_forecast_production import (
        _replacement_forecast_live_materialization_queue_config,
    )

    cfg = _replacement_forecast_live_materialization_queue_config()
    requests_pending = _replacement_forecast_queue_pending(cfg, "request_dir")
    seeds_pending = _replacement_forecast_queue_pending(cfg, "seed_dir")
    batch_limit = int(cfg["poll_batch_limit"])
    if requests_pending or seeds_pending:
        revision = _replacement_forecast_discovery_revision(cfg)
        if revision is not None:
            _replacement_forecast_last_discovery_revision = revision
    if requests_pending:
        _replacement_forecast_materialize_job(
            discover=False,
            limit=batch_limit,
            seed_limit=0,
        )
    elif seeds_pending:
        _replacement_forecast_materialize_job(
            discover=False,
            limit=batch_limit,
            seed_limit=batch_limit,
        )


@_scheduler_job(REPLACEMENT_FORECAST_DISCOVERY_JOB_ID)
def _replacement_forecast_discovery_job() -> None:
    """Run global recovery discovery without occupying the hot materialization lane."""

    global _replacement_forecast_last_discovery_revision

    if not _replacement_forecast_live_runtime_enabled():
        return
    from src.data.replacement_forecast_production import (
        _replacement_forecast_live_materialization_queue_config,
    )
    from src.data.replacement_forecast_seed_discovery import (
        discover_replacement_forecast_materialization_seeds,
    )

    cfg = _replacement_forecast_live_materialization_queue_config()
    revision = _replacement_forecast_discovery_revision(cfg)
    if revision is not None and revision == _replacement_forecast_last_discovery_revision:
        return
    if any(
        _replacement_forecast_queue_pending(cfg, key)
        for key in ("request_dir", "seed_dir")
    ):
        if revision is not None:
            _replacement_forecast_last_discovery_revision = revision
        return
    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=cfg["forecast_db"],
        raw_manifest_dir=cfg["raw_manifest_dir"],
        seed_dir=cfg["seed_dir"],
        limit=min(int(cfg["seed_discovery_limit"]), int(cfg["poll_batch_limit"])),
    )
    if revision is not None:
        _replacement_forecast_last_discovery_revision = revision
    if report.status != "NO_ELIGIBLE_TARGETS":
        logger.info("replacement forecast recovery discovery: %s", report.as_dict())


def _publish_replacement_forecast_boot_wake() -> object | None:
    """Publish current posterior scope once before the scheduler starts."""

    if not _replacement_forecast_live_runtime_enabled():
        return None
    from src.data.replacement_forecast_production import (
        _publish_current_forecast_posterior_wake,
        _replacement_forecast_live_materialization_queue_config,
    )

    return _publish_current_forecast_posterior_wake(
        _replacement_forecast_live_materialization_queue_config()
    )


def _replacement_forecast_live_cfg() -> dict:
    """The ``replacement_forecast_live`` settings section via the shared production module's
    single config reader (one source for ``download_release_lag_hours`` /
    ``materialization_interval_min``, identical to the values the moved production functions
    resolve)."""
    from src.data.replacement_forecast_production import _settings_section

    cfg = _settings_section("replacement_forecast_live", {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def _replacement_forecast_publish_cron_hours() -> tuple[int, ...]:
    """The four daily UTC hours when a model cycle becomes available = (cycle + release_lag) %% 24
    for each of the four OpenMeteo anchor cycles {00Z, 06Z, 12Z, 18Z}. With the default 14h release lag
    that is 14:00 / 20:00 / 02:00 / 08:00 UTC (matching
    scripts.download_replacement_forecast_current_targets._source_available_at).

    Previously this returned only the 00Z+12Z hours, so the download cron never fired for the
    06Z/18Z cycles in steady state — the 06Z/18Z raw inputs only ever arrived via daemon-restart
    boot catch-ups. That left a ~12h dead zone (02:10Z->14:10Z UTC) where readiness (3h TTL)
    expired for nearly all scopes and the live engine had zero certified candidates (10h
    production dead-zone incident 2026-06-10). The per-cycle hour is
    (cycle_hour + release_lag) %% 24.

    Scheduling all four is SAFE even if a cron fires before its cycle is actually published: the
    download job ALWAYS resolves the newest *available* cycle via
    ``_parse_cycle(None, now, release_lag_hours)`` (floors now-lag to the latest {0,6,12,18}
    cycle), NOT the trigger hour, and the cycle-currency gate
    (``cycle_is_current``/coverage in ``_download_replacement_forecast_current_targets_if_needed``)
    no-ops a fire whose newest-available cycle is already downloaded. A premature fire therefore
    re-resolves to the current cycle and skips cleanly rather than downloading a not-yet-published
    cycle."""
    release_lag_hours = float(_replacement_forecast_live_cfg().get("download_release_lag_hours") or 14.0)
    return tuple(int((cycle_hour + release_lag_hours) % 24) for cycle_hour in (0, 6, 12, 18))


def _replacement_forecast_materialize_interval_minutes() -> int:
    return int(_replacement_forecast_live_cfg().get("materialization_interval_min") or 1)


def _replacement_forecast_materialize_poll_seconds() -> int:
    return max(
        1,
        int(
            _replacement_forecast_live_cfg().get(
                "materialization_queue_poll_seconds"
            )
            or 1
        ),
    )


def _register_replacement_forecast_production_jobs(
    scheduler: object, *, startup_run_date: datetime | None = None
) -> None:
    """Register forecast-live's replacement materializer and bounded cross-check.

    ``ingest_main`` is the sole owner of current-target and source-clock downloads. Registering
    the legacy publish cron or boot catch-up here duplicates provider traffic whenever this
    restart-heavy daemon respawns and can consume the quota needed by the time-sensitive ingest
    lane. The download wrapper remains importable for explicit operator diagnostics only.
    """
    materialize_minutes = _replacement_forecast_materialize_interval_minutes()
    materialize_poll_seconds = _replacement_forecast_materialize_poll_seconds()
    # Light materialize: interval (consumes already-downloaded manifests; no download).
    scheduler.add_job(  # type: ignore[attr-defined]
        _replacement_forecast_materialize_poll_job,
        "interval",
        seconds=materialize_poll_seconds,
        id=REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID,
        executor=REPLACEMENT_FORECAST_EXECUTOR_LANE,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    scheduler.add_job(  # type: ignore[attr-defined]
        _replacement_forecast_discovery_job,
        "interval",
        minutes=materialize_minutes,
        id=REPLACEMENT_FORECAST_DISCOVERY_JOB_ID,
        executor=REPLACEMENT_FORECAST_DOWNLOAD_EXECUTOR_LANE,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    # Availability poll MOVED to the data-ingest daemon (operator directive 2026-06-11
    # "下载有自己的daemon"): downloads must not share a lifecycle with this restart-heavy
    # daemon — three in-flight extras passes died with forecast-live restarts in one
    # morning. ingest_main registers ingest_replacement_availability_poll (first fire
    # IMMEDIATE at boot, then 5-min). The wrapper below stays importable for tests and
    # manual one-shots but is NO LONGER scheduled here.
    # Retro cross-check of meta-stamped anchor artifacts vs single-runs (K4.0b(f)
    # belt-and-suspenders): hourly, bounded to one fetch per pending cycle.
    scheduler.add_job(  # type: ignore[attr-defined]
        _anchor_meta_stamp_cross_check_job,
        "interval",
        minutes=60,
        id=ANCHOR_META_CROSS_CHECK_JOB_ID,
        executor=REPLACEMENT_FORECAST_DOWNLOAD_EXECUTOR_LANE,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info(
        "replacement-forecast production jobs registered (downloads_owner=ingest_main; "
        "materialize queue poll=%ds discovery=%dmin; lane=%s; "
        "live_runtime_enabled=%s)",
        materialize_poll_seconds, materialize_minutes,
        REPLACEMENT_FORECAST_EXECUTOR_LANE,
        _replacement_forecast_live_runtime_enabled(),
    )


# spec->job_defs derivation is shared across both ingest daemons (one implementation, see
# src.data.scheduler_adapter) so the trigger-param stripping can never diverge between them.
from src.data.scheduler_adapter import (  # noqa: E402
    REGISTRY_OWNED_KWARGS as _REGISTRY_OWNED_KWARGS,
    job_defs_from_specs as _job_defs_from_specs,
)


def build_scheduler(*, startup_run_date: datetime | None = None):
    from apscheduler.executors.pool import ThreadPoolExecutor as _APSchedulerThreadPoolExecutor
    from apscheduler.schedulers.blocking import BlockingScheduler

    from src.data.scheduler_adapter import (
        build_registry_scheduler,
        registry_executor_pools,
    )

    specs = forecast_live_job_specs(startup_run_date=startup_run_date)

    # Operator Point-1 directive 2026-06-08: a DEDICATED executor lane for the replacement-
    # forecast production jobs, so the replacement anchor download never contends with the
    # heartbeat or OpenData lanes. The replacement jobs are registered (after the scheduler is
    # built) on this lane.
    def _replacement_production_executor() -> dict[str, object]:
        # Two SEPARATE single-worker lanes: the heavy download must never serialize ahead of
        # (and starve) the light materialize that refreshes readiness — see
        # REPLACEMENT_FORECAST_DOWNLOAD_EXECUTOR_LANE.
        return {
            REPLACEMENT_FORECAST_EXECUTOR_LANE: _APSchedulerThreadPoolExecutor(max_workers=1),
            REPLACEMENT_FORECAST_DOWNLOAD_EXECUTOR_LANE: _APSchedulerThreadPoolExecutor(max_workers=1),
        }

    # R3 (2026-07-08): registry-built scheduling with executor-lane routing + a fail-fast boot
    # assert is now the ONLY path — the legacy hand-coded 2-pool add_job() loop was deleted
    # (zero-caller-verified; no plist ever set the mode-selection env vars, see scheduler_adapter.py).
    scheduler = BlockingScheduler(
        timezone=timezone.utc,
        executors={**registry_executor_pools(), **_replacement_production_executor()},
    )
    build_registry_scheduler(
        scheduler, "forecast_live_daemon", _job_defs_from_specs(specs),
        forecast_live_owner_env=os.environ.get("ZEUS_FORECAST_LIVE_OWNER", ""),
        logger=logger,
    )
    _register_replacement_forecast_production_jobs(scheduler, startup_run_date=startup_run_date)
    return scheduler


def main() -> None:
    global _scheduler

    # F85: route INFO/DEBUG to stdout (.log) and WARNING+ to stderr (.err).
    # handlers.clear() MUST precede any logger call (including bypass_dead_proxy_env_vars).
    _fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    _stdout_h = logging.StreamHandler(sys.stdout)
    _stdout_h.setLevel(logging.INFO)
    _stdout_h.setFormatter(_fmt)
    _stdout_h.addFilter(lambda r: r.levelno < logging.WARNING)
    _stderr_h = logging.StreamHandler(sys.stderr)
    _stderr_h.setLevel(logging.WARNING)
    _stderr_h.setFormatter(_fmt)
    _root = logging.getLogger()
    _root.handlers.clear()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stdout_h)
    _root.addHandler(_stderr_h)
    logger.info("Zeus forecast-live daemon starting")

    from src.data.proxy_health import bypass_dead_proxy_env_vars
    from src.state.db import (
        assert_schema_current_forecasts,
        get_forecasts_connection,
        init_schema_forecasts,
    )

    bypass_dead_proxy_env_vars()
    conn = get_forecasts_connection(write_class="bulk")
    try:
        if _forecast_boot_schema_ready(conn):
            logger.info("forecast-live schema fast-check passed; skipping full init_schema_forecasts")
        else:
            logger.info("forecast-live schema fast-check incomplete; running init_schema_forecasts")
            init_schema_forecasts(conn)
        assert_schema_current_forecasts(conn)
        conn.commit()
    finally:
        conn.close()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    _source_health_probe_tick()
    try:
        _publish_replacement_forecast_boot_wake()
    except Exception:
        logger.warning(
            "forecast-live current-posterior boot wake failed; "
            "periodic materialization remains active",
            exc_info=True,
        )
    _scheduler = build_scheduler()
    jobs = [job.id for job in _scheduler.get_jobs()]
    _write_forecast_live_heartbeat(status="scheduler_ready")
    logger.info("Forecast-live scheduler ready. %d jobs: %s", len(jobs), jobs)
    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus forecast-live daemon shutting down")
        _scheduler.shutdown()


if __name__ == "__main__":
    main()
