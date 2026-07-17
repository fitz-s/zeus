# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (f) — effective-n from measured member dependence; walk-forward
#   no-leak discipline per combo_experiments_report.md.
"""Unit tests for scripts/fit_ens_member_dependence.py — no live DB required.

Covers: (1) identical-within-event members varying across events => rho -> 1 =>
n_eff -> 1; (2) near-independent member indicators => rho ~ 0; (3) strict
walk-forward boundary (target_date == as_of never trains); (4) determinism
(same DB state + as_of + generated_at => byte-identical artifact JSON).
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
                (r["city"], r["target_date"], r["metric"], 20.0, "C", "VERIFIED"),
            )
    conn.commit()
    return conn


def _dates(n: int) -> list[str]:
    return [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


def test_identical_members_give_rho_one_and_n_eff_one() -> None:
    """Members identical WITHIN each event, alternating 20C/21C across events:
    the member indicators are perfectly dependent => rho=1 => n_eff=1."""
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": "high",
            "members": [20.0 if i % 2 == 0 else 21.0] * 51,
        }
        for i, d in enumerate(_dates(60))
    ]
    conn = _make_db(rows)
    metrics = fed.estimate_rho_by_metric(
        fed.load_settled_member_events(conn, as_of="2026-12-31")
    )
    cell = metrics["high"]
    assert cell["rho"] == 1.0
    assert abs(cell["n_eff"] - 1.0) < 1e-9
    assert cell["n_members"] == 51
    assert cell["n_targets"] == 60


def test_near_independent_members_give_rho_near_zero() -> None:
    """Members iid across the event's own draw => indicator ICC ~ 0."""
    rng = np.random.default_rng(11)
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": "high",
            "members": (20.0 + rng.integers(0, 2, size=51)).tolist(),
        }
        for d in _dates(200)
    ]
    conn = _make_db(rows)
    metrics = fed.estimate_rho_by_metric(
        fed.load_settled_member_events(conn, as_of="2026-12-31")
    )
    assert metrics["high"]["rho"] < 0.05


def test_walk_forward_boundary_excludes_as_of_and_later() -> None:
    """target_date == as_of (and later) must never enter the fit."""
    dates = _dates(40)
    as_of = dates[30]
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": "high",
            "members": [20.0 if i % 2 == 0 else 21.0] * 51,
        }
        for i, d in enumerate(dates)
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
    rng = np.random.default_rng(5)
    rows = [
        {
            "city": "C1",
            "target_date": d,
            "metric": m,
            "members": (20.0 + rng.integers(0, 3, size=51)).tolist(),
        }
        for d in _dates(50)
        for m in ("high", "low")
    ]
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
    metrics = json.loads(payloads[0])["metrics"]
    assert set(metrics) == {"high", "low"}
    for cell in metrics.values():
        assert 0.0 <= cell["rho"] <= 1.0
        assert 1.0 <= cell["n_eff"] <= cell["n_members"]
