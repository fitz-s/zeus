# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: selection-aware settlement q_lcb calibrator
#   (frontier consult REQ-20260622-151741; live_order_pathology 2026-06-22)
"""RED-first tests for the selection-aware settlement q_lcb calibrator (runtime serving rule).

The calibrator is the price/selection-aware SUCCESSOR to the price-blind OOF reliability guard
(src/decision/qlcb_reliability_guard.py). The OOF guard keys on the *derived* q_lcb bucket, so it
cannot see the price-conditioned ADVERSE SELECTION that loses money: the live book is -23% because
the admission gate ``q_lcb_side > price`` selects exactly the bins where the model most
under-estimates the bin (its over-confident tail). On the real 104-bet buy_no slice the system's
YES-belief-in-bin = 0.126 but realized-in-bin = 0.327 (market priced 0.298) — a ~20pp over-claim on
the bought NO side that the center-uncertainty bootstrap q_lcb does not cover.

The calibrator maps pre-trade features (side, lead bucket, bin class, RAW side prob, admission
margin) -> a MONOTONE CONSERVATIVE lower bound on the realized settlement hit-rate, fit walk-forward
on settled rows ONLY (a decision at T uses only rows settled < T). It is the admission lower bound
at the q_lcb seam, BEFORE edge_lcb>0 / BH-FDR / Kelly.

These tests assert the SERVING-rule contract (the artifact-fitting no-leak contract is tested in
tests/scripts/test_fit_selection_calibrator.py):

  * MONOTONE: a higher raw side prob never yields a lower calibrated lower bound (within a cell).
  * CONSERVATIVE: the calibrated lower bound <= the point estimate; tighter (closer to point) with
    more samples in the cell.
  * SELECTION-CORRECT: on a buy_no cell whose realized NO-win-rate is ~0.679 / market ~0.699, the
    served lower bound lands near 0.67-0.70 so a ~0.70 NO cost FAILS edge_lcb>0 -> the toxic losers
    are BLOCKED. (The old center-bootstrap q_lcb admitted them.)
  * FAIL-CLOSED: missing / malformed / stale / under-min-N artifact -> NO-TRADE verdict (q_safe=0,
    trade=False, abstained=True), NEVER a raw center-bootstrap q_lcb fallback.
  * GENUINE-EDGE PRESERVED: a cheap buy_yes (raw prob ~0.45, cost ~0.30) whose cell has real settled
    coverage above cost is NOT over-blocked.
"""
from __future__ import annotations

import json
import math

import pytest

from src.decision import selection_calibrator as sc


# --------------------------------------------------------------------------------------------------
# Cell-key + monotone lower-bound math (pure, no artifact).
# --------------------------------------------------------------------------------------------------

def test_raw_prob_bucket_is_monotone_and_covers_unit_interval():
    # Buckets partition [0, 1]; a higher prob never lands in a lower bucket index.
    prev = -1
    for p in (0.0, 0.05, 0.12, 0.30, 0.50, 0.70, 0.95, 1.0):
        idx = sc.raw_prob_bucket(p)[0]
        assert idx >= prev
        prev = idx


def test_lead_bucket_matches_spine_grouping():
    assert sc.lead_bucket(0.5) == "L1"
    assert sc.lead_bucket(1.0) == "L1"
    assert sc.lead_bucket(2.5) == "L2_3"
    assert sc.lead_bucket(3.0) == "L2_3"
    assert sc.lead_bucket(5.0) == "L4P"


def test_beta_lower_bound_is_conservative_and_tightens_with_n():
    # A cell with realized hit-rate 0.70: a thin sample gets a much lower 95% lower bound than a
    # deep sample at the SAME rate (conservative on thin evidence).
    lo_thin = sc.beta_lower_bound_95(hits=7, n=10)
    lo_deep = sc.beta_lower_bound_95(hits=700, n=1000)
    assert 0.0 <= lo_thin < 0.70
    assert lo_deep > lo_thin
    assert lo_deep <= 0.70 + 1e-9  # lower bound never exceeds the point
    # Degenerate cell.
    assert sc.beta_lower_bound_95(hits=0, n=0) == 0.0


def test_isotonic_lower_bound_is_monotone_nondecreasing():
    # Given per-bucket (raw_prob_mid, realized_hit_rate) pairs that are noisy, the isotonic fit must
    # be monotone non-decreasing in raw prob (a higher belief cannot map to a lower calibrated rate).
    xs = [0.1, 0.2, 0.3, 0.4, 0.5]
    ys = [0.30, 0.25, 0.45, 0.40, 0.60]  # non-monotone raw
    fitted = sc.isotonic_nondecreasing(xs, ys)
    for a, b in zip(fitted, fitted[1:]):
        assert b >= a - 1e-9


# --------------------------------------------------------------------------------------------------
# The serving rule: apply_selection_calibrator.
# --------------------------------------------------------------------------------------------------

def _artifact(cells: dict, *, version: str = "sel_v1", min_n: int = 30, fitted_before: str = "2099-01-01T00:00:00+00:00") -> dict:
    return {
        "_meta": {
            "authority": "selection_calibrator_v1_walkforward",
            "version": version,
            # Source-of-truth constant so runtime/fitter version strings can never drift apart
            # again (the BLOCKER the consult flagged).
            "posterior_version": sc.DEFAULT_POSTERIOR_VERSION,
            "min_n": min_n,
            "max_settled_at": fitted_before,
            "cell_key_schema": "side|lead_bucket|bin_class|raw_prob_bucket",
        },
        "cells": cells,
    }


def test_selection_calibrator_blocks_toxic_buy_no_losers():
    # The real buy_no toxic slice: raw NO point prob ~0.875 (YES-belief-in-bin 0.125), but realized
    # NO-win-rate ~0.679. A deep cell (n=104) graded at 0.679. The served lower bound must land near
    # the realized region (~0.65-0.70) so a ~0.70 NO cost FAILS edge_lcb>0.
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw_no_prob = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw_no_prob)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = _artifact({key: {"n": 104, "hit_rate": 0.679}})

    v = sc.apply_selection_calibrator(
        raw_side_prob=raw_no_prob,
        side=side,
        lead_days=1.0,
        bin_class=bin_class,
        admission_margin=raw_no_prob - 0.70,
        artifact=art,
    )
    assert v.trade is True  # a deep cell licenses a calibrated bound (it does not abstain)
    # q_safe is the calibrated lower bound near the realized 0.679 region, NOT the raw 0.875.
    assert v.q_safe < raw_no_prob
    assert 0.55 <= v.q_safe <= 0.71
    # A ~0.70 NO cost no longer clears edge_lcb>0 on the calibrated bound.
    assert v.q_safe - 0.70 <= 0.0


def test_selection_calibrator_preserves_genuine_edge_buy_yes():
    # A cheap buy_yes: raw YES prob ~0.45, cost ~0.30. Its cell realized 0.46 (real coverage above
    # cost). The calibrated lower bound must stay above the 0.30 cost so the genuine edge is NOT
    # zeroed out.
    side, lead_b, bin_class = "YES", "L1", "modal"
    raw_yes_prob = 0.45
    bucket_idx, _ = sc.raw_prob_bucket(raw_yes_prob)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = _artifact({key: {"n": 200, "hit_rate": 0.46}})
    art["_meta"]["armed_sides"] = ["YES", "NO"]

    v = sc.apply_selection_calibrator(
        raw_side_prob=raw_yes_prob,
        side=side,
        lead_days=1.0,
        bin_class=bin_class,
        admission_margin=raw_yes_prob - 0.30,
        artifact=art,
    )
    assert v.trade is True
    assert v.abstained is False
    assert v.basis == "SELECTION_BETA_95"
    # The lower bound clears the 0.30 cost -> genuine edge survives.
    assert v.q_safe - 0.30 > 0.0
    assert v.q_safe <= raw_yes_prob + 1e-9  # still a lower bound


def test_legacy_sel_v1_artifact_is_no_only_so_buy_yes_passes_through():
    # The promoted sel_v1 artifact was fitted for the buy-NO adverse-selection pathology. It may
    # contain sparse YES cells as corpus bookkeeping, but without explicit YES arming those cells
    # are not live authority and must not zero every buy_yes candidate.
    side, lead_b, bin_class = "YES", "L1", "modal"
    raw_yes_prob = 0.65
    bucket_idx, _ = sc.raw_prob_bucket(raw_yes_prob)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = _artifact({key: {"n": 1, "hit_rate": 1.0}})

    v = sc.apply_selection_calibrator(
        raw_side_prob=raw_yes_prob,
        side=side,
        lead_days=1.0,
        bin_class=bin_class,
        admission_margin=0.20,
        artifact=art,
    )
    assert v.trade is True
    assert v.abstained is False
    assert v.basis == "SIDE_NOT_ARMED"
    assert v.q_safe == pytest.approx(raw_yes_prob)


def test_explicitly_armed_yes_missing_cell_still_fails_closed():
    # Future artifacts may arm YES only when their validation licenses it. Once armed, missing/thin
    # YES cells are real live authority failures and remain fail-closed.
    art = _artifact({"NO|L1|nonmodal|pb16": {"n": 104, "hit_rate": 0.679}})
    art["_meta"]["armed_sides"] = ["YES", "NO"]
    v = sc.apply_selection_calibrator(
        raw_side_prob=0.65,
        side="YES",
        lead_days=1.0,
        bin_class="modal",
        admission_margin=0.20,
        artifact=art,
    )
    assert v.trade is False
    assert v.abstained is True
    assert v.q_safe == 0.0
    assert v.basis == "ACTIVE_MISSING_CELL"


def test_fail_closed_on_absent_artifact(monkeypatch):
    # No artifact at all -> the live admission path must NOT fall back to the raw center-bootstrap
    # q_lcb. It emits a no-trade verdict.
    monkeypatch.setattr(sc, "load_artifact", lambda: None)
    sc.reset_artifact_cache()
    v = sc.apply_selection_calibrator(
        raw_side_prob=0.875,
        side="NO",
        lead_days=1.0,
        bin_class="nonmodal",
        admission_margin=0.175,
        artifact=None,
    )
    assert v.trade is False
    assert v.abstained is True
    assert v.q_safe == 0.0
    assert v.basis == "FAIL_CLOSED_NO_ARTIFACT"


def test_fail_closed_on_malformed_artifact():
    v = sc.apply_selection_calibrator(
        raw_side_prob=0.875, side="NO", lead_days=1.0, bin_class="nonmodal",
        admission_margin=0.175, artifact={"garbage": True},
    )
    assert v.trade is False and v.q_safe == 0.0 and v.abstained is True


def test_fail_closed_on_under_min_n_cell():
    # A KNOWN cell that is below min_n is thin -> abstain (never serve a thin-cell rate).
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = _artifact({key: {"n": 5, "hit_rate": 0.679}}, min_n=30)
    v = sc.apply_selection_calibrator(
        raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class,
        admission_margin=0.175, artifact=art,
    )
    assert v.trade is False and v.q_safe == 0.0 and v.abstained is True


def test_fail_closed_on_missing_cell_in_active_artifact():
    # An active artifact that did not grade THIS side/cell -> abstain (no silent authority).
    art = _artifact({"YES|L1|modal|pb9": {"n": 200, "hit_rate": 0.46}})
    v = sc.apply_selection_calibrator(
        raw_side_prob=0.875, side="NO", lead_days=1.0, bin_class="nonmodal",
        admission_margin=0.175, artifact=art,
    )
    assert v.trade is False and v.q_safe == 0.0 and v.abstained is True
    assert v.basis == "ACTIVE_MISSING_CELL"


def test_fail_closed_on_stale_artifact_version():
    # The artifact's posterior_version must match the live posterior version; a stale artifact
    # (different version) is rejected -> no-trade.
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = _artifact({key: {"n": 104, "hit_rate": 0.679}})
    art["_meta"]["posterior_version"] = "SOME_OLD_VERSION"
    v = sc.apply_selection_calibrator(
        raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class,
        admission_margin=0.175, artifact=art,
        expected_posterior_version=sc.DEFAULT_POSTERIOR_VERSION,
    )
    assert v.trade is False and v.q_safe == 0.0 and v.abstained is True
    assert v.basis == "FAIL_CLOSED_STALE_VERSION"


def test_blocker_fix_runtime_and_fitter_version_strings_agree():
    # [BLOCKER, consult REQ-20260622-154643] A freshly-fit artifact (stamped with the FITTER's
    # POSTERIOR_VERSION) must NOT stale-version-fail-close on the runtime DEFAULT path. Regression
    # guard: the two source constants must be identical, and a default-path apply must not be
    # FAIL_CLOSED_STALE_VERSION.
    import scripts.fit_selection_calibrator as fsc
    assert sc.DEFAULT_POSTERIOR_VERSION == fsc.POSTERIOR_VERSION
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    # Artifact stamped exactly as the fitter would stamp it.
    art = {
        "_meta": {"posterior_version": fsc.POSTERIOR_VERSION, "min_n": 30},
        "cells": {key: {"n": 104, "hit_rate": 0.679}},
    }
    v = sc.apply_selection_calibrator(
        raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class, artifact=art,
    )  # default expected_posterior_version
    assert v.basis != "FAIL_CLOSED_STALE_VERSION"
    assert v.trade is True


def test_no_price_anchoring_q_is_single_authority():
    # The calibrated bound is a function of the RAW side prob + settled hit-rate cell ONLY.
    # admission_margin (which carries price) must NOT change q_safe (price is context, never a
    # probability target). Two calls differing only in admission_margin return the same q_safe.
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = _artifact({key: {"n": 104, "hit_rate": 0.679}})
    v1 = sc.apply_selection_calibrator(
        raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class,
        admission_margin=0.10, artifact=art,
    )
    v2 = sc.apply_selection_calibrator(
        raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class,
        admission_margin=0.40, artifact=art,
    )
    assert math.isclose(v1.q_safe, v2.q_safe, rel_tol=1e-12)


# --------------------------------------------------------------------------------------------------
# Seam integration helper (live; no default-off/no-op mode).
# --------------------------------------------------------------------------------------------------

def _no_toxic_artifact():
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    return _artifact({key: {"n": 104, "hit_rate": 0.679}})


def test_seam_helper_is_live_without_env_flag(monkeypatch):
    # The live seam no longer has an env-default-off state. It applies the promoted calibrator
    # directly and deflates the toxic-NO cell.
    monkeypatch.delenv("ZEUS_SELECTION_CALIBRATOR_LIVE", raising=False)
    out = sc.selection_calibrated_side_lcb(
        raw_side_prob=0.875, prior_lcb=0.83, side="NO", lead_days=1.0, bin_class="nonmodal",
        artifact=_no_toxic_artifact(),
    )
    assert out < 0.83
    assert out - 0.70 <= 0.0


def test_seam_helper_live_deflates_toxic_no(monkeypatch):
    # The seam LOWERS the prior bootstrap lcb (0.83) to the calibrated ~0.59, so a 0.70
    # NO cost no longer clears edge_lcb>0.
    out = sc.selection_calibrated_side_lcb(
        raw_side_prob=0.875, prior_lcb=0.83, side="NO", lead_days=1.0, bin_class="nonmodal",
        artifact=_no_toxic_artifact(),
    )
    assert out < 0.83
    assert out - 0.70 <= 0.0  # blocked at a ~0.70 NO cost


def test_seam_helper_live_fail_closed_returns_zero(monkeypatch):
    # No artifact -> fail-closed 0.0 (NOT the prior bootstrap lcb).
    monkeypatch.setattr(sc, "load_artifact", lambda: None)
    sc.reset_artifact_cache()
    out = sc.selection_calibrated_side_lcb(
        raw_side_prob=0.875, prior_lcb=0.83, side="NO", lead_days=1.0, bin_class="nonmodal",
        artifact=None,
    )
    assert out == 0.0


def test_seam_helper_live_never_raises_prior(monkeypatch):
    # The calibrator is a guard: it can only LOWER the served bound. If the calibrated bound is
    # ABOVE the prior, the prior wins (min).
    art = _no_toxic_artifact()  # cell LB ~0.59
    out = sc.selection_calibrated_side_lcb(
        raw_side_prob=0.875, prior_lcb=0.40, side="NO", lead_days=1.0, bin_class="nonmodal",
        artifact=art,
    )
    assert out == 0.40  # prior (0.40) is below the calibrated 0.59 -> min keeps 0.40
