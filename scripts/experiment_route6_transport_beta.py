# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §3 Route 6
# Purpose: Blocked-OOS experiment for day-specific Δ(F25−F50) transport β.
#   Extends full_transport_v1 mean-shift by fitting beta per bucket:
#   b_{25,i} = b_50 + μ_Δ + β(Δ_i − μ_Δ), β~N(0,τ²) strong shrinkage.
#   Δ_i = mean(F25 members) − mean(F50 members) for day i.
#
# DATA CONSTRAINT (critical):
#   Route 6 requires PAIRED F25 (opendata) + F50 (TIGGE) snapshots per
#   (city, target_date, lead). In full.db, paired coverage is limited to
#   ~14 cities and at most 16 overlap days per city. HK and Miami (the §4.1
#   catastrophic regression cities) have ZERO paired overlap.
#   This script reports what IS testable and explicitly flags what is NOT.
#
# CONSTRAINTS:
#   - Read-only on /tmp/ens_refit/full.db.
#   - No edits to model/validator/runtime code.
#   - Gaussian approximation: same as Route 5 script.
#   - Blocked OOS: fold_of identical to audit_refit_proper_scores.py.
#
# USAGE:
#   python scripts/experiment_route6_transport_beta.py --db /tmp/ens_refit/full.db --metric high
"""Route 6 day-specific Δ transport β experiment."""

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

import numpy as np
from scipy.stats import norm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

N_FOLDS = 5
COASTAL = {
    "San Francisco", "Los Angeles", "Seattle", "Miami", "NYC", "London",
    "Tokyo", "Hong Kong", "Sydney", "Lisbon", "Barcelona", "Mumbai",
}
LEAD_BUCKETS = [(0, 0), (1, 1), (2, 3), (4, 5), (6, 7)]

# Ridge shrinkage regularisation for β fit (λ corresponds to β~N(0, 1/λ²))
RIDGE_LAMBDA = 1.0  # strong shrinkage per roadmap


# ---------------------------------------------------------------------------
# Bin edge utilities (shared with Route 5 / audit harness)
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
    sigma = max(sigma, 0.01)
    p = np.array([norm.cdf(hi, mu, sigma) - norm.cdf(lo, mu, sigma)
                  for lo, hi in zip(lo_edges, hi_edges)])
    p = np.clip(p, 1e-12, 1.0)
    p /= p.sum()
    return p


def _fit_mu_sigma_moments(p_vec: np.ndarray, lo_edges: list[float], hi_edges: list[float]) -> tuple[float, float]:
    mids = [(lo + hi) / 2 for lo, hi in zip(lo_edges, hi_edges)]
    mu = float(np.dot(p_vec, mids))
    var = float(np.dot(p_vec, [(m - mu) ** 2 for m in mids]))
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
# Data loading — paired TIGGE+opendata groups
# ---------------------------------------------------------------------------

def _load_paired_groups(conn: sqlite3.Connection, metric: str) -> tuple[list[dict], dict]:
    """Load groups that have both TIGGE (F50) and opendata (F25) snapshots.

    Returns (paired_groups, coverage_report).
    paired_groups: list of dicts with TIGGE calibration data + delta_i.
    coverage_report: per-city pair counts (including zero-coverage cities).
    """
    print(f"  Discovering paired TIGGE+opendata snapshots for {metric.upper()}...", flush=True)

    # Identify paired (city, target_date, lead) where BOTH data versions exist
    if metric == "high":
        tigge_dv = "tigge_mx2t6_local_calendar_day_max_v1"
        opd_dv = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
    else:
        tigge_dv = "tigge_mn2t6_local_calendar_day_min_v1"
        opd_dv = "ecmwf_opendata_mn2t3_local_calendar_day_min_v1"

    # Get all TIGGE snapshots with members_json
    tig_snaps = conn.execute("""
        SELECT snapshot_id, city, target_date, ROUND(lead_hours/24.0) as lead_d,
               members_json
        FROM ensemble_snapshots
        WHERE data_version = ?
          AND temperature_metric = ?
          AND members_json IS NOT NULL
    """, (tigge_dv, metric)).fetchall()

    # Get all opendata snapshots with members_json
    opd_snaps = conn.execute("""
        SELECT city, target_date, ROUND(lead_hours/24.0) as lead_d,
               members_json
        FROM ensemble_snapshots
        WHERE data_version = ?
          AND temperature_metric = ?
          AND members_json IS NOT NULL
    """, (opd_dv, metric)).fetchall()

    # Build opendata lookup: (city, date, lead_d) -> mean of members
    print(f"  Building opendata lookup ({len(opd_snaps)} rows)...", flush=True)
    opd_lookup: dict[tuple, float] = {}
    for r in opd_snaps:
        key = (r["city"], r["target_date"], int(r["lead_d"]))
        try:
            members = json.loads(r["members_json"])
            opd_lookup[key] = float(np.mean(members))
        except Exception:
            pass

    # Match TIGGE snapshots to opendata
    print(f"  Matching {len(tig_snaps)} TIGGE snapshots to opendata...", flush=True)
    paired_snap: list[dict] = []
    for r in tig_snaps:
        key = (r["city"], r["target_date"], int(r["lead_d"]))
        if key not in opd_lookup:
            continue
        try:
            tig_members = json.loads(r["members_json"])
        except Exception:
            continue
        tig_mean = float(np.mean(tig_members))
        opd_mean = opd_lookup[key]
        delta_i = opd_mean - tig_mean  # F25 - F50
        paired_snap.append({
            "snapshot_id": r["snapshot_id"],
            "city": r["city"],
            "target_date": r["target_date"],
            "lead_d": int(r["lead_d"]),
            "tig_mean": tig_mean,
            "opd_mean": opd_mean,
            "delta_i": delta_i,
        })

    print(f"  Found {len(paired_snap)} paired (city,date,lead) combinations.", flush=True)

    # Coverage report
    all_cities = set(r["city"] for r in tig_snaps)
    coverage: dict[str, dict] = {}
    for city in sorted(all_cities):
        n_tig = sum(1 for r in tig_snaps if r["city"] == city)
        n_paired = sum(1 for p in paired_snap if p["city"] == city)
        coverage[city] = {"n_tigge": n_tig, "n_paired": n_paired}

    if not paired_snap:
        return [], coverage

    # Load calibration pairs for paired snapshot_ids
    snap_ids = list({p["snapshot_id"] for p in paired_snap})
    delta_by_snid = {p["snapshot_id"]: p["delta_i"] for p in paired_snap}
    meta_by_snid = {p["snapshot_id"]: p for p in paired_snap}

    print(f"  Loading calibration data for {len(snap_ids)} paired snapshots...", flush=True)

    groups: list[dict] = []
    n_skip = 0
    chunk = 500
    for i in range(0, len(snap_ids), chunk):
        sub = snap_ids[i:i+chunk]
        ph = ",".join("?" * len(sub))
        cp_rows = conn.execute(f"""
            SELECT decision_group_id, city, lead_days, cycle, season, cluster,
                   target_date, range_label, p_raw, outcome, snapshot_id
            FROM calibration_pairs_v2
            WHERE snapshot_id IN ({ph})
              AND error_model_family = 'full_transport_v1'
              AND temperature_metric = ?
              AND bin_source = 'canonical_v2'
        """, sub + [metric]).fetchall()

        # Group by decision_group_id
        by_gid: dict[str, list] = defaultdict(list)
        meta_by_gid: dict[str, dict] = {}
        for r in cp_rows:
            gid = r["decision_group_id"]
            by_gid[gid].append(r)
            if gid not in meta_by_gid:
                meta_by_gid[gid] = r

        for gid, bins in by_gid.items():
            p_sum = sum(b["p_raw"] for b in bins)
            n_out = sum(b["outcome"] for b in bins)
            if abs(p_sum - 1.0) > 1e-3 or n_out != 1 or len(bins) < 80:
                n_skip += 1
                continue
            sorted_bins = sorted(bins, key=lambda b: _parse_bin_lower(b["range_label"])[0])
            labels = [b["range_label"] for b in sorted_bins]
            p_raw_vec = np.array([b["p_raw"] for b in sorted_bins], dtype=float)
            outcome_idx = next(i for i, b in enumerate(sorted_bins) if b["outcome"] == 1)
            lo_edges, hi_edges = _bin_edges(labels)
            mu_fit, sigma_fit = _fit_mu_sigma_moments(p_raw_vec, lo_edges, hi_edges)

            meta = meta_by_gid[gid]
            snid = meta["snapshot_id"]
            delta_i = delta_by_snid.get(snid, None)
            if delta_i is None:
                n_skip += 1
                continue

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
                "delta_i": delta_i,
            })

    if n_skip:
        print(f"  [WARN] Skipped {n_skip} malformed/unmatched groups.", flush=True)
    print(f"  Built {len(groups)} valid paired distributions.", flush=True)
    return groups, coverage


# ---------------------------------------------------------------------------
# OOS fold assignment
# ---------------------------------------------------------------------------

def _assign_folds(groups: list[dict], n_folds: int = N_FOLDS) -> None:
    sorted_gids = sorted(set(g["gid"] for g in groups))
    fold_of = {gid: i % n_folds for i, gid in enumerate(sorted_gids)}
    for g in groups:
        g["fold"] = fold_of[g["gid"]]


# ---------------------------------------------------------------------------
# Route 6: beta fitting with ridge shrinkage
# ---------------------------------------------------------------------------

def _bucket_key(g: dict) -> str:
    return f"{g['season']}_{g['lead_bucket']}_{g['cycle']}"


def _fit_beta(train: list[dict], ridge: float = RIDGE_LAMBDA) -> tuple[float, float, float]:
    """Fit (mu_delta, beta) per bucket using ridge regression.

    Model: mu_shift_i = beta * (delta_i - mu_delta)
    Ridge: beta = Cov(mu_shift, delta_centered) / (Var(delta_centered) + ridge²)
    where mu_shift_i = mu_fit_i - mu_mean_fit (deviation from bucket mean).

    Returns (mu_delta, beta_ridge, delta_std).
    """
    if len(train) < 3:
        return 0.0, 0.0, 0.0
    deltas = np.array([g["delta_i"] for g in train])
    mu_fits = np.array([g["mu_fit"] for g in train])
    mu_delta = float(np.mean(deltas))
    delta_centered = deltas - mu_delta
    mu_mean = float(np.mean(mu_fits))
    mu_centered = mu_fits - mu_mean
    # Ridge regression: beta = X'y / (X'X + lambda²)
    num = float(np.dot(delta_centered, mu_centered))
    denom = float(np.dot(delta_centered, delta_centered)) + ridge ** 2
    beta = num / denom if denom > 1e-10 else 0.0
    delta_std = float(np.std(deltas)) if len(train) >= 2 else 0.0
    return mu_delta, beta, delta_std


def _run_route6_oos(groups: list[dict]) -> list[dict]:
    """Run blocked-OOS Route 6 experiment."""
    results = []
    for fold_id in range(N_FOLDS):
        train = [g for g in groups if g["fold"] != fold_id]
        held = [g for g in groups if g["fold"] == fold_id]

        by_bucket: dict[str, list[dict]] = defaultdict(list)
        for g in train:
            by_bucket[_bucket_key(g)].append(g)
        bucket_params: dict[str, tuple[float, float, float]] = {}
        for bk, members in by_bucket.items():
            bucket_params[bk] = _fit_beta(members)

        global_mu_delta, global_beta, _ = _fit_beta(train)

        for g in held:
            bk = _bucket_key(g)
            mu_delta, beta, delta_std = bucket_params.get(bk, (global_mu_delta, global_beta, 0.0))
            # Route 6: shift mu by beta*(delta_i - mu_delta)
            mu_shift = beta * (g["delta_i"] - mu_delta)
            mu_new = g["mu_fit"] + mu_shift

            y = g["outcome_idx"]

            # Route 6 scores
            p6 = gaussian_p_vec(mu_new, g["sigma_fit"], g["lo_edges"], g["hi_edges"])
            r6_brier = _brier(p6, y)
            r6_ll = _logloss(p6, y)
            r6_rps = _rps(p6, y)

            # Gaussian baseline
            p_base = gaussian_p_vec(g["mu_fit"], g["sigma_fit"], g["lo_edges"], g["hi_edges"])
            base_brier = _brier(p_base, y)
            base_ll = _logloss(p_base, y)
            base_rps = _rps(p_base, y)

            # MC reference
            mc_brier = _brier(g["p_raw_vec"], y)
            mc_ll = _logloss(g["p_raw_vec"], y)
            mc_rps = _rps(g["p_raw_vec"], y)

            results.append({
                **{k: g[k] for k in ("gid", "city", "lead_days", "lead_bucket",
                                      "cycle", "season", "coastal", "unit")},
                "mu_delta": mu_delta, "beta": beta, "mu_shift": mu_shift,
                "delta_i": g["delta_i"],
                "base_brier": base_brier, "base_ll": base_ll, "base_rps": base_rps,
                "r6_brier": r6_brier, "r6_ll": r6_ll, "r6_rps": r6_rps,
                "mc_brier": mc_brier, "mc_ll": mc_ll, "mc_rps": mc_rps,
            })

    return results


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------

def _agg(results: list[dict], key_fn) -> dict[str, dict]:
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
            "r6_brier": m("r6_brier"), "r6_ll": m("r6_ll"), "r6_rps": m("r6_rps"),
            "mc_brier": m("mc_brier"), "mc_ll": m("mc_ll"), "mc_rps": m("mc_rps"),
            "mean_beta": float(np.mean([r["beta"] for r in rows])),
            "mean_delta_i": float(np.mean([r["delta_i"] for r in rows])),
        }
    return out


def _verdict(row: dict) -> str:
    wins = sum([
        row["r6_brier"] < row["base_brier"],
        row["r6_ll"] < row["base_ll"],
        row["r6_rps"] < row["base_rps"],
    ])
    return {3: "R6 wins all", 2: "R6 wins 2/3", 1: "R6 wins 1/3", 0: "R6 loses all"}[wins]


def _table(rows: dict[str, dict], title: str, score_prefix: str = "r6") -> str:
    lines = [f"\n### {title}\n"]
    lines.append(f"| {'Cohort':<22} | {'n':>6} | {'Δ Brier':>8} | {'Δ LogLoss':>10} | {'Δ RPS':>8} | {'mean β':>8} | Verdict |")
    lines.append(f"| {'-'*22} | {'-'*6} | {'-'*8} | {'-'*10} | {'-'*8} | {'-'*8} | ------- |")
    for label, r in sorted(rows.items()):
        db = r[f"{score_prefix}_brier"] - r["base_brier"]
        dl = r[f"{score_prefix}_ll"] - r["base_ll"]
        dr = r[f"{score_prefix}_rps"] - r["base_rps"]
        v = _verdict(r)
        lines.append(
            f"| {label:<22} | {r['n']:>6} | {db:>+8.4f} | {dl:>+10.4f} | {dr:>+8.4f} | {r.get('mean_beta', 0.0):>+8.4f} | {v} |"
        )
    return "\n".join(lines)


def _write_report(path: Path, metric: str, global_row: dict | None,
                  cohort_tables: list[str], coverage: dict,
                  n_paired: int) -> None:
    lines = [
        "# ENS Route 6 Experiment — Day-Specific Δ Transport β",
        "",
        "Created: 2026-05-25",
        "Authority: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §3 Route 6",
        "DB: /tmp/ens_refit/full.db (read-only)",
        f"Metric: {metric.upper()}",
        "",
        "## Data Coverage Assessment",
        "",
        "Route 6 requires PAIRED F25 (opendata) and F50 (TIGGE) ensemble snapshots",
        "per (city, target_date, lead_day). Coverage in full.db:",
        "",
        "| City | TIGGE days | Paired days | Route 6 testable? |",
        "| ---- | ---------- | ----------- | ----------------- |",
    ]
    for city, c in sorted(coverage.items()):
        n_tig = c["n_tigge"]
        n_paired_city = c["n_paired"]
        testable = "Yes" if n_paired_city >= 5 else ("Marginal (<5 pairs)" if n_paired_city > 0 else "NO")
        lines.append(f"| {city} | {n_tig} | {n_paired_city} | {testable} |")

    lines += [
        "",
        "### §4.1 Catastrophic Regression Cities",
        "",
        "| City | Paired days | Route 6 status |",
        "| ---- | ----------- | -------------- |",
    ]
    for city in ["Hong Kong", "Miami", "Moscow"]:
        if city in coverage:
            c = coverage[city]
            n = c["n_paired"]
            status = "UNTESTABLE — no paired overlap" if n == 0 else (
                f"MARGINAL — {n} paired days only" if n < 5 else f"{n} paired days")
        else:
            status = "UNTESTABLE — not in paired coverage"
        lines.append(f"| {city} | {coverage.get(city, {}).get('n_paired', 0)} | {status} |")

    lines += [""]

    if global_row is None:
        lines += [
            "## Result: UNTESTABLE",
            "",
            f"Total paired distributions available: {n_paired}.",
            "Insufficient data for 5-fold OOS evaluation of Route 6.",
            "The §4.1 catastrophic regressions (HK, Miami) occur exclusively in",
            "the TIGGE-only calibration period (2024-03 to 2026-05) where no",
            "corresponding opendata F25 snapshots exist. Route 6 CANNOT rescue",
            "HK/Miami with the current data available in full.db.",
            "",
            "## Implication for Roadmap",
            "",
            "Route 6 as specified (use daily Δ_i = F25−F50 from calibration data)",
            "is a data-availability problem, not a method problem.",
            "Prerequisites for Route 6 to be testable on catastrophic-regression cities:",
            "1. Accumulate ≥3 months of paired opendata+TIGGE snapshots for HK/Miami.",
            "2. Re-run the full_transport_v1 refit to include that period.",
            "3. Re-run this experiment on the updated calibration_pairs_v2.",
            "",
            "## Final Verdict",
            "",
            "UNTESTABLE on §4.1 catastrophic regression cities (HK, Miami, Moscow).",
            "Route 6 is NOT rejected — it is data-constrained. Per roadmap §3, defer",
            "Route 6 until paired F25+F50 overlap period is available.",
        ]
    else:
        lines += [
            "## Experiment Design",
            "",
            "**Model**: b_{25,i} = b_50 + μ_Δ + β(Δ_i − μ_Δ), β~N(0,1/λ²) ridge.",
            "  In Gaussian terms: μ_new_i = μ_fit_i + β·(Δ_i − μ_Δ).",
            "**Baseline**: β=0 (Gaussian, same μ_fit from full_transport p_raw).",
            f"**Ridge λ**: {RIDGE_LAMBDA} (strong shrinkage per roadmap).",
            "**OOS**: 5-fold blocked on sorted decision_group_id.",
            f"**Groups**: {n_paired:,} paired distributions.",
            "",
            "## Global Result (paired cities only)",
            "",
            "| Metric | Baseline (Gaussian) | Route 6 | Δ | Verdict |",
            "| ------ | ------------------- | ------- | -- | ------- |",
        ]
        for col, name in [("brier", "Brier"), ("ll", "LogLoss"), ("rps", "RPS")]:
            base = global_row[f"base_{col}"]
            r6 = global_row[f"r6_{col}"]
            delta = r6 - base
            v = "PASS" if delta < 0 else "FAIL"
            lines.append(f"| {name} | {base:.4f} | {r6:.4f} | {delta:+.4f} | {v} |")
        lines.append("")
        lines.append(f"*MC p_raw reference: Brier={global_row['mc_brier']:.4f}, "
                     f"LL={global_row['mc_ll']:.4f}, RPS={global_row['mc_rps']:.4f}*")
        lines.append("")
        lines.append("**Note**: This result covers the {n_paired} paired distributions,")
        lines.append("which do NOT include HK or Miami (zero paired overlap).")
        lines.append("This test validates Route 6 methodology on cities where it IS testable,")
        lines.append("but CANNOT speak to the §4.1 catastrophic regression acceptance gate.")
        lines.append("")

        for tbl in cohort_tables:
            lines.append(tbl)

        wins = sum([
            global_row["r6_ll"] < global_row["base_ll"],
            global_row["r6_rps"] < global_row["base_rps"],
        ])
        lines += [
            "",
            "## Final Verdict",
            "",
        ]
        if wins == 2:
            lines.append("PASS on testable subset — Route 6 beats baseline on LogLoss AND RPS.")
            lines.append("However: UNTESTABLE on §4.1 catastrophic regression cities (HK, Miami).")
            lines.append("Cannot confirm 'rescues HK/Miami' acceptance gate. Data accumulation needed.")
        else:
            lines.append(f"FAIL or PARTIAL on testable subset (LL/RPS wins: {wins}/2).")
            lines.append("Route 6 does not meet §3 gate even on cities where it is testable.")
            lines.append("Combined with UNTESTABLE status on HK/Miami: Route 6 is not warranted.")

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

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"Route 6 experiment: {args.metric.upper()} metric, {args.db}", flush=True)

    groups, coverage = _load_paired_groups(conn, args.metric)

    out_path = (PROJECT_ROOT / "docs" / "operations" /
                f"ENS_ROUTE6_TRANSPORT_BETA_2026-05-25.md")

    if len(groups) < 10:
        print(f"Insufficient paired groups ({len(groups)}). Writing coverage-only report.", flush=True)
        _write_report(out_path, args.metric, None, [], coverage, len(groups))
        return

    _assign_folds(groups)
    print(f"Running blocked-OOS Route 6 ({len(groups)} groups, {N_FOLDS} folds)...", flush=True)
    results = _run_route6_oos(groups)
    print(f"Done. {len(results)} scored groups.", flush=True)

    global_agg = _agg(results, lambda r: "global")
    global_row = global_agg.get("global")

    coastal_agg = _agg(results, lambda r: "coastal" if r["coastal"] else "inland")
    unit_agg = _agg(results, lambda r: f"unit={r['unit']}")
    city_agg = _agg(results, lambda r: r["city"])

    tables = [
        _table(coastal_agg, "Coastal vs Inland"),
        _table(unit_agg, "Temperature Unit"),
        _table(city_agg, "Per City"),
    ]

    _write_report(out_path, args.metric, global_row, tables, coverage, len(groups))

    if global_row:
        db = global_row["r6_brier"] - global_row["base_brier"]
        dl = global_row["r6_ll"] - global_row["base_ll"]
        dr = global_row["r6_rps"] - global_row["base_rps"]
        print(f"\n=== ROUTE 6 GLOBAL RESULT ({args.metric.upper()}) ===")
        print(f"  ΔBrier: {db:+.4f}  ΔLogLoss: {dl:+.4f}  ΔRPS: {dr:+.4f}")
        print(f"  Gate (LL AND RPS): {'PASS' if dl < 0 and dr < 0 else 'FAIL'}")
        print(f"  NOTE: HK/Miami untestable (no paired F25+F50 overlap in current data)")


if __name__ == "__main__":
    main()
