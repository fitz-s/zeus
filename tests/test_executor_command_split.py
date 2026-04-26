# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S3
"""INV-30 relationship tests: executor split build/persist/submit/ack.

Each test names the relationship it locks, not just the function.
See implementation_plan.md §P1.S3 for the full phase-order spec.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn():
    """In-memory DB with full schema (venue_commands + venue_command_events)."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


def _make_entry_intent(limit_price: float = 0.55, token_id: str = "tok-" + "0" * 36) -> object:
    """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts import Direction

    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=0.02,
        is_sandbox=False,
        market_id="mkt-test-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
    )


def _make_exit_intent(
    trade_id: str = "trd-exit-001",
    token_id: str = "tok-" + "1" * 36,
    shares: float = 10.0,
    current_price: float = 0.55,
) -> object:
    """Build a minimal ExitOrderIntent."""
    from src.execution.executor import create_exit_order_intent

    return create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=shares,
        current_price=current_price,
    )


# ---------------------------------------------------------------------------
# TestLiveOrderCommandSplit — entry path (_live_order / IntentKind.ENTRY)
# ---------------------------------------------------------------------------

class TestLiveOrderCommandSplit:
    """INV-30: _live_order must persist before it submits."""

    def test_persist_precedes_submit(self, mem_conn):
        """insert_command must run before place_limit_order.

        Uses a call-order spy: both insert_command and place_limit_order are
        wrapped; the spy records the order of calls. Assert insert_command
        index < place_limit_order index.
        """
        from src.execution.executor import _live_order

        call_log: list[str] = []

        real_insert = None
        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def spy_insert(*args, **kwargs):
            call_log.append("insert_command")
            return _real_insert(*args, **kwargs)

        intent = _make_entry_intent()

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=spy_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            def spy_place(**kwargs):
                call_log.append("place_limit_order")
                return {"orderID": "ord-test-001"}

            mock_inst.place_limit_order.side_effect = spy_place

            _live_order(
                trade_id="trd-001",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-001",
            )

        assert "insert_command" in call_log, "insert_command must have been called"
        assert "place_limit_order" in call_log, "place_limit_order must have been called"
        assert call_log.index("insert_command") < call_log.index("place_limit_order"), (
            f"INV-30: insert_command must precede place_limit_order; call order was {call_log}"
        )

    def test_submit_unknown_writes_event_with_state_unknown(self, mem_conn):
        """Crash-injection drill: place_limit_order raises RuntimeError.

        The venue_commands row must reach state=UNKNOWN via a SUBMIT_UNKNOWN
        event. The SUBMITTING row (created pre-submit) is the recovery anchor.
        """
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import find_unresolved_commands, list_events

        intent = _make_entry_intent()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.side_effect = RuntimeError("simulated venue timeout")

            result = _live_order(
                trade_id="trd-002",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-002",
            )

        # The OrderResult reflects the unknown outcome
        assert result.status == "rejected", (
            f"Expected status=rejected for SUBMIT_UNKNOWN, got {result.status!r}"
        )
        assert result.reason is not None and "submit_unknown" in result.reason, (
            f"Expected reason to contain 'submit_unknown', got {result.reason!r}"
        )

        # The durable record must show state=UNKNOWN (recovery can resolve)
        unresolved = find_unresolved_commands(mem_conn)
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved command (UNKNOWN), found {len(unresolved)}: {unresolved}"
        )
        assert unresolved[0]["state"] == "UNKNOWN", (
            f"Expected state=UNKNOWN in journal, got {unresolved[0]['state']!r}"
        )

        # Check the event chain
        events = list_events(mem_conn, unresolved[0]["command_id"])
        event_types = [e["event_type"] for e in events]
        assert "INTENT_CREATED" in event_types
        assert "SUBMIT_REQUESTED" in event_types
        assert "SUBMIT_UNKNOWN" in event_types

    def test_submit_rejected_writes_event_with_state_rejected(self, mem_conn):
        """place_limit_order returns None -> state=REJECTED."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent()
        command_ids_seen: list[str] = []

        real_insert = None
        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.return_value = None

            result = _live_order(
                trade_id="trd-003",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-003",
            )

        assert result.status == "rejected"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED", (
            f"Expected state=REJECTED after None return, got {cmd['state']!r}"
        )

    def test_submit_acked_writes_event_with_state_acked(self, mem_conn):
        """place_limit_order returns orderID -> state=ACKED, venue_order_id set."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent()
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.return_value = {"orderID": "ord-acked-001"}

            result = _live_order(
                trade_id="trd-004",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-004",
            )

        assert result.status == "pending"  # OrderResult status is 'pending' until fill
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "ACKED", (
            f"Expected state=ACKED after successful ack, got {cmd['state']!r}"
        )

    def test_idempotency_key_collision_raises_before_submit(self, mem_conn):
        """Duplicate idempotency key: place_limit_order must NOT be called.

        Insert a command with a known idempotency_key first, then run a second
        _live_order with inputs that hash to the same key. The second call must
        return a rejected OrderResult without calling place_limit_order.
        """
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command

        intent = _make_entry_intent(token_id="tok-idem" + "0" * 33)

        # Pre-insert a command with the key that _live_order will derive
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-collision",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-existing-cmd",
            position_id="trd-pre",
            decision_id="dec-collision",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-005",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-collision",  # same decision_id => same key
            )

        assert result.status == "rejected", (
            f"Expected rejected on idempotency collision, got {result.status!r}"
        )
        assert result.reason is not None and "idempotency_collision" in result.reason, (
            f"Expected reason containing 'idempotency_collision', got {result.reason!r}"
        )
        # Most importantly: place_limit_order was never reached
        mock_inst.place_limit_order.assert_not_called()

    def test_v2_preflight_failure_writes_rejected_event(self, mem_conn):
        """V2 preflight raises V2PreflightError -> state=REJECTED, place_limit_order not called.

        The command is already persisted (SUBMITTING) when preflight runs.
        On preflight failure, a SUBMIT_REJECTED event must be appended so
        the row reaches a terminal state.
        """
        from src.execution.executor import _live_order
        from src.data.polymarket_client import V2PreflightError
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent()
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.side_effect = V2PreflightError("endpoint down")

            result = _live_order(
                trade_id="trd-006",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-006",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "v2_preflight_failed" in result.reason
        mock_inst.place_limit_order.assert_not_called()

        # Command row must be REJECTED (not stuck in SUBMITTING)
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED", (
            f"Expected state=REJECTED after v2_preflight failure, got {cmd['state']!r}"
        )

    def test_executionprice_validation_runs_before_persist(self, mem_conn):
        """NaN limit_price: ExecutionPrice rejects before any DB write.

        No venue_commands row should be inserted when the price is malformed.
        """
        from src.execution.executor import _live_order
        import math

        # Build an intent with NaN limit_price; bypass the constructor if needed
        # by constructing manually via dataclass replace equivalent
        from src.contracts.execution_intent import ExecutionIntent
        from src.contracts import Direction
        import dataclasses

        base_intent = _make_entry_intent()
        nan_intent = dataclasses.replace(base_intent, limit_price=float("nan"))

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            result = _live_order(
                trade_id="trd-007",
                intent=nan_intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-007",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "malformed_limit_price" in result.reason

        # No command row should have been persisted
        row_count = mem_conn.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        assert row_count == 0, (
            f"Expected no venue_commands rows after ExecutionPrice rejection, found {row_count}"
        )
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# TestExitOrderCommandSplit — exit path (execute_exit_order / IntentKind.EXIT)
# ---------------------------------------------------------------------------

class TestExitOrderCommandSplit:
    """INV-30: execute_exit_order must persist before it submits."""

    def test_exit_persist_precedes_submit(self, mem_conn):
        """insert_command must run before place_limit_order (exit path)."""
        from src.execution.executor import execute_exit_order

        call_log: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def spy_insert(*args, **kwargs):
            call_log.append("insert_command")
            return _real_insert(*args, **kwargs)

        intent = _make_exit_intent()

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=spy_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            def spy_place(**kwargs):
                call_log.append("place_limit_order")
                return {"orderID": "ord-exit-001"}

            mock_inst.place_limit_order.side_effect = spy_place

            execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-001",
            )

        assert "insert_command" in call_log, "insert_command must have been called"
        assert "place_limit_order" in call_log, "place_limit_order must have been called"
        assert call_log.index("insert_command") < call_log.index("place_limit_order"), (
            f"INV-30: insert_command must precede place_limit_order; call order was {call_log}"
        )

    def test_exit_submit_unknown_writes_event_with_state_unknown(self, mem_conn):
        """Crash-injection drill (exit path): place_limit_order raises.

        state must reach UNKNOWN via SUBMIT_UNKNOWN event.
        """
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import find_unresolved_commands, list_events

        intent = _make_exit_intent(trade_id="trd-exit-002")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.side_effect = RuntimeError("simulated exit crash")

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-002",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "submit_unknown" in result.reason

        unresolved = find_unresolved_commands(mem_conn)
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved command (UNKNOWN exit), found {len(unresolved)}"
        )
        assert unresolved[0]["state"] == "UNKNOWN"
        assert unresolved[0]["intent_kind"] == "EXIT"

        events = list_events(mem_conn, unresolved[0]["command_id"])
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_UNKNOWN" in event_types

    def test_exit_submit_rejected_writes_event_with_state_rejected(self, mem_conn):
        """place_limit_order returns None (exit path) -> state=REJECTED."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(trade_id="trd-exit-003")
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.return_value = None

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-003",
            )

        assert result.status == "rejected"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED"

    def test_exit_submit_acked_writes_event_with_state_acked(self, mem_conn):
        """place_limit_order returns orderID (exit path) -> state=ACKED."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(trade_id="trd-exit-004")
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.return_value = {"orderID": "ord-exit-acked-001"}

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-004",
            )

        assert result.status == "pending"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "ACKED"

    def test_exit_idempotency_key_collision_raises_before_submit(self, mem_conn):
        """Duplicate idempotency key (exit path): place_limit_order not called."""
        from src.execution.executor import execute_exit_order, create_exit_order_intent
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command
        from src.contracts.tick_size import TickSize

        token_id = "tok-exit-idem" + "0" * 27
        shares = 10.0
        current_price = 0.55

        # Derive the limit_price the same way execute_exit_order will
        tick = TickSize.for_market(token_id=token_id)
        limit_price = tick.clamp_to_valid_range(current_price - tick.value)
        effective_shares = __import__("math").floor(shares * 100 + 1e-9) / 100.0

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-exit-collision",
            token_id=token_id,
            side="SELL",
            price=limit_price,
            size=effective_shares,
            intent_kind=IntentKind.EXIT,
        )
        insert_command(
            mem_conn,
            command_id="pre-exit-cmd",
            position_id="trd-exit-pre",
            decision_id="dec-exit-collision",
            idempotency_key=idem.value,
            intent_kind="EXIT",
            market_id=token_id,
            token_id=token_id,
            side="SELL",
            size=effective_shares,
            price=limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        mem_conn.commit()

        intent = create_exit_order_intent(
            trade_id="trd-exit-005",
            token_id=token_id,
            shares=shares,
            current_price=current_price,
        )

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-collision",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "idempotency_collision" in result.reason
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency collision retry (MEDIUM-1) — both paths
# ---------------------------------------------------------------------------

class TestIdempotencyCollisionRetry:
    """Collision retry: second call with same key returns existing state, not raw exc."""

    def test_idempotency_collision_returns_existing_state_acked(self, mem_conn):
        """Insert command in ACKED state, attempt second insert with same key.

        OrderResult.status must be 'pending' and reason includes 'prior attempt acked'.
        """
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(token_id="tok-coll-acked" + "0" * 27)

        # Pre-insert a command and advance it to ACKED state
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-acked",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-acked",
            position_id="trd-pre-acked",
            decision_id="dec-coll-acked",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        # Advance to ACKED via SUBMIT_REQUESTED -> SUBMIT_ACKED
        append_event(
            mem_conn,
            command_id="pre-cmd-acked",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-acked",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"venue_order_id": "acked-ord-001"},
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-acked",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-acked",
            )

        assert result.status == "pending", (
            f"Expected pending for ACKED collision, got {result.status!r}"
        )
        assert result.reason is not None and "prior attempt acked" in result.reason, (
            f"Expected reason to contain 'prior attempt acked', got {result.reason!r}"
        )
        mock_inst.place_limit_order.assert_not_called()

    def test_idempotency_collision_with_filled_state_returns_pending(self, mem_conn):
        """Insert command in FILLED state; collision must return status=pending."""
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(token_id="tok-coll-filled" + "0" * 25)

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-filled",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-filled",
            position_id="trd-pre-filled",
            decision_id="dec-coll-filled",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"venue_order_id": "fill-ord-001"},
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:00:02+00:00",
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-filled",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-filled",
            )

        assert result.status == "pending", (
            f"Expected pending for FILLED collision, got {result.status!r}"
        )
        assert result.reason is not None and "prior attempt filled" in result.reason
        mock_inst.place_limit_order.assert_not_called()

    def test_idempotency_collision_with_rejected_state_returns_rejected(self, mem_conn):
        """Insert command in REJECTED state; collision must return status=rejected."""
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(token_id="tok-coll-rejected" + "0" * 23)

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-rejected",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-rejected",
            position_id="trd-pre-rejected",
            decision_id="dec-coll-rejected",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-rejected",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-rejected",
            event_type="SUBMIT_REJECTED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"reason": "v2_preflight_failed"},
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-rejected",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-rejected",
            )

        assert result.status == "rejected", (
            f"Expected rejected for REJECTED collision, got {result.status!r}"
        )
        assert result.reason is not None and "prior attempt" in result.reason
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# MAJOR-2 WARNING: synthetic decision_id emits warning
# ---------------------------------------------------------------------------

def test_synthetic_decision_id_emits_warning(mem_conn, caplog):
    """When decision_id is empty, executor emits WARNING about synthetic id."""
    from src.execution.executor import _live_order
    import logging

    intent = _make_entry_intent()

    with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.return_value = None
        mock_inst.place_limit_order.return_value = {"orderID": "ord-synth-001"}

        with patch("src.execution.executor.alert_trade", lambda **kw: None):
            with caplog.at_level(logging.WARNING, logger="src.execution.executor"):
                result = _live_order(
                    trade_id="trd-synth",
                    intent=intent,
                    shares=18.19,
                    conn=mem_conn,
                    decision_id="",  # empty => synthetic
                )

    assert result.status == "pending"
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    synth_warnings = [m for m in warning_messages if "synthetic decision_id" in m]
    assert len(synth_warnings) >= 1, (
        f"Expected WARNING about synthetic decision_id, got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# MEDIUM-3 payload shape: v2_preflight SUBMIT_REJECTED payload
# ---------------------------------------------------------------------------

def test_v2_preflight_payload_shape(mem_conn):
    """V2 preflight failure must write SUBMIT_REJECTED with payload {{reason: v2_preflight_failed}}."""
    from src.execution.executor import _live_order
    from src.data.polymarket_client import V2PreflightError
    from src.state.venue_command_repo import get_command, list_events

    intent = _make_entry_intent()
    command_ids_seen: list[str] = []

    import src.state.venue_command_repo as _repo
    _real_insert = _repo.insert_command

    def capturing_insert(*args, **kwargs):
        command_ids_seen.append(kwargs["command_id"])
        return _real_insert(*args, **kwargs)

    with patch(
        "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
    ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.side_effect = V2PreflightError("endpoint down")

        result = _live_order(
            trade_id="trd-v2pf-payload",
            intent=intent,
            shares=18.19,
            conn=mem_conn,
            decision_id="dec-v2pf-payload",
        )

    assert result.status == "rejected"
    assert len(command_ids_seen) == 1

    events = list_events(mem_conn, command_ids_seen[0])
    rejected_events = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"]
    assert len(rejected_events) == 1

    import json
    payload = rejected_events[0].get("payload_json") or "{}"
    payload_dict = json.loads(payload)
    assert payload_dict.get("reason") == "v2_preflight_failed", (
        f"Expected payload {{\"reason\": \"v2_preflight_failed\"}}, got {payload_dict}"
    )


# ---------------------------------------------------------------------------
# INV-30 manifest test — all enforced_by.tests entries must be collect-able
# ---------------------------------------------------------------------------

def test_inv30_manifest_registered():
    """INV-30 must be in architecture/invariants.yaml with non-empty enforced_by.tests."""
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "architecture/invariants.yaml").read_text())
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-30" in by_id, "INV-30 missing from architecture/invariants.yaml"
    enforced_tests = (by_id["INV-30"].get("enforced_by") or {}).get("tests") or []
    assert len(enforced_tests) >= 5, (
        f"INV-30 must cite at least 5 enforcing tests, found {len(enforced_tests)}"
    )
