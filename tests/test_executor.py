"""Tests for executor and portfolio."""

import pytest

from src.execution.executor import (
    create_execution_intent,
    create_exit_order_intent,
    execute_exit_order,
    execute_intent,
)
from src.contracts import EdgeContext, EntryMethod
import numpy as np
from src.state.portfolio import (
    Position, PortfolioState, load_portfolio, save_portfolio,
    add_position, remove_position,
)
from src.types import Bin, BinEdge


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

        assert loaded.bankroll == 200.0
        assert len(loaded.positions) == 1
        assert loaded.positions[0].trade_id == "t1"
        assert loaded.positions[0].city == "NYC"


class TestExecutor:
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
            def __init__(self, paper_mode):
                assert paper_mode is False

            def place_limit_order(self, *, token_id, price, size, side):
                captured.update(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side,
                )
                return {"orderID": "sell-1", "status": "OPEN"}

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummyClient)

        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-1",
                token_id="yes-token",
                shares=12.349,
                current_price=0.50,
                best_bid=0.49,
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
        }

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
