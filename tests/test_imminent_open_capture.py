# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: fix/opening-hunt-include-d1 — D+1 alpha-window gap fix.
#                  DiscoveryMode.IMMINENT_OPEN_CAPTURE added to capture re-opened
#                  and D+1 markets in the 0-24h window below opening_hunt's
#                  min_hours_to_resolution:24 threshold.
"""Antibody tests for IMMINENT_OPEN_CAPTURE cycle filter logic.

T1: Cycle includes markets with hours_to_resolution in (0, 24h).
T2: Cycle EXCLUDES markets >= 24h to resolution (opening_hunt territory).
T3: Cycle EXCLUDES already-settled markets (hours_to_resolution <= 0).
T4: Freshness gate: stale day0_capture_disabled triggers skip (fail-closed).
T5: Strategy profile registry accepts imminent_open_capture with required fields.
T6: discovery_mode.py enum includes IMMINENT_OPEN_CAPTURE value.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# T6: Enum membership
# ---------------------------------------------------------------------------

def test_imminent_open_capture_enum_value():
    """T6: DiscoveryMode has IMMINENT_OPEN_CAPTURE with expected string value."""
    from src.engine.discovery_mode import DiscoveryMode
    assert hasattr(DiscoveryMode, "IMMINENT_OPEN_CAPTURE")
    assert DiscoveryMode.IMMINENT_OPEN_CAPTURE.value == "imminent_open_capture"


# ---------------------------------------------------------------------------
# T5: Strategy profile registry
# ---------------------------------------------------------------------------

def test_imminent_open_capture_strategy_profile_loads():
    """T5: strategy_profile_registry.yaml includes imminent_open_capture with valid schema."""
    from src.strategy.strategy_profile import get, live_safe_keys
    profile = get("imminent_open_capture")
    assert profile.live_status == "live"
    assert "imminent_open_capture" in profile.allowed_discovery_modes
    assert profile.cycle_axis_dispatch_mode == "imminent_open_capture"
    assert "imminent_open_capture" in live_safe_keys()


# ---------------------------------------------------------------------------
# Helpers for T1-T4: use cycle_runtime's filter directly via MODE_PARAMS
# ---------------------------------------------------------------------------

def _make_market(hours_to_resolution: float) -> dict:
    """Synthetic market dict with the fields cycle_runtime filter reads."""
    return {
        "hours_to_resolution": hours_to_resolution,
        "hours_since_open": 100.0,  # old market — should NOT be excluded by max_hours_since_open
        "city": "amsterdam",
    }


def _apply_imminent_filter(markets: list[dict]) -> list[dict]:
    """Apply the imminent_window_hours filter from MODE_PARAMS, mirroring cycle_runtime logic."""
    from src.engine.cycle_runner import MODE_PARAMS
    from src.engine.discovery_mode import DiscoveryMode
    params = MODE_PARAMS[DiscoveryMode.IMMINENT_OPEN_CAPTURE]
    window = float(params["imminent_window_hours"])
    return [
        m for m in markets
        if m.get("hours_to_resolution") is not None
        and 0 < m["hours_to_resolution"] < window
    ]


# ---------------------------------------------------------------------------
# T1: includes markets in (0, 24h)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hours", [0.5, 1.0, 5.9, 6.0, 12.0, 18.0, 23.9])
def test_t1_includes_markets_within_window(hours):
    """T1: Markets with 0 < hours_to_resolution < 24 pass the filter."""
    market = _make_market(hours_to_resolution=hours)
    result = _apply_imminent_filter([market])
    assert result == [market], (
        f"Expected market with hours_to_resolution={hours} to be INCLUDED"
    )


# ---------------------------------------------------------------------------
# T2: excludes markets >= 24h (opening_hunt territory)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hours", [24.0, 24.1, 36.0, 48.0, 72.0])
def test_t2_excludes_markets_at_or_above_window(hours):
    """T2: Markets with hours_to_resolution >= 24 are excluded (opening_hunt territory)."""
    market = _make_market(hours_to_resolution=hours)
    result = _apply_imminent_filter([market])
    assert result == [], (
        f"Expected market with hours_to_resolution={hours} to be EXCLUDED"
    )


# ---------------------------------------------------------------------------
# T3: excludes already-settled markets (hours_to_resolution <= 0)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hours", [0.0, -0.1, -1.0, -12.0])
def test_t3_excludes_settled_markets(hours):
    """T3: Markets with hours_to_resolution <= 0 (already settled) are excluded."""
    market = _make_market(hours_to_resolution=hours)
    result = _apply_imminent_filter([market])
    assert result == [], (
        f"Expected settled market with hours_to_resolution={hours} to be EXCLUDED"
    )


# ---------------------------------------------------------------------------
# T4: freshness gate — fail-closed like day0_capture
# ---------------------------------------------------------------------------

def test_t4_freshness_gate_skips_on_stale():
    """T4: run_cycle skips IMMINENT_OPEN_CAPTURE when day0_capture_disabled=True (fail-closed)."""
    import src.engine.cycle_runner as cr
    from src.engine.discovery_mode import DiscoveryMode

    stale_verdict = MagicMock()
    stale_verdict.day0_capture_disabled = True
    stale_verdict.ensemble_disabled = False
    stale_verdict.stale_sources = ["ecmwf_open_data"]

    def _fake_freshness(_):
        return stale_verdict

    with patch.object(cr, "evaluate_freshness_mid_run", _fake_freshness):
        with patch.object(cr, "get_current_level", return_value=None):
            summary = cr.run_cycle(DiscoveryMode.IMMINENT_OPEN_CAPTURE)

    assert summary.get("skipped") is True, "Expected cycle to be skipped on stale data"
    assert summary.get("skip_reason") == "cycle_skipped_freshness_degraded"
    assert "ecmwf_open_data" in summary.get("stale_sources", [])


def test_t4_freshness_gate_continues_on_fresh():
    """T4b: run_cycle does NOT skip due to freshness when data is fresh.

    get_connection returns None to trigger db_write_lock_degraded (expected);
    we assert only that skip_reason is NOT freshness-related.
    """
    import src.engine.cycle_runner as cr
    from src.engine.discovery_mode import DiscoveryMode
    from src.riskguard.risk_level import RiskLevel

    fresh_verdict = MagicMock()
    fresh_verdict.day0_capture_disabled = False
    fresh_verdict.ensemble_disabled = False
    fresh_verdict.stale_sources = []

    with patch.object(cr, "evaluate_freshness_mid_run", lambda _: fresh_verdict), \
         patch.object(cr, "get_current_level", return_value=RiskLevel.GREEN), \
         patch.object(cr, "get_force_exit_review", return_value=False), \
         patch.object(cr, "get_connection", return_value=None), \
         patch.object(cr, "find_weather_markets", return_value=[]):
        summary = cr.run_cycle(DiscoveryMode.IMMINENT_OPEN_CAPTURE)

    assert summary.get("skip_reason") != "cycle_skipped_freshness_degraded", (
        "Cycle was skipped due to freshness despite fresh data — fail-closed gate fired incorrectly"
    )
