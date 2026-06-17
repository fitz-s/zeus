# Created: 2026-06-10
# Last reused or audited: 2026-06-11
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K4.0
# (operator escalation: REST-THEN-CROSS) +
# docs/evidence/maker_taker/2026-06-10_taker_only_root_cause.md (KM deadline basis).
# 2026-06-11 audit (dependency_db_locked antibody): VERDICT CURRENT_REUSABLE. Already
# read-only on the DB and cancel-only on the venue; split into snapshot
# (find_expired_resting_entries) + pure-network cancel (run_cancels_for_expired_rests)
# so the read connection is closed before any venue cancel (close-before-network).
"""K4.0 maker-rest escalation: the DEADLINE owner for resting maker entries.

THE PLAN this module completes: the REST-THEN-CROSS policy
(src/strategy/live_inference/mode_consistent_ev.select_rest_then_cross_mode)
posts entries as post_only GTC maker rests by default. GTC orders have NO other
TTL owner (verified 2026-06-10: the legacy fill_tracker timeout only governs
portfolio pending_tracked positions; edli-lane GTC rests would sit forever).
This job is deliberately DUMB — it only cancels rests that have outlived the
measured escalation deadline. All intelligence lives elsewhere:

  - The NEXT reactor cycle re-decides the family through the FULL standard
    certification pipeline (the honest re-cert; no shortcut math here).
  - _family_rest_state (event_reactor_adapter) then sees the cancelled-unfilled
    >= deadline rest in venue truth and licenses the policy's
    TAKER_ESCALATED_AFTER_REST lane — cross only if the edge re-certifies.
  - If the edge decayed, no candidate is produced and the standard regret
    receipt records the decay (that datum measures rest-cost for free).

SCOPE GUARDS (relationship-tested):
  - ENTRY commands only — exits are never touched.
  - Only orders whose latest venue fact is an OPEN rest (LIVE / RESTING /
    PARTIALLY_MATCHED). A partial rest's REMAINDER is cancelled (standard);
    booked exposure is untouched (fill machinery owns it).
  - Only rests older than the registry deadline (maker_rest_escalation_deadline,
    basis=MEASURED, KM n=108).
  - Orders with no venue_order_id (stuck SUBMITTING etc.) are SKIPPED — the
    command-recovery sweep owns unresolved side-effect states, not this job.

Fail direction: fail-soft per order (a cancel error logs and continues; the
next tick retries). The job never submits anything.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("zeus.maker_rest_escalation")

UTC = timezone.utc

# Latest-fact states that mean "this order is resting open at the venue".
OPEN_REST_FACT_STATES = ("LIVE", "RESTING", "PARTIALLY_MATCHED")


def _deadline_minutes() -> float:
    from src.strategy.live_inference.mode_consistent_ev import (
        MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    )

    return float(MAKER_REST_ESCALATION_DEADLINE_MINUTES)


def find_expired_resting_entries(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    deadline_minutes: float | None = None,
) -> list[dict[str, Any]]:
    """ENTRY commands whose latest venue fact is an open rest older than the deadline."""
    deadline = float(deadline_minutes if deadline_minutes is not None else _deadline_minutes())
    cutoff = (now.astimezone(UTC) - timedelta(minutes=deadline)).isoformat()
    placeholders = ",".join("?" for _ in OPEN_REST_FACT_STATES)
    rows = conn.execute(
        f"""
        WITH latest_facts AS (
            SELECT venue_order_id, state, matched_size,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id ORDER BY local_sequence DESC
                   ) AS rn
            FROM venue_order_facts
        )
        SELECT vc.command_id, vc.venue_order_id, vc.token_id, vc.market_id,
               vc.created_at, lf.state AS fact_state, lf.matched_size
        FROM venue_commands vc
        JOIN latest_facts lf
          ON lf.venue_order_id = vc.venue_order_id AND lf.rn = 1
        WHERE vc.intent_kind = 'ENTRY'
          AND vc.venue_order_id IS NOT NULL
          AND vc.venue_order_id != ''
          AND lf.state IN ({placeholders})
          AND vc.created_at <= ?
        """,
        (*OPEN_REST_FACT_STATES, cutoff),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
        else:
            out.append(
                {
                    "command_id": row[0],
                    "venue_order_id": row[1],
                    "token_id": row[2],
                    "market_id": row[3],
                    "created_at": row[4],
                    "fact_state": row[5],
                    "matched_size": row[6],
                }
            )
    return out


def run_cancels_for_expired_rests(
    expired: list[dict[str, Any]],
    clob: Any,
    *,
    deadline_minutes: float | None = None,
    collect_cancelled: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Cancel each already-snapshotted expired resting maker entry. Returns counters.

    PURE NETWORK phase (dependency_db_locked clean shape, 2026-06-11): this half
    holds NO DB connection — it operates only on the ``expired`` list captured by
    ``find_expired_resting_entries`` on a now-closed connection. Splitting the
    snapshot from the cancels structurally guarantees no connection is open while
    the venue cancels run.

    ESCALATION RE-DECISION HARVEST (redecide-block fix 2026-06-16): when a caller
    passes ``collect_cancelled``, every entry whose cancel was CONFIRMED (and only
    those — a ``cancel_failed`` order is NOT appended) is appended to that list so
    the caller can emit a family-targeted re-decision opportunity_event for the
    just-cancelled, ARMED escalation family. The collection is the ONLY mutation
    added here; the connection-free network contract is preserved (no DB work in
    this phase — the world-DB event-write happens in the caller that owns DB
    access). Keeping ``stats`` byte-identical preserves the existing exact-equality
    callers/tests; the harvest rides a separate out-parameter.
    """
    stats = {"scanned": len(expired), "cancelled": 0, "cancel_failed": 0}
    for entry in expired:
        order_id = str(entry.get("venue_order_id") or "")
        try:
            clob.cancel_order(order_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft per order; next tick retries
            stats["cancel_failed"] += 1
            logger.error(
                "maker_rest_escalation: cancel failed command=%s order=%s: %r",
                entry.get("command_id"),
                order_id,
                exc,
            )
            continue
        stats["cancelled"] += 1
        if collect_cancelled is not None:
            # CONFIRMED-cancel only (this line is unreachable on the cancel_failed
            # path above — `continue` skips it): the re-decision lane must fire only
            # for families whose rest was actually pulled at the venue.
            collect_cancelled.append(entry)
        logger.info(
            "maker_rest_escalation: cancelled expired rest command=%s order=%s "
            "rested_since=%s fact_state=%s matched=%s (deadline=%.0fmin; the next "
            "certified decision for this family may cross as TAKER_ESCALATED_AFTER_REST)",
            entry.get("command_id"),
            order_id,
            entry.get("created_at"),
            entry.get("fact_state"),
            entry.get("matched_size"),
            float(deadline_minutes if deadline_minutes is not None else _deadline_minutes()),
        )
    return stats


def run_maker_rest_escalation_cycle(
    conn: sqlite3.Connection,
    clob: Any,
    *,
    now: datetime | None = None,
    deadline_minutes: float | None = None,
) -> dict[str, int]:
    """Cancel every expired resting maker entry. Returns counters.

    Composed from the two clean halves: ``find_expired_resting_entries`` (DB
    snapshot) then ``run_cancels_for_expired_rests`` (venue cancels, no conn). The
    live scheduled job calls the halves directly so the read connection is closed
    before any cancel; this combined entry point is retained for callers/tests
    that pass an already-open connection.
    """
    now = now or datetime.now(UTC)
    expired = find_expired_resting_entries(
        conn, now=now, deadline_minutes=deadline_minutes
    )
    return run_cancels_for_expired_rests(expired, clob, deadline_minutes=deadline_minutes)
