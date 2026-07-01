"""Owner-Routed Writes — the enforcement teeth of the single-ownership kernel (src/state/domains.py).

Created: 2026-06-30
Authority basis: db-root-mechanism design (wf_4acdc7d5); atlas §6. The root fix for the 19 registry
inversions: a table's DB is a PROPERTY OF THE TABLE (domains.owner_domain), never of the caller's
connection. `assert_owner_conn` generalizes db.py:7832 `_is_verified_trade_connection` from trade-only to
every domain — a write to a table on a connection whose MAIN file is not the table's owner (and the owner
is not ATTACHed) fail-closes instead of silently writing a ghost copy.

Comparison is by DB FILENAME (not full path) so it is robust across the live tree and dev worktrees AND
catches the hyphen/underscore naming-schism decoys (a conn on zeus-trades.db is NOT the owner zeus_trades.db).

This module has ZERO runtime effect until the write helpers call it (migration P3 wires the 8 unbound
write sites one at a time). Importing it is side-effect-free.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.state.domains import Domain, owner_domain

_DB_FILENAME: dict[Domain, str] = {
    Domain.WORLD: "zeus-world.db",
    Domain.FORECASTS: "zeus-forecasts.db",
    Domain.TRADE: "zeus_trades.db",
    Domain.RISK_STATE: "risk_state.db",
    Domain.BACKTEST: "zeus_backtest.db",
}


class WrongDomainWrite(RuntimeError):
    """A write was attempted against a connection that cannot reach the table's owning DB."""


def owner_db_filename(table_name: str) -> str | None:
    """The DB filename that owns `table_name`, or None if the kernel does not own it."""
    d = owner_domain(table_name)
    return _DB_FILENAME.get(d) if d is not None else None


def _database_files(conn: sqlite3.Connection) -> tuple[str | None, set[str]]:
    """Return (main filename, {all attached filenames}) for `conn` via PRAGMA database_list."""
    main: str | None = None
    attached: set[str] = set()
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row[1] if not isinstance(row, sqlite3.Row) else row["name"]
        path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
        if not path:
            continue
        fn = Path(str(path)).name
        attached.add(fn)
        if name == "main":
            main = fn
    return main, attached


def assert_owner_conn(conn: sqlite3.Connection, table_name: str) -> None:
    """Fail-closed guard: raise WrongDomainWrite if a write to `table_name` on `conn` could land in a
    non-owning file.

    Safe iff the connection's MAIN file IS the owner's file (a bare `INSERT INTO table` lands correctly)
    OR the owner's file is ATTACHed on the connection (the caller must then schema-qualify to it).
    Tables the kernel does not own (e.g. temp/test tables) fail-open — the guard only constrains the
    live ownership set declared in domains.py.
    """
    want = owner_db_filename(table_name)
    if want is None:
        return
    main, attached = _database_files(conn)
    if main == want or want in attached:
        return
    raise WrongDomainWrite(
        f"refusing write to {table_name!r}: owner is {want} but the connection is rooted at "
        f"{main or '?'} with {want} not ATTACHed — a bare write would silently hit a ghost copy. "
        f"Self-open the owner's factory or ATTACH+schema-qualify (see owner_qualified_name)."
    )


def owner_qualified_name(conn: sqlite3.Connection, table_name: str) -> str:
    """Return the write target for `table_name` on `conn`: bare `table` when MAIN is the owner, else
    `alias.table` for the ATTACHed owner. Raises WrongDomainWrite (via assert_owner_conn) if unreachable.

    The alias is resolved from PRAGMA database_list by matching the owner filename, so a caller can write
    `conn.execute(f"INSERT INTO {owner_qualified_name(conn, 'market_events')} ...")` and land in the owner
    regardless of which DB the conn is rooted in.
    """
    assert_owner_conn(conn, table_name)
    want = owner_db_filename(table_name)
    if want is None:
        return table_name
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row[1] if not isinstance(row, sqlite3.Row) else row["name"]
        path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
        if path and Path(str(path)).name == want:
            return table_name if name == "main" else f"{name}.{table_name}"
    return table_name
