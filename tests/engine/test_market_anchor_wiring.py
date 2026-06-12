# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: objective-math audit 2026-06-11. Wiring pins for the market-anchor cap in
#   event_reactor_adapter: flag DEFAULT OFF (byte-identical), and the per-candidate helper that
#   converts a bin+mu into settlement-step distance and routes near-center NO through the cap.
"""Market-anchor ADAPTER WIRING antibodies (cross-module relationship pins).

Killed categories:
  * the flag silently defaulting ON (it must be OFF until operator word + forward fills),
  * the helper mis-scoping the cap (a far-NO C4 harvest candidate getting capped, or a
    near-center C3 candidate escaping the cap) because the bin->step distance is wrong.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine import event_reactor_adapter as era


def test_market_anchor_flag_defaults_off():
    """Iron rule: the cap moves the live tradable q_lcb (HIGH risk) -> default FALSE."""
    assert era._replacement_q_market_anchor_enabled() is False


def test_market_anchor_alpha_comes_from_legacy_registry():
    """alpha is the SINGLE legacy registry value (edge.base_alpha level-3), not a new constant."""
    from src.config import settings

    assert era._market_anchor_alpha() == pytest.approx(
        float(settings["edge"]["base_alpha"]["level3"])
    )


def _candidate_with_bin(low, high, unit):
    return SimpleNamespace(bin=SimpleNamespace(low=low, high=high, unit=unit))


def test_helper_caps_near_center_no_in_celsius():
    """C-unit point bin [22,22], mu=21.5 (0.5 step away -> C3): a model NO more confident than the
    market is capped. step=1.0C so distance_steps=0.5 < 1.5 reach."""
    cand = _candidate_with_bin(22.0, 22.0, "C")
    res = era._market_anchor_no_lcb_for_candidate(
        candidate=cand, q_lcb_no=0.83, q_model_no=0.83, market_no_price=0.66, mu=21.5,
    )
    assert res is not None
    assert res.capped is True
    assert res.q_lcb_no_out < 0.83


def test_helper_leaves_far_no_harvest_untouched_in_fahrenheit():
    """F-unit range bin [90,91], mu far below (mu=80F). step=2.0F so a 10F gap = 5 steps -> C4.
    The far-NO harvest must be byte-identical even against a very different market."""
    cand = _candidate_with_bin(90.0, 91.0, "F")
    res = era._market_anchor_no_lcb_for_candidate(
        candidate=cand, q_lcb_no=0.95, q_model_no=0.95, market_no_price=0.40, mu=80.0,
    )
    assert res is not None
    assert res.capped is False
    assert res.q_lcb_no_out == pytest.approx(0.95, abs=1e-12)


def test_helper_no_bin_returns_none():
    cand = SimpleNamespace(bin=None)
    res = era._market_anchor_no_lcb_for_candidate(
        candidate=cand, q_lcb_no=0.83, q_model_no=0.83, market_no_price=0.66, mu=21.5,
    )
    assert res is None


def test_fahrenheit_step_scaling_keeps_adjacent_bin_in_scope():
    """An F point/range bin one settled bin off center is ~2F = 1 step -> still near-center (C3).
    Pins that the F step (2.0) is used, not the C step (1.0) — otherwise a 2F-away bin would read
    as 2 steps and wrongly escape the cap."""
    cand = _candidate_with_bin(92.0, 93.0, "F")
    res = era._market_anchor_no_lcb_for_candidate(
        candidate=cand, q_lcb_no=0.88, q_model_no=0.88, market_no_price=0.70, mu=90.5,
    )
    # |90.5 - 92.0| = 1.5F / 2.0F step = 0.75 steps < 1.5 reach -> capped.
    assert res is not None and res.capped is True
