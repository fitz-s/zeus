# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR 6 WAVE_B_PR_3_6_FIELD_MAP.md row 16; pr36_scaffold.md BLOCKING REVISION 5;
#   operator brief 2026-05-19 auto-wrap session; .omc/plans/2026-05-19-auto-wrap-post-redeem.md
"""Durable USDC.e → pUSD wrap command states for autonomous post-redeem wrapping.

State machine shape (WRAP path only; UNWRAP not used in this implementation):
  WRAP_REQUESTED
    → WRAP_APPROVE_TX_HASHED  (ERC20.approve tx in mempool)
    → WRAP_APPROVED           (approve confirmed ≥1 block)
    → WRAP_TX_HASHED          (pUSD.wrap tx in mempool)
    → WRAP_CONFIRMED          (wrap confirmed ≥1 block)  [terminal]
    → WRAP_FAILED             (any step failed, operator action required) [terminal+op]

DB: world (get_world_connection), NOT trades.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# Threshold for enqueuing a wrap intent: minimum USDC.e balance in micro-units.
# Override via AUTO_WRAP_THRESHOLD_MICRO env var. Default $0.10.
_DEFAULT_WRAP_THRESHOLD_MICRO = 100_000

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

# Idempotent ALTER TABLE statements for columns added after initial schema.
_WRAP_SCHEMA_ALTERS = [
    "ALTER TABLE wrap_unwrap_commands ADD COLUMN first_inclusion_block_time TEXT",
    "ALTER TABLE wrap_unwrap_commands ADD COLUMN finality_confirmed_time TEXT",
    # 2026-05-19: approve-then-wrap two-step sequencing column.
    "ALTER TABLE wrap_unwrap_commands ADD COLUMN tx_kind TEXT CHECK(tx_kind IN ('APPROVE','WRAP'))",
]


class WrapUnwrapState(str, Enum):
    WRAP_REQUESTED = "WRAP_REQUESTED"
    WRAP_APPROVE_TX_HASHED = "WRAP_APPROVE_TX_HASHED"
    WRAP_APPROVED = "WRAP_APPROVED"
    WRAP_TX_HASHED = "WRAP_TX_HASHED"
    WRAP_CONFIRMED = "WRAP_CONFIRMED"
    WRAP_FAILED = "WRAP_FAILED"
    UNWRAP_REQUESTED = "UNWRAP_REQUESTED"
    UNWRAP_TX_HASHED = "UNWRAP_TX_HASHED"
    UNWRAP_CONFIRMED = "UNWRAP_CONFIRMED"
    UNWRAP_FAILED = "UNWRAP_FAILED"


_WRAP_TERMINAL_STATES = frozenset({
    WrapUnwrapState.WRAP_CONFIRMED,
    WrapUnwrapState.WRAP_FAILED,
    WrapUnwrapState.UNWRAP_CONFIRMED,
    WrapUnwrapState.UNWRAP_FAILED,
})


def init_wrap_unwrap_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(WRAP_UNWRAP_SCHEMA)
    for stmt in _WRAP_SCHEMA_ALTERS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            # ALTER TABLE ADD COLUMN raises "duplicate column name: <col>" when
            # the column already exists (idempotent schema evolution). Any other
            # OperationalError (I/O failure, locked DB, syntax error in a future
            # stmt) is a real error and must propagate so the caller sees it.
            if "duplicate column name" not in str(exc).lower():
                raise


def enqueue_wrap_if_balance_above_threshold(
    safe_address: str,
    w3: Any,  # web3.Web3
    conn: sqlite3.Connection,
    *,
    threshold_micro: int | None = None,
) -> Optional[str]:
    """Read on-chain USDC.e balance of safe_address; insert WRAP_REQUESTED if:

    1. balance > threshold_micro (default from AUTO_WRAP_THRESHOLD_MICRO env)
    2. No non-terminal WRAP row exists in wrap_unwrap_commands

    Returns command_id if a row was inserted, else None.
    """
    from src.venue.polymarket_v2_adapter import POLYGON_USDCE_ADDRESS

    if threshold_micro is None:
        threshold_micro = int(
            os.environ.get("AUTO_WRAP_THRESHOLD_MICRO", str(_DEFAULT_WRAP_THRESHOLD_MICRO))
        )

    init_wrap_unwrap_schema(conn)

    # Idempotency gate: any non-terminal WRAP row blocks new intent.
    pending_states = tuple(
        s.value for s in WrapUnwrapState
        if s not in _WRAP_TERMINAL_STATES and s.value.startswith("WRAP_")
    )
    placeholders = ",".join("?" * len(pending_states))
    existing = conn.execute(
        f"SELECT command_id FROM wrap_unwrap_commands WHERE state IN ({placeholders}) LIMIT 1",
        pending_states,
    ).fetchone()
    if existing:
        return None

    # On-chain balance read via ERC-20 balanceOf(safe_address).
    balance_micro = _read_usdce_balance(w3, POLYGON_USDCE_ADDRESS, safe_address)
    if balance_micro <= threshold_micro:
        return None

    command_id = uuid.uuid4().hex
    requested_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO wrap_unwrap_commands (
          command_id, state, direction, amount_micro, requested_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (command_id, WrapUnwrapState.WRAP_REQUESTED.value, "WRAP", int(balance_micro), requested_at),
    )
    _append_event(conn, command_id, WrapUnwrapState.WRAP_REQUESTED.value, {
        "balance_micro": int(balance_micro),
        "threshold_micro": threshold_micro,
    })
    return command_id


def mark_wrap_approve_tx_hashed(
    command_id: str,
    tx_hash: str,
    *,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        new_state=WrapUnwrapState.WRAP_APPROVE_TX_HASHED,
        conn=conn,
        tx_hash=tx_hash,
        block_number=block_number,
        tx_kind="APPROVE",
    )


def mark_wrap_approved(
    command_id: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        new_state=WrapUnwrapState.WRAP_APPROVED,
        conn=conn,
    )


def mark_wrap_tx_hashed(
    command_id: str,
    tx_hash: str,
    *,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        new_state=WrapUnwrapState.WRAP_TX_HASHED,
        conn=conn,
        tx_hash=tx_hash,
        block_number=block_number,
        tx_kind="WRAP",
    )


def confirm_wrap(
    command_id: str,
    *,
    confirmation_count: int,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        new_state=WrapUnwrapState.WRAP_CONFIRMED,
        conn=conn,
        block_number=block_number,
        confirmation_count=confirmation_count,
        terminal=True,
    )


def fail_wrap(
    command_id: str,
    *,
    error_payload: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> None:
    _transition(
        command_id,
        new_state=WrapUnwrapState.WRAP_FAILED,
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


def list_pending_wrap_commands(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all non-terminal WRAP rows ordered by requested_at."""
    init_wrap_unwrap_schema(conn)
    pending_states = tuple(
        s.value for s in WrapUnwrapState
        if s not in _WRAP_TERMINAL_STATES and s.value.startswith("WRAP_")
    )
    placeholders = ",".join("?" * len(pending_states))
    rows = conn.execute(
        f"""
        SELECT * FROM wrap_unwrap_commands
         WHERE state IN ({placeholders})
         ORDER BY requested_at, command_id
        """,
        pending_states,
    ).fetchall()
    return [dict(r) for r in rows]


def reconcile_pending_wraps(
    w3: Any,  # web3.Web3
    adapter: Any,  # PolymarketV2Adapter
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Poll WRAP_TX_HASHED rows; advance to CONFIRMED on receipt; call balance refresh.

    Also handles WRAP_APPROVE_TX_HASHED rows → WRAP_APPROVED on receipt.

    Returns list of updated command rows.
    """
    init_wrap_unwrap_schema(conn)
    reconcile_states = (
        WrapUnwrapState.WRAP_APPROVE_TX_HASHED.value,
        WrapUnwrapState.WRAP_TX_HASHED.value,
    )
    rows = conn.execute(
        "SELECT * FROM wrap_unwrap_commands WHERE state IN (?,?) ORDER BY requested_at",
        reconcile_states,
    ).fetchall()
    results = []
    for row in rows:
        row_d = dict(row)
        command_id = row_d["command_id"]
        tx_hash = row_d.get("tx_hash")
        if not tx_hash:
            continue
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            continue
        if receipt is None:
            continue
        block_num = receipt.get("blockNumber") or receipt.get("blockHash") and None
        if hasattr(receipt, "blockNumber"):
            block_num = receipt.blockNumber
        status = receipt.get("status", 1) if isinstance(receipt, dict) else getattr(receipt, "status", 1)
        if status == 0:
            fail_wrap(command_id, error_payload={"reason": "tx_reverted", "tx_hash": tx_hash}, conn=conn)
            results.append(get_command(command_id, conn))
            continue

        current_state = row_d["state"]
        if current_state == WrapUnwrapState.WRAP_APPROVE_TX_HASHED.value:
            mark_wrap_approved(command_id, conn=conn)
        elif current_state == WrapUnwrapState.WRAP_TX_HASHED.value:
            confirm_wrap(
                command_id,
                confirmation_count=1,
                block_number=block_num,
                conn=conn,
            )
            # CLOB ledger refresh on wrap confirmation.
            try:
                try:
                    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                    params = BalanceAllowanceParams(
                        asset_type=AssetType.COLLATERAL,
                        signature_type=adapter.signature_type,
                    )
                except ImportError:
                    # py_clob_client not installed (test env or bare deployment);
                    # call with None — adapter must tolerate None or handle internally.
                    params = None
                adapter.update_balance_allowance(params)
            except Exception as refresh_exc:  # noqa: BLE001
                # Fail-open: wrap is confirmed, balance refresh is best-effort.
                # The operator can re-run adapter.update_balance_allowance manually.
                import logging
                logging.getLogger(__name__).warning(
                    "reconcile_pending_wraps: WRAP_CONFIRMED command_id=%s "
                    "balance_refresh_failed=%s (fail-open, wrap durable)",
                    command_id, refresh_exc,
                )
        results.append(get_command(command_id, conn))
    return results


def _read_usdce_balance(w3: Any, usdce_address: str, safe_address: str) -> int:
    """Read ERC-20 balanceOf(safe_address) on USDC.e contract. Returns micro-units."""
    # balanceOf(address) selector = 0x70a08231
    from eth_utils import to_checksum_address  # type: ignore[import]
    addr_padded = to_checksum_address(safe_address)[2:].lower().zfill(64)
    data = "0x70a08231" + addr_padded
    result = w3.eth.call({"to": to_checksum_address(usdce_address), "data": data})
    return int.from_bytes(result, "big")


def _transition(
    command_id: str,
    *,
    new_state: WrapUnwrapState,
    conn: sqlite3.Connection | None,
    tx_hash: Optional[str] = None,
    block_number: Optional[int] = None,
    confirmation_count: Optional[int] = None,
    error_payload: Optional[str] = None,
    terminal: bool = False,
    tx_kind: Optional[str] = None,
) -> None:
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection

        conn = get_world_connection()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT command_id FROM wrap_unwrap_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    terminal_at = datetime.now(timezone.utc).isoformat() if terminal else None
    _now_utc = datetime.now(timezone.utc).isoformat()
    _first_inclusion = _now_utc if block_number is not None else None
    _finality = _now_utc if (confirmation_count is not None and confirmation_count >= 6) else None
    try:
        conn.execute(
            """
            UPDATE wrap_unwrap_commands
               SET state = ?,
                   tx_hash = COALESCE(?, tx_hash),
                   block_number = COALESCE(?, block_number),
                   confirmation_count = COALESCE(?, confirmation_count),
                   terminal_at = COALESCE(?, terminal_at),
                   error_payload = COALESCE(?, error_payload),
                   first_inclusion_block_time = COALESCE(first_inclusion_block_time, ?),
                   finality_confirmed_time = COALESCE(finality_confirmed_time, ?),
                   tx_kind = COALESCE(?, tx_kind)
             WHERE command_id = ?
            """,
            (
                new_state.value,
                tx_hash,
                block_number,
                confirmation_count,
                terminal_at,
                error_payload,
                _first_inclusion,
                _finality,
                tx_kind,
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
                "tx_kind": tx_kind,
            },
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Backward-compatible aliases for callers written before the 2026-05-19
# state machine expansion (test_collateral_ledger.py etc.).
# These delegate to the new direction-specific functions.
# ---------------------------------------------------------------------------

def request_wrap(amount_micro: int, conn: sqlite3.Connection | None = None) -> str:
    """Backward-compat alias: insert WRAP_REQUESTED row."""
    return _request_wrap_direct(amount_micro, conn=conn)


def _request_wrap_direct(amount_micro: int, *, conn: sqlite3.Connection | None) -> str:
    if amount_micro <= 0:
        raise ValueError("amount_micro must be positive")
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection
        conn = get_world_connection()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    command_id = uuid.uuid4().hex
    requested_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO wrap_unwrap_commands (command_id, state, direction, amount_micro, requested_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (command_id, WrapUnwrapState.WRAP_REQUESTED.value, "WRAP", int(amount_micro), requested_at),
        )
        _append_event(conn, command_id, WrapUnwrapState.WRAP_REQUESTED.value, {"amount_micro": int(amount_micro)})
        if own_conn:
            conn.commit()
        return command_id
    finally:
        if own_conn:
            conn.close()


def request_unwrap(amount_micro: int, conn: sqlite3.Connection | None = None) -> str:
    """Backward-compat alias: insert UNWRAP_REQUESTED row."""
    if amount_micro <= 0:
        raise ValueError("amount_micro must be positive")
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection
        conn = get_world_connection()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    command_id = uuid.uuid4().hex
    requested_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO wrap_unwrap_commands (command_id, state, direction, amount_micro, requested_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (command_id, WrapUnwrapState.UNWRAP_REQUESTED.value, "UNWRAP", int(amount_micro), requested_at),
        )
        _append_event(conn, command_id, WrapUnwrapState.UNWRAP_REQUESTED.value, {"amount_micro": int(amount_micro)})
        if own_conn:
            conn.commit()
        return command_id
    finally:
        if own_conn:
            conn.close()


def mark_tx_hashed(
    command_id: str,
    tx_hash: str,
    *,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Backward-compat alias: transition to WRAP_TX_HASHED or UNWRAP_TX_HASHED based on direction."""
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection
        conn = get_world_connection()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT direction FROM wrap_unwrap_commands WHERE command_id = ?", (command_id,)
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    direction = str(row["direction"] if isinstance(row, sqlite3.Row) else row[0])
    target_state = (
        WrapUnwrapState.WRAP_TX_HASHED if direction == "WRAP" else WrapUnwrapState.UNWRAP_TX_HASHED
    )
    try:
        _transition(command_id, new_state=target_state, conn=conn, tx_hash=tx_hash, block_number=block_number)
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def confirm_command(
    command_id: str,
    *,
    confirmation_count: int,
    block_number: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Backward-compat alias: transition to WRAP_CONFIRMED or UNWRAP_CONFIRMED based on direction."""
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection
        conn = get_world_connection()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT direction FROM wrap_unwrap_commands WHERE command_id = ?", (command_id,)
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    direction = str(row["direction"] if isinstance(row, sqlite3.Row) else row[0])
    target_state = (
        WrapUnwrapState.WRAP_CONFIRMED if direction == "WRAP" else WrapUnwrapState.UNWRAP_CONFIRMED
    )
    try:
        _transition(
            command_id, new_state=target_state, conn=conn,
            block_number=block_number, confirmation_count=confirmation_count, terminal=True,
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def fail_command(
    command_id: str,
    *,
    error_payload: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> None:
    """Backward-compat alias: transition to WRAP_FAILED or UNWRAP_FAILED based on direction."""
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection
        conn = get_world_connection()
    assert conn is not None
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT direction FROM wrap_unwrap_commands WHERE command_id = ?", (command_id,)
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    direction = str(row["direction"] if isinstance(row, sqlite3.Row) else row[0])
    target_state = (
        WrapUnwrapState.WRAP_FAILED if direction == "WRAP" else WrapUnwrapState.UNWRAP_FAILED
    )
    try:
        _transition(
            command_id, new_state=target_state, conn=conn,
            error_payload=json.dumps(error_payload, sort_keys=True), terminal=True,
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
