# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Coverage for scripts/migrations/202607_add_venue_commands_q_version.py.
# Reuse: Run when the venue_commands q_version migration or submit schema gate changes.
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "migrations"
    / "202607_add_venue_commands_q_version.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_202607_add_venue_commands_q_version",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _create_legacy_live_venue_commands(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            envelope_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_kind TEXT NOT NULL,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            venue_order_id TEXT,
            state TEXT NOT NULL,
            last_event_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            review_required_reason TEXT
        )
        """
    )


def test_adds_nullable_q_version_to_existing_venue_commands() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL
        )
        """
    )

    _load_migration().up(conn)

    assert "q_version" in _columns(conn, "venue_commands")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='venue_commands'"
    ).fetchone()
    assert "q_version TEXT" in row[0]


def test_existing_q_version_is_idempotent_noop() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            q_version TEXT
        )
        """
    )

    _load_migration().up(conn)
    _load_migration().up(conn)

    assert _columns(conn, "venue_commands").count("q_version") == 1


def test_absent_venue_commands_table_is_noop() -> None:
    conn = sqlite3.connect(":memory:")

    _load_migration().up(conn)

    table_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='venue_commands'"
    ).fetchone()[0]
    assert table_count == 0


def test_init_schema_adds_q_version_to_legacy_venue_commands() -> None:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    _create_legacy_live_venue_commands(conn)

    init_schema(conn)

    assert "q_version" in _columns(conn, "venue_commands")


def test_trade_only_init_adds_q_version_to_legacy_venue_commands() -> None:
    from src.state.db import init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    _create_legacy_live_venue_commands(conn)

    init_schema_trade_only(conn)

    assert "q_version" in _columns(conn, "venue_commands")
