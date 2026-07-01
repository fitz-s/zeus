# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: Wellington ad064baf never-submitted-ghost reconcile (live_order_pathology 2026-06-22)
"""TDD anchor: reconcile abandoned never-submitted EDLI ghosts.

A "ghost" aggregate stalls at ``ExecutionCommandCreated`` with NO subsequent
event (no VenueSubmitAttempted / SubmitRejected / SubmitUnknown / ack / user
event / Reconciled), ``venue_order_id IS NULL`` in the projection, and ZERO
``venue_commands`` rows for its execution_command_id — i.e. it NEVER reached the
venue and has $0 capital at risk (executor accepted the command internally then
was interrupted before the venue submit). Such a ghost is non-terminal per
``event_reactor_adapter._TERMINAL_EVENT_SQL`` so it permanently blocks the
duplicate-suppression family lock ``_locked_live_opportunity_active_order_reason``.

These tests lock the behavior of
``command_recovery.reconcile_abandoned_unsubmitted_ghosts``:

  * finds + terminalizes a ghost AFTER the grace period (pre-submit SubmitRejected
    that the production ledger ACCEPTS, projection becomes terminal, cap RELEASED);
  * the duplicate-suppression terminal predicate (_TERMINAL_EVENT_SQL) sees the
    aggregate as terminal after reconcile (lock releases);
  * NEVER terminalizes a ghost with ANY venue presence (venue_order_id, a
    venue_commands row, or a VenueSubmitAttempted) — the venue-truth guard;
  * NEVER terminalizes a fresh ghost still within the grace period.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_cap import LiveCapLedger
from src.state.db import init_schema


# The ghost's ExecutionCommandCreated occurred well before "now": old enough that
# (now - occurred_at) exceeds the safe-replay grace window.
OLD = datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)
# A fresh ghost: ExecutionCommandCreated only moments ago (inside the grace window).
FRESH = datetime.now(timezone.utc) - timedelta(seconds=30)

CAP_SCOPE = "tiny_live_canary"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _pre_submit_payload(*, event_id: str, final_intent_id: str) -> dict:
    """A PreSubmitRevalidated payload the production ledger accepts (post-only maker)."""
    return {
        "event_id": event_id,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "final_intent_id": final_intent_id,
        "condition_id": "condition-1",
        "token_id": "token-yes",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-06-22T00:00:00+00:00",
        "quote_seen_at": "2026-06-21T23:59:59.950000+00:00",
        "quote_age_ms": 50,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.41,
        "current_best_ask": 0.43,
        "limit_price": 0.40,
        "size": 10.0,
        "q_live": 0.50,
        "q_lcb_5pct": 0.45,
        "expected_edge": 0.05,
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_type": "direct",
            "route_id": "DIRECT_YES:bin-1@proof",
            "side": "YES",
            "candidate_id": "YES:bin-1:DIRECT_YES:bin-1@proof",
            "bin_id": "bin-1",
            "payoff_q_point": 0.50,
            "payoff_q_lcb": 0.45,
            "cost": 0.40,
            "edge_lcb": 0.05,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.05,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.45,
        },
        "expected_edge_source_certificate_hash": "edge-cert-hash-1",
        "cost_basis_source_certificate_hash": "cost-cert-hash-1",
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": "2026-06-21T23:59:59.950000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-06-22T00:00:00+00:00",
        "user_ws_authority_id": "ws_gap_guard",
        "user_ws_checked_at": "2026-06-22T00:00:00+00:00",
        "venue_connectivity_authority_id": "polymarket_public_orderbook",
        "venue_connectivity_checked_at": "2026-06-22T00:00:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-06-22T00:00:00+00:00",
    }


def _build_ghost(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str = "event-1:intent-1",
    event_id: str = "event-1",
    final_intent_id: str = "intent-1",
    execution_command_id: str = "cmd-1",
    occurred_at: datetime = OLD,
    with_submit_attempt: bool = False,
) -> LiveOrderAggregateLedger:
    """Build a never-submitted ghost aggregate through ExecutionCommandCreated.

    Uses the PRODUCTION ledger (LiveOrderAggregateLedger.append_event), so the
    sequence is legal per _validate_event_append. A RESERVED cap usage row keyed
    by execution_command_id is seeded via the production LiveCapLedger.
    """
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="DecisionProofAccepted",
        payload={"event_id": event_id, "final_intent_id": final_intent_id},
        occurred_at=occurred_at,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="SubmitPlanBuilt",
        payload={
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "condition_id": "condition-1",
            "token_id": "token-yes",
            "direction": "buy_yes",
            "city": "boston",
            "target_date": "2026-06-23",
            "metric": "tmax",
            "family_id": "fam-1",
        },
        occurred_at=occurred_at,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(event_id=event_id, final_intent_id=final_intent_id),
        occurred_at=occurred_at,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="LiveCapReserved",
        payload={"event_id": event_id, "final_intent_id": final_intent_id, "usage_id": "usage-1"},
        occurred_at=occurred_at,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "execution_command_id": execution_command_id,
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=occurred_at,
        source_authority="engine_adapter",
    )
    if with_submit_attempt:
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="VenueSubmitAttempted",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "execution_command_id": execution_command_id,
            },
            occurred_at=occurred_at,
            source_authority="existing_executor",
        )
    # Seed the RESERVED cap usage row keyed by execution_command_id (production
    # LiveCapLedger.reserve), mirroring the live blocking aggregate.
    LiveCapLedger(conn).reserve(
        event_id=event_id,
        decision_time=occurred_at,
        cap_scope=CAP_SCOPE,
        requested_notional_usd=5.0,
        final_intent_id=final_intent_id,
        execution_command_id=execution_command_id,
    )
    return ledger


def _cap_status(conn: sqlite3.Connection, execution_command_id: str) -> str | None:
    row = conn.execute(
        "SELECT reservation_status FROM edli_live_cap_usage WHERE execution_command_id = ?",
        (execution_command_id,),
    ).fetchone()
    return None if row is None else str(row["reservation_status"])


def _terminal_per_duplicate_lock(conn: sqlite3.Connection, aggregate_id: str) -> bool:
    """True iff the duplicate-suppression terminal predicate sees the aggregate
    as terminal (lock would RELEASE). Mirrors event_reactor_adapter._TERMINAL_EVENT_SQL."""
    from src.engine.event_reactor_adapter import _TERMINAL_EVENT_SQL

    row = conn.execute(_TERMINAL_EVENT_SQL, (aggregate_id,)).fetchone()
    return row is not None


# --------------------------------------------------------------------------- #
# Finder + reconcile
# --------------------------------------------------------------------------- #

def test_reconcile_terminalizes_aged_never_submitted_ghost():
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    _build_ghost(conn, occurred_at=OLD)

    # Pre-state: non-terminal -> duplicate lock would SUPPRESS the family.
    assert not _terminal_per_duplicate_lock(conn, "event-1:intent-1")
    assert _cap_status(conn, "cmd-1") == "RESERVED"

    summary = reconcile_abandoned_unsubmitted_ghosts(conn)
    assert summary["advanced"] == 1, summary
    assert summary["errors"] == 0, summary
    assert summary["continuations"] == [
        {
            "reason": "abandoned_unsubmitted_ghost",
            "aggregate_id": "event-1:intent-1",
            "execution_command_id": "cmd-1",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "family_id": "fam-1",
            "city": "boston",
            "target_date": "2026-06-23",
            "metric": "high",
            "condition_id": "condition-1",
            "token_id": "token-yes",
            "direction": "buy_yes",
        }
    ]

    # The appended terminal is a SubmitRejected that the production ledger's
    # _validate_event_append accepts (proves the pre-submit payload is legal).
    projection = LiveOrderAggregateLedger(conn).get_projection("event-1:intent-1")
    assert projection.current_state == "SUBMIT_REJECTED"
    assert projection.pending_reconcile is False

    # Cap usage flipped to RELEASED.
    assert _cap_status(conn, "cmd-1") == "RELEASED"

    # The duplicate-suppression lock now sees the aggregate as terminal -> RELEASE.
    assert _terminal_per_duplicate_lock(conn, "event-1:intent-1")


def test_reconcile_ignores_prior_rejected_attempt_when_current_command_is_ghost():
    """A prior rejected attempt on the same aggregate is not venue presence for
    the current ExecutionCommandCreated row.

    This mirrors the live stuck class from 2026-06-26: an aggregate had an older
    VenueSubmitAttempted/SubmitRejected/CapTransitioned sequence, then a later
    second ExecutionCommandCreated with no venue command row. The old candidate
    SQL disqualified the whole aggregate because of the historical terminal
    events, leaving the current command stuck forever.
    """
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    ledger = _build_ghost(conn, occurred_at=OLD, with_submit_attempt=True)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitRejected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-1",
            "reason_code": "prior_attempt_rejected",
            "venue_call_started": True,
            "pre_submit_rejection": False,
        },
        occurred_at=OLD,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-1",
            "to_status": "RELEASED",
            "projection_status": "RELEASED",
            "transition_reason": "prior_attempt_rejected",
        },
        occurred_at=OLD,
        source_authority="live_cap_ledger",
    )
    cap_usage = conn.execute(
        "SELECT usage_id FROM edli_live_cap_usage WHERE execution_command_id = ?",
        ("cmd-1",),
    ).fetchone()
    LiveCapLedger(conn).release(str(cap_usage["usage_id"]), "prior_attempt_rejected")

    _build_ghost(conn, execution_command_id="cmd-2", occurred_at=OLD + timedelta(minutes=5))
    assert LiveOrderAggregateLedger(conn).get_projection("event-1:intent-1").current_state == (
        "EXECUTION_COMMAND_CREATED"
    )
    assert _cap_status(conn, "cmd-2") == "RESERVED"

    summary = reconcile_abandoned_unsubmitted_ghosts(conn)

    assert summary["advanced"] == 1, summary
    assert summary["errors"] == 0, summary
    assert summary["continuations"][0]["execution_command_id"] == "cmd-2"
    assert summary["continuations"][0]["metric"] == "high"
    projection = LiveOrderAggregateLedger(conn).get_projection("event-1:intent-1")
    assert projection.current_state == "SUBMIT_REJECTED"
    assert _cap_status(conn, "cmd-2") == "RELEASED"


def test_appended_terminal_is_legal_pre_submit_submit_rejected():
    """The terminal event must be a SubmitRejected with a pre-submit payload,
    appended legally per the immutable append-only ledger (no UPDATE/DELETE)."""
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    _build_ghost(conn, occurred_at=OLD)
    reconcile_abandoned_unsubmitted_ghosts(conn)

    row = conn.execute(
        """
        SELECT event_type, payload_json
        FROM edli_live_order_events
        WHERE aggregate_id = ?
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        ("event-1:intent-1",),
    ).fetchone()
    assert row["event_type"] == "SubmitRejected"
    import json

    payload = json.loads(row["payload_json"])
    assert payload["pre_submit_rejection"] is True
    assert payload["submit_status"] == "PRE_SUBMIT_ERROR"
    assert payload["venue_call_started"] is False
    assert payload["execution_command_id"] == "cmd-1"
    assert payload["event_id"] == "event-1"
    assert payload["final_intent_id"] == "intent-1"
    assert str(payload.get("reason_code") or "").strip()


# --------------------------------------------------------------------------- #
# Grace period
# --------------------------------------------------------------------------- #

def test_fresh_ghost_within_grace_is_not_terminalized():
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    _build_ghost(conn, occurred_at=FRESH)

    summary = reconcile_abandoned_unsubmitted_ghosts(conn)
    assert summary["advanced"] == 0, summary

    projection = LiveOrderAggregateLedger(conn).get_projection("event-1:intent-1")
    assert projection.current_state == "EXECUTION_COMMAND_CREATED"
    assert _cap_status(conn, "cmd-1") == "RESERVED"
    assert not _terminal_per_duplicate_lock(conn, "event-1:intent-1")


# --------------------------------------------------------------------------- #
# Venue-truth guard (money-path safety)
# --------------------------------------------------------------------------- #

def test_ghost_with_venue_submit_attempt_is_never_terminalized():
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    # Aged, but a VenueSubmitAttempted exists -> venue boundary WAS crossed.
    _build_ghost(conn, occurred_at=OLD, with_submit_attempt=True)

    summary = reconcile_abandoned_unsubmitted_ghosts(conn)
    assert summary["advanced"] == 0, summary

    projection = LiveOrderAggregateLedger(conn).get_projection("event-1:intent-1")
    assert projection.current_state == "VENUE_SUBMIT_ATTEMPTED"
    assert _cap_status(conn, "cmd-1") == "RESERVED"


def test_ghost_with_venue_order_id_in_projection_is_never_terminalized():
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    _build_ghost(conn, occurred_at=OLD)
    # Force a venue_order_id onto the projection (simulating a resting order whose
    # later events were lost) — the venue-truth guard must refuse to terminalize.
    conn.execute(
        "UPDATE edli_live_order_projection SET venue_order_id = 'venue-resting-1' WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    )

    summary = reconcile_abandoned_unsubmitted_ghosts(conn)
    assert summary["advanced"] == 0, summary
    assert _cap_status(conn, "cmd-1") == "RESERVED"


def test_ghost_with_venue_commands_row_is_never_terminalized():
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    _build_ghost(conn, occurred_at=OLD)
    # A venue_commands row exists for the execution_command_id (decision_id link)
    # -> the order DID reach the venue command bus. Never terminalize.
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at
        ) VALUES (
            'vc-1', 'snap-1', 'env-1', 'pos-1', 'cmd-1',
            'idem-1', 'ENTER', 'market-1', 'token-yes', 'BUY', 5.0, 0.40,
            NULL, 'SUBMITTING', ?, ?
        )
        """,
        ("2026-06-22T00:00:00Z", "2026-06-22T00:00:00Z"),
    )

    summary = reconcile_abandoned_unsubmitted_ghosts(conn)
    assert summary["advanced"] == 0, summary
    assert _cap_status(conn, "cmd-1") == "RESERVED"


def test_no_ghosts_is_a_clean_noop():
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    conn = _conn()
    summary = reconcile_abandoned_unsubmitted_ghosts(conn)
    assert summary == {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0, "continuations": []}


def test_reconcile_payload_is_legal_per_production_validate_event_append():
    """Independent proof the reconcile's SubmitRejected payload is LEGAL per the
    production ledger's _validate_event_append (not merely accepted by the qualified
    bypass). We re-read the exact payload the reconcile appended and replay it onto a
    fresh aggregate built through the real LiveOrderAggregateLedger — if the payload
    violated _is_pre_submit_rejection_payload / _require_command_binding / reason_code
    the ledger would raise."""
    import json

    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    # 1) Run the reconcile and capture the payload it actually appended.
    conn = _conn()
    _build_ghost(conn, occurred_at=OLD)
    reconcile_abandoned_unsubmitted_ghosts(conn)
    appended = conn.execute(
        """
        SELECT payload_json FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = 'SubmitRejected'
        ORDER BY event_sequence DESC LIMIT 1
        """,
        ("event-1:intent-1",),
    ).fetchone()
    appended_payload = json.loads(appended["payload_json"])

    # 2) Replay that exact payload through the PRODUCTION ledger on a fresh
    #    aggregate built to the same ExecutionCommandCreated state. The production
    #    append_event runs _validate_event_append: if it accepts, the payload is
    #    proven legal for a pre-submit terminal directly after ExecutionCommandCreated.
    conn2 = _conn()
    _build_ghost(conn2, occurred_at=OLD)
    ledger2 = LiveOrderAggregateLedger(conn2)
    event = ledger2.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitRejected",
        payload=appended_payload,
        occurred_at=OLD,
        source_authority="existing_executor",
    )
    assert event.event_type == "SubmitRejected"
    assert ledger2.get_projection("event-1:intent-1").current_state == "SUBMIT_REJECTED"
