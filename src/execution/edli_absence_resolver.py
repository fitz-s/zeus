# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: boot crash-loop incident (3 occurrences 2026-06-12: 11:26Z
#   x4 aggregates, 12:19Z x1, each requiring a manual operator run of
#   scripts/resolve_edli_unknown_by_authenticated_absence.py before the daemon
#   could boot) + that script's operator-ratified absence-proof contract
#   (settings.json _unpause_note_2026_06_12).
"""Authenticated-absence resolution for EDLI post-submit unknowns.

Core extracted from scripts/resolve_edli_unknown_by_authenticated_absence.py
(the script is now a thin CLI wrapper) so the daemon BOOT path can run the
SAME resolution automatically instead of crash-looping behind launchd until
an operator runs the script by hand.

THE CONTRACT (unchanged from the script):
- Absence is NEVER inferred from local rows. The proof reads authenticated
  CLOB open orders + trades, requires complete reads, and REFUSES (raises)
  when any matching target-token exposure exists — a real venue order/fill
  can never be auto-released.
- Resolution appends Reconciled + CapTransitioned(RELEASED) through the
  canonical event-sourced ledgers under the world write lock. No raw writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

from src.data.polymarket_client import PolymarketClient
from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import RECONCILE_SOURCE, append_reconciled
from src.state.db import (
    get_world_connection,
    get_world_connection_read_only,
    world_write_lock,
)

logger = logging.getLogger(__name__)

RESOLUTION_REASON = "AUTHENTICATED_CLOB_ABSENCE_NO_OPEN_ORDER_OR_TRADE"
PRE_SUBMIT_ORPHAN_REASON = "PRE_SUBMIT_ORPHAN_RECOVERY_NO_VENUE_ATTEMPT"
_LEGACY_PRE_SUBMIT_ORPHAN_REASON_PREFIX = (
    "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:SubmitRejected requires preceding VenueSubmitAttempted"
)


def _json(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(str(row["payload_json"]))


def _latest_payload(conn: sqlite3.Connection, aggregate_id: str, event_type: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT payload_json
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = ?
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (aggregate_id, event_type),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{event_type} missing for {aggregate_id}")
    return _json(row)


def _pending_aggregates(conn: sqlite3.Connection, aggregate_id: str | None) -> list[str]:
    if aggregate_id:
        row = conn.execute(
            "SELECT aggregate_id FROM edli_live_order_projection WHERE aggregate_id = ? AND pending_reconcile = 1",
            (aggregate_id,),
        ).fetchone()
        return [aggregate_id] if row is not None else []
    rows = conn.execute(
        """
        SELECT aggregate_id
        FROM edli_live_order_projection
        WHERE pending_reconcile = 1
        ORDER BY updated_at ASC
        """
    ).fetchall()
    return [str(row["aggregate_id"]) for row in rows]


def _raw(item: object) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    raw = getattr(item, "raw", None)
    if isinstance(raw, dict):
        return dict(raw)
    return dict(getattr(item, "__dict__", {}))


def _mentions_token(raw: dict[str, Any], token_id: str) -> bool:
    return token_id in json.dumps(raw, sort_keys=True, default=str)


def _summarize(raw: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "order_id",
        "status",
        "state",
        "asset_id",
        "token_id",
        "condition_id",
        "side",
        "price",
        "size",
        "original_size",
        "matched_amount",
        "match_time",
        "last_update",
        "created_at",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) is not None}


def _read_authenticated_venue() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with PolymarketClient(public_http_timeout=15) as clob:
        adapter = clob._ensure_v2_adapter()
        open_orders = [_raw(item) for item in adapter.get_open_orders()]
        trades = [_raw(item) for item in adapter.get_trades()]
    return open_orders, trades


def build_absence_proof(
    conn: sqlite3.Connection,
    aggregate_id: str,
    *,
    open_orders: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    submit_unknown = _latest_payload(conn, aggregate_id, "SubmitUnknown")
    if submit_unknown.get("venue_call_started") is not True:
        raise RuntimeError("SubmitUnknown is not a post-submit unknown; use the pre-venue resolver")
    plan = _latest_payload(conn, aggregate_id, "SubmitPlanBuilt")
    attempted = _latest_payload(conn, aggregate_id, "VenueSubmitAttempted")
    token_id = str(plan.get("token_id") or "")
    if not token_id:
        raise RuntimeError("SubmitPlanBuilt missing token_id")
    matching_open = [raw for raw in open_orders if _mentions_token(raw, token_id)]
    matching_trades = [raw for raw in trades if _mentions_token(raw, token_id)]
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
        "limit_price": plan.get("limit_price"),
        "size": plan.get("size"),
        "idempotency_key_hash": hashlib.sha256(str(attempted.get("idempotency_key") or "").encode()).hexdigest(),
        "open_orders_checked": True,
        "trades_checked": True,
        "open_orders_query_complete": True,
        "trades_query_complete": True,
        "open_order_count": len(open_orders),
        "trade_count": len(trades),
        "matching_open_order_count": len(matching_open),
        "matching_trade_count": len(matching_trades),
        "matching_open_orders": [_summarize(raw) for raw in matching_open[:10]],
        "matching_trades": [_summarize(raw) for raw in matching_trades[:10]],
    }
    proof["proof_hash"] = hashlib.sha256(json.dumps(proof, sort_keys=True, default=str).encode()).hexdigest()
    if matching_open or matching_trades:
        raise RuntimeError("authenticated venue read found matching exposure; do not release cap")
    return proof


def _latest_receipt_hash(conn: sqlite3.Connection, aggregate_id: str) -> str:
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
        raise RuntimeError(f"NO execution_receipt_hash for {aggregate_id}")
    return str(_json(row)["execution_receipt_hash"])


def _cap_usage_id_for(conn: sqlite3.Connection, final_intent_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT usage_id
        FROM edli_live_cap_usage
        WHERE final_intent_id = ? AND reservation_status = 'RESERVED'
        """,
        (final_intent_id,),
    ).fetchone()
    return str(row["usage_id"]) if row is not None else None


def _readiness_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    unresolved = conn.execute(
        "SELECT COUNT(*) c FROM edli_live_order_projection WHERE pending_reconcile = 1"
    ).fetchone()["c"]
    reserved = conn.execute(
        "SELECT COUNT(*) c FROM edli_live_cap_usage WHERE reservation_status = 'RESERVED'"
    ).fetchone()["c"]
    return int(unresolved), int(reserved)


def _pre_submit_orphan_proofs(conn: sqlite3.Connection, aggregate_id: str | None) -> list[dict[str, Any]]:
    if aggregate_id:
        rows = conn.execute(
            _PRE_SUBMIT_ORPHAN_SQL_BY_AGGREGATE,
            (_LEGACY_PRE_SUBMIT_ORPHAN_REASON_PREFIX + "%", aggregate_id),
        ).fetchall()
    else:
        rows = conn.execute(
            _PRE_SUBMIT_ORPHAN_SQL_ALL,
            (_LEGACY_PRE_SUBMIT_ORPHAN_REASON_PREFIX + "%",),
        ).fetchall()
    proofs: list[dict[str, Any]] = []
    for row in rows:
        command = _latest_payload(conn, str(row["aggregate_id"]), "ExecutionCommandCreated")
        plan = _latest_payload(conn, str(row["aggregate_id"]), "SubmitPlanBuilt")
        proof = {
            "schema_version": 1,
            "source": "local_edli_event_ledger",
            "recovery_reason": PRE_SUBMIT_ORPHAN_REASON,
            "legacy_failure_reason": str(row["rejection_reason"]),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "aggregate_id": str(row["aggregate_id"]),
            "event_id": str(row["event_id"]),
            "final_intent_id": str(row["final_intent_id"]),
            "execution_command_id": str(row["execution_command_id"] or command.get("execution_command_id") or ""),
            "usage_id": str(row["usage_id"]),
            "token_id": str(plan.get("token_id") or ""),
            "condition_id": str(plan.get("condition_id") or ""),
            "direction": str(plan.get("direction") or ""),
            "reserved_notional_usd": row["reserved_notional_usd"],
            "cap_created_at": row["cap_created_at"],
            "venue_submit_attempted_event_exists": False,
            "terminal_event_exists": False,
        }
        proof["proof_hash"] = hashlib.sha256(json.dumps(proof, sort_keys=True, default=str).encode()).hexdigest()
        proofs.append(proof)
    return proofs


_PRE_SUBMIT_ORPHAN_SQL_BASE = """
SELECT
    proj.aggregate_id,
    proj.event_id,
    proj.final_intent_id,
    usage.usage_id,
    usage.execution_command_id,
    usage.reserved_notional_usd,
    usage.created_at AS cap_created_at,
    regret.rejection_reason
FROM edli_live_order_projection proj
JOIN edli_live_cap_usage usage
  ON usage.event_id = proj.event_id
 AND usage.final_intent_id = proj.final_intent_id
 AND usage.reservation_status = 'RESERVED'
JOIN no_trade_regret_events regret
  ON regret.event_id = proj.event_id
 AND regret.rejection_reason LIKE ?
WHERE proj.current_state = 'EXECUTION_COMMAND_CREATED'
  AND COALESCE(proj.pending_reconcile, 0) = 0
"""

_PRE_SUBMIT_ORPHAN_SQL_TAIL = """
  AND NOT EXISTS (
      SELECT 1 FROM edli_live_order_events attempted
      WHERE attempted.aggregate_id = proj.aggregate_id
        AND attempted.event_type = 'VenueSubmitAttempted'
  )
  AND NOT EXISTS (
      SELECT 1 FROM edli_live_order_events terminal
      WHERE terminal.aggregate_id = proj.aggregate_id
        AND terminal.event_type IN (
            'VenueSubmitAcknowledged', 'SubmitRejected', 'SubmitUnknown',
            'Reconciled', 'CapTransitioned'
        )
  )
ORDER BY usage.created_at ASC
"""

_PRE_SUBMIT_ORPHAN_SQL_ALL = _PRE_SUBMIT_ORPHAN_SQL_BASE + _PRE_SUBMIT_ORPHAN_SQL_TAIL
_PRE_SUBMIT_ORPHAN_SQL_BY_AGGREGATE = (
    _PRE_SUBMIT_ORPHAN_SQL_BASE
    + "  AND proj.aggregate_id = ?\n"
    + _PRE_SUBMIT_ORPHAN_SQL_TAIL
)


def resolve_pre_submit_orphans(
    *,
    aggregate_id: str | None,
    apply: bool,
    log: Callable[[str], None] = print,
) -> int:
    """Recover legacy pre-submit terminal rows that never reached venue.

    This is intentionally narrower than the authenticated absence resolver. It
    only clears aggregates that have local evidence of the legacy certificate
    build bug and no VenueSubmitAttempted event. Ambiguous command-created rows
    without that receipt remain fail-closed.
    """
    ro = get_world_connection_read_only()
    try:
        before = _readiness_counts(ro)
        proofs = _pre_submit_orphan_proofs(ro, aggregate_id)
        log(f"PRE_SUBMIT_ORPHAN_BEFORE: unresolved_submit={before[0]} reserved_cap={before[1]}")
        log(f"pre-submit orphan aggregates selected: {len(proofs)}")
        for proof in proofs:
            log(
                "PRE_SUBMIT_ORPHAN_PROOF "
                + json.dumps(
                    {
                        "aggregate_id": proof["aggregate_id"][:80] + "...",
                        "usage_id": proof["usage_id"],
                        "token_id": proof["token_id"],
                        "proof_hash": proof["proof_hash"],
                    },
                    sort_keys=True,
                )
            )
    finally:
        ro.close()
    if not proofs:
        return 1
    if not apply:
        log("DRY-RUN: re-run with --apply to append SubmitRejected + CapTransitioned(RELEASED).")
        return 1

    now = datetime.now(timezone.utc)
    conn = get_world_connection(write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        with world_write_lock(conn):
            ledger = LiveOrderAggregateLedger(conn)
            cap_ledger = LiveCapLedger(conn)
            for proof in proofs:
                receipt_hash = "pre_submit_orphan:" + str(proof["proof_hash"])
                ledger.append_event(
                    aggregate_id=str(proof["aggregate_id"]),
                    event_type="SubmitRejected",
                    payload={
                        "event_id": proof["event_id"],
                        "final_intent_id": proof["final_intent_id"],
                        "execution_command_id": proof["execution_command_id"],
                        "execution_receipt_hash": receipt_hash,
                        "reason_code": PRE_SUBMIT_ORPHAN_REASON,
                        "submit_status": "PRE_SUBMIT_ERROR",
                        "venue_call_started": False,
                        "venue_ack_received": False,
                        "pre_submit_rejection": True,
                        "pre_submit_orphan_recovery_proof": proof,
                    },
                    occurred_at=now,
                    source_authority="explicit_reconcile",
                )
                ledger.append_event(
                    aggregate_id=str(proof["aggregate_id"]),
                    event_type="CapTransitioned",
                    payload={
                        "event_id": proof["event_id"],
                        "final_intent_id": proof["final_intent_id"],
                        "execution_command_id": proof["execution_command_id"],
                        "execution_receipt_hash": receipt_hash,
                        "to_status": "RELEASED",
                        "projection_status": "RELEASED",
                        "transition_reason": PRE_SUBMIT_ORPHAN_REASON,
                        "reconcile_proof_hash": proof["proof_hash"],
                    },
                    occurred_at=now,
                    source_authority="explicit_reconcile",
                )
                cap_ledger.release(str(proof["usage_id"]), PRE_SUBMIT_ORPHAN_REASON)
                log(
                    f"PRE_SUBMIT_ORPHAN_RESOLVED {str(proof['aggregate_id'])[:80]}... "
                    f"cap_usage={proof['usage_id']} proof_hash={proof['proof_hash']}"
                )
    finally:
        conn.close()

    ro = get_world_connection_read_only()
    try:
        after = _readiness_counts(ro)
    finally:
        ro.close()
    log(f"PRE_SUBMIT_ORPHAN_AFTER: unresolved_submit={after[0]} reserved_cap={after[1]}")
    return 0 if after == (0, 0) else 1


def resolve(*, aggregate_id: str | None, apply: bool, log: Callable[[str], None] = print) -> int:
    """Resolve stuck post-submit unknowns by authenticated venue absence proof.

    Returns 0 when nothing remains stuck afterwards, 1 otherwise. ``log``
    receives one line per step (the CLI passes print; the boot path passes
    logger.warning so the auto-resolution is loud in the daemon log).
    """
    open_orders, trades = _read_authenticated_venue()
    ro = get_world_connection_read_only()
    try:
        before = _readiness_counts(ro)
        aggregates = _pending_aggregates(ro, aggregate_id)
        log(f"BEFORE: unresolved_submit={before[0]} reserved_cap={before[1]}")
        log(f"pending aggregates selected: {len(aggregates)}")
        proofs = [build_absence_proof(ro, agg, open_orders=open_orders, trades=trades) for agg in aggregates]
        for proof in proofs:
            log(
                "ABSENCE_PROOF "
                + json.dumps(
                    {
                        "aggregate_id": proof["aggregate_id"][:80] + "...",
                        "token_id": proof["token_id"],
                        "open_order_count": proof["open_order_count"],
                        "trade_count": proof["trade_count"],
                        "matching_open_order_count": proof["matching_open_order_count"],
                        "matching_trade_count": proof["matching_trade_count"],
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
        log("DRY-RUN: re-run with --apply to append Reconciled + CapTransitioned(RELEASED).")
        return 0

    now = datetime.now(timezone.utc)
    conn = get_world_connection(write_class="live")
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
                        "venue_trade_exists": False,
                        "cap_transition_recommendation": "RELEASED",
                        "reconcile_reason": RESOLUTION_REASON,
                        "authenticated_absence_proof": proof,
                    },
                )
                ledger.append_event(
                    aggregate_id=agg,
                    event_type="CapTransitioned",
                    payload={
                        "event_id": event_id,
                        "final_intent_id": final_intent_id,
                        "execution_command_id": execution_command_id,
                        "execution_receipt_hash": receipt_hash,
                        "to_status": "RELEASED",
                        "projection_status": "RELEASED",
                        "transition_reason": RESOLUTION_REASON,
                        "reconcile_proof_hash": proof["proof_hash"],
                    },
                    occurred_at=now,
                    source_authority="explicit_reconcile",
                )
                usage = _cap_usage_id_for(conn, final_intent_id)
                if usage is not None:
                    cap_ledger.release(usage, RESOLUTION_REASON)
                log(f"RESOLVED {agg[:80]}... cap_usage={usage} proof_hash={proof['proof_hash']}")
    finally:
        conn.close()

    ro = get_world_connection_read_only()
    try:
        after = _readiness_counts(ro)
    finally:
        ro.close()
    log(f"AFTER: unresolved_submit={after[0]} reserved_cap={after[1]}")
    return 0 if after == (0, 0) else 1


# Readiness reasons that this resolver can clear at boot. Anything else (sha
# mismatch, risk reasons, artifact staleness) is out of scope by design.
BOOT_AUTO_RESOLVABLE_REASON_PREFIXES = (
    "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN",
    "EDLI_STAGE_LIVE_CAP_RESERVED",
)


def boot_auto_resolve_stuck_unknowns(blocking_reasons: list[str]) -> bool:
    """One bounded auto-resolution attempt at daemon boot.

    Fires ONLY when every blocking reason is in the stuck-aggregate class.
    Returns True when a resolution pass ran and cleared everything; False
    means the caller must raise exactly as before (fail-closed preserved —
    including when the absence proof refuses because real venue exposure
    exists, or the venue read fails).
    """
    if not blocking_reasons:
        return False
    if not all(r.startswith(BOOT_AUTO_RESOLVABLE_REASON_PREFIXES) for r in blocking_reasons):
        return False
    logger.warning(
        "EDLI boot readiness blocked by stuck post-submit unknowns (%s); "
        "attempting authenticated auto-resolution (absence-then-presence ladder, "
        "the manual resolver's contract, now boot-automatic)",
        ",".join(blocking_reasons),
    )
    # A stuck post-submit unknown is EITHER a true ABSENCE (the order never
    # landed -> release the cap) OR a true PRESENCE/fill (the #122 db-lock orphan:
    # the order filled but its venue_order_id was never recorded, so it was never
    # reconciled -> reconcile to FILL_CONFIRMED + CONSUME the cap). Try absence
    # first; if it refuses because real venue exposure exists, try presence. Only
    # if BOTH refuse do we fail-closed (genuinely ambiguous -> operator). Absence
    # writes nothing unless EVERY pending aggregate proves absent (its proofs are
    # built before any write), so falling through to presence is state-clean.
    try:
        rc0 = resolve_pre_submit_orphans(aggregate_id=None, apply=True, log=logger.warning)
        if rc0 == 0:
            return True
        logger.warning("boot pre-submit orphan resolution did not fully clear (rc=%s); trying absence", rc0)
    except Exception as exc:  # noqa: BLE001 — bounded local recovery failed -> try venue-truth resolvers
        logger.warning(
            "boot pre-submit orphan resolution refused (%s); attempting absence",
            exc,
        )
    try:
        rc = resolve(aggregate_id=None, apply=True, log=logger.warning)
        if rc == 0:
            return True
        logger.warning("boot absence resolution did not fully clear (rc=%s); trying presence", rc)
    except Exception as exc:  # noqa: BLE001 — absence refusal (e.g. matching exposure) -> try presence
        logger.warning(
            "boot absence resolution refused (%s); attempting presence "
            "(authenticated CONFIRMED-fill) resolution",
            exc,
        )
    try:
        from src.execution.edli_presence_resolver import resolve_presence

        rc2 = resolve_presence(aggregate_id=None, apply=True, log=logger.warning)
        if rc2 == 0:
            return True
        logger.warning("boot presence resolution did not fully clear (rc=%s); trying resting/absorbed", rc2)
    except Exception as exc:  # noqa: BLE001 — presence refused -> try resting/absorbed
        logger.warning(
            "boot presence resolution refused (%s); attempting resting/absorbed "
            "(live-resting open order OR already-absorbed fill) resolution",
            exc,
        )
    # Third rung: the two cases neither absence nor presence can clear —
    # (A) a SUBMITTED-AND-LIVE-RESTING order (funder-owned open order, no fill ->
    #     cap CONSUMED, order left live) and (B) a CONFIRMED fill that filled more
    #     than ordered AND is ALREADY a non-EDLI-keyed position_current row
    #     (presence's double-count antibody correctly refuses; re-materialising
    #     would duplicate the position) -> ledger-only reconcile + cap CONSUMED.
    # Both funder-checked and venue-truth-grounded; anything else is left for the
    # fail-closed raise below.
    try:
        from src.execution.edli_resting_absorbed_resolver import (
            resolve_resting_or_absorbed,
        )

        rc3 = resolve_resting_or_absorbed(aggregate_id=None, apply=True, log=logger.warning)
        return rc3 == 0
    except Exception as exc:  # noqa: BLE001 — none of the three -> fail-closed
        logger.error("boot auto-resolution refused/failed (boot will fail closed): %s", exc)
        return False
