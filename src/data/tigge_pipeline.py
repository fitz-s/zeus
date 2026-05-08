# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — TIGGE retrieval must run inside
#   the ingest daemon so trading does not go stale even when ingest is healthy.
#   Structural fix per Fitz Constraint #1 (one decision: TIGGE belongs to ingest)
#   replacing N patches across cron entries / manual runs / ad-hoc backfills.
"""TIGGE daily pipeline orchestrator — download + extract + ingest.

This module is the single entry point the ingest daemon uses to refresh TIGGE
ensemble forecasts daily without operator intervention.

Stages
------
1. Download stage: invokes
   ``51 source data/scripts/tigge_(mx|mn)2t6_download_resumable.py``
   as subprocess(es). Those scripts call ``ecmwfapi.ECMWFDataServer()`` which
   reads ``~/.ecmwfapirc`` for MARS credentials. We do not duplicate the auth
   logic in this repo — the .ecmwfapirc file IS the keychain entry equivalent
   for MARS (per ECMWF SDK convention).
2. Extract stage: invokes
   ``51 source data/scripts/extract_tigge_(mx|mn)2t6_localday_(max|min).py``
   as subprocess(es) to produce the canonical local-calendar-day JSONs.
3. Ingest stage: imports and calls ``ingest_track`` from
   ``scripts/ingest_grib_to_snapshots.py`` (zeus repo) which writes to
   ``ensemble_snapshots_v2``. Idempotency is provided by the existing
   UNIQUE(city, target_date, temperature_metric, issue_time, data_version)
   constraint — re-runs of the same date naturally skip existing rows.

Why subprocess for stages 1+2 and import for stage 3
----------------------------------------------------
- Stages 1+2 live in ``51 source data/scripts/`` because they have their own
  release cadence (driven by manifest changes, region tweaks, ECMWF API
  evolutions). Migrating them wholesale would balloon this commit and create
  duplicate maintenance surfaces for ~6 scripts. Subprocess invocation lets
  them keep their lifecycle while we plumb the daily cycle.
- Stage 3 (ingest) lives in this repo and has a clean importable entry
  (``ingest_track``) that already enforces TiggeSnapshotPayload (antibody #16)
  and the canonical-write contract.

Failure semantics
-----------------
- MARS auth missing/invalid → CRITICAL log + ``set_pause_source("tigge_mars",
  True)`` so the next tick short-circuits cleanly. Operator clears with
  ``set_pause_source("tigge_mars", False)`` once creds are restored.
- Per-stage failures emit structured status dicts; the orchestrator never
  raises out of ``run_tigge_daily_cycle`` — the daemon-level wrapper logs
  failures via ``_scheduler_job``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.state.db import ZEUS_WORLD_DB_PATH
from src.state.db_writer_lock import WriteClass, db_writer_lock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZEUS_ROOT = Path(__file__).resolve().parents[2]
FIFTY_ONE_ROOT = ZEUS_ROOT.parent / "51 source data"
RAW_ROOT = FIFTY_ONE_ROOT / "raw"
SCRIPTS_DIR_51 = FIFTY_ONE_ROOT / "scripts"
ECMWF_API_RC = Path.home() / ".ecmwfapirc"

# Boot-time catch-up cap (per design constraints).
MAX_LOOKBACK_DAYS = 7

# Source ID used by control_plane / source_health.
SOURCE_ID = "tigge_mars"

# Tracks managed by this pipeline — keep aligned with _TRACK_CONFIGS in
# scripts/ingest_grib_to_snapshots.py.
TRACKS: tuple[str, ...] = ("mx2t6_high", "mn2t6_low")


# ---------------------------------------------------------------------------
# MARS credential check
# ---------------------------------------------------------------------------


class MarsCredentialError(RuntimeError):
    """Raised when ~/.ecmwfapirc is missing/invalid. Surfaced by check_mars_credentials()."""


def check_mars_credentials(*, rc_path: Optional[Path] = None) -> dict:
    """Verify MARS credentials are present and well-formed.

    Returns a status dict ``{ok: bool, error: str|None, source: str}``. Does NOT
    raise — callers decide what to do (probe records failure; orchestrator pauses).
    """
    rc = rc_path or ECMWF_API_RC
    if not rc.exists():
        return {
            "ok": False,
            "error": f"ECMWF API credentials file missing: {rc}. "
                     f"Operator must create it with url/key/email JSON fields. "
                     f"See https://confluence.ecmwf.int/display/WEBAPI/",
            "source": SOURCE_ID,
        }
    try:
        body = rc.read_text(encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"could not read {rc}: {exc}", "source": SOURCE_ID}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"{rc} is not valid JSON: {exc}",
            "source": SOURCE_ID,
        }

    required = {"url", "key", "email"}
    missing = sorted(required - set(parsed.keys()))
    if missing:
        return {
            "ok": False,
            "error": f"{rc} missing fields: {missing}. Required: {sorted(required)}",
            "source": SOURCE_ID,
        }
    if not all(parsed.get(k) for k in required):
        return {
            "ok": False,
            "error": f"{rc} has empty value(s) for one of {sorted(required)}",
            "source": SOURCE_ID,
        }
    return {"ok": True, "error": None, "source": SOURCE_ID}


# ---------------------------------------------------------------------------
# Catch-up window
# ---------------------------------------------------------------------------


def _max_issue_date_in_db() -> Optional[date]:
    """Return MAX(DATE(issue_time)) from ensemble_snapshots_v2 across TIGGE data_versions.

    Returns None if the table is empty / missing or no TIGGE rows present.
    """
    try:
        from src.state.db import get_world_connection
    except Exception as exc:
        logger.warning("tigge_pipeline: cannot import get_world_connection: %s", exc)
        return None
    conn = get_world_connection()
    try:
        try:
            row = conn.execute(
                "SELECT MAX(DATE(issue_time)) FROM ensemble_snapshots_v2 "
                "WHERE data_version LIKE 'mx2t6_%' OR data_version LIKE 'mn2t6_%'"
            ).fetchone()
        except Exception as exc:
            logger.warning("tigge_pipeline: MAX(issue_time) query failed: %s", exc)
            return None
        if not row or row[0] is None:
            return None
        try:
            return date.fromisoformat(str(row[0])[:10])
        except Exception:
            return None
    finally:
        conn.close()


def determine_catch_up_dates(
    *,
    today_utc: Optional[date] = None,
    max_lookback_days: int = MAX_LOOKBACK_DAYS,
    db_max_issue: Optional[date] = None,
) -> list[date]:
    """Return list of issue dates needing catch-up (oldest first).

    Bounded by ``max_lookback_days`` to avoid runaway backfills. The newest
    issue date considered is yesterday — TIGGE on the public MARS archive
    has a **48-hour embargo** (confirmed at <https://confluence.ecmwf.int/>;
    see ``docs/operations/tigge_daemon_integration.md`` §"Source role"),
    so today's 00Z cannot be requested same-day under any circumstances.
    The earlier "TIGGE posts by 10:00 UTC" comment was wrong and has been
    purged. The oldest is ``max(yesterday - max_lookback_days + 1,
    db_max_issue + 1)``.
    """
    today = today_utc or datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    if db_max_issue is None:
        db_max_issue = _max_issue_date_in_db()

    floor = yesterday - timedelta(days=max_lookback_days - 1)
    if db_max_issue is not None:
        # Don't re-fetch dates we already have.
        floor = max(floor, db_max_issue + timedelta(days=1))
    if floor > yesterday:
        return []
    out: list[date] = []
    cur = floor
    while cur <= yesterday:
        out.append(cur)
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Subprocess invocation helpers
# ---------------------------------------------------------------------------


def _conda_python() -> str:
    """Return the python interpreter the legacy 51 scripts expect.

    The 51-source-data scripts were written against the conda base python
    (where ``ecmwfapi`` is installed). Falls back to ``sys.executable`` if
    conda is missing — tests and dry-runs won't actually invoke MARS so the
    fallback is safe.
    """
    candidate = Path("/Users/leofitz/miniconda3/bin/python")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _run_subprocess(
    args: list[str],
    *,
    timeout: int,
    label: str,
) -> dict:
    """Run a subprocess, capturing stdout/stderr. Returns status dict."""
    logger.info("tigge_pipeline %s: invoking %s", label, " ".join(args[:4]) + " ...")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("tigge_pipeline %s: TIMEOUT after %ds", label, timeout)
        return {
            "label": label,
            "ok": False,
            "returncode": None,
            "error": f"timeout after {timeout}s",
            "stderr_tail": str(exc)[-400:],
        }
    except FileNotFoundError as exc:
        logger.error("tigge_pipeline %s: script not found: %s", label, exc)
        return {
            "label": label,
            "ok": False,
            "returncode": None,
            "error": f"script not found: {exc}",
            "stderr_tail": "",
        }
    if result.returncode != 0:
        logger.warning(
            "tigge_pipeline %s: rc=%d, stderr_tail=%s",
            label, result.returncode, (result.stderr or "")[-300:],
        )
    return {
        "label": label,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-400:],
        "stderr_tail": (result.stderr or "")[-400:],
    }


# ---------------------------------------------------------------------------
# Stage 1: download
# ---------------------------------------------------------------------------


def _download_track(
    track: str,
    *,
    date_from: date,
    date_to: date,
    timeout_seconds: int,
    runner: callable = _run_subprocess,
) -> dict:
    """Run the resumable downloader for one TIGGE track over a date window."""
    if track == "mx2t6_high":
        script = SCRIPTS_DIR_51 / "tigge_mx2t6_download_resumable.py"
    elif track == "mn2t6_low":
        script = SCRIPTS_DIR_51 / "tigge_mn2t6_download_resumable.py"
    else:
        return {"label": f"download_{track}", "ok": False, "error": f"unknown track {track!r}"}

    args = [
        _conda_python(),
        str(script),
        "--date-from", date_from.isoformat(),
        "--date-to", date_to.isoformat(),
        "--max-passes", "1",  # daily ingest = single pass; resumability handled by re-runs
    ]
    return runner(args, timeout=timeout_seconds, label=f"download_{track}")


# ---------------------------------------------------------------------------
# Stage 2: extract
# ---------------------------------------------------------------------------


def _extract_track(
    track: str,
    *,
    date_from: date,
    date_to: date,
    timeout_seconds: int,
    runner: callable = _run_subprocess,
) -> dict:
    """Run the local-day extractor for one TIGGE track over a date window."""
    if track == "mx2t6_high":
        script = SCRIPTS_DIR_51 / "extract_tigge_mx2t6_localday_max.py"
    elif track == "mn2t6_low":
        script = SCRIPTS_DIR_51 / "extract_tigge_mn2t6_localday_min.py"
    else:
        return {"label": f"extract_{track}", "ok": False, "error": f"unknown track {track!r}"}

    args = [
        _conda_python(),
        str(script),
        "--date-from", date_from.isoformat(),
        "--date-to", date_to.isoformat(),
    ]
    return runner(args, timeout=timeout_seconds, label=f"extract_{track}")


# ---------------------------------------------------------------------------
# Stage 3: ingest (in-process import — uses zeus venv)
# ---------------------------------------------------------------------------


def _ingest_track(
    track: str,
    *,
    date_from: date,
    date_to: date,
) -> dict:
    """Import scripts/ingest_grib_to_snapshots.ingest_track and run it."""
    label = f"ingest_{track}"
    try:
        # Ensure scripts/ is importable.
        scripts_dir = ZEUS_ROOT / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from ingest_grib_to_snapshots import ingest_track as _ingest_track_fn  # type: ignore
        from src.state.db import get_world_connection
        from src.state.schema.v2_schema import apply_v2_schema
    except Exception as exc:
        logger.error("tigge_pipeline %s: import failed: %s", label, exc)
        return {"label": label, "ok": False, "error": f"import failed: {exc}"}

    with db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.BULK):
        conn = get_world_connection()
        try:
            apply_v2_schema(conn)
            try:
                summary = _ingest_track_fn(
                    track=track,
                    json_root=RAW_ROOT,
                    conn=conn,
                    date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(),
                    cities=None,
                    overwrite=False,
                    # Allow zero-file runs during catch-up (download stage may skip
                    # already-present files; extract may not produce new JSONs).
                    require_files=False,
                )
            except Exception as exc:
                logger.error("tigge_pipeline %s: ingest_track raised: %s", label, exc)
                return {"label": label, "ok": False, "error": str(exc)}
        finally:
            conn.close()

    return {
        "label": label,
        "ok": True,
        "written": summary.get("written", 0),
        "skipped": summary.get("skipped", 0),
        "errors": summary.get("errors", 0),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_tigge_daily_cycle(
    target_date: Optional[str] = None,
    *,
    download_timeout_seconds: int = 1800,
    extract_timeout_seconds: int = 600,
    skip_download: bool = False,
    skip_extract: bool = False,
    _runner: callable = _run_subprocess,
    _credential_checker: callable = check_mars_credentials,
    _pause_source: Optional[callable] = None,
) -> dict:
    """Run download + extract + ingest for one or more TIGGE issue dates.

    Parameters
    ----------
    target_date:
        ISO date string ``YYYY-MM-DD``. When provided, only that single issue
        date is processed (used for one-off backfills via the daemon's code
        path). When ``None``, processes the catch-up window:
        ``max(today_utc - MAX_LOOKBACK_DAYS, db_max_issue + 1) ... yesterday``.
    skip_download / skip_extract:
        Test/operator escape hatches. When the daemon is fed a fixture or the
        operator wants to re-ingest already-extracted JSONs without hitting
        MARS, set these to True.
    _runner / _credential_checker / _pause_source:
        Test seams. Production caller passes nothing.

    Returns
    -------
    dict with keys:
      - status: "ok" | "paused_by_control_plane" | "paused_mars_credentials" |
                "noop_no_dates"
      - dates: list[str] (ISO dates processed)
      - stages: list of per-stage dicts
      - written / skipped / errors: aggregate ingest counters

    Never raises — the daemon wrapper logs failures via _scheduler_job.
    """
    # Honor control-plane pause directive.
    try:
        from src.control.control_plane import read_ingest_control_state
        ctrl = read_ingest_control_state()
        if SOURCE_ID in ctrl.get("paused_sources", set()):
            logger.info("run_tigge_daily_cycle: paused_by_control_plane")
            return {"status": "paused_by_control_plane", "source": SOURCE_ID, "stages": []}
    except Exception as exc:
        logger.warning("control_plane read failed (continuing): %s", exc)

    # MARS credential gate (only if we're going to download).
    if not skip_download:
        cred_status = _credential_checker()
        if not cred_status.get("ok"):
            logger.critical(
                "run_tigge_daily_cycle: MARS credentials unavailable — pausing source. error=%s",
                cred_status.get("error"),
            )
            try:
                if _pause_source is not None:
                    _pause_source(SOURCE_ID, True)
                else:
                    from src.control.control_plane import set_pause_source
                    set_pause_source(SOURCE_ID, True)
            except Exception as exc:
                logger.error("could not auto-pause %s: %s", SOURCE_ID, exc)
            return {
                "status": "paused_mars_credentials",
                "source": SOURCE_ID,
                "error": cred_status.get("error"),
                "stages": [],
            }

    # Determine date window.
    if target_date is not None:
        try:
            single = date.fromisoformat(target_date)
        except ValueError as exc:
            logger.error("run_tigge_daily_cycle: bad target_date=%r: %s", target_date, exc)
            return {"status": "bad_target_date", "error": str(exc), "stages": []}
        dates = [single]
    else:
        dates = determine_catch_up_dates()

    if not dates:
        logger.info("run_tigge_daily_cycle: no dates to process (db is current)")
        return {"status": "noop_no_dates", "dates": [], "stages": []}

    date_from, date_to = dates[0], dates[-1]
    logger.info(
        "run_tigge_daily_cycle: %d dates [%s..%s] across tracks=%s",
        len(dates), date_from.isoformat(), date_to.isoformat(), TRACKS,
    )

    stages: list[dict] = []
    total_written = 0
    total_skipped = 0
    total_errors = 0

    for track in TRACKS:
        if not skip_download:
            dl = _download_track(
                track,
                date_from=date_from,
                date_to=date_to,
                timeout_seconds=download_timeout_seconds,
                runner=_runner,
            )
            stages.append(dl)
        if not skip_extract:
            ex = _extract_track(
                track,
                date_from=date_from,
                date_to=date_to,
                timeout_seconds=extract_timeout_seconds,
                runner=_runner,
            )
            stages.append(ex)

        ing = _ingest_track(track, date_from=date_from, date_to=date_to)
        stages.append(ing)
        if ing.get("ok"):
            total_written += int(ing.get("written", 0))
            total_skipped += int(ing.get("skipped", 0))
            total_errors += int(ing.get("errors", 0))

    return {
        "status": "ok",
        "dates": [d.isoformat() for d in dates],
        "tracks": list(TRACKS),
        "written": total_written,
        "skipped": total_skipped,
        "errors": total_errors,
        "stages": stages,
    }


__all__ = [
    "MarsCredentialError",
    "MAX_LOOKBACK_DAYS",
    "SOURCE_ID",
    "TRACKS",
    "check_mars_credentials",
    "determine_catch_up_dates",
    "run_tigge_daily_cycle",
]
