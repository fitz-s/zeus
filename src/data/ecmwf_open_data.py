# Created: prior; restructured 2026-05-01
# Last reused/audited: 2026-05-03
# Authority basis: Operator directive 2026-05-01 + PLAN_v4 Phase 5A release-calendar source-run selection.
#   ECMWF Open Data has ~6-8h latency (vs. TIGGE's 48h public embargo) so it
#   is the live-trading source for same-day forecasts. Rows must land in
#   ensemble_snapshots_v2 with the canonical local-calendar-day data_version
#   so calibration / day0 / opening_hunt readers can consume them alongside
#   TIGGE archive rows via the data_version priority list.
"""Collect ECMWF Open Data ENS member vectors into ensemble_snapshots_v2.

Replaces the legacy 2t-instantaneous + ensemble_snapshots (v1) write path.

Pipeline
--------
1. Download single GRIB containing all 51 members × 60 step hours for the
   requested run (mx2t6 OR mn2t6 per call) via
   ``51 source data/scripts/download_ecmwf_open_ens.py``.
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

These data_versions are added to ``CANONICAL_ENSEMBLE_DATA_VERSIONS`` in
``src/contracts/ensemble_snapshot_provenance.py``. The TIGGE archive
``tigge_*_v1`` data_versions remain valid alongside.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import PROJECT_ROOT
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.forecast_source_registry import gate_source, gate_source_role
from src.data.release_calendar import FetchDecision, select_source_run_for_target_horizon
from src.state.db import get_world_connection as get_connection

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
DOWNLOAD_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "download_ecmwf_open_ens.py"
EXTRACT_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "extract_open_ens_localday.py"
INGEST_SCRIPT_DIR = PROJECT_ROOT / "scripts"

# Open Data ships hourly steps; we want every 6h boundary up to 240h
# (inclusive) to cover lead 0..10 days. Steps must be multiples of 6 since
# mx2t6/mn2t6 are 6-hour aggregations.
STEP_HOURS = list(range(6, 246, 6))

# Track config — local to this module so the daemon's ingest knob is one
# clean dict rather than two parallel param lists.
TRACKS: dict[str, dict] = {
    "mx2t6_high": {
        "open_data_param": "mx2t6",
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        "ingest_track": "mx2t6_high",
        "extract_subdir": "open_ens_mx2t6_localday_max",
    },
    "mn2t6_low": {
        "open_data_param": "mn2t6",
        "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
        "ingest_track": "mn2t6_low",
        "extract_subdir": "open_ens_mn2t6_localday_min",
    },
}

SOURCE_ID = "ecmwf_open_data"
FORECAST_SOURCE_ROLE = "diagnostic"
MODEL_VERSION = "ecmwf_open_data"


def _conda_python() -> str:
    """Path to the conda interpreter that has ``ecmwf.opendata`` + eccodes installed.

    Falls back to ``sys.executable``; tests that mock the runner never invoke this.
    """
    candidate = Path("/Users/leofitz/miniconda3/bin/python")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _download_output_path(*, run_date: date, run_hour: int, param: str) -> Path:
    steps = "-".join(str(value) for value in STEP_HOURS)
    return (
        FIFTY_ONE_ROOT
        / "raw"
        / "ecmwf_open_ens"
        / "ecmwf"
        / run_date.strftime("%Y%m%d")
        / f"open_ens_{run_date.strftime('%Y%m%d')}_{run_hour:02d}z_steps_{steps}_params_{param}.grib2"
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


def _run_subprocess(args: list[str], *, label: str, timeout: int) -> dict:
    logger.info("ecmwf_open_data %s: %s", label, " ".join(args[:6]) + " ...")
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        logger.error("ecmwf_open_data %s: TIMEOUT after %ds", label, timeout)
        return {"label": label, "ok": False, "error": f"timeout after {timeout}s",
                "stderr_tail": str(exc)[-300:]}
    except FileNotFoundError as exc:
        return {"label": label, "ok": False, "error": f"script not found: {exc}",
                "stderr_tail": ""}
    if result.returncode != 0:
        logger.warning("ecmwf_open_data %s: rc=%d stderr_tail=%s",
                       label, result.returncode, (result.stderr or "")[-300:])
    return {
        "label": label,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-400:],
        "stderr_tail": (result.stderr or "")[-400:],
    }


def collect_open_ens_cycle(
    *,
    track: str = "mx2t6_high",
    run_date: Optional[date] = None,
    run_hour: Optional[int] = None,
    download_timeout_seconds: int = 600,
    extract_timeout_seconds: int = 300,
    skip_download: bool = False,
    skip_extract: bool = False,
    conn=None,
    _runner=None,
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
    source_release_time = selection_metadata.get("next_safe_fetch_at")
    if not isinstance(source_release_time, datetime):
        source_release_time = source_cycle_time
    source_run_id = f"{SOURCE_ID}:{track}:{cycle_date.isoformat()}T{cycle_hour:02d}Z"
    release_calendar_key = f"{SOURCE_ID}:{track}:{selection_metadata.get('horizon_profile', 'manual')}"

    output_path = _download_output_path(
        run_date=cycle_date, run_hour=cycle_hour, param=cfg["open_data_param"],
    )
    stages: list[dict] = []

    if not skip_download:
        download = runner(
            [
                _conda_python(),
                str(DOWNLOAD_SCRIPT),
                "--date", cycle_date.isoformat(),
                "--run-hour", str(cycle_hour),
                "--step", *[str(s) for s in STEP_HOURS],
                "--param", cfg["open_data_param"],
                "--source", "ecmwf",
                "--output-path", str(output_path),
            ],
            label=f"download_{track}",
            timeout=download_timeout_seconds,
        )
        stages.append(download)
        if not download["ok"]:
            return {
                "status": "download_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

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
            return {
                "status": "extract_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

    # Ingest stage — import in-process, share a single connection so the
    # caller's test fixture (in-memory sqlite) is honored. Production
    # caller passes ``conn=None`` and we open the world DB.
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        # Make scripts/ importable so we can call ingest_track.
        if str(INGEST_SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(INGEST_SCRIPT_DIR))
        from ingest_grib_to_snapshots import SourceRunContext, ingest_track as _ingest  # type: ignore
        from src.state.schema.v2_schema import apply_v2_schema

        apply_v2_schema(conn)
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
                overwrite=False,
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
    finally:
        if own_conn:
            conn.close()

    return {
        "status": "ok",
        "track": track,
        "data_version": cfg["data_version"],
        "run_date": cycle_date.isoformat(),
        "run_hour": cycle_hour,
        "source_run_id": source_run_id,
        "release_calendar_key": release_calendar_key,
        "source_id": SOURCE_ID,
        "forecast_source_role": FORECAST_SOURCE_ROLE,
        "degradation_level": source_spec.degradation_level,
        "download_path": str(output_path),
        "snapshots_inserted": int(summary.get("written", 0)),
        "snapshots_skipped": int(summary.get("skipped", 0)),
        "stages": stages,
    }


def data_version_priority_for_metric(temperature_metric: str) -> tuple[str, ...]:
    """Return read-priority tuple for a given metric: opendata first, TIGGE archive second.

    Use this in any reader that wants "freshest source first, fall back to
    archive". Equivalent SQL pattern::

        SELECT ... FROM ensemble_snapshots_v2
         WHERE temperature_metric = ?
           AND data_version IN (?, ?)
         ORDER BY CASE data_version WHEN ? THEN 0 ELSE 1 END, available_at DESC

    where the bound parameters are the priority tuple followed by the
    priority tuple's first element again.
    """
    if temperature_metric == "high":
        return (ECMWF_OPENDATA_HIGH_DATA_VERSION, "tigge_mx2t6_local_calendar_day_max_v1")
    if temperature_metric == "low":
        return (ECMWF_OPENDATA_LOW_DATA_VERSION, "tigge_mn2t6_local_calendar_day_min_v1")
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
