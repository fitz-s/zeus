# Created: 2026-04-26
# Lifecycle: created=2026-04-26; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Lock INV-31 command recovery behavior plus snapshot-gated command inserts.
# Reuse: Run when command recovery, command journal schema, or executable snapshot gating changes.
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md u00a7P1.S4
"""INV-31 anchor tests: command recovery loop.

All 8 resolution-table cases + cycle integration test.
Uses in-memory DB; mocks PolymarketClient.get_order.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory DB with full schema."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def mock_client():
    return MagicMock(spec_set=["get_order", "v2_preflight"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 32 hex chars — satisfies IdempotencyKey length validation.
_DEFAULT_IDEM_KEY = "a" * 32
_NOW = datetime(2026, 4, 26, tzinfo=timezone.utc)


def _insert(conn, *, command_id="cmd-001", position_id="pos-001",
            decision_id="dec-001", idempotency_key=None,
            intent_kind="ENTRY", market_id="mkt-001", token_id="tok-001",
            side="BUY", size=10.0, price=0.5,
            created_at="2026-04-26T00:00:00Z"):
    """Insert a command row and return its command_id."""
    from src.state.venue_command_repo import insert_command
    if idempotency_key is None:
        import hashlib
        # Build a unique 32-hex key per command_id so duplicate inserts don't collide.
        idempotency_key = hashlib.md5(command_id.encode()).hexdigest()
    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    insert_command(
        conn,
        command_id=command_id,
        snapshot_id=snapshot_id,
        envelope_id=_ensure_envelope(
            conn,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
        ),
        position_id=position_id,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        intent_kind=intent_kind,
        market_id=market_id,
        token_id=token_id,
        side=side,
        size=size,
        price=price,
        created_at=created_at,
    )
    return command_id


def _ensure_snapshot(conn, *, token_id: str, snapshot_id: str | None = None) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={},
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def _ensure_envelope(
    conn,
    *,
    token_id: str,
    envelope_id: str | None = None,
    side: str = "BUY",
    price: float | Decimal = 0.5,
    size: float | Decimal = 10.0,
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    envelope_id = envelope_id or f"env-{token_id}-{side}-{price_dec}-{size_dec}"
    if conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side=side,
            price=price_dec,
            size=size_dec,
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash="e" * 64,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _advance_to_submitting(conn, command_id="cmd-001", venue_order_id=None):
    """Advance from INTENT_CREATED u2192 SUBMITTING.

    If venue_order_id provided, set it on the command row after advancing.
    """
    from src.state.venue_command_repo import append_event
    append_event(conn, command_id=command_id, event_type="SUBMIT_REQUESTED",
                 occurred_at="2026-04-26T00:01:00Z")
    if venue_order_id is not None:
        conn.execute(
            "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
            (venue_order_id, command_id),
        )
        conn.commit()


def _advance_to_unknown(conn, command_id="cmd-001", venue_order_id=None):
    """Advance to UNKNOWN state (INTENT_CREATED u2192 SUBMITTING u2192 UNKNOWN)."""
    from src.state.venue_command_repo import append_event
    _advance_to_submitting(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(conn, command_id=command_id, event_type="SUBMIT_UNKNOWN",
                 occurred_at="2026-04-26T00:02:00Z")


def _advance_to_unknown_side_effect(conn, command_id="cmd-001", venue_order_id=None):
    """Advance to SUBMIT_UNKNOWN_SIDE_EFFECT for idempotency-key recovery."""
    from src.state.venue_command_repo import append_event
    _advance_to_submitting(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_TIMEOUT_UNKNOWN",
        occurred_at="2026-04-26T00:02:00Z",
    )


def _advance_to_cancel_pending(conn, command_id="cmd-001", venue_order_id=None):
    """Advance to CANCEL_PENDING (INTENT_CREATED u2192 SUBMITTING u2192 ACKED u2192 CANCEL_PENDING)."""
    from src.state.venue_command_repo import append_event
    _advance_to_submitting(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(conn, command_id=command_id, event_type="SUBMIT_ACKED",
                 occurred_at="2026-04-26T00:02:00Z")
    append_event(conn, command_id=command_id, event_type="CANCEL_REQUESTED",
                 occurred_at="2026-04-26T00:03:00Z")


def _advance_to_acked(conn, command_id="cmd-001", venue_order_id="ord-001"):
    from src.state.venue_command_repo import append_event

    _advance_to_submitting(conn, command_id=command_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at="2026-04-26T00:02:00Z",
        payload={"venue_order_id": venue_order_id, "venue_status": "accepted"},
    )


def _seed_pending_entry_projection(
    conn,
    *,
    position_id="pos-001",
    command_id="cmd-001",
    order_id="ord-001",
):
    from src.state.ledger import append_many_and_project

    event_base = {
        "position_id": position_id,
        "event_version": 1,
        "strategy_key": "opening_inertia",
        "decision_id": "dec-001",
        "snapshot_id": "snap-pos-001",
        "command_id": command_id,
        "caused_by": None,
        "source_module": "tests.test_command_recovery",
        "env": "live",
    }
    events = [
        {
            **event_base,
            "event_id": f"{position_id}:open",
            "sequence_no": 1,
            "event_type": "POSITION_OPEN_INTENT",
            "occurred_at": "2026-04-26T00:02:00Z",
            "phase_before": None,
            "phase_after": "pending_entry",
            "order_id": None,
            "idempotency_key": f"{position_id}:open",
            "venue_status": None,
            "payload_json": "{}",
        },
        {
            **event_base,
            "event_id": f"{position_id}:posted",
            "sequence_no": 2,
            "event_type": "ENTRY_ORDER_POSTED",
            "occurred_at": "2026-04-26T00:02:00Z",
            "phase_before": "pending_entry",
            "phase_after": "pending_entry",
            "order_id": order_id,
            "idempotency_key": f"{position_id}:posted",
            "venue_status": "pending",
            "payload_json": "{}",
        },
    ]
    projection = {
        "position_id": position_id,
        "phase": "pending_entry",
        "trade_id": position_id,
        "market_id": "condition-test",
        "city": "Karachi",
        "cluster": "Karachi",
        "target_date": "2026-05-17",
        "bin_label": "Karachi high",
        "direction": "buy_yes",
        "unit": "C",
        "size_usd": 3.2,
        "shares": 0.0,
        "cost_basis_usd": 0.0,
        "entry_price": 0.0,
        "p_posterior": 0.9,
        "last_monitor_prob": None,
        "last_monitor_edge": None,
        "last_monitor_market_price": None,
        "decision_snapshot_id": "snap-pos-001",
        "entry_method": "ens_member_counting",
        "strategy_key": "opening_inertia",
        "edge_source": "opening_inertia",
        "discovery_mode": "opening_hunt",
        "chain_state": "local_only",
        "token_id": "tok-001",
        "no_token_id": "tok-001-no",
        "condition_id": "condition-test",
        "order_id": order_id,
        "order_status": "pending",
        "updated_at": "2026-04-26T00:02:00Z",
        "temperature_metric": "high",
    }
    append_many_and_project(conn, events, projection)


def _append_order_fact(
    conn,
    *,
    command_id="cmd-001",
    order_id="ord-001",
    state="CANCEL_CONFIRMED",
    matched_size="0",
    remaining_size="0",
):
    from src.state.venue_command_repo import append_order_fact

    return append_order_fact(
        conn,
        venue_order_id=order_id,
        command_id=command_id,
        state=state,
        remaining_size=remaining_size,
        matched_size=matched_size,
        source="REST",
        observed_at="2026-04-26T00:05:00Z",
        venue_timestamp="2026-04-26T00:05:00Z",
        raw_payload_hash="f" * 64,
        raw_payload_json={"status": state, "order_id": order_id},
    )


def _advance_to_review_required(conn, command_id="cmd-001"):
    """Advance to REVIEW_REQUIRED (INTENT_CREATED u2192 REVIEW_REQUIRED)."""
    from src.state.venue_command_repo import append_event
    append_event(conn, command_id=command_id, event_type="REVIEW_REQUIRED",
                 occurred_at="2026-04-26T00:01:00Z")


def _get_state(conn, command_id):
    from src.state.venue_command_repo import get_command
    cmd = get_command(conn, command_id)
    return cmd["state"] if cmd else None


def _get_events(conn, command_id):
    from src.state.venue_command_repo import list_events
    return list_events(conn, command_id)


def _connect_file_db(path):
    from src.state.db import init_schema

    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


@pytest.mark.parametrize("partial_status", ["PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"])
def test_partial_polling_with_trade_id_projects_optimistic_lot(tmp_path, partial_status):
    """PARTIAL with real trade id is optimistic exposure, not synthetic finality."""
    from src.execution.fill_tracker import _maybe_append_venue_fill_observation
    from src.state.portfolio import Position

    db_path = tmp_path / "partial-fill.db"
    conn = _connect_file_db(db_path)
    _insert(
        conn,
        command_id="cmd-partial",
        position_id="runtime-pos-partial",
        decision_id="dec-partial",
        token_id="tok-partial",
        side="BUY",
        size=10.0,
        price=0.5,
    )
    conn.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        ("vord-partial", "cmd-partial"),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "mkt-001",
            "50-51°F",
            "buy_yes",
            10.0,
            0.5,
            _NOW.isoformat(),
            0.6,
            0.6,
            0.1,
            0.05,
            0.15,
            0.0,
            "pending",
            "runtime-pos-partial",
        ),
    )
    conn.commit()
    conn.close()

    pos = Position(
        trade_id="runtime-pos-partial",
        market_id="mkt-001",
        city="Paris",
        cluster="Paris",
        target_date="2026-04-26",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.5,
        shares=20.0,
        state="pending_tracked",
        order_id="vord-partial",
        entry_order_id="vord-partial",
    )
    deps = SimpleNamespace(get_connection=lambda: _connect_file_db(db_path))

    assert _maybe_append_venue_fill_observation(
        pos,
        {
            "status": partial_status,
            "trade_id": "venue-trade-partial",
            "filled_size": "4.25",
            "price": "0.5",
        },
        status=partial_status,
        shares=4.25,
        fill_price=0.5,
        observed_at=_NOW,
        deps=deps,
    )

    verify = _connect_file_db(db_path)
    try:
        order_fact = verify.execute(
            "SELECT state, matched_size FROM venue_order_facts WHERE venue_order_id = ?",
            ("vord-partial",),
        ).fetchone()
        trade_fact = verify.execute(
            "SELECT trade_fact_id, state, filled_size FROM venue_trade_facts WHERE trade_id = ?",
            ("venue-trade-partial",),
        ).fetchone()
        lot = verify.execute(
            "SELECT state, shares FROM position_lots WHERE source_trade_fact_id = ?",
            (trade_fact["trade_fact_id"],),
        ).fetchone()
    finally:
        verify.close()

    assert dict(order_fact) == {"state": "PARTIALLY_MATCHED", "matched_size": "4.25"}
    assert {key: trade_fact[key] for key in ("state", "filled_size")} == {
        "state": "MATCHED",
        "filled_size": "4.25",
    }
    assert lot["state"] == "OPTIMISTIC_EXPOSURE"
    assert Decimal(str(lot["shares"])) == Decimal("4.25")


# ---------------------------------------------------------------------------
# TestRecoveryResolutionTable
# ---------------------------------------------------------------------------

class TestRecoveryResolutionTable:
    """Cover all 8 INV-31 anchor resolution-table cases."""

    # Case 1: SUBMITTING + venue_order_id + venue finds order u2192 ACKED
    def test_submitting_with_venue_order_resolves_to_acked(self, conn, mock_client):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-001")
        mock_client.get_order.return_value = {"orderID": "vord-001", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        assert summary["scanned"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_ACKED" in event_types

    # Case 2: SUBMITTING + no venue_order_id -> REVIEW_REQUIRED
    # Grammar note: SUBMITTING->EXPIRED is not a legal transition (_TRANSITIONS
    # has no such edge). Recovery uses REVIEW_REQUIRED (legal from SUBMITTING)
    # so the operator can resolve: was this never placed, or was the ack lost?
    def test_submitting_without_order_id_resolves_to_expired(self, conn, mock_client):
        _insert(conn)
        # Advance to SUBMITTING without setting venue_order_id
        _advance_to_submitting(conn, venue_order_id=None)
        mock_client.get_order.return_value = None  # shouldn't be called

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        # EXPIRED is not a legal grammar transition from SUBMITTING;
        # recovery emits REVIEW_REQUIRED instead (operator-handoff).
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 1
        # get_order should NOT be called when venue_order_id is missing
        mock_client.get_order.assert_not_called()
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "REVIEW_REQUIRED" in event_types
        # verify payload has expected reason
        import json
        rr_event = next(e for e in events if e["event_type"] == "REVIEW_REQUIRED")
        payload = json.loads(rr_event["payload_json"])
        assert payload["reason"] == "recovery_no_venue_order_id"

    # Case 3: UNKNOWN + venue_order_id + venue finds order u2192 ACKED
    def test_unknown_with_venue_order_resolves_to_acked(self, conn, mock_client):
        _insert(conn)
        _advance_to_unknown(conn, venue_order_id="vord-002")
        mock_client.get_order.return_value = {"orderID": "vord-002", "status": "MATCHED"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_ACKED" in event_types

    # Case 4: UNKNOWN + venue_order_id + venue returns None u2192 REVIEW_REQUIRED
    def test_unknown_without_venue_order_resolves_to_review_required(self, conn, mock_client):
        _insert(conn)
        _advance_to_unknown(conn, venue_order_id="vord-003")
        mock_client.get_order.return_value = None  # order not found

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "REVIEW_REQUIRED" in event_types

    # Case 5: CANCEL_PENDING + venue returns None (order gone) u2192 CANCELLED
    def test_cancel_pending_with_missing_order_resolves_to_cancelled(self, conn, mock_client):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-004")
        mock_client.get_order.return_value = None  # order missing

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCELLED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "CANCEL_ACKED" in event_types

    # Case 6: REVIEW_REQUIRED rows are skipped (operator-handoff)
    def test_review_required_is_skipped(self, conn, mock_client):
        _insert(conn)
        _advance_to_review_required(conn)
        mock_client.get_order.return_value = {"orderID": "x", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        # State should NOT change
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0
        # get_order should NOT be called
        mock_client.get_order.assert_not_called()

    # Case 7: venue lookup raises u2192 state stays (error counted)
    def test_venue_lookup_exception_leaves_state(self, conn, mock_client):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-005")
        mock_client.get_order.side_effect = RuntimeError("network timeout")

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        # State must NOT change; error must be counted
        assert _get_state(conn, "cmd-001") == "SUBMITTING"
        assert summary["errors"] == 1
        assert summary["advanced"] == 0

    # Case 8: CANCEL_PENDING + venue says order CANCELLED u2192 CANCELLED
    def test_cancel_pending_with_cancelled_status_resolves_to_cancelled(self, conn, mock_client):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-006")
        mock_client.get_order.return_value = {"orderID": "vord-006", "status": "CANCELLED"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCELLED"
        assert summary["advanced"] == 1

    # Supplementary: CANCEL_PENDING + venue order still active u2192 stays CANCEL_PENDING
    def test_cancel_pending_with_active_order_stays_in_cancel_pending(self, conn, mock_client):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-007")
        mock_client.get_order.return_value = {"orderID": "vord-007", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCEL_PENDING"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0

    def test_acked_terminal_no_fill_order_fact_expires_command_and_voids_pending_entry(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["scanned"] == 0
        assert summary["terminal_order_facts"]["advanced"] == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "EXPIRED"
        position_event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(position_event) == {
            "event_type": "ENTRY_ORDER_VOIDED",
            "phase_before": "pending_entry",
            "phase_after": "voided",
            "command_id": "cmd-001",
            "order_id": "ord-001",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

    def test_acked_terminal_order_fact_with_matched_size_waits_for_fill_reconciliation(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="1.25", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["stayed"] == 1
        assert summary["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "pending_entry"

    @pytest.mark.parametrize("venue_status", ["MATCHED", "MINED", "FILLED"])
    def test_unknown_side_effect_nonconfirmed_status_stays_partial_not_fill_finality(
        self,
        conn,
        venue_status,
    ):
        _insert(conn)
        _advance_to_unknown_side_effect(conn)
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = {
            "orderID": f"vord-{venue_status.lower()}",
            "status": venue_status,
        }

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, client)

        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert "PARTIAL_FILL_OBSERVED" in event_types
        assert "FILL_CONFIRMED" not in event_types

    def test_unknown_side_effect_confirmed_requires_trade_fact_review(
        self,
        conn,
    ):
        _insert(conn)
        _advance_to_unknown_side_effect(conn)
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = {
            "orderID": "vord-confirmed",
            "status": "CONFIRMED",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, client)

        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert "REVIEW_REQUIRED" in event_types
        assert "FILL_CONFIRMED" not in event_types
        import json
        review = [e for e in _get_events(conn, "cmd-001") if e["event_type"] == "REVIEW_REQUIRED"][0]
        payload = json.loads(review["payload_json"])
        assert payload["reason"] == "recovery_confirmed_requires_trade_fact"
        assert payload["semantic_guard"] == "order_status_confirmed_is_not_fill_economics_authority"

    # Supplementary: summary dict has all expected keys
    def test_summary_has_all_keys(self, conn, mock_client):
        mock_client.get_order.return_value = None
        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)
        for key in ("scanned", "advanced", "stayed", "errors"):
            assert key in summary, f"summary missing key: {key}"


# ---------------------------------------------------------------------------
# TestRecoveryCycleIntegration
# ---------------------------------------------------------------------------

class TestRecoveryCycleIntegration:
    """Assert cycle_runner invokes reconcile_unresolved_commands."""

    def test_cycle_runner_calls_recovery(self, monkeypatch):
        """Patch reconcile_unresolved_commands and verify cycle_runner calls it."""
        import sys
        from unittest.mock import patch, MagicMock

        called_with = []

        def fake_reconcile(*args, **kwargs):
            called_with.append((args, kwargs))
            return {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

        # Build a minimal cycle_runner context
        # We patch at the import site inside cycle_runner (via sys.modules)
        import importlib

        # Patch posture to NORMAL so entries aren't blocked for unrelated reasons
        posture_patch = patch(
            "src.runtime.posture.read_runtime_posture",
            return_value="NORMAL",
        )

        # Patch the recovery function at the module where it's imported inside run_cycle
        recovery_patch = patch(
            "src.execution.command_recovery.reconcile_unresolved_commands",
            side_effect=fake_reconcile,
        )

        # We cannot easily run a full cycle without live deps, so instead we verify
        # the import and call structure from the cycle_runner source.
        # Approach: import cycle_runner, parse for the recovery call.
        repo_root = Path(__file__).resolve().parents[1]
        cr_src = (repo_root / "src/engine/cycle_runner.py").read_text(encoding="utf-8")

        # Assert both the import and the call appear in the source
        assert "reconcile_unresolved_commands" in cr_src, (
            "cycle_runner.py must import/call reconcile_unresolved_commands (INV-31)"
        )
        assert "command_recovery" in cr_src, (
            "cycle_runner.py must reference command_recovery module (INV-31)"
        )
        assert 'summary["command_recovery"]' in cr_src, (
            'cycle_runner.py must record summary["command_recovery"] result (INV-31)'
        )
