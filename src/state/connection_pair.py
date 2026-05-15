# Lifecycle: created=2026-04-30; last_reviewed=2026-05-14; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
#   K1 followup: docs/operations/task_2026-05-14_k1_followups/PLAN.md §1.3 (REV 4)
"""ConnectionPair / ConnectionTriple / TypedConnection — K1-aware DB connection holders.

K1 split (2026-05-11) added zeus-forecasts.db as a second physical DB.
P1 (2026-05-14) introduces:
  - TypedConnection: dataclass wrapper for sqlite3.Connection with db_identity tag
  - WorldConnection / ForecastsConnection / TradeConnection: typed aliases
  - ConnectionTriple: extends ConnectionPair with forecasts_conn slot

Usage (new):
    from src.state.connection_pair import (
        TypedConnection, WorldConnection, ForecastsConnection,
        ConnectionTriple, get_connection_triple,
    )

Usage (legacy ConnectionPair — unchanged, backward-compat):
    from src.state.connection_pair import ConnectionPair, get_connection_pair

TypedConnection.raw_factory assignment:
    `conn.row_factory = sqlite3.Row` on a TypedConnection routes the assignment
    to the underlying raw connection. The `raw` and `db_identity` fields are
    write-protected; all other attribute assignments delegate to `self.raw`.

P2 wires typed return types into get_world_connection / get_forecasts_connection.
P3 migrates callsites and retires ConnectionPair → ConnectionTriple.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from src.state.table_registry import DBIdentity


# ---------------------------------------------------------------------------
# TypedConnection (PLAN §1.3 / ARCHITECT D4)
# ---------------------------------------------------------------------------

@dataclass
class TypedConnection:
    """sqlite3.Connection wrapper with db_identity tag for static enforcement.

    Pass-through methods (.execute, .executemany, .commit, .cursor, .close,
    .executescript) preserve compatibility with legacy callsites. Attribute
    access for anything other than `raw` and `db_identity` is forwarded to
    `self.raw` so that conn.row_factory = sqlite3.Row, conn.isolation_level,
    etc. continue to work unchanged.

    The `raw` and `db_identity` fields are write-protected after __post_init__:
    attempting to reassign them raises AttributeError.

    P1 note: existing get_world_connection / get_forecasts_connection / get_trade_connection
    return raw sqlite3.Connection (unchanged). TypedConnection.wrap(raw, identity)
    is the factory for new typed callsites. P2 swaps factory return types.
    """
    raw: sqlite3.Connection
    db_identity: DBIdentity

    _PROTECTED: frozenset[str] = field(
        default=frozenset({"raw", "db_identity"}), init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        # Validate db_identity type at construction time.
        if not isinstance(self.db_identity, DBIdentity):
            raise TypeError(
                f"TypedConnection db_identity must be DBIdentity, got {type(self.db_identity)}"
            )

    def __setattr__(self, name: str, value: object) -> None:
        # Allow initial assignment (during dataclass __init__).
        if name in ("raw", "db_identity") and hasattr(self, name):
            raise AttributeError(
                f"TypedConnection.{name} is write-protected after construction. "
                "Use TypedConnection.wrap() to create a new instance."
            )
        # For all other attributes, delegate to raw connection.
        if name not in ("raw", "db_identity", "_PROTECTED") and hasattr(self, "raw"):
            setattr(self.raw, name, value)
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> object:
        # Delegate unknown attributes to the underlying connection.
        try:
            return getattr(object.__getattribute__(self, "raw"), name)
        except AttributeError:
            raise AttributeError(
                f"TypedConnection has no attribute '{name}' "
                f"(db_identity={object.__getattribute__(self, 'db_identity')})"
            ) from None

    # Explicit pass-through methods (per PLAN §1.3)
    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        return self.raw.execute(sql, parameters)  # type: ignore[arg-type]

    def executemany(self, sql: str, parameters: object, /) -> sqlite3.Cursor:
        return self.raw.executemany(sql, parameters)  # type: ignore[arg-type]

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        return self.raw.executescript(sql_script)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def cursor(self) -> sqlite3.Cursor:
        return self.raw.cursor()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> "TypedConnection":
        self.raw.__enter__()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool | None:
        return self.raw.__exit__(exc_type, exc_val, exc_tb)  # type: ignore[return-value]

    @classmethod
    def wrap(cls, raw: sqlite3.Connection, db_identity: DBIdentity) -> "TypedConnection":
        """Factory: wrap an existing raw connection with a db_identity tag."""
        return cls(raw=raw, db_identity=db_identity)


# ---------------------------------------------------------------------------
# Typed aliases (per PLAN §1.3)
# ---------------------------------------------------------------------------

class WorldConnection(TypedConnection):
    """TypedConnection pinned to DBIdentity.WORLD.

    __post_init__ validates db_identity; construct via WorldConnection.wrap(raw).
    """
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.db_identity != DBIdentity.WORLD:
            raise TypeError(
                f"WorldConnection requires db_identity=DBIdentity.WORLD, "
                f"got {self.db_identity}"
            )

    @classmethod
    def wrap(cls, raw: sqlite3.Connection) -> "WorldConnection":  # type: ignore[override]
        return cls(raw=raw, db_identity=DBIdentity.WORLD)


class ForecastsConnection(TypedConnection):
    """TypedConnection pinned to DBIdentity.FORECASTS."""
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.db_identity != DBIdentity.FORECASTS:
            raise TypeError(
                f"ForecastsConnection requires db_identity=DBIdentity.FORECASTS, "
                f"got {self.db_identity}"
            )

    @classmethod
    def wrap(cls, raw: sqlite3.Connection) -> "ForecastsConnection":  # type: ignore[override]
        return cls(raw=raw, db_identity=DBIdentity.FORECASTS)


class TradeConnection(TypedConnection):
    """TypedConnection pinned to DBIdentity.TRADE."""
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.db_identity != DBIdentity.TRADE:
            raise TypeError(
                f"TradeConnection requires db_identity=DBIdentity.TRADE, "
                f"got {self.db_identity}"
            )

    @classmethod
    def wrap(cls, raw: sqlite3.Connection) -> "TradeConnection":  # type: ignore[override]
        return cls(raw=raw, db_identity=DBIdentity.TRADE)


# ---------------------------------------------------------------------------
# ConnectionPair (legacy — backward compat, unchanged)
# ---------------------------------------------------------------------------

@dataclass
class ConnectionPair:
    """Holds separate trade (RW) and world (RO for trading lane) connections.

    Legacy class. New code should use ConnectionTriple (K1-aware, includes
    forecasts_conn). world_view/ was retired in P3 (K1 followups 2026-05-14).

    DO NOT use ATTACH DATABASE on either connection — that is the anti-pattern
    this class replaces. Cross-DB reads go through registry-typed accessors.

    Caller is responsible for closing both connections when done.
    """
    trade_conn: sqlite3.Connection
    world_conn: sqlite3.Connection

    def close(self) -> None:
        """Close both connections."""
        _safe_close(self.trade_conn)
        _safe_close(self.world_conn)


# ---------------------------------------------------------------------------
# ConnectionTriple (K1-aware — three-slot: trade + world + forecasts)
# ---------------------------------------------------------------------------

@dataclass
class ConnectionTriple:
    """Holds three connections: trade (RW), world (RW), and forecasts (RW).

    K1-aware replacement for ConnectionPair. Added forecasts_conn slot for
    zeus-forecasts.db (K1 split 2026-05-11). Registry-derived: each slot's
    db_identity maps to the canonical table set in db_table_ownership.yaml.

    Cross-DB writes (e.g., observations → forecasts_conn, data_coverage →
    world_conn) must use get_forecasts_connection_with_world SAVEPOINT (ATTACH
    atomicity), not two-independent-connection commits (INV-37).

    Caller is responsible for closing all three connections when done.
    """
    trade_conn: sqlite3.Connection
    world_conn: sqlite3.Connection
    forecasts_conn: sqlite3.Connection

    def close(self) -> None:
        """Close all three connections."""
        _safe_close(self.trade_conn)
        _safe_close(self.world_conn)
        _safe_close(self.forecasts_conn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_close(conn: Optional[sqlite3.Connection]) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def get_connection_pair() -> ConnectionPair:
    """Return a ConnectionPair for the trading lane (legacy API).

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


def get_connection_triple() -> ConnectionTriple:
    """Return a ConnectionTriple for K1-aware access to all three DBs.

    - trade_conn: RW connection to zeus_trades.db
    - world_conn: RW connection to zeus-world.db
    - forecasts_conn: RW connection to zeus-forecasts.db

    Caller is responsible for closing all three connections.
    For cross-DB writes (observations + data_coverage), use
    get_forecasts_connection_with_world() instead of this triple directly.
    """
    from src.state.db import get_forecasts_connection, get_trade_connection, get_world_connection
    return ConnectionTriple(
        trade_conn=get_trade_connection(),
        world_conn=get_world_connection(),
        forecasts_conn=get_forecasts_connection(),
    )
