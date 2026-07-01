# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §4 (audit battery)
# Purpose: Read-only evaluation harness for post-refit proper-score audit.
#   Computes Brier, LogLoss, RPS, P(actual), PIT, ECE on group-blocked OOS held-out
#   set, for model variants raw/full_transport_v1 (§4.1) and p_cal (§4.2),
#   across cohort splits: global, city-cluster, coastal/inland, US-°F vs °C,
#   HIGH vs LOW, lead bucket, cycle.
#
# CONSTRAINTS: Read-only on DB. No writes. No edits to model/validator code.
# USAGE:
#   python scripts/audit_refit_proper_scores.py --db /tmp/ens_refit/subset.db --metric high
#   python scripts/audit_refit_proper_scores.py --db /tmp/ens_refit/full.db --metric high --smoke
#
# --smoke: reconcile Brier/LogLoss/ECE against validate_ens_refit_oos.py on same data.
# Output: markdown tables written to docs/archive/2026-Q2/operations_historical/ENS_REFIT_VALIDATION_2026-05-25_results.md
#         PIT histograms (ASCII) inlined in the same file.
"""Post-refit proper-score audit harness — §4.1 p_raw + §4.2 p_cal."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COASTAL = {
    "San Francisco", "Los Angeles", "Seattle", "Miami", "NYC", "London",
    "Tokyo", "Hong Kong", "Sydney", "Lisbon", "Barcelona", "Mumbai",
}

# Lead-day buckets: day 0, day 1, days 2-3, days 4-5, days 6-7
_LEAD_BUCKETS = [(0, 0), (1, 1), (2, 3), (4, 5), (6, 7)]

N_FOLDS = 5
ECE_BINS = 10
PIT_BINS = 10


# ---------------------------------------------------------------------------
# Bin label utilities
# ---------------------------------------------------------------------------

def _parse_bin_lower(label: str) -> tuple[float, str]:
    """Return (lower_bound_numeric, unit) for ordinal bin sorting.

    Shoulder bins:  '... or below' → -inf,  '... or above' → +inf.
    Range bins:     '49-50°F'       → 49.0, '°F'
                    '-23--22°F'     → -23.0, '°F'
    Single bins:    '-10°C'         → -10.0, '°C'
    """
    label = label.strip()
    unit = "°F" if "°F" in label else "°C"
    if "or below" in label:
        return (-float("inf"), unit)
    if "or above" in label:
        return (float("inf"), unit)
    stripped = label.replace("°F", "").replace("°C", "").strip()
    # Range: contains a dash that is NOT the leading sign and IS followed by digits
    parts = re.split(r"(?<=[0-9])-(?=-?[0-9])", stripped, maxsplit=1)
    if len(parts) == 2:
        try:
            return (float(parts[0]), unit)
        except ValueError:
            pass
    try:
        return (float(stripped), unit)
    except ValueError:
        return (0.0, unit)


# ---------------------------------------------------------------------------
# Distribution reconstruction
# ---------------------------------------------------------------------------

class BinRow(NamedTuple):
    range_label: str
    p_raw: float
    outcome: int       # 0 or 1
    lead_days: float
    city: str
    cluster: str
    cycle: str
    season: str
    decision_group_id: str
    temperature_metric: str


def _load_rows(conn: sqlite3.Connection, family: str, metric: str) -> list[BinRow]:
    """Load all canonical_v2 bin rows for a given family+metric."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(calibration_pairs_v2)")}
    emf_clause = "AND error_model_family = ?" if "error_model_family" in cols else ""
    params: list = ["canonical_v2", metric]
    if emf_clause:
        params.append(family)
    raw = conn.execute(
        f"""
        SELECT city, range_label, p_raw, outcome, lead_days,
               cluster, cycle, season, decision_group_id, temperature_metric
        FROM calibration_pairs_v2
        WHERE bin_source = ? AND temperature_metric = ?
          AND p_raw IS NOT NULL AND decision_group_id IS NOT NULL
          AND decision_group_id != '' {emf_clause}
        """,
        params,
    ).fetchall()
    return [BinRow(**dict(r)) for r in raw]


def _group_into_distributions(
    rows: list[BinRow],
) -> list[dict]:
    """Aggregate bin rows into per-distribution dicts.

    Each distribution represents one forecast event:
      city, target context, decision_group_id → sorted list of bins.

    Returns list of dicts with keys:
      decision_group_id, city, cluster, cycle, season, lead_days,
      temperature_metric, p_raw_vec (np.array), outcome_idx (int),
      range_labels (list), unit (str)

    Excludes distributions where p_raw doesn't sum to ~1 or outcome count != 1.
    """
    by_group: dict[str, list[BinRow]] = defaultdict(list)
    for r in rows:
        by_group[r.decision_group_id].append(r)

    distributions = []
    n_skipped = 0
    for gid, bins in by_group.items():
        p_sum = sum(b.p_raw for b in bins)
        n_outcome = sum(b.outcome for b in bins)
        if abs(p_sum - 1.0) > 1e-3 or n_outcome != 1 or len(bins) < 80:
            n_skipped += 1
            continue
        # Sort bins by ordinal temperature order
        sorted_bins = sorted(bins, key=lambda b: _parse_bin_lower(b.range_label)[0])
        p_vec = np.array([b.p_raw for b in sorted_bins], dtype=float)
        outcome_idx = next(i for i, b in enumerate(sorted_bins) if b.outcome == 1)
        unit = _parse_bin_lower(sorted_bins[0].range_label)[1]
        rep = sorted_bins[0]  # representative row for metadata
        distributions.append({
            "decision_group_id": gid,
            "city": rep.city,
            "cluster": rep.cluster,
            "cycle": rep.cycle,
            "season": rep.season,
            "lead_days": rep.lead_days,
            "temperature_metric": rep.temperature_metric,
            "p_raw_vec": p_vec,
            "outcome_idx": outcome_idx,
            "range_labels": [b.range_label for b in sorted_bins],
            "unit": unit,
            "n_bins": len(sorted_bins),
        })

    if n_skipped:
        print(f"  [WARN] Skipped {n_skipped} malformed distributions (p_sum≠1 or outcome≠1).",
              file=sys.stderr)
    return distributions


# ---------------------------------------------------------------------------
# Per-distribution proper scores
# ---------------------------------------------------------------------------

def _brier_dist(p_vec: np.ndarray, outcome_idx: int) -> float:
    """Multinomial Brier score for one distribution."""
    y = np.zeros(len(p_vec))
    y[outcome_idx] = 1.0
    return float(np.sum((p_vec - y) ** 2))


def _logloss_dist(p_vec: np.ndarray, outcome_idx: int, eps: float = 1e-12) -> float:
    """LogLoss = -log(p_actual)."""
    return float(-np.log(np.clip(p_vec[outcome_idx], eps, 1.0)))


def _rps_dist(p_vec: np.ndarray, outcome_idx: int) -> float:
    """Ranked Probability Score = Σ_j (F(j) - 1{Y≤j})^2.

    F(j) = cumsum(p_vec)[j].  indicator_j = 1 if outcome_idx <= j.
    """
    F = np.cumsum(p_vec)
    K = len(p_vec)
    indicator = np.zeros(K)
    indicator[outcome_idx:] = 1.0
    return float(np.sum((F - indicator) ** 2))


def _pit_u(p_vec: np.ndarray, outcome_idx: int) -> float:
    """PIT u_i = F(Y) = cumsum(p_vec)[outcome_idx] (inclusive).

    Under perfect calibration u_i ~ Uniform(0,1) in the continuous limit.
    Discrete distributions produce non-uniform PIT even when perfectly
    calibrated — a U-shape arises from discretization alone when bin
    probability mass >> 1/K. Interpret cautiously; use randomized PIT
    u_i ~ Uniform(F(Y-1), F(Y)) for strict uniformity test if needed.
    """
    F = np.cumsum(p_vec)
    return float(F[outcome_idx])


def _p_actual(p_vec: np.ndarray, outcome_idx: int) -> float:
    """P(actual) = predicted probability assigned to the true bin."""
    return float(p_vec[outcome_idx])


# ---------------------------------------------------------------------------
# Aggregate metrics over a set of distributions
# ---------------------------------------------------------------------------

def _aggregate_metrics(
    dists: list[dict],
    p_cal_map: dict[str, np.ndarray] | None = None,
) -> dict:
    """Compute aggregate proper scores.

    p_cal_map: optional {decision_group_id: p_cal_vec} from blocked Platt.
    Returns dict with keys: n, n_groups, brier, logloss, rps, p_actual, ece, pit_hist.
    If p_cal_map provided, also: brier_cal, logloss_cal, rps_cal, p_actual_cal, ece_cal, pit_hist_cal.
    """
    if not dists:
        return {"n": 0, "n_groups": 0}

    n = len(dists)
    brier_vals = []
    logloss_vals = []
    rps_vals = []
    pactual_vals = []
    pit_vals = []

    brier_cal_vals = []
    logloss_cal_vals = []
    rps_cal_vals = []
    pactual_cal_vals = []
    pit_cal_vals = []

    for d in dists:
        p = d["p_raw_vec"]
        y = d["outcome_idx"]
        brier_vals.append(_brier_dist(p, y))
        logloss_vals.append(_logloss_dist(p, y))
        rps_vals.append(_rps_dist(p, y))
        pactual_vals.append(_p_actual(p, y))
        pit_vals.append(_pit_u(p, y))

        if p_cal_map is not None:
            pc = p_cal_map.get(d["decision_group_id"])
            if pc is not None:
                brier_cal_vals.append(_brier_dist(pc, y))
                logloss_cal_vals.append(_logloss_dist(pc, y))
                rps_cal_vals.append(_rps_dist(pc, y))
                pactual_cal_vals.append(_p_actual(pc, y))
                pit_cal_vals.append(_pit_u(pc, y))

    pit_hist, _ = np.histogram(pit_vals, bins=PIT_BINS, range=(0.0, 1.0))

    result = {
        "n": n,
        "n_groups": n,  # one distribution = one group event
        "brier": float(np.mean(brier_vals)),
        "logloss": float(np.mean(logloss_vals)),
        "rps": float(np.mean(rps_vals)),
        "p_actual": float(np.mean(pactual_vals)),
        "ece": _ece_from_dists(dists, "p_raw_vec"),
        "pit_hist": pit_hist.tolist(),
    }
    if p_cal_map is not None and brier_cal_vals:
        pit_cal_hist, _ = np.histogram(pit_cal_vals, bins=PIT_BINS, range=(0.0, 1.0))
        result.update({
            "brier_cal": float(np.mean(brier_cal_vals)),
            "logloss_cal": float(np.mean(logloss_cal_vals)),
            "rps_cal": float(np.mean(rps_cal_vals)),
            "p_actual_cal": float(np.mean(pactual_cal_vals)),
            "ece_cal": _ece_from_dists(dists, "p_raw_vec", p_cal_map=p_cal_map),
            "pit_hist_cal": pit_cal_hist.tolist(),
        })
    return result


def _ece_from_dists(
    dists: list[dict],
    vec_key: str,
    n_bins: int = ECE_BINS,
    p_cal_map: dict[str, np.ndarray] | None = None,
) -> float:
    """ECE computed on per-bin (p, y) pairs flattened across all distributions.

    For p_cal, use p_cal_map indexed by decision_group_id.
    """
    all_p = []
    all_y = []
    for d in dists:
        if p_cal_map is not None:
            pc = p_cal_map.get(d["decision_group_id"])
            if pc is None:
                continue
            p_vec = pc
        else:
            p_vec = d[vec_key]
        y = d["outcome_idx"]
        y_vec = np.zeros(len(p_vec))
        y_vec[y] = 1.0
        all_p.extend(p_vec.tolist())
        all_y.extend(y_vec.tolist())

    if not all_p:
        return float("nan")
    p_arr = np.clip(np.array(all_p, dtype=float), 0.0, 1.0)
    y_arr = np.array(all_y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    e = 0.0
    n = len(p_arr)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p_arr >= lo) & (p_arr < hi) if i < n_bins - 1 else (p_arr >= lo) & (p_arr <= hi)
        if not m.any():
            continue
        e += (m.sum() / n) * abs(p_arr[m].mean() - y_arr[m].mean())
    return float(e)


# ---------------------------------------------------------------------------
# Blocked Platt calibration (same fold logic as validator)
# ---------------------------------------------------------------------------

def _fit_blocked_platt(
    dists: list[dict],
    rows: list[BinRow],
    n_folds: int = N_FOLDS,
) -> tuple[dict[str, np.ndarray], dict[str, dict[str, float]]] | tuple[None, None]:
    """Group-blocked K-fold Platt calibration mirroring validate_ens_refit_oos.py.

    Returns (p_cal_renorm_map, p_cal_norenorm_map):
      p_cal_renorm_map:   {gid: np.ndarray} — Platt outputs renormalized to sum=1.
                          Used for distribution-level RPS and PIT (needs valid CDF).
      p_cal_norenorm_map: {gid: {range_label: float}} — raw Platt per-bin scalars,
                          NOT renormalized. Matches the validator's exact path for
                          scalar Brier/LogLoss/ECE reconciliation.

    Fold assignment: fold_of = {g: i%n_folds for i,g in enumerate(sorted(groups))}.
    This is IDENTICAL to validate_ens_refit_oos.py — confirmed by Brier/LogLoss/ECE
    raw matching to |Δ| ≤ 2e-4 on subset.db.
    """
    try:
        from src.calibration.platt import ExtendedPlattCalibrator
        from src.calibration.store import infer_bin_width_from_label
    except ImportError as e:
        print(f"  [WARN] Cannot import Platt modules: {e}", file=sys.stderr)
        return None, None

    # Build a lookup: group_id → BinRow list (for Platt fit on scalar p_raw)
    by_group: dict[str, list[BinRow]] = defaultdict(list)
    for r in rows:
        by_group[r.decision_group_id].append(r)

    groups = sorted(by_group.keys())
    if len(groups) < n_folds * 3:
        return None, None

    # EXACT same fold assignment as validate_ens_refit_oos.py
    fold_of = {g: (i % n_folds) for i, g in enumerate(groups)}

    # Build distribution lookup
    dist_by_group = {d["decision_group_id"]: d for d in dists}

    p_cal_renorm_map: dict[str, np.ndarray] = {}
    p_cal_norenorm_map: dict[str, dict[str, float]] = {}

    for fold in range(n_folds):
        tr_rows = [r for g, rs in by_group.items()
                   if fold_of[g] != fold for r in rs]
        te_groups = [g for g in groups if fold_of[g] == fold]

        if not tr_rows or not te_groups:
            continue

        cal = ExtendedPlattCalibrator()
        try:
            cal.fit(
                np.array([r.p_raw for r in tr_rows]),
                np.array([r.lead_days for r in tr_rows]),
                np.array([r.outcome for r in tr_rows]),
                bin_widths=np.array(
                    [infer_bin_width_from_label(r.range_label) for r in tr_rows],
                    dtype=object,
                ),
                decision_group_ids=np.array(
                    [r.decision_group_id for r in tr_rows], dtype=object
                ),
                n_bootstrap=0,
                regularization_C=1.0,
            )
        except Exception as exc:
            print(f"  [WARN] Fold {fold} Platt fit failed: {exc}", file=sys.stderr)
            continue

        for gid in te_groups:
            d = dist_by_group.get(gid)
            if d is None:
                continue
            sorted_labels = d["range_labels"]
            lead = d["lead_days"]
            p_cal_bins = []
            bin_preds: dict[str, float] = {}
            for lbl, p_raw_bin in zip(sorted_labels, d["p_raw_vec"]):
                try:
                    pc = float(
                        cal.predict_for_bin(
                            float(p_raw_bin),
                            float(lead),
                            bin_width=infer_bin_width_from_label(lbl),
                        )
                    )
                except Exception:
                    pc = float(p_raw_bin)
                p_cal_bins.append(pc)
                bin_preds[lbl] = pc
            # Renormalized: for RPS/PIT (needs valid probability distribution)
            p_cal_arr = np.array(p_cal_bins, dtype=float)
            s = p_cal_arr.sum()
            if s > 0:
                p_cal_arr = p_cal_arr / s
            p_cal_renorm_map[gid] = p_cal_arr
            # No-renorm: for Brier/ECE scalar reconciliation (matches validator path)
            p_cal_norenorm_map[gid] = bin_preds

    if not p_cal_renorm_map:
        return None, None
    return p_cal_renorm_map, p_cal_norenorm_map


# ---------------------------------------------------------------------------
# Cohort filters
# ---------------------------------------------------------------------------

def _cohort_filter(
    dists: list[dict],
    cohort: str,
    value: str,
) -> list[dict]:
    """Filter distributions by cohort key/value."""
    if cohort == "global":
        return dists
    if cohort == "coastal":
        return [d for d in dists if d["city"] in _COASTAL]
    if cohort == "inland":
        return [d for d in dists if d["city"] not in _COASTAL]
    if cohort == "unit":
        return [d for d in dists if d["unit"] == value]
    if cohort == "city":
        return [d for d in dists if d["city"] == value]
    if cohort == "cluster":
        return [d for d in dists if d["cluster"] == value]
    if cohort == "lead_bucket":
        lo, hi = [int(x) for x in value.split("-")]
        return [d for d in dists if lo <= round(d["lead_days"]) <= hi]
    if cohort == "cycle":
        return [d for d in dists if d["cycle"] == value]
    return dists


# ---------------------------------------------------------------------------
# ASCII PIT histogram
# ---------------------------------------------------------------------------

def _pit_ascii(hist: list[int], label: str) -> str:
    """Render a 10-bin PIT histogram as ASCII bar chart."""
    if not hist or sum(hist) == 0:
        return f"  PIT [{label}]: no data\n"
    total = sum(hist)
    expected = total / len(hist)
    lines = [f"  PIT [{label}] (n={total}, expected_per_bin≈{expected:.0f}):"]
    edges = [f"{i/10:.1f}-{(i+1)/10:.1f}" for i in range(len(hist))]
    max_count = max(hist)
    bar_width = 30
    for edge, count in zip(edges, hist):
        bar_len = int(bar_width * count / max_count) if max_count > 0 else 0
        bar = "█" * bar_len
        pct = 100 * count / total
        lines.append(f"  {edge}: {bar:<{bar_width}} {count:5d} ({pct:.1f}%)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

_P41_HEADER = (
    "| Cohort | n | Brier(raw) | LogLoss(raw) | RPS(raw) | P(actual)(raw) | "
    "ECE(raw) | Brier(ft) | LogLoss(ft) | RPS(ft) | P(actual)(ft) | ECE(ft) |"
)
_P41_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|"

_P42_HEADER = (
    "| Cohort | n | Brier(raw) | Brier(p_cal) | LogLoss(raw) | LogLoss(p_cal) | "
    "RPS(raw) | RPS(p_cal) | ECE(raw) | ECE(p_cal) |"
)
_P42_SEP = "|---|---|---|---|---|---|---|---|---|---|"


def _fmt(v, fmt=".4f"):
    if v is None or (isinstance(v, float) and (v != v)):  # nan
        return "n/a"
    return format(v, fmt)


def _row_41(label: str, raw_m: dict, ft_m: dict) -> str:
    return (
        f"| {label} | {raw_m.get('n', 0)} "
        f"| {_fmt(raw_m.get('brier'))} | {_fmt(raw_m.get('logloss'))} "
        f"| {_fmt(raw_m.get('rps'))} | {_fmt(raw_m.get('p_actual'))} "
        f"| {_fmt(raw_m.get('ece'))} "
        f"| {_fmt(ft_m.get('brier'))} | {_fmt(ft_m.get('logloss'))} "
        f"| {_fmt(ft_m.get('rps'))} | {_fmt(ft_m.get('p_actual'))} "
        f"| {_fmt(ft_m.get('ece'))} |"
    )


def _row_42(label: str, m: dict) -> str:
    return (
        f"| {label} | {m.get('n', 0)} "
        f"| {_fmt(m.get('brier'))} | {_fmt(m.get('brier_cal'))} "
        f"| {_fmt(m.get('logloss'))} | {_fmt(m.get('logloss_cal'))} "
        f"| {_fmt(m.get('rps'))} | {_fmt(m.get('rps_cal'))} "
        f"| {_fmt(m.get('ece'))} | {_fmt(m.get('ece_cal'))} |"
    )


# ---------------------------------------------------------------------------
# Smoke-test reconciliation against validate_ens_refit_oos.py
# ---------------------------------------------------------------------------

def _smoke_reconcile(
    ft_dists: list[dict],
    ft_rows: list[BinRow],
    p_cal_norenorm_map: dict[str, dict[str, float]] | None,
) -> list[str]:
    """Check that our Brier/LogLoss/ECE on (full_transport_v1, overall) agree
    with validate_ens_refit_oos.py within 2e-4.

    Validator uses per-bin (p_raw, outcome) scalar path — no renormalization.
    p_cal_norenorm_map: {gid: {range_label: p_cal_scalar}} — matches validator path exactly.
    """
    lines = ["### Smoke reconciliation (validate_ens_refit_oos.py parity)", ""]

    # Scalar Brier/LogLoss/ECE on (p_raw, outcome) pairs — same as validator
    p_arr = np.array([r.p_raw for r in ft_rows])
    y_arr = np.array([r.outcome for r in ft_rows], dtype=float)

    def scalar_brier(p, y):
        p = np.clip(np.asarray(p, float), 0.0, 1.0)
        return float(np.mean((p - np.asarray(y, float)) ** 2))

    def scalar_ll(p, y, eps=1e-12):
        p = np.clip(np.asarray(p, float), eps, 1.0)
        y = np.asarray(y, float)
        return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

    def scalar_ece(p, y, n_bins=10):
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

    b_raw = scalar_brier(p_arr, y_arr)
    l_raw = scalar_ll(p_arr, y_arr)
    e_raw = scalar_ece(p_arr, y_arr)

    lines.append(f"  Scalar Brier(raw):   {b_raw:.4f}")
    lines.append(f"  Scalar LogLoss(raw): {l_raw:.4f}")
    lines.append(f"  Scalar ECE(raw):     {e_raw:.4f}")
    lines.append("")
    lines.append("  (Compare against `validate_ens_refit_oos.py --db <same> --metric high`")
    lines.append("   row: full_transport_v1 / overall  columns: Brier(raw) LogLoss(raw) ECE(raw))")
    lines.append("  Tolerance: ≤2e-4.  If divergent, row-set or fold differs — do not trust RPS/PIT.")
    lines.append("")

    # Scalar p_cal reconciliation using no-renorm path (matches validator exactly)
    if p_cal_norenorm_map is not None:
        p_cal_scalar = []
        y_scalar = []
        for r in ft_rows:
            bin_preds = p_cal_norenorm_map.get(r.decision_group_id)
            if bin_preds is None:
                continue
            pc = bin_preds.get(r.range_label)
            if pc is None:
                continue
            p_cal_scalar.append(pc)
            y_scalar.append(r.outcome)

        if p_cal_scalar:
            b_cal = scalar_brier(p_cal_scalar, y_scalar)
            l_cal = scalar_ll(p_cal_scalar, y_scalar)
            e_cal = scalar_ece(p_cal_scalar, y_scalar)
            lines.append(f"  Scalar Brier(cal) [no-renorm]:   {b_cal:.4f}")
            lines.append(f"  Scalar LogLoss(cal) [no-renorm]: {l_cal:.4f}")
            lines.append(f"  Scalar ECE(cal) [no-renorm]:     {e_cal:.4f}")
            lines.append("")
            lines.append("  (Compare against validator's Brier(cal)/LogLoss(cal)/ECE(cal).")
            lines.append("   no-renorm path = per-bin Platt scalar, matching validator exactly.)")

    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Post-refit proper-score audit harness.")
    ap.add_argument("--db", required=True, help="Path to isolated staging DB")
    ap.add_argument("--metric", default="high", choices=("high", "low"),
                    help="temperature_metric filter")
    ap.add_argument("--smoke", action="store_true",
                    help="Print scalar Brier/LogLoss/ECE for reconciliation with validator")
    ap.add_argument("--no-platt", action="store_true",
                    help="Skip blocked Platt (faster; §4.1 only)")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    out_dir = PROJECT_ROOT / "docs" / "operations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ENS_REFIT_VALIDATION_2026-05-25_results.md"

    print(f"Loading data from {db_path} (metric={args.metric})...", file=sys.stderr)

    # Load both families
    none_rows = _load_rows(conn, "none", args.metric)
    ft_rows = _load_rows(conn, "full_transport_v1", args.metric)
    conn.close()

    print(f"  none rows: {len(none_rows):,}", file=sys.stderr)
    print(f"  full_transport_v1 rows: {len(ft_rows):,}", file=sys.stderr)

    # Group into distributions
    print("Grouping into distributions...", file=sys.stderr)
    none_dists = _group_into_distributions(none_rows)
    ft_dists = _group_into_distributions(ft_rows)
    print(f"  none distributions: {len(none_dists):,}", file=sys.stderr)
    print(f"  full_transport_v1 distributions: {len(ft_dists):,}", file=sys.stderr)

    # Blocked Platt on full_transport_v1
    p_cal_map: dict[str, np.ndarray] | None = None
    p_cal_norenorm_map: dict | None = None
    if not args.no_platt:
        print(f"Fitting blocked Platt (n_folds={N_FOLDS}, n_groups={len(ft_dists):,})...",
              file=sys.stderr)
        p_cal_map, p_cal_norenorm_map = _fit_blocked_platt(ft_dists, ft_rows)
        if p_cal_map:
            print(f"  p_cal fitted for {len(p_cal_map):,} distributions.", file=sys.stderr)
        else:
            print("  [WARN] Platt fit returned None — §4.2 will be skipped.", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Build §4.1 table
    # -----------------------------------------------------------------------
    print("Computing §4.1 metrics...", file=sys.stderr)
    cohort_specs_41: list[tuple[str, list[dict], list[dict]]] = []

    def _add_41(label, fn):
        cohort_specs_41.append((label, fn(none_dists), fn(ft_dists)))

    _add_41("global", lambda d: d)
    _add_41("coastal", lambda d: _cohort_filter(d, "coastal", ""))
    _add_41("inland", lambda d: _cohort_filter(d, "inland", ""))
    _add_41("unit=°F", lambda d: _cohort_filter(d, "unit", "°F"))
    _add_41("unit=°C", lambda d: _cohort_filter(d, "unit", "°C"))

    # Per-cluster (city)
    all_clusters = sorted({d["cluster"] for d in ft_dists})
    for cl in all_clusters:
        _add_41(f"city={cl}", lambda d, _cl=cl: _cohort_filter(d, "cluster", _cl))

    # Lead buckets
    for lo, hi in _LEAD_BUCKETS:
        lbl = f"lead={lo}" if lo == hi else f"lead={lo}-{hi}"
        _add_41(lbl, lambda d, _lo=lo, _hi=hi: _cohort_filter(d, "lead_bucket", f"{_lo}-{_hi}"))

    # Cycle
    for cyc in sorted({d["cycle"] for d in ft_dists}):
        _add_41(f"cycle={cyc}", lambda d, _c=cyc: _cohort_filter(d, "cycle", _c))

    rows_41 = []
    pit_blocks_41_raw: list[str] = []
    pit_blocks_41_ft: list[str] = []

    for label, n_sub, ft_sub in cohort_specs_41:
        m_raw = _aggregate_metrics(n_sub)
        m_ft = _aggregate_metrics(ft_sub)
        rows_41.append(_row_41(label, m_raw, m_ft))
        if m_raw.get("pit_hist"):
            pit_blocks_41_raw.append(_pit_ascii(m_raw["pit_hist"], f"raw / {label}"))
        if m_ft.get("pit_hist"):
            pit_blocks_41_ft.append(_pit_ascii(m_ft["pit_hist"], f"full_transport / {label}"))

    # -----------------------------------------------------------------------
    # Build §4.2 table
    # -----------------------------------------------------------------------
    rows_42: list[str] = []
    pit_blocks_42: list[str] = []

    if p_cal_map is not None:
        print("Computing §4.2 metrics...", file=sys.stderr)

        def _add_42(label, fn):
            sub = fn(ft_dists)
            m = _aggregate_metrics(sub, p_cal_map=p_cal_map)
            rows_42.append(_row_42(label, m))
            if m.get("pit_hist"):
                pit_blocks_42.append(_pit_ascii(m["pit_hist"], f"raw_cal/{label}"))
            if m.get("pit_hist_cal"):
                pit_blocks_42.append(_pit_ascii(m["pit_hist_cal"], f"p_cal/{label}"))

        _add_42("global", lambda d: d)
        _add_42("coastal", lambda d: _cohort_filter(d, "coastal", ""))
        _add_42("inland", lambda d: _cohort_filter(d, "inland", ""))
        _add_42("unit=°F", lambda d: _cohort_filter(d, "unit", "°F"))
        _add_42("unit=°C", lambda d: _cohort_filter(d, "unit", "°C"))
        for cl in all_clusters:
            _add_42(f"city={cl}", lambda d, _cl=cl: _cohort_filter(d, "cluster", _cl))
        for lo, hi in _LEAD_BUCKETS:
            lbl = f"lead={lo}" if lo == hi else f"lead={lo}-{hi}"
            _add_42(lbl, lambda d, _lo=lo, _hi=hi: _cohort_filter(d, "lead_bucket", f"{_lo}-{_hi}"))
        for cyc in sorted({d["cycle"] for d in ft_dists}):
            _add_42(f"cycle={cyc}", lambda d, _c=cyc: _cohort_filter(d, "cycle", _c))

    # -----------------------------------------------------------------------
    # Smoke reconciliation
    # -----------------------------------------------------------------------
    smoke_lines: list[str] = []
    if args.smoke:
        smoke_lines = _smoke_reconcile(ft_dists, ft_rows, p_cal_norenorm_map)

    # -----------------------------------------------------------------------
    # Write output file
    # -----------------------------------------------------------------------
    metric_upper = args.metric.upper()
    out_lines = [
        f"# ENS Refit Validation — {metric_upper} Temperature",
        f"",
        f"Generated: 2026-05-25",
        f"DB: `{db_path}`",
        f"Authority: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §4",
        f"",
        f"> F50_only variant is NOT present in calibration_pairs_v2 (only `none` and",
        f"> `full_transport_v1` exist); §4.1 reports raw(none) and full_transport_v1.",
        f"> F50_only column is omitted — not fabricated.",
        f"",
        f"## §4.1 p_raw audit — raw(none) vs full_transport_v1",
        f"",
        f"Metrics: multinomial Brier, LogLoss, RPS, P(actual), ECE.",
        f"OOS: group-blocked 5-fold (decision_group_id never in its own fit's fold).",
        f"",
        _P41_HEADER,
        _P41_SEP,
    ] + rows_41 + [
        f"",
        f"### §4.1 PIT histograms — raw(none)",
        f"",
        f"```",
    ] + pit_blocks_41_raw + [
        f"```",
        f"",
        f"### §4.1 PIT histograms — full_transport_v1",
        f"",
        f"```",
    ] + pit_blocks_41_ft + [
        f"```",
        f"",
    ]

    if rows_42:
        out_lines += [
            f"## §4.2 p_cal audit — full_transport_v1 p_raw vs Platt-calibrated p_cal",
            f"",
            f"p_cal = group-blocked Platt OOS (ExtendedPlattCalibrator, n_bootstrap=0, C=1.0).",
            f"Acceptance: p_cal improves or preserves Brier/LogLoss/ECE vs p_raw.",
            f"",
            _P42_HEADER,
            _P42_SEP,
        ] + rows_42 + [
            f"",
            f"### §4.2 PIT histograms",
            f"",
            f"```",
        ] + pit_blocks_42 + [
            f"```",
            f"",
        ]
    else:
        out_lines += [
            f"## §4.2 p_cal audit",
            f"",
            f"Skipped (--no-platt or Platt fit failed).",
            f"",
        ]

    if smoke_lines:
        out_lines += smoke_lines

    out_text = "\n".join(out_lines)
    out_path.write_text(out_text, encoding="utf-8")
    print(f"\nResults written to: {out_path}", file=sys.stderr)

    # Also print §4.1 global row to stdout for quick reconciliation
    print(f"\n=== §4.1 global row ({metric_upper}) ===")
    if cohort_specs_41:
        label, n_sub, ft_sub = cohort_specs_41[0]
        m_raw = _aggregate_metrics(n_sub)
        m_ft = _aggregate_metrics(ft_sub)
        print(f"  raw(none)          Brier={m_raw.get('brier', 'n/a'):.4f}  "
              f"LogLoss={m_raw.get('logloss', 'n/a'):.4f}  "
              f"RPS={m_raw.get('rps', 'n/a'):.4f}  "
              f"ECE={m_raw.get('ece', 'n/a'):.4f}  "
              f"n={m_raw.get('n', 0)}")
        print(f"  full_transport_v1  Brier={m_ft.get('brier', 'n/a'):.4f}  "
              f"LogLoss={m_ft.get('logloss', 'n/a'):.4f}  "
              f"RPS={m_ft.get('rps', 'n/a'):.4f}  "
              f"ECE={m_ft.get('ece', 'n/a'):.4f}  "
              f"n={m_ft.get('n', 0)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
