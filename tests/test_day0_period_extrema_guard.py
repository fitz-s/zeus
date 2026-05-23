# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Antibody — evaluate_candidate must reject day0 period_extrema (P0-3 guard)
# Reuse: Verify P0-3 guard at evaluator.py line ~3444 is intact before relying on this test
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §P0-3
"""P0-3 antibody: evaluate_candidate must reject when day0 + period_extrema.

End-to-end test driving evaluate_candidate (the real production entry point),
NOT _make_rejection_decision directly.  The guard fires inside evaluate_candidate
before any access to ens_result['members_hourly'].

RED/GREEN stash cycle (see REPORT section):
- Stash the P0-3 guard in evaluator.py → evaluate_candidate raises KeyError on
  'members_hourly' (absent in period_extrema bundles), tests FAIL RED.
- Restore guard → tests PASS GREEN.

Covers:
  - HIGH path (mx2t3): DAY0_NO_FORECAST_HOURS_REMAIN + DAY0_REMAINING_WINDOW_UNAVAILABLE detail
  - LOW path (mn2t3): same guard, metric-agnostic
  - Hourly path (no period_extrema): guard absent, evaluate_candidate proceeds past guard
"""
from __future__ import annotations

import types
from datetime import datetime, timezone

import numpy as np
import pytest

from src.config import City
from src.contracts.no_trade_reason import NoTradeReason
import src.engine.evaluator as ev_mod
from src.state.portfolio import PortfolioState
from src.strategy.risk_limits import RiskLimits


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)

_CITY = City(
    name="Amsterdam",
    lat=52.3086,
    lon=4.7639,
    timezone="Europe/Amsterdam",
    settlement_unit="C",
    cluster="Amsterdam",
    wu_station="EHAM",
)

# Celsius bins: 1°C wide interior, open shoulders on both ends, contiguous partition.
# Topology requires: leftmost=open-low, rightmost=open-high, gap-free adjacency.
_OUTCOMES_3BIN = [
    {"title": "19°C or lower", "range_low": None, "range_high": 19,
     "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
    {"title": "20°C", "range_low": 20, "range_high": 20,
     "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
    {"title": "21°C or higher", "range_low": 21, "range_high": None,
     "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
]

_OBS_STUB = {
    "high_so_far": 19.5,
    "low_so_far": 12.0,
    "current_temp": 19.5,
    "source": "EHAM",
    "observation_time": _NOW.isoformat(),
    "causality_status": "OK",
}

# 51 period_extrema members — intentionally NO members_hourly or times keys
_ENS_PERIOD_EXTREMA = {
    "period_extrema_members": [22.5, 23.0, 21.8, 24.1, 22.0] * 10 + [22.5],
    "fetch_time": _NOW,
    "members_unit": "degC",
}


def _patch_day0_path(monkeypatch, ens_result: dict) -> None:
    """Minimal patches to get evaluate_candidate through early checks to the P0-3 guard.

    Does NOT patch remaining_member_extrema_for_day0 — if the guard is deleted,
    evaluate_candidate will crash on ens_result['members_hourly'] (KeyError),
    making tests RED.
    """
    monkeypatch.setattr(ev_mod, "_day0_observation_source_rejection_reason", lambda *a, **kw: None)
    monkeypatch.setattr(ev_mod, "_day0_observation_quality_rejection_reason", lambda *a, **kw: None)
    monkeypatch.setattr(ev_mod, "fetch_ensemble", lambda *a, **kw: ens_result)
    monkeypatch.setattr(ev_mod, "validate_ensemble", lambda *a, **kw: True)
    # Bypass evidence-field validation — not under test here; P0-3 guard fires after it.
    monkeypatch.setattr(ev_mod, "_entry_forecast_evidence_errors", lambda *a, **kw: [])
    monkeypatch.setattr(
        ev_mod,
        "_get_day0_temporal_context",
        lambda *a, **kw: types.SimpleNamespace(
            current_utc_timestamp=_NOW,
            daypart="afternoon",
        ),
    )


def _make_day0_candidate(temperature_metric: str) -> ev_mod.MarketCandidate:
    """temperature_metric: 'high' or 'low' (string, as MarketCandidate expects)."""
    return ev_mod.MarketCandidate(
        city=_CITY,
        target_date="2026-05-23",
        outcomes=_OUTCOMES_3BIN,
        hours_since_open=2.0,
        hours_to_resolution=4.0,
        event_id="p0-3-test",
        discovery_mode="day0_capture",
        temperature_metric=temperature_metric,
        observation=_OBS_STUB,
    )


# ---------------------------------------------------------------------------
# P0-3 guard: HIGH path (mx2t3)
# ---------------------------------------------------------------------------

def test_day0_period_extrema_rejects_high_via_evaluate_candidate(monkeypatch):
    """evaluate_candidate with day0+period_extrema returns DAY0_NO_FORECAST_HOURS_REMAIN
    with DAY0_REMAINING_WINDOW_UNAVAILABLE in the detail — driven through the real
    production entry point, not through _make_rejection_decision directly.

    RED when guard deleted: evaluate_candidate raises KeyError('members_hourly').
    GREEN when guard present: returns rejection with correct reason + detail.
    """
    _patch_day0_path(monkeypatch, _ENS_PERIOD_EXTREMA)

    candidate = _make_day0_candidate("high")
    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=None,
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

    assert len(decisions) == 1
    d = decisions[0]
    assert d.should_trade is False, f"expected rejection, got should_trade=True"
    assert d.rejection_reason_enum == NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN, (
        f"expected DAY0_NO_FORECAST_HOURS_REMAIN, got {d.rejection_reason_enum!r}"
    )
    assert d.rejection_stage == "SIGNAL_QUALITY"
    detail = d.rejection_reason_detail or ""
    assert "DAY0_REMAINING_WINDOW_UNAVAILABLE" in detail, (
        f"rejection_reason_detail must contain DAY0_REMAINING_WINDOW_UNAVAILABLE; got: {detail!r}"
    )
    assert "period_extrema" in detail.lower(), (
        f"rejection_reason_detail must mention period_extrema; got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# P0-3 guard: LOW path (mn2t3 symmetry)
# ---------------------------------------------------------------------------

def test_day0_period_extrema_rejects_low_via_evaluate_candidate(monkeypatch):
    """LOW symmetry: mn2t3 whole-day period min is equally invalid for
    remaining-window estimation.  Same guard fires for LOW_LOCALDAY_MIN.

    RED when guard deleted: KeyError('members_hourly') from evaluate_candidate.
    GREEN when guard present: DAY0_NO_FORECAST_HOURS_REMAIN + UNAVAILABLE detail.
    """
    _patch_day0_path(monkeypatch, _ENS_PERIOD_EXTREMA)

    candidate = _make_day0_candidate("low")
    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=None,
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

    assert len(decisions) == 1
    d = decisions[0]
    assert d.should_trade is False
    assert d.rejection_reason_enum == NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN
    detail = d.rejection_reason_detail or ""
    assert "DAY0_REMAINING_WINDOW_UNAVAILABLE" in detail, (
        f"LOW path must carry DAY0_REMAINING_WINDOW_UNAVAILABLE in detail; got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# Control: hourly path must NOT be rejected by the period_extrema guard
# ---------------------------------------------------------------------------

def test_day0_hourly_path_not_blocked_by_period_extrema_guard(monkeypatch):
    """When period_extrema_members is absent the guard must not fire.

    evaluate_candidate with hourly ens_result passes the guard site.
    This confirms the guard is conditional (not a blanket day0 rejection).
    """
    hourly_ens = {
        "members_hourly": np.ones((24, 51)) * 20.0,
        "times": [
            datetime(2026, 5, 23, h, 0, tzinfo=timezone.utc).isoformat()
            for h in range(24)
        ],
        "fetch_time": _NOW,
        "n_members": 51,
    }

    _patch_day0_path(monkeypatch, hourly_ens)

    from src.signal.day0_extrema import RemainingMemberExtrema
    monkeypatch.setattr(
        ev_mod,
        "remaining_member_extrema_for_day0",
        lambda *a, **kw: (RemainingMemberExtrema(maxes=np.ones(51) * 20.0, mins=None), 2.0),
    )

    candidate = _make_day0_candidate("high")
    decisions = ev_mod.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=1000.0),
        clob=None,
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

    assert len(decisions) == 1
    d = decisions[0]
    detail = d.rejection_reason_detail or ""
    assert "DAY0_REMAINING_WINDOW_UNAVAILABLE" not in detail, (
        "Period_extrema guard must not fire for hourly ens_result"
    )
    if d.rejection_reason_enum == NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN:
        assert "period_extrema" not in detail.lower(), (
            "Hourly-path DAY0_NO_FORECAST_HOURS_REMAIN rejection must not reference period_extrema"
        )
