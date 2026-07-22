# Lifecycle: created=2026-07-21; last_reviewed=2026-07-22; last_reused=never
# Purpose: prove the settlement-outcomes reconciliation monitor detects chain-settled positions missing their outcome row.
# Reuse: run when changing scripts/ops/reconcile_settlement_outcomes.py or the settlement outbox contract.
"""Antibody for the settlement-outcomes reconciliation monitor
(scripts/ops/reconcile_settlement_outcomes.py) — the check that would have caught the 16
chain-settled positions silently missing their settlement_outcomes row.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "reconcile_settlement_outcomes.py"
PY = sys.executable


def _run(state_dir: Path, *extra: str):
    return subprocess.run([PY, str(SCRIPT), "--state-dir", str(state_dir), *extra],
                          capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))


def _trades_db(state: Path, positions: list[tuple]) -> None:
    """positions: (position_id, phase, city, target_date, metric, settlement_price)."""
    c = sqlite3.connect(str(state / "zeus_trades.db"))
    c.execute("""CREATE TABLE position_current (
        position_id TEXT PRIMARY KEY, phase TEXT, trade_id TEXT, city TEXT, target_date TEXT,
        temperature_metric TEXT, settlement_price REAL, settled_at TEXT)""")
    for pid, phase, city, target_date, metric, price in positions:
        c.execute("INSERT INTO position_current (position_id, phase, trade_id, city, target_date, "
                   "temperature_metric, settlement_price, settled_at) VALUES (?,?,?,?,?,?,?,?)",
                   (pid, phase, pid, city, target_date, metric, price, "2026-07-15T00:20:24+00:00"))
    c.commit(); c.close()


def _forecasts_db(state: Path, outcomes: list[tuple]) -> None:
    """outcomes: (city, target_date, metric)."""
    c = sqlite3.connect(str(state / "zeus-forecasts.db"))
    c.execute("CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT, temperature_metric TEXT)")
    for city, target_date, metric in outcomes:
        c.execute("INSERT INTO settlement_outcomes VALUES (?,?,?)", (city, target_date, metric))
    c.commit(); c.close()


def test_settled_position_missing_outcome_is_flagged(tmp_path):
    """The exact W13 shape: a VENUE_RESOLVED-settled position with no forecasts-side row."""
    state = tmp_path / "state"; state.mkdir()
    _trades_db(state, [("384f1dd8-5c1", "settled", "Hong Kong", "2026-07-13", "high", 0.0)])
    _forecasts_db(state, [])
    r = _run(state)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "settled_without_outcome_total=1" in r.stdout
    assert "Hong Kong" in r.stdout and "384f1dd8-5c1" in r.stdout


def test_settled_position_with_outcome_not_flagged(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    _trades_db(state, [("p2", "settled", "Paris", "2026-07-02", "low", 0.0)])
    _forecasts_db(state, [("Paris", "2026-07-02", "low")])
    r = _run(state)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "settled_without_outcome_total=0" in r.stdout


def test_unsettled_position_not_flagged(tmp_path):
    """Neither a non-settled phase nor a settled phase with no realized settlement_price
    is a gap — the anti-join only ever considers phase='settled' AND settlement_price NOT NULL."""
    state = tmp_path / "state"; state.mkdir()
    _trades_db(state, [
        ("p3", "active", "Tokyo", "2026-07-10", "high", None),
        ("p4", "settled", "Ankara", "2026-07-11", "high", None),
    ])
    _forecasts_db(state, [])
    r = _run(state)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "settled_without_outcome_total=0" in r.stdout


def test_missing_db_file_skips_clean(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    r = _run(state)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SKIP" in r.stdout
