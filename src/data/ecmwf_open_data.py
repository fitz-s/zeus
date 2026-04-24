"""Collect ECMWF Open Data ENS member vectors into ensemble_snapshots."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np

from src.config import PROJECT_ROOT, cities
from src.state.db import get_world_connection as get_connection

logger = logging.getLogger(__name__)

FIFTY_ONE_ROOT = PROJECT_ROOT.parent / "51 source data"
DOWNLOAD_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "download_ecmwf_open_ens.py"
EXTRACT_SCRIPT = FIFTY_ONE_ROOT / "scripts" / "extract_open_ens_city_member_vectors.py"
STEP_HOURS = [24, 48, 72, 96, 120, 144, 168]
DATA_VERSION = "open_ens_v1"
MODEL_VERSION = "ecmwf_open_data"


def _run_json_command(args: list[str]) -> dict:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
        cwd=str(PROJECT_ROOT.parent),
    )
    return json.loads(result.stdout)


def _default_cycle(now: datetime) -> tuple[date, int]:
    if now.hour >= 12:
        return now.date(), 12
    return now.date(), 0


def _download_output_path(run_date: date, run_hour: int) -> Path:
    steps = "-".join(str(value) for value in STEP_HOURS)
    return (
        FIFTY_ONE_ROOT
        / "raw"
        / "ecmwf_open_ens"
        / "ecmwf"
        / run_date.strftime("%Y%m%d")
        / f"open_ens_{run_date.strftime('%Y%m%d')}_{run_hour:02d}z_steps_{steps}_params_2t.grib2"
    )


def _group_members_by_step(extract_summary: dict) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = {}
    for member in extract_summary.get("members", []):
        step_range = member.get("step_range")
        if not step_range:
            continue
        try:
            step_hours = int(str(step_range).split("-")[-1])
            grouped.setdefault(step_hours, []).append(float(member["value_native_unit"]))
        except (TypeError, ValueError, KeyError):
            continue
    return grouped


def collect_open_ens_cycle(
    *,
    run_date: date | None = None,
    run_hour: int | None = None,
    conn=None,
) -> dict:
    """Download the latest ECMWF Open Data ENS run and mirror it into SQLite."""

    now = datetime.now(timezone.utc)
    cycle_date, cycle_hour = _default_cycle(now) if run_date is None or run_hour is None else (run_date, run_hour)
    if run_date is not None:
        cycle_date = run_date
    if run_hour is not None:
        cycle_hour = run_hour

    output_path = _download_output_path(cycle_date, cycle_hour)
    download_summary = _run_json_command([
        "python3",
        str(DOWNLOAD_SCRIPT),
        "--date",
        cycle_date.isoformat(),
        "--run-hour",
        str(cycle_hour),
        "--step",
        *[str(step) for step in STEP_HOURS],
        "--param",
        "2t",
        "--source",
        "ecmwf",
        "--output-path",
        str(output_path),
    ])

    issue_dt = datetime.combine(cycle_date, time(cycle_hour, 0), tzinfo=timezone.utc)
    fetch_time = download_summary.get("generated_at", now.isoformat())

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    inserted = 0
    try:
        for city in cities:
            try:
                extract_summary = _run_json_command([
                    "python3",
                    str(EXTRACT_SCRIPT),
                    city.name,
                    "--path",
                    str(output_path),
                ])
            except Exception as exc:
                logger.warning("Open ENS extract failed for %s: %s", city.name, exc)
                continue

            by_step = _group_members_by_step(extract_summary)
            for step_hours, values in by_step.items():
                if not values:
                    continue
                target_date = (cycle_date + timedelta(hours=step_hours)).isoformat()
                valid_dt = issue_dt + timedelta(hours=step_hours)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ensemble_snapshots
                    (city, target_date, issue_time, valid_time, available_at, fetch_time,
                     lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        city.name,
                        target_date,
                        issue_dt.isoformat(),
                        valid_dt.isoformat(),
                        fetch_time,
                        fetch_time,
                        float(step_hours),
                        json.dumps(values),
                        None,
                        float(np.std(values)),
                        0,
                        MODEL_VERSION,
                        DATA_VERSION,
                    ),
                )
                inserted += int(conn.execute("SELECT changes()").fetchone()[0])
        conn.commit()
    finally:
        if own_conn:
            conn.close()

    return {
        "run_date": cycle_date.isoformat(),
        "run_hour": cycle_hour,
        "download_path": str(output_path),
        "snapshots_inserted": inserted,
        "cities_attempted": len(cities),
    }
