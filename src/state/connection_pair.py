# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
"""ConnectionPair: explicit two-connection holder for trade + world DBs.

Phase 2 replacement for the get_trade_connection_with_world() ATTACH seam.
The ATTACH seam (db.py:66-73) remains as a backward-compat alias during
Phase 2 overlap. It will be deleted in Phase 3 once all 47 callsites migrate.

Usage:
    from src.state.connection_pair import ConnectionPair, get_connection_pair

    pair = get_connection_pair()
    pair.trade_conn  # RW connection to zeus_trades.db
    pair.world_conn  # RO connection to zeus-world.db

    # Old callers that only need trade conn:
    conn = pair.trade_conn
    conn.execute(...)

Migration plan for test monkeypatches:
    Old: cycle_runner_module.get_connection = lambda: fake_trade_with_world_conn
    New: cycle_runner_module.get_connection = lambda: fake_connection_pair(...)
    Helper: from tests.conftest_connection_pair import fake_connection_pair
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class ConnectionPair:
    """Holds separate trade (RW) and world (RO for trading lane) connections.

    DO NOT use ATTACH DATABASE on either connection — that is the anti-pattern
    this class replaces. Cross-DB reads go through src.contracts.world_view.*
    typed accessors using world_conn.

    Caller is responsible for closing both connections when done.
    """
    trade_conn: sqlite3.Connection
    world_conn: sqlite3.Connection

    def close(self) -> None:
        """Close both connections."""
        _safe_close(self.trade_conn)
        _safe_close(self.world_conn)


def _safe_close(conn: Optional[sqlite3.Connection]) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def get_connection_pair() -> ConnectionPair:
    """Return a ConnectionPair for the trading lane.

    - trade_conn: RW connection to zeus_trades.db
    - world_conn: RO-intent connection to zeus-world.db
      (uses standard _connect(), not ?mode=ro URI, to maintain
       WAL read semantics — Phase 3 will enforce URI-level RO)

    Caller is responsible for closing both connections.
    """
    from src.state.db import get_trade_connection, get_world_connection
    return ConnectionPair(
        trade_conn=get_trade_connection(),
        world_conn=get_world_connection(),
    )
