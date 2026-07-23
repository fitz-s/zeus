# Created: 2026-05-19
# Last reused or audited: 2026-05-20
# Authority basis: PR 6 WAVE_B_PR_3_6_FIELD_MAP.md row 16; pr36_scaffold.md BLOCKING REVISION 5;
#   operator brief 2026-05-19 auto-wrap session; .omc/plans/2026-05-19-auto-wrap-post-redeem.md
#   2026-05-20 live readiness repair: wrap confirmation balance refresh must stay behind V2 adapter boundary.
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
  error_payload TEXT,
  first_inclusion_block_time TEXT,
  finality_confirmed_time TEXT
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

# P0-2 CAS guard (2026-06-09): legal predecessor states per target. Every wrap
# state transition is a compare-and-swap — the UPDATE only fires when the row's
# CURRENT state is one of the legal predecessors below AND is non-terminal. This
# makes the state machine atomic under concurrent submitter/reconciler/same-tick
# interleaving: a stale snapshot can never revert a row that another worker has
# already advanced (e.g. reconciler reading WRAP_APPROVE_TX_HASHED then trying to
# mark_wrap_approved AFTER the same-tick path already drove the row to
# WRAP_CONFIRMED). Terminal states are absorbing: they appear in NO predecessor
# set, so any terminal -> non-terminal transition is rejected structurally
# (single SQL guard, not per-caller discipline).
#
# fail_* transitions are intentionally permissive on predecessor (any
# non-terminal state may fail) but still terminal-absorbing — a CONFIRMED row
# cannot be reverted to FAILED. Encoded as None == "any non-terminal".
_LEGAL_PREDECESSORS: dict[WrapUnwrapState, frozenset[WrapUnwrapState] | None] = {
    WrapUnwrapState.WRAP_APPROVE_TX_HASHED: frozenset({WrapUnwrapState.WRAP_REQUESTED}),
    WrapUnwrapState.WRAP_APPROVED: frozenset({WrapUnwrapState.WRAP_APPROVE_TX_HASHED}),
    # WRAP_TX_HASHED is reachable from WRAP_APPROVED (the two-step approve-then-
    # wrap path used in production since 2026-05-19) AND directly from
    # WRAP_REQUESTED (the legacy single-step `mark_tx_hashed` alias still
    # exported for backward compat — no approve tx). Both are legal non-terminal
    # predecessors; the critical anti-reversion guard (terminal states absorbing)
    # is unaffected because neither is terminal.
    WrapUnwrapState.WRAP_TX_HASHED: frozenset({
        WrapUnwrapState.WRAP_APPROVED,
        WrapUnwrapState.WRAP_REQUESTED,
    }),
    WrapUnwrapState.WRAP_CONFIRMED: frozenset({WrapUnwrapState.WRAP_TX_HASHED}),
    WrapUnwrapState.WRAP_FAILED: None,  # any non-terminal WRAP row may fail
    WrapUnwrapState.UNWRAP_TX_HASHED: frozenset({WrapUnwrapState.UNWRAP_REQUESTED}),
    WrapUnwrapState.UNWRAP_CONFIRMED: frozenset({WrapUnwrapState.UNWRAP_TX_HASHED}),
    WrapUnwrapState.UNWRAP_FAILED: None,  # any non-terminal UNWRAP row may fail
}


class WrapTransitionRejected(RuntimeError):
    """Raised when a CAS state transition is rejected (rowcount 0).

    The row's current state was not a legal predecessor of the requested
    target (a concurrent worker already advanced it, or it is terminal). The
    caller should re-read the row and decide. This is NOT an error condition —
    it is the structural anti-reversion guard firing as designed — so the
    reconciler/same-tick callers catch it, log the rejection, and move on.
    """

    def __init__(self, command_id: str, target: str, observed: str | None) -> None:
        self.command_id = command_id
        self.target = target
        self.observed = observed
        super().__init__(
            f"wrap CAS rejected: command_id={command_id} target={target} "
            f"observed_state={observed!r} (not a legal predecessor / terminal)"
        )


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

        # P0-2c: RE-READ the current state immediately before each transition.
        # The snapshot in `rows` was taken at entry; a concurrent same-tick path
        # or another worker may have advanced this row since. The CAS in
        # _transition is the structural guard, but re-reading here lets us route
        # to the correct transition (and skip rows already advanced past the
        # reconcilable states) instead of always trusting the stale snapshot.
        fresh = conn.execute(
            "SELECT state FROM wrap_unwrap_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if fresh is None:
            continue
        current_state = fresh["state"] if isinstance(fresh, sqlite3.Row) else fresh[0]

        try:
            if status == 0:
                fail_wrap(
                    command_id,
                    error_payload={"reason": "tx_reverted", "tx_hash": tx_hash},
                    conn=conn,
                )
                results.append(get_command(command_id, conn))
                continue

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
                    _refresh_collateral_after_wrap_confirmation(adapter)
                except Exception as refresh_exc:  # noqa: BLE001
                    # Fail-open: wrap is confirmed, balance refresh is best-effort.
                    # The operator can re-run a collateral refresh manually.
                    import logging
                    logging.getLogger(__name__).warning(
                        "reconcile_pending_wraps: WRAP_CONFIRMED command_id=%s "
                        "balance_refresh_failed=%s (fail-open, wrap durable)",
                        command_id, refresh_exc,
                    )
            else:
                # Row already advanced past a reconcilable state since the
                # snapshot (e.g. same-tick path confirmed it). Nothing to do.
                continue
        except WrapTransitionRejected:
            # A concurrent worker advanced this row between our re-read and the
            # CAS UPDATE. The anti-reversion guard fired as designed — skip;
            # the row is already in (or past) its intended state.
            continue
        results.append(get_command(command_id, conn))
    return results


def _refresh_collateral_after_wrap_confirmation(adapter: Any) -> None:
    """Refresh collateral facts after WRAP_CONFIRMED without importing SDK types here."""
    update_balance_allowance = getattr(adapter, "update_balance_allowance", None)
    if callable(update_balance_allowance):
        update_balance_allowance(None)
        return
    get_collateral_payload = getattr(adapter, "get_collateral_payload", None)
    if callable(get_collateral_payload):
        get_collateral_payload()
        return
    raise AttributeError("adapter exposes neither update_balance_allowance nor get_collateral_payload")


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

    # P0-2 CAS: build the WHERE state-predicate from the legal-predecessor map.
    # A transition only fires when the row's CURRENT state is a legal predecessor
    # of new_state AND is non-terminal. Terminal states never appear in any
    # predecessor set, so terminal -> anything is rejected structurally. fail_*
    # (predecessors == None) is "any non-terminal state may fail".
    _terminal_values = tuple(s.value for s in _WRAP_TERMINAL_STATES)
    _predecessors = _LEGAL_PREDECESSORS.get(new_state)
    if _predecessors is None:
        # Any non-terminal row may transition (fail_* paths).
        _state_predicate = f"state NOT IN ({','.join('?' * len(_terminal_values))})"
        _state_params: tuple[str, ...] = _terminal_values
    else:
        _pred_values = tuple(s.value for s in _predecessors)
        _state_predicate = f"state IN ({','.join('?' * len(_pred_values))})"
        _state_params = _pred_values
    try:
        cur = conn.execute(
            f"""
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
               AND {_state_predicate}
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
                *_state_params,
            ),
        )
        if cur.rowcount == 0:
            # CAS lost: the row's current state was not a legal predecessor
            # (a concurrent worker advanced it, or it is terminal). Re-read the
            # observed state, log the rejection, and raise so the caller decides.
            observed_row = conn.execute(
                "SELECT state FROM wrap_unwrap_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            observed = (
                (observed_row["state"] if isinstance(observed_row, sqlite3.Row) else observed_row[0])
                if observed_row is not None
                else None
            )
            import logging
            logging.getLogger(__name__).warning(
                "[WRAP_CAS_REJECTED] command_id=%s target=%s observed_state=%s "
                "(transition rejected: not a legal predecessor / terminal-absorbing)",
                command_id, new_state.value, observed,
            )
            _append_event(
                conn,
                command_id,
                f"CAS_REJECTED:{new_state.value}",
                {"target": new_state.value, "observed_state": observed},
            )
            if own_conn:
                conn.commit()
            raise WrapTransitionRejected(command_id, new_state.value, observed)
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


# ---------------------------------------------------------------------------
# Same-tick proceeds-driven wrap (2026-06-09, operator directive)
# ---------------------------------------------------------------------------

def _read_usdce_balance_via_adapter(adapter: Any, safe_address: str) -> int:
    """ERC-20 balanceOf(safe) on USDC.e via the adapter's urllib JSON-RPC seam.

    Same read as _read_usdce_balance but without a web3 instance — the redeem
    submitter/reconciler ticks already hold a PolymarketV2Adapter and must not
    construct a second RPC stack just for one balance call.
    """
    from src.venue.polymarket_v2_adapter import POLYGON_USDCE_ADDRESS

    addr_padded = str(safe_address).lower().removeprefix("0x").zfill(64)
    data = "0x70a08231" + addr_padded
    raw = adapter._rpc_call(
        adapter.polygon_rpc_url,
        "eth_call",
        [{"to": POLYGON_USDCE_ADDRESS, "data": data}, "latest"],
    )
    return int(str(raw or "0x0"), 16)


# P0-3: same-tick total wall-clock budget. The same-tick path is called INSIDE
# the redeem submitter/reconciler APScheduler jobs; a long synchronous chain-wait
# here starves the money-path pollers (RiskGuard, reactor, redeem). So the
# same-tick path only ENQUEUES + SUBMITS + does ONE short bounded receipt check
# per tx, then yields. Receipt FINALIZATION belongs to the fast wrap_reconciler.
_PROCEEDS_TOTAL_BUDGET_S = 12.0
_PROCEEDS_RECEIPT_CHECK_S = 4.0
# Bound on how many fresh WRAP rows the re-read loop (P0-1) may enqueue per call
# so a runaway balance read can't spin forever even within the time budget.
_PROCEEDS_MAX_ENQUEUE = 4


def wrap_proceeds_now(
    conn: sqlite3.Connection,
    adapter: Any,
    safe_address: str,
    signer_eoa: str,
    *,
    threshold_micro: int | None = None,
    total_budget_s: float = _PROCEEDS_TOTAL_BUDGET_S,
    receipt_check_s: float = _PROCEEDS_RECEIPT_CHECK_S,
    poll_interval_s: float = 1.0,
    max_steps: int = 6,
    # Deprecated (P0-3): kept for backward compat with existing callers/tests.
    # A 120s synchronous chain-wait inside a scheduler job is no longer allowed;
    # if passed, it is clamped to receipt_check_s for the per-tx check only.
    mined_timeout_s: float | None = None,
) -> dict[str, Any]:
    """Proceeds-driven wrap: enqueue + submit + ONE short receipt check per tx.

    STRUCTURAL FIX (operator directive 2026-06-09): the periodic wrap state
    machine advanced one step per 5-minute tick (intent -> approve -> approve
    confirm -> wrap -> wrap confirm), leaving fresh redemption proceeds sitting
    as unwrapped USDC.e across up to ~25 minutes ("Confirm pending deposit").

    THREE P0 invariants encoded here:

    P0-1 (no naked balance): after driving pending rows, this RE-READS the Safe
      balance and, while balance > threshold and budget remains, enqueues a NEW
      WRAP row and submits it. The honest antibody is NOT "balance < threshold
      after the call" (the wrap may still be mining) — it is "no USDC.e above
      threshold is UNCOMMITTED (no pending row) after the tick". A row left in
      a *_TX_HASHED state IS committed (funds are in the pipeline); the fast
      reconciler confirms it within its cadence.

    P0-2 (atomic state): all transitions go through _transition's compare-and-
      swap, so a concurrent reconciler/submitter can never revert a row this
      path advanced (and vice versa). A WrapTransitionRejected here means
      another worker already advanced the row — caught and treated as success.

    P0-3 (bounded wall time): the WHOLE call is bounded by total_budget_s
      (default 12s) — it does NOT block a scheduler job for minutes. Each tx
      gets ONE short receipt check (receipt_check_s, default 4s). If the receipt
      has not landed, the row is left in its *_TX_HASHED state and the periodic
      wrap_reconciler (fast cadence while any TX_HASHED row exists) finalizes
      it. Leaving a row TX_HASHED is SUCCESS, not failure.

    Fail-soft everywhere: a reverted tx marks WRAP_FAILED; a transient RPC error
    or receipt-check timeout leaves the row mid-state for the periodic poller —
    no raise into the calling scheduler tick.

    Returns {"enqueued": [ids], "confirmed": [ids], "failed": [ids],
             "pending": [ids], "balance_micro_before": int,
             "balance_micro_after": int, "budget_exhausted": bool}.
    """
    import logging
    import time as _time

    logger = logging.getLogger(__name__)
    retired_key = "ZEUS_AUTONOMOUS_WRAP_" + "DRY_RUN"
    if retired_key in os.environ:
        raise RuntimeError(
            f"retired wrap configuration {retired_key} must be removed before broadcast"
        )
    init_wrap_unwrap_schema(conn)

    if threshold_micro is None:
        threshold_micro = int(
            os.environ.get("AUTO_WRAP_THRESHOLD_MICRO", str(_DEFAULT_WRAP_THRESHOLD_MICRO))
        )
    # P0-3: clamp any legacy mined_timeout_s down to the short per-tx check.
    if mined_timeout_s is not None:
        receipt_check_s = min(receipt_check_s, float(mined_timeout_s))

    deadline = _time.monotonic() + float(total_budget_s)

    # NOTE: "enqueued" is a LIST (P0-1 may enqueue multiple rows across the
    # re-read loop). _wrap_proceeds_same_tick reads it truthily, which still
    # works for a non-empty list.
    out: dict[str, Any] = {
        "enqueued": [], "confirmed": [], "failed": [], "pending": [],
        "balance_micro_before": -1, "balance_micro_after": -1,
        "budget_exhausted": False,
    }

    def _read_balance() -> Optional[int]:
        try:
            return _read_usdce_balance_via_adapter(adapter, safe_address)
        except Exception as exc:  # noqa: BLE001 — fail-soft; periodic poller resumes
            logger.warning("[WRAP_PROCEEDS_BALANCE_READ_FAILED] %s", exc)
            return None

    balance_micro = _read_balance()
    if balance_micro is None:
        return out
    out["balance_micro_before"] = balance_micro
    out["balance_micro_after"] = balance_micro

    def _enqueue(amount_micro: int) -> str:
        command_id = uuid.uuid4().hex
        requested_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO wrap_unwrap_commands (
              command_id, state, direction, amount_micro, requested_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (command_id, WrapUnwrapState.WRAP_REQUESTED.value, "WRAP",
             int(amount_micro), requested_at),
        )
        _append_event(conn, command_id, WrapUnwrapState.WRAP_REQUESTED.value, {
            "balance_micro": int(amount_micro),
            "threshold_micro": int(threshold_micro),
            "trigger": "proceeds_driven_same_tick",
        })
        conn.commit()
        out["enqueued"].append(command_id)
        logger.info(
            "[WRAP_PROCEEDS_ENQUEUED] command_id=%s amount_micro=%d",
            command_id, amount_micro,
        )
        return command_id

    # Enqueue if needed (same idempotency gate as the periodic intent creator:
    # any non-terminal WRAP row blocks a new intent).
    pending = list_pending_wrap_commands(conn)
    if not pending and balance_micro > threshold_micro:
        _enqueue(balance_micro)

    def _short_receipt_check(tx_hash: str) -> Optional[dict[str, Any]]:
        """ONE bounded receipt check (P0-3): a few seconds, not a chain-wait.

        Bounded by both receipt_check_s AND the remaining total budget so the
        whole call honours total_budget_s.
        """
        t0 = _time.monotonic()
        while True:
            now = _time.monotonic()
            if now - t0 >= receipt_check_s or now >= deadline:
                return None
            try:
                rcpt = adapter._rpc_call(
                    adapter.polygon_rpc_url, "eth_getTransactionReceipt", [tx_hash]
                )
            except Exception:  # noqa: BLE001 — transient; keep polling
                rcpt = None
            if rcpt:
                return rcpt
            # Don't oversleep past either bound.
            remaining = min(receipt_check_s - (now - t0), deadline - now)
            if remaining <= 0:
                return None
            _time.sleep(min(poll_interval_s, remaining))

    def _advance_one(row: dict[str, Any]) -> str:
        """Submit the next tx for one pending row and do ONE short receipt check.

        Returns a coarse outcome tag: "confirmed" | "approved" | "hashed"
        (left TX_HASHED for the reconciler) | "failed" | "stop" (dry-run /
        submit failure / exception — leave for periodic poller).
        """
        command_id = row["command_id"]
        tx_kind = (
            "APPROVE"
            if row["state"] == WrapUnwrapState.WRAP_REQUESTED.value
            else "WRAP"
        )
        try:
            result = adapter._wrap_via_safe(
                safe_address=safe_address,
                amount_micro=row["amount_micro"],
                tx_kind=tx_kind,
                signer_eoa=signer_eoa,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft; poller resumes
            logger.warning(
                "[WRAP_PROCEEDS_STEP_EXCEPTION] command_id=%s tx_kind=%s exc=%s",
                command_id, tx_kind, exc,
            )
            return "stop"
        if not result.get("success"):
            logger.warning(
                "[WRAP_PROCEEDS_STEP_FAILED] command_id=%s tx_kind=%s errorCode=%s msg=%s",
                command_id, tx_kind, result.get("errorCode"), result.get("errorMessage"),
            )
            return "stop"  # leave row mid-state for the periodic poller (fail-soft)
        tx_hash = str(result["tx_hash"])
        try:
            if tx_kind == "APPROVE":
                mark_wrap_approve_tx_hashed(command_id, tx_hash, conn=conn)
            else:
                mark_wrap_tx_hashed(command_id, tx_hash, conn=conn)
            conn.commit()
        except WrapTransitionRejected:
            # Another worker advanced this row between our read and the CAS.
            return "stop"

        rcpt = _short_receipt_check(tx_hash)
        if rcpt is None:
            # P0-3: receipt not yet landed within the short budget. The row is
            # committed (funds in the pipeline); the fast reconciler finalizes.
            logger.info(
                "[WRAP_PROCEEDS_HASHED_DEFERRED] command_id=%s tx_kind=%s tx=%s "
                "— left %s_TX_HASHED for fast wrap_reconciler",
                command_id, tx_kind, tx_hash, tx_kind,
            )
            return "hashed"
        status = rcpt.get("status")
        if str(status).lower() not in ("0x1", "1"):
            try:
                fail_wrap(
                    command_id,
                    error_payload={"reason": "tx_reverted", "tx_hash": tx_hash, "tx_kind": tx_kind},
                    conn=conn,
                )
                conn.commit()
            except WrapTransitionRejected:
                return "stop"
            out["failed"].append(command_id)
            logger.warning(
                "[WRAP_PROCEEDS_TX_REVERTED] command_id=%s tx_kind=%s tx=%s",
                command_id, tx_kind, tx_hash,
            )
            return "failed"
        if tx_kind == "APPROVE":
            try:
                mark_wrap_approved(command_id, conn=conn)
                conn.commit()
            except WrapTransitionRejected:
                return "stop"
            return "approved"
        block_num = None
        try:
            _bn = rcpt.get("blockNumber")
            block_num = int(str(_bn), 16) if isinstance(_bn, str) else int(_bn)
        except Exception:  # noqa: BLE001
            block_num = None
        try:
            confirm_wrap(command_id, confirmation_count=1, block_number=block_num, conn=conn)
            conn.commit()
        except WrapTransitionRejected:
            return "stop"
        out["confirmed"].append(command_id)
        logger.info(
            "[WRAP_PROCEEDS_CONFIRMED] command_id=%s tx=%s block=%s",
            command_id, tx_hash, block_num,
        )
        try:
            _refresh_collateral_after_wrap_confirmation(adapter)
        except Exception as refresh_exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[WRAP_PROCEEDS_BALANCE_REFRESH_FAILED] command_id=%s exc=%s",
                command_id, refresh_exc,
            )
        return "confirmed"

    # ------------------------------------------------------------------
    # Drive pending rows, bounded by both max_steps and the wall-clock budget.
    # ------------------------------------------------------------------
    enqueue_budget = _PROCEEDS_MAX_ENQUEUE
    saw_failure = False
    for _ in range(max_steps):
        if _time.monotonic() >= deadline:
            out["budget_exhausted"] = True
            break
        pending = list_pending_wrap_commands(conn)
        actionable = [
            r for r in pending
            if r["state"] in (
                WrapUnwrapState.WRAP_REQUESTED.value,
                WrapUnwrapState.WRAP_APPROVED.value,
            )
        ]
        if not actionable:
            # P0-1: no actionable in-flight rows. Re-read the Safe balance — if
            # fresh proceeds (or a leftover above threshold) remain UNCOMMITTED
            # (no pending row at all), enqueue and drive a new row so nothing
            # naked survives the tick. A row already TX_HASHED counts as pending.
            #
            # Anti-storm guard: if a tx REVERTED this call, do NOT re-enqueue
            # against the same residual balance — re-driving a persistently
            # reverting wrap just burns gas. The failed row is terminal and
            # needs operator attention; leave the balance for the next tick
            # (by when the revert cause may have cleared or the operator acted).
            if pending or enqueue_budget <= 0 or saw_failure:
                break
            bal = _read_balance()
            if bal is not None:
                out["balance_micro_after"] = bal
            if bal is None or bal <= threshold_micro:
                break
            enqueue_budget -= 1
            new_id = _enqueue(bal)
            actionable = [{"command_id": new_id,
                           "state": WrapUnwrapState.WRAP_REQUESTED.value,
                           "amount_micro": bal}]

        outcome = _advance_one(actionable[0])
        if outcome == "failed":
            saw_failure = True
            break
        if outcome in ("stop", "hashed"):
            # "hashed": tx is in flight; reconciler finalizes — do not spin the
            #   same row again this tick (avoids a second submit before mining).
            # "stop":   dry-run / submit failure / exception / CAS race — defer.
            break

    # Final balance read so the antibody can assert on the committed picture.
    bal = _read_balance()
    if bal is not None:
        out["balance_micro_after"] = bal
    out["pending"] = [r["command_id"] for r in list_pending_wrap_commands(conn)]
    if _time.monotonic() >= deadline:
        out["budget_exhausted"] = True
    return out
