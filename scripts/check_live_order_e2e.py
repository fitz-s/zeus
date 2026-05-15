#!/usr/bin/env python3
# Lifecycle: created=2026-05-15; last_reviewed=2026-05-15; last_reused=2026-05-15
# Purpose: Read-only verifier for live order command, venue ack, and record-chain evidence.
# Reuse: Run after live submit attempts or when venue command/order/fill evidence semantics change.
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
"""Read-only live order end-to-end evidence checker.

This checker never submits, cancels, mutates DB truth, or fabricates proof. It
can only classify existing canonical records and fails closed when an accepted
order or its daemon-to-command correlation trace is missing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRADE_DB_NAME = "zeus_trades.db"
WORLD_DB_NAME = "zeus-world.db"

ACCEPTED_COMMAND_STATES = frozenset({"ACKED", "POST_ACKED", "PARTIAL", "FILLED"})
FILLED_COMMAND_STATES = frozenset({"PARTIAL", "FILLED"})
REJECTED_OR_UNKNOWN_STATES = frozenset(
    {
        "REJECTED",
        "SUBMIT_REJECTED",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "REVIEW_REQUIRED",
    }
)
ACCEPTED_EVENT_TYPES = frozenset(
    {"POST_ACKED", "SUBMIT_ACKED", "PARTIAL_FILL_OBSERVED", "FILL_CONFIRMED"}
)
REJECTED_OR_UNKNOWN_EVENT_TYPES = frozenset(
    {
        "SUBMIT_REJECTED",
        "SUBMIT_UNKNOWN",
        "SUBMIT_TIMEOUT_UNKNOWN",
        "CLOSED_MARKET_UNKNOWN",
        "REVIEW_REQUIRED",
    }
)
ORDER_ID_KEYS = ("venue_order_id", "order_id", "orderID", "orderId", "id")
LIVE_PROOF_SOURCES = frozenset({"REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN"})
OPEN_ORDER_FACT_STATES = frozenset({"LIVE", "RESTING"})
FILL_ORDER_FACT_STATES = frozenset({"MATCHED", "PARTIALLY_MATCHED"})
TERMINAL_ORDER_FACT_STATES = frozenset(
    {
        "CANCEL_CONFIRMED",
        "CANCEL_FAILED",
        "CANCEL_REQUESTED",
        "CANCEL_UNKNOWN",
        "EXPIRED",
        "VENUE_WIPED",
        "HEARTBEAT_CANCEL_SUSPECTED",
    }
)
FILL_TRADE_FACT_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED"})
FILLED_POSITION_PHASES = frozenset(
    {"active", "day0_window", "pending_exit", "economically_closed", "settled"}
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _connect_readonly(trade_db: Path, world_db: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{trade_db.resolve()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    if world_db is not None and world_db.exists():
        conn.execute(f"ATTACH DATABASE 'file:{world_db.resolve()}?mode=ro' AS world")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str, schema: str = "main") -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> set[str]:
    if not _table_exists(conn, table, schema):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA {schema}.table_info({table})")}


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _no_proof_result(check_name: str, detail: str) -> dict[str, Any]:
    check = Check(check_name, "FAIL", detail)
    return {
        "status": "FAIL",
        "completion_category": "NO_LIVE_ORDER_PROOF",
        "checks": [asdict(check)],
        "command": None,
    }


def _json_dict(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _latest_command(conn: sqlite3.Connection, command_id: str | None) -> dict[str, Any] | None:
    if not _table_exists(conn, "venue_commands"):
        return None
    if command_id:
        return _row_to_dict(
            conn.execute("SELECT * FROM venue_commands WHERE command_id = ?", (command_id,)).fetchone()
        )
    order_column = "created_at" if "created_at" in _columns(conn, "venue_commands") else "rowid"
    return _row_to_dict(
        conn.execute(f"SELECT * FROM venue_commands ORDER BY {order_column} DESC LIMIT 1").fetchone()
    )


def _events(conn: sqlite3.Connection, command_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "venue_command_events"):
        return []
    cols = _columns(conn, "venue_command_events")
    order_terms = []
    if "sequence_no" in cols:
        order_terms.append("sequence_no ASC")
    if "occurred_at" in cols:
        order_terms.append("occurred_at ASC")
    order_terms.append("rowid ASC")
    order_sql = ", ".join(order_terms)
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM venue_command_events WHERE command_id = ? ORDER BY {order_sql}",
            (command_id,),
        )
    ]


def _envelopes(conn: sqlite3.Connection, command: dict[str, Any]) -> list[dict[str, Any]]:
    if not _table_exists(conn, "venue_submission_envelopes"):
        return []
    command_id = str(command.get("command_id") or "")
    cols = _columns(conn, "venue_submission_envelopes")
    if "command_id" not in cols:
        envelope_id = str(command.get("envelope_id") or "")
        if not envelope_id or "envelope_id" not in cols:
            return []
    order_column = "captured_at" if "captured_at" in cols else "rowid"
    if "command_id" in cols:
        where_sql = "command_id = ?"
        params = (command_id,)
    else:
        where_sql = "envelope_id = ?"
        params = (str(command.get("envelope_id") or ""),)
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM venue_submission_envelopes WHERE {where_sql} ORDER BY {order_column} ASC",
            params,
        )
    ]


def _facts(conn: sqlite3.Connection, table: str, command_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if "command_id" not in cols:
        return []
    order_terms = []
    if "local_sequence" in cols:
        order_terms.append("local_sequence DESC")
    if "observed_at" in cols:
        order_terms.append("observed_at DESC")
    order_terms.append("rowid DESC")
    order_sql = ", ".join(order_terms)
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE command_id = ? ORDER BY {order_sql}",
            (command_id,),
        )
    ]


def _positive_decimal(value: Any) -> bool:
    try:
        return Decimal(str(value)) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _live_source(value: Any) -> bool:
    return str(value or "").upper() in LIVE_PROOF_SOURCES


def _matching_live_order_facts(order_facts: list[dict[str, Any]], order_id: str) -> list[dict[str, Any]]:
    if not order_id:
        return []
    return [
        fact
        for fact in order_facts
        if str(fact.get("venue_order_id") or "") == order_id and _live_source(fact.get("source"))
    ]


def _matching_order_facts(order_facts: list[dict[str, Any]], order_id: str) -> list[dict[str, Any]]:
    if not order_id:
        return []
    return [
        fact
        for fact in order_facts
        if str(fact.get("venue_order_id") or "") == order_id
    ]


def _latest_order_fact(order_facts: list[dict[str, Any]], order_id: str) -> dict[str, Any] | None:
    matching = _matching_order_facts(order_facts, order_id)
    return matching[0] if matching else None


def _order_fact_supports_open_order(fact: dict[str, Any] | None) -> bool:
    return (
        fact is not None
        and _live_source(fact.get("source"))
        and str(fact.get("state") or "") in OPEN_ORDER_FACT_STATES
    )


def _order_fact_supports_fill_observation(fact: dict[str, Any] | None) -> bool:
    return (
        fact is not None
        and _live_source(fact.get("source"))
        and str(fact.get("state") or "") in FILL_ORDER_FACT_STATES
    )


def _order_fact_supports_live_order_proof(fact: dict[str, Any] | None) -> bool:
    if fact is None or not _live_source(fact.get("source")):
        return False
    state = str(fact.get("state") or "")
    return state not in TERMINAL_ORDER_FACT_STATES and (
        state in OPEN_ORDER_FACT_STATES or state in FILL_ORDER_FACT_STATES
    )


def _matching_live_trade_facts(trade_facts: list[dict[str, Any]], order_id: str) -> list[dict[str, Any]]:
    if not order_id:
        return []
    return [
        fact
        for fact in trade_facts
        if str(fact.get("venue_order_id") or "") == order_id
        and str(fact.get("state") or "") in FILL_TRADE_FACT_STATES
        and _live_source(fact.get("source"))
        and _positive_decimal(fact.get("filled_size"))
        and _positive_decimal(fact.get("fill_price"))
    ]


def _latest_trade_fact(trade_facts: list[dict[str, Any]], order_id: str) -> dict[str, Any] | None:
    if not order_id:
        return None
    for fact in trade_facts:
        if str(fact.get("venue_order_id") or "") == order_id:
            return fact
    return None


def _trade_fact_supports_fill(fact: dict[str, Any] | None) -> bool:
    return (
        fact is not None
        and str(fact.get("state") or "") in FILL_TRADE_FACT_STATES
        and _live_source(fact.get("source"))
        and _positive_decimal(fact.get("filled_size"))
        and _positive_decimal(fact.get("fill_price"))
    )


def _position_events(conn: sqlite3.Connection, command_id: str, order_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "position_events"):
        return []
    cols = _columns(conn, "position_events")
    clauses: list[str] = []
    params: list[str] = []
    if command_id and "command_id" in cols:
        clauses.append("command_id = ?")
        params.append(command_id)
    if order_id and "order_id" in cols:
        clauses.append("order_id = ?")
        params.append(order_id)
    if not clauses:
        return []
    order_column = "sequence_no" if "sequence_no" in cols else "rowid"
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM position_events WHERE {' OR '.join(clauses)} ORDER BY {order_column} ASC",
            tuple(params),
        )
    ]


def _position_current(
    conn: sqlite3.Connection,
    order_id: str,
    position_ids: set[str],
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "position_current"):
        return []
    cols = _columns(conn, "position_current")
    clauses: list[str] = []
    params: list[str] = []
    if order_id and "order_id" in cols:
        clauses.append("order_id = ?")
        params.append(order_id)
    if position_ids and "position_id" in cols:
        placeholders = ", ".join("?" for _ in position_ids)
        clauses.append(f"position_id IN ({placeholders})")
        params.extend(sorted(position_ids))
    if not clauses:
        return []
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM position_current WHERE {' OR '.join(clauses)}",
            tuple(params),
        )
    ]


def _matching_fill_position_events(events: list[dict[str, Any]], order_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if str(event.get("event_type") or "") == "ENTRY_ORDER_FILLED"
        and (not order_id or str(event.get("order_id") or "") == order_id)
        and str(event.get("env") or "live") == "live"
    ]


def _has_matching_position_current(
    fill_events: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    order_id: str,
) -> bool:
    position_ids = {str(event.get("position_id") or "") for event in fill_events if event.get("position_id")}
    if not position_ids:
        return False
    for row in current_rows:
        if str(row.get("position_id") or "") not in position_ids:
            continue
        if order_id and str(row.get("order_id") or "") != order_id:
            continue
        if str(row.get("phase") or "") not in FILLED_POSITION_PHASES:
            continue
        return True
    return False


def _order_id_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ORDER_ID_KEYS:
        value = payload.get(key)
        if value:
            values.append(str(value))
    return values


def _order_identity_evidence(
    command: dict[str, Any],
    events: list[dict[str, Any]],
    envelopes: list[dict[str, Any]],
) -> tuple[str, dict[str, list[str]]]:
    evidence: dict[str, list[str]] = {
        "command": _order_id_values(command),
        "accepted_events": [],
        "envelopes": [],
    }
    for event in reversed(events):
        if str(event.get("event_type") or "") not in ACCEPTED_EVENT_TYPES:
            continue
        payload = _json_dict(event.get("payload_json") or event.get("payload"))
        evidence["accepted_events"].extend(_order_id_values(payload))
    for envelope in reversed(envelopes):
        evidence["envelopes"].extend(_order_id_values(envelope))
        evidence["envelopes"].extend(_order_id_values(_json_dict(envelope.get("raw_response_json"))))
    for source in ("command", "accepted_events", "envelopes"):
        if evidence[source]:
            return evidence[source][0], evidence
    return "", evidence


def _order_identity_consistent(evidence: dict[str, list[str]]) -> bool:
    values = {
        value
        for source_values in evidence.values()
        for value in source_values
        if value
    }
    return bool(values) and len(values) == 1


def _facts_identity_consistent(facts: list[dict[str, Any]], order_id: str) -> bool:
    if not order_id:
        return False
    fact_order_ids = {
        str(fact.get("venue_order_id") or "")
        for fact in facts
        if fact.get("venue_order_id")
    }
    return not fact_order_ids or fact_order_ids == {order_id}


def _latest_event_not_rejected_or_unknown(events: list[dict[str, Any]]) -> bool:
    if not events:
        return False
    latest = events[-1]
    event_type = str(latest.get("event_type") or "")
    state_after = str(latest.get("state_after") or "")
    return event_type not in REJECTED_OR_UNKNOWN_EVENT_TYPES and state_after not in REJECTED_OR_UNKNOWN_STATES


def _has_pre_submit_envelope(command: dict[str, Any], envelopes: list[dict[str, Any]]) -> bool:
    command_envelope_id = str(command.get("envelope_id") or "")
    for envelope in envelopes:
        value = str(envelope.get("envelope_id") or "")
        if command_envelope_id and value == command_envelope_id:
            return True
        if value.startswith("pre-submit:"):
            return True
    return False


def evaluate(conn: sqlite3.Connection, command_id: str | None = None) -> dict[str, Any]:
    checks: list[Check] = []
    command = _latest_command(conn, command_id)
    if command is None:
        checks.append(Check("command_present", "FAIL", "venue_commands row not found"))
        return {
            "status": "FAIL",
            "completion_category": "NO_LIVE_ORDER_PROOF",
            "checks": [asdict(check) for check in checks],
            "command": None,
        }

    cmd_id = str(command.get("command_id") or "")
    state = str(command.get("state") or "")
    events = _events(conn, cmd_id)
    envelopes = _envelopes(conn, command)
    order_facts = _facts(conn, "venue_order_facts", cmd_id)
    trade_facts = _facts(conn, "venue_trade_facts", cmd_id)
    event_types = {str(event.get("event_type") or "") for event in events}
    order_id, order_identity_evidence = _order_identity_evidence(command, events, envelopes)
    matching_order_facts = _matching_live_order_facts(order_facts, order_id)
    latest_order_fact = _latest_order_fact(order_facts, order_id)
    latest_order_fact_state = str(latest_order_fact.get("state") or "") if latest_order_fact else ""
    latest_order_fact_source = str(latest_order_fact.get("source") or "") if latest_order_fact else ""
    matching_trade_facts = _matching_live_trade_facts(trade_facts, order_id)
    latest_trade_fact = _latest_trade_fact(trade_facts, order_id)
    position_events = _position_events(conn, cmd_id, order_id)
    position_ids = {
        str(event.get("position_id") or "")
        for event in position_events
        if event.get("position_id")
    }
    position_current = _position_current(conn, order_id, position_ids)
    fill_position_events = _matching_fill_position_events(position_events, order_id)
    position_tables_present = _table_exists(conn, "position_events") and _table_exists(
        conn, "position_current"
    )
    fill_observed = (
        state in FILLED_COMMAND_STATES
        or _trade_fact_supports_fill(latest_trade_fact)
        or bool(event_types & {"PARTIAL_FILL_OBSERVED", "FILL_CONFIRMED"})
        or _order_fact_supports_fill_observation(latest_order_fact)
    )

    checks.append(Check("command_present", "PASS", f"command_id={cmd_id} state={state}"))
    checks.append(
        Check(
            "command_not_rejected_or_unknown",
            "PASS" if state not in REJECTED_OR_UNKNOWN_STATES else "FAIL",
            f"state={state}",
        )
    )
    for field in ("decision_id", "idempotency_key", "snapshot_id"):
        value = str(command.get(field) or "")
        checks.append(Check(f"command_{field}_present", "PASS" if value else "FAIL", value or "missing"))
    checks.append(
        Check(
            "submit_requested_event_present",
            "PASS" if "SUBMIT_REQUESTED" in event_types else "FAIL",
            ",".join(sorted(event_types)) or "no events",
        )
    )
    checks.append(
        Check(
            "accepted_event_present",
            "PASS" if event_types & ACCEPTED_EVENT_TYPES else "FAIL",
            ",".join(sorted(event_types & ACCEPTED_EVENT_TYPES)) or "missing",
        )
    )
    checks.append(
        Check(
            "latest_event_not_rejected_or_unknown",
            "PASS" if _latest_event_not_rejected_or_unknown(events) else "FAIL",
            (
                f"event_type={events[-1].get('event_type') if events else 'missing'} "
                f"state_after={events[-1].get('state_after') if events else 'missing'}"
            ),
        )
    )
    checks.append(
        Check(
            "pre_submit_envelope_present",
            "PASS" if _has_pre_submit_envelope(command, envelopes) else "FAIL",
            f"count={len(envelopes)}",
        )
    )
    checks.append(Check("venue_order_identity_present", "PASS" if order_id else "FAIL", order_id or "missing"))
    checks.append(
        Check(
            "venue_order_identity_consistent",
            "PASS" if _order_identity_consistent(order_identity_evidence) else "FAIL",
            json.dumps(order_identity_evidence, sort_keys=True),
        )
    )
    checks.append(
        Check(
            "venue_order_facts_identity_consistent",
            "PASS" if _facts_identity_consistent(order_facts, order_id) else "FAIL",
            (
                f"order_id={order_id or 'missing'} fact_order_ids="
                f"{sorted({str(fact.get('venue_order_id') or '') for fact in order_facts if fact.get('venue_order_id')})}"
            ),
        )
    )
    checks.append(
        Check(
            "latest_venue_order_fact_open",
            "PASS" if _order_fact_supports_live_order_proof(latest_order_fact) else "FAIL",
            (
                f"live_count={len(matching_order_facts)} total_count={len(order_facts)} "
                f"order_id={order_id or 'missing'} "
                f"latest_source={latest_order_fact_source or 'missing'} "
                f"latest_state={latest_order_fact_state or 'missing'}"
            ),
        )
    )
    checks.append(
        Check(
            "position_tables_present",
            "PASS" if position_tables_present else "FAIL",
            f"position_events={_table_exists(conn, 'position_events')} "
            f"position_current={_table_exists(conn, 'position_current')}",
        )
    )

    if fill_observed:
        checks.append(
            Check(
                "venue_trade_facts_identity_consistent",
                "PASS" if _facts_identity_consistent(trade_facts, order_id) else "FAIL",
                (
                    f"order_id={order_id or 'missing'} trade_fact_order_ids="
                    f"{sorted({str(fact.get('venue_order_id') or '') for fact in trade_facts if fact.get('venue_order_id')})}"
                ),
            )
        )
        checks.append(
            Check(
                "venue_trade_fact_present",
                "PASS" if _trade_fact_supports_fill(latest_trade_fact) else "FAIL",
                (
                    f"live_count={len(matching_trade_facts)} total_count={len(trade_facts)} "
                    f"latest_source={latest_trade_fact.get('source') if latest_trade_fact else 'missing'} "
                    f"latest_state={latest_trade_fact.get('state') if latest_trade_fact else 'missing'}"
                ),
            )
        )
        checks.append(
            Check(
                "position_fill_event_present",
                "PASS" if fill_position_events else "FAIL",
                f"count={len(fill_position_events)}",
            )
        )
        checks.append(
            Check(
                "position_current_projection_present",
                "PASS"
                if _has_matching_position_current(fill_position_events, position_current, order_id)
                else "FAIL",
                f"count={len(position_current)}",
            )
        )
    else:
        checks.append(
            Check(
                "no_position_without_fill",
                "PASS" if position_tables_present and not position_events and not position_current else "FAIL",
                f"position_events={len(position_events)} position_current={len(position_current)}",
            )
        )

    failed_check_names = {check.name for check in checks if check.status == "FAIL"}
    complete = state in ACCEPTED_COMMAND_STATES and not failed_check_names
    category = (
        "LIVE_ORDER_FILLED"
        if complete and fill_observed
        else "LIVE_ORDER_SUBMITTED"
        if complete
        else "NO_LIVE_ORDER_PROOF"
    )
    if (
        state in ACCEPTED_COMMAND_STATES
        and order_id
        and failed_check_names == {"latest_venue_order_fact_open"}
    ):
        category = "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    if state in ACCEPTED_COMMAND_STATES and fill_observed and failed_check_names:
        category = "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    if state in REJECTED_OR_UNKNOWN_STATES:
        category = "LIVE_ORDER_REJECTED_OR_UNKNOWN_RECORDED"
    return {
        "status": "PASS" if complete else "FAIL",
        "completion_category": category,
        "checks": [asdict(check) for check in checks],
        "command": command,
        "events": events,
        "envelopes": envelopes,
        "venue_order_id": order_id,
        "venue_order_identity_evidence": order_identity_evidence,
        "venue_order_facts": order_facts,
        "venue_trade_facts": trade_facts,
        "position_events": position_events,
        "position_current": position_current,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-db", type=Path, default=ROOT / "state" / TRADE_DB_NAME)
    parser.add_argument("--world-db", type=Path, default=ROOT / "state" / WORLD_DB_NAME)
    parser.add_argument("--command-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-no-proof", action="store_true")
    args = parser.parse_args(argv)

    if not args.trade_db.exists():
        result = _no_proof_result("trade_db_present", f"missing path={args.trade_db}")
    else:
        try:
            with _connect_readonly(args.trade_db, args.world_db) as conn:
                result = evaluate(conn, args.command_id)
        except sqlite3.Error as exc:
            result = _no_proof_result("trade_db_readable", f"{type(exc).__name__}: {exc}")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"{result['status']} {result['completion_category']}")
        for check in result["checks"]:
            print(f"{check['status']} {check['name']}: {check['detail']}")
    if result["status"] == "PASS" or args.allow_no_proof:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
