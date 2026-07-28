# Created: 2026-07-28
# Last reused or audited: 2026-07-28 (design-review corrections)
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md.
"""Smoke tests for scripts/fit_sigma_tau_calibration.py on a SYNTHETIC sqlite fixture.

NEVER touches the live DB (state/zeus-forecasts.db) -- every fixture here is a tmp_path sqlite
file the test builds and tears down itself.

Covers the 2026-07-28 design-review corrections:
  1. tau is bucketed on source_cycle_time (issue clock), NOT computed_at (decision clock) --
     many recomputes of the SAME issue must land in the SAME bucket and be event-weighted down.
  2. event weighting: MIN_BUCKET_N/MIN_GROUP_N/MIN_CITY_N are counted in UNIQUE EVENTS
     (nunique (city,target_date)), not raw rows; a single event's rows never inflate the count.
  3. the PRIMARY k is the interval-censored MLE (scripts.fit_sigma_tau_calibration.
     fit_interval_censored_scale); the closed-form event-weighted spread-skill ratio is reported
     alongside as k_normal_crosscheck, not served.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

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
            source_cycle_time TEXT NOT NULL,
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


def _insert_post(conn, *, city, target_date, metric, computed_at, source_cycle_time, mu, sig) -> None:
    prov = json.dumps({"bayes_precision_fusion": {"anchor_value_c": mu, "predictive_sigma_c": sig}})
    conn.execute(
        "INSERT INTO forecast_posteriors"
        " (city, target_date, temperature_metric, computed_at, source_cycle_time, provenance_json)"
        " VALUES (?,?,?,?,?,?)",
        (city, target_date, metric, computed_at, source_cycle_time, prov),
    )


def _insert_sett(conn, *, city, target_date, metric, value, unit) -> None:
    conn.execute(
        "INSERT INTO settlements (city, target_date, temperature_metric, settlement_value, unit) VALUES (?,?,?,?,?)",
        (city, target_date, metric, value, unit),
    )


def _target_end(target_date: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(target_date).replace(tzinfo=_dt.timezone.utc) + _dt.timedelta(days=1)


def _add_series(
    conn, *, city: str, metric: str, unit: str, n_events: int, lead_h: float, sig: float, d: float,
    date_start: _dt.date, recomputes_per_event: int = 1,
) -> None:
    """Insert n_events distinct settlement DAYS for (city, metric), one settlement row per day
    (matching the real UNIQUE(city, target_date, temperature_metric) constraint), each day's ONE
    posterior issued at a source_cycle_time chosen so lead_issue_h == lead_h exactly. The
    settlement value is fixed; the posterior's own mu is varied (mu = settled - z) so
    z = settled_c - mu alternates +d/-d exactly (mean 0 by construction, so the interval-censored
    primary and the closed-form crosscheck should closely agree -- this fixture has no center bias
    to inflate one estimator relative to the other, unlike live data).

    ``recomputes_per_event`` > 1 additionally inserts (recomputes_per_event - 1) EXTRA rows for the
    FIRST event only, at the SAME source_cycle_time but LATER computed_at timestamps (simulating
    the live "247 computed_at values, 4 source_cycle_time values" pathology) -- these rows must
    land in the SAME tau bucket (bucketed on source_cycle_time, unaffected by computed_at) and be
    event-weighted down so they do not inflate that event's influence on the fit.
    """
    settled = 25.0
    for i in range(n_events):
        target_date = (date_start + _dt.timedelta(days=i)).isoformat()
        target_end = _target_end(target_date)
        source_cycle_time = (target_end - _dt.timedelta(hours=lead_h)).isoformat()
        z = d if i % 2 == 0 else -d
        mu = settled - z
        _insert_sett(conn, city=city, target_date=target_date, metric=metric, value=settled, unit=unit)
        _insert_post(
            conn, city=city, target_date=target_date, metric=metric,
            computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=mu, sig=sig,
        )
        if i == 0:
            for r in range(1, recomputes_per_event):
                # Spaced a full HOUR apart (not minutes) so every recompute survives the fitter's
                # hourly dedup as a DISTINCT row -- matching live reality, where dedup already
                # collapses same-hour duplicates but many distinct-hour recomputes of the SAME
                # source_cycle_time still survive (exactly the residual problem event weighting
                # fixes).
                later_computed_at = (
                    _dt.datetime.fromisoformat(source_cycle_time) + _dt.timedelta(hours=r)
                ).isoformat()
                _insert_post(
                    conn, city=city, target_date=target_date, metric=metric,
                    computed_at=later_computed_at, source_cycle_time=source_cycle_time, mu=mu, sig=sig,
                )


def _build_fixture(path: Path) -> None:
    """Build a synthetic DB with a KNOWN-by-construction spread for two buckets.

    C/high, tau bucket [12,24) (lead=18h): 80 Shanghai event-days (sig=2.0, d=2.6) + 40 Beijing
    event-days (sig=2.0, d=5.2, ~2x Shanghai's dispersion). Shanghai's FIRST event-day additionally
    carries 200 EXTRA same-cycle recomputes (a "247 computed_at / 1 source_cycle_time" stand-in) --
    without event weighting this single day would swamp the whole bucket's fit.
    C/high, tau bucket [24,36) (lead=30h): 70 Shanghai event-days (sig=1.0, d=1.1) -- a DIFFERENT
    magnitude, so a bucket-mixing bug would be caught by comparing the two fitted k's.
    F/low: only 10 event-days total -- below MIN_GROUP_N=60 -- must be REFUSED.
    """
    conn = sqlite3.connect(str(path))
    _add_series(
        conn, city="Shanghai", metric="high", unit="C", n_events=80, lead_h=18.0, sig=2.0, d=2.6,
        date_start=_dt.date(2026, 1, 1), recomputes_per_event=201,
    )
    _add_series(conn, city="Beijing", metric="high", unit="C", n_events=40, lead_h=18.0, sig=2.0, d=5.2, date_start=_dt.date(2026, 4, 1))
    _add_series(conn, city="Shanghai", metric="high", unit="C", n_events=70, lead_h=30.0, sig=1.0, d=1.1, date_start=_dt.date(2026, 6, 1))
    _add_series(conn, city="Miami", metric="low", unit="F", n_events=10, lead_h=18.0, sig=2.0, d=2.0, date_start=_dt.date(2026, 9, 1))
    conn.commit()
    conn.close()


@pytest.fixture()
def fixture_db(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_forecasts.db"
    _mk_db(path)
    _build_fixture(path)
    return path


def test_prep_dedup_and_lead_issue_h(fixture_db: Path) -> None:
    d, stats = fitter.prep(str(fixture_db), since="2020-01-01")
    # 80 Shanghai (+200 extra recomputes on day 1) + 40 Beijing + 70 Shanghai + 10 Miami rows.
    assert stats["n_final"] == (80 + 200) + 40 + 70 + 10
    assert stats["n_dropped_negative_lead"] == 0
    sub = d[(d["unit_family"] == "C") & (d["taut"] == "[12,24)")]
    assert set(sub["city"].unique()) == {"Shanghai", "Beijing"}


def test_recomputes_of_one_event_share_one_bucket_and_are_weighted_down(fixture_db: Path) -> None:
    """The tau-clock correction's core claim: many computed_at values at the SAME source_cycle_time
    land in the SAME bucket (unlike a computed_at-anchored tau, which would scatter them), and
    event weighting collapses their combined influence to weight 1, not 201."""
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    first_day = sub[(sub["city"] == "Shanghai") & (sub["target_date"] == "2026-01-01")]
    assert len(first_day) == 201, "all 201 recomputes of the same issue must survive hourly dedup distinctly"
    assert first_day["taut"].nunique() == 1, "same source_cycle_time -> one bucket regardless of computed_at spread"
    assert set(first_day["taut"].unique()) == {"[12,24)"}

    weighted = fitter._add_event_weights(sub)
    row_weights = weighted[(weighted["city"] == "Shanghai") & (weighted["target_date"] == "2026-01-01")]["event_weight"]
    assert row_weights.nunique() == 1
    assert row_weights.iloc[0] == pytest.approx(1.0 / 201)
    assert row_weights.sum() == pytest.approx(1.0), "one event's total weight across the group is exactly 1"


def test_event_counts_differ_from_row_counts(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)
    bucket = group["buckets"]["[12,24)"]
    # 80 Shanghai + 200 extra recomputes + 40 Beijing rows = 320, but only 80+40=120 unique events.
    assert bucket["n"] == 320
    assert bucket["n_events"] == 120


def test_fit_group_c_high_two_buckets_differ_and_agree_with_crosscheck(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)
    assert group["fitted"] is True

    bucket_1224 = group["buckets"]["[12,24)"]
    bucket_2436 = group["buckets"]["[24,36)"]
    assert bucket_1224["fitted"] is True
    assert bucket_2436["fitted"] is True
    assert bucket_1224["k"] != bucket_2436["k"], "buckets must not be mixed together"

    # This fixture has NO center bias (z alternates +d/-d exactly) -- the interval-censored PRIMARY
    # k and the closed-form Normal-density crosscheck should closely agree (unlike live data, where
    # a real center bias makes the RAW-mu censored likelihood inflate k well above the crosscheck).
    assert bucket_1224["k"] == pytest.approx(bucket_1224["k_normal_crosscheck"], rel=0.15)
    assert bucket_2436["k"] == pytest.approx(bucket_2436["k_normal_crosscheck"], rel=0.15)

    for label in ("[0,6)", "[6,12)", "[36,48)", "[48,72)", "[72,inf)"):
        assert group["buckets"][label]["fitted"] is False
        assert group["buckets"][label]["k"] == pytest.approx(group["global_k"])
        assert group["buckets"][label]["n_events"] == 0


def test_fit_group_refuses_below_min_group_n_events(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "F") & (d["temperature_metric"] == "low")]
    group = fitter.fit_group(sub)
    assert group["fitted"] is False
    assert group["global_k"] == 1.0
    assert group["n_events"] == 10
    assert group["buckets"] == {}
    assert group["cities"] == {}
    assert "INSUFFICIENT_EVENTS" in group["refusal_reason"]


def test_city_shrinkage_pulls_toward_one_and_orders_correctly(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)
    cities = group["cities"]
    assert "Beijing" in cities  # n_events=40 >= MIN_CITY_N=30
    assert "Shanghai" in cities
    assert cities["Shanghai"]["n_events"] == 150  # pooled across BOTH fitted buckets (80 + 70 event-days)
    assert cities["Shanghai"]["n"] == 150 + 200  # 200 extra SAME-EVENT recomputes inflate rows, not events
    beijing = cities.get("Beijing") or {}
    assert beijing["n_events"] == 40
    # Beijing's residual (d=5.2 vs Shanghai's scaling d=2.6 at the same sig/k) is roughly 2x
    # Shanghai's typical scaled residual, so c_raw should be well above 1; shrinkage (n0=100) must
    # pull c_shrunk strictly between 1.0 and c_raw.
    assert beijing["c_raw"] > 1.0
    assert 1.0 < beijing["c_shrunk"] < beijing["c_raw"]


def test_main_writes_artifact_only_to_given_out_path(fixture_db: Path, tmp_path: Path) -> None:
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
    assert "k_normal_crosscheck" in artifact["families"]["C"]["high"]["buckets"]["[12,24)"]
    assert artifact["_meta"]["tau_clock"].startswith("source_cycle_time")
    assert artifact["_meta"]["min_bucket_n_events"] == fitter.MIN_BUCKET_N
    assert artifact["_meta"]["components_fence_applied"] is False
    assert "components_fence_reference_ts" in artifact["_meta"]
    assert "source_query_hash" in artifact["_meta"]
    assert list(tmp_path.iterdir()) == [out_path] or out_path in list(tmp_path.iterdir())


def test_validate_mode_does_not_require_out_and_reports_both_clocks(fixture_db: Path, capsys) -> None:
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
    assert "clock=PRIMARY(issue-clock)" in out
    assert "clock=COMPARISON-ONLY(decision-clock)" in out


def test_main_requires_out_or_validate(fixture_db: Path) -> None:
    import sys

    argv = sys.argv
    sys.argv = ["fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01"]
    try:
        with pytest.raises(SystemExit):
            fitter.main()
    finally:
        sys.argv = argv
