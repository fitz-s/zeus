# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7.4 (sha 00c2399742)
"""Antibody test: INV-bin-grid-propagation

Invariant: for every day0_nowcast_runs row post-T4 retrofit, bin_grid_id IS NOT
NULL AND matches ensemble_snapshots_v2.bin_grid_id for the triggering snapshot.

Cross-module relationship test:
  forecasts.day0_nowcast_runs (F4 retrofit adds bin_grid_id column)
  vs forecasts.ensemble_snapshots_v2 (source of bin_grid_id)

bin_grid_id propagation path (production pass):
  ensemble_snapshots_v2.bin_grid_id → evaluator caller site → day0_nowcast_runs.bin_grid_id
  NOT from cycle_runtime.bins (no propagation path — Phase 1 T2 finding).

SCAFFOLD status: xfail because day0_nowcast_runs.bin_grid_id column does not
exist until SCHEMA_FORECASTS_VERSION 4→5 production ALTER.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T4 production pending; day0_nowcast_runs.bin_grid_id column absent "
        "until SCHEMA_FORECASTS_VERSION 5 ALTER (SCAFFOLD)"
    ),
)
def test_inv_bin_grid_propagation() -> None:
    """INV-bin-grid-propagation: every day0_nowcast_runs row written after
    T4 retrofit must have bin_grid_id IS NOT NULL matching ensemble_snapshots_v2.

    Single forecasts-DB read path; no ATTACH (INV-37 trivially honored).

    SCAFFOLD: fires xfail via OperationalError (no such column: bin_grid_id)
    when the forecasts DB is accessible but the T4 ALTER has not yet run.
    Skips when forecasts DB is absent (CI / paper environments).

    Production assertion phase 1 (activated in T4 production pass):
      SELECT COUNT(*) FROM day0_nowcast_runs WHERE bin_grid_id IS NULL must be 0
      for rows inserted after T4_MERGE_DATE.

    Production assertion phase 2 (strengthened post-T4 merge):
      For each day0_nowcast_runs row, bin_grid_id must MATCH
      ensemble_snapshots_v2.bin_grid_id for the triggering snapshot (JOIN on
      fit_run_id or observation_time+market_slug foreign-key path). The IS NULL
      check here is the SCAFFOLD approximation; production pass adds the JOIN-MATCH
      assertion once the propagation path from ensemble_snapshots_v2 is wired.
    """
    from src.analysis.market_analysis_vnext import T4_MERGE_DATE
    from src.state.db import ZEUS_FORECASTS_DB_PATH

    try:
        conn = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        pytest.skip("forecasts DB not present in this environment — live-only antibody")
    conn.row_factory = sqlite3.Row

    try:
        # This SELECT fires OperationalError "no such column: bin_grid_id"
        # until SCHEMA_FORECASTS_VERSION 5 ALTER runs → xfail RED.
        null_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM day0_nowcast_runs
            WHERE bin_grid_id IS NULL
              AND observation_time >= ?
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]

        # Production assertion: zero null bin_grid_id rows post-retrofit
        assert null_count == 0, (
            f"INV-bin-grid-propagation: {null_count} day0_nowcast_runs rows "
            f"have bin_grid_id IS NULL after T4_MERGE_DATE={T4_MERGE_DATE!r}"
        )
    finally:
        conn.close()
