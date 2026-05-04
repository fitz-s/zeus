# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/PROPOSALS_2026-05-04.md P2 — coverage
#                  for src/state/schema_introspection.py.
"""Unit tests for ``src.state.schema_introspection.has_columns``.

These pin the four behavioral cases that callers depend on:
  * all columns present → True
  * any column missing → False
  * table missing → False (PRAGMA returns empty rows, not an error,
    so this case still goes through the column-set check)
  * malformed table name (e.g., raises ProgrammingError on bad
    pragma) → False (caller falls back to legacy form)
  * empty cols arg → True (vacuous)
  * attached-DB form with prefix
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.schema_introspection import has_columns


def _make_table(conn: sqlite3.Connection, name: str, *cols: str) -> None:
    coldef = ", ".join(f"{c} TEXT" for c in cols)
    conn.execute(f"CREATE TABLE {name} ({coldef})")


def test_all_columns_present_returns_true():
    conn = sqlite3.connect(":memory:")
    _make_table(conn, "t", "a", "b", "c")
    assert has_columns(conn, "t", "a", "b", "c") is True
    assert has_columns(conn, "t", "a") is True
    assert has_columns(conn, "t", "b", "c") is True


def test_any_column_missing_returns_false():
    conn = sqlite3.connect(":memory:")
    _make_table(conn, "t", "a", "b")
    assert has_columns(conn, "t", "a", "b", "c") is False
    assert has_columns(conn, "t", "z") is False


def test_table_missing_returns_false():
    conn = sqlite3.connect(":memory:")
    # No table created.
    assert has_columns(conn, "nonexistent", "a") is False


def test_no_cols_arg_returns_true():
    """Vacuous case: 'has every column in []' is trivially True.

    Lets callers chain has_columns through generators without a None
    guard.
    """
    conn = sqlite3.connect(":memory:")
    _make_table(conn, "t", "a")
    assert has_columns(conn, "t") is True


def test_attached_db_form():
    conn = sqlite3.connect(":memory:")
    aux = sqlite3.connect(":memory:")
    aux.execute("CREATE TABLE t (a TEXT, b TEXT)")
    aux.commit()
    # Attach aux to conn as 'side'.
    # SQLite ATTACH requires a real file path or :memory:; we use a temp file
    # because :memory: in ATTACH creates a fresh empty DB, not the existing one.
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        on_disk = sqlite3.connect(path)
        on_disk.execute("CREATE TABLE t (a TEXT, b TEXT)")
        on_disk.commit()
        on_disk.close()
        conn.execute(f"ATTACH DATABASE '{path}' AS side")
        assert has_columns(conn, "t", "a", "b", attached="side") is True
        assert has_columns(conn, "t", "a", "z", attached="side") is False
    finally:
        os.unlink(path)


def test_malformed_pragma_returns_false():
    """Bad table name — sqlite3 either returns empty rows or raises.

    Either way our wrapper must return False, never propagate.
    """
    conn = sqlite3.connect(":memory:")
    # Bad attached-DB name → sqlite raises.
    assert has_columns(conn, "t", "a", attached="nonexistent_db") is False


def test_store_helpers_use_has_columns():
    """Structural assert: the calibration store wrappers route through
    has_columns rather than re-implementing PRAGMA inline.  Locks the
    P2 refactor so a future regression that re-inlines the check fails
    CI.
    """
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src" / "calibration" / "store.py"
    ).read_text(encoding="utf-8")
    assert "from src.state.schema_introspection import has_columns" in src
    assert "has_columns(" in src
