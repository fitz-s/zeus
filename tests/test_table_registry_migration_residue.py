import sqlite3

from src.state.table_registry import (
    DBIdentity,
    _drop_known_empty_migration_residue,
)


def test_table_registry_drops_empty_decision_events_new_residue() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decision_events (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE decision_events_new (id INTEGER PRIMARY KEY)")

    live_tables = frozenset({"decision_events", "decision_events_new"})
    cleaned = _drop_known_empty_migration_residue(conn, DBIdentity.WORLD, live_tables)

    assert cleaned == frozenset({"decision_events"})
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == {"decision_events"}


def test_table_registry_keeps_nonempty_decision_events_new_residue() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decision_events (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE decision_events_new (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO decision_events_new (id) VALUES (1)")

    live_tables = frozenset({"decision_events", "decision_events_new"})
    cleaned = _drop_known_empty_migration_residue(conn, DBIdentity.WORLD, live_tables)

    assert cleaned == live_tables
    assert conn.execute("SELECT COUNT(*) FROM decision_events_new").fetchone()[0] == 1
