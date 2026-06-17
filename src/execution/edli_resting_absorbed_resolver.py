# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: boot crash-loop incident 2026-06-16 (live-trading daemon
#   bootout). TWO post-submit unknowns that neither the absence resolver (real
#   venue exposure exists -> refuses) nor the presence resolver can clear:
#     (1) a SUBMITTED-AND-LIVE-RESTING order (open order on the book, no fill);
#     (2) a CONFIRMED fill that filled MORE than the ordered size (venue 19.5 vs
#         order 18.5) AND was ALREADY absorbed into a non-EDLI-keyed
#         position_current row -> the presence resolver's double-count antibody
#         (filled > order_size*1.02) correctly refuses, and re-running the
#         recovered-fill path would materialise a SECOND position (the existing
#         position_id is NOT the EDLI bridge id -> the bridge would not dedup it).
#   Plan-evidence: docs/evidence/settlement_guard/boot_resting_absorbed_2026-06-16.md
"""Authenticated resolution for the two stuck post-submit-unknown classes that
the absence + presence resolvers structurally cannot clear.

THE TWO MISSING CASES (each money-committed, NOT releasable; cap RESERVED->CONSUMED):

  CASE A — SUBMITTED-AND-LIVE-RESTING
    The submit SUCCEEDED; the order is LIVE on the venue book with no fill yet.
    Absence refuses (a real open order is exposure -> never release). Presence
    refuses (no CONFIRMED trade). The money is committed to the resting order,
    so the cap is CONSUMED (NOT released — releasing would free capital for an
    order that is still live and can fill at any moment).
    Ownership proof: a LIVE open order on OUR token whose maker_address == OUR
    funder wallet (the same rigor as the presence resolver's _our_fill_legs, but
    for get_open_orders), whose economics match the aggregate (side BUY for
    buy_yes/buy_no, ~price, ~size). We reconcile the aggregate to "order is
    live" (Reconciled, pending_reconcile cleared) and CONSUME the cap. We DO NOT
    cancel the order and DO NOT materialise a position (there is no fill).

  CASE B — CONFIRMED-FILL-ALREADY-ABSORBED
    The order filled on the venue AND that fill is ALREADY a live, monitored
    position_current row (opened by the normal trade path, keyed on the trade
    id, NOT the EDLI bridge id). Only the EDLI cap ledger is stuck RESERVED and
    the aggregate is still pending_reconcile. The presence resolver refuses
    because (i) the venue filled more than the ordered size (its double-count
    antibody) and (ii) re-running its recovered-fill path would create a SECOND
    position (the existing position_id != edli_bridge_position_id). The fill is
    already booked; we reconcile ONLY the stuck cap ledger to the already-absorbed
    fill — we do NOT append a UserTradeObserved / recovered-fill (that would
    trip the durable bridge into a duplicate position). Cap RESERVED->CONSUMED.
    Ownership proof: BOTH a CONFIRMED trade on OUR token owned by OUR funder
    (maker_address == funder) AND a non-voided position_current row whose
    no_token_id/token_id == OUR token, whose order_id == the trade's
    taker_order_id, with matching direction + economics + fill_authority.

THE CONTRACT (mirrors absence/presence rigor):
- Ownership is NEVER inferred from local rows alone. CASE A requires a venue
  open order owned by our funder. CASE B requires BOTH a funder-owned venue
  trade AND an existing non-voided position proving prior absorption.
- A foreign order/trade on the shared wallet (the operator co-trades non-weather
  markets on the same proxy) NEVER qualifies — every path funder-checks.
- Forward-only event-sourced appends through the canonical ledgers under the
  world write lock. NO raw UPDATE/DELETE. Idempotent (re-run is a no-op once
  the aggregate is reconciled and the cap is CONSUMED).
- A live-resting OR a filled order's cap goes RESERVED->CONSUMED, NEVER RELEASED.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import RECONCILE_SOURCE, append_reconciled
from src.state.db import (
    get_trade_connection_read_only,
    get_world_connection,
    get_world_connection_read_only,
    world_write_lock,
)

from src.execution.edli_absence_resolver import (
    _cap_usage_id_for,
    _latest_payload,
    _pending_aggregates,
    _read_authenticated_venue,
    _readiness_counts,
)

logger = logging.getLogger(__name__)

RESTING_RESOLUTION_REASON = "AUTHENTICATED_CLOB_LIVE_RESTING_ORDER_OWNED_BY_FUNDER"
ABSORBED_RESOLUTION_REASON = "AUTHENTICATED_CLOB_FILL_ALREADY_ABSORBED_INTO_POSITION"

# Economics tolerances. Price within one cent (Polymarket tick granularity);
# size within 5% to absorb venue rounding / partial-rest jitter without ever
# matching a differently-sized foreign order.
_PRICE_TOL = 0.011
_SIZE_REL_TOL = 0.05
_NON_VOID_PHASES = (
    "pending_entry",
    "active",
    "day0_window",
    "pending_exit",
    "economically_closed",
    "settled",
    "admin_closed",
)


def _f(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _our_live_resting_order(
    open_orders: list[dict[str, Any]],
    *,
    token_id: str,
    funder_address: str,
    limit_price: float | None,
    order_size: float | None,
) -> dict[str, Any] | None:
    """Return the ONE live open order that is OUR funder's order on OUR token and
    matches the aggregate's economics, or None.

    Shared-wallet antibody: maker_address (the venue order owner) MUST equal our
    funder. A foreign order the operator placed on the same token never matches.
    Economics: venue side is BUY (both buy_yes and buy_no BUY their outcome
    token), price within a tick, original size within tolerance.
    """
    funder = str(funder_address or "").lower()
    if not funder:
        return None
    tok = str(token_id)
    matches: list[dict[str, Any]] = []
    for order in open_orders:
        if str(order.get("asset_id") or "") != tok:
            continue
        if str(order.get("status") or "").upper() != "LIVE":
            continue
        if str(order.get("maker_address") or "").lower() != funder:
            continue
        if str(order.get("side") or "").upper() != "BUY":
            continue
        price = _f(order.get("price"))
        size = _f(order.get("original_size"))
        if price is None or size is None or size <= 0:
            continue
        if limit_price is not None and abs(price - float(limit_price)) > _PRICE_TOL:
            continue
        if order_size is not None and order_size > 0:
            if abs(size - float(order_size)) > _SIZE_REL_TOL * float(order_size) + 1e-9:
                continue
        matches.append(order)
    if len(matches) != 1:
        # Zero matches -> not a resting case. >1 -> ambiguous (refuse; fail-closed
        # preserved). A single funder-owned economics-matched live order is the
        # unambiguous proof.
        return None
    return matches[0]


def _our_confirmed_trade_legs(
    trades: list[dict[str, Any]], *, token_id: str, funder_address: str
) -> list[dict[str, Any]]:
    """CONFIRMED trade legs on OUR token owned by OUR funder (taker or maker).

    Identical ownership rigor to the presence resolver's _our_fill_legs, but
    WITHOUT the size-vs-order antibody (this resolver's CASE B intentionally
    handles the over-fill / already-absorbed class). Returns the matched legs so
    the proof can record the venue order id + economics.
    """
    funder = str(funder_address or "").lower()
    tok = str(token_id)
    legs: list[dict[str, Any]] = []
    for trade in trades:
        if str(trade.get("status") or "").upper() != "CONFIRMED":
            continue
        trade_id = str(trade.get("id") or "")
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
                }
            )
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
                    }
                )
    return [leg for leg in legs if leg["price"] is not None and leg["size"] and leg["size"] > 0]


def _existing_absorbed_position(
    *,
    token_id: str,
    venue_order_ids: set[str],
    direction: str,
) -> dict[str, Any] | None:
    """Find a non-voided position_current row (zeus_trades.db) that PROVES this
    fill is already absorbed: it carries OUR token (token_id or no_token_id) AND
    a venue order id matching one of the trade legs' venue order ids, AND a
    matching direction, AND a venue-confirmed fill authority.

    This is the ownership-by-prior-absorption proof for CASE B. Read-only; never
    writes the trade DB.
    """
    tok = str(token_id)
    trade_conn = get_trade_connection_read_only()
    try:
        trade_conn.row_factory = sqlite3.Row
        rows = trade_conn.execute(
            """
            SELECT position_id, token_id, no_token_id, direction, shares,
                   entry_price, phase, fill_authority, order_id, condition_id
            FROM position_current
            WHERE (token_id = ? OR no_token_id = ?)
            """,
            (tok, tok),
        ).fetchall()
    finally:
        trade_conn.close()
    for row in rows:
        if str(row["phase"] or "") not in _NON_VOID_PHASES:
            continue
        if direction and str(row["direction"] or "") != str(direction):
            continue
        order_id = str(row["order_id"] or "")
        if order_id and order_id in venue_order_ids:
            # Strongest proof: the position's recorded venue order id IS one of
            # the trade legs we just read from the venue. Same fill, already
            # booked. fill_authority is informational provenance.
            return {
                "position_id": str(row["position_id"]),
                "token_id": str(row["token_id"] or ""),
                "no_token_id": str(row["no_token_id"] or ""),
                "direction": str(row["direction"] or ""),
                "shares": _f(row["shares"]),
                "entry_price": _f(row["entry_price"]),
                "phase": str(row["phase"] or ""),
                "fill_authority": str(row["fill_authority"] or ""),
                "order_id": order_id,
                "condition_id": str(row["condition_id"] or ""),
            }
    return None


def _latest_receipt_hash_opt(conn: sqlite3.Connection, aggregate_id: str) -> str:
    row = conn.execute(
        """
        SELECT payload_json
        FROM edli_live_order_events
        WHERE aggregate_id = ?
          AND json_extract(payload_json, '$.execution_receipt_hash') IS NOT NULL
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(json.loads(str(row["payload_json"])).get("execution_receipt_hash") or "")


def build_resolution(
    conn,
    aggregate_id: str,
    *,
    open_orders: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    funder_address: str,
) -> dict[str, Any] | None:
    """Classify a stuck aggregate as CASE A (resting) or CASE B (absorbed) and
    build the proof, or return None when neither applies (fail-closed preserved
    — the caller keeps the original raise for genuinely ambiguous unknowns)."""
    submit_unknown = _latest_payload(conn, aggregate_id, "SubmitUnknown")
    if submit_unknown.get("venue_call_started") is not True:
        return None
    plan = _latest_payload(conn, aggregate_id, "SubmitPlanBuilt")
    token_id = str(plan.get("token_id") or "")
    if not token_id:
        return None
    direction = str(plan.get("direction") or "")
    limit_price = _f(plan.get("limit_price"))
    order_size = _f(plan.get("size"))
    base = {
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
        "direction": direction,
        "funder_address": str(funder_address),
        "limit_price": limit_price,
        "order_size": order_size,
    }

    # CASE B first: a CONFIRMED funder-owned fill that is ALREADY absorbed. Tried
    # before CASE A so a filled order (which is no longer resting) is never
    # mis-classified as resting.
    legs = _our_confirmed_trade_legs(trades, token_id=token_id, funder_address=funder_address)
    if legs:
        venue_order_ids = {str(leg["venue_order_id"]) for leg in legs if leg["venue_order_id"]}
        position = _existing_absorbed_position(
            token_id=token_id, venue_order_ids=venue_order_ids, direction=direction
        )
        if position is not None:
            proof = {
                **base,
                "case": "CONFIRMED_FILL_ALREADY_ABSORBED",
                "reconcile_reason": ABSORBED_RESOLUTION_REASON,
                "venue_trade_exists": True,
                "venue_order_exists": False,
                "matched_legs": legs,
                "matched_trade_ids": sorted({leg["trade_id"] for leg in legs}),
                "absorbed_position": position,
                "cap_transition": "CONSUMED",
            }
            proof["proof_hash"] = hashlib.sha256(
                json.dumps(proof, sort_keys=True, default=str).encode()
            ).hexdigest()
            return proof
        # Funder-owned fill but NO existing position proving prior absorption.
        # That is NOT this resolver's case — defer to presence (it materialises a
        # fresh position) or fail-closed. Do not invent absorption.
        return None

    # CASE A: a LIVE funder-owned resting order matching our economics.
    order = _our_live_resting_order(
        open_orders,
        token_id=token_id,
        funder_address=funder_address,
        limit_price=limit_price,
        order_size=order_size,
    )
    if order is not None:
        proof = {
            **base,
            "case": "SUBMITTED_AND_LIVE_RESTING",
            "reconcile_reason": RESTING_RESOLUTION_REASON,
            "venue_trade_exists": False,
            "venue_order_exists": True,
            "live_order": {
                "venue_order_id": str(order.get("id") or order.get("orderID") or ""),
                "status": str(order.get("status") or ""),
                "side": str(order.get("side") or ""),
                "price": _f(order.get("price")),
                "original_size": _f(order.get("original_size")),
                "size_matched": _f(order.get("size_matched")),
                "maker_address": str(order.get("maker_address") or ""),
                "asset_id": str(order.get("asset_id") or ""),
            },
            "cap_transition": "CONSUMED",
        }
        proof["proof_hash"] = hashlib.sha256(
            json.dumps(proof, sort_keys=True, default=str).encode()
        ).hexdigest()
        return proof

    return None


def resolve_resting_or_absorbed(
    *, aggregate_id: str | None, apply: bool, log: Callable[[str], None] = print
) -> int:
    """Resolve stuck post-submit unknowns that are either LIVE-RESTING (CASE A)
    or CONFIRMED-FILL-ALREADY-ABSORBED (CASE B). Returns 0 when nothing stuck
    remains, 1 otherwise. Aggregates that match neither case are left untouched
    (the boot caller's fail-closed raise still fires for them)."""
    open_orders, trades = _read_authenticated_venue()
    funder_address = _funder_address()
    ro = get_world_connection_read_only()
    try:
        before = _readiness_counts(ro)
        aggregates = _pending_aggregates(ro, aggregate_id)
        log(f"BEFORE: unresolved_submit={before[0]} reserved_cap={before[1]}")
        log(f"pending aggregates selected: {len(aggregates)}")
        resolutions = [
            build_resolution(
                ro,
                agg,
                open_orders=open_orders,
                trades=trades,
                funder_address=funder_address,
            )
            for agg in aggregates
        ]
        for agg, proof in zip(aggregates, resolutions):
            if proof is None:
                log(f"SKIP {agg[:80]}... (neither resting nor already-absorbed)")
                continue
            log(
                "RESTING_ABSORBED_PROOF "
                + json.dumps(
                    {
                        "aggregate_id": proof["aggregate_id"][:80] + "...",
                        "case": proof["case"],
                        "token_id": proof["token_id"],
                        "cap_transition": proof["cap_transition"],
                        "venue_order_exists": proof["venue_order_exists"],
                        "venue_trade_exists": proof["venue_trade_exists"],
                        "absorbed_position_id": (
                            proof.get("absorbed_position", {}) or {}
                        ).get("position_id"),
                        "live_order_id": (proof.get("live_order", {}) or {}).get(
                            "venue_order_id"
                        ),
                        "proof_hash": proof["proof_hash"],
                    },
                    sort_keys=True,
                )
            )
    finally:
        ro.close()

    actionable = [(agg, p) for agg, p in zip(aggregates, resolutions) if p is not None]
    if not actionable:
        log("Nothing this resolver can clear.")
        return 1 if before != (0, 0) else 0
    if not apply:
        log("DRY-RUN: re-run with --apply to append Reconciled + CONSUME the cap.")
        return 0

    now = datetime.now(timezone.utc)
    conn = get_world_connection(write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        with world_write_lock(conn):
            ledger = LiveOrderAggregateLedger(conn)
            cap_ledger = LiveCapLedger(conn)
            for agg, proof in actionable:
                event_id = str(proof["event_id"])
                final_intent_id = str(proof["final_intent_id"])
                execution_command_id = str(proof["execution_command_id"])
                # Idempotency: if the aggregate is already reconciled (not
                # pending) AND its cap is no longer RESERVED, skip.
                proj = conn.execute(
                    "SELECT pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
                    (agg,),
                ).fetchone()
                usage = _cap_usage_id_for(conn, final_intent_id)
                if proj is not None and not bool(proj["pending_reconcile"]) and usage is None:
                    log(f"ALREADY_RESOLVED {agg[:80]}... (idempotent skip)")
                    continue
                # 1) Clear the pending-reconcile readiness block. NO
                #    UserTradeObserved / recovered-fill: CASE B's fill is already
                #    a position (re-materialising would duplicate it); CASE A has
                #    no fill at all. This is a ledger-only reconcile to venue
                #    truth.
                if proj is not None and bool(proj["pending_reconcile"]):
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
                            "venue_order_exists": bool(proof["venue_order_exists"]),
                            "venue_trade_exists": bool(proof["venue_trade_exists"]),
                            "cap_transition_recommendation": "CONSUMED",
                            "reconcile_reason": str(proof["reconcile_reason"]),
                            "authenticated_resting_absorbed_proof": proof,
                        },
                    )
                # 2) Transition the cap RESERVED -> CONSUMED (money committed to a
                #    live resting order OR already spent on an absorbed fill;
                #    NEVER released). Direct cap-ledger consume mirrors the
                #    presence resolver — a CapTransitioned(CONSUMED) event is not
                #    appendable here (the aggregate has no VenueSubmitAcknowledged).
                if usage is not None:
                    cap_ledger.consume(
                        usage,
                        final_intent_id=final_intent_id,
                        execution_command_id=execution_command_id,
                    )
                log(
                    f"RESOLVED[{proof['case']}] {agg[:80]}... cap_usage={usage} "
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
