#!/usr/bin/env python3
# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Dry-run-first clearance for REVIEW_REQUIRED commands with no venue id.
# Reuse: Run with --json first; use --venue-proof for authenticated read proof;
#   use --apply only after operator approval.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.
"""Classify and clear safe REVIEW_REQUIRED no-venue-exposure commands.

This script does not place, cancel, or mutate venue orders.  In dry-run mode it
classifies old ``recovery_no_venue_order_id`` commands from local append-only
truth.  With ``--venue-proof`` it also reads authenticated user open orders and
trades to build the existing command-recovery absence proof.  ``--apply`` only
appends the existing proof-backed REVIEW_CLEARED_NO_VENUE_EXPOSURE event for
rows that have no local exposure trace and zero matching venue open orders or
trades.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.execution.command_recovery import (  # noqa: E402
    _review_required_confirmed_trade_recovery,
    _review_required_confirmed_trade_match,
    build_review_required_no_venue_exposure_proof,
    clear_review_required_no_venue_exposure,
)
from src.execution.command_bus import VenueCommand  # noqa: E402
from src.state.db import get_trade_connection, get_trade_connection_read_only  # noqa: E402


SOURCE_MODULE = "scripts.repair_review_required_no_venue_exposure"
REPAIR_REASON = "review_required_no_venue_exposure_repair"
SUPPORTED_REVIEW_REASON = "recovery_no_venue_order_id"
UNSAFE_COMMAND_EVENTS = frozenset(
    {
        "POST_ACKED",
        "SUBMIT_ACKED",
        "SUBMIT_UNKNOWN",
        "SUBMIT_TIMEOUT_UNKNOWN",
        "CLOSED_MARKET_UNKNOWN",
        "PARTIAL_FILL_OBSERVED",
        "FILL_CONFIRMED",
    }
)


@dataclass(frozen=True)
class ReviewCandidate:
    command_id: str
    intent_kind: str
    state: str
    position_id: str
    token_id: str
    side: str
    size: str
    price: str
    venue_order_id: str
    decision_id: str
    latest_review_reason: str
    order_fact_count: int
    trade_fact_count: int
    position_event_count: int
    lot_count: int
    position_current_count: int
    unsafe_event_types: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def local_clearance_eligible(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "intent_kind": self.intent_kind,
            "state": self.state,
            "position_id": self.position_id,
            "token_id": self.token_id,
            "side": self.side,
            "size": self.size,
            "price": self.price,
            "venue_order_id": self.venue_order_id,
            "decision_id": self.decision_id,
            "latest_review_reason": self.latest_review_reason,
            "order_fact_count": self.order_fact_count,
            "trade_fact_count": self.trade_fact_count,
            "position_event_count": self.position_event_count,
            "lot_count": self.lot_count,
            "position_current_count": self.position_current_count,
            "unsafe_event_types": list(self.unsafe_event_types),
            "local_clearance_eligible": self.local_clearance_eligible,
            "blockers": list(self.blockers),
        }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str, where_sql: str, args: tuple[Any, ...]) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where_sql}", args).fetchone()
    return int(row["n"] if row is not None else 0)


def _latest_review_reason(conn: sqlite3.Connection, command_id: str) -> str:
    row = conn.execute(
        """
        SELECT payload_json
          FROM venue_command_events
         WHERE command_id = ?
           AND event_type = 'REVIEW_REQUIRED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    if row is None:
        return ""
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return ""
    return str(payload.get("reason") or "").strip()


def _unsafe_event_types(conn: sqlite3.Connection, command_id: str) -> tuple[str, ...]:
    rows = conn.execute(
        """
        SELECT DISTINCT event_type
          FROM venue_command_events
         WHERE command_id = ?
         ORDER BY event_type
        """,
        (command_id,),
    ).fetchall()
    return tuple(
        str(row["event_type"])
        for row in rows
        if str(row["event_type"] or "") in UNSAFE_COMMAND_EVENTS
    )


def _blockers(
    *,
    row: sqlite3.Row,
    latest_review_reason: str,
    order_fact_count: int,
    trade_fact_count: int,
    position_event_count: int,
    lot_count: int,
    position_current_count: int,
    unsafe_event_types: tuple[str, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if str(row["state"] or "") != "REVIEW_REQUIRED":
        blockers.append("state_not_review_required")
    if latest_review_reason != SUPPORTED_REVIEW_REASON:
        blockers.append("latest_review_reason_not_recovery_no_venue_order_id")
    if str(row["venue_order_id"] or "").strip():
        blockers.append("venue_order_id_present")
    if order_fact_count:
        blockers.append("order_facts_present")
    if trade_fact_count:
        blockers.append("trade_facts_present")
    if position_event_count:
        blockers.append("position_events_present")
    if lot_count:
        blockers.append("position_lots_present")
    if position_current_count:
        blockers.append("position_current_present")
    for event_type in unsafe_event_types:
        blockers.append(f"unsafe_event:{event_type}")
    return tuple(blockers)


def find_candidates(conn: sqlite3.Connection) -> list[ReviewCandidate]:
    rows = conn.execute(
        """
        SELECT command_id,
               COALESCE(intent_kind, '') AS intent_kind,
               COALESCE(state, '') AS state,
               COALESCE(position_id, '') AS position_id,
               COALESCE(token_id, '') AS token_id,
               COALESCE(side, '') AS side,
               COALESCE(size, '') AS size,
               COALESCE(price, '') AS price,
               COALESCE(venue_order_id, '') AS venue_order_id,
               COALESCE(decision_id, '') AS decision_id
          FROM venue_commands
         WHERE state = 'REVIEW_REQUIRED'
         ORDER BY updated_at, command_id
        """
    ).fetchall()
    candidates: list[ReviewCandidate] = []
    for row in rows:
        command_id = str(row["command_id"] or "")
        position_id = str(row["position_id"] or "")
        latest_review_reason = _latest_review_reason(conn, command_id)
        order_fact_count = _count(conn, "venue_order_facts", "command_id = ?", (command_id,))
        trade_fact_count = _count(conn, "venue_trade_facts", "command_id = ?", (command_id,))
        position_event_count = _count(conn, "position_events", "command_id = ?", (command_id,))
        lot_count = _count(conn, "position_lots", "source_command_id = ?", (command_id,))
        position_current_count = (
            _count(conn, "position_current", "position_id = ?", (position_id,))
            if position_id
            else 0
        )
        unsafe_event_types = _unsafe_event_types(conn, command_id)
        blockers = _blockers(
            row=row,
            latest_review_reason=latest_review_reason,
            order_fact_count=order_fact_count,
            trade_fact_count=trade_fact_count,
            position_event_count=position_event_count,
            lot_count=lot_count,
            position_current_count=position_current_count,
            unsafe_event_types=unsafe_event_types,
        )
        candidates.append(
            ReviewCandidate(
                command_id=command_id,
                intent_kind=str(row["intent_kind"] or ""),
                state=str(row["state"] or ""),
                position_id=position_id,
                token_id=str(row["token_id"] or ""),
                side=str(row["side"] or ""),
                size=str(row["size"] or ""),
                price=str(row["price"] or ""),
                venue_order_id=str(row["venue_order_id"] or ""),
                decision_id=str(row["decision_id"] or ""),
                latest_review_reason=latest_review_reason,
                order_fact_count=order_fact_count,
                trade_fact_count=trade_fact_count,
                position_event_count=position_event_count,
                lot_count=lot_count,
                position_current_count=position_current_count,
                unsafe_event_types=unsafe_event_types,
                blockers=blockers,
            )
        )
    return candidates


def _source_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001 - proof still records script identity.
        return "unknown"


def _load_live_client():
    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient()
    return client._ensure_v2_adapter()


def _proof_allows_clearance(proof: dict[str, Any]) -> bool:
    return (
        int(proof.get("matching_open_order_count", -1)) == 0
        and int(proof.get("matching_trade_count", -1)) == 0
        and proof.get("open_orders_checked") is True
        and proof.get("trades_checked") is True
    )


def _confirmed_trade_summary(match: tuple[dict, dict, list[dict]] | None) -> dict[str, Any] | None:
    if match is None:
        return None
    trade, order_match, open_orders = match
    return {
        "trade_id": str(trade.get("id") or trade.get("trade_id") or ""),
        "trade_status": str(trade.get("status") or trade.get("state") or ""),
        "venue_order_id": str(order_match.get("order_id") or ""),
        "matched_size": str(order_match.get("matched_size") or ""),
        "fill_price": str(order_match.get("fill_price") or ""),
        "match_source": str((order_match.get("maker_order") or {}).get("source") or "maker_order"),
        "open_order_count": len(open_orders),
    }


def _build_proofs(
    conn: sqlite3.Connection,
    candidates: list[ReviewCandidate],
    adapter,
) -> dict[str, dict[str, Any]]:
    proofs: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not candidate.local_clearance_eligible:
            continue
        proofs[candidate.command_id] = build_review_required_no_venue_exposure_proof(
            conn,
            candidate.command_id,
            adapter,
        )
    return proofs


def run(
    *,
    apply: bool,
    apply_confirmed_trade: bool = False,
    command_id: str | None = None,
    venue_proof: bool,
    adapter=None,
    reviewed_by: str = "operator",
) -> dict[str, Any]:
    if apply and not venue_proof:
        raise ValueError("--apply requires --venue-proof")
    if apply_confirmed_trade and not venue_proof:
        raise ValueError("--apply-confirmed-trade requires --venue-proof")
    if apply_confirmed_trade and not str(command_id or "").strip():
        raise ValueError("--apply-confirmed-trade requires --command-id")
    if apply and apply_confirmed_trade:
        raise ValueError("--apply and --apply-confirmed-trade are mutually exclusive")
    mutating = apply or apply_confirmed_trade
    conn = get_trade_connection(write_class="live") if mutating else get_trade_connection_read_only()
    conn.row_factory = sqlite3.Row
    try:
        all_candidates = find_candidates(conn)
        target_command_id = str(command_id or "").strip()
        candidates = [
            candidate
            for candidate in all_candidates
            if not target_command_id or candidate.command_id == target_command_id
        ]
        proofs: dict[str, dict[str, Any]] = {}
        confirmed_trade_matches: dict[str, dict[str, Any]] = {}
        proof_errors: dict[str, str] = {}
        if venue_proof:
            proof_adapter = adapter if adapter is not None else _load_live_client()
            for candidate in candidates:
                if not candidate.local_clearance_eligible:
                    continue
                try:
                    command = dict(
                        conn.execute(
                            "SELECT * FROM venue_commands WHERE command_id = ?",
                            (candidate.command_id,),
                        ).fetchone()
                    )
                    confirmed_trade = _confirmed_trade_summary(
                        _review_required_confirmed_trade_match(command, proof_adapter)
                    )
                    if confirmed_trade is not None:
                        confirmed_trade_matches[candidate.command_id] = confirmed_trade
                    proofs[candidate.command_id] = build_review_required_no_venue_exposure_proof(
                        conn,
                        candidate.command_id,
                        proof_adapter,
                    )
                except Exception as exc:  # noqa: BLE001 - report proof failure, do not guess.
                    proof_errors[candidate.command_id] = str(exc)

        applied: list[dict[str, Any]] = []
        if apply:
            for candidate in candidates:
                if not candidate.local_clearance_eligible:
                    continue
                proof = proofs.get(candidate.command_id)
                if proof is None or not _proof_allows_clearance(proof):
                    continue
                payload = clear_review_required_no_venue_exposure(
                    conn,
                    candidate.command_id,
                    venue_absence_proof=proof,
                    source_commit=_source_commit(),
                    source_function="operator_review",
                    reviewed_by=reviewed_by,
                )
                applied.append(
                    {
                        **candidate.as_dict(),
                        "result": "review_cleared_no_venue_exposure",
                        "payload": payload,
                    }
                )
            conn.commit()

        confirmed_trade_applied: list[dict[str, Any]] = []
        if apply_confirmed_trade:
            for candidate in candidates:
                confirmed_trade = confirmed_trade_matches.get(candidate.command_id)
                if confirmed_trade is None:
                    continue
                row = conn.execute(
                    "SELECT * FROM venue_commands WHERE command_id = ?",
                    (candidate.command_id,),
                ).fetchone()
                if row is None:
                    continue
                outcome = _review_required_confirmed_trade_recovery(
                    conn,
                    VenueCommand.from_row(row),
                    proof_adapter,
                )
                confirmed_trade_applied.append(
                    {
                        **candidate.as_dict(),
                        "result": outcome,
                        "confirmed_trade_proof": confirmed_trade,
                    }
                )
            conn.commit()

        candidate_payloads = []
        for candidate in candidates:
            proof = proofs.get(candidate.command_id)
            candidate_payloads.append(
                {
                    **candidate.as_dict(),
                    "venue_proof_checked": candidate.command_id in proofs,
                    "venue_proof_error": proof_errors.get(candidate.command_id),
                    "venue_absence_clear": _proof_allows_clearance(proof) if proof else False,
                    "venue_absence_proof": proof,
                    "confirmed_trade_recoverable": candidate.command_id in confirmed_trade_matches,
                    "confirmed_trade_proof": confirmed_trade_matches.get(candidate.command_id),
                }
            )
        return {
            "ok": True,
            "apply": apply,
            "apply_confirmed_trade": apply_confirmed_trade,
            "command_id": target_command_id,
            "venue_proof": venue_proof,
            "candidate_count": len(all_candidates),
            "selected_candidate_count": len(candidates),
            "local_clearance_eligible_count": sum(
                1 for candidate in candidates if candidate.local_clearance_eligible
            ),
            "venue_absence_clear_count": sum(
                1 for proof in proofs.values() if _proof_allows_clearance(proof)
            ),
            "confirmed_trade_recoverable_count": len(confirmed_trade_matches),
            "candidates": candidate_payloads,
            "applied": applied,
            "confirmed_trade_applied": confirmed_trade_applied,
            "venue_action": False,
            "db_backup_created": False,
            "repair_reason": REPAIR_REASON,
            "source_module": SOURCE_MODULE,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Append proof-backed clearance events.")
    parser.add_argument(
        "--apply-confirmed-trade",
        action="store_true",
        help="Append proof-backed fill recovery for one --command-id with a confirmed venue trade.",
    )
    parser.add_argument("--command-id", help="Limit classification/apply to one command id.")
    parser.add_argument(
        "--venue-proof",
        action="store_true",
        help="Read authenticated user open orders and trades for absence proof.",
    )
    parser.add_argument("--reviewed-by", default="operator", help="Reviewer label for --apply.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    result = run(
        apply=bool(args.apply),
        apply_confirmed_trade=bool(args.apply_confirmed_trade),
        command_id=args.command_id,
        venue_proof=bool(args.venue_proof),
        reviewed_by=str(args.reviewed_by),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "review-required no-venue-exposure repair: "
            f"{'APPLY' if args.apply else 'DRY-RUN'} "
            f"candidates={result['candidate_count']} "
            f"local_eligible={result['local_clearance_eligible_count']} "
            f"venue_clear={result['venue_absence_clear_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
