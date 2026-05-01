# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2 deliverable D
"""Test helper: fake_connection_pair() for monkeypatching in riskguard / fill_tracker tests.

Migration guide for test monkeypatches
---------------------------------------

**Old pattern (single ATTACH conn):**

    monkeypatch.setattr(cycle_runner_module, "get_connection",
                        lambda: fake_trade_with_world_conn)

**New pattern (ConnectionPair):**

    from tests.conftest_connection_pair import fake_connection_pair
    pair = fake_connection_pair(trade_conn=fake_trade_with_world_conn)
    monkeypatch.setattr(cycle_runner_module, "get_connection_pair", lambda: pair)
    # Keep get_connection pointing to trade_conn for backward compat during migration:
    monkeypatch.setattr(cycle_runner_module, "get_connection", lambda: pair.trade_conn)

**Riskguard tests (10+ monkeypatches at test_riskguard.py:272, 378, etc.):**

    Old:
        riskguard_module.get_connection = fake_world_conn

    New:
        from tests.conftest_connection_pair import fake_connection_pair
        pair = fake_connection_pair(world_conn=fake_world_conn,
                                    trade_conn=fake_trade_conn)
        riskguard_module.get_connection_pair = lambda: pair
        riskguard_module.get_connection = lambda: pair.trade_conn  # compat alias

The one-line change per test is: replace `fake_conn` → `fake_connection_pair(...)`.
"""

from __future__ import annotations

import sqlite3
from typing import Optional


def fake_connection_pair(
    world_conn: Optional[sqlite3.Connection] = None,
    trade_conn: Optional[sqlite3.Connection] = None,
):
    """Construct a fake ConnectionPair for test monkeypatching.

    Accepts optional world_conn and trade_conn.
    Falls back to in-memory SQLite connections for any not supplied.

    Returns a ConnectionPair-compatible object (duck-typed, no import
    of ConnectionPair itself so this helper has no prod-code dependencies).
    """
    if world_conn is None:
        world_conn = _make_in_memory_world_conn()
    if trade_conn is None:
        trade_conn = _make_in_memory_trade_conn()

    return _FakeConnectionPair(trade_conn=trade_conn, world_conn=world_conn)


class _FakeConnectionPair:
    """Duck-typed ConnectionPair for tests. Matches ConnectionPair attribute contract."""

    def __init__(self, trade_conn: sqlite3.Connection, world_conn: sqlite3.Connection):
        self.trade_conn = trade_conn
        self.world_conn = world_conn

    def close(self) -> None:
        for conn in (self.trade_conn, self.world_conn):
            try:
                conn.close()
            except Exception:
                pass


def _make_in_memory_world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Minimal world schema for tests that query world tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settlements (
            city TEXT, target_date TEXT, settlement_value REAL,
            outcome TEXT, source TEXT, authority TEXT,
            data_version TEXT, settled_at TEXT,
            range_label TEXT, winning_bin TEXT
        );
        CREATE TABLE IF NOT EXISTS observation_instants_v2 (
            city TEXT, target_date TEXT, max_temp_c REAL, min_temp_c REAL,
            source TEXT, authority TEXT, data_version TEXT, utc_timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS forecasts (
            city TEXT, target_date TEXT, source TEXT,
            forecast_basis_date TEXT, forecast_issue_time TEXT,
            lead_days INTEGER, forecast_high REAL, forecast_low REAL,
            temp_unit TEXT, authority_tier TEXT, data_source_version TEXT,
            retrieved_at TEXT
        );
    """)
    conn.commit()
    return conn


def _make_in_memory_trade_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
