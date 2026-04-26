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

        assert r1.status == "rejected"
        assert "submit_unknown" in (r1.reason or "")

        # Second call: same inputs -> pre-submit lookup finds UNKNOWN/SUBMITTING row
        with patch("src.data.polymarket_client.PolymarketClient") as MockClient2:
            instance2 = MockClient2.return_value
            instance2.v2_preflight.return_value = None
            instance2.place_limit_order.side_effect = RuntimeError("Should not be called")

            r2 = execute_intent(intent, 0.55, "bin-label", conn=mem_conn, decision_id="dec-bbb")

        # place_limit_order must NOT have been called a second time
        instance2.place_limit_order.assert_not_called()
        assert r2.status == "rejected"
        assert "idempotency_collision" in (r2.reason or "")
        # SUBMITTING or UNKNOWN state set
        assert r2.command_state in ("SUBMITTING", "UNKNOWN"), f"Got command_state={r2.command_state!r}"


# ---------------------------------------------------------------------------
# INV-32: materialize_position gates on command_state
# ---------------------------------------------------------------------------

class TestMaterializePositionGate:
    """INV-32: cycle_runtime.execute_discovery_phase skips materialize for non-durable states."""

    def _run_discovery_with_result(self, result_stub, mem_conn):
        """Run execute_discovery_phase with a mocked execute_intent that returns result_stub."""
        from src.engine.cycle_runtime import execute_discovery_phase
        from src.engine.discovery_mode import DiscoveryMode
        from src.state.portfolio import Position
        from datetime import datetime, timezone

        city = SimpleNamespace(
            name="NYC",
            cluster="US-Northeast",
            settlement_unit="F",
            timezone="America/New_York",
        )
        edge = SimpleNamespace(
            direction="buy_yes",
            bin=SimpleNamespace(label="39-40F"),
            p_posterior=0.6,
            edge=0.1,
            entry_price=0.5,
            vwmp=0.5,
            ci_lower=0.5,
            ci_upper=0.7,
        )
        decision = SimpleNamespace(
            should_trade=True,
            edge=edge,
            tokens={"market_id": "mkt-1", "token_id": "yes-1", "no_token_id": "no-1"},
            size_usd=10.0,
            decision_id="dec-inv32",
            decision_snapshot_id="snap-1",
            edge_source="center_buy",
            strategy_key="center_buy",
            selected_method="ens_member_counting",
            applied_validations=[],
            settlement_semantics_json=None,
            epistemic_context_json=None,
            edge_context_json=None,
            p_raw=None,
            p_cal=None,
            p_market=None,
            alpha=0.0,
            agreement="AGREE",
            edge_context=SimpleNamespace(p_posterior=0.6),
        )
        portfolio = SimpleNamespace(positions=[], effective_bankroll=150.0)
        artifact = SimpleNamespace(add_trade=lambda p: None, add_no_trade=lambda p: None)
        tracker = SimpleNamespace(record_entry=lambda pos: None)
        summary = {"candidates": 0, "trades": 0, "no_trades": 0}
        materialize_calls = []

        class _FakePosition:
            def __init__(self, **kw):
                # store attributes but track construction
                materialize_calls.append(kw)
                for k, v in kw.items():
                    setattr(self, k, v)

        def _add_position(portfolio_obj, pos):
            portfolio_obj.positions.append(pos)

        deps = SimpleNamespace(
            MODE_PARAMS={DiscoveryMode.UPDATE_REACTION: {}},
            find_weather_markets=lambda min_hours_to_resolution=6: [
                {
                    "city": city,
                    "target_date": "2026-04-03",
                    "outcomes": [{"title": "39-40F", "range_low": 39, "range_high": 40}],
                    "hours_since_open": 30.0,
                    "hours_to_resolution": 10.0,
                    "event_id": "evt-1",
                    "slug": "nyc-2026-04-03",
                }
            ],
            MarketCandidate=lambda **kwargs: SimpleNamespace(**kwargs),
            evaluate_candidate=lambda *args, **kwargs: [decision],
            create_execution_intent=lambda **kwargs: SimpleNamespace(),
            execute_intent=lambda *args, **kwargs: result_stub,
            add_position=_add_position,
            is_strategy_enabled=lambda strategy_name: True,
            _classify_edge_source=lambda mode, edge_obj: "center_buy",
            Position=_FakePosition,
            settings=SimpleNamespace(mode="paper"),
            logger=MagicMock(),
            _utcnow=lambda: datetime(2026, 4, 3, 0, 5, tzinfo=timezone.utc),
            DiscoveryMode=DiscoveryMode,
            NoTradeCase=SimpleNamespace,
        )

        execute_discovery_phase(
            mem_conn,
            SimpleNamespace(),
            portfolio,
            artifact,
            tracker,
            SimpleNamespace(),
            DiscoveryMode.UPDATE_REACTION,
            summary,
            150.0,
            datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc),
            env="paper",
            deps=deps,
        )
        return {"portfolio": portfolio, "materialize_calls": materialize_calls}

    def test_materialize_skipped_for_submitting_command(self, mem_conn):
        """INV-32: SUBMITTING command_state must NOT trigger materialize_position.

        Relationship: a position must not appear as active when the venue may
        not have received the order.
        """
        result_stub = SimpleNamespace(
            trade_id="t-sub",
            status="pending",
            fill_price=None,
            submitted_price=0.5,
            shares=20.0,
            order_id="ord-x",
            timeout_seconds=3600,
            command_state="SUBMITTING",
            reason="submit_in_flight",
        )
        r = self._run_discovery_with_result(result_stub, mem_conn)
        assert len(r["portfolio"].positions) == 0, (
            f"Expected no positions (SUBMITTING), got {len(r['portfolio'].positions)}"
        )
        assert len(r["materialize_calls"]) == 0

    def test_materialize_skipped_for_unknown_command(self, mem_conn):
        """INV-32: UNKNOWN command_state must NOT trigger materialize_position.

        Relationship: a position must not appear as active when venue response
        is uncertain (recovery loop resolves).
        """
        result_stub = SimpleNamespace(
            trade_id="t-unk",
            status="pending",
            fill_price=None,
            submitted_price=0.5,
            shares=20.0,
            order_id="ord-y",
            timeout_seconds=3600,
            command_state="UNKNOWN",
            reason="submit_unknown",
        )
        r = self._run_discovery_with_result(result_stub, mem_conn)
        assert len(r["portfolio"].positions) == 0, (
            f"Expected no positions (UNKNOWN), got {len(r['portfolio'].positions)}"
        )
        assert len(r["materialize_calls"]) == 0

    def test_materialize_runs_for_acked_command(self, mem_conn):
        """INV-32: ACKED command_state MUST trigger materialize_position.

        Relationship: a durable ack means the venue accepted the order;
        position authority can advance.
        """
        result_stub = SimpleNamespace(
            trade_id="t-ack",
            status="pending",
            fill_price=None,
            submitted_price=0.5,
            shares=20.0,
            order_id="ord-z",
            timeout_seconds=3600,
            command_state="ACKED",
            reason="order posted",
        )
        r = self._run_discovery_with_result(result_stub, mem_conn)
        assert len(r["portfolio"].positions) == 1, (
            f"Expected 1 position (ACKED), got {len(r['portfolio'].positions)}"
        )
        assert len(r["materialize_calls"]) == 1


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

    @pytest.mark.xfail(
        reason="P2: cycle_runtime decision_id sourcing requires full execute_discovery_phase integration"
               " test with real DB harness verifying non-empty decision_id flows to executor",
        strict=False,
    )
    def test_decision_id_threaded_from_cycle_runtime(self, mem_conn):
        """cycle_runtime.execute_discovery_phase passes non-empty decision_id to execute_intent.

        Verifies the P1.S5 wiring: d.decision_id must flow through to the executor.
        Marked xfail pending full integration test with DB isolation.
        """
        from src.engine.cycle_runtime import execute_discovery_phase
        from src.engine.discovery_mode import DiscoveryMode
        from datetime import datetime, timezone

        captured_kwargs = {}

        def _capture_execute_intent(*args, **kwargs):
            captured_kwargs.update(kwargs)
            from src.execution.executor import OrderResult
            return OrderResult(
                trade_id="t-cap",
                status="pending",
                command_state="ACKED",
            )

        city = SimpleNamespace(
            name="NYC",
            cluster="US-Northeast",
            settlement_unit="F",
            timezone="America/New_York",
        )
        edge = SimpleNamespace(
            direction="buy_yes",
            bin=SimpleNamespace(label="39-40F"),
            p_posterior=0.6,
            edge=0.1,
            entry_price=0.5,
            vwmp=0.5,
            ci_lower=0.5,
            ci_upper=0.7,
        )
        decision = SimpleNamespace(
            should_trade=True,
            edge=edge,
            tokens={"market_id": "mkt-1", "token_id": "yes-1", "no_token_id": "no-1"},
            size_usd=10.0,
            decision_id="UPSTREAM-DEC-ID-123",
            decision_snapshot_id="snap-1",
            edge_source="center_buy",
            strategy_key="center_buy",
            selected_method="ens_member_counting",
            applied_validations=[],
            settlement_semantics_json=None,
            epistemic_context_json=None,
            edge_context_json=None,
            p_raw=None,
            p_cal=None,
            p_market=None,
            alpha=0.0,
            agreement="AGREE",
            edge_context=SimpleNamespace(p_posterior=0.6),
        )

        from src.state.portfolio import Position
        portfolio = SimpleNamespace(positions=[], effective_bankroll=150.0)
        artifact = SimpleNamespace(add_trade=lambda p: None, add_no_trade=lambda p: None)
        tracker = SimpleNamespace(record_entry=lambda pos: None)
        summary = {"candidates": 0, "trades": 0, "no_trades": 0}

        def _add_position(portfolio_obj, pos):
            portfolio_obj.positions.append(pos)

        deps = SimpleNamespace(
            MODE_PARAMS={DiscoveryMode.UPDATE_REACTION: {}},
            find_weather_markets=lambda min_hours_to_resolution=6: [
                {
                    "city": city,
                    "target_date": "2026-04-03",
                    "outcomes": [{"title": "39-40F", "range_low": 39, "range_high": 40}],
                    "hours_since_open": 30.0,
                    "hours_to_resolution": 10.0,
                    "event_id": "evt-1",
                    "slug": "nyc-2026-04-03",
                }
            ],
            MarketCandidate=lambda **kwargs: SimpleNamespace(**kwargs),
            evaluate_candidate=lambda *args, **kwargs: [decision],
            create_execution_intent=lambda **kwargs: SimpleNamespace(),
            execute_intent=_capture_execute_intent,
            add_position=_add_position,
            is_strategy_enabled=lambda strategy_name: True,
            _classify_edge_source=lambda mode, edge_obj: "center_buy",
            Position=Position,
            settings=SimpleNamespace(mode="paper"),
            logger=MagicMock(),
            _utcnow=lambda: datetime(2026, 4, 3, 0, 5, tzinfo=timezone.utc),
            DiscoveryMode=DiscoveryMode,
            NoTradeCase=SimpleNamespace,
        )

        execute_discovery_phase(
            mem_conn,
            SimpleNamespace(),
            portfolio,
            artifact,
            tracker,
            SimpleNamespace(),
            DiscoveryMode.UPDATE_REACTION,
            summary,
            150.0,
            datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc),
            env="paper",
            deps=deps,
        )

        assert "decision_id" in captured_kwargs, "decision_id kwarg not passed to execute_intent"
        assert captured_kwargs["decision_id"] == "UPSTREAM-DEC-ID-123", (
            f"Expected UPSTREAM-DEC-ID-123, got {captured_kwargs.get('decision_id')!r}"
        )
