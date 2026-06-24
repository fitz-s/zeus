# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/live_order_pathology/2026-06-23_selection_curse_*.md (the buy_no
#   winner's curse happens AT ADMISSION — the gate admits mid-price NO whose realized rate is ~0.69
#   vs claimed ~0.83). MONEY-PATH antibody: the entry admission q_lcb must be deflated by the
#   price-conditioned selection-curse bound so a mid-price buy_no entry self-rejects (edge_lcb<=0).
"""selection_calibrated_admission_q_lcb composes the selection-curse deflation at ENTRY admission.

Entry is the primary curse site. With the bound armed, a mid-price buy_no's admission q_lcb deflates
below its cost (so edge_lcb = q_lcb - cost <= 0 -> not admitted); deep favorites + buy_yes untouched;
absent/unarmed bound -> identity (today's exact behavior).
"""
from __future__ import annotations

import pytest

from src.decision.selection_curse_bound import SelectionCurseBound
from src.strategy.live_inference.live_admission import selection_calibrated_admission_q_lcb


def _bound(armed=("buy_no",)):
    return SelectionCurseBound(
        price_knots=(0.50, 0.60, 0.70, 0.80, 0.90, 0.97),
        realized_lcb=(0.55, 0.58, 0.66, 0.78, 0.93, 1.00),
        n_train=900,
        armed_sides=frozenset(armed),
        artifact_hash="t",
        built_at="2026-06-23T00:00:00Z",
    )


def _patch(monkeypatch, bound):
    monkeypatch.setattr(
        "src.decision.selection_curse_bound_loader.load_bound", lambda path=None: bound
    )


def test_midprice_buy_no_admission_q_lcb_deflated_below_cost(monkeypatch):
    _patch(monkeypatch, _bound())
    # served q_lcb_no 0.83, NO price 0.70 -> deflated to realized ~0.66 < 0.70 -> edge_lcb<0.
    q = selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.85, direction="buy_no", own_side_cost=0.70
    )
    assert q == pytest.approx(0.66, abs=1e-6)
    assert q < 0.70  # edge_lcb = q - cost < 0 -> not admitted


def test_favorite_buy_no_admission_untouched(monkeypatch):
    _patch(monkeypatch, _bound())
    q = selection_calibrated_admission_q_lcb(
        q_lcb=0.99, raw_side_prob=0.99, direction="buy_no", own_side_cost=0.97
    )
    assert q == pytest.approx(0.99, abs=1e-6)


def test_buy_yes_admission_identity(monkeypatch):
    _patch(monkeypatch, _bound())
    q = selection_calibrated_admission_q_lcb(
        q_lcb=0.30, raw_side_prob=0.30, direction="buy_yes", own_side_cost=0.12
    )
    assert q == pytest.approx(0.30, abs=1e-6)


def test_absent_bound_admission_identity(monkeypatch):
    _patch(monkeypatch, None)
    q = selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.85, direction="buy_no", own_side_cost=0.70
    )
    assert q == pytest.approx(0.83, abs=1e-6)
