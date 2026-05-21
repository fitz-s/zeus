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
  autoretry_eligible INTEGER NOT NULL DEFAULT 0 CHECK (autoretry_eligible IN (0, 1)),
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


@dataclass(frozen=True)
class AnchorSourceTrust:
    """Report-facing trust classification for polymarket_end_anchor_source."""

    source: str
    evidence_class: str
    report_trust: str
    verified: bool


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


def classify_polymarket_end_anchor_source(source: Any) -> AnchorSourceTrust:
    """Classify anchor-source provenance for settlement/redeem reports.

    `unknown_legacy` is an honest historical sentinel, not verified evidence.
    Reports must preserve the row but exclude it from verified anchor counts.
    """

    source_s = str(source or "").strip() or "unknown_legacy"
    source_l = source_s.lower()
    if source_l == "unknown_legacy":
        return AnchorSourceTrust(
            source=source_s,
            evidence_class="unknown_legacy",
            report_trust="exclude_from_verified_anchor_evidence",
            verified=False,
        )
    if source_l == "gamma_explicit":
        return AnchorSourceTrust(
            source=source_s,
            evidence_class="gamma_explicit",
            report_trust="verified_market_time_anchor",
            verified=True,
        )
    if source_l == "f1_12z_fallback":
        return AnchorSourceTrust(
            source=source_s,
            evidence_class="calendar_fallback",
            report_trust="degraded_report_only",
            verified=False,
        )
    if source_l.startswith("clob"):
        return AnchorSourceTrust(
            source=source_s,
            evidence_class="clob_derived",
            report_trust="verified_clob_anchor",
            verified=True,
        )
    if source_l.startswith("chain"):
        return AnchorSourceTrust(
            source=source_s,
            evidence_class="chain_derived",
            report_trust="verified_chain_anchor",
            verified=True,
        )
    return AnchorSourceTrust(
        source=source_s,
        evidence_class="unknown_untrusted",
        report_trust="exclude_from_verified_anchor_evidence",
        verified=False,
    )


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


class SettlementSchemaNotReadyError(RuntimeError):
    """Raised by assert_settlement_schema_ready when the schema has not been
    initialized on the connection.  Hot-path callers must not run DDL;
    call ensure_settlement_schema_ready(conn) at boot before entering the
    live scheduler loop."""


def ensure_settlement_schema_ready(conn: sqlite3.Connection) -> None:
    """Boot/migration path: create tables, run idempotent ALTER migrations.

    Must be called once at daemon startup on the shared trade connection,
    BEFORE any hot-path calls to request_redeem / submit_redeem /
    reconcile_pending_redeems. Hot-path callers use assert_settlement_schema_ready
    (PRAGMA-only check, no DDL) to avoid schema-lock and transaction-boundary
    risk on every tick.
    """
    conn.executescript(SETTLEMENT_COMMAND_SCHEMA)
    # PR 3 (2026-05-19): idempotent ADD COLUMN migrations for new fields.
    # "duplicate column" is expected on already-migrated databases; ignore it.
    for alter_sql in [
        # codereview-may19 P1-3: do NOT fabricate 'gamma_explicit' for historical
        # rows during migration. Historical rows whose authority chain was never
        # captured must carry the explicit "unknown_legacy" sentinel so
        # downstream causal-evidence consumers cannot mistake them for rows
        # whose anchor source was actually verified at write time. Live callers
        # (request_redeem) are required to pass a non-legacy value explicitly.
        "ALTER TABLE settlement_commands ADD COLUMN polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'unknown_legacy'",
        # PR 6 (2026-05-19)
        "ALTER TABLE settlement_commands ADD COLUMN zeus_submit_intent_time TEXT",
        "ALTER TABLE settlement_commands ADD COLUMN venue_ack_time TEXT",
        "ALTER TABLE settlement_commands ADD COLUMN clock_skew_estimate_ms_at_submit INTEGER",
        # P1-3 live release proof: OPERATOR_REQUIRED is a manual state by
        # default. Autonomous reseat requires an explicit row-level marker so
        # scheduler policy no longer derives authority from the state name.
        "ALTER TABLE settlement_commands ADD COLUMN autoretry_eligible INTEGER NOT NULL DEFAULT 0 CHECK (autoretry_eligible IN (0, 1))",
    ]:
        try:
            conn.execute(alter_sql)
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    # codereview-may19 P1-3 / Codex P1: backfill correction for v12-era rows.
    # The previous ALTER (DEFAULT 'gamma_explicit') stamped legacy rows whose
    # anchor source was never captured with a fabricated authority label.
    # The DEFAULT change above only protects FUTURE rows; existing rows still
    # carry the fabricated 'gamma_explicit'. Convert them to 'unknown_legacy'
    # so downstream causal-evidence consumers can distinguish "verified
    # Gamma-explicit anchor" from "unknown legacy fabrication".
    #
    # Idempotency: tracked via a dedicated settlement_schema_migrations table
    # (NOT PRAGMA user_version — that is owned by db.py's init_schema and
    # collides with SCHEMA_VERSION=13). A single row with key 'v13_gamma_backfill'
    # marks completion; subsequent boots skip. Real live callers passing an
    # explicit 'gamma_explicit' value (Gamma's actual endDate as the verified
    # anchor) write rows AFTER this boot, so the one-shot does not corrupt
    # verified rows.
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settlement_schema_migrations (
              migration_key TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        already_run = conn.execute(
            "SELECT 1 FROM settlement_schema_migrations WHERE migration_key = 'v13_gamma_backfill'"
        ).fetchone()
    except Exception:
        already_run = True  # fail-safe: if we can't check, skip

    if not already_run:
        try:
            conn.execute(
                "UPDATE settlement_commands SET polymarket_end_anchor_source = 'unknown_legacy' "
                "WHERE polymarket_end_anchor_source = 'gamma_explicit'"
            )
            conn.execute(
                "INSERT OR IGNORE INTO settlement_schema_migrations (migration_key) "
                "VALUES ('v13_gamma_backfill')"
            )
        except Exception as exc:
            logger.warning(
                "[V13_BACKFILL_GAMMA_EXPLICIT_FAILED] exc=%s; legacy rows may "
                "retain fabricated authority. Investigate before next boot.",
                exc,
            )


# Backward-compatibility alias: db.py and external callers that import this
# name continue to work. New code should call ensure_settlement_schema_ready
# at boot and assert_settlement_schema_ready at hot-path sites.
init_settlement_command_schema = ensure_settlement_schema_ready


# Expected columns after ensure_settlement_schema_ready has run.
_REQUIRED_SETTLEMENT_COLUMNS: frozenset[str] = frozenset({
    "command_id", "state", "condition_id", "market_id", "payout_asset",
    "pusd_amount_micro", "token_amounts_json", "winning_index_set",
    "tx_hash", "block_number", "confirmation_count",
    "requested_at", "submitted_at", "terminal_at", "error_payload",
    "polymarket_end_anchor_source", "autoretry_eligible",
})


def assert_settlement_schema_ready(conn: sqlite3.Connection) -> None:
    """Hot-path assertion: verify the schema is present via PRAGMA (no DDL).

    Raises SettlementSchemaNotReadyError if settlement_commands is missing or
    lacks required columns.  Fast (single PRAGMA query); never runs executescript
    or ALTER TABLE — safe on read-only connections and inside open transactions.

    Usage: call at the top of request_redeem / submit_redeem /
    reconcile_pending_redeems / get_command / list_commands instead of
    init_settlement_command_schema to avoid DDL in the live hot path.
    """
    try:
        rows = conn.execute(
            "PRAGMA table_info(settlement_commands)"
        ).fetchall()
    except Exception as exc:
        raise SettlementSchemaNotReadyError(
            f"PRAGMA table_info(settlement_commands) failed: {exc}; "
            "call ensure_settlement_schema_ready at boot"
        ) from exc
    if not rows:
        raise SettlementSchemaNotReadyError(
            "settlement_commands table not found; "
            "call ensure_settlement_schema_ready at boot"
        )
    present = {row[1] if isinstance(row, (list, tuple)) else row["name"] for row in rows}
    missing = _REQUIRED_SETTLEMENT_COLUMNS - present
    if missing:
        raise SettlementSchemaNotReadyError(
            f"settlement_commands missing columns {sorted(missing)}; "
            "call ensure_settlement_schema_ready at boot to run migrations"
        )


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
        from src.state.db import get_trade_connection_with_world_required

        conn = get_trade_connection_with_world_required(write_class="live")
    assert conn is not None
    assert_settlement_schema_ready(conn)

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
                    # codereview-may19 P1-3: live-created rows must supply an
                    # explicit non-empty anchor source. Empty string falls
                    # through to "unknown_legacy" — the same sentinel migrated
                    # rows carry — so we never fabricate "gamma_explicit"
                    # provenance for rows whose authority chain wasn't recorded.
                    # Real callers thread the actual source through the keyword
                    # arg; tests / historical paths get "unknown_legacy".
                    polymarket_end_anchor_source or "unknown_legacy",
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
        from src.state.db import get_trade_connection_with_world_required

        conn = get_trade_connection_with_world_required(write_class="live")
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
            raise SettlementCommandStateError(
                "submit_redeem requires world ATTACH before live side-effect boundary"
            ) from _att_exc
    assert_settlement_schema_ready(conn)
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
        # submit_redeem is the on-chain side-effect boundary. REDEEM_SUBMITTED
        # must be durable before any adapter contact even when the caller passed
        # an external connection; otherwise crash/rollback can erase the local
        # anchor after a Safe/NegRisk tx was broadcast.
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
            # snapshot table. Live callers must provide or open a connection
            # with world ATTACHed, so the query MUST use the qualified name
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
                # Snapshot miss — try live Gamma authority fallback before
                # failing closed. The topology law (yaml:4193) forbids GUESSING
                # neg-risk facts; the public Gamma CLOB endpoint IS the canonical
                # authority for this fact (it is what every other reader in this
                # module already consults via _lookup_market_neg_risk_authoritative
                # Tier 3). The snapshot table is a populated CACHE of that
                # authority, not a separate source of truth — when the cache
                # row is absent (legacy positions entered before the snapshot
                # cycle existed; world DB never re-seeded after schema migration),
                # consulting the live authority is structurally consistent.
                #
                # Karachi failure mode (2026-05-19): in-flight redeem positions
                # 7557a029 + e914a28a + c8c220f5 were entered before the
                # capture_executable_market_snapshot side-effect path existed;
                # snapshot table held 0 rows; submitter could not advance and
                # latched OPERATOR_REQUIRED until structural fix.
                _gamma_row = _fetch_neg_risk_from_gamma_for_submitter(row["condition_id"])
                if _gamma_row is not None:
                    logger.info(
                        "[REDEEM_NEGRISK_GAMMA_FALLBACK] command_id=%s condition_id=%s "
                        "neg_risk=%s yes_token_id=%s no_token_id=%s",
                        command_id,
                        row["condition_id"],
                        _gamma_row["neg_risk"],
                        _gamma_row.get("yes_token_id"),
                        _gamma_row.get("no_token_id"),
                    )
                    neg_risk_row = _gamma_row
            if neg_risk_row is None:
                # Both snapshot AND live Gamma exhausted — fail closed.
                # No adapter call is made; existing error-code router below
                # transitions to REDEEM_OPERATOR_REQUIRED.
                logger.warning(
                    "[REDEEM_NEGRISK_FACT_MISSING] command_id=%s condition_id=%s "
                    "action=operator_must_populate_world_snapshot snapshot_miss=1 gamma_miss=1",
                    command_id, row["condition_id"],
                )
                raw = {
                    "success": False,
                    "errorCode": "REDEEM_NEGRISK_FACT_MISSING",
                    "errorMessage": (
                        f"no snapshot row in world.executable_market_snapshots "
                        f"and live Gamma fallback also failed "
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
            autoretry_eligible = _error_code_can_be_marked_autoretryable(error_code)
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
                    autoretry_eligible=autoretry_eligible,
                    terminal=terminal_flag,
                    recorded_at=_coerce_time(None),
                )
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


def _fetch_neg_risk_from_gamma_for_submitter(
    condition_id: str,
) -> Optional[dict[str, Any]]:
    """Live Gamma authority fallback for the submitter's neg_risk + token IDs.

    Returns a dict shaped like a `world.executable_market_snapshots` row:
        {"neg_risk": bool, "yes_token_id": str | None, "no_token_id": str | None}

    Or None on transient failure / malformed payload — caller falls back to
    REDEEM_NEGRISK_FACT_MISSING fail-closed.

    Topology law (yaml:4193) forbids GUESSING neg-risk facts. Gamma CLOB is the
    canonical public authority (it is the same source consulted by
    _lookup_market_neg_risk_authoritative Tier 3 and by the entry-side scanner).
    The world snapshot table is a populated CACHE of this authority; consulting
    the authority directly when the cache is missing is structurally consistent
    with the no-guessing law — what is forbidden is defaulting to False or
    using an unrelated heuristic.

    2026-05-19 Karachi root cause: in-flight redeem positions entered before the
    capture_executable_market_snapshot side-effect path existed had no cache
    row and could not advance, latching OPERATOR_REQUIRED indefinitely. Adding
    this live-authority fallback closes that gap.
    """
    import httpx

    gamma_url = f"https://clob.polymarket.com/markets/{condition_id}"
    try:
        resp = httpx.get(gamma_url, timeout=5.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning(
            "[REDEEM_NEGRISK_GAMMA_FETCH_FAILED] condition_id=%s exc=%s",
            condition_id,
            exc,
        )
        return None

    # Type guard (PR #212 Copilot review): Gamma is supposed to return a JSON
    # object, but a misconfigured endpoint or upstream bug could return a list,
    # string, or number. payload.get(...) / payload.keys() on a non-mapping
    # would raise AttributeError and break the fail-closed contract — caller
    # would not get None, it would propagate an exception up. Mapping check
    # keeps the contract typed.
    from collections.abc import Mapping as _Mapping
    if not isinstance(payload, _Mapping):
        logger.warning(
            "[REDEEM_NEGRISK_GAMMA_NON_MAPPING] condition_id=%s payload_type=%s",
            condition_id,
            type(payload).__name__,
        )
        return None

    if "neg_risk" not in payload:
        logger.warning(
            "[REDEEM_NEGRISK_GAMMA_NO_FIELD] condition_id=%s response_keys=%s",
            condition_id,
            list(payload.keys())[:20],
        )
        return None

    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    tokens = payload.get("tokens", []) or []
    if not isinstance(tokens, list):
        # Tokens is supposed to be a list of dicts; defensive type guard.
        tokens = []
    for token in tokens:
        if not isinstance(token, _Mapping):
            continue
        outcome = str(token.get("outcome") or "").strip().lower()
        token_id = token.get("token_id")
        if not token_id:
            continue
        if outcome == "yes":
            yes_token_id = str(token_id)
        elif outcome == "no":
            no_token_id = str(token_id)

    return {
        "neg_risk": bool(payload["neg_risk"]),
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
    }


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
    """Follow chain receipts for tx-hashed redeem commands to terminal state.

    P1-4 (codereview-may19.md): bounded execution via batch cap, per-call CLOB
    result cache, and wall-clock budget to prevent N×5s unbounded blocking.
      ZEUS_REDEEM_RECONCILE_BATCH_CAP  — max rows per call (default 50)
      ZEUS_REDEEM_RECONCILE_BUDGET_S   — wall-clock budget in seconds (default 60)
    """
    import time as _time

    from src.venue.polymarket_v2_adapter import (
        POLYGON_CTF_ADDRESS,
        POLYGON_NEGRISK_ADAPTER_ADDRESS,
    )

    assert_settlement_schema_ready(conn)

    # P1-4 (a): batch cap — avoid processing unbounded rows per tick.
    _batch_cap = int(os.environ.get("ZEUS_REDEEM_RECONCILE_BATCH_CAP", "50") or "50")
    # P1-4 (c): wall-clock budget.
    _budget_s = float(os.environ.get("ZEUS_REDEEM_RECONCILE_BUDGET_S", "60") or "60")
    _t_start = _time.monotonic()

    rows = conn.execute(
        """
        SELECT * FROM settlement_commands
         WHERE state = ? AND tx_hash IS NOT NULL
         ORDER BY requested_at, command_id
         LIMIT ?
        """,
        (SettlementState.REDEEM_TX_HASHED.value, _batch_cap),
    ).fetchall()

    # P1-4 (b): per-call CLOB result cache keyed by condition_id.
    # Avoids repeated 5-second Gamma HTTP calls for the same market within
    # a single reconcile invocation.
    _negrisk_cache: dict[str, Optional[bool]] = {}

    results: list[SettlementResult] = []
    for row in rows:
        # P1-4 (c): budget check — break before fetching next receipt.
        if _time.monotonic() - _t_start > _budget_s:
            logger.warning(
                "[RECONCILE_REDEEM_BUDGET_EXCEEDED] budget_s=%s processed=%d remaining>=1 "
                "— deferring rest to next tick",
                _budget_s, len(results),
            )
            break
        tx_hash = str(row["tx_hash"])
        receipt = _get_receipt(web3, tx_hash)
        if receipt is None:
            continue
        receipt_payload = _raw_dict(receipt)
        status = receipt_payload.get("status")
        block_number = _extract_int(receipt_payload, "block_number", "blockNumber")
        confirmation_count = _confirmation_count(web3, block_number)
        if status in {1, "1", True, "success", "SUCCESS"}:
            # Antibody guard (4th iteration — logs[*].address):
            # Polymarket relay-style submissions route through a proxy; receipt.to is
            # NEVER the adapter contract.  The Standard CTF adapter address appears in
            # logs[*].address for every log whose topic[0] == PayoutRedemption.
            # Root cause: Karachi c8c220f5 tx 0x0c85d9… — StandardCTF emitted
            # PayoutRedemption (logs[1].address = 0x4d97…) for a negRisk market.
            _cond_key = str(row["condition_id"])
            if _cond_key in _negrisk_cache:
                is_negrisk_market = _negrisk_cache[_cond_key]
            else:
                is_negrisk_market = _lookup_market_neg_risk_authoritative(
                    conn, _cond_key
                )
                _negrisk_cache[_cond_key] = is_negrisk_market
            if is_negrisk_market is None:
                # Unknown — all three lookup sources failed (world.db empty, trades.db miss,
                # Gamma unreachable).  Defer terminal transition; will retry next tick.
                logger.warning(
                    "[RECONCILE_REDEEM_NEGRISK_UNKNOWN] command_id=%s condition_id=%s "
                    "— deferring transition; will retry next tick",
                    row["command_id"], row["condition_id"],
                )
                continue

            # Routing detection (5th iteration — NegRiskAdapter custom event topic):
            #
            # Standard CTF emits PayoutRedemption with topic
            # 0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d.
            # NegRiskAdapter emits its OWN redemption event with a DIFFERENT topic
            # 0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224
            # (observed on Karachi tx 0xe08e03334f25... block 87135584, log[7]).
            #
            # NegRiskAdapter internally calls Standard CTF to move underlying
            # positions during a negRisk redeem, so Standard CTF emits its
            # PayoutRedemption EVEN WHEN routing is correct through NegRiskAdapter.
            # The previous check (4th iter) required NegRiskAdapter to emit a
            # PayoutRedemption event and false-flagged the correct flow as MISROUTED
            # → reseat → GS013 retry loop on already-redeemed position. Karachi
            # 2026-05-19 was the canonical case.
            #
            # Correct test: for negRisk markets, the route is correct iff
            # NegRiskAdapter's contract address appears as a log emitter anywhere
            # in the receipt. Its presence proves the contract was called
            # (only the contract itself can emit events under its own address).
            _PAYOUT_REDEMPTION_TOPIC = (
                "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
            )
            def _to_hex_str(v: Any) -> str:
                """Coerce a topic/address value to a lowercase 0x-prefixed hex string.

                web3 returns HexBytes (has .hex() but no leading '0x'); plain
                receipts return bare strings already prefixed with '0x'.
                """
                if isinstance(v, (bytes, bytearray)):
                    return "0x" + v.hex()
                s = str(v)
                if s.startswith("0x") or s.startswith("0X"):
                    return s.lower()
                return s.lower()

            _negrisk_addr = POLYGON_NEGRISK_ADAPTER_ADDRESS.lower()
            _stdctf_addr = POLYGON_CTF_ADDRESS.lower()
            payout_redemption_emitters: set[str] = set()
            log_emitter_addrs: set[str] = set()
            for _log in receipt_payload.get("logs", []):
                _addr = _to_hex_str(_log.get("address") or "")
                if _addr:
                    log_emitter_addrs.add(_addr)
                _topics = _log.get("topics") or []
                if _topics and _to_hex_str(_topics[0]) == _PAYOUT_REDEMPTION_TOPIC and _addr:
                    payout_redemption_emitters.add(_addr)

            routed_to_standard_ctf = _stdctf_addr in payout_redemption_emitters
            routed_to_neg_risk_adapter = _negrisk_addr in log_emitter_addrs
            # Back-compat: surface adapter_addrs (kept for downstream consumers
            # and logging) as the union of PayoutRedemption emitters PLUS the
            # NegRiskAdapter address when it was called.
            adapter_addrs = set(payout_redemption_emitters)
            if routed_to_neg_risk_adapter:
                adapter_addrs.add(_negrisk_addr)

            if is_negrisk_market and not routed_to_neg_risk_adapter:
                _wrong_adapters = ",".join(sorted(adapter_addrs))
                misroute_error = {
                    "errorCode": "REDEEM_NEGRISK_MISROUTED",
                    "wrong_adapter_in_logs": _wrong_adapters,
                    "expected": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                    "previous_tx_hash": tx_hash,
                }
                logger.warning(
                    "[REDEEM_NEGRISK_MISROUTED] command_id=%s condition_id=%s "
                    "tx_hash=%s wrong_adapter_in_logs=%s expected=%s — resetting to "
                    "REDEEM_OPERATOR_REQUIRED for autonomous retry via correct adapter",
                    row["command_id"], row["condition_id"], tx_hash,
                    _wrong_adapters, POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                )
                with _savepoint(conn):
                    # _transition uses COALESCE so it cannot clear tx_hash.
                    # Explicit NULL update here ensures the reseat allowlist sees
                    # a clean row (no stale hash that would re-queue the bad tx).
                    conn.execute(
                        """
                        UPDATE settlement_commands
                           SET state = ?,
                               autoretry_eligible = 1,
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

            if is_negrisk_market:
                # Compute best-effort expected amount for cross-check.
                # Unlike submit_redeem (which has the full neg_risk_row with
                # yes/no token IDs), reconcile only has token_amounts_json.
                # Use the single-entry heuristic: if the map has exactly one
                # key, that value is the winning-slot amount. Multi-entry maps
                # (or None) set amount_per_slot=None to skip the cross-check.
                _reconcile_amount_per_slot: Optional[int] = None
                _ta_json = row["token_amounts_json"]
                if _ta_json:
                    try:
                        _ta = json.loads(_ta_json)
                        if isinstance(_ta, dict) and len(_ta) == 1:
                            from decimal import Decimal, ROUND_HALF_UP
                            _reconcile_amount_per_slot = int(
                                (Decimal(str(next(iter(_ta.values())))) * Decimal(1_000_000))
                                .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                            )
                    except Exception:
                        pass

                # Payout proof (P1-2, codereview-may19-2.md):
                # Routing presence alone does not prove the payout was correct.
                # Parse the NegRiskAdapter PayoutRedemption event from receipt logs
                # to verify: (1) condition_id matches, (2) payout > 0,
                # (3) payout amount plausibly matches token_amounts_json.
                #
                # NegRiskAdapter event signature (NegRiskAdapter.sol INegRiskAdapterEE):
                #   event PayoutRedemption(
                #     address indexed redeemer,
                #     bytes32 indexed conditionId,
                #     uint256[] amounts,
                #     uint256 payout
                #   )
                # ABI encoding:
                #   topics[0] = 0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224
                #   topics[1] = redeemer (indexed address, 32-byte padded)
                #   topics[2] = conditionId (indexed bytes32)
                #   data      = ABI(uint256[] amounts, uint256 payout):
                #     bytes 0-31:  offset pointer to amounts array (= 0x40)
                #     bytes 32-63: payout (static uint256, SECOND word)
                #     bytes 64-95: array length
                #     bytes 96+:   array items
                #
                # Fail-closed: if the topic is not found or decode fails,
                # classify as REDEEM_NEGRISK_REVIEW_REQUIRED.
                _NEGRISK_REDEMPTION_TOPIC = (
                    "0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224"
                )
                _command_condition_id = str(row["condition_id"]).lower()
                _proof_error_code: Optional[str] = None
                _proof_payout: Optional[int] = None
                _proof_condition_matched: bool = False

                for _log in receipt_payload.get("logs", []):
                    _addr = _to_hex_str(_log.get("address") or "")
                    if _addr != _negrisk_addr:
                        continue
                    _topics = _log.get("topics") or []
                    if not _topics or _to_hex_str(_topics[0]) != _NEGRISK_REDEMPTION_TOPIC:
                        continue
                    # topics[2] = conditionId (indexed bytes32)
                    if len(_topics) < 3:
                        _proof_error_code = "REDEEM_NEGRISK_REVIEW_REQUIRED"
                        break
                    try:
                        _log_condition_id = _to_hex_str(_topics[2])
                        if _log_condition_id != _command_condition_id:
                            # Different condition_id — unrelated adapter activity in
                            # the same tx. Classify as wrong-condition per P1-2 spec.
                            _proof_error_code = "REDEEM_NEGRISK_WRONG_CONDITION"
                            break
                        _proof_condition_matched = True
                    except Exception:
                        _proof_error_code = "REDEEM_NEGRISK_REVIEW_REQUIRED"
                        break

                    # Decode payout from log data (second 32-byte word).
                    _raw_data = _log.get("data") or ""
                    try:
                        _data_hex = _to_hex_str(_raw_data).lstrip("0x") if _raw_data else ""
                        if len(_data_hex) < 128:
                            # Fewer than 2 full 32-byte words — cannot decode payout.
                            _proof_error_code = "REDEEM_NEGRISK_REVIEW_REQUIRED"
                            break
                        _payout_word = _data_hex[64:128]  # second 32-byte word
                        _proof_payout = int(_payout_word, 16)
                        if _proof_payout == 0:
                            _proof_error_code = "REDEEM_NEGRISK_ZERO_PAYOUT"
                            break
                    except Exception:
                        _proof_error_code = "REDEEM_NEGRISK_REVIEW_REQUIRED"
                        break

                    # Amount cross-check against token_amounts_json (in micro-units).
                    # _reconcile_amount_per_slot is None when the row predates the
                    # computation or the token map could not be resolved — skip, not reject.
                    if _reconcile_amount_per_slot is not None and _reconcile_amount_per_slot > 0:
                        _diff = abs(_proof_payout - _reconcile_amount_per_slot)
                        _tolerance = max(1, int(_reconcile_amount_per_slot * 0.001))  # 0.1% or 1
                        if _diff > _tolerance:
                            _proof_error_code = "REDEEM_NEGRISK_AMOUNT_MISMATCH"
                            logger.warning(
                                "[REDEEM_NEGRISK_AMOUNT_MISMATCH] command_id=%s "
                                "condition_id=%s payout_from_receipt=%s "
                                "expected_amount_per_slot=%s diff=%s tolerance=%s",
                                row["command_id"], row["condition_id"],
                                _proof_payout, _reconcile_amount_per_slot, _diff, _tolerance,
                            )
                            break
                    # All checks passed for this log — route confirmed with proof.
                    break

                if not _proof_condition_matched and _proof_error_code is None:
                    # NegRiskAdapter was in logs but emitted no PayoutRedemption for
                    # this condition — possible unrelated adapter activity in the tx.
                    _proof_error_code = "REDEEM_NEGRISK_REVIEW_REQUIRED"

                if _proof_error_code is not None:
                    _review_error: dict[str, Any] = {
                        "errorCode": _proof_error_code,
                        "condition_id": str(row["condition_id"]),
                        "payout_from_receipt": _proof_payout,
                        "expected_amount_per_slot": _reconcile_amount_per_slot,
                        "tx_hash": tx_hash,
                    }
                    logger.warning(
                        "[%s] command_id=%s condition_id=%s "
                        "payout_from_receipt=%s expected=%s tx_hash=%s",
                        _proof_error_code,
                        row["command_id"], row["condition_id"],
                        _proof_payout, _reconcile_amount_per_slot, tx_hash,
                    )
                    with _savepoint(conn):
                        _transition(
                            conn,
                            str(row["command_id"]),
                            SettlementState.REDEEM_REVIEW_REQUIRED,
                            payload=receipt_payload,
                            block_number=block_number,
                            confirmation_count=confirmation_count,
                            error_payload=_review_error,
                            terminal=True,
                            recorded_at=_coerce_time(None),
                        )
                    results.append(
                        SettlementResult(
                            str(row["command_id"]),
                            SettlementState.REDEEM_REVIEW_REQUIRED,
                            tx_hash=tx_hash,
                            block_number=block_number,
                            confirmation_count=confirmation_count,
                            error_payload=_review_error,
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
    assert_settlement_schema_ready(conn)
    return dict(_get_row(conn, command_id))


def list_commands(conn: sqlite3.Connection, *, state: SettlementState | str | None = None) -> list[dict[str, Any]]:
    assert_settlement_schema_ready(conn)
    if state is None:
        rows = conn.execute("SELECT * FROM settlement_commands ORDER BY requested_at, command_id").fetchall()
    else:
        state_s = SettlementState(state).value
        rows = conn.execute(
            "SELECT * FROM settlement_commands WHERE state = ? ORDER BY requested_at, command_id",
            (state_s,),
        ).fetchall()
    return [dict(row) for row in rows]


def settlement_command_report_rows(
    conn: sqlite3.Connection,
    *,
    state: SettlementState | str | None = None,
) -> list[dict[str, Any]]:
    """Return report-ready command rows with explicit anchor-source trust fields."""

    rows = list_commands(conn, state=state)
    report_rows: list[dict[str, Any]] = []
    for row in rows:
        trust = classify_polymarket_end_anchor_source(
            row.get("polymarket_end_anchor_source")
        )
        report_row = dict(row)
        report_row.update(
            {
                "anchor_source_evidence_class": trust.evidence_class,
                "anchor_source_report_trust": trust.report_trust,
                "anchor_source_verified": trust.verified,
            }
        )
        report_rows.append(report_row)
    return report_rows


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
    autoretry_eligible: bool | None = None,
    terminal: bool = False,
    recorded_at: str,
) -> None:
    terminal_at = recorded_at if terminal else None
    autoretry_value = int(bool(autoretry_eligible)) if autoretry_eligible is not None else None
    conn.execute(
        """
        UPDATE settlement_commands
           SET state = ?,
               autoretry_eligible = COALESCE(?, autoretry_eligible),
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
            autoretry_value,
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
    autoretry_eligible: bool | None = None,
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
    autoretry_value = int(bool(autoretry_eligible)) if autoretry_eligible is not None else None
    cur = conn.execute(
        """
        UPDATE settlement_commands
           SET state = ?,
               autoretry_eligible = COALESCE(?, autoretry_eligible),
               tx_hash = COALESCE(?, tx_hash),
               submitted_at = COALESCE(?, submitted_at),
               terminal_at = COALESCE(?, terminal_at),
               error_payload = ?
         WHERE command_id = ?
           AND state = ?
        """,
        (
            to_value,
            autoretry_value,
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


# Error codes ALWAYS auto-retry-eligible once autonomous mode is enabled.
# Membership criterion: the REDEEM EFFECT DID NOT OCCUR on-chain, so retrying
# is safe and idempotent — either no tx was ever broadcast (legacy stub), or
# the on-chain tx confirmed against the wrong contract but the redeem outcome
# was not settled (tx_hash is cleared by the antibody, not re-queued).
#   REDEEM_DEFERRED_TO_R1: legacy stub from the pre-PR-#183 era; no tx broadcast.
#   REDEEM_NEGRISK_MISROUTED (PR-209): antibody (reconcile_pending_redeems) fires
#     only when a confirmed negRisk tx hit POLYGON_CTF_ADDRESS instead of
#     POLYGON_NEGRISK_ADAPTER_ADDRESS. Antibody clears tx_hash and parks row here.
#     Reseat → submitter retries via NegRiskAdapter. Loop self-terminates because
#     once the correct adapter is used the reconcile guard condition
#     (routed_to_standard_ctf and not routed_to_neg_risk_adapter) is FALSE and
#     the antibody never fires again. An infinite loop would require the submitter
#     to persistently mis-route, which is a deeper routing bug orthogonal to reseat.
_AUTONOMOUS_RETRY_ERRORCODES_ALWAYS: frozenset[str] = frozenset({
    "REDEEM_DEFERRED_TO_R1",
    "REDEEM_NEGRISK_MISROUTED",   # PR-209: antibody-reset cases auto-retry via NegRiskAdapter
    # PR #212 completion: NEGRISK_FACT_MISSING is now auto-recoverable because
    # _fetch_neg_risk_from_gamma_for_submitter() supplies the missing fact from
    # the canonical Gamma authority when the world snapshot cache is empty
    # (Karachi-class positions entered before the snapshot side-effect path
    # existed). Pre-PR #212 this errorCode latched OPERATOR_REQUIRED forever;
    # post-PR #212 the next submit attempt resolves it.
    "REDEEM_NEGRISK_FACT_MISSING",
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


def _error_code_autoretry_eligible(error_code: Any, *, dry_run_enabled: bool) -> bool:
    if error_code in _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS:
        return True
    if error_code in _AUTONOMOUS_RETRY_ERRORCODES_REQUIRE_LIVE:
        return not dry_run_enabled
    return False


def _error_code_can_be_marked_autoretryable(error_code: Any) -> bool:
    return (
        error_code in _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS
        or error_code in _AUTONOMOUS_RETRY_ERRORCODES_REQUIRE_LIVE
    )


def _autonomous_dry_run_enabled() -> bool:
    return os.environ.get(
        "ZEUS_AUTONOMOUS_REDEEM_DRY_RUN", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def reseat_stub_deferred_rows_for_autonomous_retry(conn: sqlite3.Connection) -> int:
    """Promote explicitly autoretry-eligible OPERATOR_REQUIRED rows to RETRYING.

    P1-3 live release proof: REDEEM_OPERATOR_REQUIRED itself means manual
    review. Autonomous reseat requires `autoretry_eligible=1` plus an allowlisted
    errorCode, so scheduler authority no longer depends on an overloaded state
    name or on error_payload parsing alone.

    Rows parked in REDEEM_OPERATOR_REQUIRED whose errorCode is
    auto-retry-eligible move back to REDEEM_RETRYING so the submitter picks them
    up. Two-tier allowlist:
      * _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS — retry whenever autonomous mode
        is ON (legacy stubs that never produced a real tx). Includes:
        - REDEEM_DEFERRED_TO_R1: legacy stub-era, no tx ever submitted.
        - REDEEM_NEGRISK_MISROUTED (PR-208): antibody in reconcile_pending_redeems
          resets misrouted negRisk redemptions here; reseat → submitter re-routes
          through NegRiskAdapter on the next cycle. Anchor: Karachi c8c220f5.
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
        """
        SELECT command_id, error_payload, autoretry_eligible
          FROM settlement_commands
         WHERE state = ?
           AND autoretry_eligible = 1
        """,
        (SettlementState.REDEEM_OPERATOR_REQUIRED.value,),
    ).fetchall()
    promoted = 0
    for row in rows:
        try:
            err = json.loads(row["error_payload"] or "{}")
        except json.JSONDecodeError:
            continue
        err_code = err.get("errorCode")
        eligible = _error_code_autoretry_eligible(err_code, dry_run_enabled=dry_run_enabled)
        if eligible:
            cur = conn.execute(
                "UPDATE settlement_commands SET state = ?, autoretry_eligible = 0, terminal_at = NULL"
                " WHERE command_id = ? AND state = ? AND autoretry_eligible = 1",
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
                        "prior_autoretry_eligible": True,
                    },
                    recorded_at=_coerce_time(None),
                )
                promoted += 1
    return promoted
