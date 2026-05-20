# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5 (Option B)
"""Day0 nowcast persistence — writer/reader for day0_nowcast_runs + day0_horizon_platt_fits.

Storage layer for T2 (Day0HighNowcastSignal calibration output).
Tables are forecast-class; writes to ZEUS_FORECASTS_DB_PATH under db_writer_lock(LIVE).

Writer pattern mirrors src/state/decision_events.py:
- nowcast_event_id (nei_v1_ prefix) computed writer-side via nowcast_event_id_v1_hash()
- AFTER INSERT trigger is a backstop sentinel for NULL writer-bypass
- run_seq derived atomically under db_writer_lock(LIVE)

INV-37 note: all writes go to zeus-forecasts.db (single DB).
No cross-DB ATTACH needed here; all day0_nowcast tables are forecast-class.

NOT_DAEMON_WIRED: caller-site wiring (evaluator.py + monitor_refresh.py) is a
separate operator-controlled step. This module exists in src/ but is not imported
from any daemon hot-path until that wiring lands.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

import numpy as np

_NEI_V1_PREFIX = "nei_v1_"
_NEI_V1_SEP = "|"
_NEI_V1_DIGEST_CHARS = 16

# Sentinel value matching the AFTER INSERT trigger on day0_nowcast_runs.
# Any NULL writer-bypass will produce this string via the trigger.
_NEI_V1_BACKSTOP_SENTINEL = "nei_v1_BACKSTOP_NULL_WRITER_BYPASS"


def nowcast_event_id_v1_hash(
    *,
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
    run_seq: int,
) -> str:
    """Compute the v1 nowcast_event_id for a day0_nowcast_runs row.

    Namespace: nei_v1_ — DISTINCT from deid_v1_ (decision events) and dgid_v1_ (calibration).
    Field order is version-locked.

    Example output: "nei_v1_a3b2c1d4e5f60718"
    """
    if not market_slug:
        raise ValueError("market_slug must be non-empty")
    canonical = (
        f"{market_slug}{_NEI_V1_SEP}"
        f"{temperature_metric}{_NEI_V1_SEP}"
        f"{target_date}{_NEI_V1_SEP}"
        f"{observation_time}{_NEI_V1_SEP}"
        f"{run_seq:010d}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_NEI_V1_DIGEST_CHARS]
    return f"{_NEI_V1_PREFIX}{digest}"


def write_platt_fit(
    fit,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Write a HorizonPlattFit record to day0_horizon_platt_fits.

    fit: HorizonPlattFit dataclass from src.calibration.day0_horizon_calibration.
    conn=None -> get_forecasts_connection(write_class=WriteClass.LIVE).

    Idempotent: INSERT OR IGNORE on fit_run_id PK.
    """
    from src.state.db import (
        SCHEMA_FORECASTS_VERSION,
        ZEUS_FORECASTS_DB_PATH,
        get_forecasts_connection,
    )
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    own_conn = conn is None
    if own_conn:
        conn = get_forecasts_connection(write_class=WriteClass.LIVE)

    try:
        with db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.LIVE):
            conn.execute(
                """
                INSERT OR IGNORE INTO day0_horizon_platt_fits (
                    fit_run_id, fit_version,
                    alpha, beta,
                    gamma_morning, gamma_afternoon, gamma_post_peak,
                    delta, epsilon,
                    fit_date, n_obs,
                    sample_period_start, sample_period_end,
                    schema_version, source
                ) VALUES (
                    ?,?,  ?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,  ?,?
                )
                """,
                (
                    fit.fit_run_id, fit.fit_version,
                    float(fit.alpha), float(fit.beta),
                    float(fit.gamma_morning), float(fit.gamma_afternoon), float(fit.gamma_post_peak),
                    float(fit.delta), float(fit.epsilon),
                    fit.fit_date or "", fit.n_obs,
                    fit.sample_period_start or "", fit.sample_period_end or "",
                    SCHEMA_FORECASTS_VERSION, "live_fit",
                ),
            )
            conn.commit()
    finally:
        if own_conn and conn is not None:
            conn.close()


def write_nowcast_run(
    *,
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
    fit_run_id: str,
    p_nowcast: "np.ndarray | None",
    p_now_raw: "np.ndarray | None",
    hours_remaining: float,
    daypart: str,
    source: str = "live_nowcast",
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Write a day0_nowcast_runs row. Returns the nowcast_event_id (nei_v1_ hash).

    run_seq is derived atomically under db_writer_lock(LIVE) via MAX(run_seq)+1
    scoped to (market_slug, temperature_metric, target_date, observation_time).

    conn=None -> get_forecasts_connection(write_class=WriteClass.LIVE).

    p_nowcast, p_now_raw: np.ndarray or None; stored as JSON arrays.
    """
    from src.state.db import (
        SCHEMA_FORECASTS_VERSION,
        ZEUS_FORECASTS_DB_PATH,
        get_forecasts_connection,
    )
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    _valid_dayparts = frozenset({"pre_sunrise", "morning", "afternoon", "post_peak"})
    if daypart not in _valid_dayparts:
        raise ValueError(f"daypart must be one of {sorted(_valid_dayparts)}, got {daypart!r}")
    if source not in ("live_nowcast", "replay"):
        raise ValueError(f"source must be 'live_nowcast' or 'replay', got {source!r}")

    p_nowcast_json = json.dumps(p_nowcast.tolist()) if p_nowcast is not None else None
    p_now_raw_json = json.dumps(p_now_raw.tolist()) if p_now_raw is not None else None

    own_conn = conn is None
    if own_conn:
        conn = get_forecasts_connection(write_class=WriteClass.LIVE)

    try:
        with db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.LIVE):
            row_seq = conn.execute(
                """
                SELECT COALESCE(MAX(run_seq), -1) + 1
                FROM day0_nowcast_runs
                WHERE market_slug = ? AND temperature_metric = ?
                  AND target_date = ? AND observation_time = ?
                """,
                (market_slug, temperature_metric, target_date, observation_time),
            ).fetchone()[0]

            nei = nowcast_event_id_v1_hash(
                market_slug=market_slug,
                temperature_metric=temperature_metric,
                target_date=target_date,
                observation_time=observation_time,
                run_seq=row_seq,
            )

            conn.execute(
                """
                INSERT INTO day0_nowcast_runs (
                    market_slug, temperature_metric,
                    target_date, observation_time, run_seq,
                    nowcast_event_id, fit_run_id,
                    p_nowcast_json, p_now_raw_json,
                    hours_remaining, daypart,
                    schema_version, source
                ) VALUES (
                    ?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,  ?,?
                )
                """,
                (
                    market_slug, temperature_metric,
                    target_date, observation_time, row_seq,
                    nei, fit_run_id,
                    p_nowcast_json, p_now_raw_json,
                    float(hours_remaining), daypart,
                    SCHEMA_FORECASTS_VERSION, source,
                ),
            )
            conn.commit()
            return nei
    finally:
        if own_conn and conn is not None:
            conn.close()


def read_nowcast_runs(
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    *,
    observation_time: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Read day0_nowcast_runs rows ordered by (observation_time, run_seq) ASC.

    observation_time=None -> all rows for (market_slug, temperature_metric, target_date).
    conn=None -> get_forecasts_connection_read_only().

    Returns list of dicts with all column values. p_nowcast_json / p_now_raw_json
    are raw JSON strings; caller parses with json.loads() as needed.
    """
    from src.state.db import get_forecasts_connection_read_only

    own_conn = conn is None
    if own_conn:
        conn = get_forecasts_connection_read_only()
        conn.row_factory = sqlite3.Row

    try:
        if observation_time is not None:
            rows = conn.execute(
                """
                SELECT * FROM day0_nowcast_runs
                WHERE market_slug = ? AND temperature_metric = ?
                  AND target_date = ? AND observation_time = ?
                ORDER BY observation_time, run_seq
                """,
                (market_slug, temperature_metric, target_date, observation_time),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM day0_nowcast_runs
                WHERE market_slug = ? AND temperature_metric = ?
                  AND target_date = ?
                ORDER BY observation_time, run_seq
                """,
                (market_slug, temperature_metric, target_date),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own_conn and conn is not None:
            conn.close()
