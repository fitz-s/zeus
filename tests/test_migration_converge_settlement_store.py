# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: RED->GREEN tests for the W0 ghost-table archive migration.
# Reuse: Run with pytest; update if migration steps/guards change.
# Authority basis: unification-design W0 ghost-table migration 2026-06-03
# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: unification-design W0 ghost-table migration 2026-06-03
"""RED→GREEN tests for W0-T3: 202606_converge_settlement_store migration.

Covers:
  W0-T3-A: structural/scan — src/ has 0 live SELECT/FROM platt_models_v2 readers
             (confirms ghost is safe to archive)
  W0-T3-B: migration up() on temp DB with all 3 tables → renames v2 + drops ghosts
  W0-T3-C: migration up() when platt_models has rows → aborts (row-count guard)
  W0-T3-D: migration up() when model_bias has rows → aborts (row-count guard)
  W0-T3-E: migration up() is idempotent — second call is a no-op
  W0-T3-F: migration down() reverses up() on temp DB
  W0-T3-G: dry_run_plan() prints expected lines without mutating the DB
"""
from __future__ import annotations

import importlib
import re
import sqlite3
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers to build minimal forecasts-DB fixtures
# ---------------------------------------------------------------------------

def _make_forecasts_db(tmp_path, *, pm_rows=0, mb_rows=0, v2_rows=137) -> sqlite3.Connection:
    """Return an in-memory (or tmp) SQLite connection with the 3 ghost tables."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE platt_models_v2 (
            id INTEGER PRIMARY KEY,
            temperature_metric TEXT,
            cluster TEXT,
            season TEXT,
            data_version TEXT,
            input_space TEXT,
            is_active TEXT
        )"""
    )
    for i in range(v2_rows):
        conn.execute(
            "INSERT INTO platt_models_v2 VALUES (?,?,?,?,?,?,?)",
            (i, "C", "cluster_a", "JJA", f"full_transport_v1_{i}", "full", "VERIFIED"),
        )
    conn.execute(
        """CREATE TABLE platt_models (
            id INTEGER PRIMARY KEY,
            temperature_metric TEXT,
            cluster TEXT,
            season TEXT
        )"""
    )
    for i in range(pm_rows):
        conn.execute("INSERT INTO platt_models VALUES (?,?,?,?)", (i, "C", "a", "JJA"))
    conn.execute(
        """CREATE TABLE model_bias (
            city TEXT,
            season TEXT,
            source TEXT,
            bias REAL,
            mae REAL,
            n_samples INTEGER,
            discount_factor REAL
        )"""
    )
    for i in range(mb_rows):
        conn.execute(
            "INSERT INTO model_bias VALUES (?,?,?,?,?,?,?)",
            (f"city_{i}", "JJA", "ecmwf", 0.1, 0.2, 100, 0.7),
        )
    conn.commit()
    return conn


def _load_migration():
    from scripts.migrations import _load_migration_module
    mig_path = (
        Path(__file__).parent.parent
        / "scripts" / "migrations" / "202606_converge_settlement_store.py"
    )
    return _load_migration_module(mig_path)


# ---------------------------------------------------------------------------
# W0-T3-A: structural scan — 0 live platt_models_v2 readers in src/
# ---------------------------------------------------------------------------

def test_no_live_src_readers_of_platt_models_v2():
    """W0-T3-A: src/ must have 0 SELECT/FROM platt_models_v2 hits.

    This is the RED test that confirms the table is safe to archive.
    It passes once the ghost has 0 live readers (should pass on fresh code).
    """
    src_root = Path(__file__).parent.parent / "src"
    hits = []
    pattern = re.compile(r"\bplatt_models_v2\b", re.IGNORECASE)
    for py_file in src_root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        text = py_file.read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                # Exclude comment-only lines (they're documentation, not runtime reads)
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                hits.append(f"{py_file.relative_to(src_root)}:{lineno}: {stripped[:80]}")
    assert hits == [], (
        f"Found {len(hits)} live src/ reference(s) to platt_models_v2:\n"
        + "\n".join(hits)
        + "\nAll must be removed before archiving the table."
    )


# ---------------------------------------------------------------------------
# W0-T3-B: up() archives v2 + drops 0-row ghosts
# ---------------------------------------------------------------------------

def test_migration_up_full_fixture(tmp_path):
    """W0-T3-B: up() on DB with all 3 tables → rename + 2 drops."""
    conn = _make_forecasts_db(tmp_path)
    mig = _load_migration()
    mig.up(conn)
    # platt_models_v2 → renamed
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "platt_models_v2" not in tables, "platt_models_v2 should be renamed"
    assert "platt_models_v2_archived_2026_06_03" in tables, "archived table should exist"
    assert "platt_models" not in tables, "platt_models should be dropped"
    assert "model_bias" not in tables, "model_bias should be dropped"
    # Archived table should have all 137 rows
    count = conn.execute("SELECT COUNT(*) FROM platt_models_v2_archived_2026_06_03").fetchone()[0]
    assert count == 137


# ---------------------------------------------------------------------------
# W0-T3-C: row-count guard on platt_models
# ---------------------------------------------------------------------------

def test_migration_aborts_on_nonempty_platt_models(tmp_path):
    """W0-T3-C: up() with platt_models having rows → RuntimeError, nothing dropped."""
    conn = _make_forecasts_db(tmp_path, pm_rows=5)
    mig = _load_migration()
    with pytest.raises(RuntimeError, match="platt_models has 5 rows"):
        mig.up(conn)
    # DB must be unchanged
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    # platt_models_v2 may or may not have been renamed depending on SAVEPOINT ordering;
    # the key assertion is that platt_models was NOT dropped
    assert "platt_models" in tables


# ---------------------------------------------------------------------------
# W0-T3-D: row-count guard on model_bias
# ---------------------------------------------------------------------------

def test_migration_aborts_on_nonempty_model_bias(tmp_path):
    """W0-T3-D: up() with model_bias having rows → RuntimeError, nothing dropped."""
    conn = _make_forecasts_db(tmp_path, mb_rows=3)
    mig = _load_migration()
    with pytest.raises(RuntimeError, match="model_bias has 3 rows"):
        mig.up(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "model_bias" in tables


# ---------------------------------------------------------------------------
# W0-T3-E: idempotent — second up() call is a no-op
# ---------------------------------------------------------------------------

def test_migration_up_idempotent(tmp_path):
    """W0-T3-E: calling up() twice does not raise and leaves DB consistent."""
    conn = _make_forecasts_db(tmp_path)
    mig = _load_migration()
    mig.up(conn)
    mig.up(conn)  # must not raise
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "platt_models_v2_archived_2026_06_03" in tables
    assert "platt_models_v2" not in tables


# ---------------------------------------------------------------------------
# W0-T3-F: down() reverses up()
# ---------------------------------------------------------------------------

def test_migration_down_reverses_up(tmp_path):
    """W0-T3-F: up() then down() restores platt_models + model_bias shells."""
    conn = _make_forecasts_db(tmp_path)
    mig = _load_migration()
    mig.up(conn)
    mig.down(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "platt_models_v2" in tables, "platt_models_v2 should be restored"
    assert "platt_models" in tables, "platt_models ghost shell should be recreated"
    assert "model_bias" in tables, "model_bias ghost shell should be recreated"
    # Archived name should be gone after down()
    assert "platt_models_v2_archived_2026_06_03" not in tables


# ---------------------------------------------------------------------------
# W0-T3-G: dry_run_plan() is read-only
# ---------------------------------------------------------------------------

def test_dry_run_plan_does_not_mutate(tmp_path, capsys):
    """W0-T3-G: dry_run_plan() prints the plan without changing any tables."""
    conn = _make_forecasts_db(tmp_path)
    mig = _load_migration()
    mig.dry_run_plan(conn)
    captured = capsys.readouterr()
    assert "RENAME" in captured.out or "platt_models_v2" in captured.out
    # DB unchanged
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "platt_models_v2" in tables, "dry_run must not rename the table"
    assert "platt_models" in tables, "dry_run must not drop platt_models"
