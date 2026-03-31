"""Tests for executor and portfolio."""

import json
import tempfile
from pathlib import Path

import pytest

from src.execution.executor import create_execution_intent, execute_intent, OrderResult
from src.contracts import EdgeContext, EntryMethod
import numpy as np
from src.state.portfolio import (
    Position, PortfolioState, load_portfolio, save_portfolio,
    add_position, remove_position, portfolio_heat,
    city_exposure, cluster_exposure,
)
from src.types import Bin, BinEdge


class TestPortfolio:
    def test_empty_portfolio(self):
        state = PortfolioState()
        assert len(state.positions) == 0
        assert portfolio_heat(state) == 0.0

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
        assert portfolio_heat(state) == pytest.approx(0.10)

        removed = remove_position(state, "t1")
        assert removed is not None
        assert removed.trade_id == "t1"
        assert len(state.positions) == 0

    def test_remove_nonexistent(self):
        state = PortfolioState()
        assert remove_position(state, "nonexistent") is None

    def test_city_exposure(self):
        state = PortfolioState(bankroll=100.0)
        add_position(state, Position(
            trade_id="t1", market_id="m1", city="NYC",
            cluster="US-Northeast", target_date="2026-01-15",
            bin_label="39-40", direction="buy_yes",
            size_usd=10.0, entry_price=0.40, p_posterior=0.60,
            edge=0.20, entered_at="2026-01-12T00:00:00Z",
        ))
        add_position(state, Position(
            trade_id="t2", market_id="m2", city="Chicago",
            cluster="US-Midwest", target_date="2026-01-15",
            bin_label="30-32", direction="buy_yes",
            size_usd=5.0, entry_price=0.30, p_posterior=0.50,
            edge=0.20, entered_at="2026-01-12T00:00:00Z",
        ))

        assert city_exposure(state, "NYC") == pytest.approx(0.10)
        assert city_exposure(state, "Chicago") == pytest.approx(0.05)
        assert cluster_exposure(state, "US-Northeast") == pytest.approx(0.10)

    def test_save_load_roundtrip(self, tmp_path):
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
        loaded = load_portfolio(path)

        assert loaded.bankroll == 200.0
        assert len(loaded.positions) == 1
        assert loaded.positions[0].trade_id == "t1"
        assert loaded.positions[0].city == "NYC"


class TestExecutor:
    def test_paper_fill(self):
        edge = BinEdge(
            bin=Bin(low=39, high=40, label="39-40"),
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
