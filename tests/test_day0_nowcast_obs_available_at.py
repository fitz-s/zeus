# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/P1_BRIEF.md §5 (T1 write-through, T4 vocab) + §2a/§2b (ITEM 1)
# Purpose: ThePath P1 ITEM 1 antibodies — obs_available_at persistence on the Day0 nowcast lane.
#   T1  RELATIONSHIP: write_nowcast_run persists observation_available_at byte-for-byte
#       (no now() re-synthesis); read-back round-trip preserves it.
#   T4  VOCAB: obs_availability_provenance is enumerated; bad value raises.
#   MIG: idempotent migration — the two new nullable columns exist after schema build and
#       a second build is a no-op; legacy (absent-keyword) writes record NULL + 'UNVERIFIED'.
"""ThePath P1 ITEM 1 antibodies: obs_available_at persistence + provenance vocab.

These are relationship/antibody tests, not function tests: they make
"write-time-as-availability substitution" and "silent provenance" structurally
unconstructable on the Day0 nowcast lane.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from src.state.db import _create_day0_horizon_platt_fits, _create_day0_nowcast_runs
from src.state.day0_nowcast_store import read_nowcast_runs, write_nowcast_run


def _fresh_forecasts_conn() -> sqlite3.Connection:
    """In-memory conn with the two day0 forecast-class tables (FK satisfied)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # read_nowcast_runs returns dict(row); needs Row factory
    conn.execute("PRAGMA foreign_keys = ON")
    _create_day0_horizon_platt_fits(conn)
    _create_day0_nowcast_runs(conn)
    # Mirror init_schema_forecasts: the bin_grid_id/bin_schema_id columns are
    # added via ALTER (T4 retrofit), not by the static CREATE helper. The fresh
    # CREATE above already carries the obs columns, so only the bin pair is needed.
    for _alter in (
        "ALTER TABLE day0_nowcast_runs ADD COLUMN bin_grid_id TEXT",
        "ALTER TABLE day0_nowcast_runs ADD COLUMN bin_schema_id TEXT",
    ):
        try:
            conn.execute(_alter)
        except sqlite3.OperationalError as _exc:
            if "duplicate column" not in str(_exc).lower():
                raise
    # Satisfy the fit_run_id FK with one parent row.
    conn.execute(
        """
        INSERT INTO day0_horizon_platt_fits (
            fit_run_id, fit_version, alpha, beta,
            gamma_morning, gamma_afternoon, gamma_post_peak,
            delta, epsilon, n_obs, schema_version, source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("fit-001", 1, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10, 4, "live_fit"),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# MIG — idempotent migration: columns present, second build is a no-op.
# ---------------------------------------------------------------------------
def test_obs_available_at_columns_exist_and_migration_idempotent() -> None:
    conn = _fresh_forecasts_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(day0_nowcast_runs)")}
    assert "observation_available_at" in cols
    assert "obs_availability_provenance" in cols

    # Re-running the table creator is a no-op (CREATE TABLE IF NOT EXISTS) — idempotent.
    _create_day0_nowcast_runs(conn)
    cols2 = {row[1] for row in conn.execute("PRAGMA table_info(day0_nowcast_runs)")}
    assert cols2 == cols
    conn.close()


# ---------------------------------------------------------------------------
# T1 — RELATIONSHIP: byte-for-byte write-through, no now() re-synthesis.
# ---------------------------------------------------------------------------
def test_t1_observation_available_at_round_trips_byte_for_byte() -> None:
    conn = _fresh_forecasts_conn()
    avail = "2026-06-15T13:45:01.123456+00:00"

    write_nowcast_run(
        market_slug="boston-2026-06-15-high",
        temperature_metric="high",
        target_date="2026-06-15",
        observation_time="2026-06-15T14:00:00",
        fit_run_id="fit-001",
        p_nowcast=np.array([0.6]),
        p_now_raw=np.array([0.55]),
        hours_remaining=4.0,
        daypart="afternoon",
        source="live_nowcast",
        observation_available_at=avail,
        obs_availability_provenance="live_fetch",
        conn=conn,
    )

    rows = read_nowcast_runs(
        "boston-2026-06-15-high", "high", "2026-06-15", conn=conn
    )
    assert len(rows) == 1
    # Byte-for-byte: the persisted value is exactly what was supplied, not a now() stamp.
    assert rows[0]["observation_available_at"] == avail
    assert rows[0]["obs_availability_provenance"] == "live_fetch"
    conn.close()


# ---------------------------------------------------------------------------
# T1b — absent availability => NULL + honest UNVERIFIED (backward-compat).
# ---------------------------------------------------------------------------
def test_t1b_absent_availability_records_null_and_unverified() -> None:
    conn = _fresh_forecasts_conn()

    # Legacy call signature: no obs kwargs at all -> defaults apply.
    write_nowcast_run(
        market_slug="dallas-2026-06-15-high",
        temperature_metric="high",
        target_date="2026-06-15",
        observation_time="2026-06-15T08:00:00",
        fit_run_id="fit-001",
        p_nowcast=None,
        p_now_raw=None,
        hours_remaining=5.0,
        daypart="morning",
        source="live_nowcast",
        conn=conn,
    )

    rows = read_nowcast_runs("dallas-2026-06-15-high", "high", "2026-06-15", conn=conn)
    assert len(rows) == 1
    assert rows[0]["observation_available_at"] is None
    assert rows[0]["obs_availability_provenance"] == "UNVERIFIED"
    conn.close()


# ---------------------------------------------------------------------------
# T4 — VOCAB: provenance enumeration enforced; bad value raises.
# ---------------------------------------------------------------------------
def test_t4_provenance_vocab_rejects_unknown_value() -> None:
    conn = _fresh_forecasts_conn()
    with pytest.raises(ValueError, match="obs_availability_provenance"):
        write_nowcast_run(
            market_slug="x-2026-06-15-high",
            temperature_metric="high",
            target_date="2026-06-15",
            observation_time="2026-06-15T14:00:00",
            fit_run_id="fit-001",
            p_nowcast=None,
            p_now_raw=None,
            hours_remaining=4.0,
            daypart="afternoon",
            source="live_nowcast",
            observation_available_at="2026-06-15T13:00:00+00:00",
            obs_availability_provenance="made_up_source",  # not in vocab
            conn=conn,
        )
    conn.close()


@pytest.mark.parametrize(
    "value",
    ["live_fetch", "rolling_hourly_imported_at", "archive_dissemination_lag", "UNVERIFIED"],
)
def test_t4_all_valid_provenance_values_accepted(value: str) -> None:
    conn = _fresh_forecasts_conn()
    write_nowcast_run(
        market_slug=f"city-{value}-2026-06-15-high",
        temperature_metric="high",
        target_date="2026-06-15",
        observation_time="2026-06-15T14:00:00",
        fit_run_id="fit-001",
        p_nowcast=None,
        p_now_raw=None,
        hours_remaining=4.0,
        daypart="afternoon",
        source="live_nowcast",
        observation_available_at="2026-06-15T13:00:00+00:00",
        obs_availability_provenance=value,
        conn=conn,
    )
    rows = read_nowcast_runs(f"city-{value}-2026-06-15-high", "high", "2026-06-15", conn=conn)
    assert rows[0]["obs_availability_provenance"] == value
    conn.close()


# ---------------------------------------------------------------------------
# T4b — non-ISO availability is rejected (never silently coerced).
# ---------------------------------------------------------------------------
def test_t4b_non_iso_availability_raises() -> None:
    conn = _fresh_forecasts_conn()
    with pytest.raises(ValueError, match="ISO-parseable"):
        write_nowcast_run(
            market_slug="bad-iso-2026-06-15-high",
            temperature_metric="high",
            target_date="2026-06-15",
            observation_time="2026-06-15T14:00:00",
            fit_run_id="fit-001",
            p_nowcast=None,
            p_now_raw=None,
            hours_remaining=4.0,
            daypart="afternoon",
            source="live_nowcast",
            observation_available_at="not-a-timestamp",
            obs_availability_provenance="live_fetch",
            conn=conn,
        )
    conn.close()


# ---------------------------------------------------------------------------
# Root-cause regression (ITEM 2 latent): schema_version stamped by the writer
# must satisfy the deployed CHECK (which only permitted IN (3,4)). A value that
# fails the CHECK would IntegrityError and (under the monitor's fail-soft) write
# 0 rows. This asserts a real insert succeeds against the legacy CHECK shape.
# ---------------------------------------------------------------------------
def test_writer_schema_version_satisfies_legacy_check_constraint() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    _create_day0_horizon_platt_fits(conn)
    # Build the LEGACY-shaped table (deployed prod CHECK: schema_version IN (3,4)),
    # plus the new obs columns so the writer's INSERT column list is satisfiable.
    conn.execute(
        """
        CREATE TABLE day0_nowcast_runs (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            run_seq INTEGER NOT NULL,
            nowcast_event_id TEXT,
            fit_run_id TEXT NOT NULL REFERENCES day0_horizon_platt_fits(fit_run_id),
            p_nowcast_json TEXT,
            p_now_raw_json TEXT,
            hours_remaining REAL NOT NULL,
            daypart TEXT NOT NULL CHECK (daypart IN ('pre_sunrise','morning','afternoon','post_peak')),
            schema_version INTEGER NOT NULL CHECK (schema_version IN (3, 4)),
            source TEXT NOT NULL CHECK (source IN ('live_nowcast','replay')),
            bin_grid_id TEXT, bin_schema_id TEXT,
            observation_available_at TEXT,
            obs_availability_provenance TEXT,
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO day0_horizon_platt_fits (
            fit_run_id, fit_version, alpha, beta,
            gamma_morning, gamma_afternoon, gamma_post_peak,
            delta, epsilon, n_obs, schema_version, source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("fit-legacy", 1, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10, 4, "live_fit"),
    )
    conn.commit()

    # Must NOT raise IntegrityError on the legacy CHECK — the writer stamps a
    # version the deployed table accepts.
    write_nowcast_run(
        market_slug="legacy-check-2026-06-15-high",
        temperature_metric="high",
        target_date="2026-06-15",
        observation_time="2026-06-15T14:00:00",
        fit_run_id="fit-legacy",
        p_nowcast=None,
        p_now_raw=None,
        hours_remaining=4.0,
        daypart="afternoon",
        source="live_nowcast",
        conn=conn,
    )
    n = conn.execute("SELECT COUNT(*) FROM day0_nowcast_runs").fetchone()[0]
    assert n == 1, "writer must produce a row against the deployed (3,4) CHECK shape"
    conn.close()
