# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: live-path wiring of the selection-calibrator + city-skill gate + shadow logger
#   (team-lead 2026-06-22; live_order_pathology 2026-06-22). The components were inert (no live call
#   site); these tests pin the live_admission.py seam functions that wire them, flag-gated so
#   DEFAULT OFF is byte-identical to current behavior.
"""TDD for the live_admission.py wiring of the three components (pure, flag-gated, fail-soft)."""
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
        "_meta": {"posterior_version": sc.DEFAULT_POSTERIOR_VERSION, "min_n": 30},
        "cells": {key: {"n": 104, "hit_rate": 0.679}},
    }


def test_calibrator_deflation_off_returns_input_qlcb(monkeypatch):
    monkeypatch.delenv("ZEUS_SELECTION_CALIBRATOR_LIVE", raising=False)
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.875, direction="buy_no", lead_days=1.0, bin_class="nonmodal",
        own_side_cost=0.70, artifact=_toxic_calibrator_artifact(),
    )
    assert out == 0.83  # OFF -> unchanged (byte-identical)


def test_calibrator_deflation_on_lowers_toxic_no(monkeypatch):
    monkeypatch.setenv("ZEUS_SELECTION_CALIBRATOR_LIVE", "1")
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.875, direction="buy_no", lead_days=1.0, bin_class="nonmodal",
        own_side_cost=0.70, artifact=_toxic_calibrator_artifact(),
    )
    assert out < 0.83
    assert out - 0.70 <= 0.0  # deflated below the 0.70 NO cost -> edge_lcb non-positive -> not admitted


def test_calibrator_deflation_on_failclosed_to_zero(monkeypatch):
    monkeypatch.setenv("ZEUS_SELECTION_CALIBRATOR_LIVE", "1")
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=0.83, raw_side_prob=0.875, direction="buy_no", lead_days=1.0, bin_class="nonmodal",
        own_side_cost=0.70, artifact=None,
    )
    assert out == 0.0  # fail-closed (no artifact) -> non-positive edge, NOT the raw q_lcb


def test_calibrator_deflation_fail_soft_on_bad_inputs(monkeypatch):
    # Observability/robustness: a non-finite input must not raise; OFF-equivalent passthrough.
    monkeypatch.setenv("ZEUS_SELECTION_CALIBRATOR_LIVE", "1")
    out = la.selection_calibrated_admission_q_lcb(
        q_lcb=float("nan"), raw_side_prob=0.875, direction="buy_no", lead_days=1.0,
        bin_class="nonmodal", own_side_cost=0.70, artifact=_toxic_calibrator_artifact(),
    )
    assert out == pytest.approx(0.0) or out != out  # returns the (nan) input or 0; never raises


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


def test_city_skill_rejection_off_is_none(monkeypatch):
    monkeypatch.delenv("ZEUS_CITY_SKILL_GATE_LIVE", raising=False)
    assert la.city_skill_block_rejection_reason(city="Karachi", artifact=_skill_artifact()) is None


def test_city_skill_rejection_on_blocks_stable_bad(monkeypatch):
    monkeypatch.setenv("ZEUS_CITY_SKILL_GATE_LIVE", "1")
    reason = la.city_skill_block_rejection_reason(city="Karachi", artifact=_skill_artifact())
    assert reason is not None
    assert "CITY_SKILL" in reason and "Karachi" in reason


def test_city_skill_rejection_on_allows_stable_good(monkeypatch):
    monkeypatch.setenv("ZEUS_CITY_SKILL_GATE_LIVE", "1")
    assert la.city_skill_block_rejection_reason(city="Tokyo", artifact=_skill_artifact()) is None


def test_city_skill_rejection_none_city_is_noop(monkeypatch):
    # No city available -> fail-soft no-op (never blocks for missing context).
    monkeypatch.setenv("ZEUS_CITY_SKILL_GATE_LIVE", "1")
    assert la.city_skill_block_rejection_reason(city=None, artifact=_skill_artifact()) is None


def test_city_skill_rejection_unknown_city_does_not_block(monkeypatch):
    # In block-only loss-reduction mode, an UNKNOWN city is not a confirmed stable loser -> no block.
    monkeypatch.setenv("ZEUS_CITY_SKILL_GATE_LIVE", "1")
    assert la.city_skill_block_rejection_reason(city="Beijing", artifact=_skill_artifact()) is None


# --------------------------------------------------------------------------------------------------
# 3. Shadow-log admission call.
# --------------------------------------------------------------------------------------------------

def test_shadow_log_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEUS_SHADOW_ADMIT_LOG", raising=False)
    p = tmp_path / "shadow.jsonl"
    wrote = la.shadow_log_admission(
        path=str(p), decision_time="t", city="Tokyo", target_date="2026-06-23",
        condition_id="0x", bin_id="18C", direction="buy_no", raw_side_prob=0.87,
        q_lcb=0.83, own_side_cost=0.70, native_quote_available=True, quote_fresh=True,
        posterior_version="v",
    )
    assert wrote is False and not p.exists()


def test_shadow_log_on_writes_row(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_SHADOW_ADMIT_LOG", "1")
    p = tmp_path / "shadow.jsonl"
    wrote = la.shadow_log_admission(
        path=str(p), decision_time="t", city="Tokyo", target_date="2026-06-23",
        condition_id="0x", bin_id="18C", direction="buy_no", raw_side_prob=0.87,
        q_lcb=0.83, own_side_cost=0.70, native_quote_available=True, quote_fresh=True,
        posterior_version="v",
    )
    assert wrote is True and p.exists()


def test_shadow_log_never_raises(monkeypatch):
    monkeypatch.setenv("ZEUS_SHADOW_ADMIT_LOG", "1")
    # Bad path must be swallowed (observability never breaks the decision path).
    assert la.shadow_log_admission(
        path="/no_such_dir_xyz/shadow.jsonl", decision_time="t", city="Tokyo",
        target_date="2026-06-23", condition_id="0x", bin_id="18C", direction="buy_no",
        raw_side_prob=0.87, q_lcb=0.83, own_side_cost=0.70, native_quote_available=True,
        quote_fresh=True, posterior_version="v",
    ) is False
