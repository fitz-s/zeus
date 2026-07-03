# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: W4.2 (SCH-W1.2-ORDER-STATE cross-reference, docs/rebuild/schema_packets/
#   w1_2_order_state_extension_schema_packet_2026-07-02.md:197-198) — relocated verbatim out of
#   src/execution/maker_rest_escalation.py as part of that module's TTL-ownership handover to
#   src.state.order_state_predicates.rest_deadline_exceeded + src.execution.staleness_cancel.
"""Durable command-journal cancel executor, shared by every "cancel a snapshotted list of
already-open orders" caller.

This is GENERIC infrastructure, not staleness/TTL-specific: it turns a list of already-classified
cancel candidates (``{command_id, venue_order_id, cancel_reason, cancel_action, cancel_detail,
...}``) into durable ``CANCEL_REQUESTED``/``CANCEL_ACKED``/``CANCEL_REPLACE_BLOCKED`` command-journal
events around the venue cancel call. It originated inside ``maker_rest_escalation.py`` (the K4.0
GTC-deadline job) but other callers already depended on it before that module's deletion:
``main._edli_boot_invalid_pending_entry_authority_cancel_once`` (boot-time authority cancel),
``main._edli_continuous_redecision_screen_cycle`` (§4.5 rest-pull cancel), and (as of W4.2)
``main._c3_staleness_cancel_cycle``'s own carried-over invalid-entry-authority lane. None of those
are staleness/TTL classification — they each build their own ``expired``-shaped entry list and hand
it to this executor. Relocating this function here (byte-identical body) lets
``maker_rest_escalation.py`` be deleted without breaking any of them.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable

from src.contracts.canonical_lifecycle import is_cancel_confirmed_status

logger = logging.getLogger("zeus.venue_cancel_journal")

UTC = timezone.utc

TERMINAL_COMMAND_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "REJECTED", "SUBMIT_REJECTED"}
)


class _TerminalCommandNoop(RuntimeError):
    def __init__(self, command_id: str, state: str, event_type: str) -> None:
        super().__init__(
            f"terminal command {command_id} already {state}; skipping {event_type}"
        )
        self.command_id = command_id
        self.state = state
        self.event_type = event_type


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
                "venue_cancel_journal: retrying %s journal command=%s order=%s "
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
    """Cancel each already-snapshotted candidate with durable command-journal truth
    around the venue side effect.

    ``deadline_minutes`` is accepted only for the log line's benefit (callers whose
    candidates were built by a deadline classifier pass it through); this function
    itself does no deadline reasoning — it is a pure "cancel what you were told to
    cancel, durably" executor.

    1. append CANCEL_REQUESTED and commit,
    2. close the connection before the HTTP cancel,
    3. append CANCEL_ACKED / CANCEL_REPLACE_BLOCKED and commit.

    A command whose pre-side-effect journal write fails is not sent to the venue.
    A successful venue cancel whose post-side-effect journal write fails is not
    harvested for redecision; command recovery must resolve the CANCEL_PENDING row.
    ``NOT_CANCELED`` is cancel-unknown, not terminal failure: the venue may still
    have a live order, or the cancel may have raced with a match/cancel. M5
    recovery owns the next proof read.
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
                "venue_cancel_journal: skipped terminal command before cancel "
                "command=%s order=%s state=%s",
                command_id,
                order_id,
                exc.state,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            stats["cancel_failed"] += 1
            logger.error(
                "venue_cancel_journal: pre-cancel journal failed command=%s order=%s: %r",
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

        if outcome is not None and is_cancel_confirmed_status(outcome.status):
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
                "venue_cancel_journal: skipped post-cancel journal for terminal command "
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
                "venue_cancel_journal: post-cancel journal failed command=%s order=%s "
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
                "venue_cancel_journal: cancelled screened rest command=%s order=%s "
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
                "venue_cancel_journal: cancelled expired rest command=%s order=%s "
                "rested_since=%s fact_state=%s matched=%s (deadline=%.0fmin)",
                command_id,
                order_id,
                entry.get("created_at"),
                entry.get("fact_state"),
                entry.get("matched_size"),
                float(deadline_minutes) if deadline_minutes is not None else 0.0,
            )
    return stats


def _reconcile_terminal_no_fill_after_cancel_ack(
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    command_id: str,
    order_id: str,
    close_connections: bool,
) -> None:
    """Immediately consume zero-fill cancel truth after a confirmed cancel.

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
                "venue_cancel_journal: terminal no-fill reducers advanced command=%s "
                "order=%s cancel_ack=%s terminal=%s",
                command_id,
                order_id,
                cancel_summary,
                terminal_summary,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "venue_cancel_journal: terminal no-fill reducer deferred command=%s "
            "order=%s: %r",
            command_id,
            order_id,
            exc,
        )
    finally:
        _close_conn_if_needed(conn, close=close_connections)
