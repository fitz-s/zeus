# Lifecycle: created=2026-04-18; last_reviewed=2026-04-25; last_reused=2026-04-25
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

    def _make_fake_ctx_and_settlements(
        self,
        temperature_metric_captured: list,
        *,
        settlement_metric: str = "high",
    ):
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
                settlement_value REAL, winning_bin TEXT,
                temperature_metric TEXT
            )
            """
        )
        # Stub ensemble_snapshots so ReplayContext._sp probe accepts the monolithic path
        conn.execute("CREATE TABLE ensemble_snapshots (city TEXT)")
        conn.execute(
            """
            CREATE TABLE market_events (
                city TEXT, target_date TEXT, range_label TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO settlements VALUES (?, ?, ?, ?, ?)",
            ("Chicago", "2026-04-10", 52.3, "52-53°F", settlement_metric),
        )
        conn.execute(
            "INSERT INTO market_events VALUES (?, ?, ?)",
            ("Chicago", "2026-04-10", "52-53°F"),
        )
        conn.commit()
        return conn

    def test_run_replay_threads_temperature_metric_low_to_replay_one_settlement(
        self, monkeypatch
    ):
        """R-BP.1: run_replay(..., temperature_metric='low') → captured kwarg is 'low'."""
        from src.engine import replay as replay_module

        conn = self._make_fake_ctx_and_settlements([], settlement_metric="low")
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
        """R-BQ.1 (P9A structural-hardened): degraded portfolio → ANY RuntimeError
        escaping run_cycle is a DT#6 violation.

        Pre-P9A shape (carried two critic-carol P8 MAJOR findings):
          - MAJOR-3 silent-return: `except RuntimeError: return` bypassed the
            unconditional summary assertion on any non-pre-P8-string RuntimeError
          - MAJOR-4 text-match: literal pre-P8 message `"Portfolio loader degraded:
            DB not authoritative"` locked the antibody to one specific wording;
            a reworded re-raise would silent-pass

        Post-P9A shape (structural):
          - ANY RuntimeError = violation (structural immunity, not text-match)
          - summary assertion runs unconditionally (no silent bypass)
          - Covers both pre-P8 guard reintroduction AND new downstream RuntimeError
            surfacing in the degraded path
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
            pytest.fail(
                f"R-BQ.1 (P9A hardened): DT#6 branch must NOT raise RuntimeError "
                f"of any kind. Pre-P8 raised the specific 'Failsafe subsystem "
                f"shutdown' string; post-P8 rewire replaced that with "
                f"riskguard.tick_with_portfolio. Any RuntimeError reaching this "
                f"except clause means either (a) the pre-P8 guard was "
                f"reintroduced (possibly with reworded text — caught structurally, "
                f"not by string-match), or (b) downstream degraded-path code "
                f"developed a new RuntimeError. Both violate DT#6 law in "
                f"zeus_dual_track_architecture.md §6. Got: {type(exc).__name__}: {exc}"
            )

        # UNCONDITIONAL assertion — no silent-return bypass (P9A MAJOR-3 fix)
        assert summary.get("portfolio_degraded") is True, (
            f"R-BQ.1: degraded branch must emit summary['portfolio_degraded']=True. "
            f"Got summary={summary}"
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


# ---------------------------------------------------------------------------
# R-BS — Phase 9A: save_portfolio preserves positions+bankroll under degraded mode
# ---------------------------------------------------------------------------


class TestRBSSavePortfolioDegradedRoundtrip:
    """Phase 9A R-BS: authority-tag propagation antibody (DT#6 Interpretation B).

    DT#6 Interpretation B (user ruling 2026-04-18 + phase9a_contract.md):
    "read-only" means no NEW canonical-state entries (position creation, new
    risk policy changes); NOT "no JSON cache refresh at all".

    save_portfolio(degraded) is a sneaky provenance trap flagged by critic-carol
    in P8 MAJOR/learning 6 — if the degraded PortfolioState gets JSON-written
    without the authority field being re-derivable at load-time, downstream
    consumers may see a VERIFIED truth tag on degraded-mode data.

    Current code (src/state/portfolio.py:684, 1048-1091):
    - state.authority is a runtime signal derived at load time from DB availability
    - save_portfolio serializes positions/bankroll/etc but NOT state.authority
    - The JSON's _truth_authority annotation uses _TRUTH_AUTHORITY_MAP to map
      state.authority → persisted truth tag; readers interpret that tag
    - Roundtrip: degraded save → JSON has positions+bankroll; next load derives
      authority fresh from DB state (not from JSON) — so authority is re-derived,
      not round-tripped

    This antibody locks the *behavior* of save_portfolio(authority="degraded"):
    it MUST complete without raising, positions and bankroll MUST be preserved
    exactly, and the JSON MUST be readable by load_portfolio.
    """

    def test_save_portfolio_degraded_preserves_positions_and_bankroll(self, tmp_path):
        """R-BS.1: save_portfolio(degraded) → written JSON preserves positions+bankroll.

        Roundtrip smoke: construct degraded PortfolioState, save to tmp path,
        read raw JSON, assert positions/bankroll faithful. authority tag is
        re-derived at load (not serialized), so we don't assert its persistence.
        """
        import json
        from src.state.portfolio import PortfolioState, save_portfolio

        degraded = PortfolioState(
            positions=[],
            bankroll=150.0,
            portfolio_loader_degraded=True,
            authority="degraded",
        )

        save_path = tmp_path / "positions-test-degraded.json"
        save_portfolio(degraded, path=save_path)

        # File must exist and be valid JSON
        assert save_path.exists(), "R-BS.1: save_portfolio(degraded) did not write the file"
        data = json.loads(save_path.read_text())

        # Positions + bankroll faithful
        assert data["positions"] == [], (
            f"R-BS.1: positions mangled on degraded save. Got: {data['positions']}"
        )
        assert data["bankroll"] == 150.0, (
            f"R-BS.1: bankroll mangled on degraded save. Got: {data['bankroll']}"
        )

    def test_save_portfolio_degraded_truth_annotation_present(self, tmp_path):
        """R-BS.2: save_portfolio(degraded) → JSON carries a _truth_authority
        annotation (any value). Antibody locks that the truth-payload
        annotation seam is exercised for degraded saves, so future changes
        to _TRUTH_AUTHORITY_MAP surface in review (not silent corruption).

        Note: this test intentionally does NOT assert a specific truth_authority
        value — the mapping (degraded → VERIFIED today) is a design call logged
        in _TRUTH_AUTHORITY_MAP (src/state/portfolio.py:47-51). If the mapping
        changes, update the expected value here; failing loudly is the antibody.
        """
        import json
        from src.state.portfolio import PortfolioState, save_portfolio

        degraded = PortfolioState(
            positions=[],
            bankroll=150.0,
            portfolio_loader_degraded=True,
            authority="degraded",
        )

        save_path = tmp_path / "positions-test-degraded-truth.json"
        save_portfolio(degraded, path=save_path)

        data = json.loads(save_path.read_text())

        # The truth annotation must be present — may be nested under a wrapper
        # key depending on annotate_truth_payload structure. Search top-level
        # keys for any "authority" or "truth" token.
        truth_found = False
        for key in data:
            if "authority" in key.lower() or "truth" in key.lower():
                truth_found = True
                break

        # P9A-close fix (critic-carol cycle-2 MINOR-1): prior shape asserted
        # `save_path.exists()` which was trivially true after any successful
        # save — R-BS.2 was a checkbox antibody. Now assert the keyword search
        # result unconditionally so any future refactor that drops
        # annotate_truth_payload from save_portfolio fails loudly.
        assert truth_found, (
            f"R-BS.2: no truth/authority annotation key found in saved JSON. "
            f"Top-level keys: {list(data.keys())!r}. "
            f"If annotate_truth_payload was intentionally removed from save_portfolio, "
            f"update this test and _TRUTH_AUTHORITY_MAP review note at DT#6 §B."
        )


# ---------------------------------------------------------------------------
# R-BT — Phase 9A: entries_blocked_reason populated for DATA_DEGRADED
# ---------------------------------------------------------------------------


class TestRBTEntriesBlockedReasonDegraded:
    """Phase 9A R-BT: degraded-mode cycle populates entries_blocked_reason.

    Pre-P9A finding (critic-carol P8 MAJOR-1): `entries_blocked_reason` elif
    tuple at cycle_runner.py:281 excluded `RiskLevel.DATA_DEGRADED`, so
    degraded cycles silently blocked entries with `entries_blocked_reason=None`.
    Ops dashboards / Discord reports / runbook automation depending on the
    reason-code field saw "no reason" despite entries being blocked.

    P9A fix: widen the elif tuple to include DATA_DEGRADED. This antibody
    locks the fix: any degraded-cycle must emit
    `summary["entries_blocked_reason"] == "risk_level=DATA_DEGRADED"`.
    """

    def _patch_cycle_runner_surface(self, monkeypatch, degraded_portfolio):
        """Shared patcher — mirrors TestRBQCycleRunnerDT6Rewire's fixture."""
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
        monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, **kw: None)
        monkeypatch.setattr(cycle_runner, "PolymarketClient", _DummyClob)
        monkeypatch.setattr(cycle_runner, "get_tracker", lambda: _DummyTracker())
        monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
        monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
        monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
        monkeypatch.setattr(
            "src.observability.status_summary.write_status",
            lambda cycle_summary=None: None,
        )

    def test_run_cycle_degraded_sets_entries_blocked_reason_data_degraded(
        self, monkeypatch
    ):
        """R-BT: degraded cycle must emit entries_blocked_reason reflecting
        DATA_DEGRADED. Pre-P9A: None (silent block). Post-P9A: risk_level=DATA_DEGRADED.
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

        def _fake_tick(portfolio):
            return RiskLevel.DATA_DEGRADED

        monkeypatch.setattr(cycle_runner, "tick_with_portfolio", _fake_tick)

        try:
            summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
        except RuntimeError as exc:
            pytest.fail(
                f"R-BT: degraded cycle must not raise RuntimeError. Got: "
                f"{type(exc).__name__}: {exc}"
            )

        reason = summary.get("entries_blocked_reason")
        # Accept both forms:
        # - "risk_level=DATA_DEGRADED" (from the widened elif tuple at L281)
        # - "portfolio_loader_degraded" (alternative reason-code design)
        # Either is acceptable — the antibody locks that A reason is emitted,
        # not the specific string.
        assert reason is not None, (
            f"R-BT: degraded cycle must emit summary['entries_blocked_reason']. "
            f"Pre-P9A behavior: reason=None (silent block). Post-P9A fix: widen "
            f"elif tuple at cycle_runner.py:281 to include DATA_DEGRADED. "
            f"Got summary={summary}"
        )
        assert "degraded" in reason.lower() or "data_degraded" in reason.lower(), (
            f"R-BT: entries_blocked_reason must reflect degraded state; got {reason!r}"
        )


# ---------------------------------------------------------------------------
# R-BU — Phase 9A: run_replay mode+metric mismatch warning
# ---------------------------------------------------------------------------


class TestRBURunReplayModeMetricWarning:
    """Phase 9A MINOR M2: run_replay emits a warning when temperature_metric is
    passed to a mode that ignores the public replay metric kwarg.

    WU sweep remains intentionally HIGH-only. Trade-history audit ignores this
    public kwarg, but its settlement comparison is metric-aware through each
    stored `position_current.temperature_metric`.
    """

    def test_run_replay_warns_on_metric_with_wu_sweep_mode(self, monkeypatch, caplog):
        """R-BU.1: logger.warning emitted for WU_SWEEP_LANE + non-default metric."""
        import logging
        from src.engine import replay as replay_module

        # Short-circuit the sweep — just need the warning to fire before the lane
        monkeypatch.setattr(
            replay_module, "run_wu_settlement_sweep",
            lambda *a, **k: replay_module.ReplaySummary(
                run_id="t", mode="wu_settlement_sweep",
                date_range=("2026-04-10", "2026-04-10"),
                n_settlements=0, overrides={},
            ),
        )

        with caplog.at_level(logging.WARNING):
            replay_module.run_replay(
                "2026-04-10", "2026-04-10",
                mode=replay_module.WU_SWEEP_LANE,
                temperature_metric="low",
            )

        # Assert a warning was emitted mentioning the dropped metric kwarg
        warnings_matching = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "temperature_metric" in rec.getMessage()
        ]
        assert warnings_matching, (
            "R-BU.1: run_replay(mode=WU_SWEEP_LANE, temperature_metric='low') "
            "must emit a logger.warning about the silently-dropped kwarg. "
            f"Got warnings: {[r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]}"
        )

    def test_run_replay_no_warning_on_default_metric(self, monkeypatch, caplog):
        """R-BU.2: no warning when mode=WU_SWEEP_LANE and temperature_metric='high'
        (the default — nothing is being silently dropped)."""
        import logging
        from src.engine import replay as replay_module

        monkeypatch.setattr(
            replay_module, "run_wu_settlement_sweep",
            lambda *a, **k: replay_module.ReplaySummary(
                run_id="t", mode="wu_settlement_sweep",
                date_range=("2026-04-10", "2026-04-10"),
                n_settlements=0, overrides={},
            ),
        )

        with caplog.at_level(logging.WARNING):
            replay_module.run_replay(
                "2026-04-10", "2026-04-10",
                mode=replay_module.WU_SWEEP_LANE,
                # no temperature_metric kwarg — default "high"
            )

        mismatch_warnings = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "temperature_metric" in rec.getMessage()
        ]
        assert not mismatch_warnings, (
            "R-BU.2: default temperature_metric='high' + WU_SWEEP_LANE must NOT "
            f"trigger the mismatch warning. Got: {[r.getMessage() for r in mismatch_warnings]}"
        )
