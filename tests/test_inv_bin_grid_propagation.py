# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7.4 (sha 00c2399742)
"""Antibody test: INV-bin-grid-propagation

Invariant: for every day0_nowcast_runs row written after T4_MERGE_DATE,
bin_grid_id IS NOT NULL AND matches ensemble_snapshots_v2.bin_grid_id
for the triggering snapshot (JOIN-MATCH).

Cross-module relationship test:
  forecasts.day0_nowcast_runs (F4 retrofit adds bin_grid_id column)
  vs forecasts.ensemble_snapshots_v2 (source of bin_grid_id)

bin_grid_id propagation path (production):
  ensemble_snapshots_v2.bin_grid_id
    → _read_v2_snapshot_metadata (evaluator.py)
    → v2_snapshot_meta["bin_grid_id"]
    → write_nowcast_run(bin_grid_id=...)
    → day0_nowcast_runs.bin_grid_id

Skips when forecasts DB is absent (CI / paper environments).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.analysis.market_analysis_vnext import T4_MERGE_DATE
from src.state.db import ZEUS_FORECASTS_DB_PATH


def _open_forecasts_ro() -> "sqlite3.Connection | None":
    """Open forecasts DB read-only. Returns None if absent."""
    try:
        conn = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def test_inv_bin_grid_no_null_post_t4() -> None:
    """INV-bin-grid-propagation (phase 1): no NULL bin_grid_id in day0_nowcast_runs
    after T4_MERGE_DATE.

    Guards against the regression where the deferred-write path fails to thread
    bin_grid_id from v2_snapshot_meta to write_nowcast_run.

    Skips when T4_MERGE_DATE is still the placeholder or forecasts DB is absent.
    """
    if T4_MERGE_DATE == "2026-05-XX":
        pytest.skip("T4_MERGE_DATE not yet set — skip until post-merge")

    conn = _open_forecasts_ro()
    if conn is None:
        pytest.skip("forecasts DB not present in this environment — live-only antibody")

    try:
        null_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM day0_nowcast_runs
            WHERE bin_grid_id IS NULL
              AND observation_time >= ?
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f"day0_nowcast_runs.bin_grid_id column missing — SCHEMA_FORECASTS_VERSION 5 "
            f"ALTER has not run on this DB. exc={exc}"
        )
    finally:
        conn.close()

    assert null_count == 0, (
        f"INV-bin-grid-propagation: {null_count} day0_nowcast_runs rows "
        f"have bin_grid_id IS NULL after T4_MERGE_DATE={T4_MERGE_DATE!r}"
    )


def test_inv_bin_grid_join_match() -> None:
    """INV-bin-grid-propagation (phase 2 — JOIN-MATCH): for every day0_nowcast_runs
    row after T4_MERGE_DATE, bin_grid_id must MATCH ensemble_snapshots_v2.bin_grid_id
    for the triggering snapshot.

    JOIN path:
      day0_nowcast_runs.market_slug + target_date + temperature_metric + observation_time
      → ensemble_snapshots_v2 (city ~ market_slug prefix, same date/metric).

    Skips when T4_MERGE_DATE is placeholder, DB is absent, or JOIN fails
    (in which case the NULL check above is the primary guard).
    """
    if T4_MERGE_DATE == "2026-05-XX":
        pytest.skip("T4_MERGE_DATE not yet set — skip until post-merge")

    conn = _open_forecasts_ro()
    if conn is None:
        pytest.skip("forecasts DB not present in this environment — live-only antibody")

    try:
        mismatch_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM day0_nowcast_runs nr
            JOIN ensemble_snapshots_v2 es
              ON es.city        LIKE nr.market_slug || '%'
             AND es.target_date  = nr.target_date
             AND es.temperature_metric = nr.temperature_metric
            WHERE nr.observation_time >= ?
              AND nr.bin_grid_id IS NOT NULL
              AND es.bin_grid_id IS NOT NULL
              AND nr.bin_grid_id != es.bin_grid_id
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]
    except sqlite3.OperationalError:
        pytest.skip(
            "JOIN-MATCH query failed (schema or table absent) — "
            "NULL check in test_inv_bin_grid_no_null_post_t4 is the primary guard"
        )
    finally:
        conn.close()

    assert mismatch_count == 0, (
        f"INV-bin-grid-propagation JOIN-MATCH: {mismatch_count} day0_nowcast_runs rows "
        f"have bin_grid_id that mismatches ensemble_snapshots_v2.bin_grid_id "
        f"after T4_MERGE_DATE={T4_MERGE_DATE!r}"
    )
