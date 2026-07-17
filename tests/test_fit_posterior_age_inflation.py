# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §4a. The AMBER
#   sigma inflation MUST be settlement-fitted, walk-forward, deterministic (operator go
#   2026-07-17: 使用数学和统计就能证明一切).
"""Fitter for the posterior-age inflation artifact: walk-forward, monotone, deterministic.

Builds a tiny forecasts DB with, per settled target, a FRESH cycle (accurate center) and
an AGED cycle (biased center). The paired squared-error increment must land as a positive
v in the aged age band; the fit must be monotone non-decreasing in age, exclude targets at
or after ``as_of`` (walk-forward), and be byte-identical across runs.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import scripts.fit_posterior_age_inflation as fit

UTC = timezone.utc
PROD = fit.LIVE_FUSION_PRODUCT_ID
_BINS = [
    {"bin_id": f"b{i}", "center_c": float(20 + i)} for i in range(11)
]


def _q_json(center: float) -> str:
    """A q-vector whose predictive mean equals ``center`` (all mass on the nearest bin)."""
    idx = min(range(len(_BINS)), key=lambda i: abs(_BINS[i]["center_c"] - center))
    return json.dumps({b["bin_id"]: (1.0 if j == idx else 0.0) for j, b in enumerate(_BINS)})


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT, runtime_layer TEXT, training_allowed INTEGER,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            source_cycle_time TEXT, computed_at TEXT, q_json TEXT, provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT, authority TEXT
        )
        """
    )
    return conn


def _seed(conn, *, city, target_date, metric, cycle_dt, computed_dt, center):
    conn.execute(
        "INSERT INTO forecast_posteriors (product_id, runtime_layer, training_allowed, city, "
        "target_date, temperature_metric, source_cycle_time, computed_at, q_json, provenance_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            PROD, "live", 0, city, target_date, metric,
            cycle_dt.isoformat(), computed_dt.isoformat(), _q_json(center),
            json.dumps({"bin_topology": _BINS}),
        ),
    )


def _settle(conn, *, city, target_date, metric, value):
    conn.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, "
        "settlement_value, settlement_unit, authority) VALUES (?,?,?,?,?,?)",
        (city, target_date, metric, value, "C", "VERIFIED"),
    )


def _build_db(conn) -> None:
    # 120 settled targets, each with a fresh cycle (center == settlement, err 0) and an
    # aged cycle 12h older whose center is biased +2C (aged err 2C). Fresh serving age ~7h.
    base_cycle = datetime(2026, 6, 1, 0, tzinfo=UTC)
    for d in range(120):
        td = f"2026-06-{2 + d % 25:02d}"
        city = f"City{d}"
        settle = 25.0
        fresh_cyc = base_cycle + timedelta(days=d)
        aged_cyc = fresh_cyc - timedelta(hours=12)          # 12h older -> lag 12h
        _seed(conn, city=city, target_date=td, metric="high",
              cycle_dt=fresh_cyc, computed_dt=fresh_cyc + timedelta(hours=7), center=settle)
        _seed(conn, city=city, target_date=td, metric="high",
              cycle_dt=aged_cyc, computed_dt=aged_cyc + timedelta(hours=7), center=settle + 2.0)
        _settle(conn, city=city, target_date=td, metric="high", value=settle)


def test_fit_positive_amber_v_and_walk_forward() -> None:
    conn = _conn()
    _build_db(conn)
    art = fit.build_artifact(conn, as_of="2027-01-01", generated_at="X", git_sha="Y")
    high = art["metrics"]["high"]
    # Aged cycle lag 12h -> age band floor((7.2+12)/6)*6 = 18 (AMBER). Its v = err²=4.0.
    assert high["v_by_age_band"].get("18") == 4.0
    assert high["v_by_age_band"].get("6") == 0.0   # fresh band, zero increment
    assert art["fresh_serving_floor_hours"] == 7.0  # measured p50 of the seeded fresh ages
    # Walk-forward: as_of BEFORE all targets => nothing fitted.
    empty = fit.build_artifact(conn, as_of="2026-06-01", generated_at="X", git_sha="Y")
    assert empty["metrics"] == {}
    assert empty["targets_used"] == 0


def test_fit_monotone_in_age() -> None:
    conn = _conn()
    _build_db(conn)
    # Add a 24h-older cycle with a LARGER bias to a few targets so a higher band exists.
    base_cycle = datetime(2026, 6, 1, 0, tzinfo=UTC)
    for d in range(120):
        td = f"2026-06-{2 + d % 25:02d}"
        fresh_cyc = base_cycle + timedelta(days=d)
        _seed(conn, city=f"City{d}", target_date=td, metric="high",
              cycle_dt=fresh_cyc - timedelta(hours=24),
              computed_dt=fresh_cyc - timedelta(hours=24) + timedelta(hours=7), center=25.0 + 3.0)
    art = fit.build_artifact(conn, as_of="2027-01-01", generated_at="X", git_sha="Y")
    vbands = art["metrics"]["high"]["v_by_age_band"]
    ordered = [vbands[k] for k in sorted(vbands, key=int)]
    assert ordered == sorted(ordered)          # monotone non-decreasing (cummax)
    assert all(v >= 0.0 for v in ordered)      # non-negative


def test_fit_determinism() -> None:
    conn = _conn()
    _build_db(conn)
    a = fit.build_artifact(conn, as_of="2027-01-01", generated_at="PIN", git_sha="SHA")
    b = fit.build_artifact(conn, as_of="2027-01-01", generated_at="PIN", git_sha="SHA")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
