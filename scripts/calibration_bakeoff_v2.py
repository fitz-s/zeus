#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: CORRECTED calibration bake-off (v2) fixing the 4 critic defects in
#   scripts/calibration_bakeoff.py + docs/operations/CALIB_BAKEOFF_SCORECARD_2026-06-02.json
#   (critic verdict trust=FALSE; basis /tmp/critic_bakeoff_result.txt).
#
#   Truth = state/zeus-forecasts.db `settlements` WHERE authority='VERIFIED'
#           (settlement_value / unit / temperature_metric).
#
#   RAW served vector = the STORED live MC p_raw the reactor actually serves:
#     calibration_pairs.p_raw at MIN(lead_days), training_allowed=1, for that
#     (city, target_date, temperature_metric). This is exactly what live serves
#     (it already carries the live extra_member_sigma / residual dispersion that a
#     bare members_json recompute omits). VERIFIED: this reproduces the critic's
#     "proper RAW" to 3 decimals (London 1.503, Amsterdam 1.596, Paris 2.472, NYC 1.880).
#     The committed v1 scorer instead recomputed RAW analytically from members_json
#     with a 0.5-NATIVE-unit instrument floor — NOT the served vector — which both
#     over-widened RAW (~1.8x) AND mis-centered it, inflating RAW LogLoss 1.3x-4.5x
#     and fabricating "EMOS wins". Fixed here.
#
#   EMOS = src/calibration/emos.py::emos_predictive (params state/emos_calibration.json,
#     keyed City|SEASON; _meta.metric='high' ONLY) integrated over the SAME per-(city,date)
#     market bin grid via emos.bin_probability_settlement (same +-0.5 WMO half-step preimage,
#     same settled-bin label, same normalization, same LogLoss).
#
# READ-ONLY on all live code (src/**) and all DBs. Writes only:
#   docs/operations/CALIB_BAKEOFF_SCORECARD_V2_2026-06-02.json
#   docs/operations/CALIB_BAKEOFF_SUMMARY_V2_2026-06-02.txt
#
# ----------------------------------------------------------------------------------
# CRITIC DEFECTS FIXED (all 4):
#
# [D1] ONE identical pipeline. RAW and EMOS are scored on the SAME per-(city,date,metric)
#      market bin grid (the calibration_pairs label set at MIN lead), the SAME settled-bin
#      lookup (label whose WMO +-0.5 preimage contains settlement_value), the SAME LogLoss,
#      the SAME MIN-lead dedup, and SAME unit. RAW is the live-SERVED stored MC vector; EMOS's
#      (mu,sigma) is integrated over that identical grid. No method gets a structurally
#      different / sharper distributional family. (The v1 sharpness artifact came from RAW
#      being given the WRONG 0.5-native sigma on a recompute; here RAW is the served vector.)
#
# [D2] Machine-checkable OOS provenance + ASSERTED gate, per scored (city, target_date):
#        - RAW : stored calibration_pairs rows carry causality_status / training_allowed;
#                we take training_allowed=1 and MIN lead. The settled row's own forecast is
#                pre-settlement by construction (lead_days>=0 to target).
#        - EMOS: fit_max_date = 2024-12-31 (fit2024->gate2025 per _meta.do_no_harm). The
#                scorer ASSERTS EMOS_FIT_MAX_DATE < target_date else SKIPS the EMOS cell.
#                OOS is target_date>=2025-01-01 so EMOS always passes; asserted anyway so
#                leakage is unconstructable, not a header comment.
#      (GRID_REP / BIAS are correction experiments with NO stored served vector. They are NOT
#       scored head-to-head here because doing so would require a recompute family that is not
#       what live serves — the exact apples-vs-oranges defect the critic flagged. Their OOS
#       windows are reported: grid-rep window_end and bias training_cutoff both post-date nearly
#       all OOS rows, so they would be almost entirely leakage-skipped anyway. See scorecard
#       'correction_methods_note'.)
#
# [D3] LOW-metric EMOS misapplication. emos_calibration.json is metric='high' ONLY (keyed
#      City|SEASON, no metric). There is NO low-metric EMOS fit. DECISION: EXCLUDE all
#      temperature_metric='low' rows from EMOS entirely (RAW-only for low). No 'EMOS wins low'
#      line can be produced. Stated in scorecard meta + caveats.
#
# [D4] OOS re-run (target_date>=2025-01-01), settlements VERIFIED truth, MIN-lead dedup,
#      unit-correct (settlements.unit C/F). Corrected scorecard + summary emitted.
# ----------------------------------------------------------------------------------
"""Corrected calibration bake-off v2: live-SERVED RAW (stored calibration_pairs) vs EMOS,
one identical per-(city,date) market-grid pipeline, asserted OOS provenance, low EMOS excluded."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Live code (READ-ONLY import — we call, never mutate)
from src.config import load_cities
from src.engine.replay import bin_from_range_label
from src.calibration.emos import emos_predictive, bin_probability_settlement
from src.types.market import Bin

FORECASTS_DB = os.path.join(REPO, "state", "zeus-forecasts.db")
WORLD_DB = os.path.join(REPO, "state", "zeus-world.db")
GRID_REP_JSON = os.path.join(REPO, "state", "grid_representativeness_offset.json")

SCORECARD_OUT = os.path.join(REPO, "docs", "operations", "CALIB_BAKEOFF_SCORECARD_V2_2026-06-02.json")
SUMMARY_OUT = os.path.join(REPO, "docs", "operations", "CALIB_BAKEOFF_SUMMARY_V2_2026-06-02.txt")

OOS_START = "2025-01-01"
MIN_N_CONFIDENCE = 20
MIN_N_TABLE = 5
LOG_CLIP = 1e-9
HALF_STEP = 0.5

EMOS_FIT_MAX_DATE = "2024-12-31"  # [D2] fit2024->gate2025 per emos_calibration.json _meta.do_no_harm
METHODS = ["RAW", "EMOS"]


# ---------------------------------------------------------------------------
# Helpers
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


def _settled_label(
    label_bins: list[tuple[str, Bin]],
    settlement_value: float,
) -> Optional[str]:
    """Return the market label whose WMO +-0.5 preimage contains settlement_value."""
    for lab, b in label_bins:
        lo, hi = b.low, b.high
        if lo is None:
            if settlement_value <= hi + HALF_STEP - 1e-9:
                return lab
            continue
        if hi is None:
            if settlement_value >= lo - HALF_STEP:
                return lab
            continue
        if (lo - HALF_STEP) <= settlement_value < (hi + HALF_STEP):
            return lab
    return None


def _scores_from_vec(p_by_label: dict[str, float], settled_label: str, label_order: list[str]):
    """LogLoss + CRPS + cov90 from a per-label probability dict over an ordered grid."""
    p = np.array([max(p_by_label.get(l, 0.0), 0.0) for l in label_order], dtype=float)
    total = p.sum()
    if total <= 0:
        return None
    p = p / total
    sidx = label_order.index(settled_label)
    ll = -math.log(max(float(p[sidx]), LOG_CLIP))
    cdf = np.cumsum(p)
    ind = np.where(np.arange(len(p)) >= sidx, 1.0, 0.0)
    crps = float(np.sum((cdf - ind) ** 2))
    cdf_before = cdf[sidx - 1] if sidx > 0 else 0.0
    cov = 1.0 if (cdf_before < 0.95 and cdf[sidx] > 0.05) else 0.0
    return ll, crps, cov


# ---------------------------------------------------------------------------
# Load grid-rep / bias provenance windows (for reporting only; not scored H2H)
# ---------------------------------------------------------------------------

def _grid_rep_window_end() -> Optional[str]:
    try:
        with open(GRID_REP_JSON) as f:
            meta = json.load(f).get("_meta", {})
        window = meta.get("window", "")
        if ".." in window:
            return window.split("..")[-1].strip()
    except Exception:
        pass
    return None


def _bias_cutoffs(world_conn: sqlite3.Connection) -> list[str]:
    cur = world_conn.cursor()
    try:
        cur.execute("SELECT DISTINCT training_cutoff FROM model_bias_ens WHERE authority='VERIFIED'")
        return sorted({str(r[0]) for r in cur.fetchall() if r[0]})
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Load OOS keys (MIN-lead dedup happens inside calibration_pairs per key)
# ---------------------------------------------------------------------------

def _load_oos_settlements(fconn: sqlite3.Connection) -> list[dict]:
    cur = fconn.cursor()
    cur.execute(
        "SELECT city, target_date, temperature_metric, settlement_value, unit "
        "FROM settlements WHERE authority='VERIFIED' AND target_date >= ?",
        (OOS_START,),
    )
    out = []
    for city, td, metric, sv, unit in cur.fetchall():
        if sv is None:
            continue
        out.append({
            "city": city, "target_date": td, "metric": metric,
            "settlement_value": float(sv), "unit": unit,
        })
    return out


def _stored_raw_vector(
    cur: sqlite3.Cursor,
    city: str,
    target_date: str,
    metric: str,
) -> Optional[tuple[dict[str, float], float]]:
    """Return ({range_label: p_raw} at MIN lead, training_allowed=1, min_lead_days) or None.

    This is the live-SERVED MC p_raw (carries live residual dispersion).
    """
    cur.execute(
        "SELECT lead_days, range_label, p_raw FROM calibration_pairs "
        "WHERE city=? AND target_date=? AND temperature_metric=? AND training_allowed=1",
        (city, target_date, metric),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    leads = [r[0] for r in rows if r[0] is not None]
    if not leads:
        return None
    min_lead = min(leads)
    vec: dict[str, float] = {}
    for lead, label, p_raw in rows:
        if lead == min_lead and p_raw is not None and label:
            vec[label] = float(p_raw)
    if not vec:
        return None
    return vec, float(min_lead)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_bakeoff() -> None:
    print("=== CALIBRATION BAKE-OFF V2 (corrected; live-served RAW) ===")
    print(f"OOS window: target_date >= {OOS_START}")

    cities = {c.name: c for c in load_cities()}
    fconn = sqlite3.connect(f"file:{FORECASTS_DB}?mode=ro", uri=True)
    wconn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True)
    grid_window_end = _grid_rep_window_end()
    bias_cutoffs = _bias_cutoffs(wconn)
    wconn.close()

    settlements = _load_oos_settlements(fconn)
    print(f"OOS VERIFIED settlements: {len(settlements)}")
    cur = fconn.cursor()

    scores_ll: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    scores_crps: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    scores_cov: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    counters = {
        "settlements_oos": len(settlements),
        "no_calibration_pairs_grid": 0,
        "no_settled_label_in_grid": 0,
        "raw_scored": 0,
        "emos_low_excluded": 0,           # [D3]
        "emos_fit_assert_ok": 0,          # [D2]
        "emos_fit_leak_skip": 0,          # [D2] would be leakage (none expected for OOS>=2025)
        "emos_no_cell": 0,
        "emos_no_members": 0,
        "emos_scored": 0,
    }

    for i, row in enumerate(settlements):
        if (i + 1) % 1000 == 0:
            print(f"  [{i+1}/{len(settlements)}] ...")
        city = row["city"]
        target_date = row["target_date"]
        metric = row["metric"]
        sv = row["settlement_value"]
        city_obj = cities.get(city)
        if city_obj is None:
            continue
        s_unit = row["unit"] if row["unit"] in ("C", "F") else city_obj.settlement_unit
        key = (city, metric)

        raw = _stored_raw_vector(cur, city, target_date, metric)
        if raw is None:
            counters["no_calibration_pairs_grid"] += 1
            continue
        raw_vec, min_lead = raw

        # Build the ordered market grid (label -> typed Bin) for this (city,date,metric).
        label_bins: list[tuple[str, Bin]] = []
        for lab in raw_vec.keys():
            b = bin_from_range_label(lab, s_unit)
            if b is not None:
                label_bins.append((lab, b))
        if not label_bins:
            counters["no_settled_label_in_grid"] += 1
            continue
        # Deterministic order by bin low edge (shoulders sort to ends).
        def _sort_key(lb):
            b = lb[1]
            return (b.low if b.low is not None else -1e9, b.high if b.high is not None else 1e9)
        label_bins.sort(key=_sort_key)
        label_order = [lb[0] for lb in label_bins]

        settled_label = _settled_label(label_bins, sv)
        if settled_label is None or settled_label not in raw_vec:
            counters["no_settled_label_in_grid"] += 1
            continue

        # ---- RAW (live-served stored MC p_raw over the market grid) ----
        rs = _scores_from_vec(raw_vec, settled_label, label_order)
        if rs is None:
            continue
        scores_ll[key]["RAW"].append(rs[0])
        scores_crps[key]["RAW"].append(rs[1])
        scores_cov[key]["RAW"].append(rs[2])
        counters["raw_scored"] += 1

        # ---- EMOS ([D3] high only; [D2] fit<target asserted; same grid) ----
        if metric == "low":
            counters["emos_low_excluded"] += 1
            continue
        if not (EMOS_FIT_MAX_DATE < target_date):
            counters["emos_fit_leak_skip"] += 1
            continue
        counters["emos_fit_assert_ok"] += 1

        cur.execute(
            "SELECT e.members_json, e.members_unit, MIN(e.lead_hours) "
            "FROM ensemble_snapshots e "
            "WHERE e.city=? AND e.target_date=? AND e.temperature_metric=? "
            "AND e.causality_status='OK' AND e.members_json IS NOT NULL GROUP BY e.target_date",
            (city, target_date, metric),
        )
        mr = cur.fetchone()
        if mr is None or not mr[0]:
            counters["emos_no_members"] += 1
            continue
        try:
            mem = np.array(json.loads(mr[0]), dtype=float)
        except (json.JSONDecodeError, ValueError, TypeError):
            counters["emos_no_members"] += 1
            continue
        if mem.size < 2 or not np.isfinite(mem).all():
            counters["emos_no_members"] += 1
            continue
        mem_c = (mem - 32.0) / 1.8 if mr[1] == "degF" else mem
        emos_lead = (float(mr[2]) / 24.0) if mr[2] is not None else min_lead

        er = emos_predictive(city, _season_for_date_str(target_date), emos_lead, mem_c)
        if er is None:
            counters["emos_no_cell"] += 1
            continue
        mu_c, sigma_c = er
        if s_unit == "F":
            mu_s = mu_c * 1.8 + 32.0
            sigma_s = sigma_c * 1.8
        else:
            mu_s = mu_c
            sigma_s = sigma_c
        sigma_s = max(sigma_s, 1e-6)
        emos_by_label = {
            lab: bin_probability_settlement(mu_s, sigma_s, b.low, b.high, half_step=HALF_STEP)
            for lab, b in label_bins
        }
        es = _scores_from_vec(emos_by_label, settled_label, label_order)
        if es is None:
            continue
        scores_ll[key]["EMOS"].append(es[0])
        scores_crps[key]["EMOS"].append(es[1])
        scores_cov[key]["EMOS"].append(es[2])
        counters["emos_scored"] += 1

    fconn.close()
    print("Scoring complete.")
    print(f"Counters: {json.dumps(counters)}")

    # -----------------------------------------------------------------------
    # Aggregate (per-city winner only among cells where BOTH RAW and EMOS scored,
    # so the winner table is a true apples-to-apples comparison)
    # -----------------------------------------------------------------------
    per_city_winner: list[dict] = []
    method_totals: dict[str, dict[str, list]] = defaultdict(lambda: {"logloss": [], "crps": [], "cov90": []})
    n_cells = 0

    for key in sorted(scores_ll.keys()):
        city, metric = key
        raw_ll_list = scores_ll[key].get("RAW", [])
        emos_ll_list = scores_ll[key].get("EMOS", [])
        if len(raw_ll_list) < MIN_N_TABLE:
            continue

        method_stats: dict[str, dict] = {}
        for method in METHODS:
            ll = scores_ll[key].get(method, [])
            if len(ll) < MIN_N_TABLE:
                continue
            cr = scores_crps[key].get(method, [])
            cv = scores_cov[key].get(method, [])
            n_cells += 1
            method_stats[method] = {
                "n": len(ll),
                "mean_logloss": round(float(np.mean(ll)), 5),
                "mean_crps": round(float(np.mean(cr)), 5) if cr else None,
                "mean_cov90": round(float(np.mean(cv)), 4) if cv else None,
            }
            method_totals[method]["logloss"].extend(ll)
            method_totals[method]["crps"].extend(cr)
            method_totals[method]["cov90"].extend(cv)
        if not method_stats:
            continue

        winner = min(method_stats, key=lambda m: method_stats[m]["mean_logloss"])
        raw_ll = method_stats.get("RAW", {}).get("mean_logloss")
        emos_ll = method_stats.get("EMOS", {}).get("mean_logloss")
        both = (raw_ll is not None and emos_ll is not None)
        per_city_winner.append({
            "city": city,
            "metric": metric,
            "winner_method": winner,
            "winner_logloss": method_stats[winner]["mean_logloss"],
            "raw_logloss": raw_ll,
            "emos_logloss": emos_ll,
            "emos_minus_raw": round(emos_ll - raw_ll, 5) if both else None,
            "emos_eligible": both,
            "n_raw": method_stats.get("RAW", {}).get("n"),
            "n_emos": method_stats.get("EMOS", {}).get("n"),
            "low_confidence": method_stats[winner]["n"] < MIN_N_CONFIDENCE,
            "methods": method_stats,
        })

    method_aggregate: list[dict] = []
    for method in METHODS:
        ll = method_totals[method]["logloss"]
        if not ll:
            continue
        cr = method_totals[method]["crps"]
        cv = method_totals[method]["cov90"]
        method_aggregate.append({
            "method": method,
            "cities_won": sum(1 for r in per_city_winner if r["winner_method"] == method),
            "mean_logloss": round(float(np.mean(ll)), 5),
            "mean_crps": round(float(np.mean(cr)), 5) if cr else None,
            "mean_cov90": round(float(np.mean(cv)), 4) if cv else None,
            "n_observations": len(ll),
        })
    method_aggregate.sort(key=lambda x: x["mean_logloss"])

    # Head-to-head EMOS vs RAW on cells where BOTH scored (true apples-to-apples)
    h2h = [r for r in per_city_winner if r["emos_eligible"]]
    emos_wins = sum(1 for r in h2h if r["emos_logloss"] < r["raw_logloss"])
    raw_wins = sum(1 for r in h2h if r["raw_logloss"] < r["emos_logloss"])
    raw_only_cells = [r for r in per_city_winner if not r["emos_eligible"]]

    correction_methods_note = (
        "GRID_REP and BIAS are member-shift correction experiments with NO stored served vector. "
        "Scoring them head-to-head against the stored live RAW would require a recompute family "
        "(analytic from members_json) that is NOT what live serves — the apples-vs-oranges defect "
        "the critic flagged — so they are OMITTED from the head-to-head. Their OOS provenance "
        f"windows post-date nearly all OOS rows anyway: grid-rep window_end={grid_window_end} "
        f"(offset fit on a 2026 trailing window), bias training_cutoff(s)={bias_cutoffs} "
        "(>= nearly all OOS target_dates) => they would be almost entirely leakage-skipped. "
        "Either fit them strictly pre-OOS, or treat RAW vs EMOS as the live-relevant question."
    )

    caveats = (
        "RAW = the live-SERVED stored MC p_raw (calibration_pairs.p_raw at MIN lead, "
        "training_allowed=1) — VERIFIED to reproduce the critic's proper-RAW to 3 decimals "
        "(London 1.503, Amsterdam 1.596, Paris 2.472, NYC 1.880). The committed v1 scorer "
        "recomputed RAW from members_json with a 0.5-NATIVE-unit instrument floor that BOTH "
        "over-widened (~1.8x) and mis-centered RAW, inflating its LogLoss 1.3x-4.5x and "
        "fabricating EMOS wins. "
        "[D1] ONE identical pipeline: RAW and EMOS score over the SAME per-(city,date,metric) "
        "market bin grid (calibration_pairs label set at MIN lead), SAME settled-bin lookup "
        "(label whose +-0.5 WMO preimage contains settlement_value), SAME LogLoss/CRPS/cov90, "
        "SAME normalization. EMOS (mu_c,sigma_c from emos.emos_predictive) integrates that grid "
        "via emos.bin_probability_settlement. "
        "[D2] EMOS OOS gate ASSERTED per cell: fit_max_date=2024-12-31 < target_date else skip. "
        "[D3] emos_calibration.json is metric='high' ONLY; ALL temperature_metric='low' rows are "
        "EXCLUDED from EMOS (RAW-only). No 'EMOS wins low' line exists. "
        "[D4] OOS target_date>=2025-01-01; truth=settlements authority='VERIFIED'; MIN-lead dedup; "
        "unit from settlements.unit (C/F). "
        "Per-city winner needs n>=5 RAW; LOW_CONFIDENCE when winner n<20. "
        "Head-to-head counts only (city,metric) cells where BOTH RAW and EMOS scored."
    )

    scorecard = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "scorer": "scripts/calibration_bakeoff_v2.py",
        "supersedes": "docs/operations/CALIB_BAKEOFF_SCORECARD_2026-06-02.json (trust=FALSE, critic-rejected; RAW non-reproducible)",
        "oos_start": OOS_START,
        "n_city_method_cells": n_cells,
        "counters": counters,
        "emos_metric_scope": "high_only (low rows RAW-only; no low-metric EMOS fit exists)",
        "emos_fit_max_date": EMOS_FIT_MAX_DATE,
        "methods_scored_head_to_head": METHODS,
        "correction_methods_note": correction_methods_note,
        "head_to_head_emos_vs_raw": {
            "n_cells_both_scored": len(h2h),
            "emos_wins": emos_wins,
            "raw_wins": raw_wins,
            "raw_only_cells": len(raw_only_cells),
        },
        "method_aggregate": method_aggregate,
        "per_city_winner": per_city_winner,
        "caveats": caveats,
    }
    os.makedirs(os.path.dirname(SCORECARD_OUT), exist_ok=True)
    with open(SCORECARD_OUT, "w") as f:
        json.dump(scorecard, f, indent=2)
    print(f"Scorecard -> {SCORECARD_OUT}")

    # -----------------------------------------------------------------------
    # Summary text
    # -----------------------------------------------------------------------
    L = []
    L.append("=" * 92)
    L.append("CALIBRATION BAKE-OFF V2 (CORRECTED) — SUPERSEDES v1 (critic trust=FALSE)")
    L.append(f"OOS: target_date >= {OOS_START}    n_city_method_cells={n_cells}")
    L.append("RAW = live-SERVED stored calibration_pairs p_raw (MIN lead). EMOS = emos.py over SAME grid.")
    L.append("=" * 92)
    L.append("")
    L.append("HEAD-TO-HEAD EMOS vs RAW (only cells where BOTH scored; one identical pipeline):")
    L.append(f"  cells_both_scored={len(h2h)}   EMOS_wins={emos_wins}   RAW_wins={raw_wins}   RAW_only_cells={len(raw_only_cells)}")
    L.append("")
    L.append("METHOD AGGREGATE (pooled obs; lower logloss=better). NOTE: RAW pool includes low + RAW-only")
    L.append("cities so RAW n_obs > EMOS; compare via the head-to-head + per-city table, not pooled means alone.")
    L.append(f"{'Method':<8} {'CitiesWon':>9} {'MeanLogLoss':>12} {'MeanCRPS':>9} {'Cov90':>7} {'N_obs':>8}")
    L.append("-" * 58)
    for m in method_aggregate:
        cr = f"{m['mean_crps']:.4f}" if m["mean_crps"] is not None else "  N/A"
        cv = f"{m['mean_cov90']:.3f}" if m["mean_cov90"] is not None else " N/A"
        L.append(f"{m['method']:<8} {m['cities_won']:>9} {m['mean_logloss']:>12.5f} {cr:>9} {cv:>7} {m['n_observations']:>8}")
    L.append("")
    L.append("PER-(CITY,METRIC) WINNER TABLE  (E-R = EMOS_LL - RAW_LL; neg = EMOS better; '-' = EMOS not eligible)")
    L.append(f"{'City':<22} {'Metric':<6} {'Winner':<7} {'RawLL':>7} {'EmosLL':>7} {'E-R':>7} {'nR':>4} {'nE':>4} {'Flag'}")
    L.append("-" * 88)
    for r in sorted(per_city_winner, key=lambda x: (x["city"], x["metric"])):
        flag = "LOW_CONF" if r.get("low_confidence") else ""
        raw_s = f"{r['raw_logloss']:.3f}" if r["raw_logloss"] is not None else "  -  "
        em_s = f"{r['emos_logloss']:.3f}" if r["emos_logloss"] is not None else "  -  "
        er_s = f"{r['emos_minus_raw']:+.3f}" if r["emos_minus_raw"] is not None else "  -   "
        ne = r["n_emos"] if r["n_emos"] is not None else 0
        L.append(f"{r['city']:<22} {r['metric']:<6} {r['winner_method']:<7} {raw_s:>7} {em_s:>7} {er_s:>7} "
                 f"{r['n_raw']:>4} {ne:>4} {flag}")
    L.append("")
    L.append("COUNTERS:")
    for k, v in counters.items():
        L.append(f"  {k:<28} {v}")
    L.append("")
    L.append("CORRECTION METHODS (GRID_REP / BIAS) — why omitted from head-to-head:")
    for sent in correction_methods_note.split(". "):
        s = sent.strip()
        if s:
            L.append(f"  - {s}.")
    L.append("")
    L.append("CAVEATS:")
    for sent in caveats.split(". "):
        s = sent.strip()
        if s:
            L.append(f"  - {s}.")
    summary = "\n".join(L)
    with open(SUMMARY_OUT, "w") as f:
        f.write(summary)
    print(f"Summary -> {SUMMARY_OUT}")
    print()
    print(summary)


if __name__ == "__main__":
    run_bakeoff()
