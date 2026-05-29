#!/usr/bin/env python3
# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: HANDOFF_STAT_REFACTOR_2026-05-29 §4 #14
"""OOS Before/After Validation Harness — thin orchestration over P4 equivalence + scorer logic.

Two modes:
  EQUIVALENCE: Run analytic_p_raw_vector_from_maxes vs MC p_raw_vector_from_maxes on fixture
               rows.  Emits per-row and aggregate max p_raw / logit diff.  No new CDF logic —
               delegates entirely to P4 helpers from tests/test_analytic_p_raw_equivalence.py
               and src/signal/ensemble_signal.py.

  IMPROVEMENT: Run score_error_model_candidates.run_scoring on fixture rows via importlib
               (it is a script, not a package).  Emits candidate_selection_manifest.  On a
               small synthetic fixture, expects chosen='raw' (underpowered — that is the
               honest result).

Usage:
    python scripts/oos_validation_harness.py equivalence --fixture-rows <N>
    python scripts/oos_validation_harness.py improvement  --fixture-rows <N>

API (importable):
    run_equivalence_report(rows) -> EquivalenceReport
    run_improvement_report(rows, city, target_product) -> dict  (candidate_selection_manifest)
    make_synthetic_fixture_rows(n, city, *, rng_seed) -> list[dict]
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("ZEUS_MODE", "paper")


# ---------------------------------------------------------------------------
# Lazy imports (avoid full Zeus init at import time)
# ---------------------------------------------------------------------------

def _load_zeus():
    from src.config import City  # noqa: PLC0415
    from src.contracts.settlement_semantics import SettlementSemantics  # noqa: PLC0415
    from src.calibration.platt import logit_safe  # noqa: PLC0415
    from src.signal.ensemble_signal import (  # noqa: PLC0415
        p_raw_vector_from_maxes,
        analytic_p_raw_vector_from_maxes,
    )
    return City, SettlementSemantics, logit_safe, p_raw_vector_from_maxes, analytic_p_raw_vector_from_maxes


def _load_scorer():
    """Import score_error_model_candidates as a module via importlib (it is a script).

    The module is registered in sys.modules before exec_module so that @dataclass
    decorators at module level can resolve cls.__module__ correctly (Python 3.14+
    requirement: sys.modules[cls.__module__] must exist when the class body runs).
    """
    _SCORER_MODULE_NAME = "score_error_model_candidates"
    if _SCORER_MODULE_NAME in sys.modules:
        return sys.modules[_SCORER_MODULE_NAME]
    scorer_path = _SCRIPT_DIR / "score_error_model_candidates.py"
    spec = importlib.util.spec_from_file_location(_SCORER_MODULE_NAME, scorer_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_SCORER_MODULE_NAME] = mod  # register BEFORE exec_module
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Shared synthetic fixture
# ---------------------------------------------------------------------------

def make_synthetic_fixture_rows(
    n: int,
    city,
    *,
    rng_seed: int = 42,
) -> list[dict]:
    """Build n synthetic evidence rows suitable for both harness modes.

    Each row has:
      members_json          — JSON array of 51 floats (member daily-max values, native unit)
      members_unit          — city.settlement_unit
      settlement_value_c    — scalar float (native unit, same key as scorer uses)
      settlement_value      — same (alias for equivalence mode)
      winning_bin           — None (harness computes it live)
      target_date           — YYYY-MM-DD string, distinct per row so k_folds sees > 1 date
    """
    _, SettlementSemantics, _, _, _ = _load_zeus()
    from src.contracts.calibration_bins import grid_for_city  # noqa: PLC0415

    rng = np.random.default_rng(rng_seed)
    grid = grid_for_city(city)
    bins = grid.as_bins()
    semantics = SettlementSemantics.for_city(city)

    # Generate member ensembles around a mean value in the middle of the grid
    mid_bins = bins[len(bins) // 2]
    center = float(mid_bins.low or mid_bins.high or 40.0)

    rows = []
    for i in range(n):
        # Small stochastic per-row mean to create variation for OOS folds
        row_mean = center + rng.uniform(-3.0, 3.0)
        members = rng.normal(row_mean, 2.0, 51)
        # Settlement must be an integer value that falls in a bin.
        # Use round() to nearest integer — matches wmo_half_up at half-integers.
        settlement = float(round(float(rng.normal(row_mean, 1.5))))

        date = f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"

        rows.append({
            "members_json": json.dumps(members.tolist()),
            "members_unit": city.settlement_unit,
            "settlement_value_c": settlement,
            "settlement_value": settlement,
            "target_date": date,
            "city": city.name,
        })
    return rows


# ---------------------------------------------------------------------------
# EQUIVALENCE mode
# ---------------------------------------------------------------------------

@dataclass
class EquivalenceReport:
    """Summary of analytic vs MC p_raw comparison across fixture rows."""
    n_rows: int
    max_p_raw_abs_diff: float
    max_logit_abs_diff: float
    mean_p_raw_abs_diff: float
    mean_logit_abs_diff: float
    p_raw_atol: float
    logit_atol: float
    within_p_raw_atol: bool
    within_logit_atol: bool
    per_row: list[dict] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            "=== EQUIVALENCE REPORT (analytic vs MC p_raw) ===",
            f"  n_rows              : {self.n_rows}",
            f"  max |Δp_raw|        : {self.max_p_raw_abs_diff:.4e}  (atol={self.p_raw_atol:.1e})",
            f"  mean |Δp_raw|       : {self.mean_p_raw_abs_diff:.4e}",
            f"  max |Δlogit(p_raw)| : {self.max_logit_abs_diff:.4e}  (atol={self.logit_atol:.1e})",
            f"  mean |Δlogit(p_raw)|: {self.mean_logit_abs_diff:.4e}",
            f"  within p_raw atol   : {self.within_p_raw_atol}",
            f"  within logit atol   : {self.within_logit_atol}",
            "===================================================",
        ]
        return lines


# Tolerances mirror P4 constants (see tests/test_analytic_p_raw_equivalence.py)
P_RAW_ATOL: float = 2e-3
LOGIT_ATOL: float = 1.5e-2
N_MC_DEFAULT: int = 10_000


def run_equivalence_report(
    rows: list[dict],
    city,
    *,
    n_mc: int = N_MC_DEFAULT,
    rng_seed: int = 42,
) -> EquivalenceReport:
    """Run analytic vs MC p_raw comparison on fixture rows.

    Reuses the P4 logic: analytic_p_raw_vector_from_maxes + p_raw_vector_from_maxes +
    logit_safe from src/calibration/platt.py.  Does NOT reinvent the CDF.

    Args:
        rows: list of dicts with keys members_json, members_unit (unused here — city used),
              settlement_value (used only for fixture completeness, not in p_raw computation).
        city: Zeus City object.
        n_mc: MC sample count (default 10_000 matching P4 derivation).
        rng_seed: Pinned RNG seed for MC side reproducibility.

    Returns:
        EquivalenceReport with aggregate + per-row diffs.
    """
    City, SettlementSemantics, logit_safe, p_raw_vector_from_maxes, analytic_p_raw_vector_from_maxes = _load_zeus()
    from src.contracts.calibration_bins import grid_for_city  # noqa: PLC0415

    semantics = SettlementSemantics.for_city(city)
    grid = grid_for_city(city)
    bins = grid.as_bins()

    per_row_p_raw_diffs: list[float] = []
    per_row_logit_diffs: list[float] = []
    per_row_details: list[dict] = []

    rng = np.random.default_rng(rng_seed)

    for i, row in enumerate(rows):
        member_maxes = np.array(json.loads(row["members_json"]), dtype=float)

        # MC side (pinned rng, advancing per row for independence)
        p_mc = p_raw_vector_from_maxes(
            member_maxes,
            city,
            semantics,
            bins,
            n_mc=n_mc,
            rng=np.random.default_rng(rng_seed + i),
            extra_member_sigma=0.0,
        )

        # Analytic side
        p_analytic = analytic_p_raw_vector_from_maxes(
            member_maxes,
            city,
            semantics,
            bins,
            extra_member_sigma=0.0,
        )

        p_raw_diff = float(np.max(np.abs(p_analytic - p_mc)))
        logit_diff = float(np.max(np.abs(logit_safe(p_analytic) - logit_safe(p_mc))))

        per_row_p_raw_diffs.append(p_raw_diff)
        per_row_logit_diffs.append(logit_diff)
        per_row_details.append({
            "row_idx": i,
            "target_date": row.get("target_date"),
            "max_p_raw_abs_diff": p_raw_diff,
            "max_logit_abs_diff": logit_diff,
        })

    max_p = float(max(per_row_p_raw_diffs)) if per_row_p_raw_diffs else 0.0
    mean_p = float(np.mean(per_row_p_raw_diffs)) if per_row_p_raw_diffs else 0.0
    max_l = float(max(per_row_logit_diffs)) if per_row_logit_diffs else 0.0
    mean_l = float(np.mean(per_row_logit_diffs)) if per_row_logit_diffs else 0.0

    return EquivalenceReport(
        n_rows=len(rows),
        max_p_raw_abs_diff=max_p,
        max_logit_abs_diff=max_l,
        mean_p_raw_abs_diff=mean_p,
        mean_logit_abs_diff=mean_l,
        p_raw_atol=P_RAW_ATOL,
        logit_atol=LOGIT_ATOL,
        within_p_raw_atol=max_p <= P_RAW_ATOL,
        within_logit_atol=max_l <= LOGIT_ATOL,
        per_row=per_row_details,
    )


# ---------------------------------------------------------------------------
# IMPROVEMENT mode
# ---------------------------------------------------------------------------

def run_improvement_report(
    rows: list[dict],
    city,
    target_product: str,
    *,
    k_folds: int = 5,
) -> dict[str, Any]:
    """Run OOS candidate scoring on fixture rows.

    Loads score_error_model_candidates via importlib, calls run_scoring, returns manifest.

    On a small underpowered fixture: expect chosen='raw' (the honest result).
    """
    scorer = _load_scorer()
    manifest = scorer.run_scoring(rows, city, target_product, k_folds=k_folds)
    return manifest


def format_improvement_report(manifest: dict) -> list[str]:
    lines = [
        "=== IMPROVEMENT REPORT (candidate_selection_manifest) ===",
        f"  chosen             : {manifest.get('chosen')}",
        f"  reason             : {manifest.get('reason')}",
        f"  raw_is_default     : {manifest.get('raw_is_default')}",
        f"  passing candidates : {list((manifest.get('passing') or {}).keys())}",
    ]
    raw_m = manifest.get("raw_metrics") or {}
    if raw_m:
        lines.append(
            f"  raw metrics        : logloss={raw_m.get('logloss', 'n/a'):.4f}  "
            f"rps={raw_m.get('rps', 'n/a'):.4f}  "
            f"brier={raw_m.get('brier', 'n/a'):.4f}"
        )
    cand_m = manifest.get("candidate_metrics") or {}
    for name, m in cand_m.items():
        if name == "raw":
            continue
        lines.append(
            f"  cand '{name}': logloss={m.get('logloss', 'n/a'):.4f}  "
            f"rps={m.get('rps', 'n/a'):.4f}  "
            f"brier={m.get('brier', 'n/a'):.4f}"
        )
    lines.append("=========================================================")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_nyc_city():
    from src.config import City  # noqa: PLC0415
    return City(
        name="NYC",
        lat=40.7772, lon=-73.8726,
        timezone="America/New_York", cluster="US-Northeast",
        settlement_unit="F", wu_station="KLGA",
    )


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    eq_p = sub.add_parser("equivalence", help="Run equivalence (analytic vs MC) report")
    eq_p.add_argument("--fixture-rows", type=int, default=20,
                      help="Number of synthetic fixture rows")
    eq_p.add_argument("--n-mc", type=int, default=N_MC_DEFAULT)
    eq_p.add_argument("--rng-seed", type=int, default=42)

    imp_p = sub.add_parser("improvement", help="Run improvement (OOS scoring) report")
    imp_p.add_argument("--fixture-rows", type=int, default=30,
                       help="Number of synthetic fixture rows (need ≥ k_folds * 2)")
    imp_p.add_argument("--k-folds", type=int, default=5)
    imp_p.add_argument("--rng-seed", type=int, default=42)

    args = parser.parse_args()
    city = _build_nyc_city()

    if args.mode == "equivalence":
        rows = make_synthetic_fixture_rows(args.fixture_rows, city, rng_seed=args.rng_seed)
        report = run_equivalence_report(rows, city, n_mc=args.n_mc, rng_seed=args.rng_seed)
        for line in report.summary_lines():
            print(line)
        return 0 if (report.within_p_raw_atol and report.within_logit_atol) else 1

    elif args.mode == "improvement":
        rows = make_synthetic_fixture_rows(args.fixture_rows, city, rng_seed=args.rng_seed)
        manifest = run_improvement_report(rows, city, "HIGH", k_folds=args.k_folds)
        for line in format_improvement_report(manifest):
            print(line)
        return 0


if __name__ == "__main__":
    sys.exit(main())
