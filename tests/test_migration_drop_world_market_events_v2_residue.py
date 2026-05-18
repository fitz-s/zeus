# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: Coverage for scripts/migrations/202605_drop_world_market_events_v2_residue.py
#   F4 residue cleanup (POST_K1_DELTA.md F4 row) — happy path, drift refusal,
#   idempotent re-run.
# Authority basis: FIX_PLAN.md §5 PR-A (F4); brief at
#   /Users/leofitz/.claude/jobs/9ea6f95c/briefs/pr_a_f4_residue.md
"""Tests for 202605_drop_world_market_events_v2_residue migration."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = (
    _REPO_ROOT / "scripts" / "migrations"
    / "202605_drop_world_market_events_v2_residue.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migr_f4", _SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_migration = _load_migration()

# Minimal DDL for market_events_v2 matching the pre-K1 world.db schema
_CREATE_MARKET_EVENTS_V2 = """
CREATE TABLE market_events_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT,
    market_slug TEXT,
    condition_id TEXT,
    token_id TEXT,
    range_label TEXT,
    range_low REAL,
    range_high REAL,
    outcome TEXT,
    created_at TEXT,
    recorded_at TEXT
)
"""

_EXPECTED_ROW_COUNT = 2112


def _make_world_conn(row_count: int = _EXPECTED_ROW_COUNT) -> sqlite3.Connection:
    """Return an :memory: connection with market_events_v2 populated."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_CREATE_MARKET_EVENTS_V2)
    # Insert dummy rows (bulk via executemany for speed)
    conn.executemany(
        "INSERT INTO market_events_v2 (city, target_date, temperature_metric) VALUES (?,?,?)",
        [("TestCity", f"2026-01-{(i % 28) + 1:02d}", "high") for i in range(row_count)],
    )
    conn.commit()
    return conn


class TestMigrationHappyPath:
    """up(conn) with ~2,112 rows drops the table successfully."""

    def test_happy_path_drops_table(self):
        """up() on a conn with 2,112 rows drops market_events_v2."""
        conn = _make_world_conn(_EXPECTED_ROW_COUNT)

        # Pre: table exists
        pre = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_events_v2'"
        ).fetchone()[0]
        assert pre == 1

        _migration.up(conn)

        # Post: table absent
        post = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_events_v2'"
        ).fetchone()[0]
        assert post == 0, (
            "up() must drop market_events_v2; table still present after migration."
        )
        conn.close()

    def test_happy_path_within_tolerance_low(self):
        """up() accepts row count at lower tolerance bound (2,112 * 0.90 + 1 = 1902)."""
        # 10% tolerance: abs(count - 2112) <= 211
        low = _EXPECTED_ROW_COUNT - 211  # exactly at boundary
        conn = _make_world_conn(low)
        _migration.up(conn)  # must not raise
        post = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_events_v2'"
        ).fetchone()[0]
        assert post == 0
        conn.close()

    def test_happy_path_within_tolerance_high(self):
        """up() accepts row count at upper tolerance bound (2,112 + 211 = 2323)."""
        high = _EXPECTED_ROW_COUNT + 211
        conn = _make_world_conn(high)
        _migration.up(conn)  # must not raise
        post = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_events_v2'"
        ).fetchone()[0]
        assert post == 0
        conn.close()


class TestMigrationDriftRefusal:
    """up(conn) raises AssertionError when row count is outside ±10% window."""

    def test_drift_too_high_raises(self):
        """up() raises AssertionError when row count greatly exceeds expected."""
        # 5000 rows = +137% drift, far outside ±10%
        conn = _make_world_conn(5000)
        with pytest.raises(AssertionError, match="deviates >10%"):
            _migration.up(conn)
        conn.close()

    def test_drift_too_low_raises(self):
        """up() raises AssertionError when row count is far below expected."""
        # 100 rows = -95% drift, far outside ±10%
        conn = _make_world_conn(100)
        with pytest.raises(AssertionError, match="deviates >10%"):
            _migration.up(conn)
        conn.close()

    def test_drift_exactly_at_boundary_plus_one_raises(self):
        """up() raises when count is one row beyond tolerance ceiling."""
        over = _EXPECTED_ROW_COUNT + 212  # tolerance is 211; 212 is outside
        conn = _make_world_conn(over)
        with pytest.raises(AssertionError, match="deviates >10%"):
            _migration.up(conn)
        conn.close()


class TestMigrationIdempotency:
    """Second call to up(conn) on already-dropped state is a no-op."""

    def test_second_run_is_noop(self):
        """up() called twice does not raise on the second call."""
        conn = _make_world_conn(_EXPECTED_ROW_COUNT)
        _migration.up(conn)  # first run: drops table

        # Second run: table absent → early return (no exception)
        _migration.up(conn)  # must not raise

        post = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_events_v2'"
        ).fetchone()[0]
        assert post == 0, "table must remain absent after idempotent re-run"
        conn.close()

    def test_no_table_no_error(self):
        """up() on a conn where market_events_v2 never existed is a no-op."""
        conn = sqlite3.connect(":memory:")
        # Don't create market_events_v2 at all
        _migration.up(conn)  # must not raise
        conn.close()
