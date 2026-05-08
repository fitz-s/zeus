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
import subprocess
import sys
import hashlib
import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

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
from src.state.db import get_world_connection as get_connection, ZEUS_WORLD_DB_PATH
from src.state.db_writer_lock import WriteClass, db_writer_lock
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
DOWNLOAD_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "download_ecmwf_open_ens.py"
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
) -> dict[str, int | str | None]:
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
    download_timeout_seconds: int = 1500,  # was 600 — empirical full-fetch 609.6s for 71 steps × 51 members × 1.5GB
    extract_timeout_seconds: int = 900,
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

    if not skip_download:
        _download_args = [
            _conda_python(),
            str(DOWNLOAD_SCRIPT),
            "--date", cycle_date.isoformat(),
            "--run-hour", str(cycle_hour),
            "--step", *[str(s) for s in STEP_HOURS],
            "--param", cfg["open_data_param"],
            "--source", "ecmwf",
            "--output-path", str(output_path),
        ]
        _stderr_dump = (
            PROJECT_ROOT
            / "tmp"
            / f"ecmwf_open_data_{cycle_date.isoformat()}_{cycle_hour:02d}z_{track}.stderr.txt"
        )
        # Bounded retry: attempt 1 immediately, attempt 2 after 60s.
        # Worst-case wall time: 1500 + 60 + 1500 = 3060s (~51 min), leaving margin before
        # the LOW job's misfire_grace_time expires (~55 min after HIGH fires at 07:30 UTC).
        # A 404 on a grid-valid step means data not yet published → SKIPPED_NOT_RELEASED (no retry).
        # All other rc!=0 are retryable (transient network, rate-limit, timeout).
        _retry_delays = [0, 60]
        download = None
        for _attempt, _delay in enumerate(_retry_delays, start=1):
            if _delay > 0:
                logger.info(
                    "ecmwf_open_data download_%s: retry attempt %d/%d after %ds sleep",
                    track, _attempt, len(_retry_delays), _delay,
                )
                time.sleep(_delay)
            download = runner(
                _download_args,
                label=f"download_{track}",
                timeout=download_timeout_seconds,
            )
            if download["ok"]:
                break
            # Distinguish 404 on grid-valid step (not-yet-released) from other failures.
            _stderr = download.get("stderr_tail", "") or ""
            _is_404 = "404" in _stderr or "Not Found" in _stderr
            if _is_404 and "No index entries" not in _stderr:
                logger.warning(
                    "ecmwf_open_data download_%s: 404 on grid-valid step — SKIPPED_NOT_RELEASED (attempt %d)",
                    track, _attempt,
                )
                _write_stderr_dump(_stderr_dump, _stderr)
                stages.append(download)
                return {
                    "status": "skipped_not_released",
                    "track": track,
                    "data_version": cfg["data_version"],
                    "stages": stages,
                    "snapshots_inserted": 0,
                }
            if _attempt < len(_retry_delays):
                logger.warning(
                    "ecmwf_open_data download_%s: rc=%d on attempt %d/%d — will retry",
                    track, download.get("returncode", -1), _attempt, len(_retry_delays),
                )
        # Write stderr dump on final failure (any rc!=0 after all retries).
        if not download["ok"]:
            _write_stderr_dump(_stderr_dump, download.get("stderr_tail", "") or "")
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
        _lock_ctx = db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.BULK)
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
            from src.state.db import init_schema

            init_schema(conn)
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
