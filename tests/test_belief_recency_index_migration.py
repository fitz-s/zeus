# Created: 2026-09-20
# Last reused/audited: 2026-09-20
# Authority basis: hourly_capital_gains_improvement_loop.md exact belief-recency index plan
"""Acceptance tests for the world probability-trace belief recency index."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "scripts" / "migrations" / "202609_belief_recency_index.py"
INDEX_NAME = "idx_probability_trace_belief_recency_cover"
PREFIX = "edli_belief:"
UPPER = "edli_belief;"

_LATEST_TRACE_SQL = """
WITH latest_trace AS MATERIALIZED (
    SELECT trace_id, recorded_at
      FROM probability_trace_fact
     WHERE decision_id >= ?
       AND decision_id < ?
     ORDER BY recorded_at DESC, trace_id DESC
     LIMIT ?
)
SELECT p.trace_id, p.decision_id, p.recorded_at, p.city
  FROM latest_trace latest
  JOIN probability_trace_fact p
    ON p.trace_id = latest.trace_id
 ORDER BY latest.recorded_at DESC, latest.trace_id DESC
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_202609_belief_recency_index", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_fixture(db_path: Path) -> tuple[sqlite3.Connection, list[tuple[str, str, str, str]]]:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            recorded_at TEXT NOT NULL,
            city TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE unrelated_fact (fact_id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO unrelated_fact VALUES (1, 'preserve-me')")

    rows: list[tuple[str, str, str, str]] = []
    for i in range(256):
        # Keep enough rows for ANALYZE to prefer the order-preserving covering
        # index while retaining ties and exact prefix-boundary sentinels below.
        trace_id = f"trace-{i:04d}"
        decision_id = f"{PREFIX}{i:04d}"
        recorded_at = f"2026-09-20T00:{i // 60:02d}:{i % 60:02d}"
        rows.append((trace_id, decision_id, recorded_at, "Fixture"))
    rows.extend(
        [
            ("trace-tie-a", f"{PREFIX}tie-a", "2026-09-20T05:00:00", "Tie"),
            ("trace-tie-z", f"{PREFIX}tie-z", "2026-09-20T05:00:00", "Tie"),
            ("trace-lower", PREFIX, "2026-09-20T06:00:00", "Boundary"),
            ("trace-upper", UPPER, "2026-09-20T07:00:00", "Boundary"),
            ("trace-outside", "edli_beliefx:0000", "2026-09-20T08:00:00", "Other"),
        ]
    )
    conn.executemany("INSERT INTO probability_trace_fact VALUES (?,?,?,?)", rows)
    conn.commit()
    return conn, rows


def _query_ids(conn: sqlite3.Connection, limit: int) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(_LATEST_TRACE_SQL, (PREFIX, UPPER, limit)).fetchall()
    ]


def _query_plan(conn: sqlite3.Connection) -> str:
    return "\n".join(
        str(row[3])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN " + _LATEST_TRACE_SQL, (PREFIX, UPPER, 5)
        ).fetchall()
    )


def test_migration_preserves_order_boundaries_and_unrelated_data(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn, rows = _create_fixture(db_path)
    try:
        expected_by_limit = {
            limit: [
                trace_id
                for trace_id, decision_id, recorded_at, _city in sorted(
                    (row for row in rows if PREFIX <= row[1] < UPPER),
                    key=lambda row: (row[2], row[0]),
                    reverse=True,
                )[:limit]
            ]
            for limit in (1, 5, 1000)
        }
        before = {limit: _query_ids(conn, limit) for limit in expected_by_limit}
        unrelated_before = conn.execute(
            "SELECT fact_id, value FROM unrelated_fact ORDER BY fact_id"
        ).fetchall()
        assert before == expected_by_limit

        _load_migration().up(conn)

        after = {limit: _query_ids(conn, limit) for limit in expected_by_limit}
        assert after == before
        assert conn.execute("SELECT COUNT(*) FROM probability_trace_fact").fetchone()[0] == len(rows)
        assert conn.execute(
            "SELECT fact_id, value FROM unrelated_fact ORDER BY fact_id"
        ).fetchall() == unrelated_before
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone() is not None
    finally:
        conn.close()


def test_migration_plan_uses_covering_index_without_temp_sort(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn, _rows = _create_fixture(db_path)
    try:
        before_plan = _query_plan(conn)
        assert "USE TEMP B-TREE FOR ORDER BY" in before_plan

        _load_migration().up(conn)

        after_plan = _query_plan(conn)
        assert f"COVERING INDEX {INDEX_NAME}" in after_plan
        assert "TEMP B-TREE" not in after_plan
    finally:
        conn.close()


def test_migration_is_idempotent_and_fresh_schema_declares_index(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn, _rows = _create_fixture(db_path)
    try:
        migration = _load_migration()
        migration.up(conn)
        first_index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone()[0]
        first_stats = conn.execute(
            "SELECT tbl, idx, stat FROM sqlite_stat1 WHERE idx=?", (INDEX_NAME,)
        ).fetchone()
        migration.up(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone()[0] == first_index_sql
        assert conn.execute(
            "SELECT tbl, idx, stat FROM sqlite_stat1 WHERE idx=?", (INDEX_NAME,)
        ).fetchone() == first_stats
    finally:
        conn.close()

    from src.state.db import init_schema

    fresh = sqlite3.connect(":memory:")
    try:
        init_schema(fresh)
        assert fresh.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone() is not None
    finally:
        fresh.close()


def test_migration_runner_applies_world_target_and_records_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn, _rows = _create_fixture(db_path)
    try:
        from scripts.migrations import apply_migrations

        applied = apply_migrations(
            conn,
            target="202609_belief_recency_index",
            db_identity="world",
        )
        assert applied == ["202609_belief_recency_index"]
        assert conn.execute(
            "SELECT name FROM _migrations_applied WHERE name=?",
            ("202609_belief_recency_index",),
        ).fetchone() is not None
    finally:
        conn.close()


def test_runner_refuses_missing_canonical_table_without_applied_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    try:
        from scripts.migrations import apply_migrations

        with pytest.raises(RuntimeError, match="probability_trace_fact is missing"):
            apply_migrations(
                conn,
                target="202609_belief_recency_index",
                db_identity="world",
            )
        assert conn.execute(
            "SELECT 1 FROM _migrations_applied WHERE name=?",
            ("202609_belief_recency_index",),
        ).fetchone() is None
    finally:
        conn.close()


def test_runner_refuses_wrong_existing_index_shape_without_applied_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn, _rows = _create_fixture(db_path)
    try:
        conn.execute(
            "CREATE INDEX idx_probability_trace_belief_recency_cover "
            "ON probability_trace_fact(decision_id)"
        )
        conn.commit()
        from scripts.migrations import apply_migrations

        with pytest.raises(RuntimeError, match="unexpected key columns"):
            apply_migrations(
                conn,
                target="202609_belief_recency_index",
                db_identity="world",
            )
        assert conn.execute(
            "SELECT 1 FROM _migrations_applied WHERE name=?",
            ("202609_belief_recency_index",),
        ).fetchone() is None
        key_columns = conn.execute(
            "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
            ("idx_probability_trace_belief_recency_cover",),
        ).fetchall()
        assert [row[0] for row in key_columns] == ["decision_id"]
    finally:
        conn.close()


def test_runner_accepts_exact_existing_index_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "world.db"
    conn, _rows = _create_fixture(db_path)
    try:
        conn.execute(
            "CREATE INDEX idx_probability_trace_belief_recency_cover "
            "ON probability_trace_fact(recorded_at DESC, trace_id DESC, decision_id ASC)"
        )
        conn.commit()
        from scripts.migrations import apply_migrations

        applied = apply_migrations(
            conn,
            target="202609_belief_recency_index",
            db_identity="world",
        )
        assert applied == ["202609_belief_recency_index"]
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
            (INDEX_NAME,),
        ).fetchone()[0] == 1
    finally:
        conn.close()
