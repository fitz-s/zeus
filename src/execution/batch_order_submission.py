# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "batch submit + safe prefixes" (BUILD (thin)) +
#   docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W2 packet ("W2.1 batch submit/cancel wrapper ... lands INERT — no
#   production call site yet") + architecture/invariants.yaml INV-28
#   (persist-before-side-effect) applied at batch shape.
"""W2.1 batch submit/cancel journal orchestrator (inert -- no production
call site). Extends the single-order INV-28 sequence (the reference is
execute_exit_order, src/execution/executor.py:4394-4476: persist
INTENT_CREATED + SUBMIT_REQUESTED, COMMIT, THEN the one SDK call, THEN ack)
to N orders sharing ONE SDK call per chunk.

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
  fresh (new command_ids/idempotency_keys) via another submit_orders_batch
  call. This was chosen over "persist all N chunks upfront, then work
  through them" specifically to avoid a batch-wide half-submitted journal
  state whose resolution would otherwise require new recovery-loop
  machinery this inert packet does not build.

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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from src.contracts.execution_intent import ExecutionIntent
from src.execution.command_bus import IdempotencyKey, IntentKind
from src.execution.self_trade_guard import SelfTradeCheckResult, SelfTradeVerdict
from src.venue.batch_submit import MAX_ORDERS_PER_BATCH, chunk_orders


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_execution_capability_payload() -> dict:
    # ENTRY SUBMIT_REQUESTED events are validated against a specific
    # economics-proof schema (venue_command_repo._validate_entry_submit_
    # payload / _ENTRY_SUBMIT_REQUIRED_COMPONENTS) that only the real entry
    # decision path can populate (q_live, expected_edge, etc.) -- solving
    # that is W3's job, out of scope here. EXIT/CANCEL have no such
    # validator (early-return in _validate_entry_submit_payload), so a
    # minimal always-allowed shape is sufficient there. Callers submitting
    # ENTRY intents MUST supply their own execution_capability_payload via
    # BatchSubmitRequest; leaving it unset for ENTRY fails loud (ValueError
    # from append_event), not silently.
    return {"allowed": True, "components": []}


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


_SUBMIT_STATUS_TO_EVENT = {
    "accepted": "SUBMIT_ACKED",
    "rejected": "SUBMIT_REJECTED",
    "unmapped": "SUBMIT_UNKNOWN",
}
_SUBMIT_STATUS_TO_OUTCOME = {
    "accepted": "acked",
    "rejected": "rejected",
    "unmapped": "unknown",
}


def submit_orders_batch(
    conn: sqlite3.Connection,
    adapter: Any,
    client: Any,
    requests: Sequence[BatchSubmitRequest],
    *,
    rate_budget: Any = None,
    self_trade_verdicts: Optional[Mapping[int, SelfTradeCheckResult]] = None,
) -> list[BatchSubmitOutcome]:
    """Submit ``requests`` in chunks of at most MAX_ORDERS_PER_BATCH.

    ``adapter`` builds envelopes (create_submission_envelope, FC-03).
    ``client`` is the INV-24 gateway (place_limit_orders_batch) -- NOT the
    adapter directly; this module is on the INV-24 allowlist precisely so
    it may call that gateway.

    ``rate_budget`` (optional, ``src.venue.rate_budget.VenueRateBudget``):
    when provided, checked once per chunk (RequestClass.SUBMIT) before that
    chunk's persist phase. Absent -> today's behavior (no rate gating here).

    ``self_trade_verdicts`` (optional): maps a request's position in
    ``requests`` (0-based index, matching the returned outcome's
    ``request_index``) to a pre-computed ``SelfTradeCheckResult``. A
    non-CLEAR verdict (WOULD_SELF_CROSS or INDETERMINATE -- fail closed)
    blocks only that one request before persistence; the rest of its chunk
    proceeds normally. Absent -> today's behavior (no self-trade gating
    here); wiring the real guard is W3's job.

    Returns exactly one outcome per input request, in input order.
    """
    from src.venue.rate_budget import RequestClass

    outcomes: list[Optional[BatchSubmitOutcome]] = [None] * len(requests)
    should_continue = True
    verdicts = self_trade_verdicts or {}

    for chunk in chunk_orders(list(enumerate(requests)), MAX_ORDERS_PER_BATCH):
        if not should_continue:
            for idx, _req in chunk:
                outcomes[idx] = BatchSubmitOutcome(request_index=idx, status="not_attempted")
            continue

        accepted: list[tuple[int, BatchSubmitRequest]] = []
        for idx, req in chunk:
            verdict = verdicts.get(idx)
            if verdict is not None and verdict.verdict != SelfTradeVerdict.CLEAR:
                outcomes[idx] = BatchSubmitOutcome(
                    request_index=idx,
                    status="rejected",
                    error_code="SELF_TRADE_GUARD_BLOCKED",
                    error_message=f"verdict={verdict.verdict.value} reason={verdict.reason}",
                )
                continue
            accepted.append((idx, req))

        if not accepted:
            continue

        if rate_budget is not None:
            budget_result = rate_budget.try_acquire(RequestClass.SUBMIT)
            if not budget_result.granted:
                for idx, _req in accepted:
                    outcomes[idx] = BatchSubmitOutcome(
                        request_index=idx,
                        status="rate_limited",
                        error_code=f"RATE_BUDGET_{budget_result.decision.value.upper()}",
                        error_message=f"retry_after={budget_result.wait_seconds:.2f}s",
                    )
                should_continue = False
                continue

        # --- persist phase: N commands + N SUBMIT_REQUESTED events, ONE
        # transaction, committed BEFORE the chunk's SDK call. -----------
        now = _now()
        persisted: list[tuple[int, BatchSubmitRequest, str, str, Any]] = []
        try:
            for idx, req in accepted:
                envelope = adapter.create_submission_envelope(
                    req.intent, req.snapshot, req.order_type, req.post_only
                )
                # Canonicalize price/size to float precision BEFORE
                # persisting anything: insert_command's price/size columns
                # are float-typed (matching the single-order path's plain
                # float arithmetic), while envelope.price/size come from
                # create_submission_envelope's Decimal division
                # (_size_from_intent) which can be a repeating decimal
                # (e.g. 10/0.11). Persisting the EXACT Decimal into
                # venue_submission_envelopes and a float-rounded value into
                # venue_commands would make _assert_envelope_gate's
                # cross-check fail on the rounding difference. Round once,
                # consistently, and persist the SAME rounded value to both
                # tables.
                envelope = envelope.with_updates(
                    price=Decimal(str(float(envelope.price))),
                    size=Decimal(str(float(envelope.size))),
                )
                command_id = uuid.uuid4().hex[:16]
                envelope_id = f"batch:{command_id}"
                from src.state.venue_command_repo import insert_submission_envelope

                insert_submission_envelope(conn, envelope, envelope_id=envelope_id)

                idem = IdempotencyKey.from_inputs(
                    decision_id=req.decision_id,
                    token_id=str(envelope.selected_outcome_token_id),
                    side=str(envelope.side),
                    price=float(envelope.price),
                    size=float(envelope.size),
                    intent_kind=req.intent_kind,
                )

                from src.state.venue_command_repo import insert_command

                insert_command(
                    conn,
                    command_id=command_id,
                    snapshot_id=req.intent.executable_snapshot_id,
                    envelope_id=envelope_id,
                    position_id=req.position_id,
                    decision_id=req.decision_id,
                    idempotency_key=idem.value,
                    intent_kind=req.intent_kind.value,
                    market_id=req.intent.market_id,
                    token_id=str(envelope.selected_outcome_token_id),
                    side=str(envelope.side),
                    size=float(envelope.size),
                    price=float(envelope.price),
                    created_at=now,
                    snapshot_checked_at=now,
                    expected_min_tick_size=req.intent.executable_snapshot_min_tick_size,
                    expected_min_order_size=req.intent.executable_snapshot_min_order_size,
                    expected_neg_risk=req.intent.executable_snapshot_neg_risk,
                )

                from src.state.venue_command_repo import append_event

                capability_payload = (
                    req.execution_capability_payload
                    if req.execution_capability_payload is not None
                    else _default_execution_capability_payload()
                )
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REQUESTED",
                    occurred_at=now,
                    payload={
                        "order_type": req.order_type,
                        "execution_capability": capability_payload,
                        "batch": True,
                    },
                )
                persisted.append((idx, req, command_id, idem.value, envelope))
        except Exception as exc:
            # Any request's persist failure invalidates the WHOLE chunk's
            # submission attempt -- fail closed rather than partially
            # commit a chunk whose journal wouldn't match what gets sent
            # to the SDK. Roll back; nothing in this chunk was committed.
            try:
                conn.rollback()
            except Exception:
                pass
            for idx, _req in accepted:
                outcomes[idx] = BatchSubmitOutcome(
                    request_index=idx,
                    status="rejected",
                    error_code="BATCH_PERSIST_FAILED",
                    error_message=str(exc),
                )
            should_continue = False
            continue
        conn.commit()

        # --- SDK call: ONE call for the whole chunk. --------------------
        try:
            legacy_results = client.place_limit_orders_batch([envelope for *_rest, envelope in persisted])
        except Exception as exc:
            ack_time = _now()
            from src.state.venue_command_repo import append_event

            for idx, _req, command_id, idem_value, _envelope in persisted:
                try:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="SUBMIT_TIMEOUT_UNKNOWN",
                        occurred_at=ack_time,
                        payload={
                            "reason": "post_submit_exception_possible_side_effect",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "idempotency_key": idem_value,
                            "batch": True,
                        },
                    )
                except Exception:
                    pass
                outcomes[idx] = BatchSubmitOutcome(
                    request_index=idx,
                    status="unknown_side_effect",
                    command_id=command_id,
                    idempotency_key=idem_value,
                    error_code="V2_BATCH_SUBMIT_EXCEPTION",
                    error_message=str(exc),
                )
            conn.commit()
            should_continue = False
            continue

        # --- ack phase: one event per order, from the ONE response. ----
        ack_time = _now()
        from src.state.venue_command_repo import append_event

        for (idx, _req, command_id, idem_value, _envelope), legacy_result in zip(persisted, legacy_results):
            success = bool((legacy_result or {}).get("success"))
            error_code = (legacy_result or {}).get("errorCode")
            error_message = (legacy_result or {}).get("errorMessage")
            order_id = (legacy_result or {}).get("orderID")
            if success:
                submit_status = "accepted"
            elif error_code == "BATCH_RESPONSE_UNMAPPED":
                # Stable signal set verbatim by
                # polymarket_v2_adapter._unmapped_submit_result -- the
                # fail-closed mapping branch (ruling 1(c)), distinct from an
                # ordinary deterministic rejection.
                submit_status = "unmapped"
            else:
                submit_status = "rejected"
            event_type = _SUBMIT_STATUS_TO_EVENT[submit_status]
            append_event(
                conn,
                command_id=command_id,
                event_type=event_type,
                occurred_at=ack_time,
                payload={
                    "order_id": order_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "batch": True,
                },
            )
            outcomes[idx] = BatchSubmitOutcome(
                request_index=idx,
                status=_SUBMIT_STATUS_TO_OUTCOME[submit_status],
                command_id=command_id,
                idempotency_key=idem_value,
                order_id=order_id,
                error_code=error_code,
                error_message=error_message,
            )
        conn.commit()

    return [o for o in outcomes if o is not None]


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
    halts later chunks, mirroring submit_orders_batch.
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
