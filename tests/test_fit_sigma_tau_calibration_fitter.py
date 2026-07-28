# Created: 2026-07-28
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md.
"""Smoke tests for scripts/fit_sigma_tau_calibration.py on a SYNTHETIC sqlite fixture.

NEVER touches the live DB (state/zeus-forecasts.db) -- every fixture here is a tmp_path sqlite
file the test builds and tears down itself.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

import scripts.fit_sigma_tau_calibration as fitter


def _mk_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL
        );
        CREATE TABLE settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            settlement_value REAL,
            unit TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _insert_post(conn, *, city, target_date, metric, computed_at, mu, sig) -> None:
    prov = json.dumps({"bayes_precision_fusion": {"anchor_value_c": mu, "predictive_sigma_c": sig}})
    conn.execute(
        "INSERT INTO forecast_posteriors (city, target_date, temperature_metric, computed_at, provenance_json)"
        " VALUES (?,?,?,?,?)",
        (city, target_date, metric, computed_at, prov),
    )


def _insert_sett(conn, *, city, target_date, metric, value, unit) -> None:
    conn.execute(
        "INSERT INTO settlements (city, target_date, temperature_metric, settlement_value, unit) VALUES (?,?,?,?,?)",
        (city, target_date, metric, value, unit),
    )


def _add_series(
    conn, *, city: str, metric: str, unit: str, n: int, lead_h: float, sig: float, d: float,
    date_start: "__import__('datetime').date",
) -> None:
    """Insert n (settlement, posterior) pairs, ONE settlement row per distinct target_date (matching
    the real settlements.UNIQUE(city, target_date, temperature_metric) constraint) and one posterior
    row each at a computed_at chosen so lead_target_h == lead_h exactly. The settlement value is
    fixed; the posterior's own mu is varied (mu = settled - z) so z = settled_c - mu alternates
    +d/-d exactly (mean 0 by construction) -- a closed-form-checkable spread."""
    import datetime as _dt

    settled = 25.0
    for i in range(n):
        target_date = (date_start + _dt.timedelta(days=i)).isoformat()
        target_end = _dt.datetime.fromisoformat(target_date).replace(tzinfo=_dt.timezone.utc) + _dt.timedelta(days=1)
        computed_at = (target_end - _dt.timedelta(hours=lead_h)).isoformat()
        z = d if i % 2 == 0 else -d
        mu = settled - z
        _insert_sett(conn, city=city, target_date=target_date, metric=metric, value=settled, unit=unit)
        _insert_post(conn, city=city, target_date=target_date, metric=metric, computed_at=computed_at, mu=mu, sig=sig)


def _build_fixture(path: Path) -> None:
    """Build a synthetic DB with a KNOWN-by-construction spread-skill ratio for two buckets.

    C/high, tau bucket [12,24) (lead=18h): 80 Shanghai rows (sig=2.0, d=2.6) + 40 Beijing rows
    (sig=2.0, d=5.2, roughly 2x Shanghai's dispersion so its per-city c_raw is clearly >1).
    C/high, tau bucket [24,36) (lead=30h): 70 Shanghai rows (sig=1.0, d=1.1) -- a DIFFERENT
    magnitude, so a bucket-mixing bug would be caught by comparing the two fitted k's.
    F/low: only 10 rows total -- below MIN_GROUP_N=60 -- must be REFUSED.
    """
    import datetime as _dt

    conn = sqlite3.connect(str(path))
    _add_series(conn, city="Shanghai", metric="high", unit="C", n=80, lead_h=18.0, sig=2.0, d=2.6, date_start=_dt.date(2026, 1, 1))
    _add_series(conn, city="Beijing", metric="high", unit="C", n=40, lead_h=18.0, sig=2.0, d=5.2, date_start=_dt.date(2026, 4, 1))
    _add_series(conn, city="Shanghai", metric="high", unit="C", n=70, lead_h=30.0, sig=1.0, d=1.1, date_start=_dt.date(2026, 6, 1))
    _add_series(conn, city="Miami", metric="low", unit="F", n=10, lead_h=18.0, sig=2.0, d=2.0, date_start=_dt.date(2026, 9, 1))
    conn.commit()
    conn.close()


@pytest.fixture()
def fixture_db(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_forecasts.db"
    _mk_db(path)
    _build_fixture(path)
    return path


def test_prep_dedup_and_lead_target_h(fixture_db: Path) -> None:
    d, stats = fitter.prep(str(fixture_db), since="2020-01-01")
    assert stats["n_final"] == 80 + 40 + 70 + 10
    assert stats["n_dropped_negative_lead"] == 0
    sub = d[(d["unit_family"] == "C") & (d["taut"] == "[12,24)")]
    assert set(sub["city"].unique()) == {"Shanghai", "Beijing"}


def test_fit_group_c_high_bucket_12_24_matches_closed_form(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)
    assert group["fitted"] is True

    bucket = group["buckets"]["[12,24)"]
    assert bucket["fitted"] is True
    z_1224 = sub[sub["taut"] == "[12,24)"]["z"].values
    expected_k = float(np.std(z_1224, ddof=1) / np.sqrt(np.mean(sub[sub["taut"] == "[12,24)"]["sig"].values ** 2)))
    assert bucket["k"] == pytest.approx(expected_k, abs=1e-6)  # artifact rounds k to 6 decimals

    bucket_2436 = group["buckets"]["[24,36)"]
    assert bucket_2436["fitted"] is True
    assert bucket_2436["k"] != bucket["k"], "buckets must not be mixed together"

    for label in ("[0,6)", "[6,12)", "[36,48)", "[48,72)", "[72,inf)"):
        assert group["buckets"][label]["fitted"] is False
        assert group["buckets"][label]["k"] == pytest.approx(group["global_k"])


def test_fit_group_refuses_below_min_group_n(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "F") & (d["temperature_metric"] == "low")]
    group = fitter.fit_group(sub)
    assert group["fitted"] is False
    assert group["global_k"] == 1.0
    assert group["buckets"] == {}
    assert group["cities"] == {}


def test_city_shrinkage_pulls_toward_one_and_orders_correctly(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)
    cities = group["cities"]
    assert "Beijing" in cities  # n=40 >= MIN_CITY_N=30
    assert "Shanghai" in cities
    assert cities["Shanghai"]["n"] == 150  # pooled across BOTH fitted buckets ([12,24) 80 + [24,36) 70)
    beijing = cities.get("Beijing") or {}
    assert beijing["n"] == 40
    # Beijing's residual (d=5.2 vs Shanghai's scaling d=2.6 at the same sig/k) is roughly 2x
    # Shanghai's typical scaled residual, so c_raw should be well above 1; shrinkage (n0=100) must
    # pull c_shrunk strictly between 1.0 and c_raw.
    assert beijing["c_raw"] > 1.0
    assert 1.0 < beijing["c_shrunk"] < beijing["c_raw"]


def test_main_writes_artifact_only_to_given_out_path(fixture_db: Path, tmp_path: Path, capsys) -> None:
    out_path = tmp_path / "sigma_tau_calibration.json"
    import sys

    argv = sys.argv
    sys.argv = ["fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01", "--out", str(out_path)]
    try:
        rc = fitter.main()
    finally:
        sys.argv = argv
    assert rc == 0
    assert out_path.exists()
    artifact = json.loads(out_path.read_text())
    assert artifact["families"]["C"]["high"]["fitted"] is True
    assert artifact["families"]["F"]["low"]["fitted"] is False
    assert "_meta" in artifact and "source_query_hash" in artifact["_meta"]
    # never wrote anywhere else
    assert list(tmp_path.iterdir()) == [out_path] or out_path in list(tmp_path.iterdir())


def test_validate_mode_does_not_require_out(fixture_db: Path, capsys) -> None:
    import sys

    argv = sys.argv
    sys.argv = [
        "fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01",
        "--validate", "2026-07-02",
    ]
    try:
        rc = fitter.main()
    finally:
        sys.argv = argv
    assert rc == 0
    out = capsys.readouterr().out
    assert "validate cutoff=2026-07-02" in out


def test_main_requires_out_or_validate(fixture_db: Path) -> None:
    import sys

    argv = sys.argv
    sys.argv = ["fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01"]
    try:
        with pytest.raises(SystemExit):
            fitter.main()
    finally:
        sys.argv = argv
