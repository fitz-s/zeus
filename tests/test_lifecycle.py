# Created: 2025-10-01
# Lifecycle: created=2025-10-01; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Exit-trigger + harvester lifecycle regression tests — covers
#          position exit detection, harvest_settlement default-HIGH routing
#          through calibration_pairs_v2 after C5 (2026-04-24), and p_raw
#          skip behavior when ensemble signal is absent.
# Reuse: Referenced by regression suite; last touched 2026-05-08 for Wave28
#        (HIGH→v2 route). Apply v2 schema in test fixtures when asserting
#        post-harvest pair rows.
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md
"""Tests for exit triggers and harvester."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.engine import monitor_refresh
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


def _make_non_authoritative_edge_context(*, market_velocity_1h: float = 0.0) -> EdgeContext:
    """Build a degraded monitor context whose probability fields are not authority."""
    dummy_vec = np.array([1.0])
    return EdgeContext(
        p_raw=dummy_vec,
        p_cal=dummy_vec,
        p_market=dummy_vec,
        p_posterior=np.nan,
        forward_edge=np.nan,
        alpha=0.55,
        confidence_band_upper=np.nan,
        confidence_band_lower=np.nan,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap-degraded",
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=market_velocity_1h,
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

    def test_buy_yes_ev_gate_uses_current_edge_context_posterior(self, monkeypatch):
        """Monitor-current posterior must survive into the buy-yes hold EV gate."""
        import src.execution.exit_triggers as exit_triggers

        pos = _make_position(p_posterior=0.90)
        pos.neg_edge_count = 1
        captured: dict[str, float] = {}

        def capture_hold_value(shares, current_p_posterior):
            captured["posterior"] = current_p_posterior
            return SimpleNamespace(net_value=0.0)

        monkeypatch.setattr(exit_triggers, "_declared_zero_cost_hold_value", capture_hold_value)
        ctx = _make_edge_context(p_posterior=0.10, entry_price=0.50)

        signal = evaluate_exit_triggers(pos, ctx, best_bid=0.49)

        assert signal is not None
        assert signal.trigger == "EDGE_REVERSAL"
        assert captured["posterior"] == pytest.approx(ctx.p_posterior)
        assert captured["posterior"] != pytest.approx(pos.p_posterior)

    def test_monitor_current_posterior_flows_to_buy_yes_ev_gate(self, monkeypatch):
        """refresh_position -> exit trigger must preserve the fresh monitor posterior."""
        import src.execution.exit_triggers as exit_triggers

        pos = _make_position(entry_price=0.12, p_posterior=0.90)
        pos.neg_edge_count = 1
        captured: dict[str, float] = {}

        def fresh_refresh(position, *, conn, city, target_d):
            position.selected_method = position.entry_method
            position.applied_validations = ["fresh_ens_fetch"]
            return 0.05, position, True

        def capture_hold_value(shares, current_p_posterior):
            captured["posterior"] = current_p_posterior
            return SimpleNamespace(net_value=0.0)

        monkeypatch.setattr(monitor_refresh, "monitor_probability_refresh", fresh_refresh)
        monkeypatch.setattr(monitor_refresh, "monitor_quote_refresh", lambda conn, clob, position: None)
        monkeypatch.setattr(monitor_refresh, "_detect_whale_toxicity_from_orderbook", lambda *args, **kwargs: False)
        monkeypatch.setattr(exit_triggers, "_declared_zero_cost_hold_value", capture_hold_value)

        edge_ctx = monitor_refresh.refresh_position(None, None, pos)
        signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0, best_bid=0.49)

        assert edge_ctx.p_posterior == pytest.approx(0.05)
        assert edge_ctx.p_posterior != pytest.approx(pos.p_posterior)
        assert signal is not None
        assert signal.trigger == "EDGE_REVERSAL"
        assert captured["posterior"] == pytest.approx(0.05)

    def test_stale_monitor_probability_cannot_drive_exit(self, monkeypatch):
        """Stale monitor probability remains non-authoritative at the exit seam."""
        pos = _make_position(entry_price=0.12, p_posterior=0.90, last_monitor_prob=0.41)
        pos.neg_edge_count = 1

        def stale_refresh(position, *, conn, city, target_d):
            position.selected_method = position.entry_method
            position.applied_validations = ["fresh_ens_fetch", "missing_observation_timestamp"]
            return position.p_posterior, position, False

        monkeypatch.setattr(monitor_refresh, "monitor_probability_refresh", stale_refresh)
        monkeypatch.setattr(monitor_refresh, "monitor_quote_refresh", lambda conn, clob, position: None)
        monkeypatch.setattr(monitor_refresh, "_detect_whale_toxicity_from_orderbook", lambda *args, **kwargs: False)

        edge_ctx = monitor_refresh.refresh_position(None, None, pos)
        signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0, best_bid=0.49)

        assert pos.last_monitor_prob == pytest.approx(0.41)
        assert pos.last_monitor_prob_is_fresh is False
        assert not np.isfinite(pos.last_monitor_edge)
        assert "monitor_probability_stale" in pos.applied_validations
        assert not np.isfinite(edge_ctx.p_posterior)
        assert not np.isfinite(edge_ctx.forward_edge)
        assert not np.isfinite(edge_ctx.ci_width)
        assert signal is None
        assert pos.neg_edge_count == 1

    def test_missing_probability_authority_does_not_block_flash_crash_exit(self):
        """Quote/velocity safety evidence must survive missing monitor probability."""
        pos = _make_position()
        signal = evaluate_exit_triggers(
            pos,
            _make_non_authoritative_edge_context(market_velocity_1h=-0.20),
            hours_to_settlement=24.0,
        )

        assert signal is not None
        assert signal.trigger == "FLASH_CRASH_PANIC"

    def test_missing_probability_authority_does_not_block_vig_extreme_exit(self):
        """Market-vig safety evidence is not probability authority and remains live."""
        pos = _make_position()
        signal = evaluate_exit_triggers(
            pos,
            _make_non_authoritative_edge_context(),
            hours_to_settlement=24.0,
            market_vig=1.10,
        )

        assert signal is not None
        assert signal.trigger == "VIG_EXTREME"

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


class TestMonitorWhaleToxicity:
    class _BookClob:
        def __init__(self, books):
            self.books = books

        def get_best_bid_ask(self, token_id):
            return self.books[token_id]

    @staticmethod
    def _siblings():
        return [
            {"market_id": "m-below", "range_low": 37, "range_high": 38, "token_id": "yes-below"},
            {"market_id": "m1", "range_low": 39, "range_high": 40, "token_id": "yes-held"},
            {"market_id": "m-above", "range_low": 41, "range_high": 42, "token_id": "yes-above"},
        ]

    @staticmethod
    def _conn_with_prior(tmp_path, token_id: str, price: float, now: datetime):
        conn = get_connection(tmp_path / "whale.db")
        init_schema(conn)
        conn.execute(
            """
            INSERT INTO token_price_log
                (token_id, price, timestamp)
            VALUES (?, ?, ?)
            """,
            (token_id, price, (now - timedelta(hours=2)).isoformat()),
        )
        conn.commit()
        return conn

    def test_orderbook_adjacent_pressure_flags_buy_yes_whale_toxicity(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.40, now)
        clob = self._BookClob({
            "yes-above": (0.50, 0.52, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is True
        assert "whale_toxicity_available:adjacent_orderbook_pressure" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_returns_false_when_clear(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.42, now)
        clob = self._BookClob({
            "yes-above": (0.44, 0.46, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is False
        assert "whale_toxicity_available:clear" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_stays_unknown_without_verified_scan(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.40, now)
        clob = self._BookClob({
            "yes-above": (0.60, 0.62, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "STALE")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is None
        assert "whale_toxicity_unavailable:market_scan_not_verified" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_is_not_applicable_to_buy_no(self):
        pos = _make_position(direction="buy_no", no_token_id="no-held")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            None,
            None,
            pos,
            held_best_bid=None,
            held_best_ask=None,
        )

        assert result is False
        assert "whale_toxicity_not_applicable:buy_no" in pos.applied_validations


class TestHarvester:
    def test_harvest_creates_pairs(self, tmp_path):
        """Post-C5 (2026-04-24): harvest_settlement default-HIGH path now
        writes to calibration_pairs_v2 (previously legacy calibration_pairs).
        """
        from src.state.schema.v2_schema import apply_v2_schema

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        apply_v2_schema(conn)

        bin_labels = ["32 or below", "33-34", "35-36", "37-38", "39-40",
                      "41-42", "43-44", "45-46", "47-48", "49-50", "51 or higher"]
        p_raw = [0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.03, 0.01, 0.005, 0.005]

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=bin_labels,
            p_raw_vector=p_raw,
            lead_days=3.0,
            forecast_issue_time="2026-01-12T00:00:00Z",
            source_model_version="test_lifecycle_v1",
        )
        conn.commit()

        assert count == 11

        # Post-C5: HIGH default routes to calibration_pairs_v2.
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM calibration_pairs_v2 GROUP BY outcome"
        ).fetchall()
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
