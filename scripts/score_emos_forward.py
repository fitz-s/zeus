#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused/audited: 2026-06-02
# Authority basis: EMOS shadow-ledger task; PIECE 3 + CI extension §4 spec.
#   Reads state/emos_shadow_ledger.jsonl + zeus-world.db (read-only, mode=ro).
#   Live truth: observation_instants.running_max (max over the day, WU station).
#   Metrics: Brier + log-score raw vs emos; EMOS predictive band coverage (PIT/cov90);
#            k_cov solve; counterfactual n_died_raw / n_rescued_emos; licensing table.
#   LIVE TRUTH ONLY — no ERA5/online fetches.
#
#   Robust-edge formula authority: trade_score.py:68-71 (_robust_trade_score_receipt).
#   Live penalty literals: event_reactor_adapter.py:4643-4644 (penalty=0.01, stress_penalty=0.01).
"""Score EMOS shadow-ledger predictions against live-truth settlement.

Usage:
    python scripts/score_emos_forward.py

Blocks produced:
  §3  Basic Brier + log-score, raw vs emos, aggregate + per-city.
  §4i EMOS predictive band coverage: PIT/cov90 per city, k_cov solve,
      verdict {EMOS_CI_HONEST, UNDER_COVERED, OVER_DISPERSED, INSUFFICIENT_N}.
  §4ii Counterfactual: edges died under MC q_lcb but rescued by EMOS at k_cov
      (honest alpha proof). Win-rate vs cost.
  §5  Per-city LICENSABLE table (headline output).

§4ii uses TWO PASSES:
  Pass 1 (data collection loop): collect raw_brier, log-prob, PIT, raw scored truth.
  §4i  (print block): compute per-city city_k_cov from collected PIT arrays.
  Pass 2 (counterfactual loop): recompute emos_q_lcb at harness-derived k_cov
         using _bin_prob_from_row; count rescued/died using the honest band.

If too few settled rows exist, reports counts and exits cleanly.
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from math import log

import numpy as np
from scipy.stats import norm, ks_1samp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

LEDGER = os.path.join(REPO, "state", "emos_shadow_ledger.jsonl")
WORLD = os.path.join(REPO, "state", "zeus-world.db")
FORECASTS = os.path.join(REPO, "state", "zeus-forecasts.db")

# Forecast-consistency gate tolerance (°C).
# A ledger row's raw_mu_c must be within this of the causal snapshot mean.
# Rows exceeding this delta are stale/inconsistent — excluded from all scoring.
STALE_MU_TOL_C = 1.0

TODAY = date.today()
LOG_CLIP = 1e-9

# Coverage constants — match validate_analytic_ci_coverage.py
PI_LOW, PI_HIGH = 0.05, 0.95
COV90_LOW, COV90_HIGH = 0.86, 0.94
PIT_MEAN_TOL = 0.05
MIN_N_FOR_VERDICT = 20


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_ledger():
    rows = []
    if not os.path.exists(LEDGER):
        return rows
    with open(LEDGER, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"  [WARN] ledger line {lineno} parse error: {exc}")
    return rows


def _causal_snapshot_mean_c(
    forecasts_conn: sqlite3.Connection,
    city: str,
    target_date: str,
    decision_ts: str,
) -> float | None:
    """Return the member-mean in °C of the freshest causally-available snapshot.

    'Freshest causally-available' = minimum lead_hours among snapshots with
    available_at <= decision_ts for (city, target_date, metric='high').

    Returns None if no such snapshot exists (can't verify → row is excluded).
    Uses string comparison for ISO timestamps (both are UTC +00:00).
    """
    try:
        row = forecasts_conn.execute(
            """
            SELECT members_json, members_unit
            FROM ensemble_snapshots
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = 'high'
              AND available_at <= ?
            ORDER BY lead_hours ASC
            LIMIT 1
            """,
            (city, target_date, decision_ts),
        ).fetchone()
        if row is None:
            return None
        members = json.loads(row["members_json"])
        arr = np.array([float(v) for v in members if v is not None], dtype=float)
        if arr.size == 0:
            return None
        unit = (row["members_unit"] or "").lower()
        if unit in ("degf", "f"):
            arr = (arr - 32.0) * 5.0 / 9.0
        return float(arr.mean())
    except Exception:
        return None


def _build_stale_set(settled_rows: list[dict]) -> tuple[set, dict]:
    """Return (stale_ids, reason_map) for forecast-inconsistent ledger rows.

    stale_ids: set of id(row) for rows whose raw_mu_c deviates > STALE_MU_TOL_C
               from the causally-available snapshot mean.
    reason_map: id(row) → human-readable reason string (for debug).

    Opens zeus-forecasts.db read-only; handles missing DB gracefully (all rows
    pass — can't gate what we can't verify).
    """
    stale_ids: set[int] = set()
    reason_map: dict[int, str] = {}

    if not os.path.exists(FORECASTS):
        print(f"  [WARN] zeus-forecasts.db not found — skipping consistency gate")
        return stale_ids, reason_map

    try:
        conn = sqlite3.connect(f"file:{FORECASTS}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        for row in settled_rows:
            raw_mu_c = row.get("raw_mu_c")
            if raw_mu_c is None or not np.isfinite(float(raw_mu_c)):
                # No raw_mu_c to compare — exclude (can't verify)
                stale_ids.add(id(row))
                reason_map[id(row)] = "raw_mu_c missing/nan"
                continue
            decision_ts = row.get("ts", "")
            city = row.get("city", "")
            target_date = row.get("target_date", "")
            causal_mean = _causal_snapshot_mean_c(conn, city, target_date, decision_ts)
            if causal_mean is None:
                stale_ids.add(id(row))
                reason_map[id(row)] = "no causal snapshot found"
                continue
            delta = abs(float(raw_mu_c) - causal_mean)
            if delta > STALE_MU_TOL_C:
                stale_ids.add(id(row))
                reason_map[id(row)] = (
                    f"raw_mu_c={float(raw_mu_c):.2f}°C vs causal_mean={causal_mean:.2f}°C "
                    f"(delta={delta:.2f}°C > tol={STALE_MU_TOL_C}°C)"
                )
        conn.close()
    except Exception as exc:
        print(f"  [WARN] consistency gate DB error ({exc}) — skipping gate")
        stale_ids.clear()
        reason_map.clear()

    return stale_ids, reason_map


def _live_truth_by_city_date(cities_dates: set) -> dict:
    """Return (city, target_date) → (daily_max_float, temp_unit_str) | None."""
    result = {}
    if not cities_dates:
        return result
    if not os.path.exists(WORLD):
        print(f"  [ERROR] zeus-world.db not found at {WORLD}")
        return result
    try:
        conn = sqlite3.connect(f"file:{WORLD}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "observation_instants" not in tables:
            print("  [WARN] observation_instants table not found in zeus-world.db")
            conn.close()
            return result
        for city, target_date in cities_dates:
            try:
                row = conn.execute(
                    """
                    SELECT MAX(running_max) AS daily_max, temp_unit
                    FROM observation_instants
                    WHERE city = ? AND target_date = ?
                      AND running_max IS NOT NULL
                      AND COALESCE(authority, 'UNVERIFIED') != 'UNVERIFIED'
                    """,
                    (city, target_date),
                ).fetchone()
                if row and row["daily_max"] is not None:
                    result[(city, target_date)] = (float(row["daily_max"]), row["temp_unit"] or "C")
                else:
                    result[(city, target_date)] = None
            except Exception as exc:
                print(f"  [WARN] truth query failed for ({city}, {target_date}): {exc}")
                result[(city, target_date)] = None
        conn.close()
    except Exception as exc:
        print(f"  [ERROR] Failed to open zeus-world.db: {exc}")
    return result


def _to_bin_unit(value_raw: float, raw_unit: str, target_unit: str) -> float:
    if raw_unit == target_unit:
        return value_raw
    if raw_unit == "C" and target_unit == "F":
        return value_raw * 9.0 / 5.0 + 32.0
    if raw_unit == "F" and target_unit == "C":
        return (value_raw - 32.0) * 5.0 / 9.0
    return value_raw


def _to_celsius(value: float, unit: str) -> float:
    """Convert to Celsius for PIT computation (EMOS params are always °C)."""
    if (unit or "").upper() in ("F", "DEGF"):
        return (value - 32.0) * 5.0 / 9.0
    return value


def _bin_contains(truth: float, bin_low, bin_high) -> bool:
    lo_ok = (bin_low is None) or (truth >= bin_low)
    hi_ok = (bin_high is None) or (truth < bin_high)
    return lo_ok and hi_ok


def _safe_log(q: float) -> float:
    return log(max(q, LOG_CLIP))


def _mean(lst):
    return sum(lst) / len(lst) if lst else None


def _fmt(v, width: int = 9):
    return f"{v:{width}.4f}" if v is not None else f"{'N/A':>{width}}"


# ---------------------------------------------------------------------------
# §4i — PIT / cov90 + k_cov solve
# ---------------------------------------------------------------------------

def _coverage_at_k(pit: np.ndarray, k: float) -> float:
    """cov90 of the N(mu, k*sigma) band, given PIT values for k=1."""
    if k == 1.0:
        return float(np.mean((pit >= PI_LOW) & (pit <= PI_HIGH)))
    arr = np.clip(pit, 1e-9, 1.0 - 1e-9)
    pit_k = norm.cdf(norm.ppf(arr) / k)
    return float(np.mean((pit_k >= PI_LOW) & (pit_k <= PI_HIGH)))


def _solve_k_cov(pit: np.ndarray) -> float:
    """Smallest k≥1 s.t. cov90(k) ∈ [0.86,0.94]; clamp 1 if already covers."""
    arr = np.asarray(pit, dtype=float)
    if arr.size < MIN_N_FOR_VERDICT:
        return 1.0
    if _coverage_at_k(arr, 1.0) >= COV90_LOW:
        return 1.0
    k_lo, k_hi = 1.0, 10.0
    if _coverage_at_k(arr, k_hi) < COV90_LOW:
        return k_hi
    for _ in range(40):
        k_mid = (k_lo + k_hi) / 2.0
        if _coverage_at_k(arr, k_mid) >= COV90_LOW:
            k_hi = k_mid
        else:
            k_lo = k_mid
        if k_hi - k_lo < 1e-6:
            break
    return float(k_hi)


def _emos_verdict(n: int, cov90: float, pit_mean: float) -> str:
    if n < MIN_N_FOR_VERDICT:
        return "INSUFFICIENT_N"
    if cov90 < COV90_LOW:
        return "UNDER_COVERED"
    if cov90 > COV90_HIGH:
        return "OVER_DISPERSED"
    if abs(pit_mean - 0.5) <= PIT_MEAN_TOL:
        return "EMOS_CI_HONEST"
    return "OVER_DISPERSED" if cov90 > 0.90 else "UNDER_COVERED"


def _compute_pit_stats(pit: np.ndarray) -> dict:
    """Return dict with mean, cov90, ks_p for a PIT array."""
    pit_mean = float(np.mean(pit))
    cov90 = float(np.mean((pit >= PI_LOW) & (pit <= PI_HIGH)))
    try:
        ks_p = float(ks_1samp(pit, norm.cdf, args=(0.5, 0.5 / 1.96)).pvalue)
    except Exception:
        ks_p = float("nan")
    return {"mean": pit_mean, "cov90": cov90, "ks_p": ks_p}


# ---------------------------------------------------------------------------
# §4ii — counterfactual recompute helpers
# ---------------------------------------------------------------------------

def _bin_prob_from_row(row: dict, k: float = 1.0) -> float | None:
    """Recompute emos_q_lcb = min(emos_q, bin_prob(mu, k*sigma, low, high)).

    This is the load-bearing scorer path: called in Pass 2 with the
    harness-derived k_cov per city.  k=1 → returns emos_q unchanged.

    Returns None if mu_c/sigma_c fields are absent (row pre-dates CI recording).
    """
    mu_c = row.get("emos_mu_c")
    sigma_c = row.get("emos_sigma_c")
    if mu_c is None or sigma_c is None:
        return None
    try:
        from src.calibration.emos import bin_probability_settlement
        bin_unit = row.get("bin_unit", "C")
        if bin_unit == "F":
            mu_native = float(mu_c) * 9.0 / 5.0 + 32.0
            sigma_native = float(sigma_c) * 9.0 / 5.0  # σ in °F at k=1
        else:
            mu_native = float(mu_c)
            sigma_native = float(sigma_c)
        bin_low = row.get("bin_low")
        bin_high = row.get("bin_high")
        emos_q = bin_probability_settlement(mu_native, sigma_native, bin_low, bin_high)
        emos_q_k = bin_probability_settlement(mu_native, sigma_native * k, bin_low, bin_high)
        return min(emos_q, emos_q_k)
    except Exception:
        return None


def _robust_edge(q_posterior: float, q_5pct: float, cost: float, penalty: float = 0.01) -> float:
    """Mirror of trade_score.py:68-71 (_robust_trade_score_receipt edge_bound).

    With penalty=stress_penalty=0.01 and c_95pct.value=c_stress.value=cost,
    edge_bound = min(q_5pct - cost - 0.01, q_posterior - cost - 0.01).
    Authority: event_reactor_adapter.py:4643-4644.
    """
    return min(q_5pct - cost - penalty, q_posterior - cost - penalty)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("EMOS Forward Scoring  —  raw ensemble vs EMOS calibrator")
    print("=" * 70)
    print()

    # 1. Load ledger
    all_rows = _load_ledger()
    if not all_rows:
        print("Ledger is empty (0 rows).  Enable the flag and collect data first.")
        print(f"  Ledger path: {LEDGER}")
        print("  Flag: settings['edli']['edli_emos_shadow_ledger_enabled'] = true")
        return

    total_rows = len(all_rows)
    print(f"Ledger rows: {total_rows}")

    # 2. Settled rows — split by metric
    settled_rows_all = [
        r for r in all_rows
        if r.get("target_date") and r["target_date"] < str(TODAY)
    ]
    n_settled_all = len(settled_rows_all)
    # §4i/§4ii/§5 require metric=="high": EMOS table is HIGH-only; settlement truth is
    # observation_instants.running_max which is the daily HIGH.  LOW rows and rows
    # lacking the metric field are excluded from EMOS coverage/licensing.
    settled_rows = [r for r in settled_rows_all if r.get("metric", "") == "high"]
    n_settled_low = sum(1 for r in settled_rows_all if r.get("metric", "") == "low")
    n_settled_unknown = sum(1 for r in settled_rows_all if r.get("metric", "") == "")
    print(f"  settled rows (target_date < {TODAY}): {n_settled_all}")
    print(f"    metric=high (EMOS-eligible):        {len(settled_rows)}")
    print(f"    metric=low  (out of scope):         {n_settled_low}  [needs #54 LOW Platt]")
    print(f"    metric=unknown (old rows, excluded):{n_settled_unknown}")
    print(f"  unsettled rows (future / today):      {total_rows - n_settled_all}")
    print()

    if not settled_rows:
        if settled_rows_all:
            print("No HIGH-metric settled rows yet (only LOW or unknown-metric rows exist).")
            print("  Collect more data with the flag enabled, or wait for HIGH-metric target dates.")
        else:
            print("No settled rows yet — check back after target dates have passed.")
            print(f"  Earliest target_date in ledger: "
                  f"{min(r.get('target_date','?') for r in all_rows)}")
        return

    # 3. Fetch live truth (HIGH metric only — running_max is the daily HIGH)
    settled_pairs = {(r["city"], r["target_date"]) for r in settled_rows}
    truth_map = _live_truth_by_city_date(settled_pairs)
    pairs_with_truth = sum(1 for v in truth_map.values() if v is not None)
    print(f"  distinct HIGH (city, date) pairs:  {len(settled_pairs)}")
    print(f"  with live-truth in obs table:      {pairs_with_truth}")
    print()

    if pairs_with_truth == 0:
        print("No live truth available in observation_instants for settled dates.")
        return

    # ---------------------------------------------------------------------------
    # FORECAST-CONSISTENCY GATE
    # Exclude rows whose raw_mu_c deviates > STALE_MU_TOL_C from the causally-
    # available snapshot mean (available_at <= row["ts"]).  Stale rows indicate
    # the ledger was written from a stale/wrong forecast snapshot and must not
    # enter Brier/PIT/counterfactual scoring.
    # ---------------------------------------------------------------------------
    print("Forecast-consistency gate ...")
    stale_ids, stale_reason_map = _build_stale_set(settled_rows)
    n_settled_high_total = len(settled_rows)
    n_excluded_stale = len(stale_ids)
    n_scored = n_settled_high_total - n_excluded_stale
    print(f"  n_settled_high_total : {n_settled_high_total}")
    print(f"  n_excluded_stale     : {n_excluded_stale}"
          + (" (raw_mu_c mismatch vs causal forecast snapshot)" if n_excluded_stale else ""))
    print(f"  n_scored             : {n_scored}")
    if n_excluded_stale > 0 and n_excluded_stale <= 5:
        for rid, reason in stale_reason_map.items():
            print(f"    [stale] {reason}")
    print()

    settled_rows = [r for r in settled_rows if id(r) not in stale_ids]

    if not settled_rows:
        print("All HIGH settled rows excluded by forecast-consistency gate.")
        print("  Waiting for fresh ledger rows recorded from live forecast snapshots.")
        return

    # ---------------------------------------------------------------------------
    # PASS 1: score Brier/log + collect PIT per city
    # (city_k_cov not yet available; counterfactual deferred to Pass 2)
    # ---------------------------------------------------------------------------
    per_city: dict = defaultdict(lambda: {
        "raw_brier": [], "emos_brier": [], "emos_lcb_brier": [],
        "raw_logprob": [], "emos_logprob": [],
        "raw_q_win": [], "emos_q_win": [],
        "rows_scored": 0, "rows_skipped": 0,
        # Pass-2 counterfactual accumulators (populated after §4i)
        "died_raw_yes": 0, "rescued_emos_yes": 0,
        "rescued_yes_outcomes": [], "rescued_yes_costs": [],
        "cleared_raw_yes": 0,
        "inv_blocked_emos_yes": 0, "inv_blocked_outcomes": [],
        # k=1 reference column (recorded booleans, kept for comparison)
        "rescued_k1_yes": 0,
    })
    # PIT deduplication: one PIT per (city, target_date), not per bin.
    # All bins of a (city, date) share the same (mu_c, sigma_c, y_obs_c).
    # Keys: (city, target_date) → pit_val float.
    # Populated once per date (first scored row for that date wins for mu/sigma).
    pit_by_city_date: dict[tuple, float] = {}
    agg = {
        "raw_brier": [], "emos_brier": [],
        "raw_logprob": [], "emos_logprob": [],
        "raw_q_win": [], "emos_q_win": [],
    }
    rows_scored = rows_skipped = rows_no_emos = 0

    # Store per-row outcome for Pass 2 (avoid re-querying truth)
    row_outcomes: dict = {}  # id(row) → (outcome, truth_unit, truth_raw)

    for row in settled_rows:
        city = row.get("city", "")
        target_date = row.get("target_date", "")
        truth_entry = truth_map.get((city, target_date))
        if truth_entry is None:
            rows_skipped += 1
            per_city[city]["rows_skipped"] += 1
            continue

        truth_raw, truth_unit = truth_entry
        bin_unit = row.get("bin_unit", "C")
        truth_in_unit = _to_bin_unit(truth_raw, truth_unit, bin_unit)
        truth_rounded = round(truth_in_unit)
        bin_low = row.get("bin_low")
        bin_high = row.get("bin_high")
        outcome = 1.0 if _bin_contains(float(truth_rounded), bin_low, bin_high) else 0.0

        raw_q = row.get("raw_q")
        emos_q = row.get("emos_q")
        emos_q_lcb = row.get("emos_q_lcb")  # k=1 shadow reference

        if raw_q is None:
            rows_skipped += 1
            per_city[city]["rows_skipped"] += 1
            continue
        if emos_q is None:
            rows_no_emos += 1

        per_city[city]["raw_brier"].append((float(raw_q) - outcome) ** 2)
        agg["raw_brier"].append((float(raw_q) - outcome) ** 2)
        if emos_q is not None:
            per_city[city]["emos_brier"].append((float(emos_q) - outcome) ** 2)
            agg["emos_brier"].append((float(emos_q) - outcome) ** 2)
        if emos_q_lcb is not None:
            per_city[city]["emos_lcb_brier"].append((float(emos_q_lcb) - outcome) ** 2)

        if outcome == 1.0:
            per_city[city]["raw_q_win"].append(float(raw_q))
            agg["raw_q_win"].append(float(raw_q))
            per_city[city]["raw_logprob"].append(_safe_log(float(raw_q)))
            agg["raw_logprob"].append(_safe_log(float(raw_q)))
            if emos_q is not None:
                per_city[city]["emos_q_win"].append(float(emos_q))
                agg["emos_q_win"].append(float(emos_q))
                per_city[city]["emos_logprob"].append(_safe_log(float(emos_q)))
                agg["emos_logprob"].append(_safe_log(float(emos_q)))

        rows_scored += 1
        per_city[city]["rows_scored"] += 1

        # PIT: one observation per (city, target_date) — NOT per bin.
        # All bins of the same date share the same (mu_c, sigma_c) and the same
        # realized truth, so they produce the same PIT value.  Recording per-bin
        # inflates n and introduces perfect collinearity (64 copies of one PIT
        # is not 64 independent observations).  Deduplicate: record only the
        # first row seen for each (city, target_date).
        date_key = (city, target_date)
        if date_key not in pit_by_city_date:
            mu_c = row.get("emos_mu_c")
            sigma_c = row.get("emos_sigma_c")
            if mu_c is not None and sigma_c is not None and float(sigma_c) > 0:
                truth_c = _to_celsius(truth_raw, truth_unit)
                pit_val = float(norm.cdf((truth_c - float(mu_c)) / float(sigma_c)))
                pit_by_city_date[date_key] = pit_val

        # k=1 reference: recorded booleans
        cleared_raw_k1 = row.get("cleared_raw_buy_yes")
        would_clear_k1 = row.get("would_clear_emos_buy_yes")
        if cleared_raw_k1 is not None and would_clear_k1 is not None:
            if not cleared_raw_k1 and would_clear_k1:
                per_city[city]["rescued_k1_yes"] += 1

        # Stash outcome for Pass 2
        row_outcomes[id(row)] = (outcome, truth_raw, truth_unit)

    print(f"Rows scored:               {rows_scored}")
    print(f"Rows skipped (no truth):   {rows_skipped}")
    print(f"Rows raw_q only (no emos): {rows_no_emos}")
    print()

    if rows_scored == 0:
        print("0 rows scored — cannot compute metrics yet.")
        return

    # ---------------------------------------------------------------------------
    # §3 Aggregate Brier / log-score
    # ---------------------------------------------------------------------------
    print("-" * 70)
    print("§3  AGGREGATE METRICS  (raw vs emos)")
    print("-" * 70)
    raw_brier_agg = _mean(agg["raw_brier"])
    emos_brier_agg = _mean(agg["emos_brier"]) if agg["emos_brier"] else None
    print(f"  Brier raw:   {_fmt(raw_brier_agg)}")
    print(f"  Brier emos:  {_fmt(emos_brier_agg)}", end="")
    if raw_brier_agg is not None and emos_brier_agg is not None:
        print(f"   (improvement: {raw_brier_agg - emos_brier_agg:+.6f})", end="")
    print()
    raw_log_agg = _mean(agg["raw_logprob"])
    emos_log_agg = _mean(agg["emos_logprob"]) if agg["emos_logprob"] else None
    print(f"  Log raw:     {_fmt(raw_log_agg)}")
    print(f"  Log emos:    {_fmt(emos_log_agg)}", end="")
    if raw_log_agg is not None and emos_log_agg is not None:
        print(f"   (improvement: {(emos_log_agg or 0) - (raw_log_agg or 0):+.6f})", end="")
    print()
    print()

    # ---------------------------------------------------------------------------
    # §4i  EMOS band coverage per city — PIT / cov90 / k_cov
    # PIT is ONE per (city, target_date) — not per bin (per-bin inflates n with
    # perfectly correlated copies; all bins of a date share the same predictive).
    # n_dates = distinct settled dates per city; n_bins = total bin-rows scored.
    # MIN_N_FOR_VERDICT applies to n_dates (independent observations), not n_bins.
    # (Must complete BEFORE Pass 2 counterfactual; populates city_k_cov)
    # ---------------------------------------------------------------------------
    print("-" * 70)
    print("§4i EMOS PREDICTIVE BAND COVERAGE (PIT / cov90 / k_cov per city)")
    print("    HIGH metric only — LOW metric excluded (needs #54 LOW Platt)")
    print("    n_dates = distinct settled dates (independent PIT obs); n_bins = bin-rows")
    print("-" * 70)
    city_k_cov: dict[str, float] = {}
    city_verdict: dict[str, str] = {}
    city_cov90: dict[str, float] = {}
    city_n_dates: dict[str, int] = {}

    # Build per-city PIT lists from the deduplicated pit_by_city_date dict
    city_pit_dates: dict[str, list[float]] = defaultdict(list)
    for (city, _date), pit_val in pit_by_city_date.items():
        city_pit_dates[city].append(pit_val)

    print(f"  {'City':<20} {'n_dates':>8} {'n_bins':>7} {'PIT_mean':>9} {'cov90':>7} {'k_cov':>7} {'verdict'}")
    print(f"  {'-'*20} {'-'*8} {'-'*7} {'-'*9} {'-'*7} {'-'*7} {'-'*15}")
    for city in sorted(per_city.keys()):
        pit_list = city_pit_dates.get(city, [])
        n_dates = len(pit_list)
        n_bins = per_city[city]["rows_scored"]
        city_n_dates[city] = n_dates
        if n_dates == 0:
            city_k_cov[city] = 1.0
            city_verdict[city] = "NO_PIT"
            city_cov90[city] = float("nan")
            print(f"  {city:<20} {0:>8} {n_bins:>7} {'N/A':>9} {'N/A':>7} {'1.000':>7} NO_PIT")
            continue
        pit_arr = np.array(pit_list, dtype=float)
        stats = _compute_pit_stats(pit_arr)
        k = _solve_k_cov(pit_arr)
        verdict = _emos_verdict(n_dates, stats["cov90"], stats["mean"])
        city_k_cov[city] = k
        city_verdict[city] = verdict
        city_cov90[city] = stats["cov90"]
        print(f"  {city:<20} {n_dates:>8} {n_bins:>7} {stats['mean']:>9.3f} {stats['cov90']:>7.3f} {k:>7.3f} {verdict}")
    print()

    # ---------------------------------------------------------------------------
    # PASS 2: counterfactual recompute at harness-derived k_cov
    # (city_k_cov is now populated from §4i)
    # ---------------------------------------------------------------------------
    for row in settled_rows:
        city = row.get("city", "")
        row_data = row_outcomes.get(id(row))
        if row_data is None:
            continue  # was skipped in Pass 1
        outcome, truth_raw, truth_unit = row_data

        k = city_k_cov.get(city, 1.0)
        emos_q = row.get("emos_q")
        cost_yes = row.get("cost_buy_yes")
        q_live = row.get("q_live")

        # Recompute emos_q_lcb at k_cov using stored mu_c, sigma_c
        emos_q_lcb_k = _bin_prob_from_row(row, k)

        # Raw cleared (use recorded k=1 boolean as-is — it reflects raw MC q_lcb, not k_cov)
        cleared_raw = row.get("cleared_raw_buy_yes")
        if cleared_raw is not None:
            if cleared_raw:
                per_city[city]["cleared_raw_yes"] += 1

        # Honest would_clear_emos at k_cov
        if emos_q_lcb_k is not None and cost_yes is not None and emos_q is not None:
            rs_emos_k = _robust_edge(float(emos_q), emos_q_lcb_k, float(cost_yes))
            would_clear_emos_k = rs_emos_k > 0
        else:
            would_clear_emos_k = None

        if cleared_raw is not None and would_clear_emos_k is not None:
            if not cleared_raw and would_clear_emos_k:
                per_city[city]["died_raw_yes"] += 1
                per_city[city]["rescued_emos_yes"] += 1
                per_city[city]["rescued_yes_outcomes"].append(outcome)
                if cost_yes is not None:
                    per_city[city]["rescued_yes_costs"].append(float(cost_yes))
            if cleared_raw and not would_clear_emos_k:
                per_city[city]["inv_blocked_emos_yes"] += 1
                per_city[city]["inv_blocked_outcomes"].append(outcome)

    # ---------------------------------------------------------------------------
    # §4ii print: counterfactual table (uses Pass-2 k_cov-recomputed counts)
    # ---------------------------------------------------------------------------
    print("-" * 70)
    print("§4ii COUNTERFACTUAL — edges rescued by EMOS (k_cov-recomputed band)")
    print("-" * 70)
    print("  (emos_q_lcb recomputed at harness-derived k_cov per city; NOT recorded k=1 booleans)")
    print()
    agg_died = agg_rescued = agg_clear_raw = agg_inv_blocked = 0
    agg_rescued_outcomes: list[float] = []
    agg_rescued_costs: list[float] = []
    agg_inv_outcomes: list[float] = []

    print(f"  {'City':<20} {'died_raw':>9} {'resc_k1':>8} {'resc_kN':>8} "
          f"{'rescued_wr':>11} {'clear_raw':>10} {'inv_block':>10}")
    print(f"  {'-'*20} {'-'*9} {'-'*8} {'-'*8} {'-'*11} {'-'*10} {'-'*10}")
    for city in sorted(per_city.keys()):
        d = per_city[city]
        dr = d["died_raw_yes"]
        re = d["rescued_emos_yes"]
        rk1 = d["rescued_k1_yes"]
        cr = d["cleared_raw_yes"]
        ib = d["inv_blocked_emos_yes"]
        outs = d["rescued_yes_outcomes"]
        inv_outs = d["inv_blocked_outcomes"]
        wr_str = f"{_mean(outs):.3f}" if outs else "N/A"
        print(f"  {city:<20} {dr:>9} {rk1:>8} {re:>8} {wr_str:>11} {cr:>10} {ib:>10}")
        agg_died += dr
        agg_rescued += re
        agg_clear_raw += cr
        agg_inv_blocked += ib
        agg_rescued_outcomes.extend(outs)
        agg_rescued_costs.extend(d["rescued_yes_costs"])
        agg_inv_outcomes.extend(inv_outs)

    print(f"  {'AGGREGATE':<20} {agg_died:>9} {'':>8} {agg_rescued:>8} ", end="")
    agg_wr = _mean(agg_rescued_outcomes)
    agg_cost = _mean(agg_rescued_costs)
    agg_wr_str = f"{agg_wr:.3f}" if agg_wr is not None else "N/A"
    print(f"{agg_wr_str:>11} {agg_clear_raw:>10} {agg_inv_blocked:>10}")
    print()
    if agg_rescued_outcomes:
        print(f"  Rescued win-rate: {agg_wr:.3f}  |  mean cost: {_mean(agg_rescued_costs) or float('nan'):.4f}", end="")
        if agg_wr is not None and agg_cost is not None:
            alpha = agg_wr - agg_cost
            print(f"  |  realized alpha: {alpha:+.4f}", end="")
        print()
    if agg_inv_outcomes:
        inv_wr = _mean(agg_inv_outcomes)
        print(f"  Inv-blocked win-rate (EMOS stricter at k_cov): {inv_wr:.3f} — "
              f"{'low WR = EMOS correctly strict' if inv_wr is not None and inv_wr < 0.5 else 'check'}")
    print()

    # ---------------------------------------------------------------------------
    # §3 Per-city Brier breakdown
    # ---------------------------------------------------------------------------
    print("-" * 70)
    print("§3  PER-CITY BRIER  (raw | emos | emos_lcb_k1)")
    print("-" * 70)
    print(f"  {'City':<20} {'n':>5} {'brier_raw':>10} {'brier_emos':>11} {'brier_lcb':>10} {'delta':>8}")
    print(f"  {'-'*20} {'-'*5} {'-'*10} {'-'*11} {'-'*10} {'-'*8}")
    for city in sorted(per_city.keys()):
        d = per_city[city]
        n = d["rows_scored"]
        if n == 0:
            continue
        rb = _mean(d["raw_brier"])
        eb = _mean(d["emos_brier"]) if d["emos_brier"] else None
        lb = _mean(d["emos_lcb_brier"]) if d["emos_lcb_brier"] else None
        delta_s = f"{rb-eb:+.4f}" if (rb is not None and eb is not None) else "N/A"
        print(f"  {city:<20} {n:>5} {_fmt(rb):>10} {_fmt(eb):>11} {_fmt(lb):>10} {delta_s:>8}")
    print()

    # ---------------------------------------------------------------------------
    # §5  LICENSING TABLE
    # ---------------------------------------------------------------------------
    print("=" * 70)
    print("§5  PER-CITY LICENSING TABLE")
    print("=" * 70)
    print("  LICENSABLE criteria: emos_verdict==EMOS_CI_HONEST, n_settled>=20,")
    print("  rescued_win_rate > mean_cost (positive realized alpha), brier_emos<=brier_raw.")
    print("  Counterfactual uses k_cov-recomputed emos_q_lcb (honest band).")
    print()

    header = (f"  {'city':<20} {'n_set':>5} {'cov90':>6} {'k_cov':>6} "
              f"{'verdict':<18} {'n_died':>7} {'n_resc':>7} {'resc_wr':>8} "
              f"{'br_raw':>7} {'br_emos':>7} {'delta':>7} {'LICENSE':>9}")
    print(header)
    print("  " + "-" * (len(header) - 2))

    licensable_cities = []
    for city in sorted(per_city.keys()):
        d = per_city[city]
        n_set = d["rows_scored"]
        verdict = city_verdict.get(city, "N/A")
        k = city_k_cov.get(city, 1.0)
        cov90 = city_cov90.get(city, float("nan"))
        dr = d["died_raw_yes"]
        re = d["rescued_emos_yes"]
        outs = d["rescued_yes_outcomes"]
        costs = d["rescued_yes_costs"]
        resc_wr = _mean(outs)
        resc_cost = _mean(costs)
        rb = _mean(d["raw_brier"])
        eb = _mean(d["emos_brier"]) if d["emos_brier"] else None
        delta = (rb - eb) if (rb is not None and eb is not None) else None

        alpha_positive = (resc_wr is not None and resc_cost is not None and resc_wr > resc_cost)
        brier_ok = (eb is not None and rb is not None and eb <= rb)
        n_ok = n_set >= MIN_N_FOR_VERDICT
        licensed = (verdict == "EMOS_CI_HONEST" and n_ok and alpha_positive and brier_ok)
        if licensed:
            licensable_cities.append(city)
        lic_str = "LICENSABLE" if licensed else "-"

        cov_s = f"{cov90:.3f}" if not (cov90 != cov90) else "N/A"
        rwr_s = f"{resc_wr:.3f}" if resc_wr is not None else "N/A"
        print(f"  {city:<20} {n_set:>5} {cov_s:>6} {k:>6.3f} "
              f"{verdict:<18} {dr:>7} {re:>7} {rwr_s:>8} "
              f"{_fmt(rb):>7} {_fmt(eb):>7} {_fmt(delta):>7} {lic_str:>9}")

    print()
    if licensable_cities:
        print("LICENSABLE CITIES: " + ", ".join(licensable_cities))
    else:
        print("LICENSABLE CITIES: (none yet — collect more settled rows)")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
