#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (b) (staleness priced as fitted error VARIANCE, walk-forward only).
"""Unit tests for scripts/fit_model_staleness_variance.py — no live DB required.

Covers: (1) the walk-forward boundary (a settlement dated exactly on as_of is never
used); (2) v monotonicity (cummax over increasing lead; a sampling-noise m2 dip cannot
produce a decreasing v); (3) v non-negativity + freshest-bucket zero; (4) determinism
(same DB state + as_of + generated_at => byte-identical artifact + pointer); (5) F->C
settlement conversion; (6) the MIN_CELL_N floor drops thin lead cells from v (fail-open
downstream) while keeping their m2/n for transparency.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fit_model_staleness_variance as fmsv  # noqa: E402


def _make_db(rows: list[dict]) -> sqlite3.Connection:
    """rows: model, city, metric, target_date, lead_days, forecast_value_c,
    settlement_value_c (inserted unit='C' unless settlement_value+settlement_unit given)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_model_forecasts (model TEXT, city TEXT, target_date TEXT, "
        "metric TEXT, lead_days INTEGER, forecast_value_c REAL, endpoint TEXT)"
    )
    conn.execute(
        "CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT, temperature_metric TEXT, "
        "settlement_value REAL, settlement_unit TEXT, authority TEXT)"
    )
    seen: set[tuple[str, str, str]] = set()
    for r in rows:
        conn.execute(
            "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?)",
            (r["model"], r["city"], r["target_date"], r["metric"], r["lead_days"],
             r["forecast_value_c"], r.get("endpoint", "previous_runs")),
        )
        key = (r["city"], r["target_date"], r["metric"])
        if key not in seen:
            seen.add(key)
            conn.execute(
                "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
                (r["city"], r["target_date"], r["metric"],
                 r.get("settlement_value", r.get("settlement_value_c")),
                 r.get("settlement_unit", "C"), r.get("authority", "VERIFIED")),
            )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _rows(model: str, *, n: int, offsets_by_lead: dict[int, float], city: str = "C1",
          metric: str = "high") -> list[dict]:
    """n settled dates; per lead a constant residual offset (m2 = offset²)."""
    out = []
    for i in range(n):
        month = 1 + (i // 28)
        day = 1 + (i % 28)
        date = f"2026-{month:02d}-{day:02d}"
        settle = 10.0 + (i % 5) * 0.3
        for lead, off in offsets_by_lead.items():
            out.append({
                "model": model, "city": city, "metric": metric, "target_date": date,
                "lead_days": lead, "forecast_value_c": settle + off,
                "settlement_value_c": settle,
            })
    return out


def test_walk_forward_boundary_excludes_as_of_date() -> None:
    rows = _rows("A", n=35, offsets_by_lead={0: 0.5})
    # A poison row dated exactly ON as_of: must never train.
    rows += [{"model": "A", "city": "C1", "metric": "high", "target_date": "2026-06-01",
              "lead_days": 0, "forecast_value_c": 99.0, "settlement_value_c": 0.0}]
    conn = _make_db(rows)
    stats = fmsv.load_cell_stats(conn, as_of="2026-06-01")
    m2, n = stats[("A", "high", 0)]
    assert n == 35
    assert m2 == 0.25  # constant +0.5 residual; the 99-vs-0 poison would explode this


def test_v_monotone_nonneg_and_zero_at_freshest() -> None:
    # lead 0: m2=0.25; lead 1: m2=1.0; lead 2: m2=0.49 (a DIP below lead 1 — sampling
    # noise posture); lead 3: m2=4.0. cummax must hold v(2) at v(1), never decrease.
    rows = _rows("A", n=35, offsets_by_lead={0: 0.5, 1: 1.0, 2: 0.7, 3: 2.0})
    conn = _make_db(rows)
    tables = fmsv.derive_v_tables(fmsv.load_cell_stats(conn, as_of="2026-12-31"))
    entry = tables["A"]["high"]
    assert entry["freshest_lead"] == 0
    v = {int(k): val for k, val in entry["v_by_lead"].items()}
    assert v[0] == 0.0
    assert v[1] == round(1.0 - 0.25, 6)
    assert v[2] == v[1], "cummax must clip the m2 dip — v can never decrease with lag"
    assert v[3] == round(4.0 - 0.25, 6)
    leads = sorted(v)
    assert all(v[a] <= v[b] for a, b in zip(leads, leads[1:]))
    assert all(val >= 0.0 for val in v.values())


def test_thin_lead_cell_below_min_n_gets_no_v_entry() -> None:
    rows = _rows("A", n=35, offsets_by_lead={0: 0.5, 1: 1.0})
    # lead 3 exists but with only 5 settled cells (< MIN_CELL_N): m2/n recorded, no v.
    rows += _rows("A", n=5, offsets_by_lead={3: 3.0}, city="C2")
    conn = _make_db(rows)
    tables = fmsv.derive_v_tables(fmsv.load_cell_stats(conn, as_of="2026-12-31"))
    entry = tables["A"]["high"]
    assert "3" in entry["m2_by_lead"] and entry["n_by_lead"]["3"] == 5
    assert "3" not in entry["v_by_lead"]
    assert set(entry["v_by_lead"]) == {"0", "1"}


def test_settlement_fahrenheit_converted_before_residual() -> None:
    # settle 50F == 10C; forecast 10.5C -> residual 0.5C -> m2 0.25 (degC²).
    rows = []
    for i in range(31):
        date = f"2026-01-{(i % 28) + 1:02d}" if i < 28 else f"2026-02-{i - 27:02d}"
        rows.append({"model": "A", "city": "F1", "metric": "high", "target_date": date,
                     "lead_days": 0, "forecast_value_c": 10.5,
                     "settlement_value": 50.0, "settlement_unit": "F"})
    conn = _make_db(rows)
    stats = fmsv.load_cell_stats(conn, as_of="2026-12-31")
    m2, n = stats[("A", "high", 0)]
    assert n == 31
    assert abs(m2 - 0.25) < 1e-9


def test_determinism_byte_identical_artifact(tmp_path: Path) -> None:
    rows = _rows("A", n=35, offsets_by_lead={0: 0.5, 1: 1.0}) + _rows(
        "B", n=35, offsets_by_lead={0: 0.3, 2: 1.5}
    )
    conn = _make_db(rows)
    db_path = tmp_path / "fcst.db"
    disk = sqlite3.connect(db_path)
    conn.backup(disk)
    disk.close()

    out_a, out_b = tmp_path / "a", tmp_path / "b"
    for out in (out_a, out_b):
        rc = fmsv.main([
            "--fcst", str(db_path), "--as-of", "2026-06-01",
            "--generated-at", "2026-07-17T00:00:00+00:00", "--out-dir", str(out),
        ])
        assert rc == 0
    name = "staleness_variance_20260601.json"
    assert (out_a / name).read_bytes() == (out_b / name).read_bytes()
    assert (out_a / "ACTIVE.json").read_bytes() == (out_b / "ACTIVE.json").read_bytes()
    pointer = json.loads((out_a / "ACTIVE.json").read_text())
    assert pointer["artifact"] == name and len(pointer["sha256"]) == 64


def test_pooled_across_cities() -> None:
    # Same model/lead in two cities: residuals pool into ONE (model, metric, lead) cell.
    rows = _rows("A", n=20, offsets_by_lead={0: 0.5}, city="C1")
    rows += _rows("A", n=20, offsets_by_lead={0: 0.5}, city="C2")
    conn = _make_db(rows)
    stats = fmsv.load_cell_stats(conn, as_of="2026-12-31")
    m2, n = stats[("A", "high", 0)]
    assert n == 40 and abs(m2 - 0.25) < 1e-9
