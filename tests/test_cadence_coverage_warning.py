# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: timing-semantics fix C5 (cadence coverage); docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md

import logging

import pytest

from src.main import _warn_if_cadence_uncovered


def test_function_is_importable():
    """_warn_if_cadence_uncovered must be importable from src.main."""
    assert callable(_warn_if_cadence_uncovered)


def test_warning_fires_when_cadence_exceeds_freshness(caplog):
    """When effective_sweep_period > freshness_window, a WARNING is emitted naming both values."""
    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_uncovered(
            effective_sweep_period_s=200.0,
            freshness_window_s=180.0,
        )
    assert any(r.levelno == logging.WARNING for r in caplog.records), (
        "Expected a WARNING record when sweep period exceeds freshness window"
    )
    # Both numeric values must appear in the warning text
    warning_text = " ".join(r.message for r in caplog.records if r.levelno == logging.WARNING)
    assert "200.0" in warning_text, f"Expected sweep period 200.0 in warning: {warning_text!r}"
    assert "180.0" in warning_text, f"Expected freshness window 180.0 in warning: {warning_text!r}"


def test_no_warning_when_cadence_equals_freshness(caplog):
    """When effective_sweep_period == freshness_window, NO warning fires (boundary: covered)."""
    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_uncovered(
            effective_sweep_period_s=180.0,
            freshness_window_s=180.0,
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], f"Expected no WARNING at boundary equality, got: {warnings}"


def test_no_warning_when_cadence_below_freshness(caplog):
    """When effective_sweep_period < freshness_window, NO warning fires (cadence is covered)."""
    with caplog.at_level(logging.WARNING):
        _warn_if_cadence_uncovered(
            effective_sweep_period_s=20.0,
            freshness_window_s=180.0,
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], f"Expected no WARNING when cadence is below freshness window, got: {warnings}"
