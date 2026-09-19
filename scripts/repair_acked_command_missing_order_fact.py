#!/usr/bin/env python3
# Lifecycle: created=2026-09-19; last_reviewed=2026-09-19; last_reused=never
# Purpose: Dry-run-first repair for ACKED commands whose bound order has no
#   venue_order_facts row, making them invisible to every lane that could
#   terminalize them and release their collateral.
# Reuse: Run with --json first; use --apply only after operator approval.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.
"""Restore the missing order fact for an ACKED command from venue truth.

This script does not place, cancel, or mutate any venue order: it reads one
authenticated point order and delegates every write to the shipped
``reconcile_acked_commands_missing_order_facts`` lane.

WHY THIS EXISTS OUT OF PROCESS: the loaded-daemon restart gate refuses while a
command is non-terminal, and the in-daemon drain that would terminalize it runs
only after that gate admits the stop. A row only the drain can clear therefore
refuses every restart forever -- including the restart that would load the drain.
Clearing it while the daemon keeps monitoring held capital breaks that
circularity without a monitoring blackout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.execution.command_recovery import (  # noqa: E402
    _acked_command_missing_order_fact_candidates,
    _client_point_order_read,
    reconcile_acked_commands_missing_order_facts,
    reconcile_terminal_order_facts,
)
from src.state.db import get_trade_connection, get_trade_connection_read_only  # noqa: E402


SOURCE_MODULE = "scripts.repair_acked_command_missing_order_fact"
REPAIR_REASON = "acked_command_missing_order_fact_repair"


def _load_live_client():
    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient()
    client._ensure_v2_adapter()
    return client


def find_candidates(
    conn: sqlite3.Connection,
    *,
    command_id: str | None = None,
) -> list[dict[str, Any]]:
    target = str(command_id or "").strip()
    return [
        dict(row)
        for row in _acked_command_missing_order_fact_candidates(conn)
        if not target or str(row.get("command_id") or "") == target
    ]


def classify(client, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read each candidate's point order and report what the lane would derive."""

    proofs: list[dict[str, Any]] = []
    for candidate in candidates:
        command_id = str(candidate.get("command_id") or "")
        venue_order_id = str(candidate.get("venue_order_id") or "")
        try:
            read = _client_point_order_read(client, venue_order_id)
        except Exception as exc:  # noqa: BLE001 - a failed read is not absence.
            proofs.append(
                {
                    "command_id": command_id,
                    "venue_order_id": venue_order_id,
                    "recoverable": False,
                    "reason": f"point_read_failed:{exc.__class__.__name__}",
                }
            )
            continue
        proofs.append(
            {
                "command_id": command_id,
                "venue_order_id": venue_order_id,
                "point_order_absent": bool(read.absent),
                "point_order_query_complete": bool(read.query_complete),
                "point_order_source": read.source,
                "point_order_absence_reason": read.absence_reason,
                "recoverable": bool(read.query_complete),
                "would_derive": (
                    "terminal_no_fill_fact" if read.absent else "resting_or_deferred"
                ),
            }
        )
    return proofs


def run(
    *,
    apply: bool = False,
    command_id: str | None = None,
    client=None,
) -> dict[str, Any]:
    target = str(command_id or "").strip()
    if apply and not target:
        raise ValueError("--apply requires --command-id")

    conn = get_trade_connection_read_only()
    conn.row_factory = sqlite3.Row
    try:
        candidates = find_candidates(conn, command_id=target)
    finally:
        conn.close()

    proof_client = client if client is not None else _load_live_client()
    proofs = classify(proof_client, candidates)

    applied: dict[str, Any] | None = None
    if apply:
        if not any(proof.get("recoverable") for proof in proofs):
            raise ValueError(
                f"command {target} has no complete authenticated point proof"
            )
        write_conn = get_trade_connection(write_class="live")
        write_conn.row_factory = sqlite3.Row
        try:
            repair = reconcile_acked_commands_missing_order_facts(
                write_conn,
                proof_client,
            )
            # The fact alone does not release collateral: the terminal-fact lane
            # turns it into the terminal command event that does. Run it in the
            # same transaction so the row never rests half-repaired.
            terminal = reconcile_terminal_order_facts(write_conn)
            write_conn.commit()
        finally:
            write_conn.close()
        applied = {"fact_repair": repair, "terminal_order_facts": terminal}

    return {
        "ok": True,
        "apply": apply,
        "command_id": target,
        "candidate_count": len(candidates),
        "recoverable_count": sum(1 for proof in proofs if proof.get("recoverable")),
        "candidates": [
            {
                "command_id": str(candidate.get("command_id") or ""),
                "intent_kind": str(candidate.get("intent_kind") or ""),
                "command_state": str(candidate.get("state") or ""),
                "venue_order_id": str(candidate.get("venue_order_id") or ""),
                "size": str(candidate.get("size") or ""),
                "price": str(candidate.get("price") or ""),
                "created_at": str(candidate.get("created_at") or ""),
            }
            for candidate in candidates
        ],
        "proofs": proofs,
        "applied_summary": applied,
        "venue_action": False,
        "db_backup_created": False,
        "repair_reason": REPAIR_REASON,
        "source_module": SOURCE_MODULE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append the proof-backed fact and terminalize via the shipped lanes.",
    )
    parser.add_argument("--command-id", help="Limit classification/apply to one command id.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args(argv)
    try:
        result = run(apply=args.apply, command_id=args.command_id)
    except Exception as exc:  # noqa: BLE001 - surface the refusal verbatim.
        payload = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        print(json.dumps(payload, sort_keys=True) if args.json else payload["error"])
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
