# Lifecycle: created=2026-07-20; last_reviewed=2026-07-22; last_reused=never
# Purpose: detect the dangling-foreign-key class (the defect that froze trade_decisions since 2026-07-02).
# Reuse: run on any schema/migration change; promote to a boot gate once the three canonical DBs are clean.
"""Antibody for the dangling-foreign-key class (the defect that froze trade_decisions).

A column-level FK whose parent table does not exist in the SAME schema makes every
INSERT/UPDATE on the child table fail at statement-compile time under foreign_keys=ON
(`no such table: main.<parent>`), silently freezing the table. trade_decisions was frozen
this way from 2026-07-02 after the K1 split dropped its local ensemble_snapshots parent.

`find_dangling_foreign_keys(conn)` scans every FK edge in a schema and returns the ones
whose parent table is absent — the reusable check behind this antibody. Implementation lives
in scripts/ops/db_integrity_checks.py (a production module, not tests/) so an operator
deployment that omits tests/ does not lose the gate. It is cheap (sqlite_master + PRAGMA
foreign_key_list only) and belongs as a boot/CI gate once the currently-known live instances
(below) are cleared to zero.

KNOWN LIVE INSTANCES at 2026-07-21 (found by a read-only scan of the three canonical DBs;
this list must shrink to empty, never grow):
  - zeus_trades.db  trade_decisions.forecast_snapshot_id -> ensemble_snapshots  (W0-a fixes)
  - zeus-world.db   trade_decisions.forecast_snapshot_id -> ensemble_snapshots  (world ghost copy)
  - zeus-world.db   regret_decompositions.experiment_id  -> shadow_experiments  (analysis-tier,
        0 rows; parent is itself a removed shadow-named table — drop the dead FK or the table)
"""
from __future__ import annotations

import sqlite3

from scripts.ops.db_integrity_checks import find_dangling_foreign_keys


def _fixture(create_sqls: list[str]) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys=OFF")  # allow creating a table with a missing FK parent
    for sql in create_sqls:
        c.execute(sql)
    return c


def test_clean_schema_has_no_dangling_fk():
    c = _fixture([
        "CREATE TABLE parent (id INTEGER PRIMARY KEY)",
        "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent(id))",
    ])
    assert find_dangling_foreign_keys(c) == []


def test_dangling_fk_is_detected():
    """The exact trade_decisions shape: a child with an inline FK to a dropped parent."""
    c = _fixture([
        "CREATE TABLE trade_decisions ("
        "  trade_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  forecast_snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id))",
    ])
    assert find_dangling_foreign_keys(c) == [
        ("trade_decisions", "forecast_snapshot_id", "ensemble_snapshots")]


def test_dangling_fk_freezes_inserts_under_foreign_keys_on():
    """Prove the failure mode the antibody guards: with foreign_keys=ON, an insert into a
    table with a dangling FK fails at compile with `no such table`."""
    c = _fixture([
        "CREATE TABLE td (id INTEGER PRIMARY KEY, fk INTEGER REFERENCES gone(x))",
    ])
    c.execute("PRAGMA foreign_keys=ON")
    try:
        c.execute("INSERT INTO td (id, fk) VALUES (1, NULL)")
        raised = None
    except sqlite3.OperationalError as e:
        raised = str(e)
    assert raised is not None and "no such table" in raised and "gone" in raised


def test_rebuilt_table_without_fk_is_clean():
    """After the W0-a-style rebuild (FK clause removed) the antibody passes and inserts work."""
    c = _fixture([
        "CREATE TABLE td (id INTEGER PRIMARY KEY, fk INTEGER)",  # no REFERENCES
    ])
    assert find_dangling_foreign_keys(c) == []
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("INSERT INTO td (id, fk) VALUES (1, 5)")  # succeeds — no dangling parent
    assert c.execute("SELECT count(*) FROM td").fetchone()[0] == 1


def test_multiple_dangling_edges_all_reported():
    c = _fixture([
        "CREATE TABLE a (id INTEGER PRIMARY KEY, x INTEGER REFERENCES missing_a(id))",
        "CREATE TABLE b (id INTEGER PRIMARY KEY, y INTEGER REFERENCES missing_b(id))",
        "CREATE TABLE ok (id INTEGER PRIMARY KEY)",
    ])
    found = set(find_dangling_foreign_keys(c))
    assert found == {("a", "x", "missing_a"), ("b", "y", "missing_b")}
