"""Durable USDC.e ↔ pUSD wrap/unwrap command states for R3 Z4.

Z4 models request/tx/confirmation/failure state only. It does not submit live
chain transactions; later operator-gated phases may attach a submitter.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

WRAP_UNWRAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS wrap_unwrap_commands (
  command_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('WRAP','UNWRAP')),
  amount_micro INTEGER NOT NULL,
  tx_hash TEXT,
  block_number INTEGER,
  confirmation_count INTEGER DEFAULT 0,
  requested_at TEXT NOT NULL,
  terminal_at TEXT,
  error_payload TEXT
);

CREATE TABLE IF NOT EXISTS wrap_unwrap_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL REFERENCES wrap_unwrap_commands(command_id),
  event_type TEXT NOT NULL,
  payload_json TEXT,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class WrapUnwrapState(str, Enum):
    WRAP_REQUESTED = "WRAP_REQUESTED"
    WRAP_TX_HASHED = "WRAP_TX_HASHED"
    WRAP_CONFIRMED = "WRAP_CONFIRMED"
    WRAP_FAILED = "WRAP_FAILED"
    UNWRAP_REQUESTED = "UNWRAP_REQUESTED"
    UNWRAP_TX_HASHED = "UNWRAP_TX_HASHED"
    UNWRAP_CONFIRMED = "UNWRAP_CONFIRMED"
    UNWRAP_FAILED = "UNWRAP_FAILED"


def init_wrap_unwrap_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(WRAP_UNWRAP_SCHEMA)


def request_wrap(amount_micro: int, conn: sqlite3.Connection | None = None) -> str:
    return _request("WRAP", amount_micro, conn=conn)


def request_unwrap(amount_micro: int, conn: sqlite3.Connection | None = None) -> str:
    return _request("UNWRAP", amount_micro, conn=conn)


def mark_tx_hashed(
    command_id: str,
    tx_hash: str,
    *,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        tx_state={"WRAP": WrapUnwrapState.WRAP_TX_HASHED, "UNWRAP": WrapUnwrapState.UNWRAP_TX_HASHED},
        conn=conn,
        tx_hash=tx_hash,
        block_number=block_number,
    )


def confirm_command(
    command_id: str,
    *,
    confirmation_count: int,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        tx_state={"WRAP": WrapUnwrapState.WRAP_CONFIRMED, "UNWRAP": WrapUnwrapState.UNWRAP_CONFIRMED},
        conn=conn,
        block_number=block_number,
        confirmation_count=confirmation_count,
        terminal=True,
    )


def fail_command(command_id: str, *, error_payload: dict[str, Any], conn: sqlite3.Connection | None = None) -> None:
    _transition(
        command_id,
        tx_state={"WRAP": WrapUnwrapState.WRAP_FAILED, "UNWRAP": WrapUnwrapState.UNWRAP_FAILED},
        conn=conn,
        error_payload=json.dumps(error_payload, sort_keys=True),
        terminal=True,
    )


def get_command(command_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT * FROM wrap_unwrap_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    return dict(row)


def reconcile_pending_wraps_against_chain(web3) -> None:
    """Placeholder seam for later chain receipt reconciliation.

    Z4 intentionally does not perform live chain reads/writes here. R1/G1 can
    wire a concrete reconciler once operator gates and chain semantics are set.
    """

    return None


def _request(direction: str, amount_micro: int, *, conn: sqlite3.Connection | None) -> str:
    if amount_micro <= 0:
        raise ValueError("amount_micro must be positive")
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_trade_connection_with_world

        conn = get_trade_connection_with_world()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    command_id = uuid.uuid4().hex
    state = WrapUnwrapState.WRAP_REQUESTED if direction == "WRAP" else WrapUnwrapState.UNWRAP_REQUESTED
    requested_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """
            INSERT INTO wrap_unwrap_commands (
              command_id, state, direction, amount_micro, requested_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (command_id, state.value, direction, int(amount_micro), requested_at),
        )
        _append_event(conn, command_id, state.value, {"amount_micro": int(amount_micro)})
        if own_conn:
            conn.commit()
        return command_id
    finally:
        if own_conn:
            conn.close()


def _transition(
    command_id: str,
    *,
    tx_state: dict[str, WrapUnwrapState],
    conn: sqlite3.Connection | None,
    tx_hash: Optional[str] = None,
    block_number: Optional[int] = None,
    confirmation_count: Optional[int] = None,
    error_payload: Optional[str] = None,
    terminal: bool = False,
) -> None:
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_trade_connection_with_world

        conn = get_trade_connection_with_world()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT direction, state FROM wrap_unwrap_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    direction = str(row["direction"])
    new_state = tx_state[direction]
    terminal_at = datetime.now(timezone.utc).isoformat() if terminal else None
    try:
        conn.execute(
            """
            UPDATE wrap_unwrap_commands
               SET state = ?,
                   tx_hash = COALESCE(?, tx_hash),
                   block_number = COALESCE(?, block_number),
                   confirmation_count = COALESCE(?, confirmation_count),
                   terminal_at = COALESCE(?, terminal_at),
                   error_payload = COALESCE(?, error_payload)
             WHERE command_id = ?
            """,
            (
                new_state.value,
                tx_hash,
                block_number,
                confirmation_count,
                terminal_at,
                error_payload,
                command_id,
            ),
        )
        _append_event(
            conn,
            command_id,
            new_state.value,
            {
                "tx_hash": tx_hash,
                "block_number": block_number,
                "confirmation_count": confirmation_count,
                "error_payload": error_payload,
            },
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def _append_event(conn: sqlite3.Connection, command_id: str, event_type: str, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO wrap_unwrap_events (command_id, event_type, payload_json)
        VALUES (?, ?, ?)
        """,
        (command_id, event_type, json.dumps(payload, sort_keys=True)),
    )
