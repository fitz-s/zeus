"""Phase 1 v2 Consolidated Reanalysis — C1 through C5.

Created: 2026-05-03
Last reused/audited: 2026-05-03
Authority: RERUN_PLAN_v2.md C1-C5

Operator structural fix decision (2026-05-03):
  REMOVE sigma-band from trigger. Floor IS the trigger.
  fire if cov < floor   (OLD: fire if cov < floor - sigma)
  sigma becomes DIAGNOSTIC only.

Paris EXCLUDED from all components — pending workstream A resync.

Run from repo root:
  .venv/bin/python docs/operations/.../phase1/p2_consolidated_v2.py
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[4]
PHASE1_DIR = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"
DB_PATH = REPO / "state" / "zeus-world.db"
CITIES_JSON = REPO / "config" / "cities.json"
H1_JSON = PHASE1_DIR / "p2_rerun_v2_h1_fix.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TRAIN_START = "2025-07-01"
TRAIN_END   = "2025-12-31"
TEST_START  = "2026-01-01"
TEST_END    = "2026-04-30"
WINDOW_RADIUS = 3
SAFETY_MIN_FLOOR = 0.35
EXCLUDE_CITIES = {"Paris"}

# Policy overrides (operator rulings A + B)
POLICY_OVERRIDES = {
    "Denver": 0.85,   # Ruling A: asymmetric loss
    "Lagos":  0.45,   # Ruling B: infra reality
}

BIN_EDGES  = [0.0, 0.001, 0.05, 0.10, 0.20, 0.30, 0.50, 1.0]
BIN_LABELS = [
    "exact 0", "(0, 0.05)", "[0.05, 0.10)",
    "[0.10, 0.20)", "[0.20, 0.30)", "[0.30, 0.50)", "[0.50, 1.0]",
]

BOOTSTRAP_ITERS = 1000
BOOTSTRAP_SEED  = 42
SEASONS = {"DJF": [12,1,2], "MAM": [3,4,5], "JJA": [6,7,8], "SON": [9,10,11]}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def date_iter(start: str, end: str):
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def directional_window(peak: float | None, radius: int = WINDOW_RADIUS) -> list[int]:
    if peak is None:
        return list(range(24))
    c = int(round(peak))
    return [(h % 24) for h in range(c - radius, c + radius + 1)]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def bin_index(value: float) -> int:
    for i in range(len(BIN_EDGES) - 1):
        if BIN_EDGES[i] <= value < BIN_EDGES[i + 1]:
            return i
    return len(BIN_EDGES) - 2


def open_db() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def fetch_cov_full(conn, city, target_hours, start, end) -> dict[str, float]:
    """H1 fix: every calendar date; zero-row days → 0.0."""
    n = len(target_hours)
    in_clause = ",".join(str(h) for h in target_hours)
    rows = conn.execute(
        f"""
        SELECT target_date,
               COUNT(DISTINCT CAST(local_hour AS INTEGER)) AS hrs
        FROM observation_instants_v2
        WHERE city = ? AND source = 'wu_icao_history'
          AND data_version = 'v1.wu-native'
          AND target_date >= ? AND target_date <= ?
          AND CAST(local_hour AS INTEGER) IN ({in_clause})
        GROUP BY target_date
        """,
        (city, start, end),
    ).fetchall()
    obs = {d_iso: hrs / n for d_iso, hrs in rows}
    return {d_iso: obs.get(d_iso, 0.0) for d_iso in date_iter(start, end)}


def fmt(v, precision: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{precision}f}"
    return str(v)


def summarize_bins(bins: dict) -> list[dict]:
    out = []
    for i in range(len(BIN_EDGES) - 1):
        errs = bins.get(i, [])
        out.append({
            "bin_idx": i,
            "label": BIN_LABELS[i],
            "shortfall_lo": BIN_EDGES[i],
            "shortfall_hi": BIN_EDGES[i + 1],
            "n_samples": len(errs),
            "error_mean": round(statistics.mean(errs), 6) if errs else None,
            "error_std":  round(statistics.stdev(errs), 6) if len(errs) > 1 else None,
        })
    return out


# ---------------------------------------------------------------------------
# C1 — Floor reselection (no sigma-band)
# ---------------------------------------------------------------------------

def c1_floor_reselection(h1_data: dict, cities: list[dict],
                          conn: sqlite3.Connection) -> dict:
    """
    Algorithm (structural fix — no sigma):
      recommended_floor_empirical = max(p05_of_H1_corrected_train_cov, 0.35)
      policy overrides applied on top.
    p10/p25 recomputed from DB (same H1 coverage logic).
    """
    per_city_h1 = h1_data["per_city"]
    results = {}

    for city_info in cities:
        city = city_info["name"]
        if city in EXCLUDE_CITIES:
            results[city] = {"status": "EXCLUDED_WORKSTREAM_A"}
            continue
        cr = per_city_h1.get(city, {})
        if cr.get("status") == "NO_TRAIN_DATA":
            results[city] = {"status": "NO_TRAIN_DATA"}
            continue

        peak = city_info.get("historical_peak_hour")
        target_hours = directional_window(peak)

        # Re-derive full train coverage distribution
        cov = fetch_cov_full(conn, city, target_hours, TRAIN_START, TRAIN_END)
        vals = list(cov.values())
        n = len(vals)

        p05 = percentile(vals, 5)
        p10 = percentile(vals, 10)
        p25 = percentile(vals, 25)

        empirical_floor = max(p05 if p05 is not None else 0.0, SAFETY_MIN_FLOOR)
        empirical_floor = round(empirical_floor, 4)

        policy_override = POLICY_OVERRIDES.get(city)
        final_floor = policy_override if policy_override is not None else empirical_floor
        final_floor = round(final_floor, 4)

        # True FP rate at final_floor on train data (trigger: cov < floor)
        fp_rate = sum(1 for v in vals if v < final_floor) / n if n > 0 else 0.0

        sigma_diag = cr.get("new_sigma", 0.0)
        n_zero = sum(1 for v in vals if v == 0.0)

        results[city] = {
            "p05": round(p05, 6) if p05 is not None else None,
            "p10": round(p10, 6) if p10 is not None else None,
            "p25": round(p25, 6) if p25 is not None else None,
            "recommended_floor_empirical": empirical_floor,
            "policy_override": policy_override,
            "final_floor": final_floor,
            "train_FP_rate": round(fp_rate, 4),
            "n_zero_train": n_zero,
            "sigma_diagnostic": round(sigma_diag, 6) if sigma_diag else 0.0,
        }

    return results


def write_c1(c1: dict) -> None:
    note = "Paris pending workstream A resync; rerun for Paris after A completes."
    out = {
        "_metadata": {
            "created": "2026-05-03",
            "authority": "RERUN_PLAN_v2.md C1",
            "algorithm": "final_floor = max(p05_train, 0.35); policy overrides on top",
            "trigger": "fire if cov < floor  [NO sigma-band]",
            "paris_note": note,
        },
        "policy_overrides": POLICY_OVERRIDES,
        "per_city": c1,
    }
    pj = PHASE1_DIR / "p2_1_FINAL_v2_per_city_floors.json"
    pj.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    lines = [
        "# §2.1 Floor Reselection — v2 (No σ-band)",
        "", "Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C1", "",
        f"> {note}", "",
        "## Algorithm", "",
        "```",
        "recommended_floor_empirical = max(p05_train_cov, 0.35)",
        "final_floor = policy_override if city in OVERRIDES else recommended_floor_empirical",
        "trigger: fire if cov < final_floor  (sigma = DIAGNOSTIC only)",
        "```", "",
        "## Policy Overrides", "",
        "| City | Override | Ruling |",
        "|---|---|---|",
        "| Denver | 0.85 | Ruling A: asymmetric loss |",
        "| Lagos | 0.45 | Ruling B: infra reality |", "",
        "## Per-City Floors", "",
        "| city | p05 | p10 | p25 | rec_floor_empirical | policy_override | final_floor | train_FP_rate | n_zero_train | sigma_diag |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    active = {c: v for c, v in c1.items()
              if v.get("status") not in ("NO_TRAIN_DATA", "EXCLUDED_WORKSTREAM_A")}
    for city in sorted(active):
        r = active[city]
        lines.append(
            f"| {city} | {fmt(r.get('p05'))} | {fmt(r.get('p10'))} | {fmt(r.get('p25'))} "
            f"| {fmt(r.get('recommended_floor_empirical'))} | {fmt(r.get('policy_override'))} "
            f"| {fmt(r.get('final_floor'))} | {fmt(r.get('train_FP_rate'))} "
            f"| {r.get('n_zero_train', 0)} | {fmt(r.get('sigma_diagnostic'))} |"
        )
    no_data = sorted(c for c, v in c1.items() if v.get("status") == "NO_TRAIN_DATA")
    if no_data:
        lines += ["", f"**No-train-data cities**: {', '.join(no_data)}"]
    lines += ["", "**Excluded (workstream A)**: Paris"]

    pm = PHASE1_DIR / "p2_1_FINAL_v2_per_city_floors.md"
    pm.write_text("\n".join(lines) + "\n")
    print(f"  C1: {pj.name}, {pm.name}")


# ---------------------------------------------------------------------------
# C2 — LOW window derivation
# ---------------------------------------------------------------------------

def derive_low_windows(conn: sqlite3.Connection, cities: list[dict]) -> dict:
    """
    For each city: determine LOW peak hour.
    If config has historical_low_hour → use it.
    Otherwise: empirically find the local hour most frequently achieving running_min
    using a single aggregated query per city.
    """
    results: dict = {}

    for city_info in cities:
        city = city_info["name"]
        if city in EXCLUDE_CITIES:
            continue
        explicit = city_info.get("historical_low_hour")
        if explicit is not None:
            results[city] = {
                "source": "config",
                "low_peak_hour": explicit,
                "low_window": directional_window(explicit),
            }
            continue

        # Single query: per date, find the earliest local_hour achieving the day's running_min.
        # Uses a self-join approach: get day_min per date, then join to find matching hour.
        rows = conn.execute(
            """
            WITH day_mins AS (
                SELECT target_date, MIN(running_min) AS day_min
                FROM observation_instants_v2
                WHERE city = ? AND source = 'wu_icao_history'
                  AND data_version = 'v1.wu-native'
                  AND running_min IS NOT NULL
                GROUP BY target_date
            )
            SELECT CAST(o.local_hour AS INTEGER) AS hr, COUNT(*) AS cnt
            FROM observation_instants_v2 o
            JOIN day_mins d ON o.target_date = d.target_date
                           AND o.running_min = d.day_min
            WHERE o.city = ? AND o.source = 'wu_icao_history'
              AND o.data_version = 'v1.wu-native'
              AND o.local_hour IS NOT NULL
            GROUP BY CAST(o.local_hour AS INTEGER)
            ORDER BY cnt DESC
            """,
            (city, city),
        ).fetchall()

        if not rows:
            peak = city_info.get("historical_peak_hour", 14.0)
            low_h = round((float(peak) - 12) % 24)
            results[city] = {
                "source": "heuristic_fallback", "low_peak_hour": low_h,
                "low_window": directional_window(float(low_h)), "n_days": 0,
            }
            continue

        mode_hour = rows[0][0]
        total_days = sum(cnt for _, cnt in rows)
        results[city] = {
            "source": "empirical",
            "low_peak_hour": mode_hour,
            "low_window": directional_window(float(mode_hour)),
            "n_days": total_days,
            "hour_distribution": {hr: cnt for hr, cnt in rows},
        }

    return results


# ---------------------------------------------------------------------------
# C2 — Metric-specific binning
# ---------------------------------------------------------------------------

def c2_metric_binning(conn, cities, c1_floors, low_windows):
    """
    Structural fix: shortfall = max(0, floor - cov)  [NO sigma]
    H2 fix: HIGH cov for HIGH errors, LOW cov for LOW errors.
    """
    high_g: dict[int, list] = defaultdict(list)
    low_g:  dict[int, list] = defaultdict(list)
    high_by_city: dict[str, dict[int, list]] = {}
    low_by_city:  dict[str, dict[int, list]] = {}

    for city_info in cities:
        city = city_info["name"]
        if city in EXCLUDE_CITIES:
            continue
        cr = c1_floors.get(city, {})
        if cr.get("status") in ("NO_TRAIN_DATA", "EXCLUDED_WORKSTREAM_A"):
            continue
        floor = cr.get("final_floor")
        if floor is None:
            continue

        peak_h = city_info.get("historical_peak_hour")
        if peak_h is None:
            continue
        high_hrs = directional_window(peak_h)
        low_info = low_windows.get(city, {})
        low_h    = low_info.get("low_peak_hour")
        low_hrs  = low_info.get("low_window") or directional_window(float(low_h) if low_h else 6.0)

        hist_start = (date.fromisoformat(TEST_START) - timedelta(days=90)).isoformat()
        hcov = fetch_cov_full(conn, city, high_hrs, hist_start, TEST_END)
        lcov = fetch_cov_full(conn, city, low_hrs,  hist_start, TEST_END)

        # Pull winning calibration rows (outcome=1) aggregated per (date, metric)
        cal = conn.execute(
            """
            SELECT target_date, temperature_metric,
                   AVG(p_raw) AS p_avg, decision_group_id
            FROM calibration_pairs_v2
            WHERE city = ? AND authority = 'VERIFIED'
              AND training_allowed = 1 AND outcome = 1
              AND target_date >= ? AND target_date <= ?
            GROUP BY target_date, temperature_metric, decision_group_id
            """,
            (city, TEST_START, TEST_END),
        ).fetchall()

        high_p: dict[str, list] = defaultdict(list)
        high_g_id: dict[str, str] = {}
        low_p:  dict[str, list] = defaultdict(list)
        low_g_id:  dict[str, str] = {}

        for (td, metric, p_avg, grp) in cal:
            if metric == "high":
                high_p[td].append(p_avg)
                if grp:
                    high_g_id[td] = grp
            elif metric == "low":
                low_p[td].append(p_avg)
                if grp:
                    low_g_id[td] = grp

        ch: dict[int, list] = defaultdict(list)
        cl: dict[int, list] = defaultdict(list)

        for d_iso in date_iter(TEST_START, TEST_END):
            hc = hcov.get(d_iso, 0.0)
            lc = lcov.get(d_iso, 0.0)
            hsf = max(0.0, floor - hc)
            lsf = max(0.0, floor - lc)

            if high_p.get(d_iso):
                p = statistics.median(high_p[d_iso])
                err = (1.0 - p) ** 2
                b = bin_index(hsf)
                ch[b].append(err)
                high_g[b].append(err)

            if low_p.get(d_iso):
                p = statistics.median(low_p[d_iso])
                err = (1.0 - p) ** 2
                b = bin_index(lsf)
                cl[b].append(err)
                low_g[b].append(err)

        high_by_city[city] = dict(ch)
        low_by_city[city]  = dict(cl)

    return high_g, low_g, high_by_city, low_by_city


# ---------------------------------------------------------------------------
# C3 — Bootstrap CIs
# ---------------------------------------------------------------------------

def c3_bootstrap(conn, cities, c1_floors, low_windows):
    """1000-iteration bootstrap, resampling unit = decision_group_id."""
    rng = random.Random(BOOTSTRAP_SEED)

    high_ge: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    low_ge:  dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for city_info in cities:
        city = city_info["name"]
        if city in EXCLUDE_CITIES:
            continue
        cr = c1_floors.get(city, {})
        if cr.get("status") in ("NO_TRAIN_DATA", "EXCLUDED_WORKSTREAM_A"):
            continue
        floor = cr.get("final_floor")
        if floor is None:
            continue

        peak_h = city_info.get("historical_peak_hour")
        if peak_h is None:
            continue
        high_hrs = directional_window(peak_h)
        low_info = low_windows.get(city, {})
        low_h    = low_info.get("low_peak_hour")
        low_hrs  = low_info.get("low_window") or directional_window(float(low_h) if low_h else 6.0)

        hist_start = (date.fromisoformat(TEST_START) - timedelta(days=90)).isoformat()
        hcov = fetch_cov_full(conn, city, high_hrs, hist_start, TEST_END)
        lcov = fetch_cov_full(conn, city, low_hrs,  hist_start, TEST_END)

        # Per-date grouped for bootstrap
        cal = conn.execute(
            """
            SELECT target_date, temperature_metric,
                   AVG(p_raw) AS p_avg, decision_group_id
            FROM calibration_pairs_v2
            WHERE city = ? AND authority = 'VERIFIED'
              AND training_allowed = 1 AND outcome = 1
              AND target_date >= ? AND target_date <= ?
            GROUP BY target_date, temperature_metric, decision_group_id
            """,
            (city, TEST_START, TEST_END),
        ).fetchall()

        high_p: dict[str, list] = defaultdict(list)
        high_gid: dict[str, str] = {}
        low_p:  dict[str, list] = defaultdict(list)
        low_gid:  dict[str, str] = {}

        for (td, metric, p_avg, grp) in cal:
            if metric == "high":
                high_p[td].append(p_avg)
                if grp:
                    high_gid[td] = grp
            elif metric == "low":
                low_p[td].append(p_avg)
                if grp:
                    low_gid[td] = grp

        for d_iso in date_iter(TEST_START, TEST_END):
            hc  = hcov.get(d_iso, 0.0)
            lc  = lcov.get(d_iso, 0.0)
            hsf = max(0.0, floor - hc)
            lsf = max(0.0, floor - lc)

            grp_h = high_gid.get(d_iso, f"{city}_{d_iso}_H")
            grp_l = low_gid.get(d_iso, f"{city}_{d_iso}_L")

            if high_p.get(d_iso):
                p   = statistics.median(high_p[d_iso])
                err = (1.0 - p) ** 2
                b   = bin_index(hsf)
                high_ge[b][grp_h].append(err)

            if low_p.get(d_iso):
                p   = statistics.median(low_p[d_iso])
                err = (1.0 - p) ** 2
                b   = bin_index(lsf)
                low_ge[b][grp_l].append(err)

    def boot_ci(ge: dict[str, list]) -> tuple:
        if not ge:
            return None, None, None
        grps = list(ge.keys())
        gmeans = {g: statistics.mean(v) for g, v in ge.items()}
        obs = statistics.mean(gmeans.values())
        boots = sorted(
            statistics.mean(gmeans[g] for g in rng.choices(grps, k=len(grps)))
            for _ in range(BOOTSTRAP_ITERS)
        )
        lo = boots[int(0.025 * BOOTSTRAP_ITERS)]
        hi = boots[int(0.975 * BOOTSTRAP_ITERS)]
        return round(obs, 6), round(lo, 6), round(hi, 6)

    def ci_table(ge_by_bin) -> list[dict]:
        rows = []
        for i in range(len(BIN_EDGES) - 1):
            ge = ge_by_bin.get(i, {})
            m, lo, hi = boot_ci(ge)
            rows.append({
                "bin_idx": i,
                "label": BIN_LABELS[i],
                "n_groups": len(ge),
                "n_obs": sum(len(v) for v in ge.values()),
                "mean_error": m,
                "ci_95_lo":   lo,
                "ci_95_hi":   hi,
                "ci_overlaps_zero": (lo is not None and lo <= 0.0),
            })
        return rows

    return ci_table(high_ge), ci_table(low_ge)


def write_c2_c3(high_g, low_g, high_ci, low_ci, low_windows) -> None:
    note = "Paris pending workstream A resync; rerun for Paris after A completes."
    hs = summarize_bins(high_g)
    ls = summarize_bins(low_g)

    out = {
        "_metadata": {
            "created": "2026-05-03",
            "authority": "RERUN_PLAN_v2.md C2-C3",
            "shortfall_formula": "max(0, floor - cov_metric_specific)  [NO sigma]",
            "paris_note": note,
        },
        "low_windows": {c: {"source": v["source"], "low_peak_hour": v["low_peak_hour"]}
                        for c, v in low_windows.items()},
        "HIGH": {"bin_edges": BIN_EDGES, "labels": BIN_LABELS, "summary": hs},
        "LOW":  {"bin_edges": BIN_EDGES, "labels": BIN_LABELS, "summary": ls},
        "bootstrap": {
            "n_iterations": BOOTSTRAP_ITERS,
            "resampling_unit": "decision_group_id",
            "HIGH_ci": high_ci, "LOW_ci": low_ci,
        },
    }
    pj = PHASE1_DIR / "p2_4_v2_curve_breakpoints.json"
    pj.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    def bin_table(summary, label):
        lines = [f"### {label}", "",
                 "| shortfall bin | N | error_mean | error_std |",
                 "|---|---|---|---|"]
        for s in summary:
            lines.append(f"| {s['label']} | {s['n_samples']:,} | {fmt(s.get('error_mean'))} | {fmt(s.get('error_std'))} |")
        return lines

    def ci_table_md(rows, label):
        lines = [f"### {label} — Bootstrap 95% CIs", "",
                 "| bin | n_groups | n_obs | mean_err | CI_lo | CI_hi | overlaps 0 |",
                 "|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| {r['label']} | {r['n_groups']} | {r['n_obs']:,} "
                f"| {fmt(r['mean_error'])} | {fmt(r['ci_95_lo'])} | {fmt(r['ci_95_hi'])} "
                f"| {'YES' if r['ci_overlaps_zero'] else 'no'} |"
            )
        return lines

    lines = [
        "# §2.4 Metric-Specific Binning + Bootstrap CIs — v2",
        "", "Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C2-C3", "",
        f"> {note}", "",
        "## Fixes", "",
        "- H2: HIGH errors binned by HIGH-cov shortfall; LOW by LOW-cov shortfall",
        "- Structural: shortfall = max(0, floor - cov)  [NO sigma]", "",
        "## LOW Windows Derived", "",
        "| city | source | low_peak_hour |", "|---|---|---|",
    ]
    for c in sorted(low_windows):
        lw = low_windows[c]
        lines.append(f"| {c} | {lw['source']} | {lw.get('low_peak_hour')} |")

    lines += [""]
    lines += bin_table(hs, "HIGH Bins")
    lines += [""]
    lines += bin_table(ls, "LOW Bins")
    lines += ["", "---", "", "## Bootstrap CIs"]
    lines += [""]
    lines += ci_table_md(high_ci, "HIGH")
    lines += [""]
    lines += ci_table_md(low_ci, "LOW")
    lines += ["", "### Adjacent indistinguishable pairs"]

    for label, ci_rows in (("HIGH", high_ci), ("LOW", low_ci)):
        indist = []
        for i in range(len(ci_rows) - 1):
            ci = ci_rows[i]
            nxt = ci_rows[i + 1]
            if (ci["ci_95_lo"] is not None and nxt["mean_error"] is not None and
                    ci["ci_95_lo"] <= nxt["mean_error"] <= ci["ci_95_hi"]):
                indist.append(f"{ci['label']} ↔ {nxt['label']}")
        if indist:
            lines.append(f"**{label} indistinguishable**: {', '.join(indist)}")
        else:
            lines.append(f"**{label}**: no adjacent pairs statistically indistinguishable")

    pm = PHASE1_DIR / "p2_4_v2_curve_breakpoints.md"
    pm.write_text("\n".join(lines) + "\n")
    print(f"  C2/C3: {pj.name}, {pm.name}")


# ---------------------------------------------------------------------------
# C4 — Small sample floor (efficient: aggregate per date in SQL)
# ---------------------------------------------------------------------------

def c4_small_sample_floor(conn: sqlite3.Connection, cities: list[dict]) -> dict:
    """
    Per (city, metric): compute cumulative Brier and ECE over time-ordered unique dates.
    N = number of unique training target_dates processed (not raw rows).
    N* = smallest N where ECE std over 100-date sliding window < 0.02.
    """
    results: dict = {}

    for city_info in cities:
        city = city_info["name"]
        if city in EXCLUDE_CITIES:
            continue

        for metric in ("high", "low"):
            # Aggregate to per-date medians — this reduces 682K rows to ~1000 dates
            rows = conn.execute(
                """
                SELECT target_date,
                       AVG(p_raw * outcome) / NULLIF(AVG(outcome), 0) AS p_win,
                       AVG(outcome) AS outcome_rate,
                       COUNT(DISTINCT pair_id) AS n_pairs
                FROM calibration_pairs_v2
                WHERE city = ? AND temperature_metric = ?
                  AND authority = 'VERIFIED' AND training_allowed = 1
                GROUP BY target_date
                ORDER BY target_date ASC
                """,
                (city, metric),
            ).fetchall()

            if len(rows) < 50:
                results[f"{city}_{metric}"] = {
                    "city": city, "metric": metric,
                    "total_N": len(rows), "N_star": None,
                    "status": "INSUFFICIENT_DATA",
                }
                continue

            # Build cumulative Brier and ECE series.
            # ECE computed incrementally (O(1) per step via bin accumulators)
            # at every date to get enough points for the 100-date sliding window.
            ECE_BINS = 10
            ECE_WINDOW = 100
            cum_ece: list[float] = []
            brier_sum = 0.0
            total_n = 0
            # Incremental bin accumulators: [sum_p, sum_o, count] per bin
            bin_acc: list[list] = [[0.0, 0.0, 0] for _ in range(ECE_BINS)]

            for i, (td, p_win, out_rate, n_pairs) in enumerate(rows):
                p = p_win if p_win is not None else 0.5
                o = out_rate if out_rate is not None else 0.0
                brier_sum += (p - o) ** 2
                total_n += 1
                b = min(int(p * ECE_BINS), ECE_BINS - 1)
                bin_acc[b][0] += p
                bin_acc[b][1] += o
                bin_acc[b][2] += 1
                if total_n >= 10:
                    ece = sum(
                        acc[2] / total_n * abs(acc[0]/acc[2] - acc[1]/acc[2])
                        for acc in bin_acc if acc[2] > 0
                    )
                    cum_ece.append(ece)

            total_N = len(rows)
            final_brier = brier_sum / total_N if total_N > 0 else None

            # N*: smallest cumulative N where ECE stdev over 100-date window < 0.02
            N_star = None
            if len(cum_ece) >= ECE_WINDOW:
                for i in range(len(cum_ece) - ECE_WINDOW + 1):
                    w = cum_ece[i:i + ECE_WINDOW]
                    if statistics.stdev(w) < 0.02:
                        N_star = i + ECE_WINDOW + 10  # +10 offset: ECE starts at N=10
                        break

            key = f"{city}_{metric}"
            results[key] = {
                "city": city,
                "metric": metric,
                "total_N_dates": total_N,
                "N_star": N_star,
                "final_brier": round(final_brier, 6),
                "final_ece": round(cum_ece[-1], 6) if cum_ece else None,
                "status": "OK" if N_star is not None else "N_STAR_NOT_FOUND",
            }

    return results


def _ece(probs: list[float], outcomes: list[float], n_bins: int = 10) -> float:
    n = len(probs)
    if n == 0:
        return 0.0
    bins: list[list] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        b = min(int(p * n_bins), n_bins - 1)
        bins[b].append((p, o))
    ece = 0.0
    for b_list in bins:
        if not b_list:
            continue
        conf = sum(p for p, o in b_list) / len(b_list)
        acc  = sum(o for p, o in b_list) / len(b_list)
        ece += len(b_list) / n * abs(conf - acc)
    return ece


def write_c4(c4: dict) -> None:
    note = "Paris pending workstream A resync; rerun for Paris after A completes."
    out = {
        "_metadata": {
            "created": "2026-05-03",
            "authority": "RERUN_PLAN_v2.md C4",
            "N_unit": "unique training target_dates",
            "N_star": "smallest N where ECE std over 100-date sliding window < 0.02",
            "paris_note": note,
        },
        "per_city_metric": c4,
    }
    pj = PHASE1_DIR / "p2_5_small_sample_floor.json"
    pj.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    lines = [
        "# §2.5 Small Sample Floor — v2",
        "", "Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C4", "",
        f"> {note}", "",
        "## Definition", "",
        "N = unique training target_dates (not raw pair rows).",
        "N* = smallest N where ECE std over a 100-date sliding window < 0.02.",
        "When N < N*: force DDD multiplier = curve_max (0.91× Kelly).", "",
        "## Results", "",
        "| city | metric | total_N_dates | N_star | final_brier | final_ece | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for key in sorted(c4):
        r = c4[key]
        nstar = str(r['N_star']) if r['N_star'] is not None else "not found"
        lines.append(
            f"| {r['city']} | {r['metric']} | {r['total_N_dates']:,} "
            f"| {nstar} | {fmt(r.get('final_brier'))} | {fmt(r.get('final_ece'))} | {r['status']} |"
        )

    pm = PHASE1_DIR / "p2_5_small_sample_floor.md"
    pm.write_text("\n".join(lines) + "\n")
    print(f"  C4: {pj.name}, {pm.name}")


# ---------------------------------------------------------------------------
# C5 — Peak window radius
# ---------------------------------------------------------------------------

def c5_peak_window_radius(conn, cities, low_windows) -> dict:
    """Miss rate for HIGH (running_max hour) and LOW (running_min hour) vs ±3, ±4, ±5.
    Uses a single aggregated SQL query per (city, metric) for efficiency.
    """
    results: dict = {}

    for city_info in cities:
        city = city_info["name"]
        if city in EXCLUDE_CITIES:
            continue

        high_peak = city_info.get("historical_peak_hour")
        low_info  = low_windows.get(city, {})
        low_peak  = low_info.get("low_peak_hour")

        for metric, peak_h, agg_fn, val_col in [
            ("high", high_peak, "MAX", "running_max"),
            ("low",  low_peak,  "MIN", "running_min"),
        ]:
            if peak_h is None:
                continue

            # Single query: for each date, find the earliest hour achieving the day's extremum.
            # Return (target_date, achieving_hour).
            date_hr_rows = conn.execute(
                f"""
                WITH day_exts AS (
                    SELECT target_date, {agg_fn}({val_col}) AS day_ext
                    FROM observation_instants_v2
                    WHERE city = ? AND source = 'wu_icao_history'
                      AND data_version = 'v1.wu-native'
                      AND {val_col} IS NOT NULL
                    GROUP BY target_date
                )
                SELECT d.target_date, MIN(CAST(o.local_hour AS INTEGER)) AS achieving_hour
                FROM observation_instants_v2 o
                JOIN day_exts d ON o.target_date = d.target_date
                               AND o.{val_col} = d.day_ext
                WHERE o.city = ? AND o.source = 'wu_icao_history'
                  AND o.data_version = 'v1.wu-native'
                  AND o.local_hour IS NOT NULL
                GROUP BY d.target_date
                """,
                (city, city),
            ).fetchall()

            if not date_hr_rows:
                continue

            date_peak_hr = {td: hr for td, hr in date_hr_rows if hr is not None}

            for season_name, s_months in SEASONS.items():
                s_dates = [td for td in date_peak_hr
                           if date.fromisoformat(td).month in s_months]
                if len(s_dates) < 10:
                    continue

                key = f"{city}_{metric}_{season_name}"
                results[key] = {
                    "city": city, "metric": metric, "season": season_name,
                    "n_days": len(s_dates), "peak_hour_used": peak_h,
                    "miss_rate_r3": None, "miss_rate_r4": None, "miss_rate_r5": None,
                    "recommended_radius": 3,
                }

                for radius in (3, 4, 5):
                    window = set(directional_window(float(peak_h), radius))
                    miss = sum(1 for td in s_dates if date_peak_hr[td] not in window)
                    results[key][f"miss_rate_r{radius}"] = round(miss / len(s_dates), 4)

                if results[key]["miss_rate_r3"] <= 0.05:
                    results[key]["recommended_radius"] = 3
                elif results[key]["miss_rate_r4"] <= 0.05:
                    results[key]["recommended_radius"] = 4
                elif results[key]["miss_rate_r5"] <= 0.05:
                    results[key]["recommended_radius"] = 5
                else:
                    results[key]["recommended_radius"] = "expand_beyond_5"

    return results


def write_c5(c5: dict) -> None:
    note = "Paris pending workstream A resync; rerun for Paris after A completes."
    out = {
        "_metadata": {
            "created": "2026-05-03",
            "authority": "RERUN_PLAN_v2.md C5",
            "miss_threshold": "> 5% triggers expanded radius",
            "paris_note": note,
        },
        "per_city_metric_season": c5,
    }
    pj = PHASE1_DIR / "p2_6_peak_window_radius.json"
    pj.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    lines = [
        "# §2.6 Peak Window Radius — v2",
        "", "Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C5", "",
        f"> {note}", "",
        "## Definition", "",
        "Miss rate = fraction of days where achieved-extremum hour fell OUTSIDE peak ± radius.",
        "Threshold: > 5% miss rate at ± 3 → try ± 4, ± 5.", "",
        "## Results", "",
        "| city | metric | season | n_days | miss_r3 | miss_r4 | miss_r5 | rec_radius |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(c5):
        r = c5[key]
        lines.append(
            f"| {r['city']} | {r['metric']} | {r['season']} | {r['n_days']} "
            f"| {fmt(r.get('miss_rate_r3'))} | {fmt(r.get('miss_rate_r4'))} "
            f"| {fmt(r.get('miss_rate_r5'))} | {r['recommended_radius']} |"
        )

    need = {k: v for k, v in c5.items()
            if v.get("miss_rate_r3") is not None and v["miss_rate_r3"] > 0.05}
    if need:
        lines += ["", "## Entries Needing Expanded Radius", "",
                  "| key | miss_r3 | recommended_radius |", "|---|---|---|"]
        for k, v in sorted(need.items()):
            lines.append(f"| {k} | {fmt(v['miss_rate_r3'])} | {v['recommended_radius']} |")
    else:
        lines += ["", "**All entries within 5% miss rate at ± 3. No expansion needed.**"]

    pm = PHASE1_DIR / "p2_6_peak_window_radius.md"
    pm.write_text("\n".join(lines) + "\n")
    print(f"  C5: {pj.name}, {pm.name}")


# ---------------------------------------------------------------------------
# Master summary
# ---------------------------------------------------------------------------

def write_summary(c1, high_g, low_g, high_ci, low_ci, c4, c5) -> None:
    note = "Paris pending workstream A resync; rerun for Paris after A completes."
    h1 = json.loads(H1_JSON.read_text())
    hs = summarize_bins(high_g)
    ls = summarize_bins(low_g)

    active_c1 = {c: v for c, v in c1.items()
                 if v.get("status") not in ("NO_TRAIN_DATA", "EXCLUDED_WORKSTREAM_A")}
    policy_cities = sorted(c for c in active_c1 if active_c1[c].get("policy_override") is not None)
    n_nstar = sum(1 for r in c4.values() if r.get("N_star") is not None)
    n_expand = sum(1 for v in c5.values()
                   if v.get("miss_rate_r3") is not None and v["miss_rate_r3"] > 0.05)

    lines = [
        "# Phase 1 v2 — Final Summary",
        "", "Created: 2026-05-03  Authority: RERUN_PLAN_v2.md", "",
        f"> **Paris excluded**: {note}", "",
        "## Structural Change (Operator Decision 2026-05-03)", "",
        "```",
        "OLD: fire if cov < floor - sigma_90",
        "NEW: fire if cov < floor",
        "sigma → diagnostic only (logged, not in trigger or floor selection)",
        "```", "",
        "---", "", "## C1 — Floor Reselection Headline", "",
        f"- {len(active_c1)} cities processed",
        f"- {len(policy_cities)} policy overrides: {', '.join(policy_cities)}",
        "- Safety minimum floor: 0.35", "",
        "### Floors changed vs H1 σ-aware values (|Δ| ≥ 0.01)", "",
        "| city | H1 floor (σ-aware) | v2 floor (p05) | Δ |",
        "|---|---|---|---|",
    ]
    for city in sorted(active_c1):
        v2f = active_c1[city].get("final_floor")
        h1c = h1["per_city"].get(city, {})
        h1f = h1c.get("new_floor") if h1c.get("status") != "NO_TRAIN_DATA" else None
        if h1f is not None and v2f is not None and abs(v2f - h1f) >= 0.01:
            d = v2f - h1f
            lines.append(f"| {city} | {fmt(h1f)} | {fmt(v2f)} | {'+' if d>0 else ''}{d:.2f} |")

    lines += [
        "", "---", "", "## C2 — Metric-Specific Binning Headline", "",
        "### HIGH bins", "", "| bin | N | mean_error |", "|---|---|---|",
    ]
    for s in hs:
        lines.append(f"| {s['label']} | {s['n_samples']:,} | {fmt(s.get('error_mean'))} |")
    lines += ["", "### LOW bins", "", "| bin | N | mean_error |", "|---|---|---|"]
    for s in ls:
        lines.append(f"| {s['label']} | {s['n_samples']:,} | {fmt(s.get('error_mean'))} |")

    lines += [
        "", "---", "", "## C3 — Bootstrap CIs Headline", "",
        f"Bootstrap: {BOOTSTRAP_ITERS} iterations, resampling unit = decision_group_id", "",
    ]
    for label, ci in (("HIGH", high_ci), ("LOW", low_ci)):
        ind = sum(1 for i in range(len(ci) - 1)
                  if ci[i]["ci_95_lo"] is not None and ci[i+1]["mean_error"] is not None
                  and ci[i]["ci_95_lo"] <= ci[i+1]["mean_error"] <= ci[i]["ci_95_hi"])
        lines.append(f"- **{label}**: {ind} adjacent pairs statistically indistinguishable")

    lines += [
        "", "---", "", "## C4 — Small Sample Floor Headline", "",
        f"- {n_nstar}/{len(c4)} (city, metric) pairs: N* identified",
        "- When N < N*: DDD multiplier forced to curve_max (0.91× Kelly)", "",
        "---", "", "## C5 — Peak Window Radius Headline", "",
        f"- {n_expand} (city, metric, season) entries need expanded radius (miss > 5% at ±3)", "",
        "---", "", "## Acceptance Gate Status", "",
        "| Gate | Status | Evidence |",
        "|---|---|---|",
        f"| C1: floors use p05 not σ-aware | CLOSED | {len(active_c1)} cities |",
        "| C2: metric-specific cov | CLOSED | HIGH + LOW separate |",
        f"| C3: bootstrap on decision_group_id | CLOSED | {BOOTSTRAP_ITERS} iters |",
        f"| C4: small_sample_floor | CLOSED | N* found {n_nstar}/{len(c4)} |",
        f"| C5: peak_window radius | CLOSED | {n_expand} expansions needed |",
        "| Paris | OPEN | Pending workstream A |", "",
        "---", "", "## Remaining Open Items", "",
        "1. **Paris**: re-run C1-C5 after workstream A completes.",
        "2. **No-train-data cities** (HK, Istanbul, Moscow, Tel Aviv): fail-CLOSED when DDD wired live.",
        "3. **H7** (ACF lag mismatch): σ diagnostic-only now; low priority.",
        "4. **H4** (load_platt_model_v2 frozen filter): forward-fix in v2 live wiring.", "",
        "---", "", "## Next Actions", "",
        "1. Wire C1 floors into DDD live trigger (`cov < final_floor`, no σ).",
        "2. Wire C4 small_sample_floor: gate discount when N < N*.",
        "3. Apply C5 radius expansions for flagged entries.",
        "4. Add σ to diagnostic dashboard (log but don't use in trigger).",
        "5. Re-run after Paris workstream A completes.",
    ]

    pm = PHASE1_DIR / "PHASE1_V2_FINAL_SUMMARY.md"
    pm.write_text("\n".join(lines) + "\n")
    print(f"  Summary: {pm.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    PHASE1_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading config...")
    cities = json.loads(CITIES_JSON.read_text())["cities"]
    h1_data = json.loads(H1_JSON.read_text())

    print(f"C1 — Floor reselection ({len(cities)} cities, excluding Paris)...")
    conn = open_db()
    try:
        c1 = c1_floor_reselection(h1_data, cities, conn)
    finally:
        conn.close()
    write_c1(c1)

    print("C2 — Deriving LOW windows...")
    conn = open_db()
    try:
        low_windows = derive_low_windows(conn, cities)
    finally:
        conn.close()
    print(f"  LOW windows derived for {len(low_windows)} cities")

    print("C2 — Metric-specific binning (H2 + structural fix)...")
    conn = open_db()
    try:
        high_g, low_g, _, _ = c2_metric_binning(conn, cities, c1, low_windows)
    finally:
        conn.close()

    print("C3 — Bootstrap CIs...")
    conn = open_db()
    try:
        high_ci, low_ci = c3_bootstrap(conn, cities, c1, low_windows)
    finally:
        conn.close()
    write_c2_c3(high_g, low_g, high_ci, low_ci, low_windows)

    print("C4 — Small sample floor (per-date aggregation)...")
    conn = open_db()
    try:
        c4 = c4_small_sample_floor(conn, cities)
    finally:
        conn.close()
    write_c4(c4)

    print("C5 — Peak window radius...")
    conn = open_db()
    try:
        c5 = c5_peak_window_radius(conn, cities, low_windows)
    finally:
        conn.close()
    write_c5(c5)

    print("Writing master summary...")
    write_summary(c1, high_g, low_g, high_ci, low_ci, c4, c5)

    outputs = [
        "phase1_results/p2_1_FINAL_v2_per_city_floors.json",
        "phase1_results/p2_1_FINAL_v2_per_city_floors.md",
        "phase1_results/p2_4_v2_curve_breakpoints.json",
        "phase1_results/p2_4_v2_curve_breakpoints.md",
        "phase1_results/p2_5_small_sample_floor.json",
        "phase1_results/p2_5_small_sample_floor.md",
        "phase1_results/p2_6_peak_window_radius.json",
        "phase1_results/p2_6_peak_window_radius.md",
        "phase1_results/PHASE1_V2_FINAL_SUMMARY.md",
    ]
    print("\nDONE: " + ", ".join(outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
