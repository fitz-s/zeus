# Created: 2026-06-16
# Last reused or audited: 2026-07-23
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
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import (
    RECONCILE_SOURCE,
    append_reconcile_recovered_fill,
    append_reconciled,
)
from src.state.db import (
    get_trade_connection_read_only,
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
_MATCH_TIME_SKEW = timedelta(seconds=5)
_MATCH_TIME_LAG = timedelta(seconds=30)


def _f(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _d(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError(f"{field} is not a decimal") from None
    if not parsed.is_finite():
        raise RuntimeError(f"{field} is not finite")
    return parsed


def _utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
        epoch = float(value)
        if epoch > 10_000_000_000:
            epoch /= 1000.0
        parsed = datetime.fromtimestamp(epoch, tz=timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_event_time(conn: Any, aggregate_id: str, event_type: str) -> datetime:
    row = conn.execute(
        """
        SELECT occurred_at
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = ?
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (aggregate_id, event_type),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{event_type} event time is missing")
    try:
        value = row["occurred_at"]
    except (IndexError, TypeError):
        value = row[0]
    return _utc(value)


def _venue_command_for_decision_id(decision_id: str) -> dict[str, Any]:
    conn = get_trade_connection_read_only()
    try:
        rows = conn.execute(
            """
            SELECT command.command_id,
                   command.decision_id,
                   command.intent_kind,
                   command.token_id,
                   command.side,
                   command.size,
                   command.price,
                   command.state,
                   command.created_at,
                   command.venue_order_id,
                   envelope.size AS envelope_size,
                   envelope.price AS envelope_price,
                   envelope.order_type AS envelope_order_type
            FROM venue_commands command
            LEFT JOIN venue_submission_envelopes envelope
              ON envelope.envelope_id = command.envelope_id
            WHERE command.decision_id = ?
            """,
            (decision_id,),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                f"expected exactly one canonical venue command for decision_id; found {len(rows)}"
            )
        exact = rows[0]
        if not exact["envelope_size"] or not exact["envelope_price"] or not exact["envelope_order_type"]:
            raise RuntimeError("canonical venue command is missing its pre-submit envelope economics")
    finally:
        conn.close()
    return {key: exact[key] for key in exact.keys()}


def _normalized_order_type(value: object) -> str:
    text = str(value or "").strip().upper()
    return text.removesuffix("_LIMIT")


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
        price = _d(trade.get("price"), "taker fill price")
        size = _d(trade.get("size"), "taker fill size")
        legs.append(
            {
                "role": "TAKER",
                "trade_id": trade_id,
                "venue_order_id": str(trade.get("taker_order_id") or ""),
                "price": float(price),
                "size": float(size),
                "price_decimal": format(price, "f"),
                "size_decimal": format(size, "f"),
                "fees": _f(trade.get("fees")) or 0.0,
                "match_time": trade.get("match_time"),
                "side": str(trade.get("side") or "").upper(),
                "trader_side": str(trade.get("trader_side") or "").upper(),
            }
        )
    # Maker leg(s).
    for mk in trade.get("maker_orders") or []:
        if (
            str(mk.get("asset_id") or "") == tok
            and str(mk.get("maker_address") or "").lower() == funder
        ):
            price = _d(mk.get("price"), "maker fill price")
            size = _d(mk.get("matched_amount"), "maker fill size")
            legs.append(
                {
                    "role": "MAKER",
                    "trade_id": trade_id,
                    "venue_order_id": str(mk.get("order_id") or ""),
                    "price": float(price),
                    "size": float(size),
                    "price_decimal": format(price, "f"),
                    "size_decimal": format(size, "f"),
                    "fees": _f(mk.get("fee_rate_bps")) and 0.0 or 0.0,
                    "match_time": trade.get("match_time"),
                    "side": str(mk.get("side") or "").upper(),
                    "trader_side": str(trade.get("trader_side") or "").upper(),
                }
            )
    return [leg for leg in legs if _d(leg["price_decimal"], "fill price") > 0 and _d(leg["size_decimal"], "fill size") > 0]


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
    if str(plan.get("direction") or "") not in {"buy_yes", "buy_no"}:
        raise RuntimeError("SubmitPlanBuilt direction is not a token BUY")
    order_size = _f(plan.get("size")) or 0.0
    limit_price = _f(plan.get("limit_price"))
    order_type = str(plan.get("order_type") or "").strip().upper()
    is_fak = order_type in {"FAK", "FAK_LIMIT"}
    if order_size <= 0 or limit_price is None or limit_price <= 0:
        raise RuntimeError("SubmitPlanBuilt missing positive size/limit_price")
    attempted_at = _latest_event_time(conn, aggregate_id, "VenueSubmitAttempted")
    unknown_at = _latest_event_time(conn, aggregate_id, "SubmitUnknown")
    execution_command_id = str(submit_unknown.get("execution_command_id") or "")
    if not execution_command_id:
        raise RuntimeError("SubmitUnknown missing execution_command_id")
    command = _venue_command_for_decision_id(execution_command_id)
    command_size = _d(command.get("envelope_size") or command.get("size"), "command size")
    command_price = _d(command.get("envelope_price") or command.get("price"), "command price")
    if str(command.get("decision_id") or "") != execution_command_id:
        raise RuntimeError("canonical venue command decision identity mismatch")
    if str(command.get("intent_kind") or "").upper() != "ENTRY":
        raise RuntimeError("canonical venue command is not an ENTRY")
    if str(command.get("token_id") or "") != token_id:
        raise RuntimeError("canonical venue command token mismatch")
    if str(command.get("side") or "").upper() != "BUY":
        raise RuntimeError("canonical venue command side is not BUY")
    if str(command.get("state") or "").upper() not in {
        "SUBMITTING",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "REVIEW_REQUIRED",
    }:
        raise RuntimeError("canonical venue command is not in an unresolved submit state")
    command_venue_order_id = str(command.get("venue_order_id") or "").strip()
    if not command_venue_order_id:
        raise RuntimeError(
            "canonical venue command has no pre-persisted venue_order_id; exact causal binding is unavailable"
        )
    if command_size != _d(plan.get("size"), "plan size"):
        raise RuntimeError("canonical venue command size mismatch")
    if command_price != _d(plan.get("limit_price"), "plan limit_price"):
        raise RuntimeError("canonical venue command price mismatch")
    command_order_type = _normalized_order_type(command.get("envelope_order_type") or order_type)
    if command_order_type != _normalized_order_type(order_type):
        raise RuntimeError("canonical venue command order_type mismatch")
    command_created_at = _utc(command.get("created_at"))
    if command_created_at < attempted_at - _MATCH_TIME_SKEW or command_created_at > unknown_at + _MATCH_TIME_LAG:
        raise RuntimeError("canonical venue command is outside this submit attempt window")

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

    venue_order_ids = {str(leg["venue_order_id"] or "") for leg in unique_legs}
    if "" in venue_order_ids or len(venue_order_ids) != 1:
        raise RuntimeError(
            "presence legs do not bind to exactly one venue order; refusing possible cross-order attribution"
        )
    venue_order_id = next(iter(venue_order_ids))
    if venue_order_id != command_venue_order_id:
        raise RuntimeError(
            "authenticated presence order id does not match the canonical venue command"
        )
    if is_fak and (
        len(unique_legs) != 1
        or unique_legs[0]["role"] != "TAKER"
        or unique_legs[0]["trader_side"] != "TAKER"
        or unique_legs[0]["side"] != "BUY"
    ):
        raise RuntimeError("FAK presence must be one authenticated BUY taker leg")
    if any(leg["side"] != "BUY" for leg in unique_legs):
        raise RuntimeError("presence leg side is missing or not BUY")
    for leg in unique_legs:
        match_time = _utc(leg.get("match_time"))
        if match_time < attempted_at - _MATCH_TIME_SKEW or (
            is_fak and match_time > unknown_at + _MATCH_TIME_LAG
        ):
            raise RuntimeError(
                "presence leg match_time is outside this submit attempt window; refusing historical attribution"
            )
        leg_price = _d(leg["price_decimal"], "fill price")
        if leg_price <= 0:
            raise RuntimeError("presence leg price is not positive")
        if leg_price > command_price:
            raise RuntimeError("presence leg price exceeds the submitted BUY limit")

    total_size_decimal = sum(
        (_d(leg["size_decimal"], "fill size") for leg in unique_legs), Decimal(0)
    )
    total_notional_decimal = sum(
        (
            _d(leg["size_decimal"], "fill size")
            * _d(leg["price_decimal"], "fill price")
            for leg in unique_legs
        ),
        Decimal(0),
    )
    total_size = float(total_size_decimal)
    total_notional = float(total_notional_decimal)
    total_fees = sum(float(leg["fees"]) for leg in unique_legs)
    if total_size <= 0:
        raise RuntimeError("presence legs sum to non-positive size")
    # BUY FAK price improvement can return more outcome shares than the target
    # share count while preserving the approved capital bound (live CLOB truth:
    # original_size=173 @ 0.09, matched=189.77 @ 0.08). Shares are therefore not
    # the capital invariant. Exactly one order, its causal submit window, the BUY
    # limit, and total quote notional are. Multiple-order attribution and any
    # spend above the persisted limit-size budget still fail closed.
    material_share_overfill = total_size > order_size * 1.02 + 1e-6
    if material_share_overfill and not is_fak:
        raise RuntimeError(
            f"non-FAK presence legs sum {total_size} exceed order size {order_size}; "
            "refusing possible mis-attribution / double-count"
        )
    max_notional_decimal = command_size * command_price
    max_notional = float(max_notional_decimal)
    if total_notional_decimal > max_notional_decimal:
        raise RuntimeError(
            f"presence fill notional {total_notional} exceeds submitted bound {max_notional}; "
            "refusing possible mis-attribution / overspend"
        )
    avg_price = float(total_notional_decimal / total_size_decimal)
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
        "venue_command_id": str(command.get("command_id") or ""),
        "token_id": token_id,
        "condition_id": str(plan.get("condition_id") or ""),
        "direction": str(plan.get("direction") or ""),
        "funder_address": str(funder_address),
        "order_size": order_size,
        "limit_price": limit_price,
        "order_type": order_type,
        "venue_order_id": venue_order_id,
        "venue_command_state": venue_command_state,
        "filled_size": total_size,
        "filled_notional": total_notional,
        "max_submitted_notional": max_notional,
        "fill_bound_semantics": (
            "PRICE_IMPROVED_NOTIONAL_BOUNDED"
            if material_share_overfill
            else "SHARE_AND_NOTIONAL_BOUNDED"
        ),
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
        # Per-aggregate fault isolation: one aggregate whose presence proof
        # refuses (e.g. the double-count antibody, or no funder-owned fill) must
        # NOT abort the resolution of the OTHERS that DO prove a clean fill. Each
        # proof keeps its full rigor; a refusal only skips THAT aggregate, which
        # then falls through to the next resolver rung (resting/absorbed) or the
        # boot fail-closed raise. Pre-fix this was an all-or-nothing list
        # comprehension: a single refusal poisoned the whole batch and left a
        # genuinely-resolvable orphan stuck (boot crash-loop, 2026-06-16).
        proofs = []
        for agg in aggregates:
            try:
                proofs.append(
                    build_presence_proof(ro, agg, trades=trades, funder_address=funder_address)
                )
            except Exception as exc:  # noqa: BLE001 — refusal isolates to this aggregate
                log(f"PRESENCE_SKIP {agg[:80]}... ({exc})")
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
        return 1 if before != (0, 0) else 0
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
