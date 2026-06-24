# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/live_order_pathology/2026-06-23_selection_curse_*.md (counterfactual
#   admission winner's-curse: admitted mid-price buy_no claims ~0.83 / realizes ~0.69; monotone in
#   price; favorites >=0.95 calibrated; buy_yes benign). MONEY-PATH antibody: reverting the
#   selection-curse deflation in _event_bound_q_exec_lcb (back to gating the taker edge on raw q_lcb)
#   re-admits the mid-price buy_no cross the settlement-evidenced realized rate refuses.
"""The event-bound taker quality proof must tighten its admissibility edge with the price-conditioned
selection-curse bound.

A mid-price buy_no taker that clears the raw q_lcb after cost (passed=True today) must be REFUSED
once the bound is armed, because the realized NO rate at that price (~0.66) is below the cross cost.
A deep favorite buy_no and buy_yes are untouched; an absent/unarmed bound -> identity.
"""
from __future__ import annotations

import pytest

from src.decision.selection_curse_bound import SelectionCurseBound
from src.engine.event_reactor_adapter import _build_event_bound_taker_quality_proof


def _bound(armed=("buy_no",)):
    return SelectionCurseBound(
        price_knots=(0.50, 0.60, 0.70, 0.80, 0.90, 0.97),
        realized_lcb=(0.55, 0.58, 0.66, 0.78, 0.93, 1.00),
        n_train=900,
        armed_sides=frozenset(armed),
        artifact_hash="testcohort",
        built_at="2026-06-23T00:00:00Z",
    )


def _patch(monkeypatch, bound):
    monkeypatch.setattr(
        "src.decision.selection_curse_bound_loader.load_bound", lambda path=None: bound
    )


_BUY_NO = {"direction": "buy_no", "q_lcb_5pct": "0.83", "kelly_size_usd": "10.0", "q_live": "0.85"}


def test_midprice_buy_no_taker_passes_without_bound(monkeypatch):
    # Identity baseline: no bound -> gate on raw q_lcb 0.83. cost 0.70 + fee < 0.83 -> passes.
    _patch(monkeypatch, None)
    proof = _build_event_bound_taker_quality_proof(
        actionable_payload=_BUY_NO, order_mode="TAKER", fresh_best_bid=0.69, fresh_best_ask=0.70
    )
    assert proof is not None and proof["passed"] is True


def test_midprice_buy_no_taker_refused_when_bound_armed(monkeypatch):
    # realized NO rate at price 0.70 is ~0.66 << raw 0.83 -> after-cost edge goes negative -> REFUSED.
    _patch(monkeypatch, _bound())
    proof = _build_event_bound_taker_quality_proof(
        actionable_payload=_BUY_NO, order_mode="TAKER", fresh_best_bid=0.69, fresh_best_ask=0.70
    )
    assert proof is not None
    assert proof["passed"] is False
    assert proof["q_exec_lcb_basis"] == "SELECTION_CURSE:buy_no"
    assert float(proof["q_exec_lcb"]) == pytest.approx(0.66, abs=1e-6)


def test_deep_favorite_buy_no_untouched(monkeypatch):
    # Favorite NO at price 0.97: realized ~1.0 -> bound keeps raw q_lcb -> still passes.
    _patch(monkeypatch, _bound())
    payload = dict(_BUY_NO, q_lcb_5pct="0.99")
    proof = _build_event_bound_taker_quality_proof(
        actionable_payload=payload, order_mode="TAKER", fresh_best_bid=0.96, fresh_best_ask=0.97
    )
    assert proof is not None and proof["passed"] is True
    assert float(proof["q_exec_lcb"]) == pytest.approx(0.99, abs=1e-6)  # min(0.99, ~1.0)


def test_buy_yes_is_identity(monkeypatch):
    _patch(monkeypatch, _bound())  # bound has buy_no only
    payload = dict(_BUY_NO, direction="buy_yes", q_lcb_5pct="0.30")
    proof = _build_event_bound_taker_quality_proof(
        actionable_payload=payload, order_mode="TAKER", fresh_best_bid=0.11, fresh_best_ask=0.12
    )
    assert proof is not None and proof["passed"] is True
    assert proof["q_exec_lcb_basis"] == "BUY_YES_IDENTITY"
