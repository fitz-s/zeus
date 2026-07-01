# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: live-path wiring of the selection-calibrator + city-skill gate + shadow logger
#   (team-lead 2026-06-22; live_order_pathology 2026-06-22).
"""Tests for the live_admission.py wiring of the calibrator and explicit city-skill blocker."""
from __future__ import annotations

import pytest

from src.strategy.live_inference import live_admission as la
from src.decision import selection_calibrator as sc
from src.decision import city_skill_gate as csg


# --------------------------------------------------------------------------------------------------
# 1. Selection-calibrator q_lcb deflation seam.
# --------------------------------------------------------------------------------------------------

def _toxic_calibrator_artifact():
    bucket_idx, _ = sc.raw_prob_bucket(0.875)
    key = f"NO|L1|nonmodal|pb{bucket_idx}"
    return {
        "_meta": {
            "posterior_version": sc.DEFAULT_POSTERIOR_VERSION,
            "min_n": 30,
            "armed_sides": ["NO"],
        },
        "cells": {key: {"n": 104, "hit_rate": 0.679}},
    }


def test_calibrator_deflation_is_live_without_env_flag(monkeypatch):
    monkeypatch.delenv("ZEUS_SELECTION_CALIBRATOR_LIVE", raising=False)
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.875, direction="buy_no", lead_days=1.0, bin_class="nonmodal",
        own_side_cost=0.70, artifact=_toxic_calibrator_artifact(),
    )
    assert out < 0.83
    assert out - 0.70 <= 0.0


def test_calibrator_deflation_lowers_toxic_no():
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.875, direction="buy_no", lead_days=1.0, bin_class="nonmodal",
        own_side_cost=0.70, artifact=_toxic_calibrator_artifact(),
    )
    assert out < 0.83
    assert out - 0.70 <= 0.0  # deflated below the 0.70 NO cost -> edge_lcb non-positive -> not admitted


def test_calibrator_deflation_failclosed_to_zero(monkeypatch):
    monkeypatch.setattr(sc, "load_artifact", lambda: None)
    sc.reset_artifact_cache()
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.875, direction="buy_no", lead_days=1.0, bin_class="nonmodal",
        own_side_cost=0.70, artifact=None,
    )
    assert out == 0.0  # fail-closed (no artifact) -> non-positive edge, NOT the raw q_lcb


def test_calibrator_deflation_fail_soft_on_bad_inputs(monkeypatch):
    # A non-finite input must not raise and must fail closed.
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=float("nan"), raw_side_prob=0.875, direction="buy_no", lead_days=1.0,
        bin_class="nonmodal", own_side_cost=0.70, artifact=_toxic_calibrator_artifact(),
    )
    assert out == pytest.approx(0.0)


# --------------------------------------------------------------------------------------------------
# 2. City-skill block-bad gate rejection reason.
# --------------------------------------------------------------------------------------------------

def _skill_artifact():
    return {
        "_meta": {"posterior_version": csg.DEFAULT_POSTERIOR_VERSION,
                  "min_track_record": 4, "skill_floor": 0.0},
        "cities": {
            "Karachi": {"prior_skill": -0.26, "prior_n": 5, "stable_bad": True},
            "Tokyo": {"prior_skill": 0.06, "prior_n": 7, "stable_good": True},
        },
    }


def test_city_skill_missing_artifact_is_not_in_execution_path():
    assert la.city_skill_block_rejection_reason(city="Karachi", artifact=None) is None


def test_city_skill_rejection_blocks_stable_bad():
    reason = la.city_skill_block_rejection_reason(city="Karachi", artifact=_skill_artifact())
    assert reason is not None
    assert "CITY_SKILL" in reason and "Karachi" in reason


def test_city_skill_rejection_allows_stable_good():
    assert la.city_skill_block_rejection_reason(city="Tokyo", artifact=_skill_artifact()) is None


def test_city_skill_rejection_none_city_is_noop():
    # No city available -> fail-soft no-op (never blocks for missing context).
    assert la.city_skill_block_rejection_reason(city=None, artifact=_skill_artifact()) is None


def test_city_skill_rejection_unknown_city_does_not_block():
    # In block-only loss-reduction mode, an UNKNOWN city is not a confirmed stable loser -> no block.
    assert la.city_skill_block_rejection_reason(city="Beijing", artifact=_skill_artifact()) is None
