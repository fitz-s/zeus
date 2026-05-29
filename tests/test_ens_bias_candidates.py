# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P3 "REPLACE shrinkage target"; CRITIC_SYNTHESIS_2026-05-29 §2a
#   (asym SEV-1-B). The OpenData serving candidate must be OpenData-ONLY (not the TIGGE-shrunk
#   blend); TIGGE is a separately product-tagged candidate the accept-gate refuses for an
#   OpenData target. Replaces "the posterior blend IS the OpenData estimate" — that blend, at
#   thin live n, leans toward the TIGGE prior that hurts 7/11 buckets.
"""Product-segregated candidate construction for the bias model.

The estimator emits a candidate SET with honest evidence-product tags, not a single blended
posterior. This is what lets the segregation accept-gate (choose_candidate) keep only
same-product candidates — making "serve a TIGGE-shrunk correction on live OpenData"
(sd3 renamed) structurally impossible at the source.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from src.calibration.ens_bias_model import BiasCandidate, build_candidate_biases

# load choose_candidate (script module) for the end-to-end segregation check
_MOD = Path(__file__).resolve().parents[1] / "scripts" / "score_error_model_candidates.py"
_spec = importlib.util.spec_from_file_location("score_error_model_candidates", _MOD)
_sec = importlib.util.module_from_spec(_spec)
sys.modules["score_error_model_candidates"] = _sec
_spec.loader.exec_module(_sec)
choose_candidate = _sec.choose_candidate


def test_opendata_candidate_is_opendata_only_not_tigge_shrunk():
    """OpenData residuals ~ -1.0; TIGGE prior ~ -5.0 (harmful). The OpenData candidate must be
    the OpenData-only mean (~-1.0), NOT pulled toward -5.0."""
    cands = build_candidate_biases(
        target_product="mx2t3",
        opendata_residuals=[-1.0, -1.1, -0.9, -1.0, -1.05],
        tigge_residuals=[-5.0] * 50,
    )
    assert isinstance(cands["opendata_bias"], BiasCandidate)
    assert cands["opendata_bias"].evidence_product == "mx2t3"
    assert abs(cands["opendata_bias"].bias - (-1.0)) < 0.2  # NOT shrunk toward -5.0


def test_tigge_candidate_is_tagged_cross_product():
    cands = build_candidate_biases(
        target_product="mx2t3", opendata_residuals=[-1.0] * 5, tigge_residuals=[-5.0] * 50
    )
    assert cands["tigge_prior"].evidence_product != "mx2t3"  # cross-product => gate will refuse


def test_raw_candidate_present_zero_bias_same_product():
    cands = build_candidate_biases(
        target_product="mx2t3", opendata_residuals=[], tigge_residuals=[-5.0] * 10
    )
    assert cands["raw"].bias == 0.0
    assert cands["raw"].evidence_product == "mx2t3"


def test_no_opendata_yields_no_opendata_candidate():
    cands = build_candidate_biases(
        target_product="mx2t3", opendata_residuals=[], tigge_residuals=[-5.0] * 10
    )
    assert "opendata_bias" not in cands
    assert "tigge_prior" in cands  # present but cross-product


def test_end_to_end_tigge_refused_opendata_eligible():
    """Even if BOTH candidates have stellar (mismatched-product) scores, the gate keeps only the
    same-product OpenData candidate; TIGGE is refused as cross-product."""
    cands = build_candidate_biases(
        target_product="mx2t3", opendata_residuals=[-1.0] * 25, tigge_residuals=[-5.0] * 50
    )
    products = {name: c.evidence_product for name, c in cands.items()}
    raw_metrics = {"logloss": 1.0, "rps": 0.5, "brier": 0.25}
    better = {"logloss": 0.8, "rps": 0.4, "brier": 0.2}
    decision = choose_candidate(
        candidate_metrics={"opendata_bias": better, "tigge_prior": better},
        raw_metrics=raw_metrics,
        improvement_lcb={"opendata_bias": 0.05, "tigge_prior": 0.09},
        catastrophic={"opendata_bias": False, "tigge_prior": False},
        target_product="mx2t3",
        candidate_products=products,
    )
    assert "tigge_prior" in decision.refused_cross_product
    assert decision.chosen == "opendata_bias"
