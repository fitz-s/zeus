#!/usr/bin/env python3
# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: STAT_WAVE_REPORT_AND_PLATT_TASK_SPEC_2026-05-29.md Part 2 §2.3/§2.4 item 6/
#   §2.5 P5/§2.6. Full-chain (p_raw -> p_cal -> bin) date/decision-group-blocked OOS scorer for
#   Platt candidates. Accept rule (operator, verbatim): a candidate may enter selection ONLY when,
#   within the same p_raw domain, on date-blocked OOS, it beats identity on >=2/3 proper scores
#   {logloss, RPS, Brier} of the FULL normalized p_cal vector (multinomial, NOT per-bin
#   one-vs-rest) AND bootstrap LCB(improvement) > 0 AND no catastrophic cohort regression.
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Platt candidate OOS gate — emits a platt_oos_decision; never touches live trading.
# Reuse: Inspect that rows carry decision_group_id + members_json (native unit == city.settlement_unit)
#   and that candidates are fit on the SAME p_raw domain before relying on the decision.
"""Platt candidate full-chain OOS scorer + accept gate (P5).

This reuses the wave's gate machinery rather than reinventing it:
  - ``decision_group_blocked_folds`` (the Platt fold unit — label-correlated bins share a fold)
  - ``moving_block_bootstrap_lcb`` + ``bh_fdr_accept`` (S1/S3 statistics)
  - the SELECTION RULE shape from ``score_error_model_candidates.choose_candidate``
  - the proper-score primitive (logloss/RPS/Brier) on the FULL normalized vector
  - the production p_raw sampler ``p_raw_vector_from_maxes`` and the candidate transform
    ``platt_oos_resolver.apply_candidate`` (so the p_cal here is bit-identical to live)

The full chain per row: member_maxes -> p_raw vector (MC, native unit) -> apply candidate
Platt (A, B, C) -> normalize -> multinomial proper score vs the settled bin.

NOTHING here decides live trading. It emits a ``platt_oos_decision`` dict only. The live
reader (``platt_oos_resolver.resolve_p_cal``) applies a candidate only when a PROMOTE row
matching the p_raw_domain_hash exists.

API (importable):
  proper_scores_for_vector(p_vec, settled_idx) -> {"logloss":, "rps":, "brier":}
  score_platt_candidate_oos(rows, city, *, candidate, k_folds) -> {unit_key: {metric: float}}
  run_platt_scoring(rows, city, target_product, *, candidates, k_folds, p_raw_domain_hash) -> dict
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("ZEUS_MODE", "paper")

from src.calibration.oos_gate import (  # noqa: E402
    bh_fdr_accept,
    date_blocked_folds,
    decision_group_blocked_folds,
    moving_block_bootstrap_lcb,
)
from src.calibration.platt_oos_resolver import (  # noqa: E402
    PlattCandidate,
    apply_candidate,
    clamp_slope,
    slope_fuse_ok,
)

# All three proper scores are "lower is better".
PROPER_SCORES: tuple[str, ...] = ("logloss", "rps", "brier")
MIN_PROPER_SCORE_WINS = 2  # candidate must beat identity on >=2/3 to be eligible
_LOG_EPS = 1e-9  # logloss floor applied identically to identity and every candidate

# Catastrophe: a unit (decision-group/date cohort) where the candidate's logloss is
# more than this multiple of identity's logloss is a hard veto regardless of aggregate.
_CATASTROPHE_LOGLOSS_RATIO = 2.0

_IDENTITY = PlattCandidate(name="identity", A=1.0, B=0.0, C=0.0)


# ---------------------------------------------------------------------------
# Proper scores on the FULL normalized vector (multinomial; §2.4 item 6)
# ---------------------------------------------------------------------------

def proper_scores_for_vector(p_vec: np.ndarray, settled_idx: int) -> dict[str, float]:
    """logloss, brier, rps for one normalized probability vector vs the settled bin.

    All lower-is-better. RPS is the CDF-based ranked probability score, so mass placed
    FAR from the settled bin costs more than mass ADJACENT to it (ordinal). This is the
    full-vector multinomial scoring the gate requires — NOT a per-bin one-vs-rest AUC/ECE.
    """
    p = np.asarray(p_vec, dtype=float)
    n = len(p)
    onehot = np.zeros(n, dtype=float)
    onehot[settled_idx] = 1.0

    logloss = -math.log(max(float(p[settled_idx]), _LOG_EPS))

    diff = p - onehot
    brier = float(np.dot(diff, diff))

    cum_diff = np.cumsum(p) - np.cumsum(onehot)
    rps = float(np.dot(cum_diff, cum_diff))

    return {"logloss": logloss, "brier": brier, "rps": rps}


# ---------------------------------------------------------------------------
# Full-chain candidate scoring on OOS folds
# ---------------------------------------------------------------------------

def _fold_assignments(rows: list[dict], k_folds: int) -> list[int]:
    """Assign folds by decision_group_id when present, else fall back to target_date.

    The Platt fold unit is the decision_group (label-correlated bins share a fold). Rows
    without a decision_group_id fall back to date-blocked folds (S4 parity).
    """
    if all(r.get("decision_group_id") for r in rows):
        return decision_group_blocked_folds(
            [r["decision_group_id"] for r in rows], k=k_folds
        )
    return date_blocked_folds([r["target_date"] for r in rows], k=k_folds)


def _unit_key(row: dict) -> str:
    """The cohort key a score is aggregated under (decision_group if present, else date)."""
    return str(row.get("decision_group_id") or row["target_date"])


def score_platt_candidate_oos(
    rows: list[dict],
    city,
    *,
    candidate: PlattCandidate,
    k_folds: int = 3,
) -> dict[str, dict[str, float]]:
    """Compute OOS full-chain proper scores for one Platt candidate.

    For each fold f: the candidate is the SAME fixed (A, B, C) — Platt candidates in this
    gate are pre-fit parameter sets, not per-fold-refit biases — and is SCORED only on the
    held-out fold f rows (calibration-is-OOS: fit fold != score fold). The fold partition
    is decision-group-blocked, so a candidate scored on fold f never sees a label-correlated
    sibling bin that informed its fit.

    Returns {unit_key: {"logloss":, "rps":, "brier":}} aggregated (mean) across all OOS folds.

    UNIT invariant: members_unit must equal city.settlement_unit (hard-assert, fail-closed).
    """
    from src.contracts.calibration_bins import grid_for_city
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.signal.ensemble_signal import p_raw_vector_from_maxes

    if not rows:
        raise ValueError("score_platt_candidate_oos: rows must not be empty")

    for row in rows:
        mu = row.get("members_unit")
        if mu != city.settlement_unit:
            raise AssertionError(
                f"UNIT MISMATCH: row members_unit={mu!r} != city.settlement_unit="
                f"{city.settlement_unit!r}. Refuse scoring to avoid grid misalignment."
            )

    grid = grid_for_city(city)
    grid_bins = grid.as_bins()
    bin_widths = [b.width for b in grid_bins]
    semantics = SettlementSemantics.for_city(city)

    folds = _fold_assignments(rows, k_folds)

    per_unit_scores: dict[str, list[dict[str, float]]] = {}
    for fold in range(k_folds):
        test_indices = [i for i, f in enumerate(folds) if f == fold]
        train_indices = [i for i, f in enumerate(folds) if f != fold]
        # calibration-is-OOS: require a non-empty complementary train fold so the
        # score is genuinely held-out. A fold with no complement is skipped.
        if not test_indices or not train_indices:
            continue

        for i in test_indices:
            row = rows[i]
            member_maxes = np.array(json.loads(row["members_json"]), dtype=float)
            settlement_native = float(row["settlement_value_c"])
            lead_days = float(row.get("lead_days", 0.0))

            p_raw = p_raw_vector_from_maxes(
                member_maxes, city, semantics, grid_bins,
            )
            p_cal = apply_candidate(p_raw, lead_days, candidate, bin_widths=bin_widths)

            settled_bin = grid.bin_for_value(settlement_native)
            settled_idx = grid_bins.index(settled_bin)

            scores = proper_scores_for_vector(p_cal, settled_idx)
            per_unit_scores.setdefault(_unit_key(row), []).append(scores)

    # Aggregate (mean) per unit across its OOS rows.
    out: dict[str, dict[str, float]] = {}
    for unit, score_list in per_unit_scores.items():
        out[unit] = {
            m: float(np.mean([s[m] for s in score_list])) for m in PROPER_SCORES
        }
    return out


# ---------------------------------------------------------------------------
# Shared p_raw precomputation (perf): compute once per row, reuse across all candidates
# ---------------------------------------------------------------------------

def _precompute_p_raw_cache(
    rows: list[dict],
    city,
    semantics,
    grid_bins,
    *,
    n_mc: Optional[int] = None,
) -> list[tuple[np.ndarray, int, float, str]]:
    """Precompute (p_raw, settled_idx, lead_days, unit_key) for every row.

    p_raw is seeded deterministically by member_maxes content (see ensemble_signal).
    Calling this once and sharing the result across all candidates reduces the MC cost
    from O(n_candidates × n_rows) to O(n_rows). Semantic equivalence: p_raw does not
    depend on the Platt (A, B, C) parameters; only apply_candidate does.

    n_mc: MC iterations (None = ensemble_n_mc() default). Pass
    calibration_batch_rebuild_n_mc() (1000) for offline batch scoring per LAW 4.
    """
    from src.contracts.calibration_bins import grid_for_city
    from src.signal.ensemble_signal import p_raw_vector_from_maxes

    grid = grid_for_city(city)
    cache = []
    for row in rows:
        member_maxes = np.array(json.loads(row["members_json"]), dtype=float)
        settlement_native = float(row["settlement_value_c"])
        lead_days = float(row.get("lead_days", 0.0))
        p_raw = p_raw_vector_from_maxes(member_maxes, city, semantics, grid_bins, n_mc=n_mc)
        settled_bin = grid.bin_for_value(settlement_native)
        settled_idx = grid_bins.index(settled_bin)
        cache.append((p_raw, settled_idx, lead_days, _unit_key(row)))
    return cache


def score_all_candidates_oos_shared(
    rows: list[dict],
    city,
    *,
    candidates_with_identity: list[PlattCandidate],
    k_folds: int = 3,
    n_mc: Optional[int] = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """Score identity + all candidates sharing ONE p_raw precomputation pass.

    Returns {candidate_name: {unit_key: {metric: float}}} for all candidates.
    Semantically identical to calling score_platt_candidate_oos separately per candidate
    but ~(1 + n_candidates)x faster because p_raw_vector_from_maxes runs once per row.

    n_mc: MC iterations. Pass calibration_batch_rebuild_n_mc() (1000) for offline
    batch scoring per LAW 4 (batch rebuilds rely on many-pair averaging).
    """
    from src.contracts.calibration_bins import grid_for_city
    from src.contracts.settlement_semantics import SettlementSemantics

    if not rows:
        raise ValueError("score_all_candidates_oos_shared: rows must not be empty")

    for row in rows:
        mu = row.get("members_unit")
        if mu != city.settlement_unit:
            raise AssertionError(
                f"UNIT MISMATCH: row members_unit={mu!r} != city.settlement_unit="
                f"{city.settlement_unit!r}."
            )

    grid = grid_for_city(city)
    grid_bins = grid.as_bins()
    bin_widths = [b.width for b in grid_bins]
    semantics = SettlementSemantics.for_city(city)
    folds = _fold_assignments(rows, k_folds)

    # Precompute p_raw for every row (O(n_rows) MC calls total).
    print(f"[INFO] Precomputing p_raw for {len(rows)} rows (shared across {len(candidates_with_identity)} candidates, n_mc={n_mc or 'default'})...", file=sys.stderr)
    p_raw_cache = _precompute_p_raw_cache(rows, city, semantics, grid_bins, n_mc=n_mc)
    print(f"[INFO] p_raw precompute done.", file=sys.stderr)

    # Score each candidate using the cached p_raw vectors.
    all_per_unit: dict[str, dict[str, list[dict[str, float]]]] = {
        cand.name: {} for cand in candidates_with_identity
    }

    for fold in range(k_folds):
        test_indices = [i for i, f in enumerate(folds) if f == fold]
        train_indices = [i for i, f in enumerate(folds) if f != fold]
        if not test_indices or not train_indices:
            continue

        for i in test_indices:
            p_raw, settled_idx, lead_days, unit_k = p_raw_cache[i]
            for cand in candidates_with_identity:
                p_cal = apply_candidate(p_raw, lead_days, cand, bin_widths=bin_widths)
                scores = proper_scores_for_vector(p_cal, settled_idx)
                all_per_unit[cand.name].setdefault(unit_k, []).append(scores)

    # Aggregate per unit.
    result: dict[str, dict[str, dict[str, float]]] = {}
    for cand_name, per_unit_raw in all_per_unit.items():
        result[cand_name] = {
            u: {m: float(np.mean([s[m] for s in slist])) for m in PROPER_SCORES}
            for u, slist in per_unit_raw.items()
        }
    return result


# ---------------------------------------------------------------------------
# Per-fold FIT path (calibration-is-OOS load-bearing: fit on train, score on test)
# ---------------------------------------------------------------------------

def _build_training_triples(rows, indices, city, semantics, grid, grid_bins, bin_widths):
    """Build per-bin Platt training triples (p_raw_i, lead_days, outcome_i) from rows.

    Each row → its full p_raw vector → one (p_raw_bin, lead, outcome) triple per bin,
    outcome=1 for the settled bin, 0 otherwise. This is the calibration-pair construction
    the live Platt layer fits on. The p_raw values are width-normalized to match the
    ExtendedPlattCalibrator width-normalized density input space.
    """
    from src.calibration.platt import normalize_bin_probability_for_calibration
    from src.signal.ensemble_signal import p_raw_vector_from_maxes

    p_list: list[float] = []
    lead_list: list[float] = []
    out_list: list[int] = []
    for i in indices:
        row = rows[i]
        member_maxes = np.array(json.loads(row["members_json"]), dtype=float)
        settlement_native = float(row["settlement_value_c"])
        lead_days = float(row.get("lead_days", 0.0))
        p_raw = p_raw_vector_from_maxes(member_maxes, city, semantics, grid_bins)
        settled_idx = grid_bins.index(grid.bin_for_value(settlement_native))
        for j, p in enumerate(p_raw):
            p_list.append(normalize_bin_probability_for_calibration(float(p), bin_width=bin_widths[j]))
            lead_list.append(lead_days)
            out_list.append(1 if j == settled_idx else 0)
    return np.array(p_list), np.array(lead_list), np.array(out_list)


def fit_clamped_platt(p_raw, lead_days, outcomes, *, slope_cap):
    """Fit an ExtendedPlattCalibrator on training triples; return a clamped (A,B,C).

    slope_cap=None means no clamp (unclamped fit). The clamp is the fuse cap applied to
    the FITTED slope. Returns a PlattCandidate. Falls back to identity if the fit is
    degenerate (single outcome class) or too small.
    """
    from src.calibration.platt import ExtendedPlattCalibrator

    name = "fit_unclamped" if slope_cap is None else f"fit_A_clamped_{_cap_tag_local(slope_cap)}"
    try:
        cal = ExtendedPlattCalibrator()
        # bin_widths=None here: triples are already width-normalized in _build_training_triples,
        # so the calibrator must NOT re-normalize (raw_probability input space on already-density p).
        cal.fit(np.asarray(p_raw, float), np.asarray(lead_days, float), np.asarray(outcomes, int))
        A = clamp_slope(cal.A, cap=slope_cap) if slope_cap is not None else cal.A
        return PlattCandidate(name=name, A=float(A), B=float(cal.B), C=float(cal.C))
    except (ValueError, RuntimeError):
        return PlattCandidate(name=name + "_identity_fallback", A=1.0, B=0.0, C=0.0)


def _cap_tag_local(cap: float) -> str:
    return f"{cap:.1f}".replace(".", "p")


def score_fitted_candidate_oos(rows, city, *, slope_cap, k_folds=3):
    """Fit a Platt candidate PER FOLD on the train fold, score on the held-out test fold.

    This is the calibration-is-OOS load-bearing path: the slope is LEARNED from train-fold
    labels and applied to test-fold rows. Under an in-sample leak (train fold = all rows),
    the fit sees the test fold's labels and an overfit (over-steep) slope is rewarded →
    the OOS penalty disappears. The legitimate OOS path penalises overfit slopes.

    Returns {unit_key: {metric: float}} aggregated across all OOS folds.
    """
    from src.contracts.calibration_bins import grid_for_city
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.signal.ensemble_signal import p_raw_vector_from_maxes

    for row in rows:
        if row.get("members_unit") != city.settlement_unit:
            raise AssertionError(
                f"UNIT MISMATCH: members_unit={row.get('members_unit')!r} != "
                f"city.settlement_unit={city.settlement_unit!r}"
            )

    grid = grid_for_city(city)
    grid_bins = grid.as_bins()
    bin_widths = [b.width for b in grid_bins]
    semantics = SettlementSemantics.for_city(city)
    folds = _fold_assignments(rows, k_folds)

    per_unit_scores: dict[str, list[dict[str, float]]] = {}
    for fold in range(k_folds):
        test_indices = [i for i, f in enumerate(folds) if f == fold]
        train_indices = [i for i, f in enumerate(folds) if f != fold]
        if not test_indices or not train_indices:
            continue

        # FIT on train fold only (the calibration-is-OOS boundary).
        p_tr, lead_tr, out_tr = _build_training_triples(
            rows, train_indices, city, semantics, grid, grid_bins, bin_widths
        )
        fitted = fit_clamped_platt(p_tr, lead_tr, out_tr, slope_cap=slope_cap)

        # SCORE on held-out test fold.
        for i in test_indices:
            row = rows[i]
            member_maxes = np.array(json.loads(row["members_json"]), dtype=float)
            settlement_native = float(row["settlement_value_c"])
            lead_days = float(row.get("lead_days", 0.0))
            p_raw = p_raw_vector_from_maxes(member_maxes, city, semantics, grid_bins)
            p_cal = apply_candidate(p_raw, lead_days, fitted, bin_widths=bin_widths)
            settled_idx = grid_bins.index(grid.bin_for_value(settlement_native))
            per_unit_scores.setdefault(_unit_key(row), []).append(
                proper_scores_for_vector(p_cal, settled_idx)
            )

    out: dict[str, dict[str, float]] = {}
    for unit, score_list in per_unit_scores.items():
        out[unit] = {m: float(np.mean([s[m] for s in score_list])) for m in PROPER_SCORES}
    return out


# ---------------------------------------------------------------------------
# Accept gate
# ---------------------------------------------------------------------------

def _mean_metrics(per_unit: dict[str, dict[str, float]]) -> dict[str, float]:
    if not per_unit:
        return {}
    return {
        m: float(np.mean([s[m] for s in per_unit.values()])) for m in PROPER_SCORES
    }


def _beats_count(cand: dict[str, float], identity: dict[str, float]) -> int:
    n = 0
    for m in PROPER_SCORES:
        c, r = cand.get(m), identity.get(m)
        if c is not None and r is not None and math.isfinite(c) and math.isfinite(r) and c < r:
            n += 1
    return n


def run_platt_scoring(
    rows: list[dict],
    city,
    target_product: str,  # noqa: ARG001  (recorded on the decision for provenance)
    *,
    candidates: list[PlattCandidate],
    k_folds: int = 3,
    p_raw_domain_hash: str = "UNFROZEN",
    override_fuse: bool = False,
    n_mc: Optional[int] = None,
) -> dict:
    """Run the full-chain OOS gate over a candidate set; return a platt_oos_decision dict.

    Default is identity: a candidate is promoted ONLY if it beats identity on >=2/3 proper
    scores AND bootstrap LCB(improvement) > 0 AND BH-FDR pass AND no catastrophic cohort
    regression AND it clears the slope fuse (|A| <= A_REJECT_HARD unless override). Among
    passing candidates, the one with the largest improvement LCB wins. If none pass, the
    decision is IDENTITY (or INSUFFICIENT_N when there is too little OOS evidence).

    decision in {PROMOTE, IDENTITY, INSUFFICIENT_N, REJECT}.
    """
    # Split candidates: fuse-rejected go to fused_out immediately; the rest score together
    # with identity in a single shared p_raw pass (4x faster than per-candidate calls).
    fused_out: list[str] = []
    candidates_to_score: list[PlattCandidate] = [_IDENTITY]
    for cand in candidates:
        if cand.name == "identity":
            continue
        if not slope_fuse_ok(cand.A, override=override_fuse):
            fused_out.append(cand.name)
        else:
            candidates_to_score.append(cand)

    # Score all eligible candidates (including identity) in one shared p_raw pass.
    all_per_unit = score_all_candidates_oos_shared(
        rows, city, candidates_with_identity=candidates_to_score, k_folds=k_folds, n_mc=n_mc
    )

    identity_per_unit = all_per_unit.get("identity", {})
    identity_metrics = _mean_metrics(identity_per_unit)

    candidate_metrics: dict[str, dict[str, float]] = {"identity": identity_metrics}
    improvement_lcb: dict[str, float] = {}
    catastrophic: dict[str, bool] = {}
    pvalues: dict[str, float] = {}
    beats: dict[str, int] = {}

    for cand in candidates_to_score:
        if cand.name == "identity":
            continue

        cand_per_unit = all_per_unit.get(cand.name, {})
        candidate_metrics[cand.name] = _mean_metrics(cand_per_unit)
        beats[cand.name] = _beats_count(candidate_metrics[cand.name], identity_metrics)

        common = sorted(identity_per_unit.keys() & cand_per_unit.keys())
        if len(common) < 2:
            improvement_lcb[cand.name] = float("-inf")
            pvalues[cand.name] = 1.0
            catastrophic[cand.name] = False
            continue

        improvements = [
            float(np.mean([
                identity_per_unit[u][m] - cand_per_unit[u][m] for m in PROPER_SCORES
            ]))
            for u in common
        ]
        lcb, p_val = moving_block_bootstrap_lcb(improvements)
        improvement_lcb[cand.name] = lcb
        pvalues[cand.name] = p_val

        catas = False
        for u in common:
            id_ll = identity_per_unit[u]["logloss"]
            cd_ll = cand_per_unit[u]["logloss"]
            if id_ll > 0 and cd_ll > _CATASTROPHE_LOGLOSS_RATIO * id_ll:
                catas = True
                break
        catastrophic[cand.name] = catas

    # BH-FDR across the candidate family; BH-rejected -> conservative -inf LCB.
    bh_accepted = bh_fdr_accept(pvalues, q=0.10) if pvalues else set()
    for name in list(improvement_lcb.keys()):
        if name not in bh_accepted:
            improvement_lcb[name] = float("-inf")

    passing = [
        name
        for name in improvement_lcb
        if beats.get(name, 0) >= MIN_PROPER_SCORE_WINS
        and math.isfinite(improvement_lcb[name])
        and improvement_lcb[name] > 0
        and not catastrophic.get(name, False)
    ]

    if not identity_metrics:
        decision, chosen, reason = "INSUFFICIENT_N", None, "no OOS evidence (empty identity scores)"
    elif not passing:
        decision, chosen, reason = (
            "IDENTITY",
            "identity",
            "no candidate cleared the full-chain OOS gate (>=2/3 proper-score wins + LCB>0 "
            "+ BH-FDR + no catastrophe + slope fuse); identity is the default",
        )
    else:
        chosen = max(passing, key=lambda n: improvement_lcb[n])
        decision, reason = (
            "PROMOTE",
            f"{chosen} beats identity on {beats[chosen]}/3 proper scores, "
            f"LCB(improvement)={improvement_lcb[chosen]:+.4f}>0, BH-FDR pass, no catastrophe, "
            f"slope fuse ok; selected over {len(passing)} passing candidate(s) by max LCB",
        )

    return {
        "decision": decision,
        "chosen": chosen,
        "reason": reason,
        "p_raw_domain_hash": p_raw_domain_hash,
        "target_product": target_product,
        "identity_metrics": identity_metrics,
        "candidate_metrics": candidate_metrics,
        "improvement_lcb": improvement_lcb,
        "beats_identity_count": beats,
        "catastrophic": catastrophic,
        "fused_out": fused_out,
        "passing": sorted(passing),
    }
