# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: freshness contract — silent-fallback elimination
"""Tests for C: cycle_runner.evaluate_freshness_mid_run crash → fail-closed discipline.

Invariant: when evaluate_freshness_mid_run raises an Exception, freshness is UNKNOWN.
UNKNOWN must never be treated as FRESH.

  * For fail-closed modes (settlement_day_dispatch_for_mode=True, e.g. DAY0_CAPTURE, or
    IMMINENT_OPEN_CAPTURE): UNKNOWN → skip the cycle.
    summary["skipped"] is True
    summary["skip_reason"] == "cycle_skipped_freshness_gate_unevaluable"
    summary["freshness_gate_error"] is present

  * For non-fail-closed modes (e.g. OPENING_HUNT): UNKNOWN → degrade+continue.
    summary["degraded_data"] is True
    summary["freshness_entry_blocked"] is True
    summary["freshness_gate_error"] is present
    summary["skipped"] is NOT True

These tests monkeypatch evaluate_freshness_mid_run as imported in src.engine.cycle_runner,
following the pattern in test_imminent_open_capture.py (patch.object on the module attr)
and test_cycle_runner_db_lock_degrade.py (_make_minimal_run_cycle_deps helper).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_crash_freshness():
    """Return a callable that raises RuntimeError to simulate a gate crash."""
    def _crashing_evaluate(_state_dir):
        raise RuntimeError("freshness gate crashed in test")
    return _crashing_evaluate


def _patch_non_freshness_deps(monkeypatch):
    """Patch the non-freshness IO dependencies so run_cycle reaches the freshness
    try/except and can return early without touching live IO."""
    import src.engine.cycle_runner as cr
    from src.riskguard.risk_level import RiskLevel

    monkeypatch.setattr(cr, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cr, "get_force_exit_review", lambda: False)


# ---------------------------------------------------------------------------
# Settlement-day (DAY0_CAPTURE): UNKNOWN freshness → skip
# ---------------------------------------------------------------------------

def test_freshness_gate_crash_skips_settlement_day_mode(monkeypatch):
    """T-FGC-1: evaluate_freshness_mid_run raises → DAY0_CAPTURE is skipped.

    settlement_day_dispatch_for_mode(DAY0_CAPTURE) is True → fail-closed mode.
    A crashed gate must produce skipped=True with skip_reason=cycle_skipped_freshness_gate_unevaluable.
    """
    import src.engine.cycle_runner as cr
    from src.engine.discovery_mode import DiscoveryMode

    _patch_non_freshness_deps(monkeypatch)
    monkeypatch.setattr(cr, "evaluate_freshness_mid_run", _make_crash_freshness())

    summary = cr.run_cycle(DiscoveryMode.DAY0_CAPTURE)

    assert summary.get("skipped") is True, (
        f"Expected skipped=True for DAY0_CAPTURE on freshness gate crash, got: {summary}"
    )
    assert summary.get("skip_reason") == "cycle_skipped_freshness_gate_unevaluable", (
        f"Expected skip_reason=cycle_skipped_freshness_gate_unevaluable, got: {summary.get('skip_reason')}"
    )
    assert "freshness_gate_error" in summary, (
        f"Expected freshness_gate_error in summary, got keys: {list(summary.keys())}"
    )
    assert summary["freshness_gate_error"], (
        "freshness_gate_error must be a non-empty string (the repr of the exception)"
    )


# ---------------------------------------------------------------------------
# IMMINENT_OPEN_CAPTURE: UNKNOWN freshness → skip
# ---------------------------------------------------------------------------

def test_freshness_gate_crash_skips_imminent_open_capture(monkeypatch):
    """T-FGC-2: evaluate_freshness_mid_run raises → IMMINENT_OPEN_CAPTURE is skipped.

    IMMINENT_OPEN_CAPTURE is explicitly fail-closed (cycle_runner.py: mode ==
    DiscoveryMode.IMMINENT_OPEN_CAPTURE check alongside settlement_day). Markets
    close within 24h — no time to recover from a stale-signal trade.
    """
    import src.engine.cycle_runner as cr
    from src.engine.discovery_mode import DiscoveryMode

    _patch_non_freshness_deps(monkeypatch)
    monkeypatch.setattr(cr, "evaluate_freshness_mid_run", _make_crash_freshness())

    summary = cr.run_cycle(DiscoveryMode.IMMINENT_OPEN_CAPTURE)

    assert summary.get("skipped") is True, (
        f"Expected skipped=True for IMMINENT_OPEN_CAPTURE on freshness gate crash, got: {summary}"
    )
    assert summary.get("skip_reason") == "cycle_skipped_freshness_gate_unevaluable", (
        f"Expected skip_reason=cycle_skipped_freshness_gate_unevaluable, got: {summary.get('skip_reason')}"
    )
    assert "freshness_gate_error" in summary, (
        f"Expected freshness_gate_error in summary, got keys: {list(summary.keys())}"
    )


# ---------------------------------------------------------------------------
# OPENING_HUNT: UNKNOWN freshness → block entries, continue monitor/exit (NOT skip)
# ---------------------------------------------------------------------------

def test_freshness_gate_crash_blocks_opening_hunt_entries(monkeypatch):
    """T-FGC-3: evaluate_freshness_mid_run raises → OPENING_HUNT blocks entries, NOT skipped.

    OPENING_HUNT is not fail-closed (settlement is days away; ensemble-disabled degrade
    already exists as the non-fatal path). A crashed gate → degraded_data=True + error
    tag, but entry discovery is blocked while the cycle continues rather than
    short-circuiting.
    """
    import src.engine.cycle_runner as cr
    from src.engine.discovery_mode import DiscoveryMode

    _patch_non_freshness_deps(monkeypatch)
    monkeypatch.setattr(cr, "evaluate_freshness_mid_run", _make_crash_freshness())
    # Patch get_connection to None so cycle exits cleanly after the freshness block
    # without needing a real DB (mirrors _make_minimal_run_cycle_deps pattern).
    monkeypatch.setattr(cr, "get_connection", lambda: None)

    summary = cr.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary.get("degraded_data") is True, (
        f"Expected degraded_data=True for OPENING_HUNT on freshness gate crash, got: {summary}"
    )
    assert summary.get("freshness_entry_blocked") is True, (
        f"Expected freshness_entry_blocked=True for OPENING_HUNT on freshness gate crash, got: {summary}"
    )
    assert "freshness_gate_error" in summary, (
        f"Expected freshness_gate_error in summary, got keys: {list(summary.keys())}"
    )
    # Must NOT be a freshness-gate skip (it may be db_write_lock_degraded skipped, that's OK)
    assert summary.get("skip_reason") != "cycle_skipped_freshness_gate_unevaluable", (
        "OPENING_HUNT must degrade+continue on freshness crash, not be skipped as unevaluable"
    )
