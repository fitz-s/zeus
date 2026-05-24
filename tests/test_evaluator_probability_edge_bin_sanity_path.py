# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-23_probability_phantom_edge/IMPL_SPEC_operator.md §B §E (wiring)
# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Evaluator wiring tests — verifies probability_edge_bin_sanity is called per-edge in
#          the non-day0 path and that hard rejections propagate to SIGNAL_QUALITY with the
#          correct reason code.
# Reuse: Run when modifying the evaluator per-edge gate loop or probability_sanity wiring.
"""Evaluator wiring test for probability_edge_bin_sanity (LIVE-PROB-P0 Gate 6 §B).

Verifies:
  (a) probability_edge_bin_sanity called in non-day0 per-edge loop.
  (b) Hard rejection → SIGNAL_QUALITY, reason contains PROBABILITY_TAIL_SHAPE_ANOMALY_HARD.
  (c) §E telemetry columns all non-None on rejection decision.
  (d) day0 guard: gate NOT called for day0 candidate.

RED on origin/main: probability_edge_bin_sanity not imported/wired in evaluator.
GREEN on branch: per-edge gate wired, §E telemetry columns populated.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.engine.evaluator as ev_mod
from src.contracts.no_trade_reason import NoTradeReason
from src.state.portfolio import PortfolioState
from src.strategy.risk_limits import RiskLimits

_NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)

# ── shared test fixtures (minimal copies of inv_prob_tail_sanity fixtures) ──

from tests.test_inv_prob_tail_sanity import (
    _CITY,
    _OUTCOMES_3BIN,
    _FakeClob,
    _FakeEns,
    _FakeAnalysis,
)


def _standard_patches(monkeypatch):
    """Apply the standard infrastructure patches needed by evaluate_candidate."""
    monkeypatch.setattr(ev_mod, "_live_entry_forecast_config_or_blocker", lambda: (None, None))
    monkeypatch.setattr(
        ev_mod,
        "fetch_ensemble",
        lambda *a, **kw: {
            "members_hourly": np.ones((24, 51)) * 95.0,
            "times": [_NOW.isoformat()] * 24,
            "issue_time": _NOW,
            "first_valid_time": _NOW,
            "fetch_time": _NOW,
            "model": "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(ev_mod, "validate_ensemble", lambda *a, **kw: True)
    monkeypatch.setattr(ev_mod, "_entry_forecast_evidence_errors", lambda *a, **kw: [])
    monkeypatch.setattr(ev_mod, "EnsembleSignal", _FakeEns)
    monkeypatch.setattr(ev_mod, "_store_ens_snapshot", lambda *a, **kw: "snap-test")
    monkeypatch.setattr(ev_mod, "_store_snapshot_p_raw", lambda *a, **kw: None)
    fake_cal = object()
    monkeypatch.setattr(ev_mod, "get_calibrator", lambda *a, **kw: (fake_cal, 1))
    monkeypatch.setattr(
        ev_mod,
        "calibrate_and_normalize",
        lambda p_raw, cal, lead_days, bin_widths: p_raw.copy(),
    )
    from src.contracts.alpha_decision import AlphaDecision
    monkeypatch.setattr(
        ev_mod,
        "compute_alpha",
        lambda *a, **kw: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="wiring test",
            ci_bound=0.05,
        ),
    )
    from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
    def _fake_family_scan(analysis, n_bootstrap=0):
        return [FullFamilyHypothesis(
            index=1,
            range_label="90-91F",
            direction="buy_yes",
            edge=0.15,
            ci_lower=0.05,
            ci_upper=0.25,
            p_value=0.01,
            p_model=0.35,
            p_market=0.20,
            p_posterior=0.30,
            entry_price=0.20,
            is_shoulder=False,
            passed_prefilter=True,
        )]
    monkeypatch.setattr(ev_mod, "scan_full_hypothesis_family", _fake_family_scan)
    monkeypatch.setattr(ev_mod, "MarketAnalysis", _FakeAnalysis)


_LIMITS = RiskLimits(
    max_single_position_pct=1.0,
    max_portfolio_heat_pct=1.0,
    max_correlated_pct=1.0,
    max_city_pct=1.0,
    min_order_usd=0.01,
)


def test_probability_edge_bin_sanity_wiring_hard_reject(monkeypatch):
    """probability_edge_bin_sanity called in non-day0 loop; hard rejection populates §E columns.

    RED if: gate not called OR §E telemetry columns None on rejection.
    GREEN if: gate called, SIGNAL_QUALITY rejection, all 11 §E columns non-None.
    """
    _gate_called = []

    def _fake_gate(*, selected_bin_idx, bins, p_raw, p_cal, p_market,
                   direction="", metric="", strategy_key="", market_phase="", config=None):
        _gate_called.append(selected_bin_idx)
        telemetry = {
            "edge_bin_idx": selected_bin_idx,
            "edge_bin_label": f"bin_{selected_bin_idx}",
            "edge_bin_p_raw": 0.01,
            "edge_bin_p_cal": 0.20,
            "edge_bin_p_market": 0.03,
            "edge_bin_member_support": 0.01,
            "edge_bin_odds_ratio": 6.67,
            "near_tail_p_cal": 0.008,
            "near_tail_p_market": 0.002,
            "probability_sanity_mode": "hard",
            "probability_sanity_reason": (
                f"PROBABILITY_TAIL_SHAPE_ANOMALY_HARD:left:idx={selected_bin_idx},"
                f"p_raw=0.0100,p_mkt=0.0300,p_cal=0.2000,ratio=6.67,support=0.0100,"
                f"run_length=2,mode_idx=2"
            ),
        }
        reason = telemetry["probability_sanity_reason"]
        return False, reason, telemetry

    monkeypatch.setattr(ev_mod, "probability_edge_bin_sanity", _fake_gate)
    _standard_patches(monkeypatch)

    candidate = ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=6.0,
        hours_to_resolution=10.0,
        event_id="eb-sanity-wiring-test",
        discovery_mode="center_buy",  # non-day0
        temperature_metric="high",
        observation={
            "high_so_far": 88.0,
            "low_so_far": 72.0,
            "current_temp": 88.0,
            "source": "KDAL",
            "observation_time": _NOW.isoformat(),
            "causality_status": "OK",
        },
    )

    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=_FakeClob(),
        limits=_LIMITS,
        entry_bankroll=1000.0,
        decision_time=_NOW,
    )

    # Gate must have been called (wiring proof)
    assert len(_gate_called) >= 1, (
        f"probability_edge_bin_sanity was NOT called by evaluate_candidate. "
        f"Gate is not wired or is_day0_mode guard incorrect. calls={_gate_called}"
    )

    # Must have at least one SIGNAL_QUALITY rejection from the gate
    signal_rejections = [
        d for d in decisions
        if not d.should_trade and d.rejection_stage == "SIGNAL_QUALITY"
        and d.rejection_reason_enum in (
            NoTradeReason.PROBABILITY_TAIL_SHAPE_ANOMALY_HARD,
            NoTradeReason.PROBABILITY_EDGE_BIN_UNSUPPORTED,
            NoTradeReason.PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT,
            NoTradeReason.PROBABILITY_SANITY_GATE,
        )
    ]
    assert len(signal_rejections) >= 1, (
        f"Expected >=1 SIGNAL_QUALITY eb-sanity rejection. "
        f"Decisions: {[(d.rejection_stage, str(d.rejection_reason_enum)) for d in decisions]}"
    )
    d = signal_rejections[0]

    # §E telemetry: all 11 columns must be non-None on rejection decision
    assert d.probability_sanity_mode is not None, "§E: probability_sanity_mode is None"
    assert d.probability_sanity_reason is not None, "§E: probability_sanity_reason is None"
    assert d.edge_bin_idx is not None, "§E: edge_bin_idx is None"
    assert d.edge_bin_label is not None, "§E: edge_bin_label is None"
    assert d.edge_bin_p_raw is not None, "§E: edge_bin_p_raw is None"
    assert d.edge_bin_p_cal is not None, "§E: edge_bin_p_cal is None"
    assert d.edge_bin_p_market is not None, "§E: edge_bin_p_market is None"
    assert d.edge_bin_member_support is not None, "§E: edge_bin_member_support is None"
    assert d.edge_bin_odds_ratio is not None, "§E: edge_bin_odds_ratio is None"
    assert d.near_tail_p_cal is not None, "§E: near_tail_p_cal is None"
    assert d.near_tail_p_market is not None, "§E: near_tail_p_market is None"

    # PROBABILITY_TAIL_SHAPE_ANOMALY_HARD in reason detail
    assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" in (d.rejection_reason_detail or ""), (
        f"Expected PROBABILITY_TAIL_SHAPE_ANOMALY_HARD in reason. Got: {d.rejection_reason_detail!r}"
    )


def test_probability_edge_bin_sanity_day0_guard(monkeypatch):
    """Antibody: day0 candidate bypasses probability_edge_bin_sanity gate.

    RED if guard `if not is_day0_mode:` removed around gate site.
    """
    _gate_called = []

    def _fake_gate_always_reject(*, selected_bin_idx, bins, p_raw, p_cal, p_market,
                                  direction="", metric="", strategy_key="", market_phase="",
                                  config=None):
        _gate_called.append(selected_bin_idx)
        telemetry = {
            "edge_bin_idx": selected_bin_idx, "edge_bin_label": "", "edge_bin_p_raw": 0.0,
            "edge_bin_p_cal": 0.20, "edge_bin_p_market": 0.03, "edge_bin_member_support": 0.0,
            "edge_bin_odds_ratio": 6.67, "near_tail_p_cal": 0.0, "near_tail_p_market": 0.0,
            "probability_sanity_mode": "hard",
            "probability_sanity_reason": "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD:left:idx=1,...",
        }
        return False, telemetry["probability_sanity_reason"], telemetry

    monkeypatch.setattr(ev_mod, "probability_edge_bin_sanity", _fake_gate_always_reject)
    _standard_patches(monkeypatch)

    candidate = ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=6.0,
        hours_to_resolution=10.0,
        event_id="eb-sanity-day0-guard-test",
        discovery_mode="day0_capture",  # day0 → gate must be bypassed
        temperature_metric="high",
        observation={
            "high_so_far": 88.0,
            "low_so_far": 72.0,
            "current_temp": 88.0,
            "source": "KDAL",
            "observation_time": _NOW.isoformat(),
            "causality_status": "OK",
        },
    )

    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=_FakeClob(),
        limits=_LIMITS,
        entry_bankroll=1000.0,
        decision_time=_NOW,
    )

    assert len(_gate_called) == 0, (
        f"probability_edge_bin_sanity was called for day0 candidate — "
        f"is_day0_mode guard missing. calls={_gate_called}"
    )

    # Confirm no PROBABILITY_TAIL_SHAPE_ANOMALY_HARD rejection (gate bypassed)
    for d in decisions:
        if not d.should_trade:
            reason = d.rejection_reason_detail or ""
            assert "PROBABILITY_TAIL_SHAPE_ANOMALY_HARD" not in reason, (
                f"day0 candidate got PROBABILITY_TAIL_SHAPE_ANOMALY_HARD rejection — gate guard violated. "
                f"decision: {d.rejection_stage} {reason!r}"
            )
