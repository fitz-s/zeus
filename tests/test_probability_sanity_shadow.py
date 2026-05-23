# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: docs/reports/live_review_may23.md §P0-D
"""P0-D antibody: shadow sanity telemetry for non-day0 forecast strategies.

End-to-end tests driving evaluate_candidate (the real production entry point).

Covers:
  - Non-day0 pathological distribution → evaluate_candidate LOGS
    [PROBABILITY_SANITY_SHADOW] AND returns a non-rejection (shadow, not block).
  - Day0 HIGH same pathological distribution → evaluate_candidate returns
    PROBABILITY_SANITY_GATE rejection (hard gate preserved).

RED/GREEN cycle (see inline REPORT comments):
  P0-D shadow: delete the else-block (lines 4571-4588 of evaluator.py) →
    test_non_day0_shadow_logs_and_does_not_block must FAIL because no
    [PROBABILITY_SANITY_SHADOW] line appears in caplog.  Restore → PASS.
"""
from __future__ import annotations

import logging
import types
from datetime import datetime, timezone

import numpy as np
import pytest

from src.contracts.alpha_decision import AlphaDecision
from src.contracts.no_trade_reason import NoTradeReason
from src.config import City
import src.engine.evaluator as ev_mod
from src.state.portfolio import PortfolioState
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
from src.strategy.risk_limits import RiskLimits
from src.types import BinEdge


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc)

# Fahrenheit city (Dallas) — matches test_execution_price.py pattern; avoids
# Celsius topology strictness on the fake bins produced by FakeEns/FakeAnalysis.
_CITY = City(
    name="Dallas",
    lat=32.8998,
    lon=-97.0403,
    timezone="America/Chicago",
    settlement_unit="F",
    cluster="Dallas",
    wu_station="KDAL",
)

# 3-bin Fahrenheit partition: open-low, 2°F interior, open-high.
# Fahrenheit non-shoulder bins must be exactly 2°F wide.
_OUTCOMES_3BIN = [
    {"title": "89°F or lower", "range_low": None, "range_high": 89,
     "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
    {"title": "90-91°F", "range_low": 90, "range_high": 91,
     "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
    {"title": "92°F or higher", "range_low": 92, "range_high": None,
     "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
]


# ---------------------------------------------------------------------------
# Fake ensemble/analysis classes
# ---------------------------------------------------------------------------

class _FakeEns:
    """Minimal EnsembleSignal stub.  member_extrema drives analysis_member_extrema."""
    # All 51 members at 95°F — well above the bins, so FakeAnalysis drives p_raw.
    member_extrema = np.ones(51) * 95.0
    member_maxes = member_extrema  # legacy alias
    temperature_metric = None
    bias_corrected = False

    def __init__(self, *args, **kwargs):
        pass

    def spread_float(self):
        return 0.0

    def spread(self):
        from src.types.temperature import TemperatureDelta
        return TemperatureDelta(0.0, "F")

    def is_bimodal(self):
        return False

    def p_raw_vector(self, bins, **kwargs):
        # Concentrated on middle bin — pathological (high p on one bin, zero support)
        return np.array([0.05, 0.90, 0.05])


class _FakeAnalysis:
    def __init__(self, *args, **kwargs):
        self.bins = kwargs["bins"]
        self.member_maxes = np.ones(51) * 95.0
        self.entry_method = "ens_member_counting"
        self.selected_method = "ens_member_counting"

    def forecast_context(self):
        return {"uncertainty": {}, "location": {}}

    def find_edges(self, n_bootstrap):
        selected_bin = self.bins[1]
        return [
            BinEdge(
                bin=selected_bin,
                direction="buy_yes",
                edge=0.20,
                ci_lower=0.05,
                ci_upper=0.25,
                p_model=0.70,
                p_market=0.40,
                p_posterior=0.60,
                entry_price=0.40,
                p_value=0.001,
                vwmp=0.40,
            )
        ]


class _FakeClob:
    def get_best_bid_ask(self, token_id):
        return (0.39, 0.41, 10.0, 10.0)

    def get_fee_rate(self, token_id):
        return 0.00


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

_OBS_STUB = {
    "high_so_far": 88.0,
    "low_so_far": 72.0,
    "current_temp": 88.0,
    "source": "KDAL",
    "observation_time": _NOW.isoformat(),
    "causality_status": "OK",
}


def _make_candidate(discovery_mode: str) -> ev_mod.MarketCandidate:
    return ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=6.0,
        hours_to_resolution=10.0,
        event_id="p0-d-test",
        discovery_mode=discovery_mode,
        temperature_metric="high",
        observation=_OBS_STUB,
    )


def _patch_non_day0_path(monkeypatch) -> None:
    """Patch evaluate_candidate through to the shadow sanity block.

    validate_high_distribution is patched to return (False, "SHADOW_GATE_FIRED")
    so we can confirm the shadow log fires without relying on real distribution logic.
    """
    # Disable entry_forecast_cfg so use_executable_forecast_cutover=False;
    # the test path uses the legacy fetch_ensemble route (not DB reader).
    monkeypatch.setattr(
        ev_mod,
        "_live_entry_forecast_config_or_blocker",
        lambda: (None, None),
    )
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
    monkeypatch.setattr(ev_mod, "_store_ens_snapshot", lambda *a, **kw: "snap-p0d-test")
    monkeypatch.setattr(ev_mod, "_store_snapshot_p_raw", lambda *a, **kw: None)
    # cal_level must be < 4 and cal must not be None to pass the maturity gate.
    # A sentinel object suffices; calibrate_and_normalize is also patched.
    _fake_cal = object()
    monkeypatch.setattr(ev_mod, "get_calibrator", lambda *a, **kw: (_fake_cal, 1))
    monkeypatch.setattr(
        ev_mod,
        "calibrate_and_normalize",
        lambda p_raw, cal, lead_days, bin_widths: p_raw.copy(),
    )
    monkeypatch.setattr(
        ev_mod,
        "compute_alpha",
        lambda *a, **kw: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="p0-d test",
            ci_bound=0.05,
        ),
    )
    monkeypatch.setattr(ev_mod, "MarketAnalysis", _FakeAnalysis)
    monkeypatch.setattr(
        ev_mod,
        "validate_high_distribution",
        lambda *a, **kw: (False, "SHADOW_GATE_FIRED"),
    )

    def _fake_scan(analysis, *args, **kwargs):
        if False: _ = analysis.entry_method; _ = analysis.selected_method
        return [
            FullFamilyHypothesis(
                index=1,
                range_label=edge.bin.label,
                direction=edge.direction,
                edge=edge.edge,
                ci_lower=edge.ci_lower,
                ci_upper=edge.ci_upper,
                p_value=edge.p_value,
                p_model=edge.p_model,
                p_market=edge.p_market,
                p_posterior=edge.p_posterior,
                entry_price=edge.entry_price,
                is_shoulder=False,
                passed_prefilter=True,
            )
            for edge in analysis.find_edges(n_bootstrap=kwargs.get("n_bootstrap", 0))
        ]

    monkeypatch.setattr(ev_mod, "scan_full_hypothesis_family", _fake_scan)
    monkeypatch.setattr(ev_mod, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
    monkeypatch.setattr(ev_mod, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(ev_mod, "kelly_size", lambda p_posterior, entry_price, bankroll, kelly_mult, **kw: entry_price)
    monkeypatch.setattr(ev_mod, "check_position_allowed", lambda **kwargs: (True, "OK"))


def _run_evaluate(monkeypatch, discovery_mode: str):
    candidate = _make_candidate(discovery_mode)
    return ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=_FakeClob(),
        limits=RiskLimits(
            max_single_position_pct=1.0,
            max_portfolio_heat_pct=1.0,
            max_correlated_pct=1.0,
            max_city_pct=1.0,
            min_order_usd=0.01,
        ),
        entry_bankroll=1000.0,
        decision_time=_NOW,
    )


# ---------------------------------------------------------------------------
# P0-D shadow: non-day0 logs but does not block
# ---------------------------------------------------------------------------

def test_non_day0_shadow_logs_and_does_not_block(monkeypatch, caplog):
    """Non-day0 candidate with pathological distribution → evaluate_candidate
    logs [PROBABILITY_SANITY_SHADOW] and does NOT return a PROBABILITY_SANITY_GATE
    rejection.  Shadow is log-only; trade path continues.

    RED when shadow else-block deleted: no [PROBABILITY_SANITY_SHADOW] in caplog
    → assert fails.
    GREEN when shadow block present: warning emitted, test passes.
    """
    _patch_non_day0_path(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        decisions = _run_evaluate(monkeypatch, discovery_mode="center_buy")

    # Shadow log must appear
    shadow_lines = [
        r.message for r in caplog.records
        if "[PROBABILITY_SANITY_SHADOW]" in r.message
    ]
    assert len(shadow_lines) >= 1, (
        f"[PROBABILITY_SANITY_SHADOW] log line must appear for non-day0 "
        f"pathological distribution; caplog records: {[r.message for r in caplog.records]}"
    )

    # Must NOT be a hard-gate rejection
    assert len(decisions) >= 1
    for d in decisions:
        assert d.rejection_reason_enum != NoTradeReason.PROBABILITY_SANITY_GATE, (
            "Shadow branch must NOT block (non-day0); got PROBABILITY_SANITY_GATE rejection"
        )


# ---------------------------------------------------------------------------
# Day0 HIGH hard gate preserved
# ---------------------------------------------------------------------------

def test_day0_high_hard_gate_still_blocks(monkeypatch):
    """Day0 HIGH with pathological distribution → evaluate_candidate returns
    PROBABILITY_SANITY_GATE rejection (hard gate unaffected by P0-D changes).

    validate_high_distribution returns (False, "SHADOW_GATE_FIRED") in both paths;
    day0 HIGH must still block.
    """
    _patch_non_day0_path(monkeypatch)

    # Day0-specific patches
    from src.signal.day0_extrema import RemainingMemberExtrema
    monkeypatch.setattr(ev_mod, "_day0_observation_source_rejection_reason", lambda *a, **kw: None)
    monkeypatch.setattr(ev_mod, "_day0_observation_quality_rejection_reason", lambda *a, **kw: None)
    monkeypatch.setattr(
        ev_mod,
        "remaining_member_extrema_for_day0",
        lambda *a, **kw: (RemainingMemberExtrema(maxes=np.ones(51) * 95.0, mins=None), 6.0),
    )
    monkeypatch.setattr(
        ev_mod,
        "_get_day0_temporal_context",
        lambda *a, **kw: types.SimpleNamespace(current_utc_timestamp=_NOW, daypart="afternoon"),
    )

    class _FakeDay0Signal:
        def p_vector(self, bins, **kw):
            return np.array([0.05, 0.90, 0.05])
        def forecast_context(self):
            return {}

    class _FakeDay0Router:
        @staticmethod
        def route(inputs):
            return _FakeDay0Signal()

    monkeypatch.setattr(ev_mod, "Day0Router", _FakeDay0Router)

    # Prevent nowcast store DB access (no forecasts DB in test environment)
    import src.state.day0_nowcast_store as _nowcast_mod
    monkeypatch.setattr(_nowcast_mod, "read_latest_platt_fit", lambda *a, **kw: None)

    decisions = _run_evaluate(monkeypatch, discovery_mode="day0_capture")

    assert len(decisions) == 1
    d = decisions[0]
    assert d.should_trade is False
    assert d.rejection_reason_enum == NoTradeReason.PROBABILITY_SANITY_GATE, (
        f"Day0 HIGH hard gate must block; got {d.rejection_reason_enum!r}"
    )
