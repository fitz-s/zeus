# Created: 2026-05-19
# Last reused or audited: 2026-06-07
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5 (Option B)
#   + docs/the_path/P1_BRIEF.md §2b/§2c (ThePath P1 ITEM 1 obs_available_at persistence, 2026-06-07)
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
from datetime import date
from typing import Optional

import numpy as np

_NEI_V1_PREFIX = "nei_v1_"
_NEI_V1_SEP = "|"
_NEI_V1_DIGEST_CHARS = 16

# Sentinel value matching the AFTER INSERT trigger on day0_nowcast_runs.
# Any NULL writer-bypass will produce this string via the trigger.
_NEI_V1_BACKSTOP_SENTINEL = "nei_v1_BACKSTOP_NULL_WRITER_BYPASS"

IDENTITY_FIT_RUN_ID = "hpf_v1_identity_conservative_v1"
IDENTITY_FIT_ARTIFACT_ID = "hpf_v1"


def build_identity_platt_fit():
    """Documented conservative Day0 horizon fit used to bootstrap live logging.

    This fit claims zero skill: ``predict_proba(p)==p``. It exists so the live
    nowcast lane can write evidence rows and start the data clock automatically
    instead of relying on an operator-run script after every empty DB/restart.
    """
    from src.calibration.day0_horizon_calibration import HorizonPlattFit

    return HorizonPlattFit(
        alpha=1.0,
        beta=0.0,
        gamma_morning=0.0,
        gamma_afternoon=0.0,
        gamma_post_peak=0.0,
        delta=0.0,
        epsilon=0.0,
        fit_artifact_id=IDENTITY_FIT_ARTIFACT_ID,
        fit_run_id=IDENTITY_FIT_RUN_ID,
        fit_date=date.today().isoformat(),
        n_obs=0,
        sample_period_start="",
        sample_period_end="",
    )


def ensure_identity_platt_fit(
    *,
    fit_artifact_id: str = IDENTITY_FIT_ARTIFACT_ID,
    conn: Optional[sqlite3.Connection] = None,
):
    """Ensure the conservative identity fit exists and return the latest fit.

    Idempotent. With ``conn=None`` this writes through the canonical LIVE forecasts
    writer path. Tests may pass a temp connection to keep the operation local.
    """
    fit = read_latest_platt_fit(fit_artifact_id=fit_artifact_id, conn=conn)
    if fit is not None:
        return fit
    identity = build_identity_platt_fit()
    write_platt_fit(identity, conn=conn)
    return read_latest_platt_fit(fit_artifact_id=fit_artifact_id, conn=conn)


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
        ZEUS_FORECASTS_DB_PATH,
        get_forecasts_connection,
    )
    from src.state.db_writer_lock import WriteClass, db_writer_lock
    # ThePath P1 ITEM 2 root-cause fix (2026-06-07): the deployed
    # day0_horizon_platt_fits CHECK only permits schema_version IN (3, 4) (fresh
    # DBs widen to 3,4,5). B2 had frozen this stamp at 7, which violates EVERY
    # deployed/fresh CHECK -> the INSERT IntegrityErrors -> the wired write is
    # swallowed by the fail-soft monitor wrapper -> read_latest_platt_fit() stays
    # None -> the Day0 nowcast lane never fires. Stamp 4 (accepted by deployed
    # IN(3,4) AND fresh IN(3,4,5)), mirroring the write_nowcast_run fix above.
    SCHEMA_FORECASTS_VERSION = 4

    own_conn = conn is None
    if own_conn:
        conn = get_forecasts_connection(write_class=WriteClass.LIVE)

    try:
        with db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.LIVE):
            # The deployed column is `fit_version` (TEXT NOT NULL); the Python
            # contract names the same semantic value `fit_artifact_id` on the
            # HorizonPlattFit dataclass. Map dataclass.fit_artifact_id -> SQL
            # fit_version. (The prior INSERT named a non-existent
            # `fit_artifact_id` column -> OperationalError before the CHECK even
            # ran; this is the dominant of the two latent write_platt_fit bugs.)
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
                    fit.fit_run_id, fit.fit_artifact_id,
                    float(fit.alpha), float(fit.beta),
                    float(fit.gamma_morning), float(fit.gamma_afternoon), float(fit.gamma_post_peak),
                    float(fit.delta), float(fit.epsilon),
                    fit.fit_date or None, fit.n_obs,
                    fit.sample_period_start or None, fit.sample_period_end or None,
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
    bin_grid_id: Optional[str] = None,
    bin_schema_id: Optional[str] = None,
    observation_available_at: Optional[str] = None,
    obs_availability_provenance: str = "UNVERIFIED",
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Write a day0_nowcast_runs row. Returns the nowcast_event_id (nei_v1_ hash).

    run_seq is derived atomically under db_writer_lock(LIVE) via MAX(run_seq)+1
    scoped to (market_slug, temperature_metric, target_date, observation_time).

    conn=None -> get_forecasts_connection(write_class=WriteClass.LIVE).

    p_nowcast, p_now_raw: np.ndarray or None; stored as JSON arrays.
    bin_grid_id, bin_schema_id: propagated from ensemble_snapshots
        (F4 retrofit — SCHEMA_FORECASTS_VERSION 5, T4 2026-05-21).

    observation_available_at: UTC ISO timestamp — the wall-clock time Zeus could
        query the observation that fed this run (ThePath P1 ITEM 1, 2026-06-07).
        Source: Day0ObservationContext.observation_available_at (= now()-at-fetch,
        stamped at observation_client). The raw value is written VERBATIM; this
        function NEVER substitutes now(). None -> NULL (honest UNVERIFIED).
    obs_availability_provenance: enumerated provenance for the availability stamp,
        one of {'live_fetch','rolling_hourly_imported_at','archive_dissemination_lag',
        'UNVERIFIED'}. Defaults to 'UNVERIFIED' so absent availability is visible,
        never silently treated as honest (Fitz #4 authority field). Validated here
        because SQLite ALTER cannot add the CHECK on already-migrated DBs.
    """
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        get_forecasts_connection,
    )
    from src.state.db_writer_lock import WriteClass, db_writer_lock
    # B2 froze the forecast-class provenance counter at 7, but the live
    # day0_nowcast_runs CHECK constraint predates that freeze and only permits
    # schema_version IN (3, 4) on the deployed table (fresh DBs now permit
    # 3,4,5,7). SQLite cannot widen a CHECK via ALTER without a table rebuild
    # (a TRUTH_REWRITE-class op we will not do on the live forecasts DB), so a
    # writer stamping 7 silently fails the CHECK -> swallowed by the fail-soft
    # monitor wrapper -> 0 rows. Stamp 4 (accepted by EVERY existing and future
    # table variant) so the lane can actually write. ThePath P1 ITEM 2 root-cause
    # fix (2026-06-07): removes the latent CHECK-violation; additive and safe.
    SCHEMA_FORECASTS_VERSION = 4

    _valid_dayparts = frozenset({"pre_sunrise", "morning", "afternoon", "post_peak"})
    if daypart not in _valid_dayparts:
        raise ValueError(f"daypart must be one of {sorted(_valid_dayparts)}, got {daypart!r}")
    if source not in ("live_nowcast", "replay"):
        raise ValueError(f"source must be 'live_nowcast' or 'replay', got {source!r}")

    # ThePath P1 ITEM 1: provenance vocab + ISO-parse guards (writer-side because
    # the deployed table cannot carry the CHECK via ALTER). UNVERIFIED is the
    # honest default for absent availability.
    _valid_provenance = frozenset(
        {"live_fetch", "rolling_hourly_imported_at", "archive_dissemination_lag", "UNVERIFIED"}
    )
    if obs_availability_provenance not in _valid_provenance:
        raise ValueError(
            "obs_availability_provenance must be one of "
            f"{sorted(_valid_provenance)}, got {obs_availability_provenance!r}"
        )
    if observation_available_at is not None:
        # Assert the supplied stamp parses as a timestamp; never rewrite it.
        from datetime import datetime as _dt

        try:
            _dt.fromisoformat(str(observation_available_at).replace("Z", "+00:00"))
        except (TypeError, ValueError) as _exc:
            raise ValueError(
                f"observation_available_at must be ISO-parseable, got {observation_available_at!r}"
            ) from _exc

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
                    schema_version, source,
                    bin_grid_id, bin_schema_id,
                    observation_available_at, obs_availability_provenance
                ) VALUES (
                    ?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,  ?,?,  ?,?,  ?,?
                )
                """,
                (
                    market_slug, temperature_metric,
                    target_date, observation_time, row_seq,
                    nei, fit_run_id,
                    p_nowcast_json, p_now_raw_json,
                    float(hours_remaining), daypart,
                    SCHEMA_FORECASTS_VERSION, source,
                    bin_grid_id, bin_schema_id,
                    observation_available_at, obs_availability_provenance,
                ),
            )
            conn.commit()
            return nei
    finally:
        if own_conn and conn is not None:
            conn.close()


def read_latest_platt_fit(
    *,
    fit_artifact_id: str = "hpf_v1",
    conn: Optional[sqlite3.Connection] = None,
):
    """Return the most-recently written HorizonPlattFit for the given fit_artifact_id.

    Returns None when no fit row exists (e.g. before first calibration run).
    conn=None -> get_forecasts_connection_read_only().
    """
    from src.calibration.day0_horizon_calibration import HorizonPlattFit
    from src.state.db import get_forecasts_connection_read_only

    own_conn = conn is None
    if own_conn:
        conn = get_forecasts_connection_read_only()
        conn.row_factory = sqlite3.Row

    try:
        # Deployed column is `fit_version` (TEXT NOT NULL); it stores the semantic
        # fit_artifact_id value (e.g. "hpf_v1"). The Python API keeps the
        # fit_artifact_id keyword but must filter on the real SQL column. (The
        # prior WHERE/SELECT referenced a non-existent `fit_artifact_id` column,
        # mirroring the writer bug; this read would OperationalError once a row
        # existed.)
        row = conn.execute(
            """
            SELECT * FROM day0_horizon_platt_fits
            WHERE fit_version = ?
            ORDER BY rowid DESC LIMIT 1
            """,
            (fit_artifact_id,),
        ).fetchone()
        if row is None:
            return None
        r = dict(row)
        return HorizonPlattFit(
            alpha=float(r["alpha"]),
            beta=float(r["beta"]),
            gamma_morning=float(r["gamma_morning"]),
            gamma_afternoon=float(r["gamma_afternoon"]),
            gamma_post_peak=float(r["gamma_post_peak"]),
            delta=float(r["delta"]),
            epsilon=float(r["epsilon"]),
            fit_artifact_id=r.get("fit_version", fit_artifact_id),
            fit_run_id=r["fit_run_id"],
            fit_date=r.get("fit_date") or None,
            n_obs=r.get("n_obs"),
            sample_period_start=r.get("sample_period_start") or None,
            sample_period_end=r.get("sample_period_end") or None,
        )
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
