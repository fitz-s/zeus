# Created: 2026-07-28
# Last reused/audited: 2026-07-28 (FIX 1/2/3/4/5/8 deep-review corrections)
# Lifecycle: created=2026-07-28; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Smoke-test scripts/fit_sigma_tau_calibration.py's fitting pipeline (query fence,
#   local-date bucketing, settlement quantizer, log-domain MLE, OOS gate) against a synthetic
#   fixture -- never the live DB.
# Reuse: Re-run whenever the fitter's query shape, tau-clock definition, or gate thresholds change;
#   the fixtures here encode the CURRENT expected constants (MIN_*_N, K_BOUNDS, OOS_MARGIN_NATS).
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md.
"""Smoke tests for scripts/fit_sigma_tau_calibration.py on a SYNTHETIC sqlite fixture.

NEVER touches the live DB (state/zeus-forecasts.db) -- every fixture here is a tmp_path sqlite
file the test builds and tears down itself. Because the fitter now reads the REAL
config/cities.json for city timezone/rounding-rule metadata (FIX 1/FIX 2), every fixture uses REAL
city names (Shanghai, Beijing, Chicago, Hong Kong) rather than fictional ones.

Covers the 2026-07-28 corrections:
  FIX 1 (local-date endpoint): tau's target-end is the city's LOCAL midnight, not UTC.
  FIX 2 (settlement quantizer): Hong Kong's oracle_truncate preimage [v,v+1) vs the symmetric
    wmo_half_up [v-0.5,v+0.5) everyone else uses.
  FIX 3 (numerical stability + fail-closed fit): an extreme residual must not crash or silently
    clip; a bound-pinned optimum must raise a refusal, not ship silently.
  FIX 4 (ship train coefficients, date-blocked holdout, global-k comparison rung).
  FIX 5 (population fence): current_evidence_shape required, computed_at before local target end,
    settlement_outcomes.authority='VERIFIED'.
  FIX 8 (URI + read-only hardening): mode=ro enforcement, PRAGMA query_only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
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
            posterior_config_hash TEXT,
            provenance_json TEXT NOT NULL
        );
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            settlement_value REAL,
            settlement_unit TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        );
        """
    )
    conn.commit()
    conn.close()


def _insert_post(
    conn, *, city, target_date, metric, computed_at, source_cycle_time, mu, sig, current_evidence_shape=True,
    day0_observed_extreme_c=None, day0_center_delta_c=None, config_hash=None,
) -> None:
    bpf: dict = {"anchor_value_c": mu, "predictive_sigma_c": sig}
    if current_evidence_shape:
        bpf["current_evidence_shape"] = {"snapshot_id": 1}  # FIX 5: presence is the fence signal
    prov: dict = {"bayes_precision_fusion": bpf}
    if day0_observed_extreme_c is not None:
        # B1: mirrors the exact provenance shape _compute_posterior_payload stamps.
        prov["day0_conditioning"] = {"active": True, "metric": metric, "observed_extreme_c": day0_observed_extreme_c}
        if day0_center_delta_c is not None:
            prov["day0_remaining_center_delta_c"] = day0_center_delta_c
    prov_json = json.dumps(prov)
    # MEDIUM: mirrors the real posterior_config_hash column -- a hash of the numeric identity that
    # actually produced this row (anchor/sigma here), unless the caller supplies an explicit hash to
    # model a genuinely distinct config (e.g. a distinct current-evidence snapshot) at the same
    # (mu, sig) by coincidence.
    if config_hash is None:
        config_hash = hashlib.sha256(f"{mu}:{sig}".encode()).hexdigest()
    conn.execute(
        "INSERT INTO forecast_posteriors"
        " (city, target_date, temperature_metric, computed_at, source_cycle_time, posterior_config_hash, provenance_json)"
        " VALUES (?,?,?,?,?,?,?)",
        (city, target_date, metric, computed_at, source_cycle_time, config_hash, prov_json),
    )


def _insert_sett(conn, *, city, target_date, metric, value, unit, authority="VERIFIED") -> None:
    conn.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_value, settlement_unit, authority)"
        " VALUES (?,?,?,?,?,?)",
        (city, target_date, metric, value, unit, authority),
    )


def _add_series(
    conn, *, city: str, tz: str, metric: str, unit: str, n_events: int, lead_h: float, sig: float, d: float,
    date_start: _dt.date, recomputes_per_event: int = 1, day_offset: int = 0, day_stride: int = 1,
    settled: float = 25.0,
) -> None:
    """Insert n_events distinct settlement DAYS for (city, metric), one settlement_outcomes row
    per day (matching the real UNIQUE(city, target_date, temperature_metric) constraint), each
    day's ONE posterior issued at a source_cycle_time chosen so lead_issue_h == lead_h exactly,
    measured from the CITY'S LOCAL target-date end (FIX 1 -- via fitter._local_target_end_utc, the
    exact function under test, so the fixture and the code agree on what "lead_h" means). The
    settlement value is fixed; the posterior's own mu is varied (mu = settled - z) so
    z = settled_c - mu alternates +d/-d exactly (mean 0 by construction).

    ``day_offset``/``day_stride`` place this series' events at
    ``date_start + (day_offset + i*day_stride)`` days, so MULTIPLE series can be INTERLEAVED
    across one shared calendar window without colliding on the same (city, target_date).

    ``recomputes_per_event`` > 1 additionally inserts EXTRA rows for the FIRST event only, at the
    SAME source_cycle_time but LATER computed_at timestamps (simulating the live "247 computed_at
    values, 4 source_cycle_time values" pathology), spaced hourly and kept WITHIN the FIX-5
    population fence (computed_at < local target end, i.e. strictly less than ``lead_h`` hours
    after source_cycle_time).
    """
    for i in range(n_events):
        target_date = (date_start + _dt.timedelta(days=day_offset + i * day_stride)).isoformat()
        target_end = fitter._local_target_end_utc(target_date, tz)
        source_cycle_time = (target_end - _dt.timedelta(hours=lead_h)).isoformat()
        z = d if i % 2 == 0 else -d
        mu = settled - z
        _insert_sett(conn, city=city, target_date=target_date, metric=metric, value=settled, unit=unit)
        _insert_post(
            conn, city=city, target_date=target_date, metric=metric,
            computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=mu, sig=sig,
            config_hash=f"{city}:{target_date}:{metric}:recompute0",
        )
        if i == 0:
            max_extra_hours = max(0, int(lead_h) - 1)  # stay strictly inside the FIX-5 fence
            for r in range(1, min(recomputes_per_event, max_extra_hours + 1)):
                later_computed_at = (
                    _dt.datetime.fromisoformat(source_cycle_time) + _dt.timedelta(hours=r)
                ).isoformat()
                # Each recompute gets its OWN config_hash (MEDIUM: dedup is now keyed on
                # posterior_config_hash, not an hour floor) -- these rows model the live "same
                # source_cycle_time, many computed_at" pathology, i.e. genuinely distinct config
                # instances (e.g. a fresh current-evidence snapshot each recompute), not idempotent
                # retries of one config, so they must survive dedup distinctly for event-weighting to
                # have anything to weight down.
                _insert_post(
                    conn, city=city, target_date=target_date, metric=metric,
                    computed_at=later_computed_at, source_cycle_time=source_cycle_time, mu=mu, sig=sig,
                    config_hash=f"{city}:{target_date}:{metric}:recompute{r}",
                )


def _build_fixture(path: Path) -> None:
    """Build a synthetic DB with a KNOWN-by-construction spread for two buckets, using REAL cities
    (Shanghai, Beijing -- both 'C', both Asia/Shanghai timezone) so the fitter's real
    config/cities.json lookups resolve.

    C/high, tau bucket [12,24) (lead=18h): 80 Shanghai event-days (sig=2.0, d=2.6) + 40 Beijing
    event-days (sig=2.0, d=5.2, ~2x Shanghai's dispersion). Shanghai's FIRST event-day additionally
    carries several EXTRA same-cycle recomputes (kept inside the FIX-5 fence) -- without event
    weighting this single day would swamp the whole bucket's fit.
    C/high, tau bucket [24,36) (lead=30h): 70 Shanghai event-days (sig=1.0, d=1.1) -- a DIFFERENT
    magnitude, so a bucket-mixing bug would be caught by comparing the two fitted k's.
    F/low: only 10 Chicago event-days total -- below MIN_GROUP_N=60 -- must be REFUSED.

    The two Shanghai series share ONE calendar window (day_stride=2, offsets 0/1) so they land on
    even/odd days and never collide on the same target_date for the same city; this INTERLEAVES
    both tau buckets across the whole window, so the OOS gate's date-blocked holdout samples a
    representative mix of both buckets.
    """
    conn = sqlite3.connect(str(path))
    _add_series(
        conn, city="Shanghai", tz="Asia/Shanghai", metric="high", unit="C", n_events=80, lead_h=18.0, sig=2.0, d=2.6,
        date_start=_dt.date(2026, 1, 1), recomputes_per_event=17, day_offset=0, day_stride=2,
    )
    _add_series(conn, city="Beijing", tz="Asia/Shanghai", metric="high", unit="C", n_events=40, lead_h=18.0, sig=2.0, d=5.2, date_start=_dt.date(2026, 1, 1), day_offset=0, day_stride=1)
    _add_series(conn, city="Shanghai", tz="Asia/Shanghai", metric="high", unit="C", n_events=70, lead_h=30.0, sig=1.0, d=1.1, date_start=_dt.date(2026, 1, 1), day_offset=1, day_stride=2)
    _add_series(conn, city="Chicago", tz="America/Chicago", metric="low", unit="F", n_events=10, lead_h=18.0, sig=2.0, d=2.0, date_start=_dt.date(2026, 9, 1))
    conn.commit()
    conn.close()


@pytest.fixture()
def fixture_db(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_forecasts.db"
    _mk_db(path)
    _build_fixture(path)
    return path


def _build_single_bucket_fixture(path: Path, *, true_k: float, n_events: int, seed: int, sig: float = 1.0, lead_h: float = 18.0, city: str = "Shanghai", tz: str = "Asia/Shanghai") -> None:
    """A single-city, single-bucket fixture with GENUINE random noise
    (z ~ N(0, (true_k*sig)^2), fixed seed) -- has real sampling variability for an internal
    train/holdout OOS gate to meaningfully accept or reject."""
    import numpy as np

    _mk_db(path)
    conn = sqlite3.connect(str(path))
    rng = np.random.default_rng(seed)
    zs = rng.normal(0.0, true_k * sig, size=n_events)
    settled = 25.0
    for i in range(n_events):
        target_date = (_dt.date(2026, 1, 1) + _dt.timedelta(days=i)).isoformat()
        target_end = fitter._local_target_end_utc(target_date, tz)
        source_cycle_time = (target_end - _dt.timedelta(hours=lead_h)).isoformat()
        mu = settled - float(zs[i])
        _insert_sett(conn, city=city, target_date=target_date, metric="high", value=settled, unit="C")
        _insert_post(
            conn, city=city, target_date=target_date, metric="high",
            computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=mu, sig=sig,
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def test_prep_dedup_and_lead_issue_h(fixture_db: Path) -> None:
    d, stats = fitter.prep(str(fixture_db), since="2020-01-01")
    assert stats["n_dropped_unknown_city"] == 0
    assert stats["unknown_cities"] == []
    assert stats["n_dropped_negative_lead"] == 0
    sub = d[(d["unit_family"] == "C") & (d["taut"] == "[12,24)")]
    assert set(sub["city"].unique()) == {"Shanghai", "Beijing"}


def test_recomputes_of_one_event_share_one_bucket_and_are_weighted_down(fixture_db: Path) -> None:
    """FIX 1/event-weighting core claim: many computed_at values at the SAME source_cycle_time
    land in the SAME bucket, and event weighting collapses their combined influence to weight 1."""
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    first_day = sub[(sub["city"] == "Shanghai") & (sub["target_date"] == "2026-01-01")]
    assert len(first_day) > 1, "at least one extra recompute must survive hourly dedup distinctly"
    assert first_day["taut"].nunique() == 1, "same source_cycle_time -> one bucket regardless of computed_at spread"
    assert set(first_day["taut"].unique()) == {"[12,24)"}

    weighted = fitter._add_event_weights(sub)
    row_weights = weighted[(weighted["city"] == "Shanghai") & (weighted["target_date"] == "2026-01-01")]["event_weight"]
    assert row_weights.nunique() == 1
    assert row_weights.sum() == pytest.approx(1.0), "one event's total weight across the group is exactly 1"


def test_event_counts_differ_from_row_counts(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)
    bucket = group["buckets"]["[12,24)"]
    assert bucket["n"] > bucket["n_events"]
    assert bucket["n_events"] == 120  # 80 Shanghai + 40 Beijing event-days


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
    # No center bias in this fixture (z alternates +d/-d exactly) -- the interval-censored PRIMARY
    # k and the closed-form Normal-density crosscheck should closely agree.
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
    beijing = cities.get("Beijing") or {}
    assert beijing["n_events"] == 40
    assert beijing["c_raw"] > 1.0
    assert 1.0 < beijing["c_shrunk"] < beijing["c_raw"]


# ---------------------------------------------------------------------------
# FIX 1: local-date endpoint (city LOCAL midnight, not UTC)
# ---------------------------------------------------------------------------

def test_local_date_endpoint_uses_city_timezone_not_utc(tmp_path: Path) -> None:
    """A Shanghai (+8h fixed offset) posterior issued such that a UTC-anchored tau and a
    Shanghai-LOCAL-anchored tau land in DIFFERENT buckets must resolve to the LOCAL bucket.

    target_date=2026-03-10; Shanghai LOCAL end = 2026-03-11T00:00+08:00 = 2026-03-10T16:00Z.
    The OLD (wrong) UTC-anchored end would be 2026-03-11T00:00Z -- 8h later.
    source_cycle_time=2026-03-09T22:00Z -> LOCAL lead = 18.0h ([12,24)); the OLD UTC-anchored lead
    would have been 26.0h ([24,36)) -- a different bucket, proving the fix.
    """
    path = tmp_path / "tz_fixture.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    target_date = "2026-03-10"
    source_cycle_time = "2026-03-09T22:00:00+00:00"
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=25.0, unit="C")
    _insert_post(conn, city="Shanghai", target_date=target_date, metric="high", computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=25.0, sig=1.0)
    conn.commit()
    conn.close()

    d, _stats = fitter.prep(str(path), since="2020-01-01")
    assert len(d) == 1
    row = d.iloc[0]
    assert row["lead_issue_h"] == pytest.approx(18.0)
    assert row["taut"] == "[12,24)", "expected the SHANGHAI-LOCAL bucket, not the UTC-anchored one"


def test_local_date_endpoint_unknown_city_is_dropped(tmp_path: Path) -> None:
    """A city absent from config/cities.json must be DROPPED (fail-closed), never defaulted to
    UTC/wmo_half_up."""
    path = tmp_path / "unknown_city.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    _insert_sett(conn, city="Nowhereville", target_date="2026-03-10", metric="high", value=25.0, unit="C")
    _insert_post(conn, city="Nowhereville", target_date="2026-03-10", metric="high", computed_at="2026-03-09T22:00:00+00:00", source_cycle_time="2026-03-09T22:00:00+00:00", mu=25.0, sig=1.0)
    conn.commit()
    conn.close()

    d, stats = fitter.prep(str(path), since="2020-01-01")
    assert len(d) == 0
    assert stats["n_dropped_unknown_city"] == 1
    assert "Nowhereville" in stats["unknown_cities"]


# ---------------------------------------------------------------------------
# FIX 2: settlement quantizer (Hong Kong oracle_truncate vs symmetric wmo_half_up)
# ---------------------------------------------------------------------------

def test_hong_kong_uses_asymmetric_oracle_truncate_preimage(tmp_path: Path) -> None:
    """Hong Kong's settled integer v has preimage [v, v+1), NOT the symmetric [v-0.5, v+0.5)
    every other city uses -- the fitter must derive this from
    src.contracts.settlement_semantics, never a universal offset."""
    path = tmp_path / "hk_fixture.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    target_date = "2026-03-10"
    target_end = fitter._local_target_end_utc(target_date, "Asia/Hong_Kong")
    source_cycle_time = (target_end - _dt.timedelta(hours=18.0)).isoformat()
    _insert_sett(conn, city="Hong Kong", target_date=target_date, metric="high", value=28.0, unit="C")
    _insert_post(conn, city="Hong Kong", target_date=target_date, metric="high", computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=27.5, sig=1.0)
    conn.commit()
    conn.close()

    d, _stats = fitter.prep(str(path), since="2020-01-01")
    assert len(d) == 1
    row = d.iloc[0]
    assert row["bin_lower_c"] == pytest.approx(28.0)   # [v, v+1) -- lower edge AT the settled value
    assert row["bin_upper_c"] == pytest.approx(29.0)
    assert row["l"] == pytest.approx(28.0 - 27.5)
    assert row["u"] == pytest.approx(29.0 - 27.5)


def test_non_hk_city_uses_symmetric_wmo_half_up_preimage(tmp_path: Path) -> None:
    path = tmp_path / "sh_fixture.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    target_date = "2026-03-10"
    target_end = fitter._local_target_end_utc(target_date, "Asia/Shanghai")
    source_cycle_time = (target_end - _dt.timedelta(hours=18.0)).isoformat()
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=28.0, unit="C")
    _insert_post(conn, city="Shanghai", target_date=target_date, metric="high", computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=27.5, sig=1.0)
    conn.commit()
    conn.close()

    d, _stats = fitter.prep(str(path), since="2020-01-01")
    row = d.iloc[0]
    assert row["bin_lower_c"] == pytest.approx(27.5)  # [v-0.5, v+0.5) -- symmetric
    assert row["bin_upper_c"] == pytest.approx(28.5)


# ---------------------------------------------------------------------------
# FIX 3: numerical stability + fail-closed fit
# ---------------------------------------------------------------------------

def test_extreme_residual_does_not_crash_or_silently_clip(tmp_path: Path) -> None:
    """One event ~20 sigma from center (would underflow the naive cdf-difference to exactly 0.0,
    hence log(0)=-inf under the OLD clip-based code) mixed into otherwise well-behaved data must
    not crash the fit and must not silently produce a garbage k -- the log-domain computation
    keeps the objective finite everywhere it is evaluated."""
    import numpy as np

    path = tmp_path / "extreme_fixture.db"
    _build_single_bucket_fixture(path, true_k=1.1, n_events=100, seed=3)
    # Inject one extreme outlier row directly by appending a further event with a huge residual.
    conn = sqlite3.connect(str(path))
    target_date = (_dt.date(2026, 1, 1) + _dt.timedelta(days=100)).isoformat()
    target_end = fitter._local_target_end_utc(target_date, "Asia/Shanghai")
    source_cycle_time = (target_end - _dt.timedelta(hours=18.0)).isoformat()
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=25.0, unit="C")
    _insert_post(conn, city="Shanghai", target_date=target_date, metric="high", computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=45.0, sig=1.0)  # z = -20
    conn.commit()
    conn.close()

    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    group = fitter.fit_group(sub)  # must not raise
    assert group["fitted"] is True
    assert np.isfinite(group["global_k"])
    assert fitter.K_BOUNDS[0] < group["global_k"] < fitter.K_BOUNDS[1]


def test_log_interval_prob_matches_naive_in_normal_range() -> None:
    import numpy as np
    from scipy.stats import norm

    lo = np.array([-0.5, 2.5])
    hi = np.array([0.5, 3.5])
    sigma = np.array([1.0, 1.0])
    naive = np.log(norm.cdf(hi / sigma) - norm.cdf(lo / sigma))
    stable = fitter._log_interval_prob(lo, hi, sigma)
    assert stable == pytest.approx(naive, abs=1e-9)


def test_log_interval_prob_stays_finite_where_naive_underflows() -> None:
    import numpy as np
    from scipy.stats import norm

    lo = np.array([40.0])
    hi = np.array([41.0])
    sigma = np.array([1.0])
    with np.errstate(divide="ignore"):
        naive = np.log(norm.cdf(hi / sigma) - norm.cdf(lo / sigma))
    assert not np.isfinite(naive[0]), "the naive computation must underflow to -inf here (sanity check on the test itself)"
    stable = fitter._log_interval_prob(lo, hi, sigma)
    assert np.isfinite(stable[0])
    assert stable[0] < -100  # a real, very small but finite log-probability


def test_bound_pinned_optimum_raises_fit_failure() -> None:
    """Data whose true optimum lies below K_BOUNDS[0] must PIN there and raise FitFailure, not
    silently ship the pinned value."""
    import numpy as np
    import pandas as pd

    n = 200
    lo = np.full(n, -0.001)
    hi = np.full(n, 0.001)  # an absurdly tight true bin relative to sigma_base
    sigma_base = np.full(n, 100.0)  # huge sigma_base forces scale toward the LOWER bound
    w = np.ones(n)
    # B1: fit_interval_censored_scale now takes the row frame (day0-awareness); day0_active=False
    # for every row means none of the day0-only columns are ever read.
    g = pd.DataFrame({"l": lo, "u": hi, "day0_active": [False] * n})
    with pytest.raises(fitter.FitFailure):
        fitter.fit_interval_censored_scale(g, sigma_base, w, fitter.K_BOUNDS)


# ---------------------------------------------------------------------------
# FIX 4: ship train coefficients unchanged; date-blocked holdout; global-k rung
# ---------------------------------------------------------------------------

def test_split_holdout_by_target_date_never_splits_one_date_across_train_and_holdout(fixture_db: Path) -> None:
    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train, holdout = fitter._split_holdout_by_target_date(sub)
    train_dates = set(train["target_date"].unique())
    holdout_dates = set(holdout["target_date"].unique())
    assert train_dates.isdisjoint(holdout_dates), "no target_date may appear in both train and holdout"
    # Beijing shares dates with Shanghai's [12,24) series (day_stride=1 vs 2) -- any Beijing event
    # on a holdout date must be ENTIRELY in holdout, not split from its Shanghai same-date sibling.
    for date in holdout_dates:
        assert len(sub[sub["target_date"] == date]) == len(holdout[holdout["target_date"] == date])


def test_gate_ships_train_coefficients_unchanged_no_refit(tmp_path: Path) -> None:
    """FIX 4: a group that PASSES the gate ships the TRAIN split's own coefficients -- refitting on
    the full population could activate a bucket/city that was never actually OOS-scored."""
    path = tmp_path / "gate_pass.db"
    _build_single_bucket_fixture(path, true_k=1.8, n_events=300, seed=7)
    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train_sub, holdout_sub = fitter._split_holdout_by_target_date(sub)
    train_only_group = fitter.fit_group(train_sub)
    gated = fitter.gate_group(train_sub, holdout_sub, gate_method="test")
    assert gated["fitted"] is True
    assert gated["global_k"] == train_only_group["global_k"], "shipped global_k must equal the TRAIN-only fit, not a full-data refit"
    assert gated["n_events"] == train_only_group["n_events"], "shipped n_events must be the TRAIN count, not train+holdout"
    assert gated["model_type"] in (fitter.MODEL_TYPE_GLOBAL_K_V1, fitter.MODEL_TYPE_BUCKET_CITY_K_V1)


def test_oos_gate_reports_global_k_only_comparison_rung(tmp_path: Path) -> None:
    path = tmp_path / "gate_pass2.db"
    _build_single_bucket_fixture(path, true_k=1.8, n_events=300, seed=7)
    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train_sub, holdout_sub = fitter._split_holdout_by_target_date(sub)
    gated = fitter.gate_group(train_sub, holdout_sub, gate_method="test")
    assert gated["fitted"] is True
    assert "global_k_only_censored_delta" in gated["oos_gate"]
    assert gated["oos_gate"]["global_k_only_censored_delta"] is not None


def test_oos_gate_accepts_a_genuine_correction(tmp_path: Path) -> None:
    path = tmp_path / "gate_pass3.db"
    _build_single_bucket_fixture(path, true_k=1.8, n_events=300, seed=7)
    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train_sub, holdout_sub = fitter._split_holdout_by_target_date(sub)
    group = fitter.gate_group(train_sub, holdout_sub, gate_method="internal_holdout_last_25pct_dates_by_target_date")
    assert group["fitted"] is True
    assert group["oos_gate"]["passed"] is True
    assert group["oos_gate"]["censored_delta"] > fitter.OOS_MARGIN_NATS
    assert group["global_k"] > 1.3
    # B2: a single-bucket fixture has NO genuine bucket/city variation to exploit -- the full model
    # cannot beat the flat one by more than the margin, so the simpler GLOBAL_K_V1 model must win.
    assert group["model_type"] == fitter.MODEL_TYPE_GLOBAL_K_V1
    assert group["cities"] == {}
    assert len({b["k"] for b in group["buckets"].values()}) == 1, "global_k_v1 must apply ONE k uniformly to every bucket"


def test_model_selection_ships_bucket_city_when_it_earns_its_complexity(tmp_path: Path) -> None:
    """B2: when two tau buckets have GENUINELY different true dispersion, the full bucket-indexed
    model must beat the flat global-k model by more than the margin and be selected."""
    import numpy as np

    path = tmp_path / "bucket_city_wins.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    rng = np.random.default_rng(19)
    settled = 25.0
    # Bucket A (lead=18h, [12,24)): true_k=1.0 (well-calibrated). Bucket B (lead=30h, [24,36)):
    # true_k=3.0 (badly under-dispersed) -- a flat global k averaging the two is a poor fit for
    # EITHER bucket individually, so the bucket-indexed model should earn its keep.
    for i in range(220):
        target_date = (_dt.date(2026, 1, 1) + _dt.timedelta(days=i)).isoformat()
        lead_h, true_k = (18.0, 1.0) if i % 2 == 0 else (30.0, 3.0)
        target_end = fitter._local_target_end_utc(target_date, "Asia/Shanghai")
        source_cycle_time = (target_end - _dt.timedelta(hours=lead_h)).isoformat()
        z = float(rng.normal(0.0, true_k * 1.0))
        mu = settled - z
        _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=settled, unit="C")
        _insert_post(conn, city="Shanghai", target_date=target_date, metric="high", computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=mu, sig=1.0)
    conn.commit()
    conn.close()

    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train_sub, holdout_sub = fitter._split_holdout_by_target_date(sub)
    group = fitter.gate_group(train_sub, holdout_sub, gate_method="test")
    assert group["fitted"] is True
    assert group["model_type"] == fitter.MODEL_TYPE_BUCKET_CITY_K_V1, (
        f"a genuine 3x dispersion difference between two tau buckets must beat the flat global-k "
        f"model by more than the margin: oos_gate={group['oos_gate']}"
    )
    assert group["oos_gate"]["full_beats_global_by"] > fitter.OOS_MARGIN_NATS
    bucket_ks = {b["k"] for b in group["buckets"].values() if b["fitted"]}
    assert len(bucket_ks) >= 2, "the two genuinely different buckets must be fitted to DIFFERENT k's"


def test_oos_gate_rejects_a_spurious_correction(tmp_path: Path) -> None:
    path = tmp_path / "gate_reject.db"
    _build_single_bucket_fixture(path, true_k=1.0, n_events=300, seed=7)
    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train_sub, holdout_sub = fitter._split_holdout_by_target_date(sub)
    group = fitter.gate_group(train_sub, holdout_sub, gate_method="internal_holdout_last_25pct_dates_by_target_date")
    assert group["fitted"] is False
    assert group["model_type"] == fitter.MODEL_TYPE_NEUTRAL
    assert group["global_k"] == 1.0
    assert group["oos_gate"]["passed"] is False
    assert "NEUTRAL" in group["refusal_reason"]


def test_oos_gate_requires_margin_not_merely_positive(tmp_path: Path) -> None:
    """A razor-thin positive delta below OOS_MARGIN_NATS must still be REJECTED."""
    path = tmp_path / "gate_margin.db"
    _build_single_bucket_fixture(path, true_k=1.02, n_events=300, seed=11)
    d, _stats = fitter.prep(str(path), since="2020-01-01")
    sub = d[(d["unit_family"] == "C") & (d["temperature_metric"] == "high")]
    train_sub, holdout_sub = fitter._split_holdout_by_target_date(sub)
    group = fitter.gate_group(train_sub, holdout_sub, gate_method="test")
    # true_k so close to 1.0 that any real delta, if positive at all, is very unlikely to clear a
    # 0.01-nat margin on 300 events -- exact outcome doesn't matter, only that a non-passing
    # verdict is never reported as passing without exceeding the margin.
    if group["fitted"]:
        assert group["oos_gate"]["censored_delta"] > fitter.OOS_MARGIN_NATS
    else:
        assert "OOS_GATE_FAILED" in group["refusal_reason"]


# ---------------------------------------------------------------------------
# FIX 5: training population fence
# ---------------------------------------------------------------------------

def test_missing_current_evidence_shape_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "no_ces.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    target_date = "2026-03-10"
    target_end = fitter._local_target_end_utc(target_date, "Asia/Shanghai")
    source_cycle_time = (target_end - _dt.timedelta(hours=18.0)).isoformat()
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=25.0, unit="C")
    _insert_post(
        conn, city="Shanghai", target_date=target_date, metric="high", computed_at=source_cycle_time,
        source_cycle_time=source_cycle_time, mu=25.0, sig=1.0, current_evidence_shape=False,
    )
    conn.commit()
    conn.close()

    d, stats = fitter.prep(str(path), since="2020-01-01")
    assert len(d) == 0
    assert stats["n_posteriors_read"] == 0, "the SQL query itself must require current_evidence_shape presence"


def test_unverified_settlement_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "unverified.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    target_date = "2026-03-10"
    target_end = fitter._local_target_end_utc(target_date, "Asia/Shanghai")
    source_cycle_time = (target_end - _dt.timedelta(hours=18.0)).isoformat()
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=25.0, unit="C", authority="UNVERIFIED")
    _insert_post(conn, city="Shanghai", target_date=target_date, metric="high", computed_at=source_cycle_time, source_cycle_time=source_cycle_time, mu=25.0, sig=1.0)
    conn.commit()
    conn.close()

    d, stats = fitter.prep(str(path), since="2020-01-01")
    assert len(d) == 0
    assert stats["n_settlements_read"] == 0, "the SQL query itself must require authority='VERIFIED'"


def test_computed_at_after_local_target_end_is_fenced_out(tmp_path: Path) -> None:
    path = tmp_path / "late_compute.db"
    _mk_db(path)
    conn = sqlite3.connect(str(path))
    target_date = "2026-03-10"
    target_end = fitter._local_target_end_utc(target_date, "Asia/Shanghai")
    source_cycle_time = (target_end - _dt.timedelta(hours=18.0)).isoformat()
    late_computed_at = (target_end + _dt.timedelta(hours=1)).isoformat()  # AFTER local settlement
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=25.0, unit="C")
    _insert_post(conn, city="Shanghai", target_date=target_date, metric="high", computed_at=late_computed_at, source_cycle_time=source_cycle_time, mu=25.0, sig=1.0)
    conn.commit()
    conn.close()

    d, stats = fitter.prep(str(path), since="2020-01-01")
    assert len(d) == 0
    assert stats["n_dropped_computed_at_after_local_target_end"] == 1


def test_one_begin_read_transaction_covers_both_selects(fixture_db: Path) -> None:
    """FIX 5: both SELECTs run inside one BEGIN read transaction -- smoke-check that load() does
    not raise and returns internally consistent (non-empty) frames."""
    post, sett = fitter.load(str(fixture_db), "2020-01-01")
    assert len(post) > 0
    assert len(sett) > 0


# ---------------------------------------------------------------------------
# FIX 8: URI + read-only hardening
# ---------------------------------------------------------------------------

def test_connect_ro_accepts_plain_path_and_already_wrapped_file_uri(fixture_db: Path) -> None:
    conn_plain = fitter._connect_ro(str(fixture_db))
    assert conn_plain.execute("SELECT 1").fetchone() == (1,)
    conn_plain.close()

    conn_uri = fitter._connect_ro(f"file:{fixture_db}?mode=ro")
    assert conn_uri.execute("SELECT 1").fetchone() == (1,)
    conn_uri.close()

    d_plain, _ = fitter.prep(str(fixture_db), since="2020-01-01")
    d_uri, _ = fitter.prep(f"file:{fixture_db}?mode=ro", since="2020-01-01")
    assert len(d_plain) == len(d_uri)


def test_connect_ro_sets_query_only_pragma(fixture_db: Path) -> None:
    conn = fitter._connect_ro(str(fixture_db))
    try:
        value = conn.execute("PRAGMA query_only").fetchone()[0]
        assert value == 1
    finally:
        conn.close()


def test_connect_ro_rejects_explicit_non_ro_mode(fixture_db: Path) -> None:
    with pytest.raises(ValueError):
        fitter._connect_ro(f"file:{fixture_db}?mode=rw")


def test_connect_ro_query_only_blocks_writes(fixture_db: Path) -> None:
    conn = fitter._connect_ro(str(fixture_db))
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO forecast_posteriors (city, target_date, temperature_metric, computed_at, source_cycle_time, provenance_json)"
                " VALUES ('x','2026-01-01','high','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','{}')"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# main() / artifact schema
# ---------------------------------------------------------------------------

def test_main_writes_artifact_with_gate_and_schema(fixture_db: Path, tmp_path: Path) -> None:
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
    meta = artifact["_meta"]
    assert meta["authority"] == fitter.ARTIFACT_AUTHORITY
    assert meta["schema_version"] == fitter.SCHEMA_VERSION
    assert meta["tau_clock"] == fitter.TAU_CLOCK_ID
    assert meta["k_bounds"] == list(fitter.K_BOUNDS)
    assert meta["population_fence"]["current_evidence_shape_required"] is True
    assert meta["population_fence"]["computed_at_before_local_target_end_required"] is True
    assert "VERIFIED" in meta["population_fence"]["settlement_authority_filter"]
    assert meta["oos_acceptance_gate"].startswith("internal_holdout_last_")
    assert artifact["families"]["F"]["low"]["fitted"] is False
    assert "oos_gate" in artifact["families"]["C"]["high"]
    assert list(tmp_path.iterdir()) == [out_path] or out_path in list(tmp_path.iterdir())


def test_validate_mode_does_not_require_out_and_reports_both_clocks(fixture_db: Path, capsys) -> None:
    import sys

    argv = sys.argv
    sys.argv = [
        "fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01",
        "--validate", "2026-04-01",
    ]
    try:
        rc = fitter.main()
    finally:
        sys.argv = argv
    assert rc == 0
    out = capsys.readouterr().out
    assert "clock=PRIMARY(issue-clock)" in out
    assert "clock=COMPARISON-ONLY(decision-clock)" in out
    assert "global_k_only" in out


def test_main_requires_out_or_validate(fixture_db: Path) -> None:
    import sys

    argv = sys.argv
    sys.argv = ["fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01"]
    try:
        with pytest.raises(SystemExit):
            fitter.main()
    finally:
        sys.argv = argv


def test_external_validate_cutoff_governs_the_shipped_gate(fixture_db: Path, tmp_path: Path) -> None:
    out_path = tmp_path / "sigma_tau_calibration.json"
    import sys

    argv = sys.argv
    sys.argv = [
        "fit_sigma_tau_calibration.py", "--fcst", str(fixture_db), "--since", "2020-01-01",
        "--validate", "2026-04-01", "--out", str(out_path),
    ]
    try:
        rc = fitter.main()
    finally:
        sys.argv = argv
    assert rc == 0
    artifact = json.loads(out_path.read_text())
    assert artifact["_meta"]["oos_acceptance_gate"] == "external_validate_cutoff:2026-04-01"
    assert artifact["families"]["C"]["high"]["oos_gate"]["method"] == "external_validate_cutoff:2026-04-01"


# ---------------------------------------------------------------------------
# B1 (deep-review 2026-07-28): fit/serve parity -- Day0 rows join the causal provenance and score
# through the SAME served_settlement_log_probability the live materializer serves through.
# ---------------------------------------------------------------------------

def test_build_frame_joins_day0_provenance_fields(fixture_db: Path) -> None:
    conn = sqlite3.connect(str(fixture_db))
    target_date = "2027-06-15"  # well outside _build_fixture's date ranges -- no collision
    _insert_sett(conn, city="Shanghai", target_date=target_date, metric="high", value=25.0, unit="C")
    _insert_post(
        conn, city="Shanghai", target_date=target_date, metric="high",
        computed_at="2026-02-28T20:00:00+00:00", source_cycle_time="2026-02-28T20:00:00+00:00",
        mu=24.0, sig=1.5, day0_observed_extreme_c=26.0, day0_center_delta_c=0.4,
    )
    conn.commit()
    conn.close()

    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    row = d[(d["city"] == "Shanghai") & (d["target_date"] == target_date)].iloc[0]
    assert bool(row["day0_active"]) is True
    assert row["day0_observed_extreme_c"] == pytest.approx(26.0)
    assert row["day0_center_delta_c"] == pytest.approx(0.4)


def test_build_frame_non_day0_row_has_inert_day0_columns(fixture_db: Path) -> None:
    """A row with no day0_conditioning in provenance must join to day0_active=False,
    day0_observed_extreme_c=NaN, day0_center_delta_c=0.0 -- never a stray default that could make a
    non-Day0 row silently score through the day0 branch."""
    import numpy as np

    d, _stats = fitter.prep(str(fixture_db), since="2020-01-01")
    assert len(d) > 0
    assert not d["day0_active"].any()
    assert d["day0_observed_extreme_c"].isna().all()
    assert (d["day0_center_delta_c"] == 0.0).all()
    assert np.array_equal(fitter._censored_log_prob(d, d["sig"].values), fitter._log_interval_prob(d["l"].values, d["u"].values, d["sig"].values))


def test_censored_log_prob_day0_row_matches_served_settlement_log_probability() -> None:
    """The fitter's scoring of a Day0-active row must be numerically IDENTICAL to the materializer's
    served kernel -- this IS the B1 parity claim, checked directly against the shared function."""
    import numpy as np
    import pandas as pd

    from src.data.replacement_forecast_materializer import served_settlement_log_probability

    row = pd.DataFrame([{
        "l": -1.5, "u": -0.5,
        "mu": 24.0, "temperature_metric": "high",
        "bin_lower_c": 24.5, "bin_upper_c": 25.5,
        "rounding_rule": "wmo_half_up",
        "day0_active": True, "day0_observed_extreme_c": 26.0, "day0_center_delta_c": 0.4,
    }])
    sigma = np.array([1.5 * 1.2])  # already-final sigma (sigma_base * trial k), as every caller passes it
    got = fitter._censored_log_prob(row, sigma)[0]
    expected = served_settlement_log_probability(
        anchor_value_c=24.0, predictive_sigma_c=1.5 * 1.2, k=1.0, metric="high",
        bin_low_c=24.5, bin_high_c=25.5, half_step=0.5, rounding_rule="wmo_half_up",
        day0_observed_extreme_c=26.0, day0_center_delta_c=0.4,
    )
    assert got == expected


def test_censored_log_prob_day0_row_differs_from_plain_normal() -> None:
    """A day0-active row must NOT be scored as if it were plain Normal -- the absorbing max/min
    transform changes the answer whenever the observed extreme actually constrains the bin."""
    import numpy as np
    import pandas as pd

    row = pd.DataFrame([{
        "l": -1.5, "u": -0.5,
        "mu": 24.0, "temperature_metric": "high",
        "bin_lower_c": 24.5, "bin_upper_c": 25.5,
        "rounding_rule": "wmo_half_up",
        "day0_active": True, "day0_observed_extreme_c": 26.0, "day0_center_delta_c": 0.0,
    }])
    sigma = np.array([1.5])
    day0_log_p = fitter._censored_log_prob(row, sigma)[0]
    plain_log_p = fitter._log_interval_prob(row["l"].values, row["u"].values, sigma)[0]
    # observed_extreme_c=26.0 is ABOVE this bin's upper bound (25.5) -> the day0 transform collapses
    # the bin's mass to exactly 0.0 (log -> the -1e300 floor), while plain Normal would not.
    assert day0_log_p < plain_log_p
    assert day0_log_p == pytest.approx(math.log(1e-300))
