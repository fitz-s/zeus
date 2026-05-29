# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL Findings 1+5 — lead/cycle/product keyed ENS bias correction.
"""Relationship tests: candidate scorer buckets by full key (city × metric × season ×
product × cycle × lead_bucket); refuses to compare across product, cycle, or lead_bucket.

Tests covered:
  1. choose_candidate: refuses a candidate whose product != target_product.
  2. score_bucket: raises AssertionError when rows from two different lead buckets are mixed.
  3. score_bucket: rows all from the same lead bucket pass the assertion (no raise).
  4. run_scoring: manifest records cycle + lead_bucket fields.
  5. Two run_scoring calls with different lead_bucket produce different manifests.
  6. choose_candidate: raw wins when no candidate has LCB > 0 (no same-bucket improvement).
"""
from __future__ import annotations

import pytest

from scripts.score_error_model_candidates import choose_candidate, run_scoring


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_city_stub(settlement_unit: str = "C"):
    """Minimal city-like object for score_bucket."""
    class _City:
        name = "TestCity"
    return _City()


def _raw_metrics() -> dict[str, float]:
    return {"logloss": 2.0, "rps": 1.5, "brier": 0.8}


def _better_candidate_metrics() -> dict[str, float]:
    """Candidate that strictly beats raw on all 3 proper scores."""
    return {"logloss": 1.5, "rps": 1.2, "brier": 0.6}


def _worse_candidate_metrics() -> dict[str, float]:
    return {"logloss": 2.5, "rps": 1.8, "brier": 1.0}


# ---------------------------------------------------------------------------
# 1. choose_candidate refuses cross-product candidates
# ---------------------------------------------------------------------------

def test_choose_candidate_refuses_cross_product():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better_candidate_metrics()},
        raw_metrics=_raw_metrics(),
        improvement_lcb={"bias_v1": 0.05},
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t6"},  # WRONG product
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True
    assert "bias_v1" in decision.refused_cross_product


def test_choose_candidate_accepts_same_product():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better_candidate_metrics()},
        raw_metrics=_raw_metrics(),
        improvement_lcb={"bias_v1": 0.05},
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},  # correct product
    )
    assert decision.chosen == "bias_v1"
    assert decision.raw_is_default is False


# ---------------------------------------------------------------------------
# 2. score_bucket raises AssertionError on cross-lead-bucket rows
# ---------------------------------------------------------------------------

def _make_rows_with_lead(lead_hours_list: list[float]) -> list[dict]:
    """Minimal rows with lead_hours, members_json, settlement, target_date, members_unit."""
    import json
    rows = []
    for i, lh in enumerate(lead_hours_list):
        rows.append({
            "lead_hours": lh,
            "target_date": f"2025-07-{(i % 28) + 1:02d}",
            "members_json": json.dumps([20.0, 21.0, 22.0, 21.5, 20.5]),
            "settlement_value_c": 21.0,
            "members_unit": "C",
            "issue_time": "2025-07-01T00:00:00+00:00",
        })
    return rows


def test_score_bucket_rejects_cross_lead_bucket_rows():
    """Passing rows from L00_24 and L24_48 together with lead_bucket='L00_24' must raise."""
    # lead=6 → L00_24, lead=36 → L24_48: mixing them is cross-bucket
    rows = _make_rows_with_lead([6.0, 36.0])
    city = _make_city_stub()

    with pytest.raises(AssertionError, match="lead_bucket"):
        from scripts.score_error_model_candidates import score_bucket
        score_bucket(rows, city, "mx2t3", lead_bucket="L00_24")


def test_score_bucket_accepts_uniform_lead_bucket_rows():
    """All rows in the same bucket must NOT raise the cross-bucket assertion."""
    rows = _make_rows_with_lead([6.0, 12.0, 18.0])  # all L00_24
    city = _make_city_stub()

    from scripts.score_error_model_candidates import score_bucket
    # This will fail for other reasons (missing grid/settlement infrastructure) but
    # must NOT raise the cross-bucket AssertionError — the assertion must pass.
    try:
        score_bucket(rows, city, "mx2t3", lead_bucket="L00_24")
    except AssertionError as e:
        if "lead_bucket" in str(e):
            pytest.fail(f"Unexpected cross-bucket AssertionError: {e}")
    except Exception:
        pass  # Other errors (missing grid etc.) are acceptable here


# ---------------------------------------------------------------------------
# 3. run_scoring manifest records cycle + lead_bucket
# ---------------------------------------------------------------------------

def test_run_scoring_manifest_includes_cycle_and_lead_bucket():
    """run_scoring should propagate cycle and lead_bucket into the returned manifest."""
    # We can't run a real score_bucket without DB/grid infrastructure, but we can
    # verify the manifest dict structure by monkey-patching score_bucket to return
    # a stub result.
    import scripts.score_error_model_candidates as mod

    # Stub out score_bucket to avoid infrastructure deps
    def _stub_score_bucket(rows, city, target_product, *, k_folds=5, cycle=None, lead_bucket=None):
        cand_metrics = {}
        raw_metrics = _raw_metrics()
        improvement_lcb = {}
        catastrophic = {}
        candidate_products = {}
        return cand_metrics, raw_metrics, improvement_lcb, catastrophic, candidate_products

    original = mod.score_bucket
    mod.score_bucket = _stub_score_bucket
    try:
        manifest = mod.run_scoring(
            evidence_rows=[],
            city=_make_city_stub(),
            target_product="mx2t3",
            cycle="00z",
            lead_bucket="L24_48",
        )
    finally:
        mod.score_bucket = original

    assert manifest["cycle"] == "00z"
    assert manifest["lead_bucket"] == "L24_48"


# ---------------------------------------------------------------------------
# 4. Two manifests with different lead_bucket have different lead_bucket field
# ---------------------------------------------------------------------------

def test_manifests_differ_by_lead_bucket():
    import scripts.score_error_model_candidates as mod

    def _stub_score_bucket(rows, city, target_product, *, k_folds=5, cycle=None, lead_bucket=None):
        return {}, _raw_metrics(), {}, {}, {}

    original = mod.score_bucket
    mod.score_bucket = _stub_score_bucket
    try:
        m1 = mod.run_scoring([], _make_city_stub(), "mx2t3", cycle="00z", lead_bucket="L00_24")
        m2 = mod.run_scoring([], _make_city_stub(), "mx2t3", cycle="00z", lead_bucket="L24_48")
    finally:
        mod.score_bucket = original

    assert m1["lead_bucket"] != m2["lead_bucket"]
    assert m1["cycle"] == m2["cycle"]


# ---------------------------------------------------------------------------
# 5. choose_candidate: raw wins when no candidate LCB > 0
# ---------------------------------------------------------------------------

def test_choose_candidate_raw_wins_with_negative_lcb():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better_candidate_metrics()},
        raw_metrics=_raw_metrics(),
        improvement_lcb={"bias_v1": -0.01},  # LCB <= 0: no improvement guarantee
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


def test_choose_candidate_raw_wins_with_zero_lcb():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better_candidate_metrics()},
        raw_metrics=_raw_metrics(),
        improvement_lcb={"bias_v1": 0.0},  # exactly 0: strict > 0 required
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


def test_choose_candidate_raw_wins_with_catastrophic_regression():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better_candidate_metrics()},
        raw_metrics=_raw_metrics(),
        improvement_lcb={"bias_v1": 0.1},
        catastrophic={"bias_v1": True},  # veto flag set
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


def test_choose_candidate_raw_wins_without_enough_proper_score_wins():
    # Candidate only beats raw on 1 of 3 proper scores (need >= 2)
    marginal_cand = {"logloss": 1.5, "rps": 1.6, "brier": 0.9}  # only logloss better
    decision = choose_candidate(
        candidate_metrics={"bias_v1": marginal_cand},
        raw_metrics=_raw_metrics(),
        improvement_lcb={"bias_v1": 0.05},
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


# ---------------------------------------------------------------------------
# 6. Multiple candidates: best LCB wins among passing
# ---------------------------------------------------------------------------

def test_choose_candidate_picks_best_lcb_among_passing():
    decision = choose_candidate(
        candidate_metrics={
            "cand_a": _better_candidate_metrics(),
            "cand_b": _better_candidate_metrics(),
        },
        raw_metrics=_raw_metrics(),
        improvement_lcb={"cand_a": 0.03, "cand_b": 0.08},  # cand_b has higher LCB
        catastrophic={"cand_a": False, "cand_b": False},
        target_product="mx2t3",
        candidate_products={"cand_a": "mx2t3", "cand_b": "mx2t3"},
    )
    assert decision.chosen == "cand_b"
    assert decision.raw_is_default is False
