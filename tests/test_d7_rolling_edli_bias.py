# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: D-7 rolling per-city bias producer (replaces stale static-May bias).
#   Walk-forward OOS this session: bias_d7 MAE 1.882 < static 2.098 < grid_rep 2.193.
#   Relationship tests for the producer's cross-module invariants: strict causality
#   (settled_at <= decision_time, never look-ahead), unit-correctness in degC (no 1.8x
#   error), thin-n raw fallback (<MIN_N settled days -> no row), idempotent upsert.
"""Relationship/TDD tests for scripts/write_d7_rolling_edli_bias.py.

Cross-module invariants under test (these CROSS the settlement->residual->store boundary,
not just single-function behavior):

  (causality)   The D-7 bias for the trailing window uses ONLY settlements whose
                settled_at <= the decision/run time. A settlement settled in the future
                (look-ahead) is EXCLUDED even if its target_date falls in the window.
  (unit)        An F-settled city (San Francisco) residual is computed in degC and the
                written effective_bias_c is degC (NOT degF) — no 1.8x scale error. The
                live reader (event_reactor_adapter) re-multiplies by 1.8 for F-cities, so
                storing degF here would double-scale. train==serve.
  (thin-n)      A city with fewer than MIN_N (3) settled days in the window gets NO
                correction row written (raw fallback at the live reader).
  (idempotent)  Running the producer twice yields the SAME single row per (city,
                season,month,metric,ldv,lead_bucket) — daily upsert overwrite, no dupes.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import scripts.write_d7_rolling_edli_bias as d7

OPD = "ecmwf_opendata_mx2t3_local_calendar_day_max"


# ---------------------------------------------------------------------------
# In-memory forecasts-DB fixture (ensemble_snapshots + settlement_outcomes)
# ---------------------------------------------------------------------------

@pytest.fixture
def fc_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE ensemble_snapshots(
            city TEXT, target_date TEXT, temperature_metric TEXT, dataset_id TEXT,
            members_json TEXT, members_unit TEXT, lead_hours REAL, available_at TEXT,
            issue_time TEXT, authority TEXT,
            contributes_to_target_extrema INTEGER, boundary_ambiguous INTEGER,
            training_allowed INTEGER, causality_status TEXT)"""
    )
    c.execute(
        """CREATE TABLE settlement_outcomes(
            city TEXT, target_date TEXT, temperature_metric TEXT, settlement_value REAL,
            settlement_unit TEXT, settled_at TEXT, authority TEXT)"""
    )
    return c


@pytest.fixture
def world_conn():
    from src.calibration.ens_bias_repo import init_ens_bias_schema
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_ens_bias_schema(c)
    return c


def _snap(conn, city, date, members, *, unit="C", dv=OPD, metric="high", lead=24.0,
          avail=None, issue_time=None, authority="VERIFIED"):
    avail = avail or f"{date}T00:00:00Z"
    conn.execute(
        "INSERT INTO ensemble_snapshots "
        "(city,target_date,temperature_metric,dataset_id,members_json,members_unit,"
        "lead_hours,available_at,issue_time,authority,contributes_to_target_extrema,"
        "boundary_ambiguous,training_allowed,causality_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (city, date, metric, dv, json.dumps(members), unit, lead, avail, issue_time,
         authority, 1, 0, 1, "OK"),
    )


def _settle(conn, city, date, value, *, unit="C", settled_at, metric="high",
            authority="VERIFIED"):
    conn.execute(
        "INSERT INTO settlement_outcomes "
        "(city,target_date,temperature_metric,settlement_value,settlement_unit,"
        "settled_at,authority) VALUES (?,?,?,?,?,?,?)",
        (city, date, metric, value, unit, settled_at, authority),
    )


# ---------------------------------------------------------------------------
# (causality) look-ahead settlement is excluded
# ---------------------------------------------------------------------------

def test_causality_lookahead_settlement_excluded(fc_conn):
    """A settlement whose settled_at is AFTER the decision time must not enter the
    trailing window — even though its target_date is recent. Otherwise the D-7 bias
    would be contaminated by truth that was not yet known at decision time.
    """
    city = "Tokyo"
    now = "2026-06-10T00:00:00+00:00"
    # 3 legitimately-settled days (settled_at <= now): residual = mean(19,20,21)=20 - 22 = -2
    for d, sat in [("2026-06-07", "2026-06-08T18:00:00+00:00"),
                   ("2026-06-08", "2026-06-09T18:00:00+00:00"),
                   ("2026-06-09", "2026-06-09T19:00:00+00:00")]:
        _snap(fc_conn, city, d, [19.0, 20.0, 21.0], unit="C")
        _settle(fc_conn, city, d, 22.0, unit="C", settled_at=sat)
    # A LOOK-AHEAD row: target_date 2026-06-11 is the MOST RECENT (would be picked first by
    # the trailing-window sort), but its settled_at is AFTER now. If the producer ignored the
    # settled_at<=now cutoff, this huge-residual day (mean(29,30,31)=30 minus 10 = +20) would
    # dominate the window mean and flip the sign. It must be invisible at decision time.
    _snap(fc_conn, city, "2026-06-11", [29.0, 30.0, 31.0], unit="C")
    _settle(fc_conn, city, "2026-06-11", 10.0, unit="C",
            settled_at="2026-06-12T18:00:00+00:00")  # FUTURE settled_at -> excluded

    out = d7.compute_city_bias(fc_conn, city=city, metric="high",
                               data_version=OPD, now_iso=now, window_days=7, min_n=3)
    assert out is not None
    # Only the 3 legit days; each residual = -2
    assert out["n_window"] == 3, f"look-ahead row leaked: n={out['n_window']}"
    assert out["effective_bias_c"] == pytest.approx(-2.0, abs=1e-6)
    # The future settlement's target_date must NOT be in the window
    assert "2026-06-11" not in out["window_dates"]


# ---------------------------------------------------------------------------
# (unit) F-city residual + stored bias are degC, no 1.8x error
# ---------------------------------------------------------------------------

def test_unit_fcity_bias_is_degC_no_1p8_error(fc_conn):
    """San Francisco settles degF. Members are degF. The residual must be computed in
    degC (normalize both sides) and the stored effective_bias_c must be degC. A common
    bug stores the degF residual as effective_bias_c; the reader then multiplies by 1.8
    -> ~3.24x total over-correction. This pins the degC convention.
    """
    city = "San Francisco"
    now = "2026-06-10T00:00:00+00:00"
    # members mean 68F, settle 72F -> residual_F = -4F ; residual_C = -4/1.8 = -2.222C
    for d, sat in [("2026-06-07", "2026-06-08T18:00:00+00:00"),
                   ("2026-06-08", "2026-06-09T18:00:00+00:00"),
                   ("2026-06-09", "2026-06-09T19:00:00+00:00")]:
        _snap(fc_conn, city, d, [66.0, 68.0, 70.0], unit="degF")
        _settle(fc_conn, city, d, 72.0, unit="F", settled_at=sat)

    out = d7.compute_city_bias(fc_conn, city=city, metric="high",
                               data_version=OPD, now_iso=now, window_days=7, min_n=3)
    assert out is not None
    # degC residual = (68-72)/1.8 = -2.2222 ; NOT -4.0 (degF) and NOT -7.2 (1.8x error)
    assert out["effective_bias_c"] == pytest.approx((68.0 - 72.0) / 1.8, abs=1e-4)
    assert abs(out["effective_bias_c"] - (-4.0)) > 1.0, "stored value is degF (bug)"


# ---------------------------------------------------------------------------
# (thin-n) <MIN_N settled days -> no correction
# ---------------------------------------------------------------------------

def test_thin_n_no_row_written(fc_conn, world_conn):
    """A city with only 2 settled days in the window must get NO row (raw fallback).
    The live reader, finding no VERIFIED weight_live>0 row, falls back to raw members.
    """
    city = "Wuhan"
    now = "2026-06-10T00:00:00+00:00"
    for d, sat in [("2026-06-08", "2026-06-09T18:00:00+00:00"),
                   ("2026-06-09", "2026-06-09T19:00:00+00:00")]:
        _snap(fc_conn, city, d, [20.0, 21.0, 22.0], unit="C")
        _settle(fc_conn, city, d, 21.0, unit="C", settled_at=sat)

    out = d7.compute_city_bias(fc_conn, city=city, metric="high",
                               data_version=OPD, now_iso=now, window_days=7, min_n=3)
    assert out is None, "thin-n city must return None (no correction)"


# ---------------------------------------------------------------------------
# (idempotent) run twice -> same single row
# ---------------------------------------------------------------------------

def test_idempotent_upsert_same_row(fc_conn, world_conn):
    """Running write twice overwrites the same PK row — no duplicates, daily-safe."""
    city = "Shanghai"
    now = "2026-06-10T00:00:00+00:00"
    for d, sat in [("2026-06-07", "2026-06-08T18:00:00+00:00"),
                   ("2026-06-08", "2026-06-09T18:00:00+00:00"),
                   ("2026-06-09", "2026-06-09T19:00:00+00:00")]:
        _snap(fc_conn, city, d, [24.0, 25.0, 26.0], unit="C")
        _settle(fc_conn, city, d, 27.0, unit="C", settled_at=sat)

    for _ in range(2):
        out = d7.compute_city_bias(fc_conn, city=city, metric="high",
                                   data_version=OPD, now_iso=now, window_days=7, min_n=3)
        assert out is not None
        d7.write_city_bias(world_conn, city=city, metric="high", data_version=OPD,
                           bias=out, now_iso=now)
        world_conn.commit()

    rows = world_conn.execute(
        "SELECT * FROM model_bias_ens WHERE city=? AND error_model_family=?",
        (city, "edli_per_city_v1"),
    ).fetchall()
    assert len(rows) == 1, f"idempotent upsert produced {len(rows)} rows"
    assert rows[0]["effective_bias_c"] == pytest.approx(25.0 - 27.0, abs=1e-6)
    assert rows[0]["weight_live"] == 1.0
    assert (rows[0]["estimator"] or "").startswith("d7_rolling") or \
           "d7" in (rows[0]["estimator"] or "")


# ---------------------------------------------------------------------------
# (read-key parity) written row is readable by the live reader's exact key
# ---------------------------------------------------------------------------

def test_written_row_readable_by_live_reader_key(fc_conn, world_conn):
    """The row the producer writes must satisfy read_bias_model with the SAME args the
    live path uses (error_model_family + target_month coverage + VERIFIED). Otherwise the
    producer 'succeeds' but the live reader never sees the value (silent no-op).
    """
    from src.calibration.ens_bias_repo import read_bias_model
    from src.calibration.manager import season_from_date

    city = "Tokyo"
    now = "2026-06-10T00:00:00+00:00"
    for d, sat in [("2026-06-07", "2026-06-08T18:00:00+00:00"),
                   ("2026-06-08", "2026-06-09T18:00:00+00:00"),
                   ("2026-06-09", "2026-06-09T19:00:00+00:00")]:
        _snap(fc_conn, city, d, [25.0, 26.0, 27.0], unit="C")
        _settle(fc_conn, city, d, 28.0, unit="C", settled_at=sat)

    out = d7.compute_city_bias(fc_conn, city=city, metric="high",
                               data_version=OPD, now_iso=now, window_days=7, min_n=3)
    d7.write_city_bias(world_conn, city=city, metric="high", data_version=OPD,
                       bias=out, now_iso=now)
    world_conn.commit()

    season = season_from_date("2026-06-09")  # JJA (NH)
    row = read_bias_model(
        world_conn, city=city, season=season, metric="high",
        live_data_version=OPD, month=6, target_month=6,
        authority="VERIFIED", error_model_family="edli_per_city_v1",
    )
    assert row is not None, "live reader cannot see the producer's row (key mismatch)"
    assert row["effective_bias_c"] == pytest.approx(26.0 - 28.0, abs=1e-6)
