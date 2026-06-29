# Created: 2026-06-10
# Last reused or audited: 2026-06-11
# Authority basis: docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md K4.0
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
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.state.canonical_projections import OPEN_ORDER_FACT_STATES

logger = logging.getLogger("zeus.maker_rest_escalation")

UTC = timezone.utc

# Latest-fact states that mean "this order is resting open at the venue" — the
# single canonical open-order-fact set (was a local {LIVE,RESTING,PARTIALLY_MATCHED}).
OPEN_REST_FACT_STATES = tuple(sorted(OPEN_ORDER_FACT_STATES))
TERMINAL_COMMAND_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "REJECTED", "SUBMIT_REJECTED"}
)
DEADLINE_CANCEL_REASON = "MAKER_REST_DEADLINE_EXPIRED"
DEADLINE_CANCEL_ACTION = "CANCEL_REPLACE"


class _TerminalCommandNoop(RuntimeError):
    def __init__(self, command_id: str, state: str, event_type: str) -> None:
        super().__init__(
            f"terminal command {command_id} already {state}; skipping {event_type}"
        )
        self.command_id = command_id
        self.state = state
        self.event_type = event_type


def _deadline_minutes() -> float:
    from src.strategy.live_inference.mode_consistent_ev import (
        MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    )

    return float(MAKER_REST_ESCALATION_DEADLINE_MINUTES)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _deadline_cancel_detail(
    entry: dict[str, Any],
    *,
    now: datetime,
    deadline_minutes: float,
) -> dict[str, Any]:
    created_at = _parse_utc(entry.get("created_at"))
    now_utc = now.astimezone(UTC)
    rest_age_seconds = (
        max(0.0, (now_utc - created_at).total_seconds())
        if created_at is not None
        else None
    )
    return {
        "trigger": "maker_rest_deadline",
        "deadline_minutes": float(deadline_minutes),
        "rest_age_seconds": rest_age_seconds,
        "fact_state": str(entry.get("fact_state") or ""),
        "matched_size": entry.get("matched_size"),
    }


def _with_default_deadline_cancel_metadata(
    entry: dict[str, Any],
    *,
    now: datetime,
    deadline_minutes: float,
) -> dict[str, Any]:
    enriched = dict(entry)
    enriched.setdefault("cancel_reason", DEADLINE_CANCEL_REASON)
    enriched.setdefault("cancel_action", DEADLINE_CANCEL_ACTION)
    enriched.setdefault(
        "cancel_detail",
        _deadline_cancel_detail(enriched, now=now, deadline_minutes=deadline_minutes),
    )
    return enriched


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
          AND vc.state IN ('ACKED', 'POST_ACKED', 'PARTIAL')
          AND lf.state IN ({placeholders})
          AND vc.created_at <= ?
        """,
        (*OPEN_REST_FACT_STATES, cutoff),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append(
                _with_default_deadline_cancel_metadata(
                    dict(row),
                    now=now,
                    deadline_minutes=deadline,
                )
            )
        else:
            out.append(
                _with_default_deadline_cancel_metadata(
                    {
                        "command_id": row[0],
                        "venue_order_id": row[1],
                        "token_id": row[2],
                        "market_id": row[3],
                        "created_at": row[4],
                        "fact_state": row[5],
                        "matched_size": row[6],
                    },
                    now=now,
                    deadline_minutes=deadline,
                )
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
        cancel_reason = str(entry.get("cancel_reason") or "").strip()
        if cancel_reason:
            logger.info(
                "maker_rest_escalation: cancelled screened rest command=%s order=%s "
                "reason=%s action=%s detail=%s rested_since=%s fact_state=%s matched=%s",
                entry.get("command_id"),
                order_id,
                cancel_reason,
                entry.get("cancel_action"),
                entry.get("cancel_detail"),
                entry.get("created_at"),
                entry.get("fact_state"),
                entry.get("matched_size"),
            )
        else:
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


def _close_conn_if_needed(conn: sqlite3.Connection, *, close: bool) -> None:
    if not close:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text or "busy" in text


def _cancel_journal_event_already_persisted(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    event_type: str,
    venue_order_id: str,
) -> bool:
    try:
        rows = conn.execute(
            """
            SELECT payload_json
              FROM venue_command_events
             WHERE command_id = ?
               AND event_type = ?
             ORDER BY sequence_no DESC
            """,
            (command_id, event_type),
        ).fetchall()
    except Exception:
        return False
    import json

    for row in rows:
        try:
            raw = row["payload_json"]
        except Exception:
            raw = row[0]
        try:
            payload = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and str(payload.get("venue_order_id") or "") == venue_order_id:
            return True
    return False


def _append_cancel_journal_event(
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    command_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    close_connections: bool,
) -> None:
    from src.state.venue_command_repo import append_event

    venue_order_id = str(payload.get("venue_order_id") or "")
    for attempt in range(1, 4):
        conn = conn_factory()
        try:
            row = conn.execute(
                "SELECT state FROM venue_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if row is not None:
                current_state = str(row["state"] if isinstance(row, sqlite3.Row) else row[0]).upper()
                if current_state in TERMINAL_COMMAND_STATES:
                    raise _TerminalCommandNoop(command_id, current_state, event_type)
            if venue_order_id and _cancel_journal_event_already_persisted(
                conn,
                command_id=command_id,
                event_type=event_type,
                venue_order_id=venue_order_id,
            ):
                return
            append_event(
                conn,
                command_id=command_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            )
            conn.commit()
            return
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            if isinstance(exc, _TerminalCommandNoop):
                raise
            if not _is_sqlite_lock_error(exc) or attempt == 3:
                raise
            logger.warning(
                "maker_rest_escalation: retrying %s journal command=%s order=%s "
                "after sqlite lock (attempt %d/3): %s",
                event_type,
                command_id,
                venue_order_id,
                attempt,
                exc,
            )
            time.sleep(0.25 * attempt)
        finally:
            _close_conn_if_needed(conn, close=close_connections)


def run_persisted_cancels_for_expired_rests(
    expired: list[dict[str, Any]],
    clob: Any,
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    close_connections: bool = True,
    deadline_minutes: float | None = None,
    collect_cancelled: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Cancel rests with durable command-journal truth around the venue side effect.

    The legacy pure-network helper intentionally held no DB connection, but live
    callers that use it directly create a false local ACK ghost after a successful
    venue cancel. This wrapper preserves close-before-network while restoring the
    command contract:

    1. append CANCEL_REQUESTED and commit,
    2. close the connection before the HTTP cancel,
    3. append CANCEL_ACKED / CANCEL_REPLACE_BLOCKED and commit.

    A command whose pre-side-effect journal write fails is not sent to the venue.
    A successful venue cancel whose post-side-effect journal write fails is not
    harvested for redecision; command recovery must resolve the CANCEL_PENDING row.
    ``NOT_CANCELED`` is cancel-unknown for ENTRY maker rests, not terminal
    failure: the venue may still have a live order, or the cancel may have raced
    with a match/cancel. M5 recovery owns the next proof read.
    """
    from src.execution.exit_safety import parse_cancel_response

    stats = {
        "scanned": len(expired),
        "cancelled": 0,
        "cancel_failed": 0,
        "cancel_journal_failed": 0,
    }
    for entry in expired:
        command_id = str(entry.get("command_id") or "")
        order_id = str(entry.get("venue_order_id") or "")
        cancel_reason = str(entry.get("cancel_reason") or "").strip()
        cancel_action = str(entry.get("cancel_action") or "").strip()
        cancel_detail = entry.get("cancel_detail")
        now = datetime.now(UTC).isoformat()
        try:
            _append_cancel_journal_event(
                conn_factory,
                command_id=command_id,
                event_type="CANCEL_REQUESTED",
                occurred_at=now,
                payload={
                    "venue_order_id": order_id,
                    "source": "maker_rest_escalation",
                    "cancel_reason": cancel_reason,
                    "cancel_action": cancel_action,
                    "cancel_detail": cancel_detail,
                },
                close_connections=close_connections,
            )
        except _TerminalCommandNoop as exc:
            logger.info(
                "maker_rest_escalation: skipped terminal command before cancel "
                "command=%s order=%s state=%s",
                command_id,
                order_id,
                exc.state,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            stats["cancel_failed"] += 1
            logger.error(
                "maker_rest_escalation: pre-cancel journal failed command=%s order=%s: %r",
                command_id,
                order_id,
                exc,
            )
            continue

        try:
            raw = clob.cancel_order(order_id)
            outcome = parse_cancel_response(raw)
        except Exception as exc:  # noqa: BLE001 — possible side effect; record unknown
            outcome = None
            raw = {"exception_type": type(exc).__name__, "exception_message": str(exc)}

        if outcome is not None and outcome.status == "CANCELED":
            event_type = "CANCEL_ACKED"
            payload = {
                "venue_order_id": order_id,
                "cancel_outcome": outcome.raw_response,
                "source": "maker_rest_escalation",
                "cancel_reason": cancel_reason,
                "cancel_action": cancel_action,
                "cancel_detail": cancel_detail,
            }
        elif outcome is not None and outcome.status == "NOT_CANCELED":
            event_type = "CANCEL_REPLACE_BLOCKED"
            payload = {
                "venue_order_id": order_id,
                "reason": "post_cancel_unknown_possible_side_effect",
                "requires_m5_reconcile": True,
                "semantic_cancel_status": "CANCEL_UNKNOWN",
                "cancel_outcome": outcome.raw_response,
                "source": "maker_rest_escalation",
                "cancel_reason": cancel_reason,
                "cancel_action": cancel_action,
                "cancel_detail": cancel_detail,
            }
        else:
            event_type = "CANCEL_REPLACE_BLOCKED"
            payload = {
                "venue_order_id": order_id,
                "reason": "post_cancel_unknown_possible_side_effect",
                "requires_m5_reconcile": True,
                "semantic_cancel_status": "CANCEL_UNKNOWN",
                "cancel_outcome": raw,
                "source": "maker_rest_escalation",
                "cancel_reason": cancel_reason,
                "cancel_action": cancel_action,
                "cancel_detail": cancel_detail,
            }

        try:
            _append_cancel_journal_event(
                conn_factory,
                command_id=command_id,
                event_type=event_type,
                occurred_at=datetime.now(UTC).isoformat(),
                payload=payload,
                close_connections=close_connections,
            )
        except _TerminalCommandNoop as exc:
            logger.info(
                "maker_rest_escalation: skipped post-cancel journal for terminal command "
                "command=%s order=%s event=%s state=%s",
                command_id,
                order_id,
                event_type,
                exc.state,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            stats["cancel_journal_failed"] += 1
            logger.error(
                "maker_rest_escalation: post-cancel journal failed command=%s order=%s "
                "event=%s: %r",
                command_id,
                order_id,
                event_type,
                exc,
            )
            continue

        if event_type == "CANCEL_ACKED":
            stats["cancelled"] += 1
            _reconcile_terminal_no_fill_after_cancel_ack(
                conn_factory,
                command_id=command_id,
                order_id=order_id,
                close_connections=close_connections,
            )
            if collect_cancelled is not None:
                collect_cancelled.append(entry)
        else:
            stats["cancel_failed"] += 1

        if event_type == "CANCEL_ACKED" and cancel_reason:
            logger.info(
                "maker_rest_escalation: cancelled screened rest command=%s order=%s "
                "reason=%s action=%s detail=%s rested_since=%s fact_state=%s matched=%s",
                command_id,
                order_id,
                cancel_reason,
                entry.get("cancel_action"),
                entry.get("cancel_detail"),
                entry.get("created_at"),
                entry.get("fact_state"),
                entry.get("matched_size"),
            )
        elif event_type == "CANCEL_ACKED":
            logger.info(
                "maker_rest_escalation: cancelled expired rest command=%s order=%s "
                "rested_since=%s fact_state=%s matched=%s (deadline=%.0fmin; the next "
                "certified decision for this family may cross as TAKER_ESCALATED_AFTER_REST)",
                command_id,
                order_id,
                entry.get("created_at"),
                entry.get("fact_state"),
                entry.get("matched_size"),
                float(deadline_minutes if deadline_minutes is not None else _deadline_minutes()),
            )
    return stats


def _reconcile_terminal_no_fill_after_cancel_ack(
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    command_id: str,
    order_id: str,
    close_connections: bool,
) -> None:
    """Immediately consume zero-fill cancel truth after a maker-rest pull.

    The full INV-31 command-recovery sweep can be delayed by authenticated venue
    reads. A confirmed cancel already has enough durable local evidence for the
    DB-only terminal-no-fill reducers to clear a zero-exposure ``pending_entry``
    projection, so run those narrow reducers in the cancel path.
    """

    conn = conn_factory()
    try:
        required_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {
            "position_current",
            "venue_commands",
            "venue_command_events",
            "venue_order_facts",
        }.issubset(required_tables):
            return
        from src.execution.command_recovery import (
            reconcile_cancel_ack_terminal_no_fill_facts,
            reconcile_terminal_order_facts,
        )

        cancel_summary = reconcile_cancel_ack_terminal_no_fill_facts(conn)
        terminal_summary = reconcile_terminal_order_facts(conn)
        try:
            conn.commit()
        except Exception:
            pass
        advanced = int(cancel_summary.get("advanced", 0) or 0) + int(
            terminal_summary.get("advanced", 0) or 0
        )
        if advanced:
            logger.info(
                "maker_rest_escalation: terminal no-fill reducers advanced command=%s "
                "order=%s cancel_ack=%s terminal=%s",
                command_id,
                order_id,
                cancel_summary,
                terminal_summary,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "maker_rest_escalation: terminal no-fill reducer deferred command=%s "
            "order=%s: %r",
            command_id,
            order_id,
            exc,
        )
    finally:
        _close_conn_if_needed(conn, close=close_connections)


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
