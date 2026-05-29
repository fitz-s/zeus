# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P3 + CRITIC_SYNTHESIS_2026-05-29 §2a (asym SEV-1-B: 6h-TIGGE and
#   3h-OpenData are DIFFERENT random variables; a correction proven on one must not serve the
#   other). The accept-gate antibody against "sd3 renamed": a candidate may be selected ONLY if
#   its OOS evidence was computed on the SAME product it will serve.
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Antibody asserting choose_candidate refuses cross-product evidence — a TIGGE-proven candidate cannot serve an OpenData target.
# Reuse: Run after any change to choose_candidate or evidence_product tagging in score_error_model_candidates.
"""Product-segregation invariant for the candidate accept-gate.

The legacy ledger collapsed products; the asymmetry critic showed TIGGE→OpenData transfer
HURTS 7/11 buckets. So a candidate whose OOS evidence is on a different product than the
serving target is refused outright — same-product proof is mandatory, regardless of how good
its (cross-product) scores look. Transfer is allowed ONLY when it was OOS-tested on the target
product itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "score_error_model_candidates.py"
_spec = importlib.util.spec_from_file_location("score_error_model_candidates", _MOD_PATH)
sec = importlib.util.module_from_spec(_spec)
sys.modules["score_error_model_candidates"] = sec
_spec.loader.exec_module(sec)
choose_candidate = sec.choose_candidate

RAW = {"logloss": 1.000, "rps": 0.500, "brier": 0.250}


def _better(delta):
    return {"logloss": 1.000 - delta, "rps": 0.500 - delta, "brier": 0.250 - delta}


def test_cross_product_candidate_refused_even_if_it_beats_raw():
    """A TIGGE (mx2t6) candidate must NOT serve an OpenData (mx2t3) target, even with a
    healthy in-product win + LCB>0 — its evidence is on the wrong RV. This is the sd3-rearm
    antibody: a TIGGE-proven correction cannot be applied to live OpenData."""
    d = choose_candidate(
        candidate_metrics={"tigge_prior": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"tigge_prior": 0.05},
        catastrophic={"tigge_prior": False},
        target_product="mx2t3",
        candidate_products={"tigge_prior": "mx2t6"},
    )
    assert d.chosen == "raw"
    assert d.raw_is_default is True
    assert "tigge_prior" in d.refused_cross_product


def test_same_product_candidate_allowed():
    d = choose_candidate(
        candidate_metrics={"opd_bias": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"opd_bias": 0.05},
        catastrophic={"opd_bias": False},
        target_product="mx2t3",
        candidate_products={"opd_bias": "mx2t3"},
    )
    assert d.chosen == "opd_bias"
    assert d.refused_cross_product == []


def test_transfer_candidate_proven_on_target_product_allowed():
    """A transfer correction is fine ONLY when its OOS evidence is on the target product."""
    d = choose_candidate(
        candidate_metrics={"transported": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"transported": 0.05},
        catastrophic={"transported": False},
        target_product="mx2t3",
        candidate_products={"transported": "mx2t3"},
    )
    assert d.chosen == "transported"


def test_undeclared_evidence_product_is_refused_fail_closed():
    """No declared evidence product => cannot prove same-product => refused (fail-closed)."""
    d = choose_candidate(
        candidate_metrics={"mystery": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"mystery": 0.05},
        catastrophic={"mystery": False},
        target_product="mx2t3",
        candidate_products={},
    )
    assert d.chosen == "raw"
    assert "mystery" in d.refused_cross_product


def test_mixed_cross_and_same_product_only_same_survives():
    d = choose_candidate(
        candidate_metrics={"tigge_prior": _better(0.20), "opd_bias": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"tigge_prior": 0.09, "opd_bias": 0.04},
        catastrophic={"tigge_prior": False, "opd_bias": False},
        target_product="mx2t3",
        candidate_products={"tigge_prior": "mx2t6", "opd_bias": "mx2t3"},
    )
    # tigge_prior has the bigger LCB but is cross-product => refused; opd_bias wins.
    assert d.chosen == "opd_bias"
    assert d.refused_cross_product == ["tigge_prior"]
