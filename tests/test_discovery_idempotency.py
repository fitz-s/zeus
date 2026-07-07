# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md u00a7P1.S5
"""P1.S5 relationship tests: discovery integration + idempotency lookup.

INV-32: materialize_position fires ONLY after command reaches ACKED/PARTIAL/FILLED.
NC-19: discovery phase checks idempotency key BEFORE submitting; skips if existing.

Each test names the invariant relationship it locks.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn():
    """In-memory DB with full schema including venue_commands."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def allow_cutover_for_idempotency_unit_tests(monkeypatch):
    """These tests isolate idempotency/materialization, not cutover state."""
    monkeypatch.setattr("src.execution.executor._assert_cutover_allows_submit", lambda *a, **kw: None)
    monkeypatch.setattr("src.execution.executor._assert_risk_allocator_allows_submit", lambda *a, **kw: None)
    monkeypatch.setattr("src.execution.executor._assert_heartbeat_allows_submit", lambda *a, **kw: None)
    monkeypatch.setattr("src.execution.executor._assert_ws_gap_allows_submit", lambda *a, **kw: None)
    monkeypatch.setattr("src.execution.executor._assert_collateral_allows_buy", lambda *a, **kw: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *a, **kw: None)
    monkeypatch.setattr("src.execution.executor._select_risk_allocator_order_type", lambda *a, **kw: "GTC")
    monkeypatch.setattr("src.state.venue_command_repo._assert_snapshot_gate", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.venue_command_repo._assert_envelope_gate", lambda *a, **kw: "env-unit-test")


def _make_entry_intent(limit_price: float = 0.55, token_id: str = "tok-" + "0" * 36) -> object:
    """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts.slippage_bps import SlippageBps
    from src.contracts import Direction

    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(200.0, "adverse"),
        is_sandbox=False,
        market_id="mkt-test-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
    )


# ---------------------------------------------------------------------------
# NC-19: pre-submit idempotency lookup prevents double-place
# ---------------------------------------------------------------------------

class TestPreSubmitIdempotencyLookup:
    """NC-19: execute_intent checks idempotency key BEFORE submitting."""

    def test_idempotency_key_skips_duplicate_submit(self, mem_conn):
        """NC-19: Second call with identical inputs returns OrderResult from first
        command's ACKED state and calls place_limit_order exactly ONCE.

        Relationship locked: NC-19 fast-path gate prevents double-placement on retries.
        """
        from src.execution.executor import execute_intent
        from src.execution.command_bus import CommandState

        intent = _make_entry_intent()
        place_calls = []

        def _mock_place(**kwargs):
            place_calls.append(kwargs)
            return {"orderID": "ord-abc", "status": "placed"}

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            instance = MockClient.return_value
            instance.v2_preflight.return_value = None
            instance.place_limit_order.side_effect = _mock_place

            # First call: should insert command + submit
            r1 = execute_intent(intent, 0.55, "bin-label", conn=mem_conn, decision_id="dec-aaa")

        assert r1.status == "pending", f"Expected pending, got {r1.status}: {r1.reason}"
        assert r1.command_state == "ACKED", f"Expected ACKED, got {r1.command_state}"
        assert len(place_calls) == 1

        # Second call: same inputs, same decision_id -> pre-submit lookup hits existing row
        with patch("src.data.polymarket_client.PolymarketClient") as MockClient2:
            instance2 = MockClient2.return_value
            instance2.v2_preflight.return_value = None
            instance2.place_limit_order.side_effect = _mock_place

            r2 = execute_intent(intent, 0.55, "bin-label", conn=mem_conn, decision_id="dec-aaa")

        # No new calls were made
        assert len(place_calls) == 1, f"place_limit_order called {len(place_calls)} times, expected 1"
        # Result reflects prior attempt's ACKED state
        assert r2.status == "pending"
        assert "idempotency_collision" in (r2.reason or "")
        assert r2.command_state == "ACKED"

    def test_retry_after_unknown_does_not_double_place(self, mem_conn):
        """NC-19: First call returns SUBMIT_UNKNOWN (SDK raises). Second call with
        same inputs sees existing UNKNOWN/SUBMITTING row and returns rejected with
        idempotency_collision reason. place_limit_order called exactly ONCE total.

        Relationship: recovery loop should handle UNKNOWN, not a second submit.
        """
        from src.execution.executor import execute_intent

        intent = _make_entry_intent()
        place_calls = []

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            instance = MockClient.return_value
            instance.v2_preflight.return_value = None
            instance.place_limit_order.side_effect = RuntimeError("Network timeout")

            r1 = execute_intent(intent, 0.55, "bin-label", conn=mem_conn, decision_id="dec-bbb")

        assert r1.status == "unknown_side_effect"
        assert "submit_unknown" in (r1.reason or "")

        # Second call: same inputs -> pre-submit lookup finds UNKNOWN/SUBMITTING row
        with patch("src.data.polymarket_client.PolymarketClient") as MockClient2:
            instance2 = MockClient2.return_value
            instance2.v2_preflight.return_value = None
            instance2.place_limit_order.side_effect = RuntimeError("Should not be called")

            r2 = execute_intent(intent, 0.55, "bin-label", conn=mem_conn, decision_id="dec-bbb")

        # place_limit_order must NOT have been called a second time
        instance2.place_limit_order.assert_not_called()
        assert r2.status == "unknown_side_effect"
        assert "idempotency_collision" in (r2.reason or "") or "submit_unknown" in (r2.reason or "")
        # SUBMITTING or UNKNOWN state set
        assert r2.command_state in ("SUBMITTING", "UNKNOWN", "SUBMIT_UNKNOWN_SIDE_EFFECT"), f"Got command_state={r2.command_state!r}"


# ---------------------------------------------------------------------------
# Warning behavior
# ---------------------------------------------------------------------------

class TestWarningSurfaces:
    """P1.S5: diagnostic surface tests."""

    def test_synthetic_decision_id_still_uses_warning(self, mem_conn):
        """Empty decision_id passed to execute_intent should emit a WARNING log.

        Relationship: empty decision_id signals retry-idempotency is not guaranteed;
        callers should always pass a real upstream ID.
        """
        from src.execution.executor import execute_intent
        import logging

        intent = _make_entry_intent()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            instance = MockClient.return_value
            instance.v2_preflight.return_value = None
            instance.place_limit_order.return_value = {"orderID": "ord-warn", "status": "placed"}

            with patch("src.execution.executor.logger") as mock_logger:
                r = execute_intent(
                    intent,
                    0.55,
                    "bin-label",
                    conn=mem_conn,
                    decision_id="",  # explicitly empty
                )
                # Assert WARNING was emitted for synthetic decision_id
                assert mock_logger.warning.called, "Expected logger.warning for synthetic decision_id"
                warning_args = str(mock_logger.warning.call_args_list)
                assert "synthetic decision_id" in warning_args or "retry-idempotency" in warning_args
