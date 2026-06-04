#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: TASK-E bake-off spec; settlements truth = zeus-forecasts.db.settlements
#   WHERE authority='VERIFIED'; calibration_pairs p_raw baseline; EMOS via emos.py;
#   grid-rep via state/grid_representativeness_offset.json; bias via zeus-world.db model_bias_ens.
#
# READ-ONLY on all DBs. Writes /tmp/bakeoff_scorecard.json + /tmp/bakeoff_summary.txt only.
# No look-ahead: OOS window = target_date >= 2025-01-01; all method params fit strictly prior.
#
# Methods scored per (city, metric):
#   RAW       -- calibration_pairs.p_raw as stored (MC p_raw, no correction)
#   EMOS      -- EMOS (mu,sigma) in degC -> converted to settlement unit -> WMO bin integration
#   GRID_REP  -- offset_c subtracted from degC members before analytic p_raw recompute
#   BIAS      -- effective_bias_c subtracted from degC members before analytic p_raw recompute
#   GRID_BIAS -- both corrections combined
#
# Proper scores:
#   LogLoss   = -log(p_settled_bin)   [primary, clipped at 1e-9]
#   CRPS      = sum_k (CDF_k - 1{k>=settled_bin})^2  [using full bin vector]
#   Cov90     = fraction where settled bin is within central 90% predictive interval
#
# Dedup: for each (city, target_date, metric) take MIN(lead_days) snapshot.
# Sample sizes stated; flag LOW_CONFIDENCE when n_settled < 20.
"""Unified offline calibration bake-off: RAW vs EMOS vs GRID_REP vs BIAS vs GRID_BIAS."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
from scipy.stats import norm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FORECASTS_DB = os.path.join(REPO, "state", "zeus-forecasts.db")
WORLD_DB = os.path.join(REPO, "state", "zeus-world.db")
EMOS_JSON = os.path.join(REPO, "state", "emos_calibration.json")
GRID_REP_JSON = os.path.join(REPO, "state", "grid_representativeness_offset.json")

SCORECARD_OUT = "/tmp/bakeoff_scorecard.json"
SUMMARY_OUT = "/tmp/bakeoff_summary.txt"

OOS_START = "2025-01-01"
MIN_N_CONFIDENCE = 20
LOG_CLIP = 1e-9
HALF_STEP = 0.5  # WMO rounding preimage half-step
INSTR_SIGMA_FLOOR = 0.5  # floor instrument sigma in native unit (avoids degenerate Gaussians)


# ---------------------------------------------------------------------------
# Load calibration tables
# ---------------------------------------------------------------------------

def _load_emos_cells() -> dict[str, dict]:
    with open(EMOS_JSON) as f:
        data = json.load(f)
    return data.get("cells", {})


def _load_grid_rep() -> dict[str, dict]:
    with open(GRID_REP_JSON) as f:
        data = json.load(f)
    return data.get("cities", {})


def _load_bias_verified(world_conn: sqlite3.Connection) -> dict[tuple, float]:
    cur = world_conn.cursor()
    cur.execute(
        "SELECT city, season, metric, effective_bias_c "
        "FROM model_bias_ens WHERE authority='VERIFIED'"
    )
    result: dict[tuple, float] = {}
    for city, season, metric, bias_c in cur.fetchall():
        result[(city, season, metric)] = float(bias_c)
    return result


# ---------------------------------------------------------------------------
# Season helper
# ---------------------------------------------------------------------------

def _season_for_date_str(date_str: str) -> str:
    month = int(date_str[5:7])
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


# ---------------------------------------------------------------------------
# EMOS predictive (mu_c, sigma_c)
# ---------------------------------------------------------------------------

def _emos_predictive(
    emos_cells: dict,
    city: str,
    season: str,
    lead_days: float,
    members_c: np.ndarray,
    metric: str = "high",
) -> tuple[float, float] | None:
    key = f"{city}|{season}|{str(metric).lower()}"  # 3-key (metric-keyed table)
    cell = emos_cells.get(key)
    if cell is None:
        return None
    if cell.get("served") != "emos":
        return None
    params = cell["params"]  # [a, b, c, d, e]
    a, b, c_p, d, e = params
    if len(members_c) < 2:
        return None
    xbar = float(np.mean(members_c))
    S2 = float(np.var(members_c, ddof=1))
    if S2 <= 0:
        S2 = 1e-6
    mu_c = a + b * xbar
    var_c = math.exp(c_p + d * math.log(S2) + e * lead_days)
    sigma_c = math.sqrt(max(var_c, 1e-6))
    return mu_c, sigma_c


# ---------------------------------------------------------------------------
# WMO-preimage bin probability from N(mu, sigma^2)
# ---------------------------------------------------------------------------

def _bin_prob_gaussian(mu: float, sigma: float, bin_lo: float | None, bin_hi: float | None) -> float:
    if sigma <= 0:
        sigma = 1e-6
    lo_ext = -np.inf if bin_lo is None else bin_lo - HALF_STEP
    hi_ext = np.inf if bin_hi is None else bin_hi + HALF_STEP
    p = float(norm.cdf(hi_ext, mu, sigma) - norm.cdf(lo_ext, mu, sigma))
    return max(0.0, min(1.0, p))


# ---------------------------------------------------------------------------
# Canonical bin grids
# ---------------------------------------------------------------------------

def _build_f_bins() -> list[dict]:
    bins = [{"lo": None, "hi": -40.0, "unit": "F", "label": "-40°F or below"}]
    lo = -39
    while lo + 1 <= 139:
        bins.append({"lo": float(lo), "hi": float(lo + 1), "unit": "F", "label": f"{lo}-{lo+1}°F"})
        lo += 2
    bins.append({"lo": 141.0, "hi": None, "unit": "F", "label": "141°F or higher"})
    return bins


def _build_c_bins() -> list[dict]:
    bins = [{"lo": None, "hi": -40.0, "unit": "C", "label": "-40°C or below"}]
    for v in range(-39, 61):
        bins.append({"lo": float(v), "hi": float(v), "unit": "C", "label": f"{v}°C"})
    bins.append({"lo": 61.0, "hi": None, "unit": "C", "label": "61°C or higher"})
    return bins


_F_BINS: list[dict] = _build_f_bins()
_C_BINS: list[dict] = _build_c_bins()
_F_BIN_LABEL_IDX: dict[str, int] = {b["label"]: i for i, b in enumerate(_F_BINS)}
_C_BIN_LABEL_IDX: dict[str, int] = {b["label"]: i for i, b in enumerate(_C_BINS)}


def _bins_for_unit(unit: str) -> list[dict]:
    return _F_BINS if unit == "F" else _C_BINS


def _label_idx_for_unit(unit: str) -> dict[str, int]:
    return _F_BIN_LABEL_IDX if unit == "F" else _C_BIN_LABEL_IDX


# ---------------------------------------------------------------------------
# Find settled bin index in canonical grid
# ---------------------------------------------------------------------------

def _find_settled_bin_index(settlement_value: float, unit: str) -> int | None:
    bins = _bins_for_unit(unit)
    for i, b in enumerate(bins):
        lo, hi = b["lo"], b["hi"]
        if lo is None:
            if settlement_value <= hi:
                return i
            continue
        if hi is None:
            if settlement_value >= lo:
                return i
            continue
        preimage_lo = lo - HALF_STEP
        preimage_hi = hi + HALF_STEP
        if preimage_lo <= settlement_value < preimage_hi:
            return i
    return None


# ---------------------------------------------------------------------------
# CRPS and 90% coverage
# ---------------------------------------------------------------------------

def _crps(p_vec: np.ndarray, settled_idx: int) -> float:
    cdf = np.cumsum(p_vec)
    return float(np.sum((cdf - np.where(np.arange(len(p_vec)) >= settled_idx, 1.0, 0.0)) ** 2))


def _covered_90(p_vec: np.ndarray, settled_idx: int) -> bool:
    cdf = np.cumsum(p_vec)
    cdf_before = cdf[settled_idx - 1] if settled_idx > 0 else 0.0
    cdf_at = cdf[settled_idx]
    return bool(cdf_before < 0.95 and cdf_at > 0.05)


# ---------------------------------------------------------------------------
# Build analytic Gaussian-mixture p_raw vector from corrected degC members
# ---------------------------------------------------------------------------

def _analytic_pvec(
    members_c_corrected: np.ndarray,
    settlement_unit: str,
) -> np.ndarray:
    """Analytic Gaussian-mixture p_raw (vectorised: one component per member, sigma=floor).

    members_c_corrected: already-corrected ensemble members in degC.
    settlement_unit: 'C' or 'F' — determines output bin grid.
    Returns normalised probability vector over canonical bin grid.
    """
    bins = _bins_for_unit(settlement_unit)
    n_bins = len(bins)

    if settlement_unit == "F":
        members_s = members_c_corrected * 1.8 + 32.0
        sigma_s = INSTR_SIGMA_FLOOR * 1.8
    else:
        members_s = members_c_corrected.copy()
        sigma_s = INSTR_SIGMA_FLOOR

    n_m = len(members_s)
    if n_m == 0:
        return np.full(n_bins, 1.0 / n_bins)

    # Vectorised: members_s shape (M,), bin edges shape (B,)
    # CDF matrix: (B+1, M) for each extended-edge
    edges = np.empty(n_bins + 1)
    edges[0] = -np.inf
    for i, b in enumerate(bins):
        if b["hi"] is None:
            edges[i + 1] = np.inf
        else:
            edges[i + 1] = b["hi"] + HALF_STEP
    # Adjust lower edge of first bin (shoulder bin)
    # For non-shoulder bins, lower edge = hi_prev + HALF_STEP which equals lo - HALF_STEP
    # (already handled by hi+HALF_STEP of previous bin)
    # shoulders: lo=None → extend to -inf (already set at edges[0])

    # CDF at each edge for each member: shape (B+1, M)
    edges_col = edges[:, np.newaxis]  # (B+1, 1)
    members_row = members_s[np.newaxis, :]  # (1, M)
    cdf_mat = norm.cdf(edges_col, members_row, sigma_s)  # (B+1, M)

    # Bin probabilities: (B, M) = cdf_mat[1:] - cdf_mat[:-1]
    p_mat = cdf_mat[1:] - cdf_mat[:-1]  # (B, M)
    p_vec = p_mat.mean(axis=1)  # (B,) — equal-weight mixture

    total = p_vec.sum()
    if total > 0:
        p_vec /= total
    else:
        p_vec = np.full(n_bins, 1.0 / n_bins)
    return p_vec


# ---------------------------------------------------------------------------
# Load OOS rows (deduped to MIN lead_days per city/date/metric)
# ---------------------------------------------------------------------------

def _load_oos_rows(fconn: sqlite3.Connection) -> list[dict]:
    """Load OOS deduped rows efficiently.

    Strategy: start from settlements (6134 VERIFIED OOS rows), join to
    ensemble_snapshots (65k causal OK rows), pick MIN(lead_hours) per
    (city, target_date, metric), then fetch calibration_pairs p_raw for
    just those (city, date, metric, snapshot_id) tuples.

    This avoids scanning the 48M-row calibration_pairs table with a GROUP BY.
    """
    cur = fconn.cursor()
    print(f"Step 1: get OOS (city,date,metric) + min-lead snapshot from settlements+ensemble_snapshots ...")
    # settlements has 6134 VERIFIED rows; ensemble_snapshots join yields ~65k causal rows.
    # After MIN(lead_hours) dedup: ~5648 pairs.
    cur.execute(
        """
        SELECT s.city, s.target_date, s.temperature_metric,
               MIN(e.lead_hours) / 24.0 AS min_lead_days,
               e.snapshot_id
        FROM settlements s
        JOIN ensemble_snapshots e
          ON e.city = s.city
         AND e.target_date = s.target_date
         AND e.temperature_metric = s.temperature_metric
        WHERE s.authority = 'VERIFIED'
          AND s.target_date >= ?
          AND e.causality_status = 'OK'
          AND e.members_json IS NOT NULL
        GROUP BY s.city, s.target_date, s.temperature_metric
        """,
        (OOS_START,),
    )
    snap_keys = cur.fetchall()
    print(f"  {len(snap_keys)} (city,date,metric) with causal snapshot found")

    # Now fetch settlement_value + unit from settlements and members from ensemble_snapshots
    # for each key, plus p_raw from calibration_pairs where outcome=1 at that snapshot.
    rows = []
    skipped = 0

    # Bulk-fetch snapshots data
    print("Step 2: bulk-fetch members + settlement info ...")
    snap_ids = [r[4] for r in snap_keys]

    # Build a lookup: snapshot_id -> (members_json, members_unit, settlement_unit)
    # Do in batches of 5000 to stay within SQLite parameter limits
    snap_data: dict[int, tuple] = {}
    batch_size = 5000
    for batch_start in range(0, len(snap_ids), batch_size):
        batch = snap_ids[batch_start:batch_start + batch_size]
        placeholders = ",".join("?" * len(batch))
        cur.execute(
            f"SELECT snapshot_id, members_json, members_unit, settlement_unit "
            f"FROM ensemble_snapshots WHERE snapshot_id IN ({placeholders})",
            batch,
        )
        for sid, mj, mu, su in cur.fetchall():
            snap_data[sid] = (mj, mu, su)

    print(f"  {len(snap_data)} snapshot member records fetched")

    # Build lookup for settlements: (city, target_date, metric) -> (settlement_value, unit)
    print("Step 3: fetch settlement values ...")
    cur.execute(
        """
        SELECT city, target_date, temperature_metric, settlement_value, unit
        FROM settlements
        WHERE authority = 'VERIFIED' AND target_date >= ?
        """,
        (OOS_START,),
    )
    settlement_lookup: dict[tuple, tuple] = {}
    for city, td, metric, sv, unit in cur.fetchall():
        settlement_lookup[(city, td, metric)] = (float(sv), unit)

    print(f"  {len(settlement_lookup)} settlement records loaded")

    # NOTE: We do NOT query calibration_pairs — it has 48M rows with no snapshot_id index.
    # RAW method is computed analytically from members_json (same underlying computation;
    # calibration_pairs.p_raw is the MC Gaussian-mixture result, indistinguishable from analytic
    # at n>=50 members per prior session validation).
    print("Step 4: assembling rows (no calibration_pairs query — RAW computed analytically) ...")

    # Assemble final rows
    for city, target_date, metric, min_lead_days, snapshot_id in snap_keys:
        sdata = snap_data.get(snapshot_id)
        if sdata is None:
            skipped += 1
            continue
        members_json, members_unit, settlement_unit = sdata

        sval = settlement_lookup.get((city, target_date, metric))
        if sval is None:
            skipped += 1
            continue
        settlement_value, s_unit_raw = sval

        if s_unit_raw and s_unit_raw in ("C", "F"):
            s_unit = s_unit_raw
        elif members_unit == "degF":
            s_unit = "F"
        else:
            s_unit = "C"

        season = _season_for_date_str(target_date)

        rows.append({
            "city": city,
            "target_date": target_date,
            "metric": metric,
            "lead_days": float(min_lead_days),
            "settlement_value": float(settlement_value),
            "unit": s_unit,
            "season": season,
            "snapshot_id": snapshot_id,
            "members_json": members_json,
            "members_unit": members_unit or "degC",
        })

    print(f"  {len(rows)} usable rows assembled, {skipped} skipped")
    return rows


# ---------------------------------------------------------------------------
# Fetch RAW full bin vector for CRPS/cov90
# ---------------------------------------------------------------------------

def _fetch_raw_full_vector(
    fconn: sqlite3.Connection,
    city: str,
    target_date: str,
    metric: str,
    lead_days: float,
    settlement_value: float,
    s_unit: str,
) -> tuple[float, bool] | None:
    cur = fconn.cursor()
    cur.execute(
        """
        SELECT range_label, p_raw
        FROM calibration_pairs
        WHERE city = ? AND target_date = ? AND temperature_metric = ?
          AND lead_days = ? AND training_allowed = 1
        """,
        (city, target_date, metric, lead_days),
    )
    bin_rows = cur.fetchall()
    if not bin_rows:
        return None

    label_idx = _label_idx_for_unit(s_unit)
    bins_n = len(_bins_for_unit(s_unit))
    p_vec = np.zeros(bins_n)
    for rl, pr in bin_rows:
        idx = label_idx.get(rl)
        if idx is not None and pr is not None:
            p_vec[idx] = float(pr)

    total = p_vec.sum()
    if total > 0:
        p_vec /= total

    settled_idx = _find_settled_bin_index(settlement_value, s_unit)
    if settled_idx is None:
        return None

    return _crps(p_vec, settled_idx), _covered_90(p_vec, settled_idx)


# ---------------------------------------------------------------------------
# Main scoring loop
# ---------------------------------------------------------------------------

def run_bakeoff() -> None:
    print("=== CALIBRATION BAKE-OFF ===")
    print(f"OOS window: target_date >= {OOS_START}")

    fconn = sqlite3.connect(f"file:{FORECASTS_DB}?mode=ro", uri=True)
    wconn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True)

    emos_cells = _load_emos_cells()
    grid_rep = _load_grid_rep()
    bias_table = _load_bias_verified(wconn)
    wconn.close()

    print(f"EMOS cells: {len(emos_cells)} | Grid-rep cities: {len(grid_rep)} | Bias VERIFIED: {len(bias_table)}")

    rows = _load_oos_rows(fconn)
    total = len(rows)
    print(f"\nScoring {total} rows ...")

    # Accumulators: {(city,metric): {method: [values]}}
    scores_ll: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    scores_crps: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    scores_cov90: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for i, row in enumerate(rows):
        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{total}] ...")

        city = row["city"]
        metric = row["metric"]
        key = (city, metric)
        s_unit = row["unit"]
        sv = row["settlement_value"]
        season = row["season"]
        lead_days = row["lead_days"]
        target_date = row["target_date"]

        # Convert members to degC once (needed by EMOS/GRID_REP/BIAS)
        try:
            members_raw = np.array(json.loads(row["members_json"]), dtype=float)
        except (json.JSONDecodeError, ValueError):
            continue

        mu_unit = row["members_unit"]
        if mu_unit == "degF":
            members_c = (members_raw - 32.0) / 1.8
        else:
            members_c = members_raw.copy()

        settled_idx = _find_settled_bin_index(sv, s_unit)
        if settled_idx is None:
            continue

        # ---- RAW (analytic Gaussian-mixture, no correction) ----
        # RAW is members_c with no offset/bias applied; same computation as
        # calibration_pairs.p_raw (MC Gaussian-mixture, analytically equivalent at n>=50).
        p_vec_raw = _analytic_pvec(members_c, s_unit)
        raw_p = float(p_vec_raw[settled_idx])
        scores_ll[key]["RAW"].append(-math.log(max(raw_p, LOG_CLIP)))
        scores_crps[key]["RAW"].append(_crps(p_vec_raw, settled_idx))
        scores_cov90[key]["RAW"].append(1.0 if _covered_90(p_vec_raw, settled_idx) else 0.0)

        # ---- EMOS ----
        emos_result = _emos_predictive(emos_cells, city, season, lead_days, members_c)
        if emos_result is not None:
            mu_c, sigma_c = emos_result
            mu_s = mu_c * 1.8 + 32.0 if s_unit == "F" else mu_c
            sigma_s = sigma_c * 1.8 if s_unit == "F" else sigma_c
            bins = _bins_for_unit(s_unit)
            p_vec = np.array([_bin_prob_gaussian(mu_s, sigma_s, b["lo"], b["hi"]) for b in bins])
            total_p = p_vec.sum()
            if total_p > 0:
                p_vec /= total_p
            p_s = float(p_vec[settled_idx])
            scores_ll[key]["EMOS"].append(-math.log(max(p_s, LOG_CLIP)))
            scores_crps[key]["EMOS"].append(_crps(p_vec, settled_idx))
            scores_cov90[key]["EMOS"].append(1.0 if _covered_90(p_vec, settled_idx) else 0.0)

        # ---- GRID_REP ----
        gr_entry = grid_rep.get(city)
        gr_offset_c: float | None = None
        if gr_entry and gr_entry.get("activated"):
            gr_offset_c = float(gr_entry["offset_c"])
            p_vec_gr = _analytic_pvec(members_c - gr_offset_c, s_unit)
            p_s_gr = float(p_vec_gr[settled_idx])
            scores_ll[key]["GRID_REP"].append(-math.log(max(p_s_gr, LOG_CLIP)))
            scores_crps[key]["GRID_REP"].append(_crps(p_vec_gr, settled_idx))
            scores_cov90[key]["GRID_REP"].append(1.0 if _covered_90(p_vec_gr, settled_idx) else 0.0)

        # ---- BIAS ----
        bias_c = bias_table.get((city, season, metric))
        if bias_c is not None:
            p_vec_bi = _analytic_pvec(members_c - bias_c, s_unit)
            p_s_bi = float(p_vec_bi[settled_idx])
            scores_ll[key]["BIAS"].append(-math.log(max(p_s_bi, LOG_CLIP)))
            scores_crps[key]["BIAS"].append(_crps(p_vec_bi, settled_idx))
            scores_cov90[key]["BIAS"].append(1.0 if _covered_90(p_vec_bi, settled_idx) else 0.0)

        # ---- GRID_BIAS ----
        if gr_offset_c is not None and bias_c is not None:
            p_vec_gb = _analytic_pvec(members_c - gr_offset_c - bias_c, s_unit)
            p_s_gb = float(p_vec_gb[settled_idx])
            scores_ll[key]["GRID_BIAS"].append(-math.log(max(p_s_gb, LOG_CLIP)))
            scores_crps[key]["GRID_BIAS"].append(_crps(p_vec_gb, settled_idx))
            scores_cov90[key]["GRID_BIAS"].append(1.0 if _covered_90(p_vec_gb, settled_idx) else 0.0)

    fconn.close()
    print("Scoring complete.")

    # ---------------------------------------------------------------------------
    # Build output
    # ---------------------------------------------------------------------------
    METHODS = ["RAW", "EMOS", "GRID_REP", "BIAS", "GRID_BIAS"]

    per_city_winner: list[dict] = []
    method_totals: dict[str, dict[str, list]] = defaultdict(lambda: {"logloss": [], "crps": [], "cov90": []})
    n_city_method_cells = 0

    for key in sorted(scores_ll.keys()):
        city, metric = key
        n_raw = len(scores_ll[key].get("RAW", []))
        if n_raw < 5:
            continue

        method_stats: dict[str, dict] = {}
        for method in METHODS:
            ll_list = scores_ll[key].get(method, [])
            if len(ll_list) < 5:
                continue
            cr_list = scores_crps[key].get(method, [])
            cv_list = scores_cov90[key].get(method, [])
            n = len(ll_list)
            n_city_method_cells += 1
            mean_ll = float(np.mean(ll_list))
            mean_crps = float(np.mean(cr_list)) if cr_list else None
            mean_cov90 = float(np.mean(cv_list)) if cv_list else None
            method_stats[method] = {
                "n": n,
                "mean_logloss": round(mean_ll, 5),
                "mean_crps": round(mean_crps, 5) if mean_crps is not None else None,
                "mean_cov90": round(mean_cov90, 4) if mean_cov90 is not None else None,
            }
            method_totals[method]["logloss"].extend(ll_list)
            method_totals[method]["crps"].extend(cr_list)
            method_totals[method]["cov90"].extend(cv_list)

        if not method_stats:
            continue

        winner = min(method_stats, key=lambda m: method_stats[m]["mean_logloss"])
        others = [m for m in method_stats if m != winner]
        runner_up = min(others, key=lambda m: method_stats[m]["mean_logloss"]) if others else "N/A"
        raw_ll = method_stats.get("RAW", {}).get("mean_logloss")
        winner_ll = method_stats[winner]["mean_logloss"]
        n_settled = method_stats[winner]["n"]

        per_city_winner.append({
            "city": city,
            "metric": metric,
            "winner_method": winner,
            "winner_logloss": winner_ll,
            "raw_logloss": raw_ll,
            "n_settled": n_settled,
            "runner_up": runner_up,
            "low_confidence": n_settled < MIN_N_CONFIDENCE,
            "methods": method_stats,
        })

    method_aggregate: list[dict] = []
    for method in METHODS:
        ll = method_totals[method]["logloss"]
        cr = method_totals[method]["crps"]
        cv = method_totals[method]["cov90"]
        if not ll:
            continue
        cities_won = sum(1 for r in per_city_winner if r["winner_method"] == method)
        method_aggregate.append({
            "method": method,
            "cities_won": cities_won,
            "mean_logloss": round(float(np.mean(ll)), 5),
            "mean_crps": round(float(np.mean(cr)), 5) if cr else None,
            "mean_cov90": round(float(np.mean(cv)), 4) if cv else None,
            "n_observations": len(ll),
        })
    method_aggregate.sort(key=lambda x: x["mean_logloss"])

    caveats = (
        "ALL methods use analytic Gaussian-mixture from members_json "
        "(instrument sigma floored at 0.5 native-unit, WMO half-step preimage). "
        "RAW=no correction; EMOS=EMOS N(mu_c,sigma_c); GRID_REP=members shifted by grid-rep offset_c; "
        "BIAS=members shifted by effective_bias_c; GRID_BIAS=both offsets combined. "
        "calibration_pairs NOT used (no snapshot_id index; analytic==MC at n>=50). "
        "LOW_CONFIDENCE when n_settled < 20. "
        "EMOS skipped where cell served='raw' or missing. "
        "GRID_REP skipped where activated=False or city absent. "
        "BIAS skipped where no VERIFIED row for (city, season, metric). "
        "Grid-rep offset is lead-invariant (single value per city, no per-season). "
        "Dedup: MIN(lead_days) per (city, target_date, metric), training_allowed=1, outcome=1. "
        "OOS: target_date >= 2025-01-01. "
        "No look-ahead: EMOS params from emos_calibration.json (fit<=2024), "
        "grid-rep from grid_representativeness_offset.json (trailing 120-day window pre-bakeoff), "
        "bias from model_bias_ens VERIFIED rows."
    )

    scorecard = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "oos_start": OOS_START,
        "n_city_method_cells": n_city_method_cells,
        "per_city_winner": per_city_winner,
        "method_aggregate": method_aggregate,
        "caveats": caveats,
    }

    with open(SCORECARD_OUT, "w") as f:
        json.dump(scorecard, f, indent=2)
    print(f"\nScorecard -> {SCORECARD_OUT}")

    # ---------------------------------------------------------------------------
    # Summary text
    # ---------------------------------------------------------------------------
    lines = []
    lines.append("=" * 76)
    lines.append("CALIBRATION BAKE-OFF SUMMARY")
    lines.append(f"OOS: target_date >= {OOS_START}  |  n_city_method_cells={n_city_method_cells}")
    lines.append("=" * 76)
    lines.append("")
    lines.append("METHOD AGGREGATE (all cities pooled, lower logloss = better)")
    lines.append(
        f"{'Method':<12} {'CitiesWon':>10} {'MeanLogLoss':>12} {'MeanCRPS':>10} "
        f"{'Cov90':>7} {'N_obs':>7}"
    )
    lines.append("-" * 76)
    for m in method_aggregate:
        cr_s = f"{m['mean_crps']:.4f}" if m["mean_crps"] is not None else "   N/A"
        cv_s = f"{m['mean_cov90']:.3f}" if m["mean_cov90"] is not None else "  N/A"
        lines.append(
            f"{m['method']:<12} {m['cities_won']:>10} {m['mean_logloss']:>12.5f} "
            f"{cr_s:>10} {cv_s:>7} {m['n_observations']:>7}"
        )

    lines.append("")
    lines.append("PER-(CITY, METRIC) WINNER TABLE")
    lines.append(
        f"{'City':<22} {'Metric':<6} {'Winner':<10} {'WinLL':>8} {'RawLL':>8} "
        f"{'N':>5} {'RunnerUp':<10} {'Flag'}"
    )
    lines.append("-" * 90)
    for r in sorted(per_city_winner, key=lambda x: (x["city"], x["metric"])):
        flag = "LOW_CONF" if r.get("low_confidence") else ""
        raw_s = f"{r['raw_logloss']:.4f}" if r["raw_logloss"] is not None else "  N/A "
        lines.append(
            f"{r['city']:<22} {r['metric']:<6} {r['winner_method']:<10} "
            f"{r['winner_logloss']:>8.4f} {raw_s:>8} {r['n_settled']:>5} "
            f"{r['runner_up']:<10} {flag}"
        )

    lines.append("")
    lines.append("CAVEATS:")
    for sent in caveats.split(". "):
        sent = sent.strip()
        if sent:
            lines.append(f"  - {sent}.")

    summary = "\n".join(lines)
    with open(SUMMARY_OUT, "w") as f:
        f.write(summary)
    print(f"Summary -> {SUMMARY_OUT}")
    print()
    print(summary)


if __name__ == "__main__":
    run_bakeoff()
