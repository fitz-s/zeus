#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-29
# Authority basis: HANDOFF_STAT_REFACTOR_2026-05-29 §4 item-1; operator redesign 2026-05-28 — evidence-ledger-backed candidate selection.
#   Principle 1: raw baseline DOMINATES unless a correction proves OOS improvement.
#   Principle 2: a bias correction is a candidate, not an entitlement.
#   Accept rule (operator, verbatim): "Accept candidate only if: candidate beats raw on at
#   least 2 of 3 proper scores AND bootstrap LCB(improvement) > 0 AND no catastrophic cohort
#   regression. If none pass: use raw identity."
# Lifecycle: created=2026-05-28; last_reviewed=2026-05-29; last_reused=never
# Purpose: Candidate model selection gate — emits a candidate_selection_manifest; never touches live trading.
# Reuse: Inspect OOS evidence ledger source (must be cycle-strict, product-segregated) and bootstrap parameters before relying on manifest.
"""Candidate model selection — the accept-gate that makes "promote a correction that did not
beat raw OOS" structurally unwritable.

This module is built bottom-up. The SELECTION RULE (`choose_candidate`) is independent of how
the proper scores were computed, so it is pinned first by relationship tests
(`tests/test_t4_selection_rule_invariants.py`). The scoring path — per-bucket candidate
construction {raw, scale-only, prior-bias, live-bias, transported, hierarchical-fallback},
blocked-by-target_date OOS folds, re-MC of each candidate's p_raw distribution via the
PRODUCTION sampler `src.signal.ensemble_signal.p_raw_vector_from_maxes`, proper scoring vs
SETTLEMENT, and the bootstrap LCB of improvement — plugs into this rule and is wired in a
follow-up commit (it depends on the bin-grid source confirmed by the baseline proper-score run).

NOTHING here decides live trading. It emits a `candidate_selection_manifest` only.

Scoring API (wired in Phase 3 / this commit):
  score_bucket(rows, city, target_product, *, k_folds) -> (candidate_metrics, raw_metrics,
                                                             improvement_lcb, catastrophic,
                                                             candidate_products)
  run_scoring(evidence_rows, city, target_product) -> dict  (candidate_selection_manifest)
"""
from __future__ import annotations

import json
import math
import numpy as np
from dataclasses import dataclass, field

# All three are "lower is better".
PROPER_SCORES: tuple[str, ...] = ("logloss", "rps", "brier")

# A correction must beat raw on at least this many of the PROPER_SCORES to be eligible.
MIN_PROPER_SCORE_WINS = 2


@dataclass(frozen=True)
class CandidateDecision:
    """Outcome of the accept-gate for one (city, metric, season) bucket."""
    chosen: str
    reason: str
    raw_is_default: bool
    beats_raw_count: dict[str, int] = field(default_factory=dict)
    passing: list[str] = field(default_factory=list)
    refused_cross_product: list[str] = field(default_factory=list)


def _is_real(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _beats_raw_count(cand: dict[str, float], raw: dict[str, float]) -> int:
    """Count PROPER_SCORES where candidate strictly improves on raw (lower=better).

    A missing or NaN candidate/raw score for a metric does NOT count as a win (conservative:
    we never credit a correction for a score we could not compute).
    """
    n = 0
    for m in PROPER_SCORES:
        c, r = cand.get(m), raw.get(m)
        if _is_real(c) and _is_real(r) and c < r:
            n += 1
    return n


def choose_candidate(
    candidate_metrics: dict[str, dict[str, float]],
    raw_metrics: dict[str, float],
    improvement_lcb: dict[str, float],
    catastrophic: dict[str, bool],
    *,
    target_product: str,
    candidate_products: dict[str, str],
    raw_name: str = "raw",
) -> CandidateDecision:
    """Select the model for one bucket. Returns raw_name unless a correction clears the gate.

    Parameters
    ----------
    candidate_metrics : name -> {"logloss":, "rps":, "brier":} on held-out folds. The raw
        identity may be present here too; it is never selected over itself.
    raw_metrics : raw identity's held-out {"logloss":, "rps":, "brier":} (the baseline to beat).
    improvement_lcb : name -> bootstrap lower-confidence-bound of (raw_aggregate -
        candidate_aggregate) across OOS folds. > 0 means even the pessimistic bound shows the
        candidate beating raw out-of-sample.
    catastrophic : name -> True if the candidate catastrophically regresses ANY cohort
        (a hard veto regardless of aggregate wins).

    Gate (ALL required): beats_raw_count >= MIN_PROPER_SCORE_WINS (2/3) AND improvement_lcb > 0
    AND not catastrophic. Among passing candidates, pick the one with the largest
    improvement_lcb (most robust worst-case OOS gain). If none pass: raw identity.
    """
    beats: dict[str, int] = {}
    passing: list[str] = []
    refused_cross_product: list[str] = []
    for name, cand in candidate_metrics.items():
        if name == raw_name:
            continue
        # Product-segregation gate (asym SEV-1-B): a candidate may serve ONLY the product its
        # OOS evidence was computed on. 6h-TIGGE (mx2t6) and 3h-OpenData (mx2t3) are different
        # random variables; TIGGE→OpenData transfer HURTS 7/11 buckets. A candidate whose
        # evidence product differs from (or is undeclared for) the serving target is refused
        # outright — no matter how strong its (wrong-product) scores look. Transfer is allowed
        # ONLY when it was OOS-tested on the target product itself. This makes "apply a
        # TIGGE-proven correction to live OpenData" (sd3 renamed) unconstructable.
        if candidate_products.get(name) != target_product:
            refused_cross_product.append(name)
            continue
        b = _beats_raw_count(cand, raw_metrics)
        beats[name] = b
        lcb = improvement_lcb.get(name)
        if (
            b >= MIN_PROPER_SCORE_WINS
            and _is_real(lcb)
            and lcb > 0
            and not catastrophic.get(name, False)
        ):
            passing.append(name)

    if not passing:
        return CandidateDecision(
            chosen=raw_name,
            reason="no candidate cleared the OOS gate (same-product evidence + >=2/3 proper-score wins + LCB>0 + no catastrophe); raw identity dominates",
            raw_is_default=True,
            beats_raw_count=beats,
            passing=[],
            refused_cross_product=refused_cross_product,
        )

    chosen = max(passing, key=lambda n: improvement_lcb[n])
    return CandidateDecision(
        chosen=chosen,
        reason=f"beats raw on {beats[chosen]}/3 proper scores, bootstrap LCB(improvement)={improvement_lcb[chosen]:+.4f}>0, no catastrophic cohort regression; selected over {len(passing)} passing candidate(s) by max LCB",
        raw_is_default=False,
        beats_raw_count=beats,
        passing=sorted(passing),
        refused_cross_product=refused_cross_product,
    )


# ---------------------------------------------------------------------------
# Scoring path — Phase 3 implementation
# ---------------------------------------------------------------------------

_LOG_EPS = 1e-9  # logloss floor applied identically to raw and all candidates


def _proper_scores_for_row(
    p_vec: np.ndarray,
    winning_bin_idx: int,
) -> dict[str, float]:
    """Compute logloss, brier, rps for one row given a probability vector and winning bin index.

    All three are lower-is-better.
    logloss = -log(max(p[winning], eps))
    brier   = sum((p - onehot)^2)  — mean-squared error of probability forecast
    rps     = sum over bins of (CDF_forecast - CDF_onehot)^2 — ranked probability score

    eps applied identically to raw and every candidate (fairness: same floor).
    """
    n = len(p_vec)
    onehot = np.zeros(n, dtype=float)
    onehot[winning_bin_idx] = 1.0

    logloss = -math.log(max(float(p_vec[winning_bin_idx]), _LOG_EPS))

    diff = p_vec - onehot
    brier = float(np.dot(diff, diff))

    cum_p = np.cumsum(p_vec)
    cum_oh = np.cumsum(onehot)
    cum_diff = cum_p - cum_oh
    rps = float(np.dot(cum_diff, cum_diff))

    return {"logloss": logloss, "brier": brier, "rps": rps}


def _aggregate_scores_by_date(
    per_row_scores: list[tuple[str, dict[str, float]]],
) -> dict[str, dict[str, float]]:
    """Aggregate per-row proper scores by target_date (mean across rows with same date).

    Returns {date: {"logloss":, "brier":, "rps":}}.
    """
    from collections import defaultdict
    acc: dict[str, list[dict[str, float]]] = defaultdict(list)
    for date, scores in per_row_scores:
        acc[date].append(scores)
    result: dict[str, dict[str, float]] = {}
    for date, score_list in acc.items():
        result[date] = {
            m: float(np.mean([s[m] for s in score_list]))
            for m in PROPER_SCORES
        }
    return result


def _score_candidate_oos(
    rows: list[dict],
    fold_assignments: list[int],
    k_folds: int,
    bias: float,
    city,
    settlement_semantics,
    grid_bins: list,
    grid,
) -> dict[str, dict[str, float]]:
    """Compute OOS per-date proper scores for one candidate bias.

    For each fold f in [0, k_folds):
      - train rows = rows where fold_assignment != f (used to conceptually validate bias fit)
      - test rows  = rows where fold_assignment == f  (SCORED here)
    bias is applied to member_maxes pre-MC (corrected = raw - bias).
    Returns {target_date: {"logloss":, "brier":, "rps":}} aggregated across all folds.

    NO TEMPORAL LEAKAGE: only test-fold rows are scored; bias was fit externally on train folds.
    UNIT invariant asserted by caller (score_bucket).
    """
    from src.signal.ensemble_signal import p_raw_vector_from_maxes

    per_row_scores: list[tuple[str, dict[str, float]]] = []

    for fold in range(k_folds):
        test_indices = [i for i, f in enumerate(fold_assignments) if f == fold]
        if not test_indices:
            continue

        for i in test_indices:
            row = rows[i]
            member_maxes = np.array(json.loads(row["members_json"]), dtype=float)
            settlement_native = float(row["settlement_value_c"])  # misnomer: actually native unit
            target_date = row["target_date"]

            # Apply bias correction pre-MC (corrected = raw_member - bias)
            corrected_maxes = member_maxes - bias

            p_vec = p_raw_vector_from_maxes(
                corrected_maxes,
                city,
                settlement_semantics,
                grid_bins,
            )

            winning_bin = grid.bin_for_value(settlement_native)
            winning_bin_idx = grid_bins.index(winning_bin)

            row_scores = _proper_scores_for_row(p_vec, winning_bin_idx)
            per_row_scores.append((target_date, row_scores))

    return _aggregate_scores_by_date(per_row_scores)


def score_bucket(
    rows: list[dict],
    city,
    target_product: str,
    *,
    k_folds: int = 5,
) -> tuple[dict, dict, dict, dict, dict]:
    """Compute proper-score inputs for choose_candidate for one (city, product) bucket.

    Returns (candidate_metrics, raw_metrics, improvement_lcb, catastrophic, candidate_products).
    candidate_metrics and raw_metrics: name -> {"logloss":, "rps":, "brier":}.
    improvement_lcb: name -> float (bootstrap LCB of score_raw - score_cand per date).
    catastrophic: name -> bool (True if any cohort regresses badly).
    candidate_products: name -> evidence_product string.

    NO TEMPORAL LEAKAGE invariant: each candidate is fit on k-1 folds; scored on fold k.
    UNIT invariant: members_unit must equal city.settlement_unit (hard-assert, fail-closed).
    """
    from src.calibration.ens_bias_model import build_candidate_biases, robust_mean
    from src.calibration.oos_gate import (
        bh_fdr_accept,
        date_blocked_folds,
        moving_block_bootstrap_lcb,
    )
    from src.contracts.calibration_bins import grid_for_city
    from src.contracts.settlement_semantics import SettlementSemantics

    if not rows:
        raise ValueError("score_bucket: rows must not be empty")

    # Hard unit assertion: fail-closed on mismatch
    for row in rows:
        mu = row.get("members_unit")
        if mu != city.settlement_unit:
            raise AssertionError(
                f"UNIT MISMATCH: row members_unit={mu!r} != city.settlement_unit="
                f"{city.settlement_unit!r}. Refuse scoring to avoid grid misalignment."
            )

    grid = grid_for_city(city)
    grid_bins = grid.as_bins()
    settlement_semantics = SettlementSemantics.for_city(city)

    target_dates = [r["target_date"] for r in rows]
    fold_assignments = date_blocked_folds(target_dates, k=k_folds)

    # Build candidate biases using OOS (per-fold) residuals.
    # For each fold: fit candidates on train rows; score on test rows.
    # Aggregate per-date scores across all folds.
    #
    # Strategy: collect train residuals per fold, build candidates, then score test.
    # Since candidates are fit per-fold and scored on the complementary fold,
    # this is strictly OOS — no leakage.

    # Collect per-date scores for each candidate name across all folds.
    # {candidate_name: {date: {metric: float}}}
    all_oos_scores: dict[str, dict[str, dict[str, float]]] = {}
    candidate_products_map: dict[str, str] = {}

    for fold in range(k_folds):
        train_indices = [i for i, f in enumerate(fold_assignments) if f != fold]
        test_indices = [i for i, f in enumerate(fold_assignments) if f == fold]
        if not test_indices or not train_indices:
            continue

        # Build residuals from train fold: residual = member_mean - settlement (proxy for bias)
        opendata_residuals: list[float] = []
        for i in train_indices:
            row = rows[i]
            member_maxes = np.array(json.loads(row["members_json"]), dtype=float)
            settlement_native = float(row["settlement_value_c"])
            # bias = mean_forecast - actual (sign convention: negative = cold)
            opendata_residuals.append(float(member_maxes.mean()) - settlement_native)

        # tigge_residuals: use all residuals as a prior (same product in this context)
        # In real usage tigge_residuals come from a separate TIGGE evidence source;
        # here we approximate with the same opendata residuals as a fallback.
        tigge_residuals = opendata_residuals  # offline script; no separate TIGGE source

        candidates = build_candidate_biases(
            target_product=target_product,
            opendata_residuals=opendata_residuals,
            tigge_residuals=tigge_residuals,
        )

        for cname, cand in candidates.items():
            # Record product tag
            if cname not in candidate_products_map:
                candidate_products_map[cname] = cand.evidence_product

            # Score this candidate on the test fold
            test_rows_fold = [rows[i] for i in test_indices]
            # fold_assignments for test_rows_fold: all assigned to `fold`
            fold_asn_test = [fold] * len(test_rows_fold)

            per_date_scores = _score_candidate_oos(
                test_rows_fold,
                fold_asn_test,
                k_folds=1,  # treat entire test_rows_fold as fold 0
                bias=cand.bias,
                city=city,
                settlement_semantics=settlement_semantics,
                grid_bins=grid_bins,
                grid=grid,
            )

            if cname not in all_oos_scores:
                all_oos_scores[cname] = {}
            all_oos_scores[cname].update(per_date_scores)

    # Aggregate per-date scores into mean proper scores per candidate
    candidate_metrics: dict[str, dict[str, float]] = {}
    for cname, date_scores in all_oos_scores.items():
        if not date_scores:
            continue
        candidate_metrics[cname] = {
            m: float(np.mean([ds[m] for ds in date_scores.values()]))
            for m in PROPER_SCORES
        }

    raw_metrics = candidate_metrics.get("raw", {})

    # Compute per-date improvement series for each non-raw candidate
    # improvement[date] = score_raw[date] - score_cand[date] (positive = cand beats raw)
    raw_date_scores = all_oos_scores.get("raw", {})
    improvement_lcb: dict[str, float] = {}
    catastrophic: dict[str, bool] = {}

    all_pvalues: dict[str, float] = {}
    for cname in list(candidate_metrics.keys()):
        if cname == "raw":
            continue
        cand_date_scores = all_oos_scores.get(cname, {})
        common_dates = sorted(raw_date_scores.keys() & cand_date_scores.keys())
        if len(common_dates) < 2:
            improvement_lcb[cname] = float("-inf")
            all_pvalues[cname] = 1.0
            catastrophic[cname] = False
            continue

        # improvement per date (mean across metrics to get a single series)
        improvements_per_date = [
            float(np.mean([
                raw_date_scores[d][m] - cand_date_scores[d][m]
                for m in PROPER_SCORES
            ]))
            for d in common_dates
        ]

        lcb, p_val = moving_block_bootstrap_lcb(improvements_per_date)
        improvement_lcb[cname] = lcb
        all_pvalues[cname] = p_val

        # Catastrophic: any date where candidate is MUCH worse than raw on logloss
        # (> 2x raw logloss on any date = catastrophic regression)
        catas = False
        for d in common_dates:
            raw_ll = raw_date_scores[d].get("logloss", 0.0)
            cand_ll = cand_date_scores[d].get("logloss", 0.0)
            # raw_ll > 0 guard: when raw is near-perfect (logloss~0) skip the 2x test, else any
            # positive cand_ll would falsely trip catastrophe (cand_ll > 2*0). raw_ll>>0 on real grids.
            if raw_ll > 0 and cand_ll > 2.0 * raw_ll:
                catas = True
                break
        catastrophic[cname] = catas

    # BH-FDR across the candidate family
    bh_accepted = bh_fdr_accept(all_pvalues, q=0.10)
    # For candidates not accepted by BH, set LCB to -inf (conservative)
    for cname in list(improvement_lcb.keys()):
        if cname not in bh_accepted:
            improvement_lcb[cname] = float("-inf")  # BH-rejected -> conservative -inf (min(x,-inf) was always -inf)

    return (candidate_metrics, raw_metrics, improvement_lcb, catastrophic, candidate_products_map)


def run_scoring(
    evidence_rows: list[dict],
    city,
    target_product: str,
    *,
    k_folds: int = 5,
) -> dict:
    """Run full OOS candidate selection for one bucket; return candidate_selection_manifest dict.

    Calls score_bucket → choose_candidate → packages result as a JSON-serializable manifest.
    """
    cand_metrics, raw_metrics, imp_lcb, catastrophic, cand_products = score_bucket(
        evidence_rows, city, target_product, k_folds=k_folds
    )

    decision = choose_candidate(
        candidate_metrics=cand_metrics,
        raw_metrics=raw_metrics,
        improvement_lcb=imp_lcb,
        catastrophic=catastrophic,
        target_product=target_product,
        candidate_products=cand_products,
    )

    return {
        "chosen": decision.chosen,
        "reason": decision.reason,
        "raw_is_default": decision.raw_is_default,
        "beats_raw_count": decision.beats_raw_count,
        "passing": decision.passing,
        "refused_cross_product": decision.refused_cross_product,
        "raw_metrics": raw_metrics,
        "candidate_metrics": cand_metrics,
        "improvement_lcb": imp_lcb,
        "candidate_products": cand_products,
    }
