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
    order_column = "occurred_at" if "occurred_at" in _columns(conn, "venue_command_events") else "rowid"
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM venue_command_events WHERE command_id = ? ORDER BY {order_column} ASC",
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
    order_column = "observed_at" if "observed_at" in cols else "rowid"
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE command_id = ? ORDER BY {order_column} DESC",
            (command_id,),
        )
    ]


def _order_identity(
    command: dict[str, Any],
    events: list[dict[str, Any]],
    envelopes: list[dict[str, Any]],
) -> str:
    command_order_id = str(command.get("venue_order_id") or "")
    if command_order_id:
        return command_order_id
    for event in reversed(events):
        payload = _json_dict(event.get("payload_json") or event.get("payload"))
        for key in ("venue_order_id", "order_id", "orderID", "orderId", "id"):
            value = payload.get(key)
            if value:
                return str(value)
    for envelope in reversed(envelopes):
        for key in ("order_id", "orderID", "orderId", "id"):
            value = envelope.get(key)
            if value:
                return str(value)
    return ""


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
    order_id = _order_identity(command, events, envelopes)

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
            "pre_submit_envelope_present",
            "PASS" if _has_pre_submit_envelope(command, envelopes) else "FAIL",
            f"count={len(envelopes)}",
        )
    )
    checks.append(Check("venue_order_identity_present", "PASS" if order_id else "FAIL", order_id or "missing"))

    accepted = state in ACCEPTED_COMMAND_STATES and all(check.status == "PASS" for check in checks)
    filled = state in FILLED_COMMAND_STATES or bool(trade_facts)
    category = "LIVE_ORDER_SUBMITTED" if accepted else "NO_LIVE_ORDER_PROOF"
    if state in REJECTED_OR_UNKNOWN_STATES:
        category = "LIVE_ORDER_REJECTED_OR_UNKNOWN_RECORDED"
    if accepted and filled:
        category = "LIVE_ORDER_FILLED"
    return {
        "status": "PASS" if accepted else "FAIL",
        "completion_category": category,
        "checks": [asdict(check) for check in checks],
        "command": command,
        "events": events,
        "envelopes": envelopes,
        "venue_order_id": order_id,
        "venue_order_facts": order_facts,
        "venue_trade_facts": trade_facts,
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
