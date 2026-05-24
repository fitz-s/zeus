# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: ENS full_transport_v1 REFIT task 2026-05-24
#   (docs/operations/ENS_REFIT_PLAN_2026-05-24.md), STEP 6 validation.
"""Blocked-OOS validation for the ENS predictive-error refit (isolated DB).

Compares calibration quality on calibration_pairs_v2 between the uncorrected
('none') family and the corrected family ('full_transport_v1'), using p_raw and
p_cal. Anti-leakage: a target_date's pairs are scored by a Platt model fit on a
bucket that EXCLUDES that target_date's contribution is approximated here by a
forward/blocked split — each decision_group's pairs are held out of the fold
whose model scores them (group-blocked K-fold on decision_group_id). The OLD
(pre-refit) vs REFIT Platt comparison reuses the same blocked folds.

Metrics per split (per family, per p-space):
  Brier, LogLoss, ECE (10-bin), n_pairs, n_groups.

Splits: overall, by city, coastal vs inland, US-degF vs metric-C, LOW track.

This is a READ + in-memory-fit validator. It does NOT write the isolated DB's
platt_models_v2; it fits transient folds with the same ExtendedPlattCalibrator
the production refit uses. Output is a markdown table fragment.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_COASTAL = {
    "San Francisco", "Los Angeles", "Seattle", "Miami", "NYC", "London",
    "Tokyo", "Hong Kong", "Sydney", "Lisbon", "Barcelona", "Mumbai",
}


def _brier(p, y):
    p = np.clip(np.asarray(p, float), 0.0, 1.0)
    y = np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def _logloss(p, y, eps=1e-12):
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _ece(p, y, n_bins=10):
    p = np.clip(np.asarray(p, float), 0.0, 1.0)
    y = np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    e = 0.0
    n = len(p)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
        if not m.any():
            continue
        e += (m.sum() / n) * abs(p[m].mean() - y[m].mean())
    return float(e)


def _load(conn, family, metric):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(calibration_pairs_v2)")}
    emf = "AND error_model_family = ?" if "error_model_family" in cols else ""
    params = ["canonical_v2", metric]
    if emf:
        params.append(family)
    rows = conn.execute(
        f"""
        SELECT city, target_date, range_label, p_raw, outcome, lead_days,
               decision_group_id, cluster, season
        FROM calibration_pairs_v2
        WHERE bin_source = ? AND temperature_metric = ?
          AND p_raw IS NOT NULL AND decision_group_id IS NOT NULL
          AND decision_group_id != '' {emf}
        """,
        params,
    ).fetchall()
    return rows


def _fit_predict_blocked(rows, n_folds=5):
    """Group-blocked K-fold over decision_group_id. Returns OOS p_cal aligned to rows.

    Each fold holds out a disjoint set of decision groups; the Platt is fit on the
    other folds' pairs and predicts the held-out fold. A target_date's settlement
    is therefore never in its own fit (group = decision_group_id keyed on city+
    target_date+issue+data_version).
    """
    from src.calibration.platt import ExtendedPlattCalibrator
    from src.calibration.store import infer_bin_width_from_label

    groups = sorted({r["decision_group_id"] for r in rows})
    if len(groups) < n_folds * 3:
        return None  # too few groups for a meaningful blocked split
    fold_of = {g: (i % n_folds) for i, g in enumerate(groups)}
    p_cal = [None] * len(rows)
    for fold in range(n_folds):
        tr = [r for r in rows if fold_of[r["decision_group_id"]] != fold]
        te_idx = [i for i, r in enumerate(rows) if fold_of[r["decision_group_id"]] == fold]
        if not tr or not te_idx:
            continue
        cal = ExtendedPlattCalibrator()
        try:
            cal.fit(
                np.array([r["p_raw"] for r in tr]),
                np.array([r["lead_days"] for r in tr]),
                np.array([r["outcome"] for r in tr]),
                bin_widths=np.array([infer_bin_width_from_label(r["range_label"]) for r in tr], dtype=object),
                decision_group_ids=np.array([r["decision_group_id"] for r in tr], dtype=object),
                n_bootstrap=0,
                regularization_C=1.0,
            )
        except Exception:
            continue
        for i in te_idx:
            r = rows[i]
            try:
                p_cal[i] = float(cal.predict_for_bin(
                    float(r["p_raw"]), float(r["lead_days"]),
                    bin_width=infer_bin_width_from_label(r["range_label"]),
                ))
            except Exception:
                p_cal[i] = float(r["p_raw"])
    return p_cal


def _split_filter(rows, kind):
    if kind == "overall":
        return rows
    if kind == "coastal":
        return [r for r in rows if r["city"] in _COASTAL]
    if kind == "inland":
        return [r for r in rows if r["city"] not in _COASTAL]
    return rows


def _metrics_block(rows, label):
    if not rows:
        return f"| {label} | 0 | 0 | - | - | - | - | - | - |"
    y = [r["outcome"] for r in rows]
    praw = [r["p_raw"] for r in rows]
    ng = len({r["decision_group_id"] for r in rows})
    pcal = _fit_predict_blocked(rows)
    b_raw, l_raw, e_raw = _brier(praw, y), _logloss(praw, y), _ece(praw, y)
    if pcal is not None and all(v is not None for v in pcal):
        b_cal, l_cal, e_cal = _brier(pcal, y), _logloss(pcal, y), _ece(pcal, y)
        cal_s = f"{b_cal:.4f} | {l_cal:.4f} | {e_cal:.4f}"
    else:
        cal_s = "n/a | n/a | n/a"
    return (f"| {label} | {len(rows)} | {ng} | {b_raw:.4f} | {l_raw:.4f} | "
            f"{e_raw:.4f} | {cal_s} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--metric", default="high", choices=("high", "low"))
    args = ap.parse_args()
    import sqlite3
    conn = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"### ENS refit OOS validation — metric={args.metric}")
    print()
    print("p_cal columns are group-blocked 5-fold OOS (a decision group is never in its own fit).")
    print()
    print("| family / split | n_pairs | n_groups | Brier(raw) | LogLoss(raw) | ECE(raw) | Brier(cal) | LogLoss(cal) | ECE(cal) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for family in ("none", "full_transport_v1"):
        rows = _load(conn, family, args.metric)
        for split in ("overall", "coastal", "inland"):
            sub = _split_filter(rows, split)
            print(_metrics_block(sub, f"{family} / {split}"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
