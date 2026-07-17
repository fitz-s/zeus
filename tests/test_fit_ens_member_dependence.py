# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (f) — effective-n from measured member dependence; coverage
#   calibration per the cp_coverage measurement 2026-07-17 (ICC is provenance,
#   the operational rho is the smallest rho with nominal empirical coverage).
"""Unit tests for scripts/fit_ens_member_dependence.py — no live DB required.

Covers: (a) perfect-forecast synthetic (outcome drawn from the member
distribution => hit rate matches k/n) => calibrated rho ~ 0; (b) overconfident
synthetic (settlement frequently OUTSIDE all-member support => r(0) exceeds
UCB(0,n,0)) => calibrated rho > 0 with violated_k_at_zero containing 0;
(c) strict walk-forward boundary + determinism (byte-identical across two runs
INCLUDING the seeded bootstrap); (d) ICC provenance: perfectly dependent
members give rho_icc=1 while the coverage rho stays 0 — the exact distinction
that motivated the recalibration.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
)

import fit_ens_member_dependence as fed  # noqa: E402


def _make_db(rows: list[dict]) -> sqlite3.Connection:
    """rows: dicts with city, target_date, metric, members (list of float|None),
    settlement_value (degrees, unit 'C' unless settlement_value_unit overrides),
    plus optional members_unit / settlement_unit / rounding_policy / filter-column
    overrides (defaults satisfy the serving-side snapshot filter)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE ensemble_snapshots ("
        "snapshot_id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, "
        "temperature_metric TEXT, members_json TEXT, members_unit TEXT, "
        "settlement_unit TEXT, settlement_rounding_policy TEXT, "
        "source_id TEXT, model_version TEXT, authority TEXT, "
        "causality_status TEXT, boundary_ambiguous INTEGER, "
        "forecast_window_attribution_status TEXT, contributes_to_target_extrema INTEGER, "
        "source_cycle_time TEXT, issue_time TEXT, source_available_at TEXT, available_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT, "
        "temperature_metric TEXT, settlement_value REAL, settlement_unit TEXT, authority TEXT)"
    )
    seen_settlement: set[tuple[str, str, str]] = set()
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                i + 1,
                r["city"],
                r["target_date"],
                r["metric"],
                json.dumps(r["members"]),
                r.get("members_unit", "degC"),
                r.get("settlement_unit", "C"),
                r.get("rounding_policy", "wmo_half_up"),
                r.get("source_id", "ecmwf_open_data"),
                r.get("model_version", "ecmwf_ens"),
                r.get("authority", "VERIFIED"),
                r.get("causality_status", "OK"),
                r.get("boundary_ambiguous", 0),
                r.get("attribution", "FULLY_INSIDE_TARGET_LOCAL_DAY"),
                r.get("contributes", 1),
                f"{r['target_date']}T00:00:00+00:00",
                f"{r['target_date']}T00:00:00+00:00",
                f"{r['target_date']}T08:00:00+00:00",
                f"{r['target_date']}T08:00:00+00:00",
            ),
        )
        key = (r["city"], r["target_date"], r["metric"])
        if key not in seen_settlement and r.get("settled", True):
            seen_settlement.add(key)
            conn.execute(
                "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
                (
                    r["city"],
                    r["target_date"],
                    r["metric"],
                    r.get("settlement_value", 20.0),
                    r.get("settlement_value_unit", "C"),
                    "VERIFIED",
                ),
            )
    conn.commit()
    return conn


def _dates(n: int) -> list[str]:
    assert n <= 336, "12 months x 28 days"
    return [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


def _events(conn: sqlite3.Connection):
    return fed.load_settled_member_events(conn, as_of="2026-12-31")


def test_perfect_forecast_gives_rho_near_zero() -> None:
    """Outcome drawn from the SAME distribution as the members: the settled bin
    lands on a k-hit bin with frequency ~ k/n, which the rho=0 CP UCB covers
    with margin => calibrated rho ~ 0."""
    rng = np.random.default_rng(7)
    rows = []
    for i, d in enumerate(_dates(336)):
        center = 18.0 + (i % 5)
        members = (center + rng.normal(0.0, 2.0, size=51)).round(2).tolist()
        outcome = float(center + rng.normal(0.0, 2.0))
        rows.append(
            {
                "city": f"C{i % 8}",
                "target_date": d,
                "metric": "high",
                "members": members,
                "settlement_value": outcome,
            }
        )
    conn = _make_db(rows)
    metrics, pooled = fed.estimate_metrics(_events(conn), reps=200)
    cell = metrics["high"]
    # ~0: bounded by per-k bootstrap noise at 400 targets; an order of magnitude
    # below any meaningful widening (live measurement: high 0.0051, ICC 0.294).
    assert cell["rho"] < 0.02, cell
    assert cell["rho_calibrated"] < 0.02
    assert pooled["rho_calibrated"] < 0.02
    assert cell["n_members"] == 51
    assert cell["n_targets"] == 336


def test_overconfident_ensemble_gives_positive_rho() -> None:
    """All 51 members in bin 20 but settlement lands OUTSIDE all-member support
    (bin 21) on 50% of events: r(0) over the zero-hit grid cells exceeds
    UCB(0,51,0)=0.057 => violated at k=0 => calibrated rho > 0."""
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": "high",
            "members": [20.0] * 51,
            "settlement_value": 21.0 if i % 2 == 0 else 20.0,
        }
        for i, d in enumerate(_dates(200))
    ]
    conn = _make_db(rows)
    metrics, _pooled = fed.estimate_metrics(_events(conn), reps=200)
    cell = metrics["high"]
    # PAD_C=3 grid around bin 20 => 6 zero-hit bins per target; outcome lands in
    # one of them (bin 21) on 50% of targets => r(0) ~ 0.5/6 ~ 0.083 > 0.057.
    assert 0 in cell["violated_k_at_zero"], cell
    assert cell["rho"] > 0.0
    assert cell["rho_calibrated"] > 0.0
    assert cell["n_eff"] < cell["n_members"]


def test_icc_provenance_diverges_from_coverage_rho() -> None:
    """Perfectly dependent members (identical within each event, alternating
    across events) with a PERFECT forecast (settlement == the member value):
    rho_icc = 1 (total member correlation) while the coverage-calibrated
    operational rho stays 0 (the bound is never violated) — the exact
    ICC-vs-coverage distinction the 2026-07-17 measurement established."""
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": "high",
            "members": [20.0 if i % 2 == 0 else 21.0] * 51,
            "settlement_value": 20.0 if i % 2 == 0 else 21.0,
        }
        for i, d in enumerate(_dates(80))
    ]
    conn = _make_db(rows)
    metrics, _pooled = fed.estimate_metrics(_events(conn), reps=200)
    cell = metrics["high"]
    assert cell["rho_icc"] == 1.0
    assert cell["rho"] == 0.0
    assert cell["rho_calibrated"] == 0.0
    assert cell["violated_k_at_zero"] == []


def test_walk_forward_boundary_excludes_as_of_and_later() -> None:
    """target_date == as_of (and later) must never enter the fit."""
    dates = _dates(40)
    as_of = dates[30]
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": "high",
            "members": [20.0] * 51,
            "settlement_value": 20.0,
        }
        for d in dates
    ]
    conn = _make_db(rows)
    events = fed.load_settled_member_events(conn, as_of=as_of)
    assert len(events) == 30
    assert all(e["target_date"] < as_of for e in events)


def test_unsettled_and_filtered_snapshots_are_excluded() -> None:
    """No VERIFIED settlement, wrong source, or quarantine-reduced member count
    below MIN_MEMBERS => the event never enters the archive."""
    good = {
        "city": "C1",
        "target_date": "2026-01-01",
        "metric": "high",
        "members": [20.0] * 51,
        "settlement_value": 20.0,
    }
    rows = [
        good,
        {**good, "target_date": "2026-01-02", "settled": False},
        {**good, "target_date": "2026-01-03", "source_id": "other"},
        {**good, "target_date": "2026-01-04", "contributes": 0},
        # 51 slots but 40 boundary-quarantined nulls -> 11 < MIN_MEMBERS.
        {**good, "target_date": "2026-01-05", "members": [20.0] * 11 + [None] * 40},
    ]
    conn = _make_db(rows)
    events = fed.load_settled_member_events(conn, as_of="2026-12-31")
    assert [e["target_date"] for e in events] == ["2026-01-01"]


def test_determinism_byte_identical_artifact() -> None:
    """Same DB state + as_of + generated_at => byte-identical artifact JSON,
    INCLUDING the seeded target-clustered bootstrap (fixed BOOT_SEED)."""
    rng = np.random.default_rng(5)
    rows = []
    for i, d in enumerate(_dates(60)):
        for m in ("high", "low"):
            members = (20.0 + rng.integers(0, 3, size=51)).tolist()
            rows.append(
                {
                    "city": "C1",
                    "target_date": d,
                    "metric": m,
                    "members": members,
                    # Half the outcomes inside member support, half one bin out.
                    "settlement_value": members[0] if i % 2 == 0 else 24.0,
                }
            )
    payloads = []
    for _ in range(2):
        conn = _make_db(rows)
        artifact = fed.build_artifact(
            conn,
            as_of="2026-07-17",
            generated_at="2026-07-17T00:00:00+00:00",
            git_sha="testsha",
        )
        payloads.append(json.dumps(artifact, sort_keys=True))
    assert payloads[0] == payloads[1]
    doc = json.loads(payloads[0])
    metrics = doc["metrics"]
    assert set(metrics) == {"high", "low"}
    assert doc["_meta"]["boot_seed"] == fed.BOOT_SEED
    assert doc["_meta"]["boot_reps"] == fed.N_BOOT
    for cell in metrics.values():
        assert 0.0 <= cell["rho"] <= 1.0
        assert 1.0 <= cell["n_eff"] <= cell["n_members"]
        # Thin metrics (< MIN_TARGETS_FOR_METRIC_RHO targets) take the
        # conservative max(own, pooled) as their operational rho.
        assert cell["pooled_fallback_applied"] is True
        assert cell["rho"] == max(
            cell["rho_calibrated"], cell["rho_pooled_calibrated"]
        )
