# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Regression coverage for executor and portfolio mechanics under R3 cutover preflight opt-outs.
# Reuse: Run when executor order submission or portfolio save/load mechanics change.
# Created: 2026-04-27
# Last reused/audited: 2026-04-30
# Authority basis: R3 Z1 cutover guard audit; pre-existing executor behavior tests updated to opt out of CutoverGuard so they keep testing executor mechanics.
"""Tests for executor and portfolio."""

import inspect
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.execution.executor import (
    OrderResult,
    create_execution_intent,
    create_exit_order_intent,
    execute_final_intent,
    execute_exit_order,
    execute_intent,
)
from src.contracts import (
    DecisionSourceContext,
    Direction,
    EdgeContext,
    EntryMethod,
    FinalExecutionIntent,
)
import numpy as np
from src.config import settings
from src.state.portfolio import (
    Position, PortfolioState, load_portfolio, save_portfolio,
    add_position, remove_position,
)
from src.types import Bin, BinEdge

_TEST_CONN = None
_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _mem_conn(monkeypatch):
    """Inject an in-memory DB into executor fallback connection.

    execute_exit_order and _live_order now call get_trade_connection_with_world()
    when no explicit conn is provided. Supply an in-memory DB with schema so
    unit tests don't depend on on-disk DB state.
    """
    from src.state.db import init_schema

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys=ON")
    init_schema(mem)
    global _TEST_CONN
    _TEST_CONN = mem
    monkeypatch.setattr("src.execution.executor.get_trade_connection_with_world", lambda: mem)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)
    yield mem
    _TEST_CONN = None
    mem.close()


def _snapshot_kwargs(token_id: str) -> dict:
    snapshot_id = _ensure_snapshot(_TEST_CONN, token_id=token_id)
    return {
        "executable_snapshot_id": snapshot_id,
        "executable_snapshot_min_tick_size": Decimal("0.01"),
        "executable_snapshot_min_order_size": Decimal("0.01"),
        "executable_snapshot_neg_risk": False,
    }


def _ensure_snapshot(conn, *, token_id: str) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    assert conn is not None
    snapshot_id = f"snap-{token_id}"
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
            fee_details={
                "source": "test",
                "token_id": token_id,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
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


def _final_submit_result(bound_envelope, *, order_id: str | None, status: str = "OPEN") -> dict:
    if bound_envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")
    raw_response = {"status": status}
    if order_id is not None:
        raw_response["orderID"] = order_id
    final = bound_envelope.with_updates(
        raw_response_json=json.dumps(raw_response, sort_keys=True, separators=(",", ":")),
        order_id=order_id,
    )
    result = {
        "status": status,
        "_venue_submission_envelope": final.to_dict(),
    }
    if order_id is not None:
        result["orderID"] = order_id
    return result


def _decision_source_context() -> DecisionSourceContext:
    return DecisionSourceContext(
        source_id="tigge",
        model_family="ecmwf_ifs025",
        forecast_issue_time="2026-04-27T09:00:00+00:00",
        forecast_valid_time="2026-04-27T18:00:00+00:00",
        forecast_fetch_time="2026-04-27T10:00:00+00:00",
        forecast_available_at="2026-04-27T09:30:00+00:00",
        raw_payload_hash="f" * 64,
        degradation_level="OK",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time="2026-04-27T12:00:00+00:00",
        decision_time_status="OK",
    )


def _final_execution_intent(**overrides) -> FinalExecutionIntent:
    payload = dict(
        hypothesis_id="hypothesis:finaltest01",
        selected_token_id="no-token-final",
        direction="buy_no",
        size_kind="shares",
        size_value=Decimal("10"),
        final_limit_price=Decimal("0.49"),
        expected_fill_price_before_fee=Decimal("0.49"),
        fee_adjusted_execution_price=Decimal("0.49"),
        order_policy="limit_may_take_conservative",
        order_type="GTC",
        post_only=False,
        cancel_after=None,
        snapshot_id="snap-final",
        snapshot_hash="a" * 64,
        cost_basis_id="cost_basis:" + ("d" * 16),
        cost_basis_hash="d" * 64,
        max_slippage_bps=Decimal("200"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_rate=Decimal("0"),
        neg_risk=False,
    )
    payload.update(overrides)
    return FinalExecutionIntent(**payload)


class TestPortfolio:
    def test_empty_portfolio(self):
        state = PortfolioState()
        assert len(state.positions) == 0

    def test_add_and_remove_position(self):
        state = PortfolioState(bankroll=100.0)
        pos = Position(
            trade_id="t1", market_id="m1", city="NYC",
            cluster="US-Northeast", target_date="2026-01-15",
            bin_label="39-40", direction="buy_yes",
            size_usd=10.0, entry_price=0.40, p_posterior=0.60,
            edge=0.20, entered_at="2026-01-12T00:00:00Z",
        )
        add_position(state, pos)
        assert len(state.positions) == 1

        removed = remove_position(state, "t1")
        assert removed is not None
        assert removed.trade_id == "t1"
        assert len(state.positions) == 0

    def test_remove_nonexistent(self):
        state = PortfolioState()
        assert remove_position(state, "nonexistent") is None

    def test_save_load_roundtrip(self, tmp_path):
        from src.state.db import get_connection, init_schema

        path = tmp_path / "positions.json"
        state = PortfolioState(bankroll=200.0)
        add_position(state, Position(
            trade_id="t1", market_id="m1", city="NYC",
            cluster="US-Northeast", target_date="2026-01-15",
            bin_label="39-40", direction="buy_yes",
            size_usd=15.0, entry_price=0.40, p_posterior=0.60,
            edge=0.20, entered_at="2026-01-12T00:00:00Z",
        ))

        save_portfolio(state, path)

        # P4: load_portfolio reads from canonical DB first.
        # Seed zeus.db (fallback path) with the same position so roundtrip works.
        db = get_connection(tmp_path / "zeus.db")
        init_schema(db)
        db.execute(
            """
            INSERT INTO position_current
            (position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
             direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
             entry_method, strategy_key, edge_source, discovery_mode, chain_state,
             order_id, order_status, updated_at)
            VALUES ('t1','active','t1','m1','NYC','US-Northeast','2026-01-15','39-40',
                    'buy_yes','F',15.0,0.0,0.0,0.40,0.60,'ens_member_counting','center_buy',
                    'center_buy','opening_hunt','unknown','','filled','2026-01-12T00:00:00Z')
            """
        )
        db.commit()
        db.close()

        loaded = load_portfolio(path)

        assert loaded.bankroll == pytest.approx(settings.capital_base_usd)
        assert len(loaded.positions) == 1
        assert loaded.positions[0].trade_id == "t1"
        assert loaded.positions[0].city == "NYC"


class TestExecutor:
    def test_execute_final_intent_bridges_immutable_submit_shape(self, monkeypatch):
        captured = {}

        def fake_live_order(trade_id, intent, shares, conn=None, decision_id=""):
            captured.update(
                trade_id=trade_id,
                intent=intent,
                shares=shares,
                conn=conn,
                decision_id=decision_id,
            )
            return OrderResult(
                trade_id=trade_id,
                status="pending",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )

        monkeypatch.setattr("src.execution.executor._live_order", fake_live_order)
        source_context = _decision_source_context()
        final_intent = _final_execution_intent()

        result = execute_final_intent(
            final_intent,
            market_id="condition-final",
            mode="opening_hunt",
            decision_source_context=source_context,
            conn=_TEST_CONN,
            decision_id="decision-final",
            trade_id="trade-final",
            event_id="event-final",
            resolution_window="2026-04-30",
            correlation_key="corr-final",
            decision_edge=0.07,
        )

        assert result.status == "pending"
        assert captured["trade_id"] == "trade-final"
        assert captured["shares"] == pytest.approx(10.0)
        assert captured["conn"] is _TEST_CONN
        assert captured["decision_id"] == "decision-final"
        legacy_intent = captured["intent"]
        assert legacy_intent.direction == Direction.NO
        assert legacy_intent.token_id == "no-token-final"
        assert legacy_intent.limit_price == pytest.approx(0.49)
        assert legacy_intent.target_size_usd == pytest.approx(4.90)
        assert legacy_intent.executable_snapshot_id == "snap-final"
        assert legacy_intent.executable_snapshot_min_tick_size == Decimal("0.01")
        assert legacy_intent.executable_snapshot_min_order_size == Decimal("0.01")
        assert legacy_intent.executable_snapshot_neg_risk is False
        assert legacy_intent.decision_source_context == source_context
        assert legacy_intent.market_id == "condition-final"
        assert legacy_intent.event_id == "event-final"
        assert legacy_intent.resolution_window == "2026-04-30"
        assert legacy_intent.correlation_key == "corr-final"
        assert legacy_intent.order_type == "GTC"
        assert legacy_intent.post_only is False

    def test_execute_final_intent_rejects_missing_source_context_before_live_order(
        self,
        monkeypatch,
    ):
        called = False

        def fake_live_order(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("_live_order must not run without source context")

        monkeypatch.setattr("src.execution.executor._live_order", fake_live_order)

        result = execute_final_intent(
            _final_execution_intent(),
            market_id="condition-final",
            mode="opening_hunt",
            decision_source_context=None,
            trade_id="trade-final",
        )

        assert result.status == "rejected"
        assert result.reason == "decision_source_integrity:missing_decision_source_context"
        assert called is False

    def test_execute_final_intent_rejects_post_only_before_live_order(self, monkeypatch):
        called = False

        def fake_live_order(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("_live_order must not run for unsupported post-only")

        monkeypatch.setattr("src.execution.executor._live_order", fake_live_order)

        result = execute_final_intent(
            _final_execution_intent(
                order_policy="post_only_passive_limit",
                post_only=True,
            ),
            market_id="condition-final",
            mode="opening_hunt",
            decision_source_context=_decision_source_context(),
            trade_id="trade-final",
        )

        assert result.status == "rejected"
        assert result.reason == "post_only_entry_submit_not_supported"
        assert called is False

    def test_execute_final_intent_exposes_no_recompute_inputs(self):
        from src.execution import executor as executor_module

        signature = inspect.signature(executor_module.execute_final_intent)
        forbidden_parameters = {
            "edge",
            "edge_context",
            "edge_vwmp",
            "label",
            "p_posterior",
            "vwmp",
            "bin_edge",
        }
        assert forbidden_parameters.isdisjoint(signature.parameters)
        source = inspect.getsource(executor_module.execute_final_intent)
        assert "create_execution_intent(" not in source
        assert "compute_native_limit_price(" not in source

    def test_create_execution_intent_routes_buy_no_to_no_token_id(self):
        edge = BinEdge(
            bin=Bin(low=None, high=67, label="67°F or lower", unit="F"),
            direction="buy_no",
            edge=0.22,
            ci_lower=0.03,
            ci_upper=0.31,
            p_model=0.70,
            p_market=0.40,
            p_posterior=0.62,
            entry_price=0.40,
            p_value=0.01,
            vwmp=0.40,
            forward_edge=0.22,
        )
        edge_context = EdgeContext(
            p_raw=np.array([0.30, 0.70]),
            p_cal=np.array([0.30, 0.70]),
            p_market=np.array([0.60, 0.40]),
            p_posterior=0.62,
            forward_edge=0.22,
            alpha=1.0,
            confidence_band_upper=0.31,
            confidence_band_lower=0.03,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="test-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

        intent = create_execution_intent(
            edge_context=edge_context,
            edge=edge,
            size_usd=5.0,
            mode="opening_hunt",
            market_id="m1",
            token_id="yes-token",
            no_token_id="no-token",
            best_ask=0.42,
            executable_snapshot_id="snap-no-token",
            executable_snapshot_min_tick_size=Decimal("0.01"),
            executable_snapshot_min_order_size=Decimal("0.01"),
            executable_snapshot_neg_risk=False,
        )

        assert intent.direction.value == "buy_no"
        assert intent.token_id == "no-token"
        assert intent.executable_snapshot_id == "snap-no-token"

    def test_create_execution_intent_honors_repriced_limit_contract(self):
        edge = BinEdge(
            bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
            direction="buy_yes",
            edge=0.22,
            ci_lower=0.03,
            ci_upper=0.31,
            p_model=0.70,
            p_market=0.25,
            p_posterior=0.47,
            entry_price=0.25,
            p_value=0.01,
            vwmp=0.25,
            forward_edge=0.22,
        )
        edge_context = EdgeContext(
            p_raw=np.array([0.50]),
            p_cal=np.array([0.50]),
            p_market=np.array([0.25]),
            p_posterior=0.47,
            forward_edge=0.22,
            alpha=1.0,
            confidence_band_upper=0.31,
            confidence_band_lower=0.03,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="test-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

        intent = create_execution_intent(
            edge_context=edge_context,
            edge=edge,
            size_usd=5.0,
            mode="opening_hunt",
            market_id="m1",
            token_id="yes-token",
            no_token_id="no-token",
            best_ask=0.234,
            repriced_limit_price=0.234,
            executable_snapshot_id="snap-limit",
            executable_snapshot_min_tick_size=Decimal("0.001"),
            executable_snapshot_min_order_size=Decimal("0.01"),
            executable_snapshot_neg_risk=False,
        )

        assert intent.limit_price == pytest.approx(0.234)

    def test_create_execution_intent_rejects_reprice_above_slippage_budget(self):
        edge = BinEdge(
            bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
            direction="buy_yes",
            edge=0.22,
            ci_lower=0.03,
            ci_upper=0.31,
            p_model=0.70,
            p_market=0.25,
            p_posterior=0.47,
            entry_price=0.25,
            p_value=0.01,
            vwmp=0.25,
            forward_edge=0.22,
        )
        edge_context = EdgeContext(
            p_raw=np.array([0.50]),
            p_cal=np.array([0.50]),
            p_market=np.array([0.25]),
            p_posterior=0.47,
            forward_edge=0.22,
            alpha=1.0,
            confidence_band_upper=0.31,
            confidence_band_lower=0.03,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="test-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

        with pytest.raises(ValueError, match="MAX_SLIPPAGE_EXCEEDED"):
            create_execution_intent(
                edge_context=edge_context,
                edge=edge,
                size_usd=5.0,
                mode="opening_hunt",
                market_id="m1",
                token_id="yes-token",
                no_token_id="no-token",
                best_ask=0.30,
                repriced_limit_price=0.30,
                executable_snapshot_id="snap-limit",
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            )

    @pytest.mark.skip(reason="Phase2: paper mode removed")
    def test_paper_fill(self):
        edge = BinEdge(
            bin=Bin(low=39, high=40, label="39-40", unit="F"),
            direction="buy_yes", edge=0.10,
            ci_lower=0.03, ci_upper=0.17,
            p_model=0.50, p_market=0.40, p_posterior=0.50,
            entry_price=0.40, p_value=0.02, vwmp=0.42,
        )
        edge_context = EdgeContext(
            p_raw=np.array([0.50]),
            p_cal=np.array([0.50]),
            p_market=np.array([0.40]),
            p_posterior=0.50,
            forward_edge=0.10,
            alpha=0.65,
            confidence_band_upper=0.17,
            confidence_band_lower=0.03,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="test-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )
        intent = create_execution_intent(
            edge_context=edge_context,
            edge=edge,
            size_usd=5.0,
            mode="opening_hunt",
            market_id="m1",
            token_id="yes-token",
            no_token_id="no-token",
            **_snapshot_kwargs("yes-token"),
        )
        result = execute_intent(intent, edge.vwmp, edge.bin.label)

        assert result.status == "filled"
        assert result.fill_price is not None
        assert 0.01 <= result.fill_price <= 0.99
        assert result.trade_id is not None

    def test_create_exit_order_intent_carries_boundary_fields(self):
        intent = create_exit_order_intent(
            trade_id="trade-1",
            token_id="yes-token",
            shares=12.345,
            current_price=0.46,
            best_bid=0.45,
        )

        assert intent.trade_id == "trade-1"
        assert intent.token_id == "yes-token"
        assert intent.shares == pytest.approx(12.345)
        assert intent.current_price == pytest.approx(0.46)
        assert intent.best_bid == pytest.approx(0.45)
        assert intent.intent_id == "trade-1:exit"

    def test_execute_exit_order_places_sell_and_rounds_down(self, monkeypatch):
        captured = {}

        class DummyClient:
            def __init__(self):
                self.bound_envelope = None

            def bind_submission_envelope(self, envelope):
                self.bound_envelope = envelope

            def place_limit_order(self, *, token_id, price, size, side, order_type="GTC"):
                captured.update(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side,
                    order_type=order_type,
                )
                return _final_submit_result(self.bound_envelope, order_id="sell-1")

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummyClient)

        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-1",
                token_id="yes-token",
                shares=12.349,
                current_price=0.50,
                best_bid=0.49,
                **_snapshot_kwargs("yes-token"),
            )
        )

        assert result.status == "pending"
        assert result.order_role == "exit"
        assert result.order_id == "sell-1"
        assert captured == {
            "token_id": "yes-token",
            "price": pytest.approx(0.49),
            "size": pytest.approx(12.34),
            "side": "SELL",
            "order_type": "GTC",
        }

    def test_execute_exit_order_rejects_missing_order_id_response(self, monkeypatch):
        class DummyClient:
            def __init__(self):
                self.bound_envelope = None

            def bind_submission_envelope(self, envelope):
                self.bound_envelope = envelope

            def place_limit_order(self, *, token_id, price, size, side, order_type="GTC"):
                return _final_submit_result(self.bound_envelope, order_id=None)

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummyClient)

        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-1",
                token_id="yes-token",
                shares=12.349,
                current_price=0.50,
                best_bid=0.49,
                **_snapshot_kwargs("yes-token"),
            )
        )

        assert result.status == "rejected"
        assert result.reason == "missing_order_id"
        assert result.order_id in (None, "")
        assert result.order_id != "trade-1"

    def test_execute_exit_order_rejects_missing_token(self):
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-1",
                token_id="",
                shares=12.0,
                current_price=0.50,
            )
        )

        assert result.status == "rejected"
        assert result.reason == "no_token_id"
