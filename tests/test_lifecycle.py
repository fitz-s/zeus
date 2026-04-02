"""Tests for exit triggers and harvester."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.execution.exit_triggers import (
    evaluate_exit_triggers, clear_reversal_state,
    ExitSignal,
)
from src.execution.harvester import harvest_settlement
from src.state.portfolio import Position
from src.state.db import get_connection, init_schema
from src.config import City
from src.contracts import EdgeContext, EntryMethod


def _make_edge_context(p_posterior: float, entry_price: float) -> EdgeContext:
    """Build a minimal EdgeContext for tests. forward_edge = p_posterior - entry_price."""
    forward_edge = p_posterior - entry_price
    dummy_vec = np.array([1.0])
    return EdgeContext(
        p_raw=dummy_vec,
        p_cal=dummy_vec,
        p_market=dummy_vec,
        p_posterior=p_posterior,
        forward_edge=forward_edge,
        alpha=0.55,
        confidence_band_upper=forward_edge + 0.05,
        confidence_band_lower=forward_edge - 0.05,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap",
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )


NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1", market_id="m1", city="NYC",
        cluster="US-Northeast", target_date="2026-01-15",
        bin_label="39-40", direction="buy_yes",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60,
        edge=0.20, entered_at="2026-01-12T00:00:00Z",
    )
    defaults.update(kwargs)
    return Position(**defaults)


class TestExitTriggers:

    def test_settlement_imminent(self):
        pos = _make_position()
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.60, 0.40), hours_to_settlement=0.5)
        assert signal is not None
        assert signal.trigger == "SETTLEMENT_IMMINENT"
        assert signal.urgency == "immediate"

    def test_whale_toxicity(self):
        pos = _make_position()
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.60, 0.40), is_whale_sweep=True)
        assert signal is not None
        assert signal.trigger == "WHALE_TOXICITY"

    def test_soft_divergence_requires_adverse_velocity_confirmation(self):
        pos = _make_position()
        edge_ctx = EdgeContext(
            p_raw=np.array([1.0]),
            p_cal=np.array([1.0]),
            p_market=np.array([0.40]),
            p_posterior=0.20,
            forward_edge=-0.20,
            alpha=0.0,
            confidence_band_upper=0.05,
            confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="test-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.20,
        )
        signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0)
        assert signal is None

    def test_hard_divergence_panics_without_velocity_confirmation(self):
        pos = _make_position()
        edge_ctx = EdgeContext(
            p_raw=np.array([1.0]),
            p_cal=np.array([1.0]),
            p_market=np.array([0.40]),
            p_posterior=0.20,
            forward_edge=-0.20,
            alpha=0.0,
            confidence_band_upper=0.05,
            confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="test-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.31,
        )
        signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0)
        assert signal is not None
        assert signal.trigger == "MODEL_DIVERGENCE_PANIC"

    def test_edge_reversal_needs_two_confirmations(self):
        """CLAUDE.md §4.2: EDGE_REVERSAL needs 2 confirmations, 1st doesn't trigger."""
        pos = _make_position()

        # First check: edge reversed but only 1 confirmation
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.30, 0.40))  # edge < 0
        assert signal is None  # Should NOT trigger on first reversal

        # Second check: confirmed reversal
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.30, 0.40))
        assert signal is not None
        assert signal.trigger == "EDGE_REVERSAL"

    def test_edge_reversal_resets_on_recovery(self):
        """If edge recovers between checks, counter resets."""
        pos = _make_position()

        # First reversal
        evaluate_exit_triggers(pos, _make_edge_context(0.30, 0.40))
        # Edge recovers
        evaluate_exit_triggers(pos, _make_edge_context(0.60, 0.40))
        # Another reversal — should need 2 new confirmations
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.30, 0.40))
        assert signal is None  # Only 1st confirmation after reset

    def test_no_exit_when_edge_healthy(self):
        pos = _make_position()
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.60, 0.40))
        assert signal is None

    def test_vig_extreme(self):
        pos = _make_position()
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.60, 0.40), market_vig=1.10)
        assert signal is not None
        assert signal.trigger == "VIG_EXTREME"

class TestHarvester:
    def test_harvest_creates_pairs(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        bin_labels = ["32 or below", "33-34", "35-36", "37-38", "39-40",
                      "41-42", "43-44", "45-46", "47-48", "49-50", "51 or higher"]
        p_raw = [0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.03, 0.01, 0.005, 0.005]

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=bin_labels,
            p_raw_vector=p_raw,
            lead_days=3.0,
        )
        conn.commit()

        assert count == 11

        # Verify: exactly 1 outcome=1 (the winner), 10 outcome=0
        rows = conn.execute("SELECT outcome, COUNT(*) FROM calibration_pairs GROUP BY outcome").fetchall()
        outcome_counts = {r[0]: r[1] for r in rows}
        assert outcome_counts[1] == 1
        assert outcome_counts[0] == 10

        conn.close()

    def test_harvest_skips_missing_p_raw(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=["39-40", "41-42"],
            p_raw_vector=None,
        )

        assert count == 0  # No P_raw → no pairs created
        conn.close()
