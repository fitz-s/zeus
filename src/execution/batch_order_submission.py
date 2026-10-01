# Created: 2026-07-02
# Authority basis: architecture/invariants.yaml INV-28.
"""Current journaled batch-cancel orchestration."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from src.venue.batch_submit import MAX_ORDERS_PER_BATCH, chunk_orders


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _cancel_journal_transaction(conn: sqlite3.Connection, *, owner: str):
    """Own only this batch's TRADE writes; never commit a caller's transaction."""
    if conn.in_transaction:
        raise RuntimeError("batch_cancel_existing_transaction")

    from src.state.db import _zeus_trade_db_path
    from src.state.write_coordinator import (
        DBIdentity,
        WritePriority,
        bounded_sqlite_write,
        default_runtime_write_coordinator,
    )

    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    canonical = bool(main) and Path(main).resolve() == _zeus_trade_db_path().resolve()
    lease_context = (
        default_runtime_write_coordinator().lease(
            (DBIdentity.TRADE,),
            owner=owner,
            write_class="live",
            priority=WritePriority.MONITOR,
            deadline_ms=1_500,
            max_hold_ms=500,
        )
        if canonical else nullcontext(None)
    )
    with lease_context as lease:
        with (bounded_sqlite_write(conn, lease, max_hold_ms=500) if lease else nullcontext()):
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise


def _journal_error(exc: Exception) -> str:
    # Never expose SDK response bodies, order IDs, keys, or SQL parameters in logs.
    if isinstance(exc, sqlite3.Error):
        return str(getattr(exc, "sqlite_errorname", None) or type(exc).__name__)
    return type(exc).__name__


@dataclass(frozen=True)
class BatchCancelOutcome:
    request_index: int
    command_id: str
    status: str  # "acked" | "not_canceled" | "unknown" | "not_requestable" | "not_attempted"
    venue_order_id: Optional[str] = None
    error_message: Optional[str] = None


def cancel_commands_batch(
    conn: sqlite3.Connection,
    client: Any,
    command_ids: Sequence[str],
    *,
    rate_budget: Any = None,
    cancel_reasons: Mapping[str, str] | None = None,
) -> list[BatchCancelOutcome]:
    """Cancel ``command_ids`` in chunks of at most MAX_ORDERS_PER_BATCH.

    ``cancel_reasons`` (command_id -> reason) is journaled as the
    CANCEL_REQUESTED payload's ``cancel_reason``, in the same transaction that
    persists the request, so the reason is durable before any venue call.

    Reuses the existing single-order cancel machinery
    (src.execution.exit_safety: parse_cancel_response,
    _CANCEL_REQUESTABLE_STATES, _append_cancel_unknown) rather than
    inventing new journaling -- only the "N CANCEL_REQUESTED events, ONE
    cancel_orders HTTP call" batching shape is new here. ``client`` is the
    gateway (cancel_orders_batch), which already enforces
    cutover_guard.gate_for_intent(CANCEL) -- this module does not
    duplicate that check.

    A command that is not cancel-requestable (unknown, wrong state, no
    venue_order_id) is skipped with status "not_requestable" WITHOUT
    blocking the rest of its chunk. Only an ambiguous outcome (the SDK
    call itself raising, a CutoverPending block, or rate-budget denial)
    halts later chunks (the module docstring's shared halt-on-ambiguous
    design).
    """
    from src.contracts.canonical_lifecycle import is_cancel_confirmed_status
    from src.control.cutover_guard import CutoverPending
    from src.execution.exit_safety import (
        _CANCEL_REQUESTABLE_STATES,
        CancelOutcome,
        _append_cancel_unknown,
        parse_cancel_response,
    )
    from src.state.venue_command_repo import append_event, get_command
    from src.venue.batch_submit import CANCEL_ECHO_CANDIDATE_FIELDS, map_batch_items
    from src.venue.rate_budget import RequestClass

    outcomes: list[Optional[BatchCancelOutcome]] = [None] * len(command_ids)
    should_continue = True

    for chunk in chunk_orders(list(enumerate(command_ids)), MAX_ORDERS_PER_BATCH):
        if not should_continue:
            for idx, command_id in chunk:
                outcomes[idx] = BatchCancelOutcome(idx, command_id, "not_attempted")
            continue

        eligible: list[tuple[int, str, str, str]] = []
        for idx, command_id in chunk:
            cmd = get_command(conn, command_id)
            if cmd is None:
                outcomes[idx] = BatchCancelOutcome(idx, command_id, "not_requestable", error_message="unknown_command_id")
                continue
            state = str(cmd.get("state") or "").upper()
            venue_order_id = str(cmd.get("venue_order_id") or "")
            if state not in _CANCEL_REQUESTABLE_STATES and state != "CANCEL_PENDING":
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "not_requestable",
                    error_message=f"state_not_cancel_requestable:{state}",
                )
                continue
            if not venue_order_id:
                outcomes[idx] = BatchCancelOutcome(idx, command_id, "not_requestable", error_message="missing_venue_order_id")
                continue
            eligible.append((idx, command_id, venue_order_id, state))

        if not eligible:
            continue

        if rate_budget is not None:
            budget_result = rate_budget.try_acquire(RequestClass.CANCEL)
            if not budget_result.granted:
                for idx, command_id, *_rest in eligible:
                    outcomes[idx] = BatchCancelOutcome(
                        idx, command_id, "not_attempted",
                        error_message=f"rate_budget_{budget_result.decision.value}",
                    )
                should_continue = False
                continue

        # --- persist phase: CANCEL_REQUESTED per eligible command, ONE
        # transaction, committed BEFORE the chunk's SDK call. ------------
        now = _now()
        persisted: list[tuple[int, str, str]] = []
        try:
            with _cancel_journal_transaction(conn, owner="batch_cancel_request"):
                for idx, command_id, venue_order_id, state in eligible:
                    if state != "CANCEL_PENDING":
                        payload: dict[str, Any] = {
                            "venue_order_id": venue_order_id,
                            "batch": True,
                        }
                        reason = (cancel_reasons or {}).get(command_id)
                        if reason:
                            payload["cancel_reason"] = str(reason)
                        append_event(
                            conn,
                            command_id=command_id,
                            event_type="CANCEL_REQUESTED",
                            occurred_at=now,
                            payload=payload,
                        )
                    persisted.append((idx, command_id, venue_order_id))
        except Exception as exc:
            for idx, command_id, *_rest in eligible:
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "not_attempted",
                    error_message=f"batch_cancel_persist_failed:{_journal_error(exc)}",
                )
            should_continue = False
            continue

        # --- SDK call: ONE call for the whole chunk. --------------------
        try:
            legacy_results = client.cancel_orders_batch([venue_order_id for *_r, venue_order_id in persisted])
        except CutoverPending as exc:
            for idx, command_id, venue_order_id in persisted:
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "not_attempted",
                    venue_order_id=venue_order_id, error_message=f"cutover_pending: {exc}",
                )
            should_continue = False
            continue
        except Exception as exc:
            try:
                with _cancel_journal_transaction(conn, owner="batch_cancel_unknown"):
                    for _idx, command_id, _venue_order_id in persisted:
                        _append_cancel_unknown(
                            conn, command_id,
                            CancelOutcome("UNKNOWN", "post_cancel_exception_possible_side_effect", {
                                "exception_type": type(exc).__name__,
                            }), _now(),
                        )
            except Exception as persist_exc:
                error = f"batch_cancel_unknown_persist_failed:{_journal_error(persist_exc)}"
            else:
                error = "post_cancel_exception_possible_side_effect"
            for idx, command_id, venue_order_id in persisted:
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "unknown", venue_order_id=venue_order_id,
                    error_message=error,
                )
            should_continue = False
            continue

        # --- ack phase. --------------------------------------------------
        try:
            mapped = map_batch_items(
                legacy_results,
                echo_keys=[venue_order_id for *_r, venue_order_id in persisted],
                echo_candidate_fields=CANCEL_ECHO_CANDIDATE_FIELDS,
            )
            ack_time = _now()
            with _cancel_journal_transaction(conn, owner="batch_cancel_ack"):
                for (idx, command_id, venue_order_id), mapped_item in zip(persisted, mapped):
                    if mapped_item.source == "unmapped":
                        outcome = CancelOutcome("UNKNOWN", "batch_response_unmapped", {})
                    else:
                        outcome = parse_cancel_response(mapped_item.raw_item)
                    if is_cancel_confirmed_status(outcome.status):
                        append_event(
                            conn, command_id=command_id, event_type="CANCEL_ACKED", occurred_at=ack_time,
                            payload={"venue_order_id": venue_order_id, "cancel_outcome": outcome.raw_response, "batch": True},
                        )
                        outcomes[idx] = BatchCancelOutcome(idx, command_id, "acked", venue_order_id=venue_order_id)
                    elif outcome.status == "NOT_CANCELED":
                        append_event(
                            conn, command_id=command_id, event_type="CANCEL_FAILED", occurred_at=ack_time,
                            payload={"venue_order_id": venue_order_id, "reason": outcome.reason, "cancel_outcome": outcome.raw_response, "batch": True},
                        )
                        outcomes[idx] = BatchCancelOutcome(idx, command_id, "not_canceled", venue_order_id=venue_order_id, error_message=outcome.reason)
                    else:
                        _append_cancel_unknown(conn, command_id, outcome, ack_time)
                        outcomes[idx] = BatchCancelOutcome(idx, command_id, "unknown", venue_order_id=venue_order_id, error_message=outcome.reason)
        except Exception as exc:
            # A venue call may already have happened. Discard speculative ACKs,
            # then journal uncertainty; never send the chunk a second time here.
            try:
                with _cancel_journal_transaction(conn, owner="batch_cancel_ack_unknown"):
                    for _idx, command_id, _venue_order_id in persisted:
                        _append_cancel_unknown(
                            conn, command_id,
                            CancelOutcome("UNKNOWN", "batch_cancel_ack_persist_failed", {}), _now(),
                        )
            except Exception as persist_exc:
                error = f"batch_cancel_unknown_persist_failed:{_journal_error(persist_exc)}"
            else:
                error = f"batch_cancel_ack_persist_failed:{_journal_error(exc)}"
            for idx, command_id, venue_order_id in persisted:
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "unknown", venue_order_id=venue_order_id,
                    error_message=error,
                )
            should_continue = False

    return [o for o in outcomes if o is not None]
