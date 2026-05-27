#!/usr/bin/env python3
"""EDLI live canary release gate.

Created: 2026-05-26
Authority basis: PR332 EDLI live promotion package; read-only canary proof gate.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANARY_PROOF_PASS = "CANARY_PROOF_PASS"
WAITING_FOR_QUALIFYING_EVENT = "WAITING_FOR_QUALIFYING_EVENT"
FAIL = "FAIL"

_REQUIRED_FIELDS = (
    "event_id",
    "aggregate_id",
    "final_intent_id",
    "execution_command_id",
    "condition_id",
    "token_id",
    "direction",
    "side",
    "order_type",
    "time_in_force",
    "post_only",
    "book_hash",
    "quote_seen_at",
    "best_bid",
    "best_ask",
    "limit_price",
    "tickSize",
    "negRisk",
    "balance_allowance_witness",
    "heartbeat_witness",
    "idempotency_key",
    "live_cap_usage_id",
    "cap_transition",
    "order_lifecycle_projection",
    "expected_edge",
    "realized_state",
)


@dataclass(frozen=True)
class CanaryGateResult:
    status: str
    reasons: tuple[str, ...] = ()


def evaluate_canary_artifact(
    artifact: dict[str, Any] | None,
    *,
    max_quote_age_ms: int = 1000,
    min_expected_edge: float = 0.0,
    conn: sqlite3.Connection | None = None,
) -> CanaryGateResult:
    if artifact is None:
        return CanaryGateResult(WAITING_FOR_QUALIFYING_EVENT, ("CANARY_ARTIFACT_MISSING",))
    missing = tuple(field for field in _REQUIRED_FIELDS if _missing(artifact.get(field)))
    reasons: list[str] = []
    if missing:
        reasons.append("CANARY_REQUIRED_FIELDS_MISSING:" + ",".join(missing))
    if _missing(artifact.get("venue_order_id")) and _missing(artifact.get("SubmitUnknown")) and _missing(
        artifact.get("submit_unknown")
    ):
        reasons.append("CANARY_REQUIRES_VENUE_ORDER_OR_SUBMIT_UNKNOWN")
    if _missing(artifact.get("user_channel_observation")) and _missing(artifact.get("reconcile_observation")):
        reasons.append("CANARY_REQUIRES_USER_CHANNEL_OR_RECONCILE")
    if bool(artifact.get("unresolved_submit_unknown", False)):
        reasons.append("CANARY_SUBMIT_UNKNOWN_UNRESOLVED")
    projection = artifact.get("order_lifecycle_projection") or {}
    if isinstance(projection, dict) and bool(projection.get("pending_reconcile", False)):
        reasons.append("CANARY_PENDING_RECONCILE")
    quote_age_ms = _quote_age_ms(artifact)
    if quote_age_ms is not None and quote_age_ms > max_quote_age_ms:
        reasons.append("CANARY_QUOTE_STALE")
    try:
        if float(artifact.get("expected_edge", 0.0)) <= min_expected_edge:
            reasons.append("CANARY_EXPECTED_EDGE_INSUFFICIENT")
    except (TypeError, ValueError):
        reasons.append("CANARY_EXPECTED_EDGE_INVALID")
    reasons.extend(_economic_object_mismatch_reasons(artifact))
    cap_transition = artifact.get("cap_transition") or {}
    if not isinstance(cap_transition, dict) or str(cap_transition.get("to_status") or "").upper() not in {
        "CONSUMED",
        "RELEASED",
        "PENDING_RECONCILE",
    }:
        reasons.append("CANARY_CAP_TRANSITION_INVALID")
    realized_state = str(artifact.get("realized_state") or "").upper()
    if realized_state not in {"CONFIRMED", "TERMINAL_NO_FILL", "RECONCILED", "PENDING_RECONCILE"}:
        reasons.append("CANARY_REALIZED_STATE_INVALID")
    if conn is not None:
        reasons.extend(_db_verification_reasons(conn, artifact))
    if reasons:
        return CanaryGateResult(FAIL, tuple(reasons))
    return CanaryGateResult(CANARY_PROOF_PASS, ())


def load_canary_artifact(path: str | Path) -> dict[str, Any] | None:
    artifact_path = Path(path)
    if not artifact_path.exists():
        return None
    payload = json.loads(artifact_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("CANARY_ARTIFACT_NOT_OBJECT")
    return payload


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == {}


def _quote_age_ms(artifact: dict[str, Any]) -> int | None:
    if artifact.get("quote_age_ms") is not None:
        return int(artifact["quote_age_ms"])
    checked_at = artifact.get("checked_at") or artifact.get("canary_checked_at")
    quote_seen_at = artifact.get("quote_seen_at")
    if not checked_at or not quote_seen_at:
        return None
    checked = _parse_time(str(checked_at))
    seen = _parse_time(str(quote_seen_at))
    return int((checked - seen).total_seconds() * 1000)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _economic_object_mismatch_reasons(artifact: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for nested_key in ("pre_submit", "execution_command", "final_intent"):
        nested = artifact.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for field in ("condition_id", "token_id", "side"):
            if nested.get(field) is not None and str(nested.get(field)) != str(artifact.get(field)):
                reasons.append(f"CANARY_{nested_key.upper()}_{field.upper()}_MISMATCH")
    return reasons


def _db_verification_reasons(conn: sqlite3.Connection, artifact: dict[str, Any]) -> list[str]:
    aggregate_id = str(artifact.get("aggregate_id") or "")
    execution_command_id = str(artifact.get("execution_command_id") or "")
    live_cap_usage_id = str(artifact.get("live_cap_usage_id") or "")
    reasons: list[str] = []
    try:
        projection = conn.execute(
            """
            SELECT *
            FROM edli_live_order_projection
            WHERE aggregate_id = ?
            """,
            (aggregate_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return ["CANARY_DB_PROJECTION_MISSING"]
    if projection is None:
        return ["CANARY_DB_PROJECTION_MISSING"]
    artifact_projection = artifact.get("order_lifecycle_projection") or {}
    if isinstance(artifact_projection, dict):
        if bool(projection["pending_reconcile"]) != bool(artifact_projection.get("pending_reconcile", False)):
            reasons.append("CANARY_DB_PENDING_RECONCILE_MISMATCH")
    try:
        events = _aggregate_events(conn, aggregate_id)
    except sqlite3.OperationalError:
        events = []
    event_types = [row["event_type"] for row in events]
    for required_type in (
        "DecisionProofAccepted",
        "SubmitPlanBuilt",
        "PreSubmitRevalidated",
        "LiveCapReserved",
        "ExecutionCommandCreated",
        "VenueSubmitAttempted",
    ):
        if required_type not in event_types:
            reasons.append(f"CANARY_DB_EVENT_MISSING:{required_type}")
    if "VenueSubmitAcknowledged" not in event_types and "SubmitUnknown" not in event_types:
        reasons.append("CANARY_DB_EVENT_MISSING:VenueSubmitAcknowledged_OR_SubmitUnknown")
    if "CapTransitioned" not in event_types:
        reasons.append("CANARY_DB_CAP_TRANSITION_MISSING")
    if not any(row["event_type"] in {"UserOrderObserved", "UserTradeObserved", "Reconciled"} for row in events):
        reasons.append("CANARY_DB_USER_OR_RECONCILE_MISSING")
    if live_cap_usage_id and not _table_has_row(conn, "edli_live_cap_usage", "usage_id", live_cap_usage_id):
        reasons.append("CANARY_DB_LIVE_CAP_USAGE_MISSING")
    if not _table_has_row(conn, "edli_live_profit_audit", "aggregate_id", aggregate_id):
        reasons.append("CANARY_DB_PROFIT_AUDIT_MISSING")
    command_cert = _execution_command_certificate(conn, execution_command_id)
    if command_cert is None:
        reasons.append("CANARY_DB_EXECUTION_COMMAND_CERTIFICATE_MISSING")
    command_payload = _payload_for_event(events, "ExecutionCommandCreated")
    if command_payload and execution_command_id and command_payload.get("execution_command_id") != execution_command_id:
        reasons.append("CANARY_DB_EXECUTION_COMMAND_ID_MISMATCH")
    pre_submit = _payload_for_event(events, "PreSubmitRevalidated")
    pre_submit_hash = _event_hash_for_event(events, "PreSubmitRevalidated")
    command_event_hash = _event_hash_for_event(events, "ExecutionCommandCreated")
    if command_cert is not None:
        cert_payload = command_cert["payload"]
        if cert_payload.get("aggregate_id") != aggregate_id:
            reasons.append("CANARY_DB_EXECUTION_COMMAND_AGGREGATE_MISMATCH")
        if command_event_hash and cert_payload.get("aggregate_execution_command_event_hash") != command_event_hash:
            reasons.append("CANARY_DB_EXECUTION_COMMAND_EVENT_HASH_MISMATCH")
        if pre_submit_hash and cert_payload.get("aggregate_pre_submit_event_hash") != pre_submit_hash:
            reasons.append("CANARY_DB_PRE_SUBMIT_EVENT_HASH_MISMATCH")
        for field in ("condition_id", "token_id", "side", "direction"):
            if cert_payload.get(field) is not None and str(cert_payload.get(field)) != str(artifact.get(field)):
                reasons.append(f"CANARY_DB_COMMAND_CERT_{field.upper()}_MISMATCH")
        if live_cap_usage_id and cert_payload.get("live_cap_usage_id") not in {None, live_cap_usage_id}:
            reasons.append("CANARY_DB_COMMAND_CERT_LIVE_CAP_USAGE_MISMATCH")
        parent_types = set(command_cert["parent_types"])
        for required_parent in (
            "FinalIntentCertificate",
            "ActionableTradeCertificate",
            "PreSubmitRevalidationCertificate",
            "LiveCapCertificate",
        ):
            if required_parent not in parent_types:
                reasons.append(f"CANARY_DB_COMMAND_CERT_PARENT_MISSING:{required_parent}")
    if pre_submit:
        for field in ("condition_id", "token_id", "side"):
            if pre_submit.get(field) is not None and str(pre_submit.get(field)) != str(artifact.get(field)):
                reasons.append(f"CANARY_DB_PRE_SUBMIT_{field.upper()}_MISMATCH")
        quote_age_ms = _quote_age_ms(artifact)
        if quote_age_ms is not None and int(pre_submit.get("quote_age_ms", quote_age_ms)) != quote_age_ms:
            reasons.append("CANARY_DB_QUOTE_AGE_MISMATCH")
    return reasons


def _table_has_row(conn: sqlite3.Connection, table: str, column: str, value: str) -> bool:
    if (table, column) == ("edli_live_cap_usage", "usage_id"):
        sql = "SELECT 1 FROM edli_live_cap_usage WHERE usage_id = ?"
    elif (table, column) == ("edli_live_profit_audit", "aggregate_id"):
        sql = "SELECT 1 FROM edli_live_profit_audit WHERE aggregate_id = ?"
    else:
        return False
    try:
        return conn.execute(sql, (value,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _aggregate_events(conn: sqlite3.Connection, aggregate_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT event_type, event_hash, payload_json
            FROM edli_live_order_events
            WHERE aggregate_id = ?
            ORDER BY event_sequence ASC
            """,
            (aggregate_id,),
        ).fetchall()
    )


def _payload_for_event(events: list[sqlite3.Row], event_type: str) -> dict[str, Any] | None:
    for row in reversed(events):
        if row["event_type"] == event_type:
            payload = json.loads(str(row["payload_json"]))
            return payload if isinstance(payload, dict) else None
    return None


def _event_hash_for_event(events: list[sqlite3.Row], event_type: str) -> str | None:
    for row in reversed(events):
        if row["event_type"] == event_type:
            return str(row["event_hash"])
    return None


def _execution_command_certificate(conn: sqlite3.Connection, execution_command_id: str) -> dict[str, Any] | None:
    if not execution_command_id:
        return None
    try:
        rows = conn.execute(
            """
            SELECT certificate_id, payload_json, certificate_hash
            FROM decision_certificates
            WHERE certificate_type = 'ExecutionCommandCertificate'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if isinstance(payload, dict) and payload.get("execution_command_id") == execution_command_id:
            return {
                "certificate_id": str(row["certificate_id"]),
                "certificate_hash": str(row["certificate_hash"]),
                "payload": payload,
                "parent_types": _certificate_parent_types(conn, str(row["certificate_id"])),
            }
    return None


def _certificate_parent_types(conn: sqlite3.Connection, certificate_id: str) -> tuple[str, ...]:
    try:
        rows = conn.execute(
            """
            SELECT parent_certificate_type
            FROM decision_certificate_edges
            WHERE child_certificate_id = ? AND required = 1
            """,
            (certificate_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(str(row["parent_certificate_type"]) for row in rows)


def _connect_world_db(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, help="Path to a JSON canary proof artifact.")
    parser.add_argument("--max-quote-age-ms", type=int, default=1000)
    parser.add_argument("--min-expected-edge", type=float, default=0.0)
    parser.add_argument("--world-db", help="World DB path for DB-backed canary verification.")
    parser.add_argument("--verify-db", action="store_true", help="Verify artifact against canonical DB rows.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    artifact = load_canary_artifact(args.artifact)
    conn = None
    if args.verify_db:
        if not args.world_db:
            raise SystemExit("--verify-db requires --world-db")
        conn = _connect_world_db(args.world_db)
    try:
        result = evaluate_canary_artifact(
            artifact,
            max_quote_age_ms=args.max_quote_age_ms,
            min_expected_edge=args.min_expected_edge,
            conn=conn,
        )
    finally:
        if conn is not None:
            conn.close()
    if args.json:
        print(json.dumps({"status": result.status, "reasons": list(result.reasons)}, sort_keys=True))
    else:
        print(result.status)
        for reason in result.reasons:
            print(reason)
    return 0 if result.status == CANARY_PROOF_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
