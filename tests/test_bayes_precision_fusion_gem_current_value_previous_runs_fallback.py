# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: K2 gem_global resolution 2026-06-09, curl-verified against the open-meteo
#   single-runs API: cmc_gem_gdps_15km is NOT served there AT ALL (even cadence-valid 00z runs
#   return modelRunUnavailable; verified 2026-06-09T18Z). gem_seamless would serve HRDPS/RDPS
#   for North-American cities — a DIFFERENT physical product than the GDPS de-bias history
#   (the exact source-identity violation class of the EB-bias wrong-set bug ff7f33dd5b).
#   Resolution: gem's CURRENT value comes from its previous_runs row at the SAME natural key —
#   the SAME GDPS product the walk-forward de-bias history is fit on (MORE source-consistent
#   than single_runs; the ECMWF anchor needs an ifs025->ifs9 bridge precisely because its
#   history product != live product; gem now has NO such mismatch).
"""K2 antibody: gem_global current value = previous_runs fallback, declared and scoped.

Relationship being pinned (download lane -> materializer read boundary):
  - The single-runs API structurally cannot serve GDPS -> the download must NOT request the
    known-dead leg (no 51-cities-per-cycle fail-soft noise masquerading as transient).
  - The materializer's persisted-current read serves gem_global from the previous_runs row at
    the SAME natural key (city, metric, target_date, source_cycle_time) — DECLARED exception,
    single model. Any other model with a missing single_runs row stays missing (no silent
    endpoint masking: a broken single_runs capture for gfs must stay LOUD, not be papered
    over by previous_runs).
"""
from __future__ import annotations

import sqlite3

from src.data.replacement_forecast_materializer import _read_persisted_current_capture

CYCLE = "2026-06-09T00:00:00+00:00"
OTHER_CYCLE = "2026-06-08T18:00:00+00:00"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER, model TEXT, forecast_value_c REAL,
            city TEXT, metric TEXT, target_date TEXT, lead_days INTEGER,
            source_cycle_time TEXT, endpoint TEXT
        )
        """
    )
    return conn


def _insert(conn, rid, model, value, endpoint, cycle=CYCLE):
    conn.execute(
        "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, model, value, "Amsterdam", "high", "2026-06-10", 1, cycle, endpoint),
    )


def _read(conn):
    return _read_persisted_current_capture(
        conn, city="Amsterdam", metric="high", target_date="2026-06-10",
        lead_days=1, source_cycle_time_iso=CYCLE,
    )


def test_gem_current_served_from_previous_runs_at_same_natural_key() -> None:
    conn = _conn()
    _insert(conn, 1, "gfs_global", 21.0, "single_runs")
    _insert(conn, 2, "icon_global", 20.5, "single_runs")
    _insert(conn, 3, "jma_seamless", 22.0, "single_runs")
    _insert(conn, 4, "gem_global", 19.5, "previous_runs")  # GDPS not on single-runs API
    out = _read(conn)
    assert "gem_global" in out, (
        "gem_global must be served from its previous_runs row at the same natural key — "
        "GDPS is structurally unavailable on the single-runs API (curl-verified 2026-06-09)"
    )
    assert out["gem_global"] == (19.5, 4)
    assert set(out) == {"gfs_global", "icon_global", "jma_seamless", "gem_global"}


def test_gem_single_runs_row_wins_over_previous_runs_when_both_exist() -> None:
    # If open-meteo ever starts serving GDPS on single-runs, the forward row takes priority.
    conn = _conn()
    _insert(conn, 1, "gem_global", 19.9, "single_runs")
    _insert(conn, 2, "gem_global", 19.5, "previous_runs")
    out = _read(conn)
    assert out["gem_global"] == (19.9, 1)


def test_non_gem_models_do_not_fall_back_to_previous_runs() -> None:
    # No silent endpoint masking: a missing gfs single_runs capture must STAY missing/loud.
    conn = _conn()
    _insert(conn, 1, "gfs_global", 21.0, "previous_runs")
    _insert(conn, 2, "icon_global", 20.5, "single_runs")
    out = _read(conn)
    assert "gfs_global" not in out
    assert set(out) == {"icon_global"}


def test_gem_fallback_respects_natural_key_cycle() -> None:
    # A previous_runs row from a DIFFERENT cycle must not leak into this capture.
    conn = _conn()
    _insert(conn, 1, "icon_global", 20.5, "single_runs")
    _insert(conn, 2, "gem_global", 19.5, "previous_runs", cycle=OTHER_CYCLE)
    out = _read(conn)
    assert "gem_global" not in out


def test_download_skips_gem_single_runs_leg_but_keeps_previous_runs_leg(tmp_path) -> None:
    from datetime import UTC, datetime

    from src.data.bayes_precision_fusion_download import (
        BayesPrecisionFusionDownloadTarget,
        download_bayes_precision_fusion_extra_raw_inputs,
    )
    from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema

    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_shadow_schema(conn)
    conn.close()

    single_calls: list[str] = []
    prev_calls: list[str] = []

    def _single(*, model, **_kw):
        single_calls.append(model)
        return 20.0

    def _previous(*, model, **_kw):
        prev_calls.append(model)
        return 19.5

    target = BayesPrecisionFusionDownloadTarget(
        city="Amsterdam", latitude=52.3, longitude=4.77, timezone_name="Europe/Amsterdam",
        target_date="2026-06-10", lead_days=1, metric="high",
    )
    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC), targets=[target],
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )
    assert "gem_global" not in single_calls, (
        "the single-runs API structurally cannot serve GDPS — the known-dead request leg must "
        "not be fired (51-cities of fail-soft noise masquerading as a transient drop)"
    )
    assert "gem_global" in prev_calls  # the GDPS capture lane stays alive
    assert "gfs_global" in single_calls  # other models unaffected
