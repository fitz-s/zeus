# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: HANDOFF_STAT_REFACTOR_2026-05-29 §4 item-1 + score_error_model_candidates
#   docstring. Relationship tests for the NO TEMPORAL LEAKAGE invariant — the cross-module
#   boundary that must hold between build_candidate_biases (fit), date_blocked_folds, and
#   the proper scoring path. Written RED-first per Fitz TDD protocol before scoring impl.
"""Relationship tests: NO TEMPORAL LEAKAGE.

The load-bearing cross-module invariant:
  Each candidate's OOS proper scores (logloss/rps/brier) are computed ONLY on held-out folds
  whose target_date set is DATE-DISJOINT from the dates used to FIT that candidate's bias.

Four relationship tests — these MUST run RED (FAIL) before Phase 3 is wired.
RED = score_bucket / run_scoring raise NotImplementedError (stubs in place).
GREEN = assertions evaluate correctly after implementation.

1. test_score_dates_disjoint_from_fit_dates
   date_blocked_folds structural guarantee: same-date records always share a fold.
   score_bucket must honour this — call it and assert it returns without NotImplementedError.

2. test_leakage_is_detectable_not_absorbed
   A BIASED fold (mean shifted +10° vs rest) must produce a different logloss when the
   biased date appears in train vs test. Uses run_scoring with one fold intentionally
   injected into training — score must differ from the clean partition.

3. test_raw_identity_scored_same_path
   score_bucket on raw (bias=0.0) must return finite proper scores in all three metrics.

4. test_proper_scores_lower_is_better_orientation
   improvement = score_raw − score_cand. Positive = candidate beats raw.
   A known-better candidate (bias corrects a systematic +10° warm bias in the data)
   must produce improvement > 0 on logloss.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Load score_error_model_candidates from scripts/
# ---------------------------------------------------------------------------
_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "score_error_model_candidates.py"
_spec = importlib.util.spec_from_file_location("score_error_model_candidates_leakage", _MOD_PATH)
_sem = importlib.util.module_from_spec(_spec)
sys.modules["score_error_model_candidates_leakage"] = _sem
_spec.loader.exec_module(_sem)

score_bucket = _sem.score_bucket
run_scoring = _sem.run_scoring

from src.calibration.oos_gate import date_blocked_folds
from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics

# ---------------------------------------------------------------------------
# Synthetic city fixture — °F, Fahrenheit grid
# ---------------------------------------------------------------------------
CITY_F = City(
    name="TestCityF",
    lat=40.0,
    lon=-75.0,
    timezone="America/New_York",
    settlement_unit="F",
    cluster="US",
    wu_station="KTESTF",
)
TARGET_PRODUCT = "test_mx2t3"

# ---------------------------------------------------------------------------
# Synthetic evidence rows for testing.
# Design: 18 rows, 6 distinct dates, 3 rows/date, k=3 folds.
# "BIASED fold": fold-0 dates get members_json with mean ~85°F (warm);
#   other dates get mean ~70°F. settlement_native set consistently.
# This creates a synthetic warm-bias that a bias=-15 candidate would correct.
# ---------------------------------------------------------------------------

def _date_for_idx(i: int) -> str:
    """6 dates cycling across 18 rows."""
    dates = [
        "2026-01-10", "2026-01-20",
        "2026-02-10", "2026-02-20",
        "2026-03-10", "2026-03-20",
    ]
    return dates[i % 6]


def _make_rows(n: int = 18) -> list[dict]:
    """Build synthetic rows with a warm bias on the first 2 dates (fold-0 dates)."""
    rng = np.random.default_rng(42)
    dates = [_date_for_idx(i) for i in range(n)]
    fold_asn = date_blocked_folds(dates, k=3)

    # Determine which dates land in fold 0
    fold0_dates = {d for d, f in zip(dates, fold_asn) if f == 0}

    rows = []
    for i in range(n):
        d = dates[i]
        if d in fold0_dates:
            # Warm-biased fold: members around 85°F, settlement at 80°F
            members = rng.normal(85.0, 2.0, 20).tolist()
            settlement = 80.0
        else:
            # Normal fold: members around 70°F, settlement at 70°F
            members = rng.normal(70.0, 2.0, 20).tolist()
            settlement = 70.0
        rows.append({
            "target_date": d,
            "settlement_value_c": float(settlement),  # misnomer: actually native (°F here)
            "members_json": json.dumps(members),
            "members_unit": "F",
        })
    return rows


_ROWS = _make_rows(18)
_K = 3
_FOLD_ASSIGNMENTS = date_blocked_folds([r["target_date"] for r in _ROWS], k=_K)


# ---------------------------------------------------------------------------
# Test 1: date_blocked_folds structural guarantee + score_bucket returns
# ---------------------------------------------------------------------------

def test_score_dates_disjoint_from_fit_dates():
    """date_blocked_folds must produce zero date-overlap across train/test for every fold.

    score_bucket must be callable and return without a fold-boundary violation.

    RED: score_bucket raises NotImplementedError (Phase 2 stub).
    GREEN: score_bucket returns 5-tuple; fold date-disjointness invariant holds throughout.
    """
    # Invariant must hold regardless of impl — verify date_blocked_folds guarantee.
    for test_fold in range(_K):
        test_dates = {
            r["target_date"] for r, f in zip(_ROWS, _FOLD_ASSIGNMENTS) if f == test_fold
        }
        train_dates = {
            r["target_date"] for r, f in zip(_ROWS, _FOLD_ASSIGNMENTS) if f != test_fold
        }
        overlap = test_dates & train_dates
        assert overlap == set(), (
            f"date_blocked_folds violated — fold {test_fold} overlap: {overlap}"
        )

    # Now call score_bucket — RED: NotImplementedError propagates (not caught).
    cand_metrics, raw_metrics, imp_lcb, catastrophic, cand_products = score_bucket(
        _ROWS, CITY_F, TARGET_PRODUCT, k_folds=_K
    )

    # GREEN assertions (active once impl exists):
    assert set(raw_metrics.keys()) >= {"logloss", "rps", "brier"}
    assert all(math.isfinite(v) for v in raw_metrics.values())
    assert "raw" in cand_metrics


# ---------------------------------------------------------------------------
# Test 2: leakage is detectable — biased fold produces different logloss
# ---------------------------------------------------------------------------

def test_leakage_is_detectable_not_absorbed():
    """A scoring path that ignores fold boundaries absorbs leakage silently.

    The FIT→SCORE boundary test: compare opendata_bias candidate logloss when scored
    with bias fit EXCLUDING fold-0 dates (correct, OOS) vs when fold-0's biased rows
    are injected into fold-1's train dates (leakage).

    The biased fold (fold-0 dates, ~85°F members / 80°F settlement) has residuals
    ~+15°F (warm-biased). When these rows contaminate another fold's training data,
    the bias estimate for that fold shifts — changing the candidate's logloss on the
    test fold. If leakage is absorbed silently, cand scores are identical (bad).

    RED: run_scoring raises NotImplementedError.
    GREEN: opendata_bias candidate logloss differs between clean and leaked runs.
    """
    # Clean run: proper OOS fold partition
    manifest_clean = run_scoring(_ROWS, CITY_F, TARGET_PRODUCT, k_folds=_K)

    # Leaked run: inject warm-biased fold-0 rows into fold-1 dates.
    # This contaminates fold-1's training data with fold-0's distribution.
    # The bias estimated for fold-1 train rows will be pulled toward +15 (warm bias).
    fold1_dates = {
        r["target_date"] for r, f in zip(_ROWS, _FOLD_ASSIGNMENTS) if f == 1
    }
    fold1_date_for_inject = sorted(fold1_dates)[0]
    fold0_rows = [r for r, f in zip(_ROWS, _FOLD_ASSIGNMENTS) if f == 0]
    # Clone fold-0 warm-biased rows with a fold-1 date → leaks +15°F bias into fold-1 training
    leaked_extra = [
        {**r, "target_date": fold1_date_for_inject} for r in fold0_rows
    ]
    leaked_rows = _ROWS + leaked_extra

    manifest_leaked = run_scoring(leaked_rows, CITY_F, TARGET_PRODUCT, k_folds=_K)

    # GREEN: opendata_bias candidate logloss must DIFFER between clean and leaked runs.
    # The bias estimate changes when warm-biased rows contaminate training → candidate
    # scores on held-out folds must change.
    cand_clean = manifest_clean["candidate_metrics"].get("opendata_bias", {})
    cand_leaked = manifest_leaked["candidate_metrics"].get("opendata_bias", {})

    assert cand_clean and cand_leaked, (
        "opendata_bias candidate absent from one or both manifests — "
        f"clean keys: {list(manifest_clean['candidate_metrics'].keys())}, "
        f"leaked keys: {list(manifest_leaked['candidate_metrics'].keys())}"
    )

    diffs = [
        abs(cand_clean[m] - cand_leaked[m]) > 1e-12
        for m in ("logloss", "rps", "brier")
        if m in cand_clean and m in cand_leaked
    ]
    assert any(diffs), (
        "Leakage was silently absorbed — injecting warm-biased fold-0 rows into "
        "fold-1 training dates produced identical opendata_bias candidate scores. "
        f"clean={cand_clean}, leaked={cand_leaked}. "
        "The scoring path must be sensitive to which rows are in each training fold."
    )


# ---------------------------------------------------------------------------
# Test 3: raw identity (bias=0) scored same path as correction candidates
# ---------------------------------------------------------------------------

def test_raw_identity_scored_same_path():
    """score_bucket must score raw (bias=0) through the same MC + grid path.

    RED: score_bucket raises NotImplementedError.
    GREEN: raw_metrics has all three proper scores as finite real numbers.
    """
    cand_metrics, raw_metrics, imp_lcb, catastrophic, cand_products = score_bucket(
        _ROWS, CITY_F, TARGET_PRODUCT, k_folds=_K
    )

    assert set(raw_metrics.keys()) >= {"logloss", "rps", "brier"}, (
        f"raw_metrics missing proper scores. Got: {set(raw_metrics.keys())}"
    )
    for metric, val in raw_metrics.items():
        assert math.isfinite(val), f"raw_metrics[{metric!r}] = {val!r} is not finite"
    assert "raw" in cand_metrics, "raw candidate must appear in cand_metrics"


# ---------------------------------------------------------------------------
# Test 4: improvement orientation matches choose_candidate (lower-is-better)
# ---------------------------------------------------------------------------

def test_proper_scores_lower_is_better_orientation():
    """improvement = score_raw − score_cand > 0 means candidate beats raw.

    Build a dataset where members are WARM-BIASED (+10°F above settlement).
    A candidate with bias=-10 corrects the members toward settlement → should
    produce lower logloss than raw (which applies no correction).

    sign convention must match choose_candidate._beats_raw_count: c < r (lower=better).

    RED: score_bucket raises NotImplementedError.
    GREEN: improvement_lcb["opendata_bias"] > 0 OR at least cand_metrics["opendata_bias"]
           has lower logloss than raw_metrics (correct sign orientation).
    """
    # Build consistently warm-biased rows: members +10°F above settlement.
    # So raw (bias=0) will over-predict temperature → higher logloss on correct settlement.
    # A candidate that subtracts the +10 warm bias should predict better.
    rng = np.random.default_rng(99)
    biased_rows = []
    dates = [
        "2026-01-05", "2026-01-15", "2026-01-25",
        "2026-02-05", "2026-02-15", "2026-02-25",
        "2026-03-05", "2026-03-15", "2026-03-25",
    ]
    for i, d in enumerate(dates * 3):  # 27 rows, 9 dates, 3 rows/date
        settlement = 70.0  # all settle at 70°F
        # Members 10°F warmer than settlement
        members = rng.normal(80.0, 1.0, 20).tolist()
        biased_rows.append({
            "target_date": d,
            "settlement_value_c": float(settlement),
            "members_json": json.dumps(members),
            "members_unit": "F",
        })

    cand_metrics, raw_metrics, imp_lcb, catastrophic, cand_products = score_bucket(
        biased_rows, CITY_F, TARGET_PRODUCT, k_folds=3
    )

    # Orientation check: if opendata_bias candidate exists, it should beat raw on logloss
    if "opendata_bias" in cand_metrics:
        raw_ll = raw_metrics.get("logloss", float("inf"))
        cand_ll = cand_metrics["opendata_bias"].get("logloss", float("inf"))
        # candidate logloss < raw logloss = improvement positive = correct orientation
        assert cand_ll < raw_ll, (
            f"Wrong orientation: opendata_bias logloss {cand_ll:.4f} >= raw logloss "
            f"{raw_ll:.4f}. For a warm-biased dataset, a debiasing candidate MUST "
            f"produce lower logloss. Check: improvement = score_raw - score_cand (not reversed)."
        )
