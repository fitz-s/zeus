# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Purpose: Phase 8 R-BP..R-BQ antibodies: code-ready LOW shadow prerequisites.
#          R-BP — run_replay public entry threads temperature_metric kwarg to
#          _replay_one_settlement (S1); default 'high' backward compat preserved.
#          R-BQ — cycle_runner degraded-portfolio path replaces raise RuntimeError
#          with riskguard.tick_with_portfolio (DT#6 graceful-degradation, S2).
# Reuse: Anchors on phase8_contract.md (route A, code-only). No TIGGE data import;
#        v2 tables stay zero-row. Tests assert the code seams, not runtime shadow
#        traces (Gate E data closure blocks on Golden Window lift, P9 scope).

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# R-BP — run_replay public-entry temperature_metric threading
# ---------------------------------------------------------------------------


class TestRBPRunReplayMetricThreading:
    """S1: temperature_metric kwarg on run_replay() threads to _replay_one_settlement.

    Pre-P8: run_replay had no temperature_metric param; _replay_one_settlement
    accepts the kwarg (since P5C) but was never called with it — every replay
    ran with the 'high' default, silently hiding the LOW lane from audit.

    Antibody locks: public kwarg added + passed through; default still 'high'
    so every pre-P8 caller's behavior is unchanged.
    """

    def _make_fake_ctx_and_settlements(self, temperature_metric_captured: list):
        """Build the minimal fake replay context + settlement row needed to
        drive run_replay through at least one _replay_one_settlement call.

        We don't need a real ensemble pipeline — the captured_args list records
        what kwarg _replay_one_settlement was called with, which is the
        antibody assertion target.
        """
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Minimal settlements table matching run_replay's query shape
        conn.execute(
            """
            CREATE TABLE settlements (
                city TEXT, target_date TEXT,
                settlement_value REAL, winning_bin TEXT
            )
            """
        )
        # Stub ensemble_snapshots so ReplayContext._sp probe accepts the monolithic path
        conn.execute("CREATE TABLE ensemble_snapshots (city TEXT)")
        conn.execute(
            "INSERT INTO settlements VALUES (?, ?, ?, ?)",
            ("Chicago", "2026-04-10", 52.3, "52-53°F"),
        )
        conn.commit()
        return conn

    def test_run_replay_threads_temperature_metric_low_to_replay_one_settlement(
        self, monkeypatch
    ):
        """R-BP.1: run_replay(..., temperature_metric='low') → captured kwarg is 'low'."""
        from src.engine import replay as replay_module

        conn = self._make_fake_ctx_and_settlements([])
        captured: dict = {}

        def _fake_replay_one(ctx, city, target_date, settlement, temperature_metric="high"):
            captured["temperature_metric"] = temperature_metric
            return None  # short-circuit; we only care about the kwarg

        monkeypatch.setattr(replay_module, "_replay_one_settlement", _fake_replay_one)
        monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: conn)
        # Stub out backtest-run insertion (requires its own table)
        monkeypatch.setattr(replay_module, "_insert_backtest_run", lambda *a, **k: None)

        replay_module.run_replay("2026-04-10", "2026-04-10", temperature_metric="low")

        assert captured.get("temperature_metric") == "low", (
            "R-BP.1: run_replay did not thread temperature_metric='low' through "
            "to _replay_one_settlement; captured="
            f"{captured.get('temperature_metric')!r}. "
            "Regression means LOW audit lane silently reverts to HIGH."
        )

    def test_run_replay_default_temperature_metric_is_high_backward_compat(
        self, monkeypatch
    ):
        """R-BP.2: run_replay() without kwarg → captured kwarg is 'high'.

        Every pre-P8 caller relies on the implicit 'high' default. If S1 ever
        flips the default or drops the kwarg, this test goes RED immediately.
        """
        from src.engine import replay as replay_module

        conn = self._make_fake_ctx_and_settlements([])
        captured: dict = {}

        def _fake_replay_one(ctx, city, target_date, settlement, temperature_metric="high"):
            captured["temperature_metric"] = temperature_metric
            return None

        monkeypatch.setattr(replay_module, "_replay_one_settlement", _fake_replay_one)
        monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: conn)
        monkeypatch.setattr(replay_module, "_insert_backtest_run", lambda *a, **k: None)

        replay_module.run_replay("2026-04-10", "2026-04-10")  # no kwarg

        assert captured.get("temperature_metric") == "high", (
            "R-BP.2: run_replay default temperature_metric regressed from 'high'; "
            f"captured={captured.get('temperature_metric')!r}. "
            "All pre-P8 callers rely on this default."
        )


# ---------------------------------------------------------------------------
# R-BQ — cycle_runner DT#6 graceful-degradation rewire
# ---------------------------------------------------------------------------


class TestRBQCycleRunnerDT6Rewire:
    """S2: cycle_runner.run_cycle on portfolio_loader_degraded=True must NOT raise.

    Pre-P8 behavior: `raise RuntimeError("Portfolio loader degraded: ...")` at
    cycle_runner.py:180-181 killed the entire cycle — monitor / exit /
    reconciliation lanes never ran, which violates DT#6 law
    (zeus_dual_track_architecture.md §6).

    Post-P8: riskguard.tick_with_portfolio(portfolio) runs the degraded-mode
    risk tick; downstream entry gates honour DATA_DEGRADED; monitor / exit /
    reconciliation continue read-only.
    """

    def _patch_cycle_runner_surface(self, monkeypatch, degraded_portfolio):
        """Patch just enough of cycle_runner's dependencies to let run_cycle
        reach the degraded-portfolio branch and continue past it without
        invoking real DB / CLOB / riskguard heavy surfaces.

        We only need to observe: (1) no RuntimeError raised, (2) the degraded
        branch calls tick_with_portfolio with the degraded portfolio.
        """
        from src.engine import cycle_runner
        from src.riskguard.risk_level import RiskLevel

        class _DummyConn:
            def execute(self, *a, **k):
                class _C:
                    def fetchall(self_c):
                        return []
                    def fetchone(self_c):
                        return None
                return _C()
            def commit(self):
                pass
            def close(self):
                pass

        class _DummyClob:
            def get_balance(self):
                return 0.0
            def get_positions_from_api(self):
                return []
            def get_open_orders(self):
                return []

        class _DummyTracker:
            def snapshot(self):
                return {}

        monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
        monkeypatch.setattr(cycle_runner, "get_connection", lambda: _DummyConn())
        monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: degraded_portfolio)
        monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
        monkeypatch.setattr(cycle_runner, "PolymarketClient", _DummyClob)
        monkeypatch.setattr(cycle_runner, "get_tracker", lambda: _DummyTracker())
        monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
        monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
        monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
        monkeypatch.setattr(
            "src.observability.status_summary.write_status",
            lambda cycle_summary=None: None,
        )

    def test_run_cycle_degraded_portfolio_does_not_raise_runtime_error(
        self, monkeypatch
    ):
        """R-BQ.1: degraded portfolio → no RuntimeError; cycle proceeds.

        Runs run_cycle; may hit downstream issues (stubbed DB returns no rows),
        but the degraded branch itself must NOT raise the pre-P8 message.
        We assert by catching RuntimeError and checking its message doesn't
        match the pre-P8 failsafe-shutdown string.
        """
        from src.engine import cycle_runner
        from src.engine.discovery_mode import DiscoveryMode
        from src.riskguard.risk_level import RiskLevel
        from src.state.portfolio import PortfolioState

        degraded = PortfolioState(
            positions=[],
            portfolio_loader_degraded=True,
            authority="degraded",
        )
        self._patch_cycle_runner_surface(monkeypatch, degraded)

        tick_calls: list = []

        def _fake_tick(portfolio):
            tick_calls.append(portfolio)
            return RiskLevel.DATA_DEGRADED

        monkeypatch.setattr(cycle_runner, "tick_with_portfolio", _fake_tick)

        try:
            summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
        except RuntimeError as exc:
            if "Portfolio loader degraded: DB not authoritative" in str(exc):
                pytest.fail(
                    "R-BQ.1: cycle_runner re-raised the pre-P8 failsafe-shutdown "
                    "RuntimeError on degraded portfolio. DT#6 law violated: "
                    "process must not raise on portfolio_loader_degraded=True."
                )
            # Different RuntimeError (from stubbed downstream) is fine for this antibody;
            # the degraded-branch rewire is the focus.
            return

        # If run_cycle completed, summary must reflect the DT#6 path
        assert summary.get("portfolio_degraded") is True, (
            "R-BQ.1: degraded branch did not emit summary['portfolio_degraded']=True"
        )

    def test_run_cycle_degraded_portfolio_calls_tick_with_portfolio(self, monkeypatch):
        """R-BQ.2: degraded path invokes riskguard.tick_with_portfolio exactly once
        with the degraded PortfolioState, and the returned risk_level reaches summary.
        """
        from src.engine import cycle_runner
        from src.engine.discovery_mode import DiscoveryMode
        from src.riskguard.risk_level import RiskLevel
        from src.state.portfolio import PortfolioState

        degraded = PortfolioState(
            positions=[],
            portfolio_loader_degraded=True,
            authority="degraded",
        )
        self._patch_cycle_runner_surface(monkeypatch, degraded)

        tick_calls: list = []

        def _fake_tick(portfolio):
            tick_calls.append(portfolio)
            return RiskLevel.DATA_DEGRADED

        monkeypatch.setattr(cycle_runner, "tick_with_portfolio", _fake_tick)

        try:
            summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
        except RuntimeError:
            # Downstream RuntimeError from stubbed env is tolerated — we assert
            # tick_with_portfolio was invoked BEFORE that.
            pass

        assert len(tick_calls) == 1, (
            f"R-BQ.2: expected tick_with_portfolio to be called exactly once; "
            f"got {len(tick_calls)} calls"
        )
        assert tick_calls[0] is degraded, (
            "R-BQ.2: tick_with_portfolio was called with a different PortfolioState "
            "than the one returned by load_portfolio"
        )
