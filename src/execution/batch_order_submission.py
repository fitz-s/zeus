# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "batch submit + safe prefixes" (BUILD (thin)) +
#   docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W2 packet ("W2.1 batch submit/cancel wrapper ... lands INERT — no
#   production call site yet") + architecture/invariants.yaml INV-28
#   (persist-before-side-effect) applied at batch shape.
"""W2.1 batch cancel journal orchestrator. Extends the single-order INV-28
sequence (the reference is execute_exit_order,
src/execution/executor.py:4394-4476: persist INTENT_CREATED +
SUBMIT_REQUESTED, COMMIT, THEN the one SDK call, THEN ack) to N orders
sharing ONE SDK call per chunk.

The W2.1 packet also built a batch SUBMIT orchestrator (``submit_orders_
batch``) alongside ``cancel_commands_batch`` below; it was deleted as dead
code in the gate-stack simplification (Phase 1, 2026-07-06) -- it had zero
live callers and was never wired to a production submit path.
``BatchSubmitRequest``/``BatchSubmitOutcome`` remain as inert leftover types
from that removal. Only ``cancel_commands_batch`` is live today (wired by
``src.execution.staleness_cancel``); the design notes below describe the
shared batch shape both orchestrators used.

Persist-before-side-effect at batch shape (design decision, documented per
the W2.1 packet brief's open tension -- INV-28's text is singular and does
not natively address batching):

  For each chunk of at most MAX_ORDERS_PER_BATCH orders:
    1. persist: insert_command(INTENT_CREATED) + append_event(SUBMIT_REQUESTED)
       for EVERY order in the chunk, all in one transaction, COMMITTED.
    2. ONE SDK call (client.place_limit_orders_batch / cancel_orders_batch)
       covering the whole chunk.
    3. ack: one append_event per order, mapped from the one response via
       src.venue.batch_submit.map_batch_items's fail-closed precedence.
  Chunks are processed SEQUENTIALLY, and a chunk's persist phase happens
  immediately before that chunk's own SDK call -- never earlier. This means
  an UNREACHED chunk (because an earlier chunk's SDK call raised, or a rate
  budget denied it) is simply never persisted: there is no orphan
  INTENT_CREATED row to clean up, and the caller can retry those requests
  fresh via another batch call. This was chosen over "persist all N chunks
  upfront, then work through them" specifically to avoid a batch-wide
  half-submitted journal state whose resolution would otherwise require new
  recovery-loop machinery this inert packet does not build.

  A chunk HALTS further processing (later chunks become "not_attempted")
  only on an AMBIGUOUS outcome: the SDK call itself raising (side effect
  possibly crossed, mirrors executor.py's SUBMIT_TIMEOUT_UNKNOWN handling)
  or a denied/deferred rate-budget grant. An ordinary deterministic
  per-item outcome (SUBMIT_ACKED, SUBMIT_REJECTED, or even a fail-closed
  SUBMIT_UNKNOWN from response-mapping ambiguity within an otherwise-
  received response) does NOT halt later chunks -- each chunk's SDK call is
  an independent HTTP request.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from src.contracts.execution_intent import ExecutionIntent
from src.execution.command_bus import IntentKind
from src.venue.batch_submit import MAX_ORDERS_PER_BATCH, chunk_orders


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BatchSubmitRequest:
    """One order to submit as part of a batch.

    ``intent``/``snapshot`` feed the FC-03 seam directly
    (``adapter.create_submission_envelope`` -- the sole freshness-assert
    call site); this module never bypasses it or re-implements freshness
    logic. Command-row fields (token_id, side, price, size, market_id) are
    derived from the BUILT envelope, not re-specified here, so they cannot
    drift from what was actually signed.
    """

    decision_id: str
    intent_kind: IntentKind
    position_id: str
    intent: ExecutionIntent
    snapshot: Any
    order_type: str
    post_only: bool = False
    execution_capability_payload: Optional[dict] = None


@dataclass(frozen=True)
class BatchSubmitOutcome:
    request_index: int
    status: str  # "acked" | "rejected" | "unknown" | "unknown_side_effect" | "rate_limited" | "not_attempted"
    command_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    order_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


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
) -> list[BatchCancelOutcome]:
    """Cancel ``command_ids`` in chunks of at most MAX_ORDERS_PER_BATCH.

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
            for idx, command_id, venue_order_id, state in eligible:
                if state != "CANCEL_PENDING":
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="CANCEL_REQUESTED",
                        occurred_at=now,
                        payload={"venue_order_id": venue_order_id, "batch": True},
                    )
                persisted.append((idx, command_id, venue_order_id))
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            for idx, command_id, *_rest in eligible:
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "not_requestable",
                    error_message=f"batch_cancel_persist_failed: {exc}",
                )
            should_continue = False
            continue
        conn.commit()

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
            for idx, command_id, venue_order_id in persisted:
                outcome = CancelOutcome(
                    "UNKNOWN",
                    f"post_cancel_exception_possible_side_effect: {exc}",
                    {"exception_type": type(exc).__name__, "exception_message": str(exc)},
                )
                _append_cancel_unknown(conn, command_id, outcome, _now())
                outcomes[idx] = BatchCancelOutcome(
                    idx, command_id, "unknown", venue_order_id=venue_order_id, error_message=outcome.reason,
                )
            conn.commit()
            should_continue = False
            continue

        # --- ack phase. --------------------------------------------------
        mapped = map_batch_items(
            legacy_results,
            echo_keys=[venue_order_id for *_r, venue_order_id in persisted],
            echo_candidate_fields=CANCEL_ECHO_CANDIDATE_FIELDS,
        )
        ack_time = _now()
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
        conn.commit()

    return [o for o in outcomes if o is not None]
