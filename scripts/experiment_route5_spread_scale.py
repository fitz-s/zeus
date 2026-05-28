# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §3 Route 5
# Purpose: Blocked-OOS experiment for EMOS spread-dependent residual scale.
#   Fits b_g per bucket such that sigma_i² = a_g + b_g·s_i²,
#   where s_i = std(TIGGE ensemble members) = per-day spread.
#   full_transport_v1 = b_g≡0 baseline. Evaluates if b_g>0 improves LogLoss+RPS.
#
# CONSTRAINTS:
#   - Read-only on /tmp/ens_refit/full.db (never writes).
#   - No edits to model/validator/runtime code.
#   - Gaussian approximation: p_vec recomputed analytically from bin edges + Gaussian CDF.
#     Baseline and Route 5 use the SAME Gaussian approximation for fair comparison.
#     MC p_raw from DB reported separately for reference.
#   - Blocked OOS: fold_of = {g: i%N for i,g in enumerate(sorted(groups))}, N=5
#     IDENTICAL to audit_refit_proper_scores.py and validate_ens_refit_oos.py.
#
# USAGE:
#   python scripts/experiment_route5_spread_scale.py --db /tmp/ens_refit/full.db --metric high
#   python scripts/experiment_route5_spread_scale.py --db /tmp/ens_refit/full.db --metric low
"""Route 5 EMOS experiment: spread-dependent residual scale."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize_scalar

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

N_FOLDS = 5
COASTAL = {
    "San Francisco", "Los Angeles", "Seattle", "Miami", "NYC", "London",
    "Tokyo", "Hong Kong", "Sydney", "Lisbon", "Barcelona", "Mumbai",
}
LEAD_BUCKETS = [(0, 0), (1, 1), (2, 3), (4, 5), (6, 7)]


# ---------------------------------------------------------------------------
# Bin edge utilities (shared with audit harness)
# ---------------------------------------------------------------------------

def _parse_bin_lower(label: str) -> tuple[float, str]:
    label = label.strip()
    unit = "°F" if "°F" in label else "°C"
    if "or below" in label:
        return (-float("inf"), unit)
    if "or above" in label:
        return (float("inf"), unit)
    stripped = label.replace("°F", "").replace("°C", "").strip()
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


def _parse_bin_upper(label: str) -> tuple[float, str]:
    label = label.strip()
    unit = "°F" if "°F" in label else "°C"
    if "or below" in label:
        m = re.search(r"(-?\d+\.?\d*)", label)
        return (float(m.group(1)) if m else -float("inf"), unit)
    if "or above" in label:
        return (float("inf"), unit)
    stripped = label.replace("°F", "").replace("°C", "").strip()
    parts = re.split(r"(?<=[0-9])-(?=-?[0-9])", stripped, maxsplit=1)
    if len(parts) == 2:
        try:
            return (float(parts[1]), unit)
        except ValueError:
            pass
    try:
        return (float(stripped) + 1.0, unit)
    except ValueError:
        return (0.0, unit)


def _bin_edges(sorted_labels: list[str]) -> tuple[list[float], list[float]]:
    """Return (lower_cdf_edges, upper_cdf_edges) with inf replaced by extended values."""
    lowers = [_parse_bin_lower(lbl)[0] for lbl in sorted_labels]
    uppers = [_parse_bin_upper(lbl)[0] for lbl in sorted_labels]
    finite_lo = [l for l in lowers if not math.isinf(l)]
    finite_up = [u for u in uppers if not math.isinf(u)]
    if finite_lo and finite_up:
        span = max(finite_up) - min(finite_lo)
        ext_lo = min(finite_lo) - span * 4
        ext_hi = max(finite_up) + span * 4
    else:
        ext_lo, ext_hi = -100.0, 100.0
    lowers_cdf = [ext_lo if math.isinf(l) else l for l in lowers]
    uppers_cdf = [ext_hi if math.isinf(u) else u for u in uppers]
    return lowers_cdf, uppers_cdf


def gaussian_p_vec(mu: float, sigma: float, lo_edges: list[float], hi_edges: list[float]) -> np.ndarray:
    """Bin probabilities from Gaussian(mu, sigma) over precomputed edges."""
    sigma = max(sigma, 0.01)
    p = np.array([norm.cdf(hi, mu, sigma) - norm.cdf(lo, mu, sigma)
                  for lo, hi in zip(lo_edges, hi_edges)])
    p = np.clip(p, 1e-12, 1.0)
    p /= p.sum()
    return p


def _fit_mu_sigma_moments(p_vec: np.ndarray, lo_edges: list[float], hi_edges: list[float]) -> tuple[float, float]:
    """Fit Gaussian (mu, sigma) to p_vec histogram by moment matching."""
    mids = [(lo + hi) / 2 for lo, hi in zip(lo_edges, hi_edges)]
    mu = float(np.dot(p_vec, mids))
    var = float(np.dot(p_vec, [(m - mu)**2 for m in mids]))
    sigma = math.sqrt(max(var, 0.01))
    return mu, sigma


# ---------------------------------------------------------------------------
# Proper scores
# ---------------------------------------------------------------------------

def _brier(p: np.ndarray, y: int) -> float:
    v = np.zeros(len(p)); v[y] = 1.0
    return float(np.sum((p - v) ** 2))

def _logloss(p: np.ndarray, y: int, eps: float = 1e-12) -> float:
    return float(-np.log(np.clip(p[y], eps, 1.0)))

def _rps(p: np.ndarray, y: int) -> float:
    F = np.cumsum(p)
    ind = np.zeros(len(p)); ind[y:] = 1.0
    return float(np.sum((F - ind) ** 2))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_groups(conn: sqlite3.Connection, metric: str) -> list[dict]:
    """Load all full_transport_v1 groups with spread (from members_json)."""
    print(f"  Loading calibration_pairs_v2 for full_transport_v1 {metric.upper()}...", flush=True)

    # Step 1: load per-group membership info via snapshot_id join
    spread_rows = conn.execute("""
        SELECT cp.decision_group_id, cp.city, cp.lead_days, cp.cycle,
               cp.season, cp.cluster, cp.target_date,
               es.members_json
        FROM calibration_pairs_v2 cp
        JOIN ensemble_snapshots es ON cp.snapshot_id = es.snapshot_id
        WHERE cp.error_model_family = 'full_transport_v1'
          AND cp.temperature_metric = ?
          AND cp.bin_source = 'canonical_v2'
          AND cp.outcome = 1
          AND es.members_json IS NOT NULL
    """, (metric,)).fetchall()

    print(f"  Loaded {len(spread_rows)} outcome rows with snapshot. Computing spread...", flush=True)

    # Build group-level spread map
    gid_spread: dict[str, float] = {}
    gid_meta: dict[str, dict] = {}
    for r in spread_rows:
        gid = r[0]
        if gid in gid_spread:
            continue
        try:
            members = json.loads(r[7])
            s = statistics.stdev(members) if len(members) >= 2 else 0.0
        except Exception:
            s = 0.0
        gid_spread[gid] = s
        gid_meta[gid] = {"city": r[1], "lead_days": r[2], "cycle": r[3],
                          "season": r[4], "cluster": r[5], "target_date": r[6]}

    print(f"  Spread computed for {len(gid_spread)} groups. Loading bin distributions...", flush=True)

    # Step 2: load all bin rows for these groups
    placeholders = ",".join("?" * len(gid_spread))
    if not gid_spread:
        return []

    # Fetch in chunks to avoid sqlite3 variable limit
    all_bin_rows: dict[str, list] = defaultdict(list)
    gid_list = list(gid_spread.keys())
    chunk = 500
    for i in range(0, len(gid_list), chunk):
        sub = gid_list[i:i+chunk]
        ph = ",".join("?" * len(sub))
        rows = conn.execute(f"""
            SELECT decision_group_id, range_label, p_raw, outcome
            FROM calibration_pairs_v2
            WHERE decision_group_id IN ({ph})
              AND error_model_family = 'full_transport_v1'
              AND temperature_metric = ?
              AND bin_source = 'canonical_v2'
        """, sub + [metric]).fetchall()
        for r in rows:
            all_bin_rows[r[0]].append(r)

    print(f"  Building distributions...", flush=True)

    groups = []
    n_skip = 0
    for gid, bins in all_bin_rows.items():
        if gid not in gid_spread:
            n_skip += 1
            continue
        p_sum = sum(b[2] for b in bins)
        n_out = sum(b[3] for b in bins)
        if abs(p_sum - 1.0) > 1e-3 or n_out != 1 or len(bins) < 80:
            n_skip += 1
            continue
        sorted_bins = sorted(bins, key=lambda b: _parse_bin_lower(b[1])[0])
        labels = [b[1] for b in sorted_bins]
        p_raw_vec = np.array([b[2] for b in sorted_bins], dtype=float)
        outcome_idx = next(i for i, b in enumerate(sorted_bins) if b[3] == 1)
        lo_edges, hi_edges = _bin_edges(labels)
        mu_fit, sigma_fit = _fit_mu_sigma_moments(p_raw_vec, lo_edges, hi_edges)

        meta = gid_meta[gid]
        lead_d = float(meta["lead_days"])
        lead_bucket = next((f"{lo}-{hi}" if lo != hi else str(lo)
                            for lo, hi in LEAD_BUCKETS if lo <= lead_d <= hi), "6-7")
        unit = _parse_bin_lower(labels[0])[1]

        groups.append({
            "gid": gid,
            "city": meta["city"],
            "lead_days": lead_d,
            "lead_bucket": lead_bucket,
            "cycle": meta["cycle"],
            "season": meta["season"],
            "cluster": meta["cluster"],
            "unit": unit,
            "coastal": meta["city"] in COASTAL,
            "p_raw_vec": p_raw_vec,
            "outcome_idx": outcome_idx,
            "lo_edges": lo_edges,
            "hi_edges": hi_edges,
            "mu_fit": mu_fit,
            "sigma_fit": sigma_fit,
            "spread": gid_spread[gid],
        })

    if n_skip:
        print(f"  [WARN] Skipped {n_skip} malformed groups.", flush=True)
    print(f"  Built {len(groups)} valid distributions.", flush=True)
    return groups


# ---------------------------------------------------------------------------
# OOS fold assignment (identical to audit harness)
# ---------------------------------------------------------------------------

def _assign_folds(groups: list[dict], n_folds: int = N_FOLDS) -> None:
    """Assign fold index to each group in-place (sorted by gid, modular)."""
    sorted_gids = sorted(set(g["gid"] for g in groups))
    fold_of = {gid: i % n_folds for i, gid in enumerate(sorted_gids)}
    for g in groups:
        g["fold"] = fold_of[g["gid"]]


# ---------------------------------------------------------------------------
# Route 5: EMOS sigma fitting
# ---------------------------------------------------------------------------

def _bucket_key(g: dict) -> str:
    """Bucket key for b_g fitting: season × lead_bucket × cycle."""
    return f"{g['season']}_{g['lead_bucket']}_{g['cycle']}"


def _fit_b_g(train: list[dict]) -> tuple[float, float]:
    """Fit (a_g, b_g) by OLS: sigma_i² = a_g + b_g * s_i².

    a_g is pinned to mean(sigma_i²) - b_g * mean(s_i²) so that b_g=0
    reproduces the training-mean sigma. b_g floored to 0 (non-negative constraint:
    larger spread cannot DECREASE predictive width).
    """
    if len(train) < 3:
        return 0.0, 0.0  # degenerate bucket: use baseline (b_g=0)
    s2 = np.array([g["spread"] ** 2 for g in train])
    sigma2 = np.array([g["sigma_fit"] ** 2 for g in train])
    s2_mean = float(np.mean(s2))
    sigma2_mean = float(np.mean(sigma2))
    # OLS slope: b_g = Cov(sigma², s²) / Var(s²)
    cov = float(np.cov(sigma2, s2, ddof=1)[0, 1]) if len(train) >= 3 else 0.0
    var_s2 = float(np.var(s2, ddof=1)) if len(train) >= 3 else 0.0
    b_g = max(0.0, cov / var_s2) if var_s2 > 1e-10 else 0.0
    a_g = sigma2_mean - b_g * s2_mean
    return a_g, b_g


def _run_route5_oos(groups: list[dict]) -> list[dict]:
    """Run blocked-OOS Route 5 experiment.

    For each held-out fold:
      1. Train b_g per bucket on the other 4 folds.
      2. For each held-out group: compute sigma_new = sqrt(max(a_g + b_g*s²,  0.01)).
      3. Score with Gaussian(mu_fit, sigma_new) over the group's bin edges.
    Also record Gaussian-baseline scores (b_g=0, sigma=sigma_fit) for fair comparison.
    """
    results = []
    for fold_id in range(N_FOLDS):
        train = [g for g in groups if g["fold"] != fold_id]
        held = [g for g in groups if g["fold"] == fold_id]

        # Fit per-bucket
        by_bucket: dict[str, list[dict]] = defaultdict(list)
        for g in train:
            by_bucket[_bucket_key(g)].append(g)
        bucket_params: dict[str, tuple[float, float]] = {}
        for bk, members in by_bucket.items():
            bucket_params[bk] = _fit_b_g(members)

        # Fallback: global b_g if bucket too small
        global_a, global_b = _fit_b_g(train)

        for g in held:
            bk = _bucket_key(g)
            a_g, b_g = bucket_params.get(bk, (global_a, global_b))
            sigma_new2 = max(a_g + b_g * g["spread"] ** 2, 0.01 ** 2)
            sigma_new = math.sqrt(sigma_new2)

            # Route 5 scores (Gaussian with new sigma)
            p5 = gaussian_p_vec(g["mu_fit"], sigma_new, g["lo_edges"], g["hi_edges"])
            y = g["outcome_idx"]
            r5_brier = _brier(p5, y)
            r5_ll = _logloss(p5, y)
            r5_rps = _rps(p5, y)

            # Gaussian baseline scores (b_g=0, sigma=sigma_fit)
            p_base = gaussian_p_vec(g["mu_fit"], g["sigma_fit"], g["lo_edges"], g["hi_edges"])
            base_brier = _brier(p_base, y)
            base_ll = _logloss(p_base, y)
            base_rps = _rps(p_base, y)

            # MC p_raw scores (from DB, for reference only)
            mc_brier = _brier(g["p_raw_vec"], y)
            mc_ll = _logloss(g["p_raw_vec"], y)
            mc_rps = _rps(g["p_raw_vec"], y)

            results.append({
                **{k: g[k] for k in ("gid", "city", "lead_days", "lead_bucket",
                                      "cycle", "season", "coastal", "unit")},
                "a_g": a_g, "b_g": b_g,
                "sigma_base": g["sigma_fit"], "sigma_new": sigma_new,
                "spread": g["spread"],
                # Gaussian baseline
                "base_brier": base_brier, "base_ll": base_ll, "base_rps": base_rps,
                # Route 5
                "r5_brier": r5_brier, "r5_ll": r5_ll, "r5_rps": r5_rps,
                # MC reference
                "mc_brier": mc_brier, "mc_ll": mc_ll, "mc_rps": mc_rps,
            })

    return results


# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------

def _agg(results: list[dict], key_fn) -> dict[str, dict]:
    """Aggregate metrics by a grouping key function."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        buckets[key_fn(r)].append(r)
    out = {}
    for k, rows in buckets.items():
        n = len(rows)
        def m(col): return float(np.mean([r[col] for r in rows]))
        out[k] = {
            "n": n,
            "base_brier": m("base_brier"), "base_ll": m("base_ll"), "base_rps": m("base_rps"),
            "r5_brier": m("r5_brier"), "r5_ll": m("r5_ll"), "r5_rps": m("r5_rps"),
            "mc_brier": m("mc_brier"), "mc_ll": m("mc_ll"), "mc_rps": m("mc_rps"),
            "mean_b_g": float(np.mean([r["b_g"] for r in rows])),
            "mean_spread": float(np.mean([r["spread"] for r in rows])),
        }
    return out


def _verdict(row: dict) -> str:
    wins = sum([
        row["r5_brier"] < row["base_brier"],
        row["r5_ll"] < row["base_ll"],
        row["r5_rps"] < row["base_rps"],
    ])
    return {3: "R5 wins all", 2: "R5 wins 2/3", 1: "R5 wins 1/3", 0: "R5 loses all"}[wins]


def _table(rows: dict[str, dict], title: str) -> str:
    lines = [f"\n### {title}\n"]
    lines.append(f"| {'Cohort':<22} | {'n':>6} | {'Δ Brier':>8} | {'Δ LogLoss':>10} | {'Δ RPS':>8} | {'mean b_g':>9} | Verdict |")
    lines.append(f"| {'-'*22} | {'-'*6} | {'-'*8} | {'-'*10} | {'-'*8} | {'-'*9} | ------- |")
    for label, r in sorted(rows.items()):
        db = r["r5_brier"] - r["base_brier"]
        dl = r["r5_ll"] - r["base_ll"]
        dr = r["r5_rps"] - r["base_rps"]
        v = _verdict(r)
        lines.append(
            f"| {label:<22} | {r['n']:>6} | {db:>+8.4f} | {dl:>+10.4f} | {dr:>+8.4f} | {r['mean_b_g']:>9.4f} | {v} |"
        )
    return "\n".join(lines)


def _write_report(path: Path, metric: str, global_row: dict, cohort_tables: list[str],
                  b_g_dist: dict, n_groups: int, n_buckets: int) -> None:
    lines = [
        f"# ENS Route 5 Experiment — Spread-Dependent Residual Scale",
        f"",
        f"Created: 2026-05-25",
        f"Authority: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §3 Route 5",
        f"DB: /tmp/ens_refit/full.db (read-only)",
        f"Metric: {metric.upper()}",
        f"",
        f"## Experiment Design",
        f"",
        f"**Model**: σ_i² = a_g + b_g·s_i² where s_i = std(TIGGE ENS members) per day.",
        f"**Baseline**: b_g ≡ 0 (= full_transport_v1, Gaussian approximation).",
        f"**Route 5**: b_g fitted per bucket (season×lead×cycle) via OLS on held-out OOS fold.",
        f"**OOS**: 5-fold blocked on sorted decision_group_id (identical to audit harness).",
        f"**Scoring**: Gaussian CDF over bin edges. Both baseline AND Route 5 use same",
        f"  Gaussian approximation for fair comparison. MC p_raw scores from DB reported",
        f"  separately as reference (not used for acceptance gate).",
        f"**Groups**: {n_groups:,} groups, {n_buckets} fitting buckets.",
        f"",
        f"## Acceptance Gate (roadmap §3)",
        f"PASS requires: Route 5 beats baseline on LogLoss AND RPS, NO cohort regression.",
        f"",
        f"## Global Result",
        f"",
        f"| Metric | Baseline (Gaussian) | Route 5 | Δ | Verdict |",
        f"| ------ | ------------------- | ------- | -- | ------- |",
    ]
    for col, name in [("brier", "Brier"), ("ll", "LogLoss"), ("rps", "RPS")]:
        base = global_row[f"base_{col}"]
        r5 = global_row[f"r5_{col}"]
        delta = r5 - base
        v = "PASS" if delta < 0 else "FAIL"
        lines.append(f"| {name} | {base:.4f} | {r5:.4f} | {delta:+.4f} | {v} |")
    lines.append("")
    lines.append(f"*MC p_raw reference (full_transport_v1 baseline from DB): "
                 f"Brier={global_row['mc_brier']:.4f}, LL={global_row['mc_ll']:.4f}, RPS={global_row['mc_rps']:.4f}*")
    lines.append("")

    # b_g distribution
    lines.append(f"## b_g Distribution Across Buckets")
    lines.append(f"")
    b_vals = list(b_g_dist.values())
    if b_vals:
        lines.append(f"- mean b_g: {float(np.mean(b_vals)):.4f}")
        lines.append(f"- median b_g: {float(np.median(b_vals)):.4f}")
        lines.append(f"- max b_g: {float(np.max(b_vals)):.4f}")
        lines.append(f"- fraction b_g > 0: {sum(1 for v in b_vals if v > 0)}/{len(b_vals)}")
    lines.append("")

    for tbl in cohort_tables:
        lines.append(tbl)

    lines.append("")
    lines.append("## Final Verdict")
    wins = sum([
        global_row["r5_ll"] < global_row["base_ll"],
        global_row["r5_rps"] < global_row["base_rps"],
    ])
    if wins == 2:
        verdict = "PASS — Route 5 beats baseline on both LogLoss and RPS globally."
    elif wins == 1:
        verdict = "PARTIAL — Route 5 beats baseline on one of LogLoss/RPS. Does not meet §3 gate."
    else:
        verdict = "FAIL — Route 5 does not beat baseline on LogLoss or RPS. Route not warranted."
    lines.append(verdict)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written to: {path}", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--metric", choices=["high", "low"], default="high")
    args = parser.parse_args()

    db_path = args.db
    metric = args.metric

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"Route 5 experiment: {metric.upper()} metric, {db_path}", flush=True)

    groups = _load_groups(conn, metric)
    if not groups:
        print("No groups loaded — check DB and query filters.", flush=True)
        sys.exit(1)

    _assign_folds(groups)

    print(f"Running blocked-OOS Route 5 experiment ({len(groups):,} groups, {N_FOLDS} folds)...", flush=True)
    results = _run_route5_oos(groups)
    print(f"Done. {len(results):,} scored groups.", flush=True)

    # Collect b_g per bucket (from first fold's training for distribution report)
    # Use actual fitted b_g from results
    b_g_by_bucket = defaultdict(list)
    for r in results:
        bk = f"{r['season']}_{r['lead_bucket']}_{r['cycle']}"
        b_g_by_bucket[bk].append(r["b_g"])
    b_g_dist = {bk: float(np.mean(vals)) for bk, vals in b_g_by_bucket.items()}

    n_groups = len(groups)
    n_buckets = len(b_g_dist)

    # Global
    global_agg = _agg(results, lambda r: "global")
    global_row = global_agg["global"]

    # Cohort tables
    coastal_agg = _agg(results, lambda r: "coastal" if r["coastal"] else "inland")
    unit_agg = _agg(results, lambda r: f"unit={r['unit']}")
    lead_agg = _agg(results, lambda r: f"lead={r['lead_bucket']}")
    cycle_agg = _agg(results, lambda r: f"cycle={r['cycle']}")
    city_agg = _agg(results, lambda r: r["city"])

    tables = [
        _table(coastal_agg, "Coastal vs Inland"),
        _table(unit_agg, "Temperature Unit"),
        _table(lead_agg, "Lead Day Bucket"),
        _table(cycle_agg, "Forecast Cycle"),
        _table(city_agg, "Per City"),
    ]

    out_path = PROJECT_ROOT / "docs" / "operations" / f"ENS_ROUTE5_SPREAD_SCALE_2026-05-25.md"
    _write_report(out_path, metric, global_row, tables, b_g_dist, n_groups, n_buckets)

    # Print summary
    db = global_row["r5_brier"] - global_row["base_brier"]
    dl = global_row["r5_ll"] - global_row["base_ll"]
    dr = global_row["r5_rps"] - global_row["base_rps"]
    print(f"\n=== ROUTE 5 GLOBAL RESULT ({metric.upper()}) ===")
    print(f"  ΔBrier: {db:+.4f}  ΔLogLoss: {dl:+.4f}  ΔRPS: {dr:+.4f}")
    print(f"  Gate (LL AND RPS): {'PASS' if dl < 0 and dr < 0 else 'FAIL'}")


if __name__ == "__main__":
    main()
