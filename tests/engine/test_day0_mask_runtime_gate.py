# Created: 2026-06-05
# Last reused or audited: 2026-06-05
# Authority basis: day0 phased plan P3 (architect 2026-06-05) — runtime probability-vector
#   correctness gate. After the day0 absorbing mask is applied + renormalized at the live
#   seam (_apply_edli_live_family_before_selection, evaluator.py DAY0_EXTREME_UPDATED branch),
#   a fail-closed runtime assertion re-derives the absorbing-boundary invariant INDEPENDENTLY
#   of the masker and raises DAY0_MASK_CONTRADICTS_OBSERVATION if any surviving (mass>0) bin
#   contradicts rounded_value. This makes a FUTURE inversion of the masker fail CLOSED at
#   runtime (#98 wrong-side trade unconstructable), not merely in tests.
"""P3 tests: runtime DAY0_MASK_CONTRADICTS_OBSERVATION fail-closed gate."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from src.engine.evaluator import _assert_day0_mask_consistent_with_observation


def _analysis(bins):
    return SimpleNamespace(bins=[SimpleNamespace(low=lo, high=hi) for lo, hi in bins])


# ---------------------------------------------------------------------------
# Direct verifier: a posterior that keeps mass on an IMPOSSIBLE bin must raise.
# ---------------------------------------------------------------------------
def test_high_surviving_bin_below_observed_raises():
    # HIGH observed 30: a bin 28-29 (entirely below 30) is impossible. If the
    # posterior still carries mass there, the mask was inverted -> fail closed.
    bins = [(28, 29), (30, 31), (32, 33)]
    inverted_posterior = np.array([0.5, 0.3, 0.2])  # mass on the impossible 28-29
    with pytest.raises(ValueError, match="DAY0_MASK_CONTRADICTS_OBSERVATION"):
        _assert_day0_mask_consistent_with_observation(
            _analysis(bins),
            {"metric": "high", "rounded_value": 30.0},
            inverted_posterior,
        )


def test_low_surviving_bin_above_observed_raises_paris98():
    # #98 inversion shape: LOW observed 14, mass survives on 15-16 (above 14).
    bins = [(13, 13), (14, 14), (15, 15), (16, 16)]
    inverted_posterior = np.array([0.1, 0.1, 0.4, 0.4])  # mass on impossible 15,16
    with pytest.raises(ValueError, match="DAY0_MASK_CONTRADICTS_OBSERVATION"):
        _assert_day0_mask_consistent_with_observation(
            _analysis(bins),
            {"metric": "low", "rounded_value": 14.0},
            inverted_posterior,
        )


# ---------------------------------------------------------------------------
# A CORRECT posterior (mass only on reachable bins) must NOT raise.
# ---------------------------------------------------------------------------
def test_high_correct_posterior_passes():
    bins = [(28, 29), (30, 31), (32, 33)]
    correct = np.array([0.0, 0.6, 0.4])  # impossible 28-29 carries exactly 0
    _assert_day0_mask_consistent_with_observation(
        _analysis(bins), {"metric": "high", "rounded_value": 30.0}, correct
    )


def test_low_correct_posterior_passes():
    bins = [(13, 13), (14, 14), (15, 15), (16, 16)]
    correct = np.array([0.5, 0.5, 0.0, 0.0])
    _assert_day0_mask_consistent_with_observation(
        _analysis(bins), {"metric": "low", "rounded_value": 14.0}, correct
    )


def test_growth_side_shoulder_and_boundary_bin_pass():
    # open-high shoulder + the bin containing the observed value must be allowed mass.
    bins = [(28, 29), (30, 31), (32, None)]
    correct = np.array([0.0, 0.5, 0.5])
    _assert_day0_mask_consistent_with_observation(
        _analysis(bins), {"metric": "high", "rounded_value": 30.0}, correct
    )


# ---------------------------------------------------------------------------
# No observation (missing rounded_value) -> gate is a no-op (mask was all-ones).
# ---------------------------------------------------------------------------
def test_missing_rounded_value_is_noop():
    bins = [(13, 14), (15, 16)]
    _assert_day0_mask_consistent_with_observation(
        _analysis(bins), {"metric": "low"}, np.array([0.5, 0.5])
    )


# ---------------------------------------------------------------------------
# Integration: an inverted MASKER at the live seam trips the runtime gate.
#   Monkeypatch _edli_day0_mask_for_analysis to return a deliberately inverted
#   mask; the seam must raise DAY0_MASK_CONTRADICTS_OBSERVATION rather than emit a
#   wrong-side family.
# ---------------------------------------------------------------------------
def test_inverted_masker_trips_gate_at_seam(monkeypatch):
    import src.engine.evaluator as ev

    bins = [
        SimpleNamespace(low=13, high=13, label="13"),
        SimpleNamespace(low=14, high=14, label="14"),
        SimpleNamespace(low=15, high=15, label="15"),
        SimpleNamespace(low=16, high=16, label="16"),
    ]
    # An INVERTED mask: keeps the impossible above-observed bins (15,16), kills the
    # reachable ones (13,14) — the exact #98 transpose.
    inverted_mask = np.array([0.0, 0.0, 1.0, 1.0])
    monkeypatch.setattr(ev, "_edli_day0_mask_for_analysis", lambda *_a, **_k: inverted_mask)

    analysis = SimpleNamespace(
        bins=bins,
        p_posterior=np.array([0.25, 0.25, 0.25, 0.25]),
        _bootstrap_cache={},
        p_market=np.array([0.25, 0.25, 0.25, 0.25]),
    )
    candidate = SimpleNamespace(
        edli_event_context={
            "event_type": "DAY0_EXTREME_UPDATED",
            "payload": {"metric": "low", "rounded_value": 14.0},
            "causal_snapshot_id": "",
            "event_id": "e1",
        }
    )
    with pytest.raises(ValueError, match="DAY0_MASK_CONTRADICTS_OBSERVATION"):
        ev._apply_edli_live_family_before_selection(
            candidate=candidate, analysis=analysis, decision_snapshot_id=""
        )


def test_correct_masker_passes_seam(monkeypatch):
    # Control: the REAL masker (correct) must NOT trip the gate at the seam.
    import src.engine.evaluator as ev

    bins = [
        SimpleNamespace(low=13, high=13, label="13"),
        SimpleNamespace(low=14, high=14, label="14"),
        SimpleNamespace(low=15, high=15, label="15"),
        SimpleNamespace(low=16, high=16, label="16"),
    ]
    analysis = SimpleNamespace(
        bins=bins,
        p_posterior=np.array([0.25, 0.25, 0.25, 0.25]),
        _bootstrap_cache={},
        p_market=np.array([0.25, 0.25, 0.25, 0.25]),
    )

    def _buy_no_market_price(idx):
        return 0.5

    analysis.buy_no_market_price = _buy_no_market_price
    candidate = SimpleNamespace(
        edli_event_context={
            "event_type": "DAY0_EXTREME_UPDATED",
            "payload": {"metric": "low", "rounded_value": 14.0},
            "causal_snapshot_id": "",
            "event_id": "e1",
        }
    )
    proof = ev._apply_edli_live_family_before_selection(
        candidate=candidate, analysis=analysis, decision_snapshot_id=""
    )
    assert proof is not None
    # Reachable bins (13,14) carry mass; impossible (15,16) are exactly zero.
    assert analysis.p_posterior[2] == 0.0 and analysis.p_posterior[3] == 0.0
    assert analysis.p_posterior[0] > 0.0 and analysis.p_posterior[1] > 0.0
