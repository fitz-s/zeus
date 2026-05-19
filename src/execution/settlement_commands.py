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
import os
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
  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_REVIEW_REQUIRED');

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
    # PR 3 (2026-05-19): idempotent ADD COLUMN migrations for new fields.
    # "duplicate column" is expected on already-migrated databases; ignore it.
    for alter_sql in [
        "ALTER TABLE settlement_commands ADD COLUMN polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit'",
        # PR 6 (2026-05-19)
        "ALTER TABLE settlement_commands ADD COLUMN zeus_submit_intent_time TEXT",
        "ALTER TABLE settlement_commands ADD COLUMN venue_ack_time TEXT",
        "ALTER TABLE settlement_commands ADD COLUMN clock_skew_estimate_ms_at_submit INTEGER",
    ]:
        try:
            conn.execute(alter_sql)
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                raise


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
    polymarket_end_anchor_source: str = "",
) -> str:
    """Create a durable redeem intent and return its command id.

    winning_index_set: JSON-encoded uint256[] for CTF redeemPositions indexSets.
    For binary markets: '["2"]' = YES outcome won, '["1"]' = NO outcome won.
    NULL is valid for historical rows and callers that don't yet know the bin.
    V1 limitation: multi-bin (ranged market) encoding is not supported here;
    callers should pass None for non-binary markets until PR-I.5.b extends this.

    This records intent only. pUSD redemption submission/accounting remains
    Q-FX-1 gated in submit_redeem(); a missing FX classification must not erase
    the durable command that tells the operator what work is pending. Legacy
    USDC.e payout is not silently promoted into pUSD accounting; it is recorded
    directly into ``REDEEM_REVIEW_REQUIRED`` for operator classification.
    """

    condition_id = _require_nonempty("condition_id", condition_id)
    market_id = _require_nonempty("market_id", market_id or condition_id)
    payout_asset = _normalize_payout_asset(payout_asset)
    if fx_classification is not None and not isinstance(fx_classification, FXClassification):
        raise TypeError(
            "pUSD redemption FX classification must be FXClassification, "
            f"got {type(fx_classification).__name__}"
        )
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
        SELECT command_id, state, winning_index_set FROM settlement_commands
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
        if winning_index_set is not None and existing["winning_index_set"] is None:
            requested_at_s = _coerce_time(requested_at)
            with _savepoint(conn):
                conn.execute(
                    """
                    UPDATE settlement_commands
                       SET winning_index_set = ?
                     WHERE command_id = ?
                       AND winning_index_set IS NULL
                    """,
                    (winning_index_set, existing["command_id"]),
                )
                _append_event(
                    conn,
                    str(existing["command_id"]),
                    "REDEEM_INDEX_SET_BACKFILLED",
                    {
                        "condition_id": condition_id,
                        "market_id": market_id,
                        "payout_asset": payout_asset,
                        "winning_index_set": winning_index_set,
                        "previous_state": existing["state"],
                    },
                    recorded_at=requested_at_s,
                )
            if own_conn:
                conn.commit()
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
                  requested_at, terminal_at, error_payload,
                  polymarket_end_anchor_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    polymarket_end_anchor_source or "gamma_explicit",
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
    # INV-37 (ATTACH guard): callers may pass an external trade connection that
    # lacks the world schema (e.g. main.py's scheduler conn opened via
    # get_trade_connection(write_class="live")).  Ensure world is ATTACHed
    # before the world.executable_market_snapshots query at line ~482 so that
    # negRisk snapshot lookup never raises OperationalError and silently routes
    # every redeem to REDEEM_NEGRISK_FACT_MISSING / REDEEM_OPERATOR_REQUIRED.
    from src.state.db import ZEUS_WORLD_DB_PATH as _WORLD_PATH
    _attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" not in _attached:
        try:
            conn.execute("ATTACH DATABASE ? AS world", (str(_WORLD_PATH),))
        except sqlite3.OperationalError as _att_exc:
            logger.warning("[SUBMIT_REDEEM_ATTACH_WORLD_FAILED] exc=%r", _att_exc)
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
            # PR-I.5.c (2026-05-18): parse JSON-encoded winning_index_set
            # ('["2"]' for YES win, '["1"]' for NO win) into list[int] for the
            # adapter. None means harvester did not derive the winning bin —
            # adapter will return REDEEM_INDEX_SETS_MISSING (when autonomous
            # ON) or the stub (when OFF). Either path is safe; both surface a
            # well-typed errorCode rather than silently calling with wrong arg.
            raw_winning_index_set = row["winning_index_set"]
            parsed_index_sets: list[int] | None
            if raw_winning_index_set is None:
                parsed_index_sets = None
            else:
                try:
                    decoded = json.loads(raw_winning_index_set)
                    # Validate decoded is a non-empty list of integer-like
                    # entries before iteration. A JSON string (e.g. "2") or
                    # object would iterate characters/keys and produce
                    # silently wrong index_sets.
                    if not isinstance(decoded, list) or not decoded:
                        raise ValueError(
                            f"winning_index_set must be a non-empty JSON array, got {type(decoded).__name__}: {decoded!r}"
                        )
                    parsed_index_sets = [int(x) for x in decoded]
                except Exception as parse_exc:
                    logger.warning(
                        "[REDEEM_INDEX_SET_PARSE_FAILED] command_id=%s raw=%r exc=%s",
                        command_id, raw_winning_index_set, parse_exc,
                    )
                    parsed_index_sets = None
            logger.debug(
                "[REDEEM_CTX] command_id=%s winning_index_set=%s parsed=%s",
                command_id,
                raw_winning_index_set,
                parsed_index_sets,
            )

            # negRisk routing: look up neg_risk flag + token IDs from world
            # snapshot table. get_trade_connection_with_world() ATTACHes the
            # world schema so the query MUST use the qualified name
            # world.executable_market_snapshots. The main connection is
            # zeus_trades.db; unqualified reads would hit any legacy ghost row
            # in the trade DB, not the authoritative world snapshot.
            # (Thread 1 fix: qualify table as world.executable_market_snapshots)
            #
            # Topology law (topology.yaml:4193): neg-risk facts may not be
            # guessed; a missing snapshot row FAILS CLOSED by assigning raw to
            # REDEEM_NEGRISK_FACT_MISSING and skipping the adapter call.
            # (Thread 3 fix: fail-closed instead of defaulting is_neg_risk=False)
            amount_per_slot: int | None = None
            try:
                neg_risk_row = conn.execute(
                    """
                    SELECT neg_risk, yes_token_id, no_token_id
                      FROM world.executable_market_snapshots
                     WHERE condition_id = ?
                     ORDER BY captured_at DESC
                     LIMIT 1
                    """,
                    (row["condition_id"],),
                ).fetchone()
            except Exception as snap_exc:
                logger.warning(
                    "[REDEEM_NEGRISK_SNAPSHOT_LOOKUP_FAILED] command_id=%s exc=%s",
                    command_id, snap_exc,
                )
                neg_risk_row = None
            if neg_risk_row is None:
                # Fail-closed: topology.yaml:4193 law forbids guessing neg-risk
                # facts. An absent snapshot row means the world DB has not
                # recorded authority data for this market; proceeding with
                # is_neg_risk=False would silently route negRisk markets to the
                # standard CTF path and produce a zero-payout redeem (Karachi
                # failure mode). Short-circuit: assign raw directly so the
                # existing error-code router below transitions to
                # REDEEM_OPERATOR_REQUIRED (REDEEM_NEGRISK_FACT_MISSING is in
                # _OPERATOR_REVIEW_ERRORCODES). No adapter call is made.
                logger.warning(
                    "[REDEEM_NEGRISK_FACT_MISSING] command_id=%s condition_id=%s "
                    "action=operator_must_populate_world_snapshot",
                    command_id, row["condition_id"],
                )
                raw = {
                    "success": False,
                    "errorCode": "REDEEM_NEGRISK_FACT_MISSING",
                    "errorMessage": (
                        f"no snapshot row in world.executable_market_snapshots "
                        f"for condition_id={row['condition_id']!r}; "
                        "cannot determine neg_risk without authority data "
                        "(topology.yaml:4193)"
                    ),
                    "condition_id": row["condition_id"],
                }
            else:
                # neg_risk_row is present: extract neg_risk flag + token IDs.
                _nr_val = (
                    neg_risk_row["neg_risk"]
                    if hasattr(neg_risk_row, "keys")
                    else neg_risk_row[0]
                )
                is_neg_risk: bool = bool(_nr_val)
                if is_neg_risk and parsed_index_sets and row["token_amounts_json"]:
                    try:
                        token_amounts: dict = json.loads(row["token_amounts_json"])
                        # winning_index_set=[2]=YES uses yes_token_id; [1]=NO uses no_token_id
                        winning_slot = parsed_index_sets[0]
                        _yes = (
                            neg_risk_row["yes_token_id"]
                            if hasattr(neg_risk_row, "keys")
                            else neg_risk_row[1]
                        )
                        _no = (
                            neg_risk_row["no_token_id"]
                            if hasattr(neg_risk_row, "keys")
                            else neg_risk_row[2]
                        )
                        winning_token_id: str | None
                        if winning_slot == 2:
                            winning_token_id = str(_yes) if _yes else None
                        elif winning_slot == 1:
                            winning_token_id = str(_no) if _no else None
                        else:
                            winning_token_id = None
                        if winning_token_id and winning_token_id in token_amounts:
                            from decimal import Decimal, ROUND_HALF_UP
                            amount_per_slot = int(
                                (Decimal(str(token_amounts[winning_token_id])) * Decimal(1_000_000))
                                .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                            )
                        elif len(token_amounts) == 1:
                            # Single-key map: use the only entry (binary market)
                            from decimal import Decimal, ROUND_HALF_UP
                            amount_per_slot = int(
                                (Decimal(str(next(iter(token_amounts.values())))) * Decimal(1_000_000))
                                .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                            )
                    except Exception as amt_exc:
                        logger.warning(
                            "[REDEEM_NEGRISK_AMOUNT_PARSE_FAILED] command_id=%s exc=%s",
                            command_id, amt_exc,
                        )
                logger.debug(
                    "[REDEEM_NEGRISK_CTX] command_id=%s is_neg_risk=%s amount_per_slot=%s",
                    command_id, is_neg_risk, amount_per_slot,
                )
                raw = adapter.redeem(
                    row["condition_id"],
                    index_sets=parsed_index_sets,
                    neg_risk=is_neg_risk,
                    amount_per_slot=amount_per_slot,
                )
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
            # scripts/operator_record_redeem.py.
            #
            # PR-I.5.c (2026-05-18): extend the operator-required set to cover
            # structural pre-flight failures that are not chain-submission errors
            # and should not be terminally classified as REDEEM_FAILED:
            #   - REDEEM_INDEX_SETS_MISSING / REDEEM_INDEX_SET_PARSE_FAILED:
            #     missing winning-bin data; harvester must repopulate before retry.
            #   - REDEEM_GAS_ESTIMATE_REVERTED: on-chain revert (already redeemed,
            #     wrong index_sets, no balance) — operator must inspect chain state.
            #   - REDEEM_CALLDATA_BUILD_FAILED: malformed condition_id or index_sets;
            #     input repair required before re-run.
            #   - REDEEM_SIGNER_FUNDER_MISMATCH: EOA != funder (Safe deployment);
            #     operator must configure a matching EOA funder.
            #   - REDEEM_WRONG_CHAIN: chain_id != 137; config repair required.
            # These are operator-review states, not terminal failures.
            _OPERATOR_REVIEW_ERRORCODES = frozenset({
                "REDEEM_DEFERRED_TO_R1",
                "REDEEM_INDEX_SETS_MISSING",
                "REDEEM_INDEX_SET_PARSE_FAILED",
                "REDEEM_GAS_ESTIMATE_REVERTED",
                "REDEEM_CALLDATA_BUILD_FAILED",
                "REDEEM_SIGNER_FUNDER_MISMATCH",
                "REDEEM_WRONG_CHAIN",
                # Safe v1.3.0 execTransaction wrap codes (Option A, 2026-05-19)
                "REDEEM_SAFE_VERSION_UNSUPPORTED",
                "REDEEM_SAFE_OWNER_MISMATCH",
                "REDEEM_EOA_MATIC_INSUFFICIENT",
                # negRisk adapter requires token balance (in micro-units).
                # Missing data → operator must inspect token_amounts_json /
                # executable_market_snapshots before retry. Not terminal.
                "REDEEM_NEGRISK_AMOUNT_MISSING",
                # Missing world snapshot row for condition_id: topology.yaml:4193
                # forbids guessing neg-risk facts; operator must populate
                # world.executable_market_snapshots before retry.
                "REDEEM_NEGRISK_FACT_MISSING",
                # Design intent: dry-run routes to OPERATOR_REQUIRED so the
                # operator reviews the Tenderly trace before clearing to live
                # broadcast.  This is the intended smoke-gate for first-run
                # validation, not a transient error state.
                "REDEEM_DRY_RUN_LOGGED",
            })
            error_code = raw_payload.get("errorCode")
            stub_deferred = error_code in _OPERATOR_REVIEW_ERRORCODES
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


def _lookup_market_neg_risk_authoritative(
    conn: sqlite3.Connection, condition_id: str
) -> Optional[bool]:
    """Return True/False if neg_risk is known, or None if all sources are unavailable.

    Three-tier authoritative lookup — fail-closed contract:
      Tier 1 — world.executable_market_snapshots via ATTACH (primary source).
      Tier 2 — main executable_market_snapshots on the active conn (zeus_trades.db
               already has 478 rows; guards against empty world.db).
      Tier 3 — Gamma CLOB API https://clob.polymarket.com/markets/{condition_id}
               (5-second timeout, reads .neg_risk field).

    Returns None (NOT False) when all three tiers fail — the caller MUST defer the
    terminal transition rather than silently marking REDEEM_CONFIRMED on an unknown
    market type.  None means "unknown", False means "confirmed standard CTF".
    """
    import httpx
    from src.state.db import ZEUS_WORLD_DB_PATH as _WORLD_PATH

    # --- Tier 1: world.executable_market_snapshots via ATTACH ---
    _attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" not in _attached:
        try:
            conn.execute("ATTACH DATABASE ? AS world", (str(_WORLD_PATH),))
        except sqlite3.OperationalError as _att_exc:
            logger.warning("[RECONCILE_REDEEM_ATTACH_WORLD_FAILED] exc=%r", _att_exc)
    # Attempt the query only if world schema is now attached
    _attached_now = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in _attached_now:
        try:
            neg_risk_row = conn.execute(
                """
                SELECT neg_risk
                  FROM world.executable_market_snapshots
                 WHERE condition_id = ?
                 ORDER BY captured_at DESC
                 LIMIT 1
                """,
                (condition_id,),
            ).fetchone()
            if neg_risk_row is not None:
                _nr_val = neg_risk_row["neg_risk"] if hasattr(neg_risk_row, "keys") else neg_risk_row[0]
                return bool(_nr_val)
            # Row not found in world.db — fall through to Tier 2
            logger.debug(
                "[RECONCILE_REDEEM_NEGRISK_WORLD_MISS] condition_id=%s — world.db has no row; trying trades.db",
                condition_id,
            )
        except Exception as snap_exc:
            logger.warning(
                "[RECONCILE_REDEEM_NEGRISK_WORLD_QUERY_FAILED] condition_id=%s exc=%s",
                condition_id, snap_exc,
            )

    # --- Tier 2: main (zeus_trades.db) executable_market_snapshots ---
    try:
        trades_row = conn.execute(
            """
            SELECT neg_risk
              FROM executable_market_snapshots
             WHERE condition_id = ?
             ORDER BY captured_at DESC
             LIMIT 1
            """,
            (condition_id,),
        ).fetchone()
        if trades_row is not None:
            _nr_val2 = trades_row["neg_risk"] if hasattr(trades_row, "keys") else trades_row[0]
            logger.info(
                "[RECONCILE_REDEEM_NEGRISK_TRADES_HIT] condition_id=%s neg_risk=%s",
                condition_id, _nr_val2,
            )
            return bool(_nr_val2)
        logger.debug(
            "[RECONCILE_REDEEM_NEGRISK_TRADES_MISS] condition_id=%s — trades.db has no row; trying Gamma",
            condition_id,
        )
    except Exception as trades_exc:
        logger.warning(
            "[RECONCILE_REDEEM_NEGRISK_TRADES_QUERY_FAILED] condition_id=%s exc=%s",
            condition_id, trades_exc,
        )

    # --- Tier 3: Gamma CLOB API ---
    gamma_url = f"https://clob.polymarket.com/markets/{condition_id}"
    try:
        resp = httpx.get(gamma_url, timeout=5.0)
        resp.raise_for_status()
        payload = resp.json()
        if "neg_risk" in payload:
            gamma_val = bool(payload["neg_risk"])
            logger.info(
                "[RECONCILE_REDEEM_NEGRISK_GAMMA_HIT] condition_id=%s neg_risk=%s",
                condition_id, gamma_val,
            )
            return gamma_val
        logger.warning(
            "[RECONCILE_REDEEM_NEGRISK_GAMMA_NO_FIELD] condition_id=%s response keys=%s",
            condition_id, list(payload.keys()),
        )
    except Exception as gamma_exc:
        logger.warning(
            "[RECONCILE_REDEEM_NEGRISK_GAMMA_FAILED] condition_id=%s exc=%s",
            condition_id, gamma_exc,
        )

    # All three tiers exhausted — return None (fail-closed; caller must defer)
    logger.warning(
        "[RECONCILE_REDEEM_NEGRISK_ALL_SOURCES_FAILED] condition_id=%s — "
        "world.db, trades.db, and Gamma all unavailable; returning None (fail-closed)",
        condition_id,
    )
    return None


def reconcile_pending_redeems(web3: Any, conn: sqlite3.Connection) -> list[SettlementResult]:
    """Follow chain receipts for tx-hashed redeem commands to terminal state."""

    from src.venue.polymarket_v2_adapter import (
        POLYGON_CTF_ADDRESS,
        POLYGON_NEGRISK_ADAPTER_ADDRESS,
    )

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
            # Antibody guard: if this is a negRisk market but tx.to is Standard CTF,
            # the redeem went to the wrong adapter and yielded 0 payout.  Do NOT
            # mark terminal — reset to REDEEM_OPERATOR_REQUIRED + clear tx_hash so
            # reseat_stub_deferred_rows_for_autonomous_retry promotes it back to
            # REDEEM_RETRYING and the submitter rebuilds via NegRiskAdapter.
            # Root cause: Karachi c8c220f5 tx 0x0c85d9… mined to Standard CTF.
            tx_to = (receipt_payload.get("to") or "").lower()
            is_negrisk_market = _lookup_market_neg_risk_authoritative(
                conn, str(row["condition_id"])
            )
            if is_negrisk_market is None:
                # Unknown — all three lookup sources failed (world.db empty, trades.db miss,
                # Gamma unreachable).  Defer terminal transition; will retry next tick.
                logger.warning(
                    "[RECONCILE_REDEEM_NEGRISK_UNKNOWN] command_id=%s condition_id=%s "
                    "— deferring transition; will retry next tick",
                    row["command_id"], row["condition_id"],
                )
                continue
            if is_negrisk_market and tx_to == POLYGON_CTF_ADDRESS.lower():
                misroute_error = {
                    "errorCode": "REDEEM_NEGRISK_MISROUTED",
                    "wrong_adapter": tx_to,
                    "expected": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                    "previous_tx_hash": tx_hash,
                }
                logger.warning(
                    "[REDEEM_NEGRISK_MISROUTED] command_id=%s condition_id=%s "
                    "tx_hash=%s wrong_adapter=%s expected=%s — resetting to "
                    "REDEEM_OPERATOR_REQUIRED for autonomous retry via correct adapter",
                    row["command_id"], row["condition_id"], tx_hash,
                    tx_to, POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                )
                with _savepoint(conn):
                    # _transition uses COALESCE so it cannot clear tx_hash.
                    # Explicit NULL update here ensures the reseat allowlist sees
                    # a clean row (no stale hash that would re-queue the bad tx).
                    conn.execute(
                        """
                        UPDATE settlement_commands
                           SET state = ?,
                               tx_hash = NULL,
                               terminal_at = NULL,
                               error_payload = ?
                         WHERE command_id = ?
                        """,
                        (
                            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
                            _json_dumps(misroute_error),
                            str(row["command_id"]),
                        ),
                    )
                    _append_event(
                        conn,
                        str(row["command_id"]),
                        SettlementState.REDEEM_OPERATOR_REQUIRED.value,
                        {**receipt_payload, **misroute_error},
                        recorded_at=_coerce_time(None),
                    )
                results.append(
                    SettlementResult(
                        str(row["command_id"]),
                        SettlementState.REDEEM_OPERATOR_REQUIRED,
                        tx_hash=None,
                        error_payload={"errorCode": "REDEEM_NEGRISK_MISROUTED"},
                        raw_response=receipt_payload,
                    )
                )
                continue
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


def _jsonable(o: Any) -> Any:
    """Coerce HexBytes / bytes / AttributeDict / similar for JSON serialization.

    Receipt payloads from web3.eth.get_transaction_receipt contain HexBytes
    (blockHash, transactionHash, logsBloom, logs[].topics, etc.) AND
    AttributeDict (web3 mapping for nested log entries) that the stdlib
    JSONEncoder rejects. Without this default-hook the reconcile_pending_redeems
    path crashes with TypeError, leaving rows stuck in REDEEM_TX_HASHED.
    """
    # HexBytes inherits from bytes and has .hex() method; bytes also has .hex()
    if isinstance(o, (bytes, bytearray)):
        h = o.hex()
        return h if h.startswith("0x") else "0x" + h
    # Mapping-like (web3 AttributeDict) — coerce to plain dict so JSON encoder
    # recurses normally. Required because in web3 7.x / Python 3.14 AttributeDict
    # is not a strict dict subclass, so json's internal isinstance(o, dict) check
    # falls through to this default hook. (2026-05-19 live reconciler error:
    # "Object of type AttributeDict is not JSON serializable when serializing
    # list item 0 when serializing dict item 'logs'".)
    if hasattr(o, "keys") and callable(getattr(o, "keys", None)):
        return dict(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_jsonable)


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


# Error codes ALWAYS auto-retry-eligible once autonomous mode is enabled:
# "on-chain action did NOT happen, just stubbed". REDEEM_DEFERRED_TO_R1 is
# the legacy stub from the pre-PR-#183 era.
_AUTONOMOUS_RETRY_ERRORCODES_ALWAYS: frozenset[str] = frozenset({
    "REDEEM_DEFERRED_TO_R1",
})

# Error codes that require DRY_RUN to be OFF before retry. REDEEM_DRY_RUN_LOGGED
# rows ARE eligible — but only after operator review during dry-run smoke. If
# reseat'ed while DRY_RUN is still ON, the adapter dry-run branch returns
# DRY_RUN_LOGGED again, creating an infinite reseat→submit→DRY_RUN loop that
# defeats the operator-review-before-broadcast gate. Anchor: Codex P2 + Copilot
# review on PR #186 caught this; antibody covers the dry-run gating.
_AUTONOMOUS_RETRY_ERRORCODES_REQUIRE_LIVE: frozenset[str] = frozenset({
    "REDEEM_DRY_RUN_LOGGED",
})


def _autonomous_dry_run_enabled() -> bool:
    return os.environ.get(
        "ZEUS_AUTONOMOUS_REDEEM_DRY_RUN", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def reseat_stub_deferred_rows_for_autonomous_retry(conn: sqlite3.Connection) -> int:
    """Promote rows parked in REDEEM_OPERATOR_REQUIRED whose errorCode is
    auto-retry-eligible back to REDEEM_RETRYING so the submitter picks them
    up. Two-tier allowlist:
      * _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS — retry whenever autonomous mode
        is ON (legacy stubs that never produced a real tx).
      * _AUTONOMOUS_RETRY_ERRORCODES_REQUIRE_LIVE — retry only when DRY_RUN is
        OFF (DRY_RUN_LOGGED would otherwise infinite-loop through the dry-run
        branch and defeat the operator smoke gate).
    Reason: REDEEM_OPERATOR_REQUIRED was designed for stub-era; post-PR-#183
    the autonomous path obviates manual operator action for these cases."""
    autonomous_enabled = os.environ.get(
        "ZEUS_AUTONOMOUS_REDEEM_ENABLED", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    if not autonomous_enabled:
        return 0
    dry_run_enabled = _autonomous_dry_run_enabled()
    rows = conn.execute(
        "SELECT command_id, error_payload FROM settlement_commands WHERE state = ?",
        (SettlementState.REDEEM_OPERATOR_REQUIRED.value,),
    ).fetchall()
    promoted = 0
    for row in rows:
        try:
            err = json.loads(row["error_payload"] or "{}")
        except json.JSONDecodeError:
            continue
        err_code = err.get("errorCode")
        if err_code in _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS:
            eligible = True
        elif err_code in _AUTONOMOUS_RETRY_ERRORCODES_REQUIRE_LIVE:
            # DRY_RUN_LOGGED: only reseat once dry-run gate is lifted, otherwise
            # the adapter dry-run branch returns DRY_RUN_LOGGED again → loop.
            eligible = not dry_run_enabled
        else:
            eligible = False
        if eligible:
            cur = conn.execute(
                "UPDATE settlement_commands SET state = ?, terminal_at = NULL"
                " WHERE command_id = ? AND state = ?",
                (
                    SettlementState.REDEEM_RETRYING.value,
                    row["command_id"],
                    SettlementState.REDEEM_OPERATOR_REQUIRED.value,
                ),
            )
            if cur.rowcount == 1:
                _append_event(
                    conn,
                    row["command_id"],
                    SettlementState.REDEEM_RETRYING.value,
                    {
                        "reason": "stub_deferred_reseat_autonomous",
                        "prior_errorcode": err_code,
                    },
                    recorded_at=_coerce_time(None),
                )
                promoted += 1
    return promoted
