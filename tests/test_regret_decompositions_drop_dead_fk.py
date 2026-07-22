# Lifecycle: created=2026-07-20; last_reviewed=2026-07-22; last_reused=never
# Purpose: prove the regret_decompositions dead-FK drop migration removes the dangling FK without data loss.
# Reuse: run before/after scripts/migrations/202607_regret_decompositions_drop_dead_fk.py.
"""Antibody for the regret_decompositions dead-FK drop (2nd dangling-FK-class instance)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrations" / "202607_regret_decompositions_drop_dead_fk.py"
PY = sys.executable

CREATE = ("CREATE TABLE regret_decompositions (\n"
          "    id                              INTEGER PRIMARY KEY AUTOINCREMENT,\n"
          "    experiment_id                   TEXT NOT NULL\n"
          "        REFERENCES shadow_experiments(experiment_id),\n"
          "    decision_event_id               TEXT NOT NULL,\n"
          "    forecast_error_usd              REAL,\n"
          "    observation_error_usd           REAL,\n"
          "    quote_error_usd                 REAL,\n"
          "    non_fill_error_usd              REAL,\n"
          "    fee_error_usd                   REAL,\n"
          "    timing_error_usd                REAL,\n"
          "    settlement_ambiguity_error_usd  REAL,\n"
          "    total_regret_usd                REAL NOT NULL,\n"
          "    computed_at                     TEXT NOT NULL\n"
          ", strategy_id TEXT NOT NULL DEFAULT '', cohort_tag TEXT NOT NULL DEFAULT '')")


def _fixture(state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / "zeus-world.db"
    c = sqlite3.connect(str(db))
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=OFF")
    c.execute(CREATE); c.commit(); c.close()
    return db


def _run(state_dir: Path, extra=None):
    env = dict(os.environ, ZEUS_REGRET_FK_SKIP_PROCESS_CHECK="1")
    cmd = [PY, str(SCRIPT), "--operator-confirms-fenced", "--state-dir", str(state_dir)]
    if extra:
        cmd += extra
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def _fks(db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA foreign_key_list(regret_decompositions)").fetchall()
    finally:
        c.close()


def test_drops_dead_fk_and_unfreezes(tmp_path):
    db = _fixture(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _fks(db) == []
    c = sqlite3.connect(str(db)); c.execute("PRAGMA foreign_keys=ON")
    try:
        c.execute("INSERT INTO regret_decompositions (experiment_id,decision_event_id,total_regret_usd,computed_at) "
                  "VALUES ('e','d',1.0,'t')")
        assert c.execute("SELECT count(*) FROM regret_decompositions").fetchone()[0] == 1
    finally:
        c.close()


def test_idempotent_second_run_refuses(tmp_path):
    _fixture(tmp_path)
    assert _run(tmp_path).returncode == 0
    assert _run(tmp_path).returncode != 0  # FK already gone -> schema-pin refuses


def test_dry_run_no_change(tmp_path):
    db = _fixture(tmp_path)
    assert _run(tmp_path, extra=["--dry-run"]).returncode == 0
    assert any(e[2] == "shadow_experiments" for e in _fks(db))


def test_refuses_without_fence(tmp_path):
    _fixture(tmp_path)
    r = subprocess.run([PY, str(SCRIPT), "--state-dir", str(tmp_path)],
                       env=dict(os.environ, ZEUS_REGRET_FK_SKIP_PROCESS_CHECK="1"),
                       capture_output=True, text=True)
    assert r.returncode != 0 and "fenced" in (r.stderr + r.stdout)
