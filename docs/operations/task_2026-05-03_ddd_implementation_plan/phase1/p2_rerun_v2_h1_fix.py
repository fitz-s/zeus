"""PHASE 1 rerun — H1 denominator fix (HIGH metric only).

Created: 2026-05-03
Authority: operator adversarial review (review2.md §3.1, §13) — denominator defect:
           per_day_coverage() returns only rows WHERE observations exist, so zero-row
           days are absent from result dict → caller `cov_full.get(d_iso)` returns None
           → `continue` skips them instead of counting as coverage=0/7.

Fix: enumerate every calendar date in the window via Python date_iter;
     for each (city, date) query DB for observed count; zero-row → 0.0.

Scope: HIGH metric only (LOW window definition is a separate fix).

Outputs:
  phase1_results/p2_rerun_v2_h1_fix.json
  phase1_results/p2_rerun_v2_h1_fix.md

Run from repo root: .venv/bin/python <path-to-this-file>
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PHASE1_RESULTS = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"
DB_PATH = REPO / "state" / "zeus-world.db"
CITIES_JSON = REPO / "config" / "cities.json"
ORIG_FLOORS_JSON = PHASE1_RESULTS / "p2_1_FINAL_per_city_floors.json"
ORIG_STATS_JSON = PHASE1_RESULTS / "p2_1_per_city_coverage_stats.json"

TRAIN_START = "2025-07-01"
TRAIN_END = "2025-12-31"
TEST_START = "2026-01-01"
TEST_END = "2026-04-30"
WINDOW_RADIUS = 3
SIGMA_WINDOW = 90

# Floor recommendation parameters — same as p2_1c
CANDIDATE_FLOORS = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
ABSOLUTE_PHYSICS_FLOOR = 0.35
UPPER_CAP = 0.85
TARGET_FP_PCT = 1.0

# Shortfall bins — same as p2_4
BIN_EDGES = [0.0, 0.001, 0.05, 0.10, 0.20, 0.30, 0.50, 2.0]
BIN_LABELS = [
    "exact 0",
    "(0, 0.05)",
    "[0.05, 0.10)",
    "[0.10, 0.20)",
    "[0.20, 0.30)",
    "[0.30, 0.50)",
    "[0.50, inf)",
]

DEFAULT_HARD_FLOOR = 0.85


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def directional_window(peak: float | None, radius: int = WINDOW_RADIUS) -> list[int]:
    if peak is None:
        return list(range(24))
    c = int(round(peak))
    return [(h % 24) for h in range(c - radius, c + radius + 1)]


def date_iter(start: str, end: str):
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def calendar_count(start: str, end: str) -> int:
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return (e - d).days + 1


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


def per_day_coverage_observed(
    conn: sqlite3.Connection,
    city: str,
    target_hours: list[int],
    start: str,
    end: str,
) -> dict[str, float]:
    """Return {target_date: cov} for dates that HAVE observations.
    Zero-coverage dates are absent from the dict — caller must enumerate
    calendar to add them as 0.0.
    """
    n_target = len(target_hours)
    target_in = ",".join(str(h) for h in target_hours)
    rows = conn.execute(
        f"""
        SELECT target_date,
               COUNT(DISTINCT CAST(local_hour AS INTEGER)) AS hrs
        FROM observation_instants_v2
        WHERE city = ? AND source = 'wu_icao_history'
          AND data_version = 'v1.wu-native'
          AND target_date >= ? AND target_date <= ?
          AND CAST(local_hour AS INTEGER) IN ({target_in})
        GROUP BY target_date
        """,
        (city, start, end),
    ).fetchall()
    return {d_iso: hrs / n_target for d_iso, hrs in rows}


def per_day_coverage_full(
    conn: sqlite3.Connection,
    city: str,
    target_hours: list[int],
    start: str,
    end: str,
) -> dict[str, float]:
    """H1 FIX: Return {target_date: cov} for EVERY calendar date in [start, end].
    Dates with zero observations → cov = 0.0.
    """
    observed = per_day_coverage_observed(conn, city, target_hours, start, end)
    out: dict[str, float] = {}
    for d_iso in date_iter(start, end):
        out[d_iso] = observed.get(d_iso, 0.0)
    return out


def sigma_aware_fp_rate(values: list[float], floor: float, sigma: float) -> float:
    if not values:
        return 0.0
    threshold = floor - sigma
    return sum(1 for v in values if v < threshold) / len(values)


def recommend_floor(train_vals: list[float]) -> tuple[float, float, str]:
    """σ-aware floor recommendation — same algorithm as p2_1c."""
    if not train_vals:
        return (ABSOLUTE_PHYSICS_FLOOR, 0.0, "no train data → physics floor")
    if len(train_vals) == 1:
        return (ABSOLUTE_PHYSICS_FLOOR, 0.0, "n=1 → physics floor")

    sigma = statistics.stdev(train_vals)
    best_floor = ABSOLUTE_PHYSICS_FLOOR
    for f in CANDIDATE_FLOORS:
        fp_pct = sigma_aware_fp_rate(train_vals, f, sigma) * 100.0
        if fp_pct <= TARGET_FP_PCT:
            best_floor = f
        else:
            break
    capped = min(best_floor, UPPER_CAP)
    actual_fp = sigma_aware_fp_rate(train_vals, capped, sigma) * 100.0
    return (
        capped,
        sigma,
        f"σ-aware: σ_train={sigma:.3f}, best_floor={best_floor:.2f}, "
        f"capped={capped:.2f}, actual_σ_fp={actual_fp:.2f}%",
    )


def bin_index(value: float) -> int:
    for i in range(len(BIN_EDGES) - 1):
        if BIN_EDGES[i] <= value < BIN_EDGES[i + 1]:
            return i
    return len(BIN_EDGES) - 2


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def main() -> int:
    PHASE1_RESULTS.mkdir(parents=True, exist_ok=True)

    with open(CITIES_JSON) as f:
        cities_d = json.load(f)
    cities = cities_d["cities"]
    city_by_name = {c["name"]: c for c in cities}

    orig_stats = json.loads(ORIG_STATS_JSON.read_text())
    orig_floors_data = json.loads(ORIG_FLOORS_JSON.read_text())
    orig_floors: dict[str, float] = orig_floors_data["per_city_floors"]

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    n_cal_train = calendar_count(TRAIN_START, TRAIN_END)  # 184
    n_cal_test = calendar_count(TEST_START, TEST_END)      # 120

    # -----------------------------------------------------------------------
    # Pass 1: per-city coverage stats + floor recommendations
    # -----------------------------------------------------------------------
    city_results: dict[str, dict] = {}

    for city_info in cities:
        city = city_info["name"]
        peak = city_info.get("historical_peak_hour")
        target_hours = directional_window(peak)

        # Original stats (pre-fix) for comparison
        orig = orig_stats.get(city, {})
        if orig.get("status") == "NO_TRAIN_DATA":
            city_results[city] = {"status": "NO_TRAIN_DATA", "peak_hour": peak}
            continue

        # Fixed: full calendar enumeration
        train_cov = per_day_coverage_full(conn, city, target_hours, TRAIN_START, TRAIN_END)
        test_cov = per_day_coverage_full(conn, city, target_hours, TEST_START, TEST_END)

        train_vals = list(train_cov.values())
        test_vals = list(test_cov.values())

        n_obs_train = sum(1 for v in train_vals if v > 0)
        n_obs_test = sum(1 for v in test_vals if v > 0)
        n_zero_train = sum(1 for v in train_vals if v == 0)
        n_zero_test = sum(1 for v in test_vals if v == 0)

        floor_new, sigma_new, rationale = recommend_floor(train_vals)

        # Original floor recommendation from p2_1c
        orig_floor = orig_floors.get(city, DEFAULT_HARD_FLOOR)

        city_results[city] = {
            "peak_hour": peak,
            "window_hours": target_hours,
            # ---- train ----
            "train": {
                "n_calendar_days": n_cal_train,
                "n_observed_days": n_obs_train,
                "n_zero_days": n_zero_train,
                "min": min(train_vals),
                "p05": percentile(train_vals, 5),
                "mean": statistics.mean(train_vals),
                "median": percentile(train_vals, 50),
                "p95": percentile(train_vals, 95),
                "sigma": sigma_new,
            },
            # ---- test ----
            "test": {
                "n_calendar_days": n_cal_test,
                "n_observed_days": n_obs_test,
                "n_zero_days": n_zero_test,
                "min": min(test_vals),
                "p05": percentile(test_vals, 5),
                "mean": statistics.mean(test_vals),
                "median": percentile(test_vals, 50),
                "p95": percentile(test_vals, 95),
            },
            # ---- floor ----
            "orig_floor": orig_floor,
            "new_floor": floor_new,
            "new_sigma": sigma_new,
            "floor_delta": floor_new - (orig_floor if orig_floor is not None else DEFAULT_HARD_FLOOR),
            "rationale": rationale,
            # ---- original sigma from p2_1c for delta ----
            "orig_sigma": orig.get("train", {}).get("stddev") if isinstance(orig.get("train"), dict) else None,
        }

    # -----------------------------------------------------------------------
    # Pass 2: §2.4 shortfall×error binning (HIGH metric only, fixed coverage)
    # -----------------------------------------------------------------------
    bin_data: dict[int, list[float]] = defaultdict(list)
    bin_data_by_city: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    p24_cities_processed = 0

    for city, cr in city_results.items():
        if cr.get("status") == "NO_TRAIN_DATA":
            continue
        peak = cr["peak_hour"]
        if peak is None:
            continue
        target_hours = cr["window_hours"]

        floor = orig_floors.get(city)
        if floor is None:
            continue  # no-data cities

        # Need full coverage history including 90-day pre-test seed
        history_start = (date.fromisoformat(TEST_START) - timedelta(days=90)).isoformat()
        # Build full-calendar coverage dict for history + test
        cov_hist_test = per_day_coverage_full(
            conn, city, target_hours, history_start, TEST_END
        )

        # Calibration pairs for HIGH metric only in test window
        cal_rows = conn.execute(
            """
            SELECT target_date, p_raw
            FROM calibration_pairs_v2
            WHERE city = ? AND authority = 'VERIFIED' AND outcome = 1
              AND temperature_metric = 'high'
              AND target_date >= ? AND target_date <= ?
            """,
            (city, TEST_START, TEST_END),
        ).fetchall()
        per_day_p: dict[str, list[float]] = defaultdict(list)
        for d_iso, p in cal_rows:
            per_day_p[d_iso].append(p)

        for d_iso in date_iter(TEST_START, TEST_END):
            cov = cov_hist_test.get(d_iso, 0.0)

            # σ_90: stddev over [D-90, D-1] — full calendar (H1 fix applies here too)
            target_dt = date.fromisoformat(d_iso)
            w_start = (target_dt - timedelta(days=90)).isoformat()
            w_end = (target_dt - timedelta(days=1)).isoformat()
            window_vals = [v for d, v in cov_hist_test.items() if w_start <= d <= w_end]

            if len(window_vals) < 30:
                continue
            sigma = statistics.stdev(window_vals) if len(window_vals) > 1 else 0.0
            shortfall = max(0.0, floor - cov - sigma)
            b = bin_index(shortfall)

            p_vals = per_day_p.get(d_iso, [])
            if not p_vals:
                continue
            p_repr = statistics.median(p_vals)
            error = (1.0 - p_repr) ** 2
            bin_data[b].append(error)
            bin_data_by_city[city][b].append(error)

        p24_cities_processed += 1

    conn.close()

    # Summarize §2.4 bins
    p24_summary = []
    for i in range(len(BIN_EDGES) - 1):
        errs = bin_data[i]
        p24_summary.append({
            "bin_idx": i,
            "label": BIN_LABELS[i],
            "shortfall_lo": BIN_EDGES[i],
            "shortfall_hi": BIN_EDGES[i + 1],
            "n_samples": len(errs),
            "error_mean": statistics.mean(errs) if errs else None,
            "error_std": statistics.stdev(errs) if len(errs) > 1 else None,
        })

    # -----------------------------------------------------------------------
    # Assemble output JSON
    # -----------------------------------------------------------------------
    out = {
        "_metadata": {
            "purpose": "H1 denominator fix rerun: zero-coverage days included via calendar enumeration",
            "produced_at": "2026-05-03",
            "fix": "H1 — every calendar date contributes; zero-row days = coverage 0.0, not skipped",
            "scope": "HIGH metric only (LOW is a separate fix)",
            "train_window": f"{TRAIN_START} → {TRAIN_END}",
            "test_window": f"{TEST_START} → {TEST_END}",
            "n_calendar_train": n_cal_train,
            "n_calendar_test": n_cal_test,
        },
        "per_city": city_results,
        "p24_curve_breakpoints_high_only": {
            "cities_processed": p24_cities_processed,
            "bin_edges": BIN_EDGES,
            "bin_labels": BIN_LABELS,
            "global_summary": p24_summary,
        },
    }

    json_path = PHASE1_RESULTS / "p2_rerun_v2_h1_fix.json"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # -----------------------------------------------------------------------
    # Markdown report
    # -----------------------------------------------------------------------
    lines: list[str] = []
    lines.append("# Phase 1 — H1 Denominator Fix Rerun (HIGH metric only)")
    lines.append("")
    lines.append("Created: 2026-05-03")
    lines.append("Authority: operator adversarial review (review2.md §3.1, §13)")
    lines.append("")
    lines.append("## What was fixed")
    lines.append("")
    lines.append("The original §2.1/§2.3/§2.4 scripts called `per_day_coverage()` which")
    lines.append("returns only dates WHERE observations exist (GROUP BY target_date).")
    lines.append("Days with zero observations are absent from the result dict.")
    lines.append("Caller code then does `cov_full.get(d_iso)` → `None` → `continue`,")
    lines.append("silently excluding zero-coverage days from all downstream analysis.")
    lines.append("")
    lines.append("**H1 fix**: enumerate every calendar date; zero-row days → coverage 0.0.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Per-city coverage delta: original → fixed")
    lines.append("")

    def fmt(v) -> str:
        if v is None:
            return "n/a"
        return f"{v:.4f}"

    # Table for train window
    lines.append("### Train window coverage (2025-07-01 → 2025-12-31)")
    lines.append("")
    lines.append(
        "| city | n_calendar | n_observed | n_zero | min | p05 | mean | median | p95 | orig_n_days |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    active_cities = [
        (name, r) for name, r in city_results.items()
        if r.get("status") != "NO_TRAIN_DATA"
    ]
    active_cities.sort(key=lambda kv: kv[1]["train"]["n_zero_days"], reverse=True)

    for name, r in active_cities:
        t = r["train"]
        orig = orig_stats.get(name, {})
        orig_n = orig.get("train", {}).get("n_days", "n/a") if isinstance(orig.get("train"), dict) else "n/a"
        lines.append(
            f"| {name} | {t['n_calendar_days']} | {t['n_observed_days']} | "
            f"{t['n_zero_days']} | {fmt(t['min'])} | {fmt(t['p05'])} | "
            f"{fmt(t['mean'])} | {fmt(t['median'])} | {fmt(t['p95'])} | {orig_n} |"
        )
    lines.append("")

    lines.append("### Test window coverage (2026-01-01 → 2026-04-30)")
    lines.append("")
    lines.append(
        "| city | n_calendar | n_observed | n_zero | min | p05 | mean | median | p95 | orig_n_days |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    active_test = sorted(active_cities, key=lambda kv: kv[1]["test"]["n_zero_days"], reverse=True)
    for name, r in active_test:
        te = r["test"]
        orig = orig_stats.get(name, {})
        orig_n = orig.get("test", {}).get("n_days", "n/a") if isinstance(orig.get("test"), dict) else "n/a"
        lines.append(
            f"| {name} | {te['n_calendar_days']} | {te['n_observed_days']} | "
            f"{te['n_zero_days']} | {fmt(te['min'])} | {fmt(te['p05'])} | "
            f"{fmt(te['mean'])} | {fmt(te['median'])} | {fmt(te['p95'])} | {orig_n} |"
        )
    lines.append("")

    # σ_90 delta
    lines.append("---")
    lines.append("")
    lines.append("## Per-city σ_train delta (original → H1-fixed)")
    lines.append("")
    lines.append("σ is computed over training window coverage values.")
    lines.append("With zero-days included, σ should increase for cities with gaps.")
    lines.append("")
    lines.append("| city | orig_σ_train (obs-only) | new_σ_train (all calendar) | delta |")
    lines.append("|---|---|---|---|")

    sigma_sorted = sorted(
        active_cities,
        key=lambda kv: (kv[1].get("orig_sigma") or 0) - kv[1]["train"]["sigma"],
    )
    for name, r in sigma_sorted:
        orig_s = r.get("orig_sigma")
        new_s = r["train"]["sigma"]
        delta_s = (new_s - orig_s) if orig_s is not None else None
        delta_str = f"+{delta_s:.4f}" if delta_s is not None and delta_s > 0 else (f"{delta_s:.4f}" if delta_s is not None else "n/a")
        lines.append(
            f"| {name} | {fmt(orig_s)} | {fmt(new_s)} | {delta_str} |"
        )
    lines.append("")

    # Floor delta
    lines.append("---")
    lines.append("")
    lines.append("## Floor recommendation delta (≥0.05 movers only)")
    lines.append("")
    lines.append(
        "Floor algorithm: same σ-aware logic as p2_1c "
        "(fire if cov < floor - σ_train; FP ≤ 1% target; cap at 0.85)."
    )
    lines.append("")

    floor_movers = [
        (name, r) for name, r in active_cities
        if abs(r["floor_delta"]) >= 0.05
    ]
    floor_movers.sort(key=lambda kv: abs(kv[1]["floor_delta"]), reverse=True)

    if floor_movers:
        lines.append("| city | orig_floor (p2_1_FINAL) | new_floor (H1-fixed) | delta |")
        lines.append("|---|---|---|---|")
        for name, r in floor_movers:
            delta = r["floor_delta"]
            delta_str = f"+{delta:.2f}" if delta > 0 else f"{delta:.2f}"
            lines.append(
                f"| {name} | {fmt(r['orig_floor'])} | {fmt(r['new_floor'])} | {delta_str} |"
            )
    else:
        lines.append("**No cities with floor movement ≥ 0.05.**")
    lines.append("")

    # §2.4 binning
    lines.append("---")
    lines.append("")
    lines.append("## §2.4 Shortfall×Error binning re-run (HIGH metric only)")
    lines.append("")
    lines.append(
        f"Cities processed: {p24_cities_processed}. "
        "σ_90 computed over full-calendar coverage (H1 fix). "
        "Shortfall = max(0, floor[city] - cov - σ_90)."
    )
    lines.append("")
    lines.append("| shortfall bin | N samples | error_mean | error_std |")
    lines.append("|---|---|---|---|")
    for s in p24_summary:
        em = fmt(s["error_mean"]) if s["error_mean"] is not None else "n/a"
        es = fmt(s["error_std"]) if s["error_std"] is not None else "n/a"
        lines.append(f"| {s['label']} | {s['n_samples']:,} | {em} | {es} |")
    lines.append("")

    means = [s["error_mean"] for s in p24_summary if s["error_mean"] is not None and s["n_samples"] >= 10]
    monotone = all(means[i] <= means[i + 1] + 0.05 for i in range(len(means) - 1)) if len(means) >= 2 else False
    spread = (means[-1] - means[0]) if len(means) >= 2 else 0
    lines.append(f"Monotone (±0.05 tolerance): {monotone}. Spread (high-low bin mean): {spread:.4f}")
    lines.append("")

    # Headline summary
    lines.append("---")
    lines.append("")
    lines.append("## Headline summary")
    lines.append("")

    # Floors moved
    n_moved = len(floor_movers)
    lines.append(f"**{n_moved} cities' floors move by ≥0.05** with the H1 fix.")
    if floor_movers:
        for name, r in floor_movers:
            lines.append(f"- {name}: {fmt(r['orig_floor'])} → {fmt(r['new_floor'])} (Δ{r['floor_delta']:+.2f})")
    lines.append("")

    # Lagos σ
    lagos = city_results.get("Lagos")
    if lagos:
        orig_lagos_sigma = lagos.get("orig_sigma")
        new_lagos_sigma = lagos["train"]["sigma"]
        lines.append(
            f"**Lagos σ_train**: {fmt(orig_lagos_sigma)} → {fmt(new_lagos_sigma)} "
            f"(zero_train={lagos['train']['n_zero_days']}, zero_test={lagos['test']['n_zero_days']})"
        )
        lines.append(
            f"Lagos floor: {fmt(lagos['orig_floor'])} → {fmt(lagos['new_floor'])} "
            f"(delta={lagos['floor_delta']:+.2f})"
        )
    lines.append("")

    # Key infrastructure cities
    lines.append("### Key infrastructure cities")
    for cn in ["Shenzhen", "Lucknow", "Jakarta"]:
        r = city_results.get(cn)
        if r and r.get("status") != "NO_TRAIN_DATA":
            orig_s = r.get("orig_sigma")
            new_s = r["train"]["sigma"]
            lines.append(
                f"- {cn}: σ {fmt(orig_s)} → {fmt(new_s)}, "
                f"floor {fmt(r['orig_floor'])} → {fmt(r['new_floor'])} "
                f"(Δ{r['floor_delta']:+.2f})"
            )
    lines.append("")

    # Denver (had 4 zero train days)
    denver = city_results.get("Denver")
    if denver and denver.get("status") != "NO_TRAIN_DATA":
        lines.append(
            f"**Denver**: {denver['train']['n_zero_days']} zero train days revealed; "
            f"σ {fmt(denver.get('orig_sigma'))} → {fmt(denver['train']['sigma'])}, "
            f"floor {fmt(denver['orig_floor'])} → {fmt(denver['new_floor'])} "
            f"(Δ{denver['floor_delta']:+.2f})"
        )
    lines.append("")

    lines.append("### §2.4 note")
    lines.append(
        "With H1 fix, zero-coverage days now have shortfall values and contribute "
        "to non-zero shortfall bins. Check if non-zero bins gain meaningful sample size."
    )
    for s in p24_summary:
        if s["n_samples"] > 0 and s["label"] != "exact 0":
            lines.append(f"- {s['label']}: N={s['n_samples']}, mean_err={fmt(s['error_mean'])}")
    lines.append("")

    md_path = PHASE1_RESULTS / "p2_rerun_v2_h1_fix.md"
    md_path.write_text("\n".join(lines) + "\n")

    print(f"DONE: {json_path}")
    print(f"DONE: {md_path}")
    print(f"cities analyzed: {len([r for r in city_results.values() if r.get('status') != 'NO_TRAIN_DATA'])}")
    n_floor_movers = sum(1 for r in city_results.values() if r.get("status") != "NO_TRAIN_DATA" and abs(r.get("floor_delta", 0)) >= 0.05)
    print(f"floor movers (>=0.05): {n_floor_movers}")
    if lagos:
        print(f"Lagos sigma: {fmt(lagos.get('orig_sigma'))} -> {fmt(lagos['train']['sigma'])}")
        print(f"Lagos zero train days: {lagos['train']['n_zero_days']}, test: {lagos['test']['n_zero_days']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
