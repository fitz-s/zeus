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


# ===========================================================================
# (A) lead-consistent multi-snapshot daily residual  +  (B) significance shrink
# ---------------------------------------------------------------------------
# These pin the two corrections that stop the producer manufacturing spurious
# corrections on noisy/transition cities while preserving the real ones:
#   (A) the daily residual is the MEAN of the in-band (trade-lead 3-5d, 60-144h)
#       snapshots' member-means — not one arbitrary latest snapshot. Removes the
#       single-snapshot draw noise AND matches the lead the q is computed at.
#   (B) per-city b is shrunk toward 0 by a smooth t-gate
#       b_shrunk = b * t^2/(t^2 + c), c=2 (half-shrink at |t|~1.41 ~ "needs ~2sigma").
# ===========================================================================

# Trade-lead band the live q uses (config discovery.preferred_lead_days=[3,4,5]).
LEAD_3D, LEAD_4D, LEAD_5D, LEAD_6D = 72.0, 96.0, 120.0, 144.0
NOW_PROOF = "2026-06-10T00:00:00+00:00"


def _proof_window_dates():
    """7 distinct settled days, each settled strictly before NOW_PROOF."""
    return [
        ("2026-06-02", "2026-06-03T18:00:00+00:00"),
        ("2026-06-01", "2026-06-02T18:00:00+00:00"),
        ("2026-05-31", "2026-06-01T18:00:00+00:00"),
        ("2026-05-29", "2026-05-30T18:00:00+00:00"),
        ("2026-05-27", "2026-05-28T18:00:00+00:00"),
        ("2026-05-25", "2026-05-26T18:00:00+00:00"),
        ("2026-05-24", "2026-05-25T18:00:00+00:00"),
    ]


def _seed_city_from_daily_residuals(conn, city, daily_residuals, *, unit="C",
                                    settle_value=20.0):
    """Seed a city so that each settled day's IN-BAND (3-5d) snapshot mean equals
    ``settle_value + residual``. For every day we write THREE in-band snapshots
    (72h/96h/120h) whose member-means straddle the intended day-mean, plus a noisy
    OUT-OF-BAND nowcast (24h) snapshot that must be IGNORED. This proves the
    multi-snapshot-in-band averaging (A): if the producer picked the single latest
    (nowcast) snapshot it would read the out-of-band noise instead.
    """
    days = _proof_window_dates()
    assert len(daily_residuals) <= len(days)
    for (d, sat), resid in zip(days, daily_residuals):
        day_mean = settle_value + resid  # forecast mean we want at trade lead
        # three in-band snapshots whose means average to exactly day_mean
        _snap(conn, city, d, [day_mean - 1.0, day_mean, day_mean + 1.0], unit=unit,
              lead=LEAD_3D, avail=f"{d}T00:00:00Z")
        _snap(conn, city, d, [day_mean - 0.5, day_mean, day_mean + 0.5], unit=unit,
              lead=LEAD_4D, avail=f"{d}T01:00:00Z")
        _snap(conn, city, d, [day_mean - 2.0, day_mean, day_mean + 2.0], unit=unit,
              lead=LEAD_5D, avail=f"{d}T02:00:00Z")
        # OUT-OF-BAND nowcast with a wildly different mean (latest available_at) —
        # the producer must NOT use this; (A) selects the trade-lead band.
        _snap(conn, city, d, [day_mean + 50.0], unit=unit, lead=24.0,
              avail=f"{d}T23:00:00Z")
        _settle(conn, city, d, settle_value, unit=unit, settled_at=sat)


def _b_shrunk(out):
    return out["effective_bias_c"]


def test_A_inband_multisnapshot_ignores_nowcast_noise(fc_conn):
    """(A) The daily residual uses the MEAN of the 3-5d in-band snapshots, not the
    latest (nowcast) snapshot. Seed each day's in-band mean to settle exactly (resid 0)
    while the nowcast is +50 off — the bias must be ~0, proving the out-of-band noisy
    draw is excluded and the in-band snapshots are averaged.
    """
    _seed_city_from_daily_residuals(fc_conn, "BandProbe", [0.0] * 5, settle_value=20.0)
    out = d7.compute_city_bias(fc_conn, city="BandProbe", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    assert out["n_window"] == 5
    assert out["raw_bias_c"] == pytest.approx(0.0, abs=1e-6), \
        "nowcast (+50) leaked — in-band selection/averaging broken"
    assert _b_shrunk(out) == pytest.approx(0.0, abs=1e-6)


def test_A_nearest_lead_fallback_when_no_inband(fc_conn):
    """(A) If a day has NO snapshot in the 3-5d band, fall back to the nearest-lead
    snapshot and record that a fallback happened. Here every day has only a 24h
    (nowcast) snapshot; residual must still be computed from it (nearest lead).
    """
    days = _proof_window_dates()
    for d, sat in days[:4]:
        _snap(fc_conn, "FallbackCity", d, [18.0, 20.0, 22.0], unit="C", lead=24.0,
              avail=f"{d}T00:00:00Z")  # mean 20, only lead = 24h (out of band)
        _settle(fc_conn, "FallbackCity", d, 22.0, unit="C", settled_at=sat)
    out = d7.compute_city_bias(fc_conn, city="FallbackCity", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    assert out["n_window"] == 4
    # residual = 20 - 22 = -2 per day (degC), from the nearest-lead fallback
    assert out["raw_bias_c"] == pytest.approx(-2.0, abs=1e-6)
    assert out["n_fallback_days"] == 4, "fallback-day count not recorded"


def test_B_shrink_seoul_noise_collapses_toward_zero(fc_conn):
    """Seoul PROOF: real in-band residuals are ~0 with one dead cold episode; raw b
    is weakly significant (|t|~1) so shrinkage collapses it toward 0. Must be <= ~1.0C
    in magnitude (would have been -4.11 under the old single-snapshot flat mean).
    """
    seoul = [-0.726, 1.774, -1.002, 0.390, 1.144, 0.080, 1.058]  # measured in-band
    _seed_city_from_daily_residuals(fc_conn, "Seoul", seoul, settle_value=25.0)
    out = d7.compute_city_bias(fc_conn, city="Seoul", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    assert abs(_b_shrunk(out)) <= 1.0, \
        f"Seoul not collapsed: b_shrunk={_b_shrunk(out):+.3f} (raw {out['raw_bias_c']:+.3f})"


def test_B_shrink_tokyo_real_bias_preserved(fc_conn):
    """Tokyo PROOF: tight, highly-significant real cold bias (|t|~7.5) must be PRESERVED
    at <= -4.0C after shrinkage (shrinkage barely touches strong stable biases).
    """
    tokyo = [-4.341, -7.710, -4.638, -6.156, -6.010, -3.938, -2.328]
    _seed_city_from_daily_residuals(fc_conn, "Tokyo", tokyo, settle_value=25.0)
    out = d7.compute_city_bias(fc_conn, city="Tokyo", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    assert _b_shrunk(out) <= -4.0, \
        f"Tokyo real bias not preserved: b_shrunk={_b_shrunk(out):+.3f}"


def test_B_shrink_taipei_real_bias_preserved(fc_conn):
    """Taipei PROOF: real ~-2 to -3 bias preserved within ~0.5C (b_shrunk stays a clear
    cold correction, not collapsed). Raw b ~ -2.34, |t| ~ 2.5.
    """
    taipei = [-5.673, 0.663, -0.773, 0.285, -4.299, -3.187, -3.404]
    _seed_city_from_daily_residuals(fc_conn, "Taipei", taipei, settle_value=28.0)
    out = d7.compute_city_bias(fc_conn, city="Taipei", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    bs = _b_shrunk(out)
    # distance from bs to the nearest edge of the real cold band [-3, -2] (0 if inside).
    lo, hi = -3.0, -2.0
    nearest_edge_dist = 0.0 if lo <= bs <= hi else min(abs(bs - lo), abs(bs - hi))
    assert nearest_edge_dist <= 0.5, f"Taipei not preserved: b_shrunk={bs:+.3f}"
    assert bs <= -1.5, f"Taipei collapsed too far: b_shrunk={bs:+.3f}"


def test_B_shrink_zero_variance_full_bias(fc_conn):
    """SYNTHETIC PROOF: residuals [-5,-5,-5,-5,-5] (zero variance, n=5) -> b_shrunk ~ -5.
    Zero variance => infinite t => full bias, no shrinkage.
    """
    _seed_city_from_daily_residuals(fc_conn, "ZeroVar", [-5.0] * 5, settle_value=20.0)
    out = d7.compute_city_bias(fc_conn, city="ZeroVar", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    assert _b_shrunk(out) == pytest.approx(-5.0, abs=1e-6), \
        f"zero-variance bias must be full: b_shrunk={_b_shrunk(out):+.3f}"


def test_B_shrink_high_variance_collapses_to_zero(fc_conn):
    """SYNTHETIC PROOF: residuals [-4,+4,-4,+4,0] (mean ~0, high variance) -> b_shrunk ~ 0.
    Mean zero with large spread => no significant bias => fully shrunk.
    """
    _seed_city_from_daily_residuals(fc_conn, "HighVar", [-4.0, 4.0, -4.0, 4.0, 0.0],
                                    settle_value=20.0)
    out = d7.compute_city_bias(fc_conn, city="HighVar", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    assert abs(_b_shrunk(out)) <= 0.5, \
        f"high-variance zero-mean must collapse: b_shrunk={_b_shrunk(out):+.3f}"


# ===========================================================================
# (representativeness sigma) residual_sd_c persisted alongside the shrunk bias
# ---------------------------------------------------------------------------
# A downstream variance term reads model_bias_ens.residual_sd_c to inflate q_lcb so it
# honestly reflects per-city representativeness uncertainty (the operator removed the
# canary cap; honest q_lcb is the only protection). The persisted residual_sd_c MUST be
# the std of the trailing-window daily residuals (degC) — the per-DAY spread, which stays
# LARGE for a noisy city even after the bias MEAN was shrunk to ~0.
# ===========================================================================

def test_residual_std_persisted_equals_daily_residual_std(fc_conn, world_conn):
    """The written model_bias_ens.residual_sd_c equals statistics.stdev of the trailing-
    window daily residuals (degC), and equals the producer's returned residual_std_c.
    """
    import statistics as _stats

    seoul = [-0.726, 1.774, -1.002, 0.390, 1.144, 0.080, 1.058]
    _seed_city_from_daily_residuals(fc_conn, "Seoul", seoul, settle_value=25.0)
    out = d7.compute_city_bias(fc_conn, city="Seoul", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    expected_std = _stats.stdev(seoul)  # degC residual std of the 7 daily residuals
    assert out["residual_std_c"] == pytest.approx(expected_std, abs=1e-6)
    assert out["sd_c"] == pytest.approx(expected_std, abs=1e-6)

    d7.write_city_bias(world_conn, city="Seoul", metric="high", data_version=OPD,
                       bias=out, now_iso=NOW_PROOF)
    world_conn.commit()
    row = world_conn.execute(
        "SELECT residual_sd_c, effective_bias_c FROM model_bias_ens "
        "WHERE city=? AND error_model_family=?", ("Seoul", "edli_per_city_v1"),
    ).fetchone()
    assert row is not None
    assert row["residual_sd_c"] == pytest.approx(expected_std, abs=1e-6)
    # The KEY relationship: the bias MEAN was shrunk toward 0 (|eff|<=1) but the
    # representativeness sigma is UNSHRUNK and large (>1C) — q_lcb sees honest spread.
    assert abs(row["effective_bias_c"]) <= 1.0
    assert row["residual_sd_c"] > 1.0, \
        "representativeness sigma collapsed with the bias mean (q_lcb would over-trust)"


def test_residual_std_large_when_bias_shrunk_high_variance(fc_conn, world_conn):
    """High-variance zero-mean city: effective_bias_c ~ 0 but residual_sd_c is LARGE.
    This is the honesty invariant — shrinking the mean must not shrink the sigma.
    """
    import statistics as _stats

    resids = [-4.0, 4.0, -4.0, 4.0, 0.0]
    _seed_city_from_daily_residuals(fc_conn, "HighVar", resids, settle_value=20.0)
    out = d7.compute_city_bias(fc_conn, city="HighVar", metric="high",
                               data_version=OPD, now_iso=NOW_PROOF,
                               window_days=7, min_n=3)
    assert out is not None
    d7.write_city_bias(world_conn, city="HighVar", metric="high", data_version=OPD,
                       bias=out, now_iso=NOW_PROOF)
    world_conn.commit()
    row = world_conn.execute(
        "SELECT residual_sd_c, effective_bias_c FROM model_bias_ens "
        "WHERE city=? AND error_model_family=?", ("HighVar", "edli_per_city_v1"),
    ).fetchone()
    assert abs(row["effective_bias_c"]) <= 0.5  # mean collapsed
    assert row["residual_sd_c"] == pytest.approx(_stats.stdev(resids), abs=1e-6)
    assert row["residual_sd_c"] >= 3.0  # spread preserved (honest q_lcb inflation)
