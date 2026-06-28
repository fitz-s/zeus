# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/P1_BRIEF.md §1 (q_d0 leg: day0_nowcast_runs=0,
#   "Lane never fired in production"; day0_horizon_platt_fits=0) + §5 T1
#   (write-through relationship) + iron rules (conservative/identity fit, no
#   fabricated skill). ThePath P1 "activate the Day0 nowcast lane".
# Purpose: ANTIBODY / RELATIONSHIP test for the lane-activation seam.
#   LANE-WRITES: with a persisted (conservative/identity) HorizonPlattFit,
#     monitor_refresh._maybe_write_day0_nowcast STOPS short-circuiting at the
#     `if fit is None: return` guard (monitor_refresh.py:1768) and INSERTS a real
#     day0_nowcast_runs row CARRYING observation_available_at.
#   FIT-WRITE: write_platt_fit succeeds against the DEPLOYED-shape table
#     (CHECK schema_version IN (3,4), column `fit_version`) -> the two latent
#     write_platt_fit bugs (fit_artifact_id column name; schema_version=7) are
#     regression-locked.
#   AUTO-BOOTSTRAP: with NO fit, the lane persists the documented conservative
#     identity fit and writes the row; no operator script is required after restart.
"""ThePath P1 lane-activation antibodies.

Relationship test: the obs-timing data clock starts when the conservative identity
HorizonPlattFit is present or can be auto-bootstrapped. These exercise the REAL
monitor_refresh._maybe_write_day0_nowcast
against a temp DB (never LIVE) by binding the day0_nowcast_store functions to a
temp-DB connection — the monitor function itself is left byte-identical.
"""
from __future__ import annotations

import sqlite3
import types

import numpy as np
import pytest

from src.calibration.day0_horizon_calibration import HorizonPlattFit
from src.state.db import _create_day0_horizon_platt_fits, _create_day0_nowcast_runs
from src.state import day0_nowcast_store
from src.engine import monitor_refresh


# --------------------------------------------------------------------------- #
# Fixtures: a deployed-shape temp DB + minimal monitor call-site stand-ins.
# --------------------------------------------------------------------------- #
def _deployed_shape_conn() -> sqlite3.Connection:
    """In-memory DB mirroring the DEPLOYED LIVE shape.

    day0_horizon_platt_fits: CHECK schema_version IN (3,4), column `fit_version`
      (the deployed shape — NOT the fresh IN(3,4,5) variant — so the writer fix
      is proven against the exact constraint the live DB carries).
    day0_nowcast_runs: built by the canonical creator so the obs columns + FK +
      backstop trigger match production.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE day0_horizon_platt_fits (
            fit_run_id TEXT PRIMARY KEY, fit_version TEXT NOT NULL,
            alpha REAL NOT NULL, beta REAL NOT NULL,
            gamma_morning REAL NOT NULL, gamma_afternoon REAL NOT NULL, gamma_post_peak REAL NOT NULL,
            delta REAL NOT NULL, epsilon REAL NOT NULL,
            fit_date TEXT, n_obs INTEGER NOT NULL,
            sample_period_start TEXT, sample_period_end TEXT,
            schema_version INTEGER NOT NULL CHECK (schema_version IN (3, 4)),
            source TEXT NOT NULL CHECK (source IN ('live_fit', 'replay_fit'))
        )
        """
    )
    _create_day0_nowcast_runs(conn)
    conn.execute(
        """
        CREATE TABLE market_events (
            market_slug TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            outcome TEXT
        )
        """
    )
    # bin pair is added via ALTER in init_schema_forecasts; the writer references them.
    for _alter in (
        "ALTER TABLE day0_nowcast_runs ADD COLUMN bin_grid_id TEXT",
        "ALTER TABLE day0_nowcast_runs ADD COLUMN bin_schema_id TEXT",
    ):
        try:
            conn.execute(_alter)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.commit()
    return conn


def _identity_fit() -> HorizonPlattFit:
    """The documented CONSERVATIVE/IDENTITY fit (predict_proba(p)==p, zero skill)."""
    return HorizonPlattFit(
        alpha=1.0, beta=0.0,
        gamma_morning=0.0, gamma_afternoon=0.0, gamma_post_peak=0.0,
        delta=0.0, epsilon=0.0,
        fit_artifact_id="hpf_v1",
        fit_run_id="hpf_v1_identity_conservative_v1",
        fit_date="2026-06-07", n_obs=0,
    )


def _bind_store_to_conn(monkeypatch, conn: sqlite3.Connection) -> None:
    """Redirect the lazily-imported store functions to the temp conn.

    _maybe_write_day0_nowcast does `from src.state.day0_nowcast_store import
    read_latest_platt_fit, write_nowcast_run` then calls them with conn=None
    (LIVE). Patch both at the module so the test never touches the LIVE DB while
    the monitor function stays byte-identical.
    """
    real_read = day0_nowcast_store.read_latest_platt_fit
    real_write = day0_nowcast_store.write_nowcast_run
    real_ensure = day0_nowcast_store.ensure_identity_platt_fit
    real_resolve = day0_nowcast_store.resolve_market_slug_for_position_identity

    def _read_bound(*, fit_artifact_id: str = "hpf_v1", **_kw):
        return real_read(fit_artifact_id=fit_artifact_id, conn=conn)

    def _write_bound(**kw):
        kw["conn"] = conn
        return real_write(**kw)

    def _ensure_bound(*, fit_artifact_id: str = "hpf_v1", **_kw):
        return real_ensure(fit_artifact_id=fit_artifact_id, conn=conn)

    def _resolve_bound(**kw):
        kw["conn"] = conn
        return real_resolve(**kw)

    monkeypatch.setattr(day0_nowcast_store, "read_latest_platt_fit", _read_bound)
    monkeypatch.setattr(day0_nowcast_store, "write_nowcast_run", _write_bound)
    monkeypatch.setattr(day0_nowcast_store, "ensure_identity_platt_fit", _ensure_bound)
    monkeypatch.setattr(day0_nowcast_store, "resolve_market_slug_for_position_identity", _resolve_bound)


def _call_lane(
    conn: sqlite3.Connection,
    *,
    obs_avail: str | None,
    market_slug: str | None = "boston-2026-06-15-high",
    token_id: str | None = None,
    condition_id: str | None = None,
    bin_label: str = "Will the highest temperature in Boston be 20°C on June 15?",
) -> None:
    """Drive the REAL _maybe_write_day0_nowcast with minimal stand-ins."""
    position = types.SimpleNamespace(
        market_slug=market_slug,
        trade_id="t-1",
        token_id=token_id,
        condition_id=condition_id,
        market_id=condition_id,
        city="Boston",
        target_date="2026-06-15",
        bin_label=bin_label,
    )
    temporal_context = types.SimpleNamespace(daypart="afternoon")
    temperature_metric = types.SimpleNamespace(temperature_metric="high")
    from datetime import date

    monitor_refresh._maybe_write_day0_nowcast(
        position=position,
        hours_remaining=4.0,
        temporal_context=temporal_context,
        p_cal_full=np.array([0.6, 0.4]),
        p_raw_vector=np.array([0.55, 0.45]),
        temperature_metric=temperature_metric,
        target_d=date(2026, 6, 15),
        observation_time="2026-06-15T14:00:00",
        observation_available_at=obs_avail,
    )


# --------------------------------------------------------------------------- #
# LANE-WRITES: persisted fit -> a real day0_nowcast_runs row WITH obs_available_at
# --------------------------------------------------------------------------- #
def test_lane_writes_row_with_obs_available_at_after_fit_persisted(monkeypatch) -> None:
    conn = _deployed_shape_conn()

    # 1. Persist the conservative/identity fit via the REAL writer (proves the
    #    fit_version + schema_version=4 fixes against the deployed (3,4) CHECK).
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM day0_horizon_platt_fits").fetchone()[0] == 1

    # 2. With the fit present, the lane must STOP short-circuiting and write a row.
    _bind_store_to_conn(monkeypatch, conn)
    avail = "2026-06-15T13:45:01.123456+00:00"
    _call_lane(conn, obs_avail=avail)

    rows = conn.execute(
        "SELECT * FROM day0_nowcast_runs ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 1, "lane must write exactly one day0_nowcast_runs row"
    row = dict(rows[0])
    # The obs-timing clock value is carried byte-for-byte (no now() re-synthesis).
    assert row["observation_available_at"] == avail
    assert row["obs_availability_provenance"] == "live_fetch"
    # FK + fit linkage intact.
    assert row["fit_run_id"] == "hpf_v1_identity_conservative_v1"
    assert row["daypart"] == "afternoon"
    assert row["temperature_metric"] == "high"
    conn.close()


# --------------------------------------------------------------------------- #
# LANE-WRITES (absent availability): NULL + honest UNVERIFIED, lane still fires.
# --------------------------------------------------------------------------- #
def test_lane_writes_null_unverified_when_availability_absent(monkeypatch) -> None:
    conn = _deployed_shape_conn()
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)

    _bind_store_to_conn(monkeypatch, conn)
    _call_lane(conn, obs_avail=None)

    rows = conn.execute("SELECT * FROM day0_nowcast_runs").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["observation_available_at"] is None
    assert row["obs_availability_provenance"] == "UNVERIFIED"  # honest, never now()
    conn.close()


# --------------------------------------------------------------------------- #
# AUTO-BOOTSTRAP: with NO fit, the lane persists identity fit and writes.
# --------------------------------------------------------------------------- #
def test_lane_auto_bootstraps_identity_fit_when_missing(monkeypatch) -> None:
    conn = _deployed_shape_conn()
    # NO write_platt_fit upfront: runtime must create the conservative identity fit.
    _bind_store_to_conn(monkeypatch, conn)
    _call_lane(conn, obs_avail="2026-06-15T13:45:01+00:00")

    fit_n = conn.execute("SELECT COUNT(*) FROM day0_horizon_platt_fits").fetchone()[0]
    run_n = conn.execute("SELECT COUNT(*) FROM day0_nowcast_runs").fetchone()[0]
    assert fit_n == 1
    assert run_n == 1
    conn.close()


# --------------------------------------------------------------------------- #
# SQL position compatibility: market_slug is JSON-only, so live SQL positions
# must resolve through canonical market_events instead of silently skipping.
# --------------------------------------------------------------------------- #
def test_lane_resolves_missing_position_market_slug_from_market_events_token(monkeypatch) -> None:
    conn = _deployed_shape_conn()
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)
    conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, temperature_metric,
            condition_id, token_id, range_label, outcome
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            "highest-temperature-in-boston-on-june-15-2026",
            "Boston",
            "2026-06-15",
            "high",
            "0xcond",
            "yes-token",
            "Will the highest temperature in Boston be 20°C on June 15?",
            "Will the highest temperature in Boston be 20°C on June 15?",
        ),
    )
    conn.commit()

    _bind_store_to_conn(monkeypatch, conn)
    _call_lane(conn, obs_avail="2026-06-15T13:45:01+00:00", market_slug=None, token_id="yes-token")

    row = conn.execute("SELECT market_slug FROM day0_nowcast_runs").fetchone()
    assert row["market_slug"] == "highest-temperature-in-boston-on-june-15-2026"
    conn.close()


def test_lane_resolves_missing_position_market_slug_from_condition_bridge(monkeypatch) -> None:
    conn = _deployed_shape_conn()
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)
    conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, temperature_metric,
            condition_id, token_id, range_label, outcome
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            "highest-temperature-in-boston-on-june-15-2026",
            "Boston",
            "2026-06-15",
            "high",
            "0xcond",
            "yes-token",
            "Will the highest temperature in Boston be 20°C on June 15?",
            "Will the highest temperature in Boston be 20°C on June 15?",
        ),
    )
    conn.commit()

    _bind_store_to_conn(monkeypatch, conn)
    _call_lane(
        conn,
        obs_avail="2026-06-15T13:45:01+00:00",
        market_slug=None,
        token_id=None,
        condition_id="0xcond",
    )

    row = conn.execute("SELECT market_slug FROM day0_nowcast_runs").fetchone()
    assert row["market_slug"] == "highest-temperature-in-boston-on-june-15-2026"
    conn.close()


# --------------------------------------------------------------------------- #
# FIT-WRITE regression: write_platt_fit succeeds against the deployed (3,4) shape
# and round-trips through read_latest_platt_fit. Locks the two latent bugs:
#   (a) INSERT named a non-existent `fit_artifact_id` column (OperationalError);
#   (b) schema_version stamped 7, violating CHECK IN (3,4) (IntegrityError).
# --------------------------------------------------------------------------- #
def test_write_platt_fit_round_trips_on_deployed_shape() -> None:
    conn = _deployed_shape_conn()
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)

    stored = conn.execute(
        "SELECT fit_version, schema_version, source FROM day0_horizon_platt_fits"
    ).fetchone()
    assert stored["fit_version"] == "hpf_v1"        # dataclass.fit_artifact_id -> SQL fit_version
    assert stored["schema_version"] == 4            # accepted by deployed CHECK IN (3,4)
    assert stored["source"] == "live_fit"

    got = day0_nowcast_store.read_latest_platt_fit(fit_artifact_id="hpf_v1", conn=conn)
    assert got is not None
    assert got.fit_run_id == "hpf_v1_identity_conservative_v1"
    assert got.fit_artifact_id == "hpf_v1"
    # Identity property: zero claimed skill.
    for p in (0.1, 0.5, 0.9):
        pp = got.predict_proba(p, hours_remaining=4.0, daypart="afternoon",
                               temperature_metric_indicator=1.0)
        assert pp == pytest.approx(p, abs=1e-9)
    conn.close()


def test_write_platt_fit_idempotent_on_rerun() -> None:
    conn = _deployed_shape_conn()
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)
    day0_nowcast_store.write_platt_fit(_identity_fit(), conn=conn)  # INSERT OR IGNORE on PK
    n = conn.execute("SELECT COUNT(*) FROM day0_horizon_platt_fits").fetchone()[0]
    assert n == 1, "re-running the persist must not stack duplicate identity rows"
    conn.close()
