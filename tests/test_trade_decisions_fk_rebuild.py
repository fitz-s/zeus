# Lifecycle: created=2026-07-20; last_reviewed=2026-07-22; last_reused=never
# Purpose: green-gate the W0-a trade_decisions dangling-FK rebuild migration (fixture DB, 6-kill-point crash-atomicity).
# Reuse: run before/after scripts/migrations/202607_trade_decisions_drop_dangling_fk.py.
"""Antibody suite for W0-a: trade_decisions dangling-FK removal rebuild.

Green-gate for `scripts/migrations/202607_trade_decisions_drop_dangling_fk.py`
(the live-money migration). Every test runs against a FIXTURE trades DB built from
the exact live CREATE sql — never the live DB. The crash matrix runs the migration
as a SUBPROCESS with ZEUS_W0A_KILL_AT set so `os._exit(1)` simulates a real
SIGKILL/power-loss at each pre-COMMIT checkpoint, then reopens from a fresh process
and asserts the pre-migration state survived intact (never a `_new` residue / mixed
state). Authority: W0_RUNBOOK.md + consult_W0_verdict.md.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "scripts" / "migrations" / "202607_trade_decisions_drop_dangling_fk.py"
PY = sys.executable

# The exact live CREATE sql this migration is pinned against (sha256 asserted by the
# script). Kept here so the fixture reproduces the pinned schema byte-for-byte.
LIVE_CREATE_SQL = """CREATE TABLE trade_decisions (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            bin_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            size_usd REAL NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            forecast_snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id),
            calibration_model_version TEXT,
            p_raw REAL NOT NULL,
            p_calibrated REAL,
            p_posterior REAL NOT NULL,
            edge REAL NOT NULL,
            ci_lower REAL NOT NULL,
            ci_upper REAL NOT NULL,
            kelly_fraction REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filled_at TEXT,
            fill_price REAL,
            runtime_trade_id TEXT,
            order_id TEXT,
            order_status_text TEXT,
            order_posted_at TEXT,
            entered_at_ts TEXT,
            chain_state TEXT,
            -- Attribution fields (CLAUDE.md: mandatory on every trade)
            strategy TEXT,
            edge_source TEXT,
            bin_type TEXT,
            discovery_mode TEXT,
            market_hours_open REAL,
            fill_quality REAL,
            entry_method TEXT,
            selected_method TEXT,
            applied_validations_json TEXT,
            exit_trigger TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            exit_divergence_score REAL DEFAULT 0.0,
            exit_market_velocity_1h REAL DEFAULT 0.0,
            exit_forward_edge REAL DEFAULT 0.0,
            -- Phase 2 Domain Object Snapshots (JSON flattened blobs)
            settlement_semantics_json TEXT,
            epistemic_context_json TEXT,
            edge_context_json TEXT,
            -- Phase 3: Shadow Proof True Attribution
            entry_alpha_usd REAL DEFAULT 0.0,
            execution_slippage_usd REAL DEFAULT 0.0,
            exit_timing_usd REAL DEFAULT 0.0,
            risk_throttling_usd REAL DEFAULT 0.0,
            settlement_edge_usd REAL DEFAULT 0.0
        , env TEXT NOT NULL DEFAULT 'live')"""

_MIN_COLS = ("market_id", "bin_label", "direction", "size_usd", "price", "timestamp",
             "p_raw", "p_posterior", "edge", "ci_lower", "ci_upper", "kelly_fraction",
             "status", "env")


def _pinned_sha_matches() -> bool:
    return hashlib.sha256(LIVE_CREATE_SQL.encode()).hexdigest() == \
        "6a637b7e6ef3f690276c899c96a5deb89d7931aa07bfd3ec5f48a39fd6621c55"


# The whole suite is meaningless if the fixture schema has drifted from the pin.
pytestmark = pytest.mark.skipif(
    not _pinned_sha_matches(),
    reason="LIVE_CREATE_SQL no longer matches the pinned sha256; re-pin the migration + this fixture.")


def _build_fixture(state_dir: Path, *, rows: int = 5, delete_top: bool = True) -> Path:
    """Fixture trades DB: the pinned trade_decisions schema (with the dangling FK),
    `rows` rows, and — when delete_top — the top row deleted so seq > max(rowid)
    (the B3 high-water case)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / "zeus_trades.db"
    c = sqlite3.connect(str(db))
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute(LIVE_CREATE_SQL)
        cols = "trade_id," + ",".join(_MIN_COLS)
        ph = ",".join(["?"] * (1 + len(_MIN_COLS)))
        for i in range(1, rows + 1):
            c.execute(f"INSERT INTO trade_decisions ({cols}) VALUES ({ph})",
                      (i, "m", "b", "buy_yes", 1.0, 0.5, "t", 0.5, 0.5, 0.1, 0.0, 1.0, 0.1, "s", "live"))
        if delete_top:
            c.execute("DELETE FROM trade_decisions WHERE trade_id=?", (rows,))
        c.commit()
    finally:
        c.close()
    return db


def _run(state_dir: Path, *, kill_at: str | None = None, extra: list[str] | None = None):
    env = dict(os.environ, ZEUS_W0A_SKIP_PROCESS_CHECK="1")
    if kill_at:
        env["ZEUS_W0A_KILL_AT"] = kill_at
    cmd = [PY, str(MIGRATION), "--operator-confirms-fenced", "--state-dir", str(state_dir),
           "--capsule-dir", str(state_dir / "cap")]
    if extra:
        cmd += extra
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def _fk_edges(db: Path):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA foreign_key_list(trade_decisions)").fetchall()
    finally:
        c.close()


def _has_new_residue(db: Path) -> bool:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return bool(c.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='trade_decisions_new'").fetchone()[0])
    finally:
        c.close()


# --- Happy path -------------------------------------------------------------

def test_happy_path_removes_fk_preserves_rows_and_unfreezes(tmp_path):
    db = _build_fixture(tmp_path, rows=5, delete_top=True)  # max=4, seq=5
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _fk_edges(db) == [], "dangling ensemble_snapshots FK must be gone"
    assert not _has_new_residue(db)
    c = sqlite3.connect(str(db))
    try:
        c.execute("PRAGMA foreign_keys=ON")  # the frozen condition
        assert c.execute("SELECT count(*) FROM trade_decisions").fetchone()[0] == 4
        # B3: high-water preserved -> next id is 6, not the reused 5
        cols = ",".join(_MIN_COLS)
        ph = ",".join(["?"] * len(_MIN_COLS))
        c.execute(f"INSERT INTO trade_decisions ({cols}) VALUES ({ph})",
                  ("m", "b", "buy_yes", 1.0, 0.5, "t", 0.5, 0.5, 0.1, 0.0, 1.0, 0.1, "s", "live"))
        assert c.execute("SELECT max(trade_id) FROM trade_decisions").fetchone()[0] == 6
    finally:
        c.close()


def test_idempotent_second_run_refuses(tmp_path):
    _build_fixture(tmp_path)
    assert _run(tmp_path).returncode == 0
    r2 = _run(tmp_path)  # FK already gone -> schema-pin/FK-presence assert fails
    assert r2.returncode != 0
    assert "REFUSED" in (r2.stderr + r2.stdout)


# --- Sequence matrix (B3) ---------------------------------------------------

@pytest.mark.parametrize("rows,delete_top,expect_next", [(5, True, 6), (4, False, 5)])
def test_sequence_high_water_preserved(tmp_path, rows, delete_top, expect_next):
    db = _build_fixture(tmp_path, rows=rows, delete_top=delete_top)
    assert _run(tmp_path).returncode == 0
    c = sqlite3.connect(str(db))
    try:
        cols = ",".join(_MIN_COLS)
        ph = ",".join(["?"] * len(_MIN_COLS))
        c.execute(f"INSERT INTO trade_decisions ({cols}) VALUES ({ph})",
                  ("m", "b", "buy_yes", 1.0, 0.5, "t", 0.5, 0.5, 0.1, 0.0, 1.0, 0.1, "s", "live"))
        assert c.execute("SELECT max(trade_id) FROM trade_decisions").fetchone()[0] == expect_next
    finally:
        c.close()


def test_sequence_wide_gap_high_water(tmp_path):
    """Consult-mandated antibody: id 9000 committed then deleted (max drops back to 4),
    sqlite_sequence.seq=9000. After migration the next AUTOINCREMENT id must be 9001 —
    never a reused id. This is the exact case that a naive rebuild (reset seq to max)
    would corrupt by aliasing a deleted historical decision to a new one (consult B3)."""
    db = _build_fixture(tmp_path, rows=4, delete_top=False)  # ids 1..4, seq=4
    c = sqlite3.connect(str(db))
    try:
        cols = "trade_id," + ",".join(_MIN_COLS)
        ph = ",".join(["?"] * (1 + len(_MIN_COLS)))
        c.execute(f"INSERT INTO trade_decisions ({cols}) VALUES ({ph})",
                  (9000, "m", "b", "buy_yes", 1.0, 0.5, "t", 0.5, 0.5, 0.1, 0.0, 1.0, 0.1, "s", "live"))
        c.execute("DELETE FROM trade_decisions WHERE trade_id=9000")  # max=4, seq stays 9000
        c.commit()
        assert c.execute("SELECT seq FROM sqlite_sequence WHERE name='trade_decisions'").fetchone()[0] == 9000
    finally:
        c.close()
    assert _run(tmp_path).returncode == 0
    c = sqlite3.connect(str(db))
    try:
        assert c.execute("SELECT seq FROM sqlite_sequence WHERE name='trade_decisions'").fetchone()[0] == 9000
        cols = ",".join(_MIN_COLS)
        ph = ",".join(["?"] * len(_MIN_COLS))
        c.execute(f"INSERT INTO trade_decisions ({cols}) VALUES ({ph})",
                  ("m", "b", "buy_yes", 1.0, 0.5, "t", 0.5, 0.5, 0.1, 0.0, 1.0, 0.1, "s", "live"))
        assert c.execute("SELECT max(trade_id) FROM trade_decisions").fetchone()[0] == 9001
    finally:
        c.close()


def test_dependent_view_refused_before_any_change(tmp_path):
    """A global view referencing trade_decisions must be refused EARLY (before BEGIN,
    before the operator's fence), not aborted mid-RENAME under legacy_alter_table=OFF."""
    db = _build_fixture(tmp_path)
    c = sqlite3.connect(str(db))
    try:
        c.execute("CREATE VIEW v_recent AS SELECT trade_id, price FROM trade_decisions")
        c.commit()
    finally:
        c.close()
    r = _run(tmp_path)
    assert r.returncode != 0
    assert "dependent view/trigger" in (r.stderr + r.stdout)
    assert any(e[2] == "ensemble_snapshots" for e in _fk_edges(db))
    assert not _has_new_residue(db)


# --- Crash matrix -----------------------------------------------------------

@pytest.mark.parametrize("kill_at",
                         ["after_begin", "after_create", "after_copy", "after_drop",
                          "after_rename", "before_commit"])
def test_crash_leaves_old_state_intact(tmp_path, kill_at):
    """Kill (os._exit) at each pre-COMMIT checkpoint; a fresh reopen must show the
    ORIGINAL table (FK present) and NO trade_decisions_new — never a mixed state."""
    db = _build_fixture(tmp_path, rows=5, delete_top=True)
    r = _run(tmp_path, kill_at=kill_at)
    assert r.returncode != 0  # hard-exit
    # WAL discards the uncommitted frames on reopen -> pre-migration state survives.
    assert any(e[2] == "ensemble_snapshots" for e in _fk_edges(db)), \
        f"kill@{kill_at}: original dangling FK must survive a pre-COMMIT crash"
    assert not _has_new_residue(db), f"kill@{kill_at}: no trade_decisions_new residue"
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert c.execute("SELECT count(*) FROM trade_decisions").fetchone()[0] == 4
        assert c.execute("SELECT seq FROM sqlite_sequence WHERE name='trade_decisions'").fetchone()[0] == 5
    finally:
        c.close()


# --- Drift matrix (B2) ------------------------------------------------------

def test_schema_drift_aborts_before_write(tmp_path):
    """An extra column (physical drift from the pin) must abort before any change."""
    db = _build_fixture(tmp_path)
    c = sqlite3.connect(str(db))
    try:
        c.execute("ALTER TABLE trade_decisions ADD COLUMN sneaky TEXT")
        c.commit()
    finally:
        c.close()
    r = _run(tmp_path)
    assert r.returncode != 0
    assert "REFUSED" in (r.stderr + r.stdout)
    # unchanged: FK still present, no _new
    assert any(e[2] == "ensemble_snapshots" for e in _fk_edges(db))
    assert not _has_new_residue(db)


def test_preexisting_new_table_aborts(tmp_path):
    db = _build_fixture(tmp_path)
    c = sqlite3.connect(str(db))
    try:
        c.execute("CREATE TABLE trade_decisions_new (x INTEGER)")
        c.commit()
    finally:
        c.close()
    r = _run(tmp_path)
    assert r.returncode != 0
    assert "REFUSED" in (r.stderr + r.stdout)


def test_dry_run_makes_no_change_but_writes_capsule(tmp_path):
    db = _build_fixture(tmp_path)
    r = _run(tmp_path, extra=["--dry-run"])
    assert r.returncode == 0, r.stderr
    assert any(e[2] == "ensemble_snapshots" for e in _fk_edges(db)), "dry-run must not modify the table"
    caps = list((tmp_path / "cap").glob("*.sqlite"))
    assert caps, "dry-run must still write a rollback capsule"


# --- Fence (B1) -------------------------------------------------------------

def test_refuses_without_operator_confirms_fenced(tmp_path):
    _build_fixture(tmp_path)
    env = dict(os.environ, ZEUS_W0A_SKIP_PROCESS_CHECK="1")
    r = subprocess.run([PY, str(MIGRATION), "--state-dir", str(tmp_path)],
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "ALL-WRITER plane fenced" in (r.stderr + r.stdout)
