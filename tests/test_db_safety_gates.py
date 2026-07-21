"""Antibody for the combined DB safety-gate preflight (scripts/ops/db_safety_gates.py)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "db_safety_gates.py"
PY = sys.executable


def _run(state_dir: Path, manifest: Path):
    return subprocess.run([PY, str(SCRIPT), "--state-dir", str(state_dir), "--manifest", str(manifest)],
                          capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))


def _clean_manifest(tmp_path: Path) -> Path:
    m = tmp_path / "manifest.yaml"
    m.write_text("tables:\n  - name: t\n    db: trade\n    schema_class: trade_class\n    notes: live\n")
    return m


def test_clean_fleet_passes(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    c = sqlite3.connect(str(state / "zeus_trades.db"))
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)"); c.commit(); c.close()
    r = _run(state, _clean_manifest(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GATE OK" in r.stdout and "GATE FAIL" not in r.stdout


def test_dangling_fk_fails_the_gate(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    c = sqlite3.connect(str(state / "zeus_trades.db"))
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, fk INTEGER REFERENCES gone(x))")
    c.commit(); c.close()
    r = _run(state, _clean_manifest(tmp_path))
    assert r.returncode == 1
    assert "dangling foreign key" in r.stdout


def test_manifest_rot_fails_the_gate(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    c = sqlite3.connect(str(state / "zeus_trades.db"))
    c.execute("CREATE TABLE archived_but_live (id INTEGER PRIMARY KEY)")
    c.execute("INSERT INTO archived_but_live VALUES (1)")
    c.commit(); c.close()
    m = tmp_path / "manifest.yaml"
    m.write_text("tables:\n  - name: archived_but_live\n    db: trade\n    schema_class: legacy_archived\n    notes: ghost\n")
    r = _run(state, m)
    assert r.returncode == 1
    assert "droppable-labeled" in r.stdout
