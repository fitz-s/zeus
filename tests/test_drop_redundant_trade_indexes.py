"""Antibody for F15 redundant-index drop (scripts/migrations/202607_drop_redundant_trade_indexes.py)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrations" / "202607_drop_redundant_trade_indexes.py"
PY = sys.executable


def _fixture(state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / "zeus_trades.db"
    c = sqlite3.connect(str(db))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE book_hash_transitions (market_slug TEXT, observed_at TEXT, "
              "transition_seq INTEGER, UNIQUE(market_slug, observed_at, transition_seq))")
    c.execute("CREATE INDEX idx_book_hash_transitions_market_time "
              "ON book_hash_transitions(market_slug, observed_at)")
    c.execute("CREATE TABLE market_price_history (token_id TEXT, recorded_at TEXT, market_slug TEXT, "
              "UNIQUE(token_id, recorded_at))")
    c.execute("CREATE INDEX idx_market_price_history_token_recorded "
              "ON market_price_history(token_id, recorded_at)")
    c.commit(); c.close()
    return db


def _run(state_dir: Path, *, extra=None, fenced=True):
    env = dict(os.environ, ZEUS_DROPIDX_SKIP_PROCESS_CHECK="1")
    cmd = [PY, str(SCRIPT), "--state-dir", str(state_dir)]
    if fenced:
        cmd.append("--operator-confirms-fenced")
    if extra:
        cmd += extra
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def _indexes(db: Path):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        c.close()


def test_drops_redundant_keeps_covering(tmp_path):
    db = _fixture(tmp_path)
    assert _run(tmp_path).returncode == 0
    idx = _indexes(db)
    assert "idx_book_hash_transitions_market_time" not in idx
    assert "idx_market_price_history_token_recorded" not in idx
    assert "sqlite_autoindex_book_hash_transitions_1" in idx
    assert "sqlite_autoindex_market_price_history_1" in idx


def test_dry_run_changes_nothing(tmp_path):
    db = _fixture(tmp_path)
    before = _indexes(db)
    assert _run(tmp_path, extra=["--dry-run"]).returncode == 0
    assert _indexes(db) == before


def test_idempotent_second_run(tmp_path):
    _fixture(tmp_path)
    assert _run(tmp_path).returncode == 0
    assert _run(tmp_path).returncode == 0  # second run: SKIP already-absent, no error


def test_refuses_without_fence(tmp_path):
    _fixture(tmp_path)
    r = _run(tmp_path, fenced=False)
    assert r.returncode != 0 and "fenced" in (r.stderr + r.stdout)


def test_refuses_if_covering_index_missing(tmp_path):
    """If the covering UNIQUE autoindex is absent, never drop the last index for the prefix."""
    state_dir = tmp_path
    state_dir.mkdir(exist_ok=True)
    db = state_dir / "zeus_trades.db"
    c = sqlite3.connect(str(db))
    # book_hash_transitions WITHOUT the UNIQUE constraint -> no covering autoindex
    c.execute("CREATE TABLE book_hash_transitions (market_slug TEXT, observed_at TEXT, transition_seq INTEGER)")
    c.execute("CREATE INDEX idx_book_hash_transitions_market_time "
              "ON book_hash_transitions(market_slug, observed_at)")
    c.commit(); c.close()
    r = _run(state_dir)
    assert r.returncode != 0 and "do not cover" in (r.stderr + r.stdout)
    assert "idx_book_hash_transitions_market_time" in _indexes(db)  # not dropped
