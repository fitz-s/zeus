# Created: 2026-06-09
# Last reused or audited: 2026-06-11
# Authority basis: K2 gem_global resolution 2026-06-09, curl-verified against the open-meteo
#   single-runs API: cmc_gem_gdps_15km is NOT served there AT ALL (even cadence-valid 00z runs
#   return modelRunUnavailable; verified 2026-06-09T18Z). gem_seamless would serve HRDPS/RDPS
#   for North-American cities — a DIFFERENT physical product than the GDPS de-bias history
#   (the exact source-identity violation class of the EB-bias wrong-set bug ff7f33dd5b).
#   Resolution: gem's CURRENT value comes from its previous_runs row at the SAME natural key —
#   the SAME GDPS product the walk-forward de-bias history is fit on (MORE source-consistent
#   than single_runs; the ECMWF anchor needs an ifs025->ifs9 bridge precisely because its
#   history product != live product; gem now has NO such mismatch).
#   SUPERSESSION (Task #32 follow-up, operator 2026-06-11): the gem-ONLY scoping is superseded
#   by the generalized 没有新的就用老的 serving rule (replacement_current_value_serving) — ANY
#   provider absent from single_runs at the selected cycle now serves its previous_runs row at
#   the SAME natural key, BRANDED served_via="previous_runs" in the fusion provenance (the old
#   law's "silent endpoint masking" objection is resolved by the branding: the substitution is
#   loud-by-provenance, not silent). Live evidence: JMA publishes 00/12Z only, so at every
#   06Z-cadence cycle jma_seamless could NEVER appear in single_runs (0/49 cities) and the
#   fusion ran served=4/5 — dropping the provider instead of serving its freshest previous run.
#   gem's behavior is BYTE-IDENTICAL under the generalized rule (pinned below).
"""K2 antibody: previous_runs current-value serving — gem unchanged, rule generalized.

Relationship being pinned (download lane -> materializer read boundary):
  - The single-runs API structurally cannot serve GDPS -> the download must NOT request the
    known-dead leg (no 51-cities-per-cycle fail-soft noise masquerading as transient).
  - The materializer's persisted-current read serves gem_global from the previous_runs row at
    the SAME natural key (city, metric, target_date, source_cycle_time) — byte-identical to the
    original edc598b440 behavior.
  - GENERALIZED (2026-06-11): any other model missing its single_runs row at the cycle is now
    served from its previous_runs row at the same natural key too — BRANDED in provenance
    (served_via="previous_runs"), never silent. A model absent from BOTH endpoints stays
    dropped; a different cycle's row never leaks (natural-key isolation preserved).
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


def test_non_gem_previous_runs_substitution_is_served_and_branded() -> None:
    # SUPERSEDED LAW (2026-06-11, Task #32 follow-up): the old pin here ("a missing gfs
    # single_runs capture must STAY missing") dropped JMA from EVERY 06Z-cadence fusion (JMA
    # publishes 00/12Z only — structurally absent from 06Z single_runs) and cost the whole city
    # its conservative edge. The generalized rule serves the previous_runs row at the SAME
    # natural key; "loud" is now delivered by PROVENANCE BRANDING (served_via="previous_runs"
    # recorded per instrument), not by dropping the provider.
    from src.data.replacement_current_value_serving import read_current_instrument_values

    conn = _conn()
    _insert(conn, 1, "gfs_global", 21.0, "previous_runs")
    _insert(conn, 2, "icon_global", 20.5, "single_runs")
    out = _read(conn)
    assert out["gfs_global"] == (21.0, 1), "previous_runs row serves the current value"
    assert set(out) == {"icon_global", "gfs_global"}
    served = read_current_instrument_values(
        conn, city="Amsterdam", metric="high", target_date="2026-06-10",
        source_cycle_time_iso=CYCLE,
    )
    assert served["gfs_global"].served_via == "previous_runs"  # BRANDED, never silent
    assert served["icon_global"].served_via == "single_runs"


def test_gem_fallback_can_serve_prior_possessed_cycle() -> None:
    # A prior previous_runs row is already-possessed evidence for the same target when the
    # selected anchor cycle has no fresher value.
    conn = _conn()
    _insert(conn, 1, "icon_global", 20.5, "single_runs")
    _insert(conn, 2, "gem_global", 19.5, "previous_runs", cycle=OTHER_CYCLE)
    out = _read(conn)
    assert out["gem_global"] == (19.5, 2)


def test_gem_fallback_rejects_future_cycle() -> None:
    conn = _conn()
    _insert(conn, 1, "icon_global", 20.5, "single_runs")
    _insert(conn, 2, "gem_global", 19.5, "previous_runs", cycle="2026-06-09T06:00:00+00:00")
    out = _read(conn)
    assert "gem_global" not in out


def test_download_no_longer_fetches_dropped_globals(tmp_path) -> None:
    # 2026-06-17 COARSE-GLOBAL REMOVAL + JMA DROP: gem_global (~15km GDPS), gfs_global (0.25/25km)
    # and the settlement-cold jma_seamless were dropped from model_selection.DECORR_GLOBALS, so
    # they leave BAYES_PRECISION_FUSION_EXTRA_MODELS and are no longer requested in EITHER leg
    # (forward single_runs OR previous_runs). The generalized previous_runs-substitution lane stays
    # alive for the models that remain (proven by the ukmo_global case in test_fusion_upgrade_trigger).
    from datetime import UTC, datetime

    from src.data.bayes_precision_fusion_download import (
        BayesPrecisionFusionDownloadTarget,
        download_bayes_precision_fusion_extra_raw_inputs,
    )
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
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
    for dropped in ("gem_global", "gfs_global", "jma_seamless"):
        assert dropped not in single_calls, f"{dropped} dropped from the fusion -> no single_runs fetch"
        assert dropped not in prev_calls, f"{dropped} dropped from the fusion -> no previous_runs fetch"
    # the models that remain are still fetched (lane alive)
    assert "icon_global" in single_calls and "ukmo_global_deterministic_10km" in single_calls
