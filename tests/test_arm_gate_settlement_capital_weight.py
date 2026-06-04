# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: STRUCTURAL_FIX_PLAN_2026-06-03 §P0.2 (F3 capital-weighted ARM).
#   GOAL#36 live profit >51% after-cost SETTLEMENT win-rate. Antibody:
#   equal-row win-rate can exceed 51% while capital-weighted ROI is NEGATIVE
#   (the system sized UP on the losers). The ARM verdict must fail closed on
#   that — a row-count-democracy verdict is dangerous when capital is unequal.
"""Relationship tests for the capital-weighted ARM verdict.

The cross-module property under test: when the WIN/LOSS truth (graded per the
Direction Law) flows into the ARM sizing-weighted aggregation, a cohort whose
EQUAL-ROW win-rate clears 51% but whose CAPITAL-WEIGHTED ROI is negative must
produce ARM_DENIED. Written RED-first.
"""
from __future__ import annotations

import pytest

from scripts.measure_arm_gate_settlement import (
    CapitalWeightedArmVerdict,
    _compute_capital_weighted_verdict,
    _capital_weighted_arm_decision,
)


def _row(city: str, win: bool, price: float, size_usd: float) -> dict:
    """A graded ARM row: win flag + entry price + kelly size (capital)."""
    return {
        "city": city,
        "win": win,
        "price": price,
        "kelly_size_usd": size_usd,
    }


# ---------------------------------------------------------------------------
# ANTIBODY — equal-row wr > 51% but capital-weighted ROI < 0 → DENIED
# ---------------------------------------------------------------------------
def test_capital_weight_denies_when_size_concentrated_on_loser():
    """Two $1 winners + one $100 loser:
        equal-row win-rate = 2/3 = 0.667 > 0.51  (looks armable)
        capital-weighted   : the $100 loser dominates → ROI < 0
    The capital-weighted verdict MUST be ARM_DENIED despite the row-rate."""
    rows = [
        _row("Tokyo", win=True, price=0.50, size_usd=1.0),
        _row("Paris", win=True, price=0.50, size_usd=1.0),
        _row("Seoul", win=False, price=0.50, size_usd=100.0),
    ]
    verdict = _compute_capital_weighted_verdict(rows)
    assert isinstance(verdict, CapitalWeightedArmVerdict)
    # Equal-row win-rate clears the headline bar.
    assert verdict.equal_row_win_rate > 0.51
    # But capital-weighted ROI is negative (loser carries the capital).
    assert verdict.capital_weighted_roi < 0.0
    eligible, reason = _capital_weighted_arm_decision(verdict)
    assert eligible is False
    assert "DENIED" in reason or "INSUFFICIENT" in reason


# ---------------------------------------------------------------------------
# ANTIBODY — missing / non-positive size fails CLOSED (never silent equal-weight)
# ---------------------------------------------------------------------------
def test_missing_size_raises_value_error():
    rows = [
        _row("Tokyo", win=True, price=0.50, size_usd=1.0),
        _row("Paris", win=True, price=0.50, size_usd=None),  # missing
    ]
    with pytest.raises(ValueError) as ei:
        _compute_capital_weighted_verdict(rows)
    assert "MISSING_SIZE" in str(ei.value)


def test_zero_size_raises_value_error():
    rows = [
        _row("Tokyo", win=True, price=0.50, size_usd=1.0),
        _row("Paris", win=True, price=0.50, size_usd=0.0),  # <= 0
    ]
    with pytest.raises(ValueError) as ei:
        _compute_capital_weighted_verdict(rows)
    assert "MISSING_SIZE" in str(ei.value)


# ---------------------------------------------------------------------------
# ANTIBODY — a per-city capital-weighted negative cluster denies even if pooled positive
# ---------------------------------------------------------------------------
def test_per_city_negative_cluster_denies_even_if_pooled_positive():
    """One city pools strongly positive, dragging the pooled CW-ROI positive,
    but a second city is capital-weighted negative. ARM must DENY: every active
    city's capital cluster must be non-negative beyond tolerance."""
    rows = [
        # Tokyo: big winners, positive cluster
        _row("Tokyo", win=True, price=0.40, size_usd=100.0),
        _row("Tokyo", win=True, price=0.40, size_usd=100.0),
        _row("Tokyo", win=True, price=0.40, size_usd=100.0),
        _row("Tokyo", win=True, price=0.40, size_usd=100.0),
        _row("Tokyo", win=True, price=0.40, size_usd=100.0),
        # Seoul: losers with real capital, negative cluster
        _row("Seoul", win=False, price=0.60, size_usd=10.0),
        _row("Seoul", win=False, price=0.60, size_usd=10.0),
        _row("Seoul", win=False, price=0.60, size_usd=10.0),
        _row("Seoul", win=False, price=0.60, size_usd=10.0),
        _row("Seoul", win=False, price=0.60, size_usd=10.0),
    ]
    verdict = _compute_capital_weighted_verdict(rows)
    assert verdict.capital_weighted_roi > 0.0  # pooled positive (Tokyo dominates)
    assert verdict.per_city_cw_roi["Seoul"] < 0.0  # but Seoul cluster negative
    eligible, reason = _capital_weighted_arm_decision(verdict)
    assert eligible is False
    assert "DENIED" in reason


# ---------------------------------------------------------------------------
# All required fields present (no Optional) — the type forces full computation
# ---------------------------------------------------------------------------
def test_verdict_has_all_required_fields():
    rows = [_row("Tokyo", win=True, price=0.50, size_usd=5.0)]
    verdict = _compute_capital_weighted_verdict(rows)
    # Every documented field must be populated (dataclass with no Optional).
    assert verdict.equal_row_win_rate is not None
    assert verdict.equal_row_ev_sigma is not None
    assert verdict.capital_weighted_roi is not None
    assert verdict.capital_weighted_ev_sigma is not None
    assert isinstance(verdict.per_city_cw_roi, dict)


def test_empty_rows_denies_not_crashes():
    verdict = _compute_capital_weighted_verdict([])
    eligible, reason = _capital_weighted_arm_decision(verdict)
    assert eligible is False
    assert "INSUFFICIENT" in reason
