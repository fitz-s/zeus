"""Durable settlement/redeem command ledger for R3 R1.

R1 makes redemption side effects crash-recoverable without authorizing default
live chain submission.  The ledger records intent, submission, tx-hash, terminal
confirmation/failure, and operator-review states.  Chain truth follows the
``REDEEM_TX_HASHED`` anchor during reconciliation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Mapping, Optional

from src.architecture.decorators import capability
from src.control.cutover_guard import CutoverPending, redemption_decision
from src.contracts.fx_classification import FXClassification
from src.state.collateral_ledger import require_pusd_redemption_allowed

logger = logging.getLogger(__name__)

PAYOUT_ASSETS = frozenset({"pUSD", "USDC", "USDC_E"})

SETTLEMENT_COMMAND_SCHEMA = """
CREATE TABLE IF NOT EXISTS settlement_commands (
  command_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN (
    'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
    'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED',
    'REDEEM_OPERATOR_REQUIRED'
  )),
  condition_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  payout_asset TEXT NOT NULL CHECK (payout_asset IN ('pUSD','USDC','USDC_E')),
  pusd_amount_micro INTEGER,
  token_amounts_json TEXT,
  winning_index_set TEXT,
  tx_hash TEXT,
  block_number INTEGER,
  confirmation_count INTEGER DEFAULT 0,
  requested_at TEXT NOT NULL,
  submitted_at TEXT,
  terminal_at TEXT,
  error_payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_settlement_commands_state
  ON settlement_commands (state, requested_at);
CREATE INDEX IF NOT EXISTS idx_settlement_commands_condition
  ON settlement_commands (condition_id, market_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_settlement_commands_active_condition_asset
  ON settlement_commands (condition_id, market_id, payout_asset)
  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');

CREATE TABLE IF NOT EXISTS settlement_command_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL REFERENCES settlement_commands(command_id),
  event_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_settlement_command_events_command
  ON settlement_command_events (command_id, recorded_at);
"""


class SettlementState(str, Enum):
    REDEEM_INTENT_CREATED = "REDEEM_INTENT_CREATED"
    REDEEM_SUBMITTED = "REDEEM_SUBMITTED"
    REDEEM_TX_HASHED = "REDEEM_TX_HASHED"
    REDEEM_CONFIRMED = "REDEEM_CONFIRMED"
    REDEEM_FAILED = "REDEEM_FAILED"
    REDEEM_RETRYING = "REDEEM_RETRYING"
    REDEEM_REVIEW_REQUIRED = "REDEEM_REVIEW_REQUIRED"
    # 2026-05-16 SCAFFOLD §K.2 v5 Path A-clean: first-class state for
    # operator-completion when PolymarketV2Adapter.redeem returns the
    # REDEEM_DEFERRED_TO_R1 stub. Exit transitions only via
    # scripts/operator_record_redeem.py CLI (record-only, no web3 write).
    REDEEM_OPERATOR_REQUIRED = "REDEEM_OPERATOR_REQUIRED"


# NOTE (2026-05-16 SCAFFOLD §K v5): REDEEM_OPERATOR_REQUIRED is NOT terminal.
# It is a designed-terminal-with-operator-action state (per
# cascade_liveness_contract.yaml terminal_states_with_operator_action).
# The operator CLI transitions it out to REDEEM_TX_HASHED; no scheduler tick
# touches it (disjoint state guard with _SUBMITTABLE_STATES).
_TERMINAL_STATES = {
    SettlementState.REDEEM_CONFIRMED,
    SettlementState.REDEEM_FAILED,
    SettlementState.REDEEM_REVIEW_REQUIRED,
}

_SUBMITTABLE_STATES = {
    SettlementState.REDEEM_INTENT_CREATED,
    SettlementState.REDEEM_RETRYING,
}


@dataclass(frozen=True)
class SettlementResult:
    command_id: str
    state: SettlementState
    tx_hash: str | None = None
    block_number: int | None = None
    confirmation_count: int = 0
    raw_response: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None


class SettlementCommandError(RuntimeError):
    """Base error for invalid settlement command operations."""


class SettlementCommandStateError(SettlementCommandError):
    """Raised for illegal settlement command transitions."""


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _capability_component(
    component: str,
    *,
    allowed: bool,
    reason: str,
    **details: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "component": str(component),
        "allowed": bool(allowed),
        "reason": str(reason),
    }
    if details:
        payload["details"] = {
            key: _enum_value(value) if not isinstance(value, (bool, int, float)) else value
            for key, value in details.items()
        }
    return payload


def _build_redeem_execution_capability(
    *,
    row: sqlite3.Row,
    cutover: Any,
    fx_classification: FXClassification | None,
    freshness_time: str,
) -> dict[str, Any]:
    payout_asset = str(row["payout_asset"])
    state = str(row["state"])
    state_allowed = SettlementState(state) in _SUBMITTABLE_STATES
    cutover_allowed = bool(getattr(cutover, "allow_redemption", False))
    fx_required = payout_asset == "pUSD"
    fx_allowed = (not fx_required) or isinstance(fx_classification, FXClassification)
    components = [
        _capability_component(
            "redeem_command_state",
            allowed=state_allowed,
            reason="allowed" if state_allowed else f"state_not_redeem_submittable:{state}",
            command_state=state,
        ),
        _capability_component(
            "payout_asset_fx_classification",
            allowed=fx_allowed,
            reason=(
                fx_classification.value
                if isinstance(fx_classification, FXClassification)
                else ("missing_pusd_fx_classification" if fx_required else f"not_required_for_{payout_asset}")
            ),
            payout_asset=payout_asset,
        ),
        _capability_component(
            "cutover_guard",
            allowed=cutover_allowed,
            reason=str(getattr(cutover, "block_reason", None) or ("allowed" if cutover_allowed else "blocked")),
            state=_enum_value(getattr(cutover, "state", None)),
        ),
    ]
    proof: dict[str, Any] = {
        "schema_version": 1,
        "action": "REDEEM",
        "intent_kind": "REDEEM",
        "mode": "redeem",
        "allowed": all(bool(component.get("allowed")) for component in components),
        "freshness_time": freshness_time,
        "command_id": str(row["command_id"]),
        "condition_id": str(row["condition_id"]),
        "market_id": str(row["market_id"]),
        "payout_asset": payout_asset,
        "components": components,
    }
    proof["capability_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return proof


def init_settlement_command_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SETTLEMENT_COMMAND_SCHEMA)


def request_redeem(
    condition_id: str,
    payout_asset: str,
    *,
    market_id: str | None = None,
    pusd_amount_micro: int | None = None,
    token_amounts: Mapping[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
    requested_at: datetime | str | None = None,
    fx_classification: FXClassification | None = None,
    winning_index_set: str | None = None,
) -> str:
    """Create a durable redeem intent and return its command id.

    winning_index_set: JSON-encoded uint256[] for CTF redeemPositions indexSets.
    For binary markets: '["2"]' = YES outcome won, '["1"]' = NO outcome won.
    NULL is valid for historical rows and callers that don't yet know the bin.
    V1 limitation: multi-bin (ranged market) encoding is not supported here;
    callers should pass None for non-binary markets until PR-I.5.b extends this.

    pUSD redemption/accounting is Q-FX-1 gated.  Legacy USDC.e payout is not
    silently promoted into pUSD accounting; it is recorded directly into
    ``REDEEM_REVIEW_REQUIRED`` for operator classification.
    """

    condition_id = _require_nonempty("condition_id", condition_id)
    market_id = _require_nonempty("market_id", market_id or condition_id)
    payout_asset = _normalize_payout_asset(payout_asset)
    if payout_asset == "pUSD":
        require_pusd_redemption_allowed(fx_classification)
    if pusd_amount_micro is not None and int(pusd_amount_micro) < 0:
        raise ValueError("pusd_amount_micro must be non-negative")

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_trade_connection_with_world

        conn = get_trade_connection_with_world()
    assert conn is not None
    init_settlement_command_schema(conn)

    existing = conn.execute(
        """
        SELECT command_id FROM settlement_commands
         WHERE condition_id = ?
           AND market_id = ?
           AND payout_asset = ?
           AND state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED')
         ORDER BY requested_at, command_id
         LIMIT 1
        """,
        (condition_id, market_id, payout_asset),
    ).fetchone()
    if existing is not None:
        if own_conn:
            conn.close()
        return str(existing["command_id"])

    command_id = uuid.uuid4().hex
    requested_at_s = _coerce_time(requested_at)
    state = (
        SettlementState.REDEEM_REVIEW_REQUIRED
        if payout_asset == "USDC_E"
        else SettlementState.REDEEM_INTENT_CREATED
    )
    error_payload = (
        {"reason": "legacy_usdc_e_payout_requires_operator_review"}
        if payout_asset == "USDC_E"
        else None
    )
    token_amounts_json = _json_dumps(dict(token_amounts or {}))
    try:
        with _savepoint(conn):
            conn.execute(
                """
                INSERT INTO settlement_commands (
                  command_id, state, condition_id, market_id, payout_asset,
                  pusd_amount_micro, token_amounts_json, winning_index_set,
                  requested_at, terminal_at, error_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    state.value,
                    condition_id,
                    market_id,
                    payout_asset,
                    int(pusd_amount_micro) if pusd_amount_micro is not None else None,
                    token_amounts_json,
                    winning_index_set,
                    requested_at_s,
                    requested_at_s if state in _TERMINAL_STATES else None,
                    _json_dumps(error_payload) if error_payload else None,
                ),
            )
            _append_event(
                conn,
                command_id,
                state.value,
                {
                    "condition_id": condition_id,
                    "market_id": market_id,
                    "payout_asset": payout_asset,
                    "pusd_amount_micro": pusd_amount_micro,
                    "token_amounts": dict(token_amounts or {}),
                    "winning_index_set": winning_index_set,
                    "error_payload": error_payload,
                },
                recorded_at=requested_at_s,
            )
        if own_conn:
            conn.commit()
        return command_id
    finally:
        if own_conn:
            conn.close()


@capability("on_chain_mutation", lease=True)
def submit_redeem(
    command_id: str,
    adapter: Any,
    ledger: Any,
    *,
    conn: sqlite3.Connection | None = None,
    submitted_at: datetime | str | None = None,
    fx_classification: FXClassification | None = None,
) -> SettlementResult:
    """Submit a pending redeem command through an adapter-like boundary.

    The durable ``REDEEM_SUBMITTED`` event is committed before adapter contact.
    If the adapter returns a tx hash, ``REDEEM_TX_HASHED`` becomes the recovery
    anchor; later ``reconcile_pending_redeems`` follows chain receipt truth.
    """
    from src.architecture.gate_runtime import check as _gate_runtime_check
    _gate_runtime_check("on_chain_mutation")

    _ = ledger  # R1 keeps the public seam; collateral accounting remains Q-FX gated.
    command_id = _require_nonempty("command_id", command_id)
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_trade_connection_with_world

        conn = get_trade_connection_with_world()
    assert conn is not None
    init_settlement_command_schema(conn)
    submitted_at_s = _coerce_time(submitted_at)

    try:
        row = _get_row(conn, command_id)
        state = SettlementState(row["state"])
        if state not in _SUBMITTABLE_STATES:
            raise SettlementCommandStateError(f"command {command_id} is not submittable from {state.value}")
        selected_fx_classification: FXClassification | None = None
        if row["payout_asset"] == "pUSD":
            selected_fx_classification = require_pusd_redemption_allowed(fx_classification)
        cutover = redemption_decision()
        if not cutover.allow_redemption:
            raise CutoverPending(cutover.block_reason or cutover.state.value)
        execution_capability = _build_redeem_execution_capability(
            row=row,
            cutover=cutover,
            fx_classification=selected_fx_classification,
            freshness_time=submitted_at_s,
        )
        with _savepoint(conn):
            _transition(
                conn,
                command_id,
                SettlementState.REDEEM_SUBMITTED,
                payload={
                    "condition_id": row["condition_id"],
                    "pre_side_effect": True,
                    "execution_capability": execution_capability,
                },
                submitted_at=submitted_at_s,
                recorded_at=submitted_at_s,
            )
        if own_conn:
            conn.commit()

        try:
            logger.debug(
                "[REDEEM_CTX] command_id=%s winning_index_set=%s (web3 wire pending PR-I.5.c)",
                command_id,
                row["winning_index_set"],
            )
            raw = adapter.redeem(row["condition_id"])
        except Exception as exc:  # preserve durable SUBMITTED before retry classification
            error_payload = {"exception_type": type(exc).__name__, "message": str(exc)}
            with _savepoint(conn):
                _transition(
                    conn,
                    command_id,
                    SettlementState.REDEEM_RETRYING,
                    payload=error_payload,
                    error_payload=error_payload,
                    recorded_at=_coerce_time(None),
                )
            if own_conn:
                conn.commit()
            return SettlementResult(command_id, SettlementState.REDEEM_RETRYING, error_payload=error_payload)

        raw_payload = _raw_dict(raw)
        if not _success(raw_payload):
            # SCAFFOLD §K.3 v5 (2026-05-16): when adapter returns the
            # REDEEM_DEFERRED_TO_R1 stub, route to REDEEM_OPERATOR_REQUIRED
            # (first-class operator-completion state) instead of generic
            # REVIEW_REQUIRED catch-all. Operator-completion CLI is
            # scripts/operator_record_redeem.py. Other errorCodes still
            # route to REDEEM_FAILED as a true terminal failure.
            stub_deferred = raw_payload.get("errorCode") == "REDEEM_DEFERRED_TO_R1"
            if stub_deferred:
                state_after = SettlementState.REDEEM_OPERATOR_REQUIRED
                terminal_flag = False  # operator CLI exits this state
            else:
                state_after = SettlementState.REDEEM_FAILED
                terminal_flag = True
            with _savepoint(conn):
                _transition(
                    conn,
                    command_id,
                    state_after,
                    payload=raw_payload,
                    error_payload=raw_payload,
                    terminal=terminal_flag,
                    recorded_at=_coerce_time(None),
                )
            if own_conn:
                conn.commit()
            # SCAFFOLD §K.3 v5 atomicity contract: alert fires AFTER the
            # savepoint+commit completes (best-effort, not part of transaction).
            # Heartbeat-sensor (Finding #10 path) picks up the
            # [REDEEM_OPERATOR_REQUIRED] prefix from logs/zeus-live.err.
            if stub_deferred:
                logger.warning(
                    "[REDEEM_OPERATOR_REQUIRED] command_id=%s condition_id=%s "
                    "action=run_operator_record_redeem details='Polymarket UI claim + "
                    "scripts/operator_record_redeem.py <condition_id> <tx_hash>'",
                    command_id, row["condition_id"],
                )
            return SettlementResult(command_id, state_after, raw_response=raw_payload, error_payload=raw_payload)

        tx_hash = _extract_tx_hash(raw_payload)
        block_number = _extract_int(raw_payload, "block_number", "blockNumber")
        confirmation_count = _extract_int(raw_payload, "confirmation_count", "confirmations") or 0
        state_after = SettlementState.REDEEM_TX_HASHED if tx_hash else SettlementState.REDEEM_REVIEW_REQUIRED
        with _savepoint(conn):
            _transition(
                conn,
                command_id,
                state_after,
                payload=raw_payload,
                tx_hash=tx_hash,
                block_number=block_number,
                confirmation_count=confirmation_count,
                error_payload=None if tx_hash else {"reason": "redeem_success_without_tx_hash", "raw": raw_payload},
                terminal=state_after in _TERMINAL_STATES,
                recorded_at=_coerce_time(None),
            )
        if own_conn:
            conn.commit()
        return SettlementResult(
            command_id,
            state_after,
            tx_hash=tx_hash,
            block_number=block_number,
            confirmation_count=confirmation_count,
            raw_response=raw_payload,
        )
    finally:
        if own_conn:
            conn.close()


def reconcile_pending_redeems(web3: Any, conn: sqlite3.Connection) -> list[SettlementResult]:
    """Follow chain receipts for tx-hashed redeem commands to terminal state."""

    init_settlement_command_schema(conn)
    rows = conn.execute(
        """
        SELECT * FROM settlement_commands
         WHERE state = ? AND tx_hash IS NOT NULL
         ORDER BY requested_at, command_id
        """,
        (SettlementState.REDEEM_TX_HASHED.value,),
    ).fetchall()
    results: list[SettlementResult] = []
    for row in rows:
        tx_hash = str(row["tx_hash"])
        receipt = _get_receipt(web3, tx_hash)
        if receipt is None:
            continue
        receipt_payload = _raw_dict(receipt)
        status = receipt_payload.get("status")
        block_number = _extract_int(receipt_payload, "block_number", "blockNumber")
        confirmation_count = _confirmation_count(web3, block_number)
        if status in {1, "1", True, "success", "SUCCESS"}:
            state_after = SettlementState.REDEEM_CONFIRMED
            error_payload = None
        elif status in {0, "0", False, "failed", "FAILED"}:
            state_after = SettlementState.REDEEM_FAILED
            error_payload = receipt_payload
        else:
            continue
        with _savepoint(conn):
            _transition(
                conn,
                str(row["command_id"]),
                state_after,
                payload=receipt_payload,
                block_number=block_number,
                confirmation_count=confirmation_count,
                error_payload=error_payload,
                terminal=True,
                recorded_at=_coerce_time(None),
            )
        results.append(
            SettlementResult(
                str(row["command_id"]),
                state_after,
                tx_hash=tx_hash,
                block_number=block_number,
                confirmation_count=confirmation_count,
                raw_response=receipt_payload,
                error_payload=error_payload,
            )
        )
    return results


def get_command(conn: sqlite3.Connection, command_id: str) -> dict[str, Any]:
    init_settlement_command_schema(conn)
    return dict(_get_row(conn, command_id))


def list_commands(conn: sqlite3.Connection, *, state: SettlementState | str | None = None) -> list[dict[str, Any]]:
    init_settlement_command_schema(conn)
    if state is None:
        rows = conn.execute("SELECT * FROM settlement_commands ORDER BY requested_at, command_id").fetchall()
    else:
        state_s = SettlementState(state).value
        rows = conn.execute(
            "SELECT * FROM settlement_commands WHERE state = ? ORDER BY requested_at, command_id",
            (state_s,),
        ).fetchall()
    return [dict(row) for row in rows]


def _get_row(conn: sqlite3.Connection, command_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM settlement_commands WHERE command_id = ?", (command_id,)).fetchone()
    if row is None:
        raise KeyError(command_id)
    return row


def _transition(
    conn: sqlite3.Connection,
    command_id: str,
    state: SettlementState,
    *,
    payload: Mapping[str, Any],
    tx_hash: str | None = None,
    block_number: int | None = None,
    confirmation_count: int | None = None,
    submitted_at: str | None = None,
    error_payload: Mapping[str, Any] | None = None,
    terminal: bool = False,
    recorded_at: str,
) -> None:
    terminal_at = recorded_at if terminal else None
    conn.execute(
        """
        UPDATE settlement_commands
           SET state = ?,
               tx_hash = COALESCE(?, tx_hash),
               block_number = COALESCE(?, block_number),
               confirmation_count = COALESCE(?, confirmation_count),
               submitted_at = COALESCE(?, submitted_at),
               terminal_at = COALESCE(?, terminal_at),
               error_payload = ?
         WHERE command_id = ?
        """,
        (
            state.value,
            tx_hash,
            block_number,
            confirmation_count,
            submitted_at,
            terminal_at,
            _json_dumps(error_payload) if error_payload is not None else None,
            command_id,
        ),
    )
    _append_event(conn, command_id, state.value, dict(payload), recorded_at=recorded_at)


def _atomic_transition(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    from_state: SettlementState | str,
    to_state: SettlementState | str,
    tx_hash: str | None = None,
    submitted_at: str | None = None,
    terminal_at: str | None = None,
    error_payload: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> bool:
    """SQLite-atomic conditional state transition with WHERE state guard.

    Returns True if the row was transitioned (cursor.rowcount == 1), False
    if the row was already in a different state (no UPDATE happened). The
    caller MUST check the return value before firing side effects (alerts,
    audit events) — see SCAFFOLD §K.3 v5 atomicity contract.

    Distinct from `_transition`:
      - `_transition` is Python-guard + SAVEPOINT semantics (caller pre-checks
        state then transitions). Used by submit_redeem internally.
      - `_atomic_transition` is SQL-guard via `WHERE state = ?`. Used by the
        operator CLI (scripts/operator_record_redeem.py) which races with the
        scheduler tick across processes — disjoint state guards (CLI on
        OPERATOR_REQUIRED, scheduler on _SUBMITTABLE_STATES) are race-free.

    Per SCAFFOLD §K.3 v5: if rowcount == 0, alert MUST NOT fire (no
    false-alert on failed transition). The event append also depends on
    successful UPDATE, so on rowcount == 0 nothing is appended.
    """
    from_value = from_state.value if isinstance(from_state, SettlementState) else from_state
    to_value = to_state.value if isinstance(to_state, SettlementState) else to_state
    cur = conn.execute(
        """
        UPDATE settlement_commands
           SET state = ?,
               tx_hash = COALESCE(?, tx_hash),
               submitted_at = COALESCE(?, submitted_at),
               terminal_at = COALESCE(?, terminal_at),
               error_payload = ?
         WHERE command_id = ?
           AND state = ?
        """,
        (
            to_value,
            tx_hash,
            submitted_at,
            terminal_at,
            _json_dumps(error_payload) if error_payload is not None else None,
            command_id,
            from_value,
        ),
    )
    transitioned = cur.rowcount == 1
    if transitioned and payload is not None:
        _append_event(
            conn,
            command_id,
            to_value,
            dict(payload),
            recorded_at=recorded_at or _coerce_time(None),
        )
    return transitioned


def _append_event(
    conn: sqlite3.Connection,
    command_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    recorded_at: str,
) -> None:
    payload_json = _json_dumps(payload)
    conn.execute(
        """
        INSERT INTO settlement_command_events (
          command_id, event_type, payload_hash, payload_json, recorded_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (command_id, event_type, _payload_hash(payload_json), payload_json, recorded_at),
    )


@contextlib.contextmanager
def _savepoint(conn: sqlite3.Connection) -> Iterator[None]:
    name = f"settlement_cmd_{uuid.uuid4().hex}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")


def _normalize_payout_asset(value: str) -> str:
    normalized = _require_nonempty("payout_asset", value).upper().replace(".", "_").replace("-", "_")
    if normalized in {"PUSD", "POLYMARKET_USD"}:
        asset = "pUSD"
    elif normalized in {"USDC", "USDC_POS"}:
        asset = "USDC"
    elif normalized in {"USDC_E", "USDCE", "USDC_BRIDGED"}:
        asset = "USDC_E"
    else:
        raise ValueError(f"unsupported payout_asset={value!r}; expected one of {sorted(PAYOUT_ASSETS)}")
    return asset


def _require_nonempty(name: str, value: str | None) -> str:
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
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _raw_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "items"):
        return dict(value.items())
    out: dict[str, Any] = {}
    for key in ("success", "tx_hash", "transaction_hash", "hash", "status", "blockNumber", "block_number"):
        if hasattr(value, key):
            out[key] = getattr(value, key)
    return out


def _success(raw: Mapping[str, Any]) -> bool:
    if "success" in raw:
        return raw.get("success") is True
    if "ok" in raw:
        return raw.get("ok") is True
    status = raw.get("status")
    return status in {"submitted", "SUBMITTED", "success", "SUCCESS", 1, "1", True}


def _extract_tx_hash(raw: Mapping[str, Any]) -> str | None:
    for key in ("tx_hash", "transaction_hash", "transactionHash", "hash"):
        value = raw.get(key)
        if value:
            return str(value)
    return None


def _extract_int(raw: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _get_receipt(web3: Any, tx_hash: str) -> Any | None:
    if web3 is None:
        return None
    eth = getattr(web3, "eth", web3)
    getter = getattr(eth, "get_transaction_receipt", None) or getattr(eth, "getTransactionReceipt", None)
    if getter is None:
        return None
    try:
        return getter(tx_hash)
    except Exception:
        return None


def _confirmation_count(web3: Any, block_number: int | None) -> int:
    if web3 is None or block_number is None:
        return 0
    eth = getattr(web3, "eth", web3)
    current = getattr(eth, "block_number", None)
    if current is None:
        current = getattr(eth, "blockNumber", None)
    try:
        return max(0, int(current) - int(block_number) + 1)
    except (TypeError, ValueError):
        return 0
