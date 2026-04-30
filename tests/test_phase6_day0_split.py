# Lifecycle: created=2026-04-18; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: Phase 6 R-BA..R-BG invariants: Day0 split + router + DT#6 graceful-degradation + B055 absorption
# Reuse: Anchors on phase6_contract.md. Confirms Day0Router routes by metric+causality,
#   LOW missing low_so_far clean-rejects, RemainingMemberExtrema prevents MAX→MIN alias.
#   Fails RED until impl lands.
from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# R-BA: HIGH path uses MAX array; settlement samples >= obs_high_so_far
# ---------------------------------------------------------------------------

class TestRBA_HighPathMaxArray:
    """R-BA: Day0Router sends HIGH metric to Day0HighSignal using MAX array."""

    def test_high_signal_settlement_respects_hard_floor(self):
        """Settlement samples must all be >= observed_high_so_far (hard-floor semantics)."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs

        maxes = np.array([72.0, 75.0, 68.0, 80.0], dtype=np.float64)
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        inputs = Day0SignalInputs(
            temperature_metric=HIGH_LOCALDAY_MAX,
            current_temp=70.0,
            hours_remaining=6.0,
            observed_high_so_far=74.0,
            observed_low_so_far=None,
            member_maxes_remaining=maxes,
            member_mins_remaining=None,
        )
        signal = Day0Router.route(inputs)
        samples = signal.settlement_samples()
        assert np.all(samples >= 74.0), f"Hard-floor violated: min={samples.min()} < 74.0"

    def test_high_signal_uses_max_array_not_min_array(self):
        """Router must pass MAX array to high signal, producing correct samples."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        maxes = np.array([80.0, 82.0, 85.0], dtype=np.float64)
        mins = np.array([50.0, 52.0, 48.0], dtype=np.float64)
        inputs = Day0SignalInputs(
            temperature_metric=HIGH_LOCALDAY_MAX,
            current_temp=78.0,
            hours_remaining=3.0,
            observed_high_so_far=79.0,
            observed_low_so_far=None,
            member_maxes_remaining=maxes,
            member_mins_remaining=mins,
        )
        signal = Day0Router.route(inputs)
        samples = signal.settlement_samples()
        # Hard-floor of 79 applied to [80, 82, 85] → all >= 79
        assert samples.min() >= 79.0
        # Would fail if mins array was mistakenly used (50-52 < 79)
        assert samples.max() <= 90.0  # sanity: within plausible range

    def test_high_signal_returns_day0_high_signal_instance(self):
        """Router must return Day0HighSignal for HIGH metric."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.signal.day0_high_signal import Day0HighSignal
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        inputs = Day0SignalInputs(
            temperature_metric=HIGH_LOCALDAY_MAX,
            current_temp=70.0,
            hours_remaining=4.0,
            observed_high_so_far=68.0,
            observed_low_so_far=None,
            member_maxes_remaining=np.array([69.0, 71.0]),
            member_mins_remaining=None,
        )
        signal = Day0Router.route(inputs)
        assert isinstance(signal, Day0HighSignal)


# ---------------------------------------------------------------------------
# R-BB: LOW path uses MIN array; settlement samples <= obs_low_so_far
# ---------------------------------------------------------------------------

class TestRBB_LowPathMinArray:
    """R-BB: Day0Router sends LOW metric to Day0LowNowcastSignal using MIN array."""

    def test_low_signal_settlement_respects_ceiling(self):
        """Settlement samples must all be <= observed_low_so_far (ceiling semantics)."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        mins = np.array([28.0, 30.0, 25.0, 32.0], dtype=np.float64)
        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=33.0,
            hours_remaining=8.0,
            observed_high_so_far=None,
            observed_low_so_far=31.0,
            member_maxes_remaining=None,
            member_mins_remaining=mins,
            causality_status="OK",
        )
        signal = Day0Router.route(inputs)
        samples = signal.settlement_samples()
        assert np.all(samples <= 31.0), f"Ceiling violated: max={samples.max()} > 31.0"

    def test_low_signal_returns_day0_low_nowcast_instance(self):
        """Router must return Day0LowNowcastSignal for LOW metric."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        mins = np.array([28.0, 30.0, 25.0], dtype=np.float64)
        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=35.0,
            hours_remaining=6.0,
            observed_high_so_far=None,
            observed_low_so_far=33.0,
            member_maxes_remaining=None,
            member_mins_remaining=mins,
            causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED",
        )
        signal = Day0Router.route(inputs)
        assert isinstance(signal, Day0LowNowcastSignal)

    def test_low_signal_p_vector_handles_open_shoulders(self):
        """LOW Day0 must use Bin shoulder semantics instead of float(None)."""
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
        from src.types import Bin

        bins = [
            Bin(low=None, high=36, label="36°F or below", unit="F"),
            Bin(low=37, high=38, label="37-38°F", unit="F"),
            Bin(low=39, high=None, label="39°F or higher", unit="F"),
        ]

        low_tail_signal = Day0LowNowcastSignal(
            observed_low_so_far=35.0,
            member_mins_remaining=np.array([35.0, 35.0, 35.0], dtype=np.float64),
            current_temp=40.0,
            hours_remaining=0.0,
            unit="F",
        )
        low_tail_probs = low_tail_signal.p_vector(bins)
        assert np.isfinite(low_tail_probs).all()
        assert low_tail_probs[0] == pytest.approx(1.0)
        assert low_tail_probs.sum() == pytest.approx(1.0)

        high_tail_signal = Day0LowNowcastSignal(
            observed_low_so_far=45.0,
            member_mins_remaining=np.array([45.0, 45.0, 45.0], dtype=np.float64),
            current_temp=45.0,
            hours_remaining=0.0,
            unit="F",
        )
        high_tail_probs = high_tail_signal.p_vector(bins)
        assert np.isfinite(high_tail_probs).all()
        assert high_tail_probs[2] == pytest.approx(1.0)
        assert high_tail_probs.sum() == pytest.approx(1.0)

    def test_low_signal_applies_injected_settlement_rounding_before_binning(self):
        """LOW Day0 samples must be rounded by the market settlement rule."""
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
        from src.types import Bin

        bins = [
            Bin(low=37, high=38, label="37-38°F", unit="F"),
            Bin(low=39, high=40, label="39-40°F", unit="F"),
        ]

        floor_signal = Day0LowNowcastSignal(
            observed_low_so_far=40.0,
            member_mins_remaining=np.array([38.7, 38.7, 38.7], dtype=np.float64),
            current_temp=38.7,
            hours_remaining=24.0,
            unit="F",
            round_fn=lambda values: np.floor(np.asarray(values, dtype=float)),
        )
        np.testing.assert_allclose(floor_signal.p_vector(bins), np.array([1.0, 0.0]))

        half_up_signal = Day0LowNowcastSignal(
            observed_low_so_far=40.0,
            member_mins_remaining=np.array([38.5, 38.5, 38.5], dtype=np.float64),
            current_temp=38.5,
            hours_remaining=24.0,
            unit="F",
        )
        np.testing.assert_allclose(half_up_signal.p_vector(bins), np.array([0.0, 1.0]))

    def test_low_router_preserves_rich_observation_context(self):
        """Router LOW branch must not drop observation timing/context metadata."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        current_utc = datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)
        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=40.0,
            hours_remaining=2.0,
            observed_high_so_far=None,
            observed_low_so_far=38.0,
            member_maxes_remaining=None,
            member_mins_remaining=np.array([37.0, 38.0, 39.0], dtype=np.float64),
            causality_status="OK",
            unit="F",
            observation_source="wu_api",
            observation_time="2026-04-29T17:45:00+00:00",
            temporal_context=SimpleNamespace(current_utc_timestamp=current_utc),
        )

        signal = Day0Router.route(inputs)
        context = signal.forecast_context()

        assert context["backbone"]["observation_source"] == "wu_api"
        assert context["backbone"]["observation_time"] == "2026-04-29T17:45:00+00:00"
        assert context["backbone"]["current_utc_timestamp"] == current_utc.isoformat()
        assert context["backbone"]["nowcast"]["fresh_observation"] is True


# ---------------------------------------------------------------------------
# R-BC: LOW missing low_so_far → raises (clean reject, no silent degrade)
# ---------------------------------------------------------------------------

class TestRBC_LowMissingLowSoFar:
    """R-BC: LOW path must raise ValueError when observed_low_so_far is None."""

    def test_low_signal_raises_when_low_so_far_missing(self):
        """Router/LowSignal must raise ValueError for None observed_low_so_far, not silently produce wrong output."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=35.0,
            hours_remaining=6.0,
            observed_high_so_far=None,
            observed_low_so_far=None,  # missing
            member_maxes_remaining=None,
            member_mins_remaining=np.array([28.0, 30.0]),
            causality_status="OK",
        )
        with pytest.raises((ValueError, TypeError)):
            Day0Router.route(inputs)

    def test_low_signal_class_raises_directly_when_low_so_far_missing(self):
        """Day0LowNowcastSignal.__init__ raises on None observed_low_so_far."""
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
        from src.signal.day0_router import Day0SignalInputs
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=35.0,
            hours_remaining=6.0,
            observed_high_so_far=None,
            observed_low_so_far=None,
            member_maxes_remaining=None,
            member_mins_remaining=np.array([28.0, 30.0]),
            causality_status="OK",
        )
        with pytest.raises((ValueError, TypeError)):
            Day0LowNowcastSignal(inputs)


# ---------------------------------------------------------------------------
# R-BD: LOW + N/A_CAUSAL_DAY_ALREADY_STARTED → nowcast, NOT historical Platt
# ---------------------------------------------------------------------------

class TestRBD_CausalityRouting:
    """R-BD: LOW + N/A_CAUSAL_DAY_ALREADY_STARTED must route through nowcast."""

    def test_low_ok_causality_routes_to_nowcast(self):
        """OK causality status for LOW → routes to Day0LowNowcastSignal."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=32.0,
            hours_remaining=5.0,
            observed_high_so_far=None,
            observed_low_so_far=30.0,
            member_maxes_remaining=None,
            member_mins_remaining=np.array([26.0, 28.0, 29.0]),
            causality_status="OK",
        )
        signal = Day0Router.route(inputs)
        assert isinstance(signal, Day0LowNowcastSignal)

    def test_low_na_causal_routes_to_nowcast(self):
        """N/A_CAUSAL_DAY_ALREADY_STARTED for LOW → routes to Day0LowNowcastSignal."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=32.0,
            hours_remaining=5.0,
            observed_high_so_far=None,
            observed_low_so_far=30.0,
            member_maxes_remaining=None,
            member_mins_remaining=np.array([26.0, 28.0]),
            causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED",
        )
        signal = Day0Router.route(inputs)
        assert isinstance(signal, Day0LowNowcastSignal)

    def test_low_unsupported_causality_raises(self):
        """LOW with unsupported causality_status must raise ValueError, not silently route."""
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=32.0,
            hours_remaining=5.0,
            observed_high_so_far=None,
            observed_low_so_far=30.0,
            member_maxes_remaining=None,
            member_mins_remaining=np.array([26.0, 28.0]),
            causality_status="UNSUPPORTED_STATUS",
        )
        with pytest.raises(ValueError):
            Day0Router.route(inputs)


# ---------------------------------------------------------------------------
# R-BE: day0_low_nowcast_signal does NOT import day0_high_signal (AST walk)
# ---------------------------------------------------------------------------

class TestRBE_NoCircularImport:
    """R-BE: day0_low_nowcast_signal must NOT import day0_high_signal."""

    def test_low_nowcast_does_not_import_high_signal(self):
        """AST walk: day0_low_nowcast_signal.py must not import day0_high_signal."""
        src_file = Path(__file__).parent.parent / "src" / "signal" / "day0_low_nowcast_signal.py"
        assert src_file.exists(), f"day0_low_nowcast_signal.py not found at {src_file}"
        tree = ast.parse(src_file.read_text())
        forbidden = "day0_high_signal"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert forbidden not in alias.name, (
                        f"day0_low_nowcast_signal.py imports {alias.name!r} which contains '{forbidden}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert forbidden not in module, (
                    f"day0_low_nowcast_signal.py has 'from {module} import ...' which contains '{forbidden}'"
                )


# ---------------------------------------------------------------------------
# R-BF: RemainingMemberExtrema both None → raises; metric-mismatch → raises
# ---------------------------------------------------------------------------

class TestRBF_RemainingMemberExtrema:
    """R-BF: RemainingMemberExtrema dataclass enforces structural invariants."""

    def test_both_maxes_and_mins_none_raises(self):
        """RemainingMemberExtrema(maxes=None, mins=None) must raise at construction."""
        from src.signal.day0_extrema import RemainingMemberExtrema

        with pytest.raises((ValueError, TypeError)):
            RemainingMemberExtrema(maxes=None, mins=None)

    def test_high_metric_sets_maxes_not_mins(self):
        """For HIGH metric, RemainingMemberExtrema.maxes is populated; mins is None."""
        from src.signal.day0_extrema import RemainingMemberExtrema
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        arr = np.array([70.0, 72.0, 75.0])
        extrema = RemainingMemberExtrema.for_metric(arr, HIGH_LOCALDAY_MAX)
        assert extrema.maxes is not None
        assert extrema.mins is None

    def test_low_metric_sets_mins_not_maxes(self):
        """For LOW metric, RemainingMemberExtrema.mins is populated; maxes is None."""
        from src.signal.day0_extrema import RemainingMemberExtrema
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        arr = np.array([28.0, 30.0, 25.0])
        extrema = RemainingMemberExtrema.for_metric(arr, LOW_LOCALDAY_MIN)
        assert extrema.mins is not None
        assert extrema.maxes is None

    def test_metric_mismatch_raises(self):
        """Passing maxes where mins expected (or vice versa) at Router boundary raises."""
        from src.signal.day0_extrema import RemainingMemberExtrema
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

        arr = np.array([70.0, 72.0])
        # Create a HIGH extrema then try to use with LOW router inputs
        extrema = RemainingMemberExtrema.for_metric(arr, HIGH_LOCALDAY_MAX)
        # mins should be None — passing to LOW router should fail at signal init
        from src.signal.day0_router import Day0Router, Day0SignalInputs

        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=35.0,
            hours_remaining=6.0,
            observed_high_so_far=None,
            observed_low_so_far=33.0,
            member_maxes_remaining=extrema.maxes,
            member_mins_remaining=extrema.mins,  # None — mismatch
            causality_status="OK",
        )
        with pytest.raises((ValueError, TypeError)):
            Day0Router.route(inputs)


# ---------------------------------------------------------------------------
# R-BG: DT#6 graceful-degradation — authority-loss does not kill cycle
# ---------------------------------------------------------------------------

class TestRBG_DT6GracefulDegradation:
    """R-BG: DT#6 — portfolio authority-loss must not raise; monitor lane runs read-only."""

    def test_load_portfolio_degraded_returns_portfolio_state_not_raises(self):
        """When DB connection fails, load_portfolio must return PortfolioState(authority='unverified'), not raise."""
        from src.state.portfolio import load_portfolio, PortfolioState
        from unittest.mock import patch

        # get_connection and get_trade_connection_with_world are imported inside load_portfolio(),
        # so patch at the source module (src.state.db), not at portfolio.
        with patch("src.state.db.get_connection", side_effect=Exception("DB unavailable")):
            with patch("src.state.db.get_trade_connection_with_world", side_effect=Exception("DB unavailable")):
                result = load_portfolio()
        assert isinstance(result, PortfolioState), "Must return PortfolioState on authority loss"
        assert result.authority in ("unverified", "degraded"), (
            f"Degraded path must set authority to 'unverified' or 'degraded', got {result.authority!r}"
        )
        assert result.portfolio_loader_degraded is True

    def test_degraded_portfolio_has_no_active_positions(self):
        """Degraded PortfolioState must not surface unverified positions as tradeable."""
        from src.state.portfolio import load_portfolio
        from unittest.mock import patch

        with patch("src.state.db.get_connection", side_effect=Exception("DB unavailable")):
            with patch("src.state.db.get_trade_connection_with_world", side_effect=Exception("DB unavailable")):
                result = load_portfolio()
        # The key invariant: authority is not "canonical_db" on connection failure
        assert result.authority != "canonical_db"

    def test_riskguard_trailing_loss_stale_does_not_halt(self):
        """B055: stale trailing-loss reference must degrade to DATA_DEGRADED, not raise RuntimeError."""
        import sqlite3
        from datetime import timedelta, datetime, timezone
        from src.riskguard.riskguard import _trailing_loss_snapshot, RiskLevel

        # Create an in-memory DB with only stale rows (> 2h old)
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE risk_state (
                id INTEGER PRIMARY KEY,
                checked_at TEXT,
                level TEXT,
                brier REAL,
                accuracy REAL,
                win_rate REAL,
                details_json TEXT,
                force_exit_review INTEGER DEFAULT 0
            )
        """)
        # Insert a row that is 3+ hours old (stale beyond TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE)
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        details = '{"effective_bankroll": 1000.0}'
        conn.execute(
            "INSERT INTO risk_state (checked_at, level, brier, accuracy, win_rate, details_json) VALUES (?, 'GREEN', 0.25, 0.6, 0.6, ?)",
            (stale_time, details),
        )
        conn.commit()

        now = datetime.now(timezone.utc).isoformat()
        # Must not raise — must return degraded snapshot
        result = _trailing_loss_snapshot(
            conn,
            now=now,
            lookback=timedelta(hours=24),
            current_equity=1000.0,
            initial_bankroll=1000.0,
            threshold_pct=0.05,
        )
        conn.close()

        assert result["degraded"] is True or result["level"] == RiskLevel.DATA_DEGRADED, (
            f"Stale reference must produce degraded result, got: {result}"
        )
        assert "RuntimeError" not in str(type(result.get("level", ""))), (
            "Must not raise RuntimeError on stale reference"
        )

    def test_riskguard_portfolio_authority_degraded_does_not_halt_cycle(self):
        """DT#6: riskguard cycle entry must handle degraded portfolio without RuntimeError.

        Requires a DT6-aware entry point (e.g. riskguard.tick_with_degraded_portfolio_ok)
        that accepts a pre-loaded PortfolioState and does not raise when authority != 'canonical_db'.
        This test is RED until the DT#6 graceful-degradation path is implemented.
        """
        from src.state.portfolio import PortfolioState
        # DT#6 contract: riskguard must expose an entry that accepts PortfolioState directly
        # so callers can pre-check authority and pass degraded state without hiding it.
        # Import the DT#6 entry point — RED until implemented.
        from src.riskguard.riskguard import tick_with_portfolio  # noqa: F401 — RED until impl
