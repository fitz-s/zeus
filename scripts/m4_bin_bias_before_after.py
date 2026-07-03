#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Lifecycle: created=2026-05-28; last_reviewed=2026-05-28; last_reused=never
# Purpose: M4 bin-bias before/after analysis — proves sd3 directional improvement vs mainstream forecast.
# Reuse: Run AFTER M2 MC rebuild completes. Read-only. Requires --baseline-source operator decision.
# Authority basis: M-series #154 per docs/archive/2026-Q2/operations_historical/ENS_B_BLOCKERS_AND_M_SERIES_CONTEXT_2026-05-28.md.
"""M4 — bin-bias before/after analysis.

Compares per-bin probability mass against a mainstream forecast baseline + against
the observed settlement, under two conditions:
  PRE  : pre-sd3 rows (calibration_pairs_v2 generated under sd2/legacy gates) + plain p_raw.
  POST : sd3 rows + ft-corrected p_raw (gate_set_hash='deabf8f64bde27b7').

Reports per-city + global metrics:
  - bin_bias_mean      : mean(P(stored_bin) - P(observed_bin))   — signed; ideal 0
  - rps                : Ranked Probability Score                 — lower better
  - ece                : Expected Calibration Error               — lower better
  - improvement_vote   : per-city tally of POST < PRE on bin_bias + rps + ece

ACCEPTANCE THRESHOLD (M4 gate to authorize M5 promotion):
  - directional improvement in ≥2 of 3 metrics on ≥75% of cities
  - global mean POST-vs-PRE delta favourable in ≥2 of 3 metrics

OPERATOR DECISION PENDING:
  --baseline-source must be either 'open_meteo' or 'tigge_consensus'. Default 'open_meteo'.
  Operator may also redefine metric thresholds via --improvement-frac.

USAGE
-----
    # Read-only against staging DB after M2 MC rebuild completes
    .venv/bin/python scripts/m4_bin_bias_before_after.py \
        --staging-db /private/tmp/scratch_ens_fit.db \
        --world-db state/zeus-world.db \
        --baseline-source open_meteo \
        --out docs/operations/M4_BIN_BIAS_BEFORE_AFTER_2026-05-28.csv

  --out CSV columns: city, metric, season, n_pairs_pre, n_pairs_post,
        bin_bias_pre, bin_bias_post,
        rps_pre, rps_post,
        ece_pre, ece_post,
        directional_improvement_count (0..3)

VERDICT FILE (--verdict <path>):
  Writes a one-line summary doc:
      "M4 VERDICT: GREEN/RED  cities_improved=N/50  global_delta_bias_bias=...  global_delta_rps=...  global_delta_ece=...  authorizes_m5=true/false"

  GREEN authorizes M5 promotion per §12 D17 of context doc.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logger = logging.getLogger(__name__)


def _load_pre_pairs(forecasts_db: Path, baseline: str, metric: str):
    """Load pre-sd3 calibration_pairs_v2 (error_model_family=='none' OR gate_set_hash IS NULL).

    Returns iterator over (city, target_date, range_label, p_raw, outcome).
    """
    raise NotImplementedError(
        "M4 step 1: load pre-sd3 pairs. Pull rows where "
        "error_model_family='none' OR gate_set_hash NULL/old. Filter by metric."
    )


def _load_post_pairs(staging_db: Path, baseline: str, metric: str):
    """Load post-sd3 calibration_pairs_v2 (gate_set_hash='deabf8f64bde27b7')."""
    raise NotImplementedError(
        "M4 step 2: load post-sd3 pairs. Filter on the production gate hash."
    )


def _load_baseline_forecast(world_db: Path, baseline_source: str, city: str, target_date: str):
    """Look up the mainstream forecast probability for the city/date.

    For baseline='open_meteo': read from open-meteo daily-max forecast snapshot.
    For baseline='tigge_consensus': read from TIGGE ensemble mean forecast.

    Returns dict {bin_label: probability} for the city's bin schema.
    """
    raise NotImplementedError(
        "M4 step 3: load baseline forecast probabilities per (city, target_date). "
        "Schema varies per source."
    )


def _bin_bias(stored_bin_prob: float, observed_bin_prob: float) -> float:
    """Signed bias = P(stored) - P(observed). Ideal 0."""
    return stored_bin_prob - observed_bin_prob


def _rps(prob_vec: list[float], outcome_idx: int) -> float:
    """Ranked Probability Score; lower better."""
    cum_p = 0.0
    cum_o = 0.0
    score = 0.0
    for i, p in enumerate(prob_vec):
        cum_p += p
        cum_o += 1.0 if i == outcome_idx else 0.0
        score += (cum_p - cum_o) ** 2
    return score


def _ece(prob_vec: list[float], outcome_idx: int, n_bins: int = 10) -> float:
    """Expected Calibration Error — placeholder; full ECE requires aggregation across many pairs."""
    raise NotImplementedError("ECE accumulates across pairs; use _ece_aggregate.")


def _ece_aggregate(pairs: list[tuple[list[float], int]], n_buckets: int = 10) -> float:
    """ECE across all pairs."""
    import numpy as np  # PLC0415

    if not pairs:
        return float("nan")
    confidences = []
    correctness = []
    for prob_vec, outcome_idx in pairs:
        max_idx = int(np.argmax(prob_vec))
        confidences.append(float(prob_vec[max_idx]))
        correctness.append(1.0 if max_idx == outcome_idx else 0.0)
    confidences = np.asarray(confidences)
    correctness = np.asarray(correctness)
    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    ece = 0.0
    n = len(confidences)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (confidences >= lo) & (confidences < hi)
        if mask.sum() == 0:
            continue
        bucket_conf = confidences[mask].mean()
        bucket_acc = correctness[mask].mean()
        ece += (mask.sum() / n) * abs(bucket_conf - bucket_acc)
    return float(ece)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staging-db", required=True, type=Path,
                    help="Staging DB with post-sd3 calibration_pairs_v2 (output of M2 MC rebuild).")
    ap.add_argument("--world-db", required=True, type=Path,
                    help="Production world.db (for baseline forecast lookups + settlement_value).")
    ap.add_argument("--forecasts-db", type=Path, default=None,
                    help="Production forecasts.db for pre-sd3 pairs (default: same as staging).")
    ap.add_argument("--baseline-source", choices=("open_meteo", "tigge_consensus"),
                    default="open_meteo",
                    help="Mainstream forecast baseline. OPERATOR DECISION PENDING.")
    ap.add_argument("--metric", choices=("high", "low", "both"), default="both")
    ap.add_argument("--improvement-frac", type=float, default=0.75,
                    help="Fraction of cities that must show improvement to GREEN.")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output CSV with per-city metrics.")
    ap.add_argument("--verdict", type=Path, default=None,
                    help="Output one-line verdict file. If omitted, verdict printed to stdout only.")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(message)s")

    logger.error(
        "M4 SCAFFOLD: NOT YET IMPLEMENTED. Operator decision pending on baseline source "
        "definition + metric thresholds. This script is the post-MC entry point and contains "
        "complete metric helpers (_rps, _ece_aggregate, _bin_bias). The three NotImplementedError "
        "stubs (_load_pre_pairs, _load_post_pairs, _load_baseline_forecast) are the wiring to fill "
        "in once operator confirms baseline + acceptance gates."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
