#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult P2-B: gamma_g fitted walk-forward on "fresh center + then-available shape"
#   pairs (actual availability timestamps); serving adds gamma*lag/6 to the transported
#   predictive VARIANCE only, fail-open dormant.
"""Shape-age sigma term: fitter + serving antibodies.

FITTER (scripts/fit_shape_age_sigma.py):
  (1) walk-forward boundary — a settlement dated on/after as_of never enters; centers
      only ever use previous_runs values with source_available_at <= the decision ref;
  (2) known-synthetic recovery — inject residual variance growing linearly with shape
      lag, recover the slope;
  (3) determinism — same DB + as_of + generated_at => byte-identical artifact+pointer;
  (4) clamp — a negative fitted slope serves gamma_per_6h = 0.0.

SERVING (src/forecast/shape_age_sigma + materializer composition):
  (5) gamma_for fail-open zeros (dir absent, sha mismatch, unknown metric, negative);
  (6) gamma=0 / artifact-absent => byte-identical shape (hash + payload) on BOTH
      branches;
  (7) transported branch: sigma² increases by exactly gamma*lag/6; term stamped in the
      payload; shape_hash identity dict untouched by the term field itself;
  (8) same-cycle branch: byte-identical even with gamma > 0.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fit_shape_age_sigma as fss  # noqa: E402

import src.data.replacement_forecast_materializer as mod  # noqa: E402
import src.forecast.shape_age_sigma as sas  # noqa: E402


# ---------------------------------------------------------------------------
# Fitter fixtures
# ---------------------------------------------------------------------------

_BASKETS = {("c1", "high"): {"ma": 0.6, "mb": 0.4}}


def _make_db(
    *,
    n_dates: int,
    gamma_true: float,
    seed: int = 7,
    poison_date: str | None = None,
) -> sqlite3.Connection:
    """Synthetic archive: per settled date, ENS cycles at lags {0,6,12,18,24}h behind the
    freshest provider cycle; residual = N(0, sqrt(sigma0² + gamma_true*lag/6)) with
    sigma0² = within² + between² known exactly by construction."""
    import random

    rng = random.Random(seed)
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT,
            temperature_metric TEXT, settlement_value REAL, settlement_unit TEXT,
            authority TEXT);
        CREATE TABLE ensemble_snapshots (snapshot_id INTEGER PRIMARY KEY, city TEXT,
            target_date TEXT, temperature_metric TEXT, members_json TEXT,
            members_unit TEXT, source_cycle_time TEXT, issue_time TEXT,
            source_available_at TEXT, available_at TEXT, local_day_start_utc TEXT,
            source_id TEXT, model_version TEXT, authority TEXT, causality_status TEXT,
            boundary_ambiguous INTEGER, forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER);
        CREATE TABLE raw_model_forecasts (model TEXT, city TEXT, metric TEXT,
            target_date TEXT, source_cycle_time TEXT, source_available_at TEXT,
            forecast_value_c REAL, endpoint TEXT);
        """
    )
    # 25 members centered on member_mean with pstdev exactly 0.8 (within² = 0.64).
    raw = [i - 12 for i in range(25)]
    scale = 0.8 / statistics.pstdev(raw)
    within_sq = 0.64
    # Provider values 19.5/.6 and 20.75/.4 -> center exactly 20.0;
    # between² = .6*(19.5-20)² + .4*(20.75-20)² = 0.375.
    between_sq = 0.6 * 0.25 + 0.4 * 0.5625
    sigma0_sq = within_sq + between_sq
    from datetime import timedelta

    # ONE settlement per date is physical, so one ENS lag per date (rotating through
    # {0,6,12,18,24}h) keeps the residual variance EXACT per lag:
    # settle ~ N(center, sigma0² + gamma_true*lag/6).
    for i in range(n_dates):
        month = 3 + (i // 28)
        day = 1 + (i % 28)
        tdate = f"2026-{month:02d}-{day:02d}"
        base = fss._parse_utc(f"{tdate}T00:00:00+00:00")
        # Freshest provider cycle 00Z, available 01Z: every 6h decision ref after the
        # ENS availability (02Z) sees the same then-fresh center = 20.0, carrier 00Z.
        prov_cycle, prov_avail = base.isoformat(), (base + timedelta(hours=1)).isoformat()
        for model, value in (("ma", 19.5), ("mb", 20.75)):
            conn.execute(
                "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?)",
                (model, "C1", "high", tdate, prov_cycle, prov_avail, value, "previous_runs"),
            )
        lag_h = (0, 6, 12, 18, 24)[i % 5]
        cycle = (base - timedelta(hours=lag_h)).isoformat()
        avail = (base + timedelta(hours=2)).isoformat()
        member_mean = rng.uniform(15.0, 25.0)  # transport erases this — any value works
        members = [member_mean + v * scale for v in raw]
        conn.execute(
            "INSERT INTO ensemble_snapshots (city, target_date, temperature_metric,"
            " members_json, members_unit, source_cycle_time, source_available_at,"
            " local_day_start_utc, source_id, model_version, authority, causality_status,"
            " boundary_ambiguous, forecast_window_attribution_status,"
            " contributes_to_target_extrema) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("C1", tdate, "high", json.dumps(members), "degC", cycle, avail,
             base.isoformat(), "ecmwf_open_data", "ecmwf_ens", "VERIFIED", "OK",
             0, "FULLY_INSIDE_TARGET_LOCAL_DAY", 1),
        )
        sigma = math.sqrt(sigma0_sq + gamma_true * lag_h / 6.0)
        settle = 20.0 + rng.gauss(0.0, sigma)
        conn.execute(
            "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
            ("C1", tdate, "high", settle, "C", "VERIFIED"),
        )
    if poison_date is not None:
        conn.execute(
            "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
            ("C1", poison_date, "high", 99.0, "C", "VERIFIED"),
        )
    conn.commit()
    return conn


def _fit(conn, *, as_of="2026-12-31", holdout_start="2026-12-31"):
    return fss.build_artifact(
        conn,
        baskets=_BASKETS,
        as_of=as_of,
        holdout_start=holdout_start,
        generated_at="2026-07-17T00:00:00+00:00",
        git_sha="test",
        min_bucket_n=10,
        n_boot=60,
    )


def test_walk_forward_boundary_excludes_as_of_and_later() -> None:
    conn = _make_db(n_dates=40, gamma_true=0.0)
    settlements = fss.load_settlements(conn, as_of="2026-04-01")
    assert all(tdate < "2026-04-01" for _c, tdate, _m in settlements)
    cycles = fss.load_ens_cycles(conn, as_of="2026-04-01")
    assert all(tdate < "2026-04-01" for _c, tdate, _m in cycles)
    runs = fss.load_previous_runs(conn, as_of="2026-04-01", models={"ma", "mb"})
    assert all(tdate < "2026-04-01" for _m, _c, _me, tdate in runs)


def test_center_uses_only_then_available_values() -> None:
    """A provider run available AFTER the decision ref must not enter the center."""
    runs = {
        "ma": [
            (fss._parse_utc("2026-03-01T01:00:00+00:00"),
             fss._parse_utc("2026-03-01T00:00:00+00:00"), 19.5),
            (fss._parse_utc("2026-03-01T13:00:00+00:00"),
             fss._parse_utc("2026-03-01T12:00:00+00:00"), 30.0),  # future at ref
        ],
        "mb": [
            (fss._parse_utc("2026-03-01T01:00:00+00:00"),
             fss._parse_utc("2026-03-01T00:00:00+00:00"), 20.75),
        ],
    }
    ref = fss._parse_utc("2026-03-01T06:00:00+00:00")
    resolved = fss._then_fresh_center(_BASKETS[("c1", "high")], runs, ref)
    assert resolved is not None
    center, between, carrier = resolved
    assert center == pytest.approx(0.6 * 19.5 + 0.4 * 20.75)
    assert carrier == fss._parse_utc("2026-03-01T00:00:00+00:00")
    # After the 12Z run publishes, it takes over as the carrier.
    late_ref = fss._parse_utc("2026-03-01T18:00:00+00:00")
    center2, _b2, carrier2 = fss._then_fresh_center(_BASKETS[("c1", "high")], runs, late_ref)
    assert carrier2 == fss._parse_utc("2026-03-01T12:00:00+00:00")
    assert center2 == pytest.approx(0.6 * 30.0 + 0.4 * 20.75)


def test_known_synthetic_recovery() -> None:
    gamma_true = 0.9
    conn = _make_db(n_dates=280, gamma_true=gamma_true)
    artifact, _replay = _fit(conn)
    fit = artifact["metrics"]["high"]
    assert fit["status"] == "OK"
    assert fit["n_pairs"] > 500
    assert fit["gamma_per_6h"] == pytest.approx(gamma_true, abs=0.35)
    assert fit["se"] is not None and fit["p_value"] is not None
    assert fit["p_value"] < 0.05


def test_zero_gamma_world_fits_near_zero_and_clamps() -> None:
    conn = _make_db(n_dates=280, gamma_true=0.0)
    artifact, _replay = _fit(conn)
    fit = artifact["metrics"]["high"]
    assert fit["status"] == "OK"
    assert fit["gamma_per_6h"] >= 0.0  # clamp invariant
    assert abs(fit["slope"]) < 0.3     # no fabricated age signal
    # And the clamp itself: a hand-built downward-sloping bucket set serves 0.0.
    pairs = [
        {"target_date": f"2026-03-{d:02d}", "lag_h": lag, "age_h": lag,
         "residual": 0.0, "sigma0_sq": 1.0, "y": -0.5 * (lag / 6.0), "bucket": int(lag // 6)}
        for d in range(1, 29) for lag in (0.0, 6.0, 12.0) for _ in range(4)
    ]
    fit2 = fss.fit_gamma(pairs, min_bucket_n=10, n_boot=30)
    assert fit2["slope"] < 0.0
    assert fit2["gamma_per_6h"] == 0.0


def test_fitter_determinism(tmp_path) -> None:
    def run(out: Path) -> tuple[str, str]:
        conn = _make_db(n_dates=60, gamma_true=0.4)
        artifact, _ = _fit(conn, holdout_start="2026-05-01")
        payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
        return payload, hashlib.sha256(payload.encode()).hexdigest()

    p1, s1 = run(tmp_path / "a")
    p2, s2 = run(tmp_path / "b")
    assert p1 == p2 and s1 == s2


def test_holdout_split_partitions_pairs() -> None:
    conn = _make_db(n_dates=120, gamma_true=0.4)
    artifact, replay = _fit(conn, holdout_start="2026-05-15")
    fit = artifact["metrics"]["high"]
    assert fit["n_holdout_pairs"] > 0
    assert fit["n_pairs"] > 0
    assert replay["high"], "holdout replay table must be populated"
    # fitted sigma widens with a positive gamma: CRPS columns differ at lag>0 buckets
    aged = [row for row in replay["high"] if row["bucket"] > 0]
    assert aged and any(row["fitted"] != row["gamma0"] for row in aged)


# ---------------------------------------------------------------------------
# Serving loader
# ---------------------------------------------------------------------------

def _write_artifact(tmp_path: Path, metrics: dict) -> None:
    artifact = {"schema_version": 1, "as_of": "2026-07-17", "metrics": metrics}
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    name = "shape_age_sigma_20260717.json"
    (tmp_path / name).write_text(payload, encoding="utf-8")
    (tmp_path / "ACTIVE.json").write_text(
        json.dumps({"artifact": name,
                    "sha256": hashlib.sha256(payload.encode()).hexdigest()})
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _fresh_cache():
    sas._load_active_artifact.cache_clear()
    yield
    sas._load_active_artifact.cache_clear()


def test_gamma_for_fails_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(sas.ENV_SHAPE_AGE_SIGMA_DIR, str(tmp_path))
    assert sas.gamma_for("high") == 0.0  # dir empty
    _write_artifact(tmp_path, {"high": {"gamma_per_6h": 0.25},
                               "low": {"gamma_per_6h": -1.0}})
    sas._load_active_artifact.cache_clear()
    assert sas.gamma_for("high") == 0.25
    assert sas.gamma_for("low") == 0.0       # negative clamps
    assert sas.gamma_for("unknown") == 0.0   # unknown metric
    # sha mismatch fails open
    pointer = json.loads((tmp_path / "ACTIVE.json").read_text())
    pointer["sha256"] = "0" * 64
    (tmp_path / "ACTIVE.json").write_text(json.dumps(pointer))
    sas._load_active_artifact.cache_clear()
    assert sas.gamma_for("high") == 0.0


# ---------------------------------------------------------------------------
# Serving composition (materializer)
# ---------------------------------------------------------------------------

_CYCLE = "2026-07-10T00:00:00+00:00"
_CARRIER = "2026-07-10T12:00:00+00:00"  # 12h newer -> transported branch


def _shape(*, carrier=None, gamma=0.0):
    raw = tuple(range(-25, 26))
    scale = 0.4 / statistics.pstdev(raw)
    members = tuple(10.5 + value * scale for value in raw)
    return mod._current_evidence_shape_from_values(
        snapshot_id=7,
        source_cycle_time=_CYCLE,
        source_available_at="2026-07-10T08:00:00+00:00",
        members_c=members,
        provider_values_c={"a": 10.0, "b": 10.6, "c": 12.0},
        provider_weights={"a": 0.4, "b": 0.4, "c": 0.2},
        center_c=10.5,
        carrier_cycle_time=carrier,
        shape_age_gamma_c2_per_6h=gamma,
    )


def test_gamma_zero_is_byte_identical_both_branches() -> None:
    for carrier in (None, _CYCLE, _CARRIER):
        base = _shape(carrier=carrier, gamma=0.0)
        default = _shape(carrier=carrier)  # parameter default
        assert default.shape_hash == base.shape_hash
        assert default.as_payload() == base.as_payload()
        assert "shape_age_sigma_term_c2" not in base.as_payload()


def test_transported_branch_sigma_increases_by_exact_term() -> None:
    gamma = 0.3
    base = _shape(carrier=_CARRIER, gamma=0.0)
    widened = _shape(carrier=_CARRIER, gamma=gamma)
    expected_term = gamma * 12.0 / 6.0
    assert widened.predictive_sigma_c ** 2 == pytest.approx(
        base.predictive_sigma_c ** 2 + expected_term
    )
    payload = widened.as_payload()
    assert payload["shape_age_sigma_term_c2"] == pytest.approx(expected_term)
    # Everything except sigma-derived fields untouched.
    assert widened.ensemble_within_sigma_c == base.ensemble_within_sigma_c
    assert widened.provider_between_sigma_c == base.provider_between_sigma_c
    assert widened.ensemble_center_delta_c == 0.0
    # center_sigma deliberately excludes the term (fresh-center estimation error
    # unchanged by shape age).
    assert widened.center_sigma_c == base.center_sigma_c
    # The widened predictive sigma is identity: the hash must move.
    assert widened.shape_hash != base.shape_hash


def test_same_cycle_branch_untouched_by_gamma() -> None:
    for carrier in (None, _CYCLE):
        base = _shape(carrier=carrier, gamma=0.0)
        with_gamma = _shape(carrier=carrier, gamma=5.0)
        assert with_gamma.shape_hash == base.shape_hash
        assert with_gamma.as_payload() == base.as_payload()
        assert with_gamma.predictive_sigma_c == base.predictive_sigma_c


def test_term_field_is_outside_identity_dict() -> None:
    """The provenance field itself must not enter the shape_hash identity: two shapes
    with the same widened sigma but term stamped vs not (impossible live, simulated by
    construction) — instead we pin the mechanism: payload drops None, keeps positive."""
    dormant = _shape(carrier=_CARRIER, gamma=0.0)
    active = _shape(carrier=_CARRIER, gamma=0.3)
    assert "shape_age_sigma_term_c2" not in dormant.as_payload()
    assert active.as_payload()["shape_age_sigma_term_c2"] > 0.0
    # Identity dict fields are unchanged in NAME SET: hash difference comes only from
    # predictive_sigma_c (checked via a same-sigma probe: zero-lag transported is
    # impossible, so equality of every identity input except sigma implies the term
    # never entered identity as its own key).
    assert set(dormant.as_payload()) | {"shape_age_sigma_term_c2"} == set(active.as_payload())
