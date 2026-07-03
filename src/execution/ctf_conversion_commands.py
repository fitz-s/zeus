# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 row "CTF convert/split/merge" (W2.4 packet) + §2 notes; docs/operations/
#   current/plans/order_engine_rebuild_execution_plan_2026-07-02.md W2 section.
"""Durable command ledger for CTF split/merge/convert (R3 W2.4).

INTENT-KIND ROUTING DECISION (2026-07-02, W2.4): split/merge/convert are
Zeus-side on-chain CTF/NegRiskAdapter contract calls, not CLOB orders — they
do NOT extend ``venue_commands.intent_kind`` / ``command_bus.IntentKind``.
Two existing precedents for a non-order venue side effect BOTH chose an own
dedicated command table with its own closed state enum instead of overloading
the order-shaped venue_commands grammar (token_id/side/size/price columns,
CommandState=SUBMITTING/POSTING/ACKED/FILLED transitions that don't describe
a quantity-only on-chain conversion):
  - settlement_commands.py — redemption ACCOUNTING; the sanctioned "how
    redeem is journaled" pattern this module was told to study. Trade DB,
    own SettlementState enum, condition_id/market_id-scoped.
  - wrap_unwrap_commands.py — USDC.e<->pUSD wrap lifecycle. World DB, own
    WrapUnwrapState enum, no market scoping.
This module follows the settlement_commands.py shape specifically (trade DB,
condition_id/market_id-scoped, redemption-adjacent position lifecycle) rather
than wrap_unwrap_commands.py's world DB (pure collateral wrap, no market
scoping) — see architecture/db_table_ownership.yaml for the table's db/
schema_class rows.

INV-28 discipline: every venue side effect is preceded by a persisted command
row. ``enqueue_split``/``enqueue_merge``/``enqueue_convert`` persist an
INTENT_CREATED row (+ event) BEFORE ``execute_conversion`` ever calls the
adapter. ``execute_conversion`` maps the adapter's response to an ack/reject/
unknown outcome and NEVER guesses success: an exception, or an adapter error
code that signals the broadcast RPC call itself may or may not have landed
(``*_BROADCAST_FAILED`` / ``*_INVALID_TX_HASH``), is fail-closed UNKNOWN —
never silently promoted to TX_HASHED or FAILED.

Methods land INERT this packet: nothing in production calls
enqueue_*/execute_conversion, and the new
PolymarketV2Adapter.split_positions/merge_positions/convert_positions have no
production caller either. ``src/execution/negrisk_routes.py``'s
``conversion_routes`` stay ``executable=False`` in this packet (W3 flips
them) — this module and negrisk_routes.py are not wired together yet.

State machine (flat WHERE-guarded transitions, modeled on
settlement_commands.py's ``_atomic_transition`` — no CAS legal-predecessor
matrix like wrap_unwrap_commands.py's, because that machinery exists there
for a same-tick concurrent submitter/reconciler interleaving that has no
analogue here yet; a stronger guard is added the day a second writer lands):

  CTF_CONVERSION_INTENT_CREATED
    -> CTF_CONVERSION_TX_HASHED   (adapter call returned a tx_hash — "ack")
    -> CTF_CONVERSION_CONFIRMED   (chain receipt status=1)               [terminal]
    -> CTF_CONVERSION_FAILED      (adapter call cleanly rejected, OR
                                    chain receipt status=0)               [terminal]
    -> CTF_CONVERSION_UNKNOWN     (adapter call raised, OR the broadcast
                                    RPC call itself failed/returned a
                                    malformed hash — genuinely ambiguous;
                                    NEVER guessed success)   [non-terminal,
                                                               operator/
                                                               reconciler
                                                               follow-up]

DB: trade (get_trade_connection_with_world_required), matching
settlement_commands.py / venue_commands — condition_id/market_id-scoped, NOT
world (see architecture/db_table_ownership.yaml).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional

__all__ = [
    "CTF_CONVERSION_COMMAND_SCHEMA",
    "ConversionState",
    "ConversionCommandError",
    "ConversionCommandStateError",
    "ensure_ctf_conversion_schema_ready",
    "enqueue_split",
    "enqueue_merge",
    "enqueue_convert",
    "execute_conversion",
    "reconcile_pending_conversions",
    "get_command",
    "list_commands",
]

CTF_CONVERSION_COMMAND_SCHEMA = """
CREATE TABLE IF NOT EXISTS ctf_conversion_commands (
  command_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN (
    'CTF_CONVERSION_INTENT_CREATED','CTF_CONVERSION_TX_HASHED',
    'CTF_CONVERSION_CONFIRMED','CTF_CONVERSION_FAILED','CTF_CONVERSION_UNKNOWN'
  )),
  operation_type TEXT NOT NULL CHECK (operation_type IN ('SPLIT','MERGE','CONVERT')),
  neg_risk INTEGER NOT NULL DEFAULT 0 CHECK (neg_risk IN (0,1)),
  condition_id TEXT,
  market_id TEXT,
  index_set INTEGER,
  amount_micro INTEGER NOT NULL CHECK (amount_micro > 0),
  tx_hash TEXT,
  block_number INTEGER,
  confirmation_count INTEGER DEFAULT 0,
  requested_at TEXT NOT NULL,
  terminal_at TEXT,
  error_payload TEXT,
  -- SPLIT/MERGE key on condition_id (the CTF/NegRiskAdapter per-condition
  -- shape); CONVERT keys on market_id + index_set (NegRiskAdapter's distinct
  -- multi-market convertPositions shape) — the two identifier spaces are not
  -- interchangeable (see polymarket_v2_adapter.py NEGRISK_CONVERT_POSITIONS_SELECTOR
  -- comment), so a row can only carry one.
  CHECK (
    (operation_type IN ('SPLIT','MERGE')
     AND condition_id IS NOT NULL AND market_id IS NULL AND index_set IS NULL)
    OR
    (operation_type = 'CONVERT'
     AND market_id IS NOT NULL AND condition_id IS NULL AND index_set IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_ctf_conversion_commands_state
  ON ctf_conversion_commands (state, requested_at);
CREATE INDEX IF NOT EXISTS idx_ctf_conversion_commands_condition
  ON ctf_conversion_commands (condition_id);
CREATE INDEX IF NOT EXISTS idx_ctf_conversion_commands_market
  ON ctf_conversion_commands (market_id);

CREATE TABLE IF NOT EXISTS ctf_conversion_command_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL REFERENCES ctf_conversion_commands(command_id),
  event_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT,
  recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ctf_conversion_command_events_command
  ON ctf_conversion_command_events (command_id, recorded_at);
"""


class ConversionState(str, Enum):
    INTENT_CREATED = "CTF_CONVERSION_INTENT_CREATED"
    TX_HASHED = "CTF_CONVERSION_TX_HASHED"
    CONFIRMED = "CTF_CONVERSION_CONFIRMED"
    FAILED = "CTF_CONVERSION_FAILED"
    UNKNOWN = "CTF_CONVERSION_UNKNOWN"


# Adapter error codes whose failure point is the broadcast RPC call itself
# (eth_sendRawTransaction raised, or returned something that isn't a valid
# tx hash). Every OTHER adapter error code fires strictly before broadcast
# (preflight/sign/calldata-build), so it is a clean reject: no tx was ever
# sent. Only these two are genuinely ambiguous about on-chain state.
_AMBIGUOUS_ERROR_CODE_SUFFIXES = ("_BROADCAST_FAILED", "_INVALID_TX_HASH")


class ConversionCommandError(RuntimeError):
    """Base error for invalid CTF conversion command operations."""


class ConversionCommandStateError(ConversionCommandError):
    """Raised for illegal conversion command transitions."""


def ensure_ctf_conversion_schema_ready(conn: sqlite3.Connection) -> None:
    conn.executescript(CTF_CONVERSION_COMMAND_SCHEMA)


def enqueue_split(
    condition_id: str,
    amount_micro: int,
    *,
    neg_risk: bool = False,
    conn: sqlite3.Connection | None = None,
    requested_at: datetime | str | None = None,
) -> str:
    """Persist a SPLIT intent row. Returns command_id. Persist-before-side-effect."""
    return _enqueue(
        operation_type="SPLIT",
        condition_id=_require_nonempty("condition_id", condition_id),
        amount_micro=amount_micro,
        neg_risk=neg_risk,
        conn=conn,
        requested_at=requested_at,
    )


def enqueue_merge(
    condition_id: str,
    amount_micro: int,
    *,
    neg_risk: bool = False,
    conn: sqlite3.Connection | None = None,
    requested_at: datetime | str | None = None,
) -> str:
    """Persist a MERGE intent row. Returns command_id. Persist-before-side-effect."""
    return _enqueue(
        operation_type="MERGE",
        condition_id=_require_nonempty("condition_id", condition_id),
        amount_micro=amount_micro,
        neg_risk=neg_risk,
        conn=conn,
        requested_at=requested_at,
    )


def enqueue_convert(
    market_id: str,
    index_set: int,
    amount_micro: int,
    *,
    conn: sqlite3.Connection | None = None,
    requested_at: datetime | str | None = None,
) -> str:
    """Persist a CONVERT intent row. Returns command_id. Persist-before-side-effect.

    CONVERT is always neg-risk (conversion is a NegRiskAdapter-only concept —
    there is no standard-CTF equivalent).
    """
    if int(index_set) <= 0:
        raise ValueError(f"index_set must be a positive bitfield, got {index_set!r}")
    return _enqueue(
        operation_type="CONVERT",
        market_id=_require_nonempty("market_id", market_id),
        index_set=int(index_set),
        amount_micro=amount_micro,
        neg_risk=True,
        conn=conn,
        requested_at=requested_at,
    )


def _enqueue(
    *,
    operation_type: str,
    amount_micro: int,
    neg_risk: bool,
    conn: sqlite3.Connection | None,
    requested_at: datetime | str | None,
    condition_id: str | None = None,
    market_id: str | None = None,
    index_set: int | None = None,
) -> str:
    if int(amount_micro) <= 0:
        raise ValueError(f"amount_micro must be positive, got {amount_micro!r}")

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_trade_connection_with_world_required

        conn = get_trade_connection_with_world_required(write_class="live")
    assert conn is not None
    ensure_ctf_conversion_schema_ready(conn)

    command_id = uuid.uuid4().hex
    requested_at_s = _coerce_time(requested_at)
    try:
        conn.execute(
            """
            INSERT INTO ctf_conversion_commands (
              command_id, state, operation_type, neg_risk, condition_id,
              market_id, index_set, amount_micro, requested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                ConversionState.INTENT_CREATED.value,
                operation_type,
                int(bool(neg_risk)),
                condition_id,
                market_id,
                index_set,
                int(amount_micro),
                requested_at_s,
            ),
        )
        _append_event(
            conn,
            command_id,
            ConversionState.INTENT_CREATED.value,
            {
                "operation_type": operation_type,
                "neg_risk": bool(neg_risk),
                "condition_id": condition_id,
                "market_id": market_id,
                "index_set": index_set,
                "amount_micro": int(amount_micro),
            },
            recorded_at=requested_at_s,
        )
        if own_conn:
            conn.commit()
        return command_id
    finally:
        if own_conn:
            conn.close()


def execute_conversion(
    conn: sqlite3.Connection,
    adapter: Any,  # PolymarketV2Adapter
    command_id: str,
    *,
    safe_address: str,
    signer_eoa: str,
) -> dict[str, Any]:
    """Call the adapter's matching split/merge/convert method for a persisted
    INTENT_CREATED row and map the result to an ack/reject/unknown transition.

    Requires the command row to already exist (via enqueue_split/enqueue_merge/
    enqueue_convert) — this function does not persist an intent itself, it only
    drives the side effect and records the outcome (INV-28: persist happens in
    the enqueue_* step, strictly before this is ever called).

    Fail-closed: any exception raised while calling the adapter, or an adapter
    error code whose failure point is the broadcast RPC call itself (chain
    state is genuinely unknown), transitions the row to UNKNOWN — never
    guessed as TX_HASHED or FAILED.
    """
    ensure_ctf_conversion_schema_ready(conn)
    row = _get_row(conn, command_id)
    if row["state"] != ConversionState.INTENT_CREATED.value:
        raise ConversionCommandStateError(
            f"execute_conversion: command_id={command_id} is in state "
            f"{row['state']!r}, not {ConversionState.INTENT_CREATED.value!r}"
        )

    operation_type = row["operation_type"]
    try:
        if operation_type == "SPLIT":
            result = adapter.split_positions(
                row["condition_id"],
                int(row["amount_micro"]),
                neg_risk=bool(row["neg_risk"]),
                safe_address=safe_address,
                signer_eoa=signer_eoa,
            )
        elif operation_type == "MERGE":
            result = adapter.merge_positions(
                row["condition_id"],
                int(row["amount_micro"]),
                neg_risk=bool(row["neg_risk"]),
                safe_address=safe_address,
                signer_eoa=signer_eoa,
            )
        else:
            result = adapter.convert_positions(
                row["market_id"],
                int(row["index_set"]),
                int(row["amount_micro"]),
                safe_address=safe_address,
                signer_eoa=signer_eoa,
            )
    except Exception as exc:  # noqa: BLE001 — fail-closed: chain state unknown
        return _mark_unknown(
            conn, command_id,
            error_payload={"reason": "adapter_call_raised", "detail": str(exc)},
        )

    if not isinstance(result, Mapping):
        return _mark_unknown(
            conn, command_id,
            error_payload={"reason": "adapter_returned_non_mapping", "detail": repr(result)},
        )

    if result.get("success"):
        tx_hash = result.get("tx_hash")
        if not tx_hash:
            # Adapter claims success but no tx_hash — cannot be ACKed honestly.
            return _mark_unknown(
                conn, command_id,
                error_payload={"reason": "success_without_tx_hash", "raw": dict(result)},
            )
        return _mark_tx_hashed(conn, command_id, tx_hash=str(tx_hash))

    error_code = str(result.get("errorCode") or "")
    if error_code.endswith(_AMBIGUOUS_ERROR_CODE_SUFFIXES):
        return _mark_unknown(
            conn, command_id,
            error_payload={"reason": "ambiguous_broadcast_outcome", "raw": dict(result)},
        )

    # Every other failure (preflight/sign/calldata-build rejection, disabled
    # kill switch, dry-run) fires strictly before any RPC broadcast attempt —
    # a clean reject, no tx was ever sent. Dry-run is intentionally treated as
    # a reject here too: this call did not produce a chain side effect.
    return _mark_failed(conn, command_id, error_payload=dict(result))


def reconcile_pending_conversions(
    w3: Any,  # web3.Web3
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Poll TX_HASHED rows; advance to CONFIRMED/FAILED on chain receipt."""
    ensure_ctf_conversion_schema_ready(conn)
    rows = conn.execute(
        "SELECT * FROM ctf_conversion_commands WHERE state = ? ORDER BY requested_at, command_id",
        (ConversionState.TX_HASHED.value,),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        row_d = dict(row)
        command_id = row_d["command_id"]
        tx_hash = row_d.get("tx_hash")
        if not tx_hash:
            continue
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:  # noqa: BLE001 — transient RPC error; retry next tick
            continue
        if receipt is None:
            continue
        status = receipt.get("status", 1) if isinstance(receipt, dict) else getattr(receipt, "status", 1)
        block_number = receipt.get("blockNumber") if isinstance(receipt, dict) else getattr(receipt, "blockNumber", None)
        if isinstance(block_number, str):
            try:
                block_number = int(block_number, 16)
            except ValueError:
                block_number = None

        if str(status).lower() in ("0", "0x0", "false"):
            transitioned = _atomic_transition(
                conn, command_id,
                from_state=ConversionState.TX_HASHED, to_state=ConversionState.FAILED,
                terminal=True,
                error_payload={"reason": "tx_reverted", "tx_hash": tx_hash},
            )
        else:
            transitioned = _atomic_transition(
                conn, command_id,
                from_state=ConversionState.TX_HASHED, to_state=ConversionState.CONFIRMED,
                terminal=True,
                block_number=block_number,
                confirmation_count=1,
            )
        if transitioned:
            conn.commit()
            results.append(dict(_get_row(conn, command_id)))
    return results


def get_command(conn: sqlite3.Connection, command_id: str) -> dict[str, Any]:
    ensure_ctf_conversion_schema_ready(conn)
    return dict(_get_row(conn, command_id))


def list_commands(
    conn: sqlite3.Connection, *, state: ConversionState | str | None = None
) -> list[dict[str, Any]]:
    ensure_ctf_conversion_schema_ready(conn)
    if state is None:
        rows = conn.execute(
            "SELECT * FROM ctf_conversion_commands ORDER BY requested_at, command_id"
        ).fetchall()
    else:
        state_value = state.value if isinstance(state, ConversionState) else str(state)
        rows = conn.execute(
            "SELECT * FROM ctf_conversion_commands WHERE state = ? ORDER BY requested_at, command_id",
            (state_value,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal transition helpers
# ---------------------------------------------------------------------------

def _mark_tx_hashed(conn: sqlite3.Connection, command_id: str, *, tx_hash: str) -> dict[str, Any]:
    transitioned = _atomic_transition(
        conn, command_id,
        from_state=ConversionState.INTENT_CREATED, to_state=ConversionState.TX_HASHED,
        tx_hash=tx_hash,
        payload={"tx_hash": tx_hash},
    )
    conn.commit()
    row = dict(_get_row(conn, command_id))
    return {"success": transitioned, "state": row["state"], "tx_hash": tx_hash, "command_id": command_id}


def _mark_failed(conn: sqlite3.Connection, command_id: str, *, error_payload: Mapping[str, Any]) -> dict[str, Any]:
    transitioned = _atomic_transition(
        conn, command_id,
        from_state=ConversionState.INTENT_CREATED, to_state=ConversionState.FAILED,
        terminal=True,
        error_payload=error_payload,
        payload=dict(error_payload),
    )
    conn.commit()
    row = dict(_get_row(conn, command_id))
    return {"success": False, "state": row["state"], "command_id": command_id, **dict(error_payload)}


def _mark_unknown(conn: sqlite3.Connection, command_id: str, *, error_payload: Mapping[str, Any]) -> dict[str, Any]:
    transitioned = _atomic_transition(
        conn, command_id,
        from_state=ConversionState.INTENT_CREATED, to_state=ConversionState.UNKNOWN,
        error_payload=error_payload,
        payload=dict(error_payload),
    )
    conn.commit()
    row = dict(_get_row(conn, command_id))
    return {"success": False, "state": row["state"], "command_id": command_id, **dict(error_payload)}


def _get_row(conn: sqlite3.Connection, command_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM ctf_conversion_commands WHERE command_id = ?", (command_id,)
    ).fetchone()
    if row is None:
        raise KeyError(command_id)
    return row


def _atomic_transition(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    from_state: ConversionState | str,
    to_state: ConversionState | str,
    tx_hash: str | None = None,
    block_number: int | None = None,
    confirmation_count: int | None = None,
    error_payload: Mapping[str, Any] | None = None,
    terminal: bool = False,
    payload: Mapping[str, Any] | None = None,
) -> bool:
    """SQLite-atomic conditional state transition with a WHERE state guard.

    Returns True iff the row was transitioned (rowcount == 1). Modeled on
    settlement_commands.py's ``_atomic_transition``: the caller must check the
    return value before treating the transition as having happened.
    """
    from_value = from_state.value if isinstance(from_state, ConversionState) else from_state
    to_value = to_state.value if isinstance(to_state, ConversionState) else to_state
    recorded_at = _coerce_time(None)
    terminal_at = recorded_at if terminal else None
    cur = conn.execute(
        """
        UPDATE ctf_conversion_commands
           SET state = ?,
               tx_hash = COALESCE(?, tx_hash),
               block_number = COALESCE(?, block_number),
               confirmation_count = COALESCE(?, confirmation_count),
               terminal_at = COALESCE(?, terminal_at),
               error_payload = COALESCE(?, error_payload)
         WHERE command_id = ?
           AND state = ?
        """,
        (
            to_value,
            tx_hash,
            block_number,
            confirmation_count,
            terminal_at,
            _json_dumps(dict(error_payload)) if error_payload is not None else None,
            command_id,
            from_value,
        ),
    )
    transitioned = cur.rowcount == 1
    if transitioned:
        _append_event(conn, command_id, to_value, dict(payload or {}), recorded_at=recorded_at)
    return transitioned


def _append_event(
    conn: sqlite3.Connection,
    command_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    recorded_at: str,
) -> None:
    payload_json = _json_dumps(dict(payload))
    conn.execute(
        """
        INSERT INTO ctf_conversion_command_events (
          command_id, event_type, payload_hash, payload_json, recorded_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (command_id, event_type, _payload_hash(payload_json), payload_json, recorded_at),
    )


def _require_nonempty(name: str, value: Optional[str]) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{name} is required")
    return str(value).strip()


def _coerce_time(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
