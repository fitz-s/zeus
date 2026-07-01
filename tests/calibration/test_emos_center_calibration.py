# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS/NGR affine center calibration (consult REQ-20260701-010328).
# Tests the pure affine estimator (shrink-to-identity + slope clamp) and the fail-soft lookup.
import json
import sqlite3

import pytest

from scripts import fit_emos_center_calibration as fit_script

from src.calibration.emos_center_calibration import (
    apply_affine,
    apply_affine_in_support,
    fit_affine_eb,
    lookup_affine,
)


# ---- EB affine estimator (data-derived shrink, no kappa / no clamp) --------------------------
def _pool(n_cities=4, lo=10, hi=30):
    """An unbiased pool: settle == center for every city (identity target)."""
    return {f"c{i}": [(float(t), float(t)) for t in range(lo, hi)] for i in range(n_cities)}


def test_eb_identity_when_all_cities_unbiased():
    out = fit_affine_eb(_pool(5))
    assert all(abs(a) < 1e-6 and abs(b - 1.0) < 1e-6 for a, b in out.values())


def test_eb_pool_too_small_returns_identity():
    cp = {"a": [(float(t), float(t) + 2.0) for t in range(10, 25)],
          "b": [(float(t), float(t)) for t in range(10, 25)]}
    out = fit_affine_eb(cp)                                   # < 3 cities -> cannot estimate tau^2
    assert out["a"] == (0.0, 1.0) and out["b"] == (0.0, 1.0)


def test_eb_degenerate_city_is_identity():
    cp = _pool(4)
    cp["thin"] = [(1.0, 3.0)]                                 # < MIN_CITY_POINTS -> identity
    assert fit_affine_eb(cp)["thin"] == (0.0, 1.0)


def test_eb_recovers_clean_constant_bias():
    # A perfectly clean +3 bias (se==0) is recovered ~fully: EB shrinks by uncertainty, and there is
    # none here. Slope stays ~1 (constant offset, not a tilt).
    cp = _pool(4)
    cp["hot"] = [(float(t), float(t) + 3.0) for t in range(10, 30)]
    a, b = fit_affine_eb(cp)["hot"]
    mx = sum(range(10, 30)) / 20
    assert (a + b * mx - mx) == pytest.approx(3.0, abs=0.2)
    assert abs(b - 1.0) < 0.05


def test_eb_shrinks_noisy_bias_below_raw():
    # A city whose mean bias is +1 but ESTIMATED with noise gets pulled below +1: the shrink is a
    # function of the city's own sampling variance, not a hand-set constant.
    cp = _pool(4)
    cp["noisy"] = [(float(t), float(t) + 1.0 + (2.0 if t % 2 else -2.0)) for t in range(10, 30)]
    a, b = fit_affine_eb(cp)["noisy"]
    mx = sum(range(10, 30)) / 20
    corr = a + b * mx - mx
    assert 0.0 < corr < 1.0                                   # raw mean bias +1, shrunk below it


def test_apply_affine():
    assert apply_affine(20.0, 0.0, 1.0) == 20.0            # identity
    assert apply_affine(20.0, 1.0, 1.05) == pytest.approx(22.0)


def test_apply_affine_in_support_clamps_outside_range():
    # b=0.6 line; inside [20,30] the affine applies, outside the delta is held flat at the endpoint.
    a, b, xlo, xhi = 8.0, 0.6, 20.0, 30.0
    assert apply_affine_in_support(25.0, a, b, xlo, xhi) == pytest.approx(8.0 + 0.6 * 25.0)  # in-range
    # above support: input clamped to 30 -> correction frozen at the x_hi value
    assert apply_affine_in_support(40.0, a, b, xlo, xhi) == pytest.approx(8.0 + 0.6 * 30.0)
    # below support: input clamped to 20
    assert apply_affine_in_support(5.0, a, b, xlo, xhi) == pytest.approx(8.0 + 0.6 * 20.0)
    # no range -> plain affine
    assert apply_affine_in_support(40.0, a, b, None, None) == pytest.approx(8.0 + 0.6 * 40.0)


# ---- artifact lookup (fail-soft; returns (a, b, x_lo, x_hi); lead-gated) ---------------------
IDENT = (0.0, 1.0, None, None)


def _write(tmp_path, monkeypatch, cities, **top):
    art = {"authority": "emos_center_calibration_v1", "served_lead": 1,
           "metrics": {"high": {"served_lead": 1, "cities": cities}}}
    art.update(top)
    (tmp_path / "emos_center_calibration.json").write_text(json.dumps(art), encoding="utf-8")
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)


def test_lookup_identity_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)
    assert lookup_affine("Seoul", "high", 1) == IDENT


def test_lookup_serves_gated_city(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "Seoul": {"a": 0.54, "b": 1.062, "serve": True, "x_lo": 20.0, "x_hi": 30.0},
        "Madrid": {"a": 0.2, "b": 1.01, "serve": False},
    })
    assert lookup_affine("Seoul", "high", 1) == (pytest.approx(0.54), pytest.approx(1.062), 20.0, 30.0)
    assert lookup_affine("Madrid", "high", 1) == IDENT    # serve=False -> identity
    assert lookup_affine("Tokyo", "high", 1) == IDENT     # absent -> identity
    assert lookup_affine("Seoul", "low", 1) == IDENT      # wrong metric -> identity


def test_lookup_lead_gate(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"Seoul": {"a": 0.5, "b": 1.06, "serve": True}})
    assert lookup_affine("Seoul", "high", 1)[:2] == (pytest.approx(0.5), pytest.approx(1.06))  # served lead
    assert lookup_affine("Seoul", "high", 2) == IDENT     # wrong lead -> identity (no extrapolation)
    assert lookup_affine("Seoul", "high", 0) == IDENT


def test_lookup_does_not_serve_canary_tier(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "Shanghai": {"a": 0.8, "b": 1.04, "serve": False, "tier": "canary"},
    })
    assert lookup_affine("Shanghai", "high", 1) == IDENT


def test_lookup_failsoft_on_malformed(tmp_path, monkeypatch):
    (tmp_path / "emos_center_calibration.json").write_text("{ bad json", encoding="utf-8")
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)
    assert lookup_affine("Seoul", "high", 1) == IDENT     # never raises


def test_kill_switch_disables_whole_layer(tmp_path, monkeypatch):
    art = {"enabled": False, "served_lead": 1,
           "metrics": {"high": {"served_lead": 1, "cities": {"Seoul": {"a": 0.5, "b": 1.06, "serve": True}}}}}
    (tmp_path / "emos_center_calibration.json").write_text(json.dumps(art), encoding="utf-8")
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)
    assert lookup_affine("Seoul", "high", 1) == IDENT     # enabled=false -> identity even for a served city


def test_lookup_reads_both_metrics(tmp_path, monkeypatch):
    art = {"served_lead": 1, "metrics": {
        "high": {"served_lead": 1, "cities": {"Seoul": {"a": 0.5, "b": 1.06, "serve": True}}},
        "low": {"served_lead": 1, "cities": {"Seoul": {"a": -0.2, "b": 0.98, "serve": True}}}}}
    (tmp_path / "emos_center_calibration.json").write_text(json.dumps(art), encoding="utf-8")
    monkeypatch.setattr("src.config.runtime_state_path", lambda name: tmp_path / name)
    assert lookup_affine("Seoul", "high", 1)[:2] == (pytest.approx(0.5), pytest.approx(1.06))
    assert lookup_affine("Seoul", "low", 1)[:2] == (pytest.approx(-0.2), pytest.approx(0.98))


def test_lookup_round_trips_served_coeffs(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"Taipei": {"a": -1.31, "b": 1.085, "serve": True, "x_lo": 25.7, "x_hi": 35.0}})
    a, b, xlo, xhi = lookup_affine("Taipei", "high", 1)
    assert (a, b, xlo, xhi) == (pytest.approx(-1.31), pytest.approx(1.085), 25.7, 35.0)


# ---- fitter ground truth --------------------------------------------------------------------
def test_fitter_ground_truth_uses_observations_for_both_metrics():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observations (
            city TEXT, target_date TEXT, high_temp REAL, low_temp REAL, unit TEXT, source TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO observations (city, target_date, high_temp, low_temp, unit, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("Paris", "2026-06-20", 77.0, 59.0, "F", "other_source"),
            ("Paris", "2026-06-20", 25.0, 15.0, "C", "wu_icao_history"),
            ("Seoul", "2026-06-20", 29.0, 21.0, "C", "wu_icao_history"),
        ],
    )

    high = fit_script._observed_ground_truth(conn, "high")
    low = fit_script._observed_ground_truth(conn, "low")

    assert high[("Paris", "2026-06-20")] == pytest.approx(25.0)
    assert low[("Paris", "2026-06-20")] == pytest.approx(15.0)
    assert high[("Seoul", "2026-06-20")] == pytest.approx(29.0)
    assert low[("Seoul", "2026-06-20")] == pytest.approx(21.0)


def test_fitter_ground_truth_does_not_require_settlement_outcomes_table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observations (
            city TEXT, target_date TEXT, high_temp REAL, low_temp REAL, unit TEXT, source TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO observations (city, target_date, high_temp, low_temp, unit, source)
        VALUES ('Tokyo', '2026-06-20', 86.0, 70.0, 'F', 'wu_icao_history')
        """
    )

    assert fit_script._observed_ground_truth(conn, "high")[("Tokyo", "2026-06-20")] == pytest.approx(30.0)
    assert fit_script._observed_ground_truth(conn, "low")[("Tokyo", "2026-06-20")] == pytest.approx(
        21.1111111111
    )


def test_fitter_ground_truth_prefers_verified_venue_settlement_when_present():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observations (
            city TEXT, target_date TEXT, high_temp REAL, low_temp REAL, unit TEXT, source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, settlement_value REAL, settlement_unit TEXT,
            temperature_metric TEXT, authority TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO observations (city, target_date, high_temp, low_temp, unit, source)
        VALUES ('Paris', '2026-06-20', 25.0, 15.0, 'C', 'wu_icao_history')
        """
    )
    conn.execute(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, settlement_value, settlement_unit, temperature_metric, authority
        ) VALUES ('Paris', '2026-06-20', 24.0, 'C', 'high', 'VERIFIED')
        """
    )

    high = fit_script._observed_ground_truth(conn, "high")
    low = fit_script._observed_ground_truth(conn, "low")

    assert high[("Paris", "2026-06-20")] == pytest.approx(24.0)
    assert low[("Paris", "2026-06-20")] == pytest.approx(15.0)


# ---- date-blocked validation (one ΔMSE per held-out date -> bootstrap over dates) -----------
def test_date_block_lcb_needs_three_dates():
    assert fit_script._date_block_lcb([1.0, 1.0]) == float("-inf")


def test_date_block_lcb_positive_when_every_date_improves():
    assert fit_script._date_block_lcb([0.5, 0.6, 0.4, 0.55, 0.5, 0.6]) > 0.0


def test_date_block_lcb_negative_when_dates_mostly_harm():
    assert fit_script._date_block_lcb([-1.0, -0.8, 0.1, -0.5, -0.9, -0.7]) < 0.0


# ---- policy-stability tier gate (production requires nested-fold reselection stability) ------
# A city may pass the per-city no-harm gate on the FULL data yet be policy-unstable: reselected in
# only a minority of nested global-date folds. Such a city is demoted to canary (serve=False,
# accruing) rather than served with real capital — the shipped production set must be robust to
# dropping any single date, not just positive on the full sample. Threshold is a documented
# supermajority (2/3), data-derived: the observed freq gap separates the stable core from the tail.
@pytest.mark.parametrize(
    "layer_ok,city_ok,stable,exp_serve,exp_tier",
    [
        (True, True, True, True, "production"),   # passes all three -> served
        (True, True, False, False, "canary"),     # no-harm OK but policy-unstable -> canary
        (False, True, True, False, "canary"),     # layer disabled -> canary (accruing)
        (True, False, True, False, None),         # fails per-city no-harm -> not served at all
        (False, False, False, False, None),
    ],
)
def test_serve_tier(layer_ok, city_ok, stable, exp_serve, exp_tier):
    assert fit_script._serve_tier(layer_ok, city_ok, stable) == (exp_serve, exp_tier)


def test_stability_threshold_is_documented_supermajority():
    # The production bar is a supermajority of nested folds (2/3), NOT a hand-tuned cutoff picked to
    # slice a specific city list. Locked so an audit sees the bar explicitly.
    assert fit_script.STABILITY_MIN_FREQ == pytest.approx(2.0 / 3.0)
