# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/live_order_pathology/2026-06-23_selection_curse_*.md (counterfactual
#   admission winner's-curse measurement: admitted buy_no claims ~0.83 / realizes ~0.69, monotone in
#   price, favorites >=0.95 calibrated, buy_yes benign; walk-forward price-conditioned correction
#   collapses OOS over-claim to +/-0.01). MONEY-PATH antibody: removing the deflation re-admits the
#   mid-price buy_no the settlement-evidenced realized rate refuses.
"""The selection-curse bound deflates a buy_no authorization toward its settlement-evidenced
realized rate, conditioned on price — and ONLY ever tightens.

Population q is calibrated; the loss is the admission gate selecting over-claiming mid-price NO.
The bound is a monotone (isotonic) realized-NO-rate vs NO-price fit walk-forward on the admitted
slice. corrected_side_q_lcb deflates q_lcb_no to min(raw, realized_lcb(price)); buy_yes and deep
favorites are untouched; absent/unarmed/out-of-support -> identity (today's behavior).
"""
from __future__ import annotations

import math

import pytest

from src.decision.selection_curse_bound import (
    SelectionCurseBound,
    corrected_side_q_lcb,
)


def _bound(armed=("buy_no",)):
    # Monotone realized-NO-rate lower band by NO price (from the counterfactual measurement):
    # mid-price NO realizes ~0.60-0.69; favorites ~1.0. Knots ascending, realized monotone nondecr.
    return SelectionCurseBound(
        price_knots=(0.50, 0.60, 0.70, 0.80, 0.90, 0.97),
        realized_lcb=(0.55, 0.58, 0.66, 0.78, 0.93, 1.00),
        n_train=900,
        armed_sides=frozenset(armed),
        artifact_hash="testhash",
        built_at="2026-06-23T00:00:00Z",
    )


def test_midprice_buy_no_is_deflated_below_its_cost():
    # NO priced 0.70 (cost ~0.70): served q_lcb_no 0.83 (gate would admit), but realized rate at
    # price 0.70 is ~0.66 -> deflated to 0.66 < cost -> self-rejects.
    q, basis = corrected_side_q_lcb(_bound(), side="buy_no", price=0.70, raw_q_lcb=0.83)
    assert q == pytest.approx(0.66, abs=1e-9)
    assert "buy_no" in basis


def test_deep_favorite_buy_no_is_not_deflated():
    # Favorite NO priced 0.97: realized ~1.0 >= raw q_lcb -> min keeps raw (no loosening, no harm).
    q, _ = corrected_side_q_lcb(_bound(), side="buy_no", price=0.97, raw_q_lcb=0.95)
    assert q == pytest.approx(0.95, abs=1e-9)  # min(0.95, ~1.0) = 0.95


def test_only_ever_tightens_never_raises_raw():
    # The deflation can never RAISE q_lcb above the served value.
    q, _ = corrected_side_q_lcb(_bound(), side="buy_no", price=0.60, raw_q_lcb=0.40)
    assert q <= 0.40


def test_buy_yes_is_identity():
    q, basis = corrected_side_q_lcb(_bound(), side="buy_yes", price=0.12, raw_q_lcb=0.30)
    assert q == 0.30
    assert basis == "BUY_YES_IDENTITY"


def test_absent_bound_is_identity():
    q, basis = corrected_side_q_lcb(None, side="buy_no", price=0.70, raw_q_lcb=0.83)
    assert q == 0.83
    assert basis == "BOUND_ABSENT"


def test_unarmed_side_is_identity():
    q, basis = corrected_side_q_lcb(_bound(armed=()), side="buy_no", price=0.70, raw_q_lcb=0.83)
    assert q == 0.83
    assert basis == "SIDE_NOT_ARMED"


def test_price_out_of_train_support_is_identity():
    # Below the smallest knot (no settlement evidence at that price) -> identity, never fabricate.
    q, basis = corrected_side_q_lcb(_bound(), side="buy_no", price=0.20, raw_q_lcb=0.83)
    assert q == 0.83
    assert basis == "OUT_OF_SUPPORT"


def test_interpolates_monotonically_between_knots():
    # price 0.75 sits between knots 0.70(0.66) and 0.80(0.78) -> linear interp ~0.72.
    q, _ = corrected_side_q_lcb(_bound(), side="buy_no", price=0.75, raw_q_lcb=0.99)
    assert 0.66 < q < 0.78


@pytest.mark.parametrize("bad_price", [None, float("nan"), float("inf"), float("-inf"), "x"])
def test_nonfinite_or_nonnumeric_price_is_identity(bad_price):
    # A garbage price must NEVER deflate or raise into the live gate -> identity with INVALID_INPUT.
    q, basis = corrected_side_q_lcb(_bound(), side="buy_no", price=bad_price, raw_q_lcb=0.83)
    assert q == 0.83
    assert basis == "INVALID_INPUT"


@pytest.mark.parametrize("bad_raw", [None, float("nan"), "x"])
def test_nonfinite_raw_q_lcb_is_identity(bad_raw):
    q, basis = corrected_side_q_lcb(_bound(), side="buy_no", price=0.70, raw_q_lcb=bad_raw)
    assert basis == "INVALID_INPUT"
    # returns the raw input coerced (nan if uncoercible) — never a fabricated deflation
    assert math.isnan(q) or q == bad_raw
