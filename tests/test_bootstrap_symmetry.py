# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: DSA-07 paper/live residue cleanup; bootstrap exit symmetry reuse.
"""Tests for A1: bootstrap symmetry at exit.

Verifies that monitor refresh produces fresh bootstrap CI bounds
(not stale entry CI width), and that fallback paths are correct.
"""

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from src.calibration.platt import ExtendedPlattCalibrator
from src.engine.monitor_refresh import _build_all_bins
from src.types import Bin


def _fitted_calibrator(seed: int = 42) -> ExtendedPlattCalibrator:
    """Build a calibrator with intentional bias so renormalization matters."""
    rng = np.random.default_rng(seed)
    n = 200
    p_raw = rng.uniform(0.05, 0.95, n)
    lead_days = rng.uniform(1, 7, n)
    true_p = np.clip(p_raw * 0.8 + 0.1, 0.01, 0.99)
    outcomes = (rng.random(n) < true_p).astype(int)
    cal = ExtendedPlattCalibrator()
    cal.fit(p_raw, lead_days, outcomes)
    return cal


def _make_position(bin_label="50-51°F", market_id="cond_B", direction="buy_yes",
                   entry_ci_width=0.04, entry_method="ens_member_counting"):
    pos = MagicMock()
    pos.market_id = market_id
    pos.bin_label = bin_label
    pos.unit = "F"
    pos.target_date = "2026-07-15"
    pos.p_posterior = 0.55
    pos.entered_at = None
    pos.p_entry = 0.35
    pos.entry_method = entry_method
    pos.direction = direction
    pos.entry_ci_width = entry_ci_width
    pos.entry_price = 0.40
    pos.last_monitor_market_price = 0.40
    pos.last_monitor_prob = None
    pos.last_monitor_edge = None
    pos.last_exit_edge_context = None
    pos.decision_snapshot_id = "snap_001"
    pos.trade_id = "trade_001"
    pos.token_id = "tok_yes_001"
    pos.no_token_id = "tok_no_001"
    pos.state = "open"
    pos.city = "chicago"
    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = None
    pos.last_monitor_market_vig = None
    pos.last_monitor_whale_toxicity = None
    pos.last_monitor_market_price_is_fresh = False
    pos.last_monitor_prob_is_fresh = False
    pos.last_monitor_at = None
    pos.selected_method = None
    pos.applied_validations = []
    return pos


def _siblings():
    return [
        {"title": "48-49°F", "market_id": "cond_A", "range_low": 48.0, "range_high": 49.0},
        {"title": "50-51°F", "market_id": "cond_B", "range_low": 50.0, "range_high": 51.0},
        {"title": "52-53°F", "market_id": "cond_C", "range_low": 52.0, "range_high": 53.0},
    ]


def _live_clob_at(price: float):
    clob = MagicMock()
    clob.get_best_bid_ask.return_value = (price, price, 100.0, 100.0)
    return clob


class TestBootstrapContextStashing:
    """Verify _bootstrap_context is stashed during ENS and Day0 refresh."""

    @patch("src.engine.monitor_refresh.get_sibling_outcomes", return_value=_siblings())
    @patch("src.engine.monitor_refresh.get_calibrator")
    @patch("src.engine.monitor_refresh.fetch_ensemble")
    @patch("src.engine.monitor_refresh.validate_ensemble", return_value=True)
    @patch("src.engine.monitor_refresh.lead_days_to_date_start", return_value=3.0)
    @patch("src.engine.monitor_refresh._check_persistence_anomaly", return_value=1.0)
    def test_ens_refresh_stashes_bootstrap_context(
        self, mock_anomaly, mock_lead, mock_validate, mock_fetch_ens,
        mock_get_cal, mock_siblings,
    ):
        """ENS refresh should stash _bootstrap_context with all required keys."""
        from src.engine.monitor_refresh import _refresh_ens_member_counting

        cal = _fitted_calibrator()
        mock_get_cal.return_value = (cal, "city_level")

        # Mock ensemble result
        rng = np.random.default_rng(99)
        member_maxes = rng.normal(50.0, 2.0, 50)
        mock_fetch_ens.return_value = {
            "members_hourly": [member_maxes.tolist()] * 24,
            "times": [f"2026-07-12T{h:02d}:00:00Z" for h in range(24)],
            "model": "gfs",
        }

        pos = _make_position()
        city = MagicMock()
        city.settlement_unit = "F"
        city.timezone = "America/Chicago"
        city.name = "chicago"
        city.lat = 41.8

        conn = MagicMock()
        target_d = MagicMock()

        # Patch EnsembleSignal to avoid full construction
        with patch("src.engine.monitor_refresh.EnsembleSignal") as mock_ens_cls, \
             patch("src.engine.monitor_refresh.SettlementSemantics"):
            mock_ens = MagicMock()
            mock_ens.p_raw_vector.return_value = np.array([0.25, 0.40, 0.35])
            mock_ens.member_maxes = member_maxes
            mock_ens.spread.return_value = MagicMock(value=2.0)
            mock_ens_cls.return_value = mock_ens

            with patch("src.engine.monitor_refresh.compute_alpha", return_value=MagicMock(value=0.6)):
                result = _refresh_ens_member_counting(
                    position=pos,
                    current_p_market=0.40,
                    conn=conn,
                    city=city,
                    target_d=target_d,
                )

        # Verify _bootstrap_context was stashed
        ctx = getattr(pos, "_bootstrap_context", None)
        assert ctx is not None, "_bootstrap_context not stashed"
        assert "p_raw" in ctx
        assert "p_cal" in ctx
        assert "alpha" in ctx
        assert "bins" in ctx
        assert "held_idx" in ctx
        assert "member_extrema" in ctx
        assert "calibrator" in ctx
        assert "lead_days" in ctx
        assert "unit" in ctx
        assert len(ctx["bins"]) == 3
        assert ctx["held_idx"] == 1
        assert ctx["calibrator"] is cal
        assert ctx["unit"] == "F"


class TestBootstrapCIInRefreshPosition:
    """Verify refresh_position uses fresh bootstrap CI or falls back correctly."""

    def test_stale_ci_fallback_when_no_bootstrap_context(self):
        """Without _bootstrap_context, EdgeContext uses stale CI width."""
        from src.engine.monitor_refresh import refresh_position
        from src.contracts.edge_context import EdgeContext

        pos = _make_position()
        pos.entry_ci_width = 0.06
        # Don't set _bootstrap_context

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        clob = _live_clob_at(0.45)

        with patch("src.engine.monitor_refresh.cities_by_name", {"chicago": MagicMock(timezone="America/Chicago", settlement_unit="F", name="chicago")}), \
             patch("src.engine.monitor_refresh.recompute_native_probability", return_value=0.50):
            result = refresh_position(conn, clob, pos)

        # Should use stale CI: forward_edge ± entry_ci_width/2
        forward_edge = 0.50 - 0.45
        assert isinstance(result, EdgeContext)
        assert result.confidence_band_upper == pytest.approx(forward_edge + 0.03, abs=1e-6)
        assert result.confidence_band_lower == pytest.approx(forward_edge - 0.03, abs=1e-6)

    def test_fresh_bootstrap_ci_when_context_available(self):
        """With _bootstrap_context and multi-bin, EdgeContext uses fresh bootstrap CI."""
        from src.engine.monitor_refresh import refresh_position
        from src.contracts.edge_context import EdgeContext

        cal = _fitted_calibrator()
        pos = _make_position()
        pos.entry_ci_width = 0.06

        # Manually stash bootstrap context (simulating what ENS refresh does)
        bins = [
            Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
            Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
            Bin(low=52.0, high=53.0, label="52-53°F", unit="F"),
        ]
        rng = np.random.default_rng(42)
        member_maxes = rng.normal(50.0, 2.0, 50)

        setattr(pos, "_bootstrap_context", {
            "p_raw": np.array([0.25, 0.40, 0.35]),
            "p_cal": np.array([0.22, 0.42, 0.36]),
            "alpha": 0.6,
            "bins": bins,
            "held_idx": 1,
            "member_extrema": member_maxes,
            "calibrator": cal,
            "lead_days": 3.0,
            "unit": "F",
        })

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        clob = _live_clob_at(0.40)

        with patch("src.engine.monitor_refresh.cities_by_name", {"chicago": MagicMock(timezone="America/Chicago", settlement_unit="F", name="chicago")}), \
             patch("src.engine.monitor_refresh.recompute_native_probability", return_value=0.50):
            result = refresh_position(conn, clob, pos)

        # CI should NOT be stale symmetric bounds
        forward_edge = 0.50 - 0.40
        stale_upper = forward_edge + 0.03
        stale_lower = forward_edge - 0.03

        assert isinstance(result, EdgeContext)
        # Fresh bootstrap CI should differ from stale CI (statistically almost certain)
        # The key property: CI bounds are from bootstrap, not symmetric around edge
        assert result.ci_width > 0, "CI width should be positive"
        # With fresh bootstrap, alpha is populated (not 0.0)
        assert result.alpha == pytest.approx(0.6, abs=1e-6)
        # Fresh CI should differ from stale symmetric bounds
        assert (
            result.confidence_band_upper != pytest.approx(stale_upper, abs=1e-6) or
            result.confidence_band_lower != pytest.approx(stale_lower, abs=1e-6)
        ), "CI should differ from stale symmetric bounds"

    def test_fresh_bootstrap_ci_buy_no_direction(self):
        """buy_no direction should convert market price to YES-side for bootstrap."""
        from src.engine.monitor_refresh import refresh_position
        from src.contracts.edge_context import EdgeContext

        cal = _fitted_calibrator()
        pos = _make_position(direction="buy_no")
        pos.entry_ci_width = 0.06

        bins = [
            Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
            Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
            Bin(low=52.0, high=53.0, label="52-53°F", unit="F"),
        ]
        rng = np.random.default_rng(42)
        member_maxes = rng.normal(50.0, 2.0, 50)

        setattr(pos, "_bootstrap_context", {
            "p_raw": np.array([0.25, 0.40, 0.35]),
            "p_cal": np.array([0.22, 0.42, 0.36]),
            "alpha": 0.6,
            "bins": bins,
            "held_idx": 1,
            "member_extrema": member_maxes,
            "calibrator": cal,
            "lead_days": 3.0,
            "unit": "F",
        })

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        clob = _live_clob_at(0.60)

        # current_p_market for buy_no is the NO-token best executable price.
        with patch("src.engine.monitor_refresh.cities_by_name", {"chicago": MagicMock(timezone="America/Chicago", settlement_unit="F", name="chicago")}), \
             patch("src.engine.monitor_refresh.recompute_native_probability", return_value=0.55):
            result = refresh_position(conn, clob, pos)

        assert isinstance(result, EdgeContext)
        assert result.ci_width > 0, "CI width should be positive for buy_no"
        # Forward edge should be p_posterior - p_market (NO-side)
        # p_market for buy_no = 1 - 0.40 = 0.60
        expected_forward_edge = 0.55 - 0.60
        assert result.forward_edge == pytest.approx(expected_forward_edge, abs=1e-6)

    def test_bootstrap_failure_falls_back_to_stale_ci(self):
        """If bootstrap computation raises, falls back to stale CI width."""
        from src.engine.monitor_refresh import refresh_position
        from src.contracts.edge_context import EdgeContext

        pos = _make_position()
        pos.entry_ci_width = 0.08

        # Stash a bootstrap context that will cause an error (empty member_maxes)
        setattr(pos, "_bootstrap_context", {
            "p_raw": np.array([0.3, 0.7]),
            "p_cal": np.array([0.3, 0.7]),
            "alpha": 0.5,
            "bins": [
                Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
                Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
            ],
            "held_idx": 1,
            "member_extrema": np.array([]),  # Empty — will cause bootstrap to fail
            "calibrator": None,  # No calibrator — may cause issues
            "lead_days": 3.0,
            "unit": "F",
        })

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        clob = _live_clob_at(0.40)

        with patch("src.engine.monitor_refresh.cities_by_name", {"chicago": MagicMock(timezone="America/Chicago", settlement_unit="F", name="chicago")}), \
             patch("src.engine.monitor_refresh.recompute_native_probability", return_value=0.50):
            result = refresh_position(conn, clob, pos)

        # Should fall back to stale CI
        forward_edge = 0.50 - 0.40
        assert isinstance(result, EdgeContext)
        assert result.confidence_band_upper == pytest.approx(forward_edge + 0.04, abs=1e-6)
        assert result.confidence_band_lower == pytest.approx(forward_edge - 0.04, abs=1e-6)


class TestDay0WindowBootstrapPropagation:
    """Verify bootstrap context propagates through the replace() path for day0_window."""

    def test_day0_window_propagates_bootstrap_from_refresh_pos(self):
        """When state=day0_window and entry_method differs, _bootstrap_context
        is stashed on refresh_pos (a copy). It must be propagated to the original pos."""
        from src.engine.monitor_refresh import refresh_position
        from src.contracts.edge_context import EdgeContext

        cal = _fitted_calibrator()
        pos = _make_position(entry_method="ens_member_counting")
        pos.state = "day0_window"
        pos.entry_ci_width = 0.06

        bins = [
            Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
            Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
            Bin(low=52.0, high=53.0, label="52-53°F", unit="F"),
        ]
        rng = np.random.default_rng(42)
        member_maxes = rng.normal(50.0, 2.0, 50)

        bootstrap_ctx = {
            "p_raw": np.array([0.25, 0.40, 0.35]),
            "p_cal": np.array([0.22, 0.42, 0.36]),
            "alpha": 0.6,
            "bins": bins,
            "held_idx": 1,
            "member_extrema": member_maxes,
            "calibrator": cal,
            "lead_days": 0.0,
            "unit": "F",
        }

        # Track that refresh_pos received the bootstrap context
        refresh_pos_ref = [None]

        def mock_recompute(position, current_p_market, registry, **kwargs):
            """Simulate refresh stashing _bootstrap_context on refresh_pos."""
            refresh_pos_ref[0] = position
            setattr(position, "_bootstrap_context", bootstrap_ctx)
            setattr(position, "selected_method", "day0_observation")
            setattr(position, "applied_validations", ["day0_observation"])
            return 0.50

        # Patch replace() to return a distinct MagicMock (simulating a copy)
        refresh_pos_mock = MagicMock()
        refresh_pos_mock.entry_method = "day0_observation"

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        clob = _live_clob_at(0.40)

        with patch("src.engine.monitor_refresh.cities_by_name", {"chicago": MagicMock(timezone="America/Chicago", settlement_unit="F", name="chicago")}), \
             patch("src.engine.monitor_refresh.recompute_native_probability", side_effect=mock_recompute), \
             patch("src.engine.monitor_refresh.replace", return_value=refresh_pos_mock):
            result = refresh_position(conn, clob, pos)

        # The bootstrap context should have been propagated from refresh_pos to pos
        assert isinstance(result, EdgeContext)
        assert result.ci_width > 0
        # With fresh bootstrap, alpha should be non-zero
        assert result.alpha == pytest.approx(0.6, abs=1e-6)
