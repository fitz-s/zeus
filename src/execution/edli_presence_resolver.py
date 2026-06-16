# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: boot crash-loop incident 2026-06-16 (#122 db-lock orphaned a
#   FILLED maker order into UNRESOLVED_SUBMIT_UNKNOWN; the absence resolver
#   correctly REFUSES because real exposure exists, but there was no PRESENCE
#   path -> permanent crash-loop). Plan-evidence:
#   docs/evidence/settlement_guard/boot_presence_reconcile_2026-06-16.md
"""Authenticated-PRESENCE resolution for EDLI post-submit unknowns.

The symmetric counterpart to ``edli_absence_resolver``. When a post-submit
``SubmitUnknown`` order ACTUALLY FILLED on the venue (the #122 db-lock orphan:
the venue_order_id was never recorded because the recording write hit
``database is locked``, so the order/trade poller never ingested the fill), the
absence proof correctly refuses ("matching exposure; do not release cap"). This
module proves the PRESENCE of that fill and reconciles it to ``FILL_CONFIRMED``
through the EXISTING canonical recovered-fill seam
(``live_order_reconcile.append_reconcile_recovered_fill`` — built for exactly
this orphan class, the HK 30°C 2026-06-12 incident), so the position
materialises through the canonical fill->position bridge
(``_edli_durable_fill_bridge_scan`` / boot fill-bridge recovery) and the cap
transitions ``RESERVED -> CONSUMED`` (NOT released — the money was spent).

THE CONTRACT (mirrors the absence contract's rigor):
- Presence is NEVER inferred from local rows. The proof reads authenticated CLOB
  trades and requires a CONFIRMED trade whose matched leg is OWNED BY OUR FUNDER
  wallet on OUR token. A foreign order on the same token (operator co-trading on
  the shared wallet) can never be attributed — the leg must match our funder
  AND our token AND the order's economics, or the proof refuses.
- Resolution appends UserTradeObserved(FILL_CONFIRMED) + Reconciled(not pending)
  + cap CONSUMED through the canonical event-sourced ledgers under the world
  write lock. No raw writes. No hand-rolled position math (the canonical bridge
  reads the FILL_CONFIRMED event payload).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import (
    RECONCILE_SOURCE,
    append_reconcile_recovered_fill,
    append_reconciled,
)
from src.state.db import (
    get_world_connection,
    get_world_connection_read_only,
    world_write_lock,
)

from src.execution.edli_absence_resolver import (
    _cap_usage_id_for,
    _latest_payload,
    _latest_receipt_hash,
    _pending_aggregates,
    _read_authenticated_venue,
    _readiness_counts,
)

logger = logging.getLogger(__name__)

PRESENCE_RESOLUTION_REASON = "AUTHENTICATED_CLOB_CONFIRMED_FILL_OWNED_BY_FUNDER"


def _f(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _our_fill_legs(
    trade: dict[str, Any], token_id: str, funder_address: str
) -> list[dict[str, Any]]:
    """Return the legs of THIS trade that are OUR order on OUR token.

    A leg qualifies ONLY when its wallet == our funder AND its asset == our
    token. This is the shared-wallet antibody: a counterparty's leg, or a
    foreign order the operator placed on the same token, never qualifies. Both
    the taker (top-level) and every maker sub-order are checked.
    """
    if str(trade.get("status") or "").upper() != "CONFIRMED":
        return []
    funder = str(funder_address or "").lower()
    tok = str(token_id)
    legs: list[dict[str, Any]] = []
    trade_id = str(trade.get("id") or "")
    # Taker leg (top-level perspective).
    if (
        str(trade.get("asset_id") or "") == tok
        and str(trade.get("maker_address") or "").lower() == funder
    ):
        legs.append(
            {
                "role": "TAKER",
                "trade_id": trade_id,
                "venue_order_id": str(trade.get("taker_order_id") or ""),
                "price": _f(trade.get("price")),
                "size": _f(trade.get("size")),
                "fees": _f(trade.get("fees")) or 0.0,
            }
        )
    # Maker leg(s).
    for mk in trade.get("maker_orders") or []:
        if (
            str(mk.get("asset_id") or "") == tok
            and str(mk.get("maker_address") or "").lower() == funder
        ):
            legs.append(
                {
                    "role": "MAKER",
                    "trade_id": trade_id,
                    "venue_order_id": str(mk.get("order_id") or ""),
                    "price": _f(mk.get("price")),
                    "size": _f(mk.get("matched_amount")),
                    "fees": _f(mk.get("fee_rate_bps")) and 0.0 or 0.0,
                }
            )
    return [leg for leg in legs if leg["price"] is not None and leg["size"] and leg["size"] > 0]


def build_presence_proof(
    conn,
    aggregate_id: str,
    *,
    trades: list[dict[str, Any]],
    funder_address: str,
) -> dict[str, Any]:
    """Prove our post-submit-unknown order FILLED on-venue. Raise if it did not,
    or if no confirmed trade is attributable to OUR funder on OUR token."""
    submit_unknown = _latest_payload(conn, aggregate_id, "SubmitUnknown")
    if submit_unknown.get("venue_call_started") is not True:
        raise RuntimeError("SubmitUnknown is not a post-submit unknown; presence proof N/A")
    plan = _latest_payload(conn, aggregate_id, "SubmitPlanBuilt")
    token_id = str(plan.get("token_id") or "")
    if not token_id:
        raise RuntimeError("SubmitPlanBuilt missing token_id")
    order_size = _f(plan.get("size")) or 0.0

    # Collect every leg of every confirmed trade attributable to us on this token.
    legs: list[dict[str, Any]] = []
    for trade in trades:
        legs.extend(_our_fill_legs(trade, token_id, funder_address))
    # Dedupe by (trade_id, venue_order_id) — one economic fill leg per pair.
    seen: set[tuple[str, str]] = set()
    unique_legs: list[dict[str, Any]] = []
    for leg in legs:
        key = (leg["trade_id"], leg["venue_order_id"])
        if key in seen:
            continue
        seen.add(key)
        unique_legs.append(leg)
    if not unique_legs:
        raise RuntimeError(
            "no CONFIRMED trade owned by our funder on this token; not a presence "
            "(absence resolver or quarantine applies)"
        )

    total_size = sum(float(leg["size"]) for leg in unique_legs)
    total_notional = sum(float(leg["size"]) * float(leg["price"]) for leg in unique_legs)
    total_fees = sum(float(leg["fees"]) for leg in unique_legs)
    if total_size <= 0:
        raise RuntimeError("presence legs sum to non-positive size")
    # Double-count antibody (defense-in-depth): our matched fill can never exceed
    # the order's intended size (modulo venue rounding). If leg attribution ever
    # over-counts — e.g. a trade-schema surprise that lets both a maker and a
    # taker leg of the SAME order qualify — REFUSE rather than record an inflated
    # position with a blended cost basis. Fail-closed beats a wrong live position.
    if order_size and total_size > order_size * 1.02 + 1e-6:
        raise RuntimeError(
            f"presence legs sum {total_size} exceed order size {order_size} "
            f"(>2% over); refusing as possible mis-attribution / double-count"
        )
    avg_price = total_notional / total_size
    # All legs of one order share the same venue_order_id; take the first.
    venue_order_id = unique_legs[0]["venue_order_id"]
    if not venue_order_id:
        raise RuntimeError("matched leg carries no venue order id")
    # Fully-vs-partially filled relative to the order's intended size (truthful
    # venue command state for the recovered-fill proof chain).
    venue_command_state = "FILLED" if (order_size and total_size + 1e-9 >= order_size) else "PARTIAL"

    proof = {
        "schema_version": 1,
        "source": "authenticated_clob_user_read",
        "owner_scope": "authenticated_funder",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "aggregate_id": aggregate_id,
        "event_id": str(plan.get("event_id") or ""),
        "final_intent_id": str(plan.get("final_intent_id") or ""),
        "execution_command_id": str(submit_unknown.get("execution_command_id") or ""),
        "token_id": token_id,
        "condition_id": str(plan.get("condition_id") or ""),
        "direction": str(plan.get("direction") or ""),
        "funder_address": str(funder_address),
        "order_size": order_size,
        "venue_order_id": venue_order_id,
        "venue_command_state": venue_command_state,
        "filled_size": total_size,
        "avg_fill_price": avg_price,
        "fees": total_fees,
        "matched_legs": unique_legs,
        "matched_trade_ids": sorted({leg["trade_id"] for leg in unique_legs}),
    }
    proof["proof_hash"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, default=str).encode()
    ).hexdigest()
    return proof


def resolve_presence(
    *, aggregate_id: str | None, apply: bool, log: Callable[[str], None] = print
) -> int:
    """Resolve stuck post-submit unknowns that ACTUALLY FILLED, by authenticated
    presence proof. Returns 0 when nothing stuck remains, 1 otherwise."""
    open_orders, trades = _read_authenticated_venue()
    del open_orders
    funder_address = _funder_address()
    ro = get_world_connection_read_only()
    try:
        before = _readiness_counts(ro)
        aggregates = _pending_aggregates(ro, aggregate_id)
        log(f"BEFORE: unresolved_submit={before[0]} reserved_cap={before[1]}")
        log(f"pending aggregates selected: {len(aggregates)}")
        proofs = [
            build_presence_proof(ro, agg, trades=trades, funder_address=funder_address)
            for agg in aggregates
        ]
        for proof in proofs:
            log(
                "PRESENCE_PROOF "
                + json.dumps(
                    {
                        "aggregate_id": proof["aggregate_id"][:80] + "...",
                        "token_id": proof["token_id"],
                        "venue_order_id": proof["venue_order_id"],
                        "filled_size": proof["filled_size"],
                        "avg_fill_price": proof["avg_fill_price"],
                        "venue_command_state": proof["venue_command_state"],
                        "matched_trade_ids": proof["matched_trade_ids"],
                        "proof_hash": proof["proof_hash"],
                    },
                    sort_keys=True,
                )
            )
    finally:
        ro.close()
    if not proofs:
        log("Nothing to resolve.")
        return 0
    if not apply:
        log("DRY-RUN: re-run with --apply to append recovered-fill + Reconciled + CONSUMED.")
        return 0

    now = datetime.now(timezone.utc)
    conn = get_world_connection(write_class="live")
    import sqlite3

    conn.row_factory = sqlite3.Row
    try:
        with world_write_lock(conn):
            ledger = LiveOrderAggregateLedger(conn)
            cap_ledger = LiveCapLedger(conn)
            for proof in proofs:
                agg = str(proof["aggregate_id"])
                event_id = str(proof["event_id"])
                final_intent_id = str(proof["final_intent_id"])
                execution_command_id = str(proof["execution_command_id"])
                receipt_hash = _latest_receipt_hash(conn, agg)
                # Deterministic dedup key (the aggregate ledger requires
                # raw_user_channel_message_hash on every UserTradeObserved and
                # dedupes on it). STABLE across boots — NO timestamp — so a
                # re-run of this resolver is idempotent (the duplicate append is
                # rejected, never a second position).
                message_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "recovery": PRESENCE_RESOLUTION_REASON,
                            "aggregate_id": agg,
                            "venue_order_id": str(proof["venue_order_id"]),
                            "token_id": str(proof["token_id"]),
                            "matched_trade_ids": proof["matched_trade_ids"],
                            "filled_size": float(proof["filled_size"]),
                            "avg_fill_price": float(proof["avg_fill_price"]),
                        },
                        sort_keys=True,
                        default=str,
                    ).encode()
                ).hexdigest()
                # 1) Canonical recovered-fill -> FILL_CONFIRMED (the bridge will
                #    materialise the position from this event's economics).
                append_reconcile_recovered_fill(
                    ledger,
                    aggregate_id=agg,
                    event_id=event_id,
                    final_intent_id=final_intent_id,
                    venue_order_id=str(proof["venue_order_id"]),
                    occurred_at=now,
                    payload={
                        "raw_user_channel_message_hash": message_hash,
                        "source_trade_fact_authority": "authenticated_clob_user_read",
                        "venue_command_state": str(proof["venue_command_state"]),
                        "recovery_basis": PRESENCE_RESOLUTION_REASON,
                        "execution_command_id": execution_command_id,
                        "execution_receipt_hash": receipt_hash,
                        "trade_id": (proof["matched_trade_ids"] or [""])[0],
                        "matched_trade_ids": proof["matched_trade_ids"],
                        "filled_size": float(proof["filled_size"]),
                        "avg_fill_price": float(proof["avg_fill_price"]),
                        "fees": float(proof["fees"]),
                        "token_id": str(proof["token_id"]),
                        "condition_id": str(proof["condition_id"]),
                        "direction": str(proof["direction"]),
                        "authenticated_presence_proof": proof,
                    },
                )
                # 2) Clear the pending-reconcile readiness block.
                append_reconciled(
                    ledger,
                    aggregate_id=agg,
                    event_id=event_id,
                    final_intent_id=final_intent_id,
                    source=RECONCILE_SOURCE,
                    pending_reconcile=False,
                    occurred_at=now,
                    payload={
                        "execution_command_id": execution_command_id,
                        "venue_order_exists": False,
                        "venue_trade_exists": True,
                        "cap_transition_recommendation": "CONSUMED",
                        "reconcile_reason": PRESENCE_RESOLUTION_REASON,
                        "authenticated_presence_proof": proof,
                    },
                )
                # 3) Transition the cap RESERVED -> CONSUMED (money was spent;
                #    NOT released).
                usage = _cap_usage_id_for(conn, final_intent_id)
                if usage is not None:
                    cap_ledger.consume(
                        usage,
                        final_intent_id=final_intent_id,
                        execution_command_id=execution_command_id,
                    )
                log(
                    f"PRESENCE_RESOLVED {agg[:80]}... cap_usage={usage} "
                    f"filled={proof['filled_size']}@{proof['avg_fill_price']:.4f} "
                    f"proof_hash={proof['proof_hash']}"
                )
    finally:
        conn.close()

    ro = get_world_connection_read_only()
    try:
        after = _readiness_counts(ro)
    finally:
        ro.close()
    log(f"AFTER: unresolved_submit={after[0]} reserved_cap={after[1]}")
    return 0 if after == (0, 0) else 1


def _funder_address() -> str:
    from src.data.polymarket_client import PolymarketClient

    with PolymarketClient(public_http_timeout=15) as clob:
        adapter = clob._ensure_v2_adapter()
        return str(getattr(adapter, "funder_address", "") or "")
