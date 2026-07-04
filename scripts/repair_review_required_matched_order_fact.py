#!/usr/bin/env python3
# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Dry-run-first targeted repair for REVIEW_REQUIRED commands whose
#   existing order fact plus authenticated trade payload prove a fill.
# Reuse: Run with --venue-proof --json first; use --apply only after operator approval.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.
"""Repair one REVIEW_REQUIRED matched-order command from authenticated trade truth.

This script does not place or cancel venue orders.  Dry-run mode reads the
canonical trade DB and, only with ``--venue-proof``, authenticated CLOB account
trades.  Apply mode requires ``--command-id`` and delegates to the existing
append-first ``reconcile_matched_order_facts`` path filtered to that single
command.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.execution.command_recovery import (  # noqa: E402
    _client_trade_payloads,
    _latest_matched_order_fact_candidates,
    _matched_event_type,
    _matched_order_fact_state,
    _matched_remaining_size,
    _point_order_fill_price,
    _point_order_from_maker_trade_payloads,
    _point_order_matched_size,
    _point_order_trade_ids,
    _point_order_transaction_hashes,
    _positive_decimal_or_none,
    reconcile_matched_order_facts,
)
from src.state.db import get_trade_connection, get_trade_connection_read_only  # noqa: E402


SOURCE_MODULE = "scripts.repair_review_required_matched_order_fact"
REPAIR_REASON = "review_required_matched_order_fact_repair"


def _load_live_client():
    from src.data.polymarket_client import PolymarketClient

    return PolymarketClient()._ensure_v2_adapter()


def find_candidates(
    conn: sqlite3.Connection,
    *,
    command_id: str | None = None,
) -> list[dict[str, Any]]:
    target_command_id = str(command_id or "").strip()
    candidates: list[dict[str, Any]] = []
    for row in _latest_matched_order_fact_candidates(conn):
        row_command_id = str(row.get("command_id") or "")
        if target_command_id and row_command_id != target_command_id:
            continue
        if str(row.get("state") or "").upper() != "REVIEW_REQUIRED":
            continue
        candidates.append(dict(row))
    return candidates


def _proof_from_trade_payloads(
    row: Mapping[str, Any],
    trade_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    order_id = str(row.get("order_fact_venue_order_id") or row.get("venue_order_id") or "")
    point_order = _point_order_from_maker_trade_payloads(trade_payloads, order_id=order_id)
    matched_size = _point_order_matched_size(
        point_order,
        fallback=row.get("order_fact_matched_size") or row.get("size") or "0",
        side=str(row.get("side") or ""),
    )
    venue_status = str(
        (point_order or {}).get("status") or (point_order or {}).get("state") or ""
    ).upper()
    remaining_size = _matched_remaining_size(dict(row), matched_size, venue_status=venue_status)
    event_type = _matched_event_type(dict(row), matched_size, venue_status=venue_status)
    fill_price = _point_order_fill_price(
        point_order,
        fallback=row.get("price"),
        side=str(row.get("side") or ""),
    )
    trade_ids = list(_point_order_trade_ids(point_order))
    tx_hashes = list(_point_order_transaction_hashes(point_order))
    recoverable = (
        point_order is not None
        and _positive_decimal_or_none(matched_size) is not None
        and _positive_decimal_or_none(fill_price) is not None
        and bool(trade_ids)
        and event_type == "FILL_CONFIRMED"
        and remaining_size == "0"
    )
    return {
        "command_id": str(row.get("command_id") or ""),
        "intent_kind": str(row.get("intent_kind") or ""),
        "command_state": str(row.get("state") or ""),
        "venue_order_id": order_id,
        "point_order_found": point_order is not None,
        "point_source": str((point_order or {}).get("source") or ""),
        "venue_status": venue_status,
        "matched_size": matched_size,
        "remaining_size": remaining_size,
        "fill_price": fill_price,
        "event_type": event_type,
        "order_fact_state": _matched_order_fact_state(
            event_type=event_type,
            venue_status=venue_status,
            remaining_size=remaining_size,
        ),
        "trade_ids": trade_ids,
        "tx_hashes": tx_hashes,
        "recoverable": recoverable,
    }


def build_proofs(
    candidates: list[dict[str, Any]],
    trade_payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_proof_from_trade_payloads(candidate, trade_payloads) for candidate in candidates]


def run(
    *,
    apply: bool,
    command_id: str | None,
    venue_proof: bool,
    adapter=None,
) -> dict[str, Any]:
    target_command_id = str(command_id or "").strip()
    if apply and not venue_proof:
        raise ValueError("--apply requires --venue-proof")
    if apply and not target_command_id:
        raise ValueError("--apply requires --command-id")

    proof_adapter = None
    trade_payloads: list[dict[str, Any]] = []
    conn = get_trade_connection_read_only()
    conn.row_factory = sqlite3.Row
    try:
        candidates = find_candidates(conn, command_id=target_command_id)
        if venue_proof:
            proof_adapter = adapter if adapter is not None else _load_live_client()
            trade_payloads = _client_trade_payloads(proof_adapter)
        proofs = build_proofs(candidates, trade_payloads) if venue_proof else []
    finally:
        conn.close()

    applied_summary: dict[str, Any] | None = None
    if apply:
        if not any(proof.get("recoverable") for proof in proofs):
            raise ValueError(f"command {target_command_id} has no recoverable matched-order proof")
        write_conn = get_trade_connection(write_class="live")
        write_conn.row_factory = sqlite3.Row
        try:
            applied_summary = reconcile_matched_order_facts(
                write_conn,
                proof_adapter,
                command_id=target_command_id,
            )
            write_conn.commit()
        finally:
            write_conn.close()

    return {
        "ok": True,
        "apply": apply,
        "command_id": target_command_id,
        "venue_proof": venue_proof,
        "candidate_count": len(candidates),
        "recoverable_count": sum(1 for proof in proofs if proof.get("recoverable")),
        "candidates": [
            {
                "command_id": str(candidate.get("command_id") or ""),
                "intent_kind": str(candidate.get("intent_kind") or ""),
                "command_state": str(candidate.get("state") or ""),
                "venue_order_id": str(
                    candidate.get("order_fact_venue_order_id")
                    or candidate.get("venue_order_id")
                    or ""
                ),
                "order_fact_state": str(candidate.get("order_fact_state") or ""),
                "order_fact_matched_size": str(candidate.get("order_fact_matched_size") or ""),
                "order_fact_remaining_size": str(candidate.get("order_fact_remaining_size") or ""),
            }
            for candidate in candidates
        ],
        "proofs": proofs,
        "applied_summary": applied_summary,
        "venue_action": False,
        "db_backup_created": False,
        "repair_reason": REPAIR_REASON,
        "source_module": SOURCE_MODULE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Append proof-backed fill repair.")
    parser.add_argument("--command-id", help="Limit classification/apply to one command id.")
    parser.add_argument(
        "--venue-proof",
        action="store_true",
        help="Read authenticated user trades for matched-order fill proof.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    result = run(
        apply=bool(args.apply),
        command_id=args.command_id,
        venue_proof=bool(args.venue_proof),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "review-required matched-order repair: "
            f"{'APPLY' if args.apply else 'DRY-RUN'} "
            f"candidates={result['candidate_count']} "
            f"recoverable={result['recoverable_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
