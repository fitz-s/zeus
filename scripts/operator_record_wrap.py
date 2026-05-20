#!/usr/bin/env python3
# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Lifecycle: created=2026-05-20; last_reviewed=2026-05-20; last_reused=never
# Purpose: Operator CLI to record resolution of a WRAP_FAILED row in
#   wrap_unwrap_commands. Use after diagnosing the root cause (balance, gas,
#   Safe owner mismatch, etc.) and confirming the wrap either succeeded via
#   alternate means or should be cleared so a fresh intent can be enqueued.
# Reuse: Invoke ONLY after operator investigation. Normal path: re-queue via
#   enqueue_wrap_if_balance_above_threshold (see architecture/cascade_liveness_contract.yaml).
#   This CLI records resolution notes so the row transitions to WRAP_CONFIRMED
#   with an operator-provided on-chain tx_hash, or marks WRAP_ABANDONED if the
#   balance was already wrapped by other means.
# Authority basis: architecture/cascade_liveness_contract.yaml wrap_unwrap_commands
#   terminal_states_with_operator_action (WRAP_FAILED cli_invocation).
"""Operator CLI to resolve a WRAP_FAILED row in wrap_unwrap_commands.

Two sub-commands:
  confirm <command_id> <tx_hash>   Advance WRAP_FAILED → WRAP_CONFIRMED with
                                   operator-supplied on-chain tx hash.
  abandon <command_id> --notes     Mark row as operator-acknowledged and clear
                                   the active wrap so a fresh enqueue can run.
                                   Does NOT create a new row; the submitter
                                   cycle will detect the balance and re-enqueue.

Exit codes:
  0  Success
  2  Wrong source state
  3  Malformed tx_hash
  5  No matching row found
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Iterable

logger = logging.getLogger(__name__)

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

EXIT_OK = 0
EXIT_WRONG_STATE = 2
EXIT_BAD_HASH = 3
EXIT_NOT_FOUND = 5


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _load_row(conn, command_id: str) -> dict:
    from src.execution.wrap_unwrap_commands import init_wrap_unwrap_schema
    init_wrap_unwrap_schema(conn)
    row = conn.execute(
        "SELECT * FROM wrap_unwrap_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        logger.error("[OPERATOR_RECORD_WRAP] command_id=%s not found", command_id)
        raise SystemExit(EXIT_NOT_FOUND)
    return dict(row)


def _cmd_confirm(args, conn) -> dict:
    """Advance WRAP_FAILED → WRAP_CONFIRMED with operator-supplied tx_hash."""
    if not _TX_HASH_RE.match(args.tx_hash):
        logger.error("[OPERATOR_RECORD_WRAP] malformed tx_hash=%s", args.tx_hash)
        raise SystemExit(EXIT_BAD_HASH)
    row = _load_row(conn, args.command_id)
    if row["state"] != "WRAP_FAILED":
        logger.error(
            "[OPERATOR_RECORD_WRAP] confirm requires WRAP_FAILED; got state=%s",
            row["state"],
        )
        raise SystemExit(EXIT_WRONG_STATE)
    from src.execution.wrap_unwrap_commands import confirm_wrap
    confirm_wrap(args.command_id, confirmation_count=1, conn=conn)
    # Overwrite tx_hash with the operator-supplied value.
    conn.execute(
        "UPDATE wrap_unwrap_commands SET tx_hash = ? WHERE command_id = ?",
        (args.tx_hash, args.command_id),
    )
    conn.commit()
    logger.info(
        "[OPERATOR_RECORD_WRAP] confirmed command_id=%s tx_hash=%s",
        args.command_id, args.tx_hash,
    )
    return {"command_id": args.command_id, "result": "confirmed", "tx_hash": args.tx_hash}


def _cmd_abandon(args, conn) -> dict:
    """Fail-acknowledge a WRAP_FAILED row so a fresh enqueue can run."""
    if not args.notes or len(args.notes.strip()) < 10:
        logger.error("[OPERATOR_RECORD_WRAP] abandon requires --notes with ≥10 chars")
        raise SystemExit(EXIT_WRONG_STATE)
    row = _load_row(conn, args.command_id)
    if row["state"] != "WRAP_FAILED":
        logger.error(
            "[OPERATOR_RECORD_WRAP] abandon requires WRAP_FAILED; got state=%s",
            row["state"],
        )
        raise SystemExit(EXIT_WRONG_STATE)
    import json as _json
    existing_error = _json.loads(row.get("error_payload") or "{}")
    existing_error["operator_abandoned_at"] = _now_iso()
    existing_error["operator_notes"] = args.notes.strip()
    conn.execute(
        "UPDATE wrap_unwrap_commands SET error_payload = ?, terminal_at = ? WHERE command_id = ?",
        (_json.dumps(existing_error, sort_keys=True), _now_iso(), args.command_id),
    )
    conn.commit()
    logger.warning(
        "[OPERATOR_RECORD_WRAP] abandoned command_id=%s notes=%r",
        args.command_id, args.notes,
    )
    return {"command_id": args.command_id, "result": "abandoned"}


def main(argv: Iterable[str] | None = None, *, conn=None) -> int:
    parser = argparse.ArgumentParser(
        description="Operator CLI to resolve a WRAP_FAILED wrap_unwrap_commands row."
    )
    sub = parser.add_subparsers(dest="subcmd", required=True)

    p_confirm = sub.add_parser("confirm", help="Advance WRAP_FAILED → WRAP_CONFIRMED.")
    p_confirm.add_argument("command_id", help="wrap_unwrap_commands.command_id (hex UUID).")
    p_confirm.add_argument("tx_hash", help="On-chain tx hash (0x + 64 hex).")

    p_abandon = sub.add_parser("abandon", help="Acknowledge WRAP_FAILED; clear for re-enqueue.")
    p_abandon.add_argument("command_id", help="wrap_unwrap_commands.command_id (hex UUID).")
    p_abandon.add_argument("--notes", required=True, help="Operator justification (≥10 chars).")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection
        conn = get_world_connection(write_class="live")

    try:
        try:
            if args.subcmd == "confirm":
                outcome = _cmd_confirm(args, conn)
            else:
                outcome = _cmd_abandon(args, conn)
        except SystemExit as e:
            return int(e.code) if isinstance(e.code, int) else 1
        print(json.dumps(outcome, indent=2))
        return EXIT_OK
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
