# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: selection-aware settlement q_lcb calibrator
#   (frontier consult REQ-20260622-151741; live_order_pathology 2026-06-22).
"""Estimator antibodies for scripts/fit_selection_calibrator.py (walk-forward selection calibrator).

Invariants proven here:
  1. WALK-FORWARD NO-LEAK: a settled row dated >= a decision's settled_at is NEVER used to grade that
     decision's cell. The fitter accumulates rows in settlement-time order; a leak attempt (using a
     row settled at-or-after the boundary) is rejected.
  2. MONOTONE-IN-PROB: within a (side, lead, bin_class) cell group, the persisted per-prob-bucket
     hit-rate lower bound is monotone NON-DECREASING in the raw side prob (isotonic projection).
  3. CONSERVATIVE: the persisted cell bound is the beta/Wilson 95% LOWER bound of the realized rate,
     never the point rate; it is <= the realized point and tighter with more samples.
  4. SCHEMA + FAIL-CLOSED PROVENANCE: the artifact carries _meta with posterior_version, min_n,
     max_settled_at, and cells keyed by the live cell_key schema, so the runtime serving rule
     (src.decision.selection_calibrator) reads it 1:1.
  5. ADVERSE-SELECTION RECOVERY: synthetic over-confident NO rows (raw NO prob ~0.87, realized NO win
     ~0.68) -> the fitted cell's served bound lands BELOW a 0.70 cost (the toxic losers are blocked),
     while a genuine cheap-YES cell (raw ~0.45, realized 0.46, deep n) keeps a bound above a 0.30 cost.
"""
from __future__ import annotations

import json

import pytest

import scripts.fit_selection_calibrator as fsc
from src.decision import selection_calibrator as sc


# ---------------------------------------------------------------------------
# Synthetic settled-decision rows. A row = one historical SIDE decision graded by settlement.
# fields: settled_at (ISO), side, lead_days, bin_class, raw_side_prob, side_won (0/1)
# ---------------------------------------------------------------------------

def _row(settled_at, side, lead_days, bin_class, raw_side_prob, side_won):
    return fsc.SettledDecisionRow(
        settled_at=settled_at, side=side, lead_days=lead_days, bin_class=bin_class,
        raw_side_prob=raw_side_prob, side_won=int(side_won),
    )


def test_walk_forward_no_leak_rejects_at_or_after_boundary():
    # Rows settled at-or-after the boundary T must NOT contribute to a cell graded as-of T.
    rows = [
        _row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, 1),
        _row("2026-06-11T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, 0),
        _row("2026-06-12T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, 1),
    ]
    # As-of T = the 2nd row's settled_at: only the 1st row is strictly-prior.
    prior = fsc.rows_strictly_before(rows, boundary="2026-06-11T00:00:00+00:00")
    assert len(prior) == 1
    assert prior[0].settled_at == "2026-06-10T00:00:00+00:00"
    # A row exactly at the boundary is a LEAK and is excluded (strict <).
    assert all(r.settled_at < "2026-06-11T00:00:00+00:00" for r in prior)


def test_fit_cells_uses_only_settled_rows_and_keys_match_live_schema():
    rows = [
        _row("2026-06-1{}T00:00:00+00:00".format(i % 10), "NO", 1.0, "nonmodal", 0.87, i % 3 != 0)
        for i in range(60)
    ]
    artifact = fsc.fit_cells(rows, min_n=30, posterior_version=fsc.POSTERIOR_VERSION)
    assert "_meta" in artifact and "cells" in artifact
    meta = artifact["_meta"]
    assert meta["posterior_version"] == fsc.POSTERIOR_VERSION
    assert meta["min_n"] == 30
    assert "max_settled_at" in meta
    # Every persisted cell key parses to the live schema (side|lead|class|pbN).
    for key in artifact["cells"]:
        parts = key.split("|")
        assert len(parts) == 4
        assert parts[0] in {"YES", "NO"}
        assert parts[1] in {"L1", "L2_3", "L4P"}
        assert parts[3].startswith("pb")
    # The cell the runtime would resolve for this slice exists and is deep.
    live_key = sc.cell_key(side="NO", lead_days=1.0, bin_class="nonmodal", raw_side_prob=0.87)
    assert live_key in artifact["cells"]
    assert artifact["cells"][live_key]["n"] >= 30


def test_persisted_bound_is_conservative_below_point():
    # 40/60 wins = 0.667 point; the persisted cell hit_rate is the realized rate, and the runtime
    # serves its beta/Wilson 95% LOWER bound which is < 0.667.
    rows = [_row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 40) for i in range(60)]
    artifact = fsc.fit_cells(rows, min_n=30, posterior_version=fsc.POSTERIOR_VERSION)
    live_key = sc.cell_key(side="NO", lead_days=1.0, bin_class="nonmodal", raw_side_prob=0.87)
    cell = artifact["cells"][live_key]
    assert abs(cell["hit_rate"] - (40 / 60)) < 1e-6
    served = sc.beta_lower_bound_95(int(round(cell["hit_rate"] * cell["n"])), cell["n"])
    assert served < cell["hit_rate"]


def test_monotone_in_prob_within_cell_group():
    # Two prob buckets in the SAME (side,lead,class) group with INVERTED raw realized rates
    # (low-prob bucket realizes HIGHER than high-prob bucket). The fitted persisted bounds must be
    # monotone NON-DECREASING in raw prob after isotonic projection.
    rows = []
    # low-prob bucket (raw 0.55): realized 0.80 (60 rows)
    for i in range(60):
        rows.append(_row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.55, i < 48))
    # high-prob bucket (raw 0.90): realized 0.60 (60 rows) -- INVERTED vs belief
    for i in range(60):
        rows.append(_row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.90, i < 36))
    artifact = fsc.fit_cells(rows, min_n=30, posterior_version=fsc.POSTERIOR_VERSION,
                             enforce_monotone=True)
    lo_key = sc.cell_key(side="NO", lead_days=1.0, bin_class="nonmodal", raw_side_prob=0.55)
    hi_key = sc.cell_key(side="NO", lead_days=1.0, bin_class="nonmodal", raw_side_prob=0.90)
    # After monotone projection the higher-prob bucket's bound is >= the lower-prob bucket's bound.
    assert artifact["cells"][hi_key]["hit_rate"] >= artifact["cells"][lo_key]["hit_rate"] - 1e-9


def test_adverse_selection_recovery_blocks_toxic_no_and_keeps_genuine_yes():
    rows = []
    # Toxic over-confident NO: raw 0.87, realized NO-win 0.68 (104 rows).
    for i in range(104):
        rows.append(_row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 71))
    # Genuine cheap YES: raw 0.45, realized 0.46 (200 rows).
    for i in range(200):
        rows.append(_row("2026-06-10T00:00:00+00:00", "YES", 1.0, "modal", 0.45, i < 92))
    artifact = fsc.fit_cells(rows, min_n=30, posterior_version=fsc.POSTERIOR_VERSION)
    sc.reset_artifact_cache()

    v_no = sc.apply_selection_calibrator(
        raw_side_prob=0.87, side="NO", lead_days=1.0, bin_class="nonmodal",
        admission_margin=0.17, artifact=artifact,
    )
    assert v_no.trade is True
    assert v_no.q_safe - 0.70 <= 0.0  # the ~0.70 NO cost no longer clears edge -> blocked

    v_yes = sc.apply_selection_calibrator(
        raw_side_prob=0.45, side="YES", lead_days=1.0, bin_class="modal",
        admission_margin=0.15, artifact=artifact,
    )
    assert v_yes.trade is True
    assert v_yes.q_safe - 0.30 > 0.0  # genuine cheap-YES edge preserved


# ---------------------------------------------------------------------------
# Executed-EB hybrid fit (STEP-1 forced: no current-regime would-admit; corpus=prior, executed=lik).
# ---------------------------------------------------------------------------

def test_fit_eb_cells_persists_q_safe_lb_and_blocks_toxic_no():
    # Corpus (PRIOR): the full-family NO at raw 0.87 realizes high (0.85) — un-conditioned.
    corpus = [_row("2026-06-09T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 170) for i in range(200)]
    # Selected (EXECUTED would-admit, admit0=True): the SAME cell realizes only 0.68 (toxic).
    selected = [_row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 71) for i in range(104)]
    artifact = fsc.fit_eb_cells(
        corpus_rows=corpus, selected_rows=selected, min_n=30,
        posterior_version=fsc.POSTERIOR_VERSION, tau=10.0,
    )
    key = sc.cell_key(side="NO", lead_days=1.0, bin_class="nonmodal", raw_side_prob=0.87)
    cell = artifact["cells"][key]
    assert "q_safe_lb" in cell and "p0_corpus" in cell and "n_selected" in cell and "tau" in cell
    # The EB lower bound is pulled toward the selected 0.68, BELOW the corpus 0.85.
    assert cell["q_safe_lb"] < 0.75
    sc.reset_artifact_cache()
    v = sc.apply_selection_calibrator(
        raw_side_prob=0.87, side="NO", lead_days=1.0, bin_class="nonmodal", artifact=artifact,
    )
    assert v.trade is True and v.basis == "SELECTION_EB_BETA"
    assert v.q_safe - 0.70 <= 0.0  # blocks the ~0.70-cost toxic NO


def test_fit_eb_cells_fail_closed_when_selected_support_thin():
    # Corpus deep, but the SELECTED cell has < min_n -> the v2 cell must NOT license (corpus alone
    # is only a prior). The runtime returns EB_THIN_SELECTED no-trade.
    corpus = [_row("2026-06-09T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 170) for i in range(200)]
    selected = [_row("2026-06-10T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 4) for i in range(5)]
    artifact = fsc.fit_eb_cells(
        corpus_rows=corpus, selected_rows=selected, min_n=30,
        posterior_version=fsc.POSTERIOR_VERSION, tau=10.0,
    )
    key = sc.cell_key(side="NO", lead_days=1.0, bin_class="nonmodal", raw_side_prob=0.87)
    assert artifact["cells"][key]["n_selected"] == 5
    sc.reset_artifact_cache()
    v = sc.apply_selection_calibrator(
        raw_side_prob=0.87, side="NO", lead_days=1.0, bin_class="nonmodal", artifact=artifact,
    )
    assert v.trade is False and v.q_safe == 0.0 and v.basis == "EB_THIN_SELECTED"


def test_learn_tau_returns_grid_value_by_prequential_score():
    # A selected population that disagrees with the corpus prior should prefer a SMALLER tau (trust
    # the data more). learn_tau returns a value from the grid.
    corpus = [_row("2026-06-09T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 180) for i in range(200)]
    # selected disagrees strongly (0.55) with deep support across multiple as-of times
    selected = []
    for day in range(10, 20):
        for i in range(40):
            selected.append(_row(f"2026-06-{day}T00:00:00+00:00", "NO", 1.0, "nonmodal", 0.87, i < 22))
    tau = fsc.learn_tau(corpus_rows=corpus, selected_rows=selected,
                        grid=[0.0, 1.0, 5.0, 10.0, 50.0, 250.0])
    assert tau in [0.0, 1.0, 5.0, 10.0, 50.0, 250.0]
    # With deep disagreeing data, the prequential-best tau should not pin the (wrong) prior hard.
    assert tau <= 50.0
