"""PHASE 1 §2.4 — Discount Curve Breakpoints Calibration.

Created: 2026-05-03
Authority: PLAN.md §2.4 + canonical reference §6 (DDD curve)

## Hypothesis

Higher coverage shortfall on a given day → higher prediction error (mismatch
proxy). The financial penalty curve `0/0.10/0.25/0.40 → 0%/2%/5%/8%/9%`
should approximately track the empirical relationship.

## Method

For each (city, day) in test window (2026-01-01 → 2026-04-30, per Ruling 1):

1. Compute that day's directional coverage `cov` (HIGH window peak±3).
2. Compute that day's shortfall using §6 formula:
   `shortfall = max(0, hard_floor[city] - cov - σ_90[city])`
   where σ_90 is the 90-day rolling σ ending on that day, hard_floor is from
   p2_1_FINAL_per_city_floors.json.
3. Compute that day's "mismatch proxy" using winning-bucket Brier:
   `error = (1 - p_raw_winner)²`
   on the calibration_pairs_v2 row where outcome=1 for that (city, day, metric).
4. Bin by shortfall: [0, 0.05), [0.05, 0.10), [0.10, 0.20), [0.20, 0.30),
   [0.30, 0.50), [0.50, 1.0]
5. For each bin: report N, mean error, std error.

## Acceptance

- **PASS**: error_mean monotonically increases across shortfall bins, with
  meaningful spread between lowest and highest bin.
- **MARGINAL**: monotone but flat (no meaningful spread) → curve breakpoints
  may need adjustment.
- **FAIL**: bimodal (e.g., 0% error for shortfall<0.40 then jump to 15% above)
  → curve must be replaced with step function.

## Outputs

  phase1_results/p2_4_curve_breakpoints.{json,md}
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
FLOORS_JSON = PHASE1_RESULTS / "p2_1_FINAL_per_city_floors.json"

DEFAULT_HARD_FLOOR = 0.85
SIGMA_WINDOW = 90  # per §2.3 conclusion
TEST_START = "2026-01-01"
TEST_END = "2026-04-30"

# Shortfall bins (operator's proposed curve breakpoints)
BIN_EDGES = [0.0, 0.001, 0.05, 0.10, 0.20, 0.30, 0.50, 2.0]
BIN_LABELS = [
    "exact 0",
    "(0, 0.05)",
    "[0.05, 0.10)",
    "[0.10, 0.20)",
    "[0.20, 0.30)",
    "[0.30, 0.50)",
    "[0.50, ∞)",
]


def directional_window(peak: float | None, radius: int = 3) -> list[int]:
    if peak is None:
        return list(range(24))
    c = int(round(peak))
    return [(h % 24) for h in range(c - radius, c + radius + 1)]


def per_day_coverage_dict(
    conn: sqlite3.Connection, city: str, target_hours: list[int],
    start: str, end: str,
) -> dict[str, float]:
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
    return {date: hrs / n_target for date, hrs in rows}


def date_iter(start: str, end: str):
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def bin_index(value: float) -> int:
    for i in range(len(BIN_EDGES) - 1):
        if BIN_EDGES[i] <= value < BIN_EDGES[i + 1]:
            return i
    return len(BIN_EDGES) - 2


def main() -> int:
    PHASE1_RESULTS.mkdir(parents=True, exist_ok=True)

    # Load floors
    floors_data = json.loads(FLOORS_JSON.read_text())
    per_city_floors: dict[str, float] = floors_data["per_city_floors"]

    with open(CITIES_JSON) as f:
        cities_d = json.load(f)
    city_cfg = {c["name"]: c for c in cities_d["cities"]}

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    # For each city: pull pre-test-window 90-day coverage history (to seed σ),
    # then iterate test window computing daily shortfall + prediction error.
    bin_data: dict[int, list[float]] = defaultdict(list)  # bin_idx -> list of errors
    bin_data_by_city: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    cities_processed = 0
    for city, floor in per_city_floors.items():
        if floor is None:
            continue
        peak = city_cfg.get(city, {}).get("historical_peak_hour")
        if peak is None:
            continue
        target_hours = directional_window(peak, 3)

        # History window: 90 days before TEST_START to seed first σ
        history_start = (date.fromisoformat(TEST_START) - timedelta(days=90)).isoformat()
        cov_full = per_day_coverage_dict(conn, city, target_hours, history_start, TEST_END)

        # Pull winning-bucket calibration pairs in test window for this city
        # both metrics
        cal_rows = conn.execute(
            """
            SELECT target_date, temperature_metric, p_raw
            FROM calibration_pairs_v2
            WHERE city = ? AND authority = 'VERIFIED' AND outcome = 1
              AND target_date >= ? AND target_date <= ?
            """,
            (city, TEST_START, TEST_END),
        ).fetchall()
        # Index per (target_date, metric) — many pairs per day across lead_days
        per_day_metric_p: dict[tuple[str, str], list[float]] = defaultdict(list)
        for d_iso, m, p in cal_rows:
            per_day_metric_p[(d_iso, m)].append(p)

        for d_iso in date_iter(TEST_START, TEST_END):
            cov = cov_full.get(d_iso)
            if cov is None:
                continue

            # Compute σ_90 for this day: pull the 90 days ending day-before
            # (exclude today per §6 spec)
            target_dt = date.fromisoformat(d_iso)
            window_start = (target_dt - timedelta(days=90)).isoformat()
            window_end = (target_dt - timedelta(days=1)).isoformat()
            window_vals = [
                v for d, v in cov_full.items()
                if window_start <= d <= window_end
            ]
            if len(window_vals) < 30:  # need at least 30 days of history
                continue
            sigma = statistics.stdev(window_vals) if len(window_vals) > 1 else 0.0

            shortfall = max(0.0, floor - cov - sigma)
            bin_idx = bin_index(shortfall)

            # Aggregate winning-bucket error across both metrics for this day
            for metric in ["high", "low"]:
                p_vals = per_day_metric_p.get((d_iso, metric), [])
                if not p_vals:
                    continue
                # Each (date, metric) has multiple lead_days; use the most-confident
                # prediction (max p_raw among winning-bucket rows for that day)
                # OR the median? Use median to avoid outlier dominance.
                p_repr = statistics.median(p_vals)
                error = (1.0 - p_repr) ** 2
                bin_data[bin_idx].append(error)
                bin_data_by_city[city][bin_idx].append(error)

        cities_processed += 1

    conn.close()

    # Aggregate across all cities
    summary = []
    for i in range(len(BIN_EDGES) - 1):
        errs = bin_data[i]
        summary.append({
            "bin_idx": i,
            "label": BIN_LABELS[i],
            "shortfall_lo": BIN_EDGES[i],
            "shortfall_hi": BIN_EDGES[i + 1],
            "n_samples": len(errs),
            "error_mean": statistics.mean(errs) if errs else None,
            "error_median": statistics.median(errs) if errs else None,
            "error_std": statistics.stdev(errs) if len(errs) > 1 else None,
        })

    out = {
        "method": "winning-bucket Brier residual per (city, day) binned by shortfall",
        "test_window": f"{TEST_START} → {TEST_END}",
        "sigma_window_days": SIGMA_WINDOW,
        "cities_processed": cities_processed,
        "bin_edges": BIN_EDGES,
        "bin_labels": BIN_LABELS,
        "global_summary": summary,
        "_per_city_summary": {
            city: {
                str(i): {
                    "n": len(errs),
                    "mean": statistics.mean(errs) if errs else None,
                }
                for i, errs in bins.items() if errs
            }
            for city, bins in bin_data_by_city.items()
        },
    }

    json_path = PHASE1_RESULTS / "p2_4_curve_breakpoints.json"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Markdown report
    lines = []
    lines.append("# Phase 1 §2.4 — Discount Curve Breakpoints Calibration")
    lines.append("")
    lines.append("Created: 2026-05-03 (executed)")
    lines.append("Authority: PLAN.md §2.4 + canonical reference §6")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("For each (city, day) in test window (2026-01-01 → 2026-04-30 per Ruling 1):")
    lines.append("- shortfall = max(0, floor[city] - cov[day] - σ_90[city, day])")
    lines.append("- error = (1 - p_raw_winner)² (winning-bucket Brier residual, median across lead_days)")
    lines.append("")
    lines.append("Then bin by shortfall and report per-bin error statistics.")
    lines.append("")
    lines.append("## Global aggregate (all cities)")
    lines.append("")
    lines.append("| shortfall bin | N samples | error_mean | error_median | error_std |")
    lines.append("|---|---|---|---|---|")
    for s in summary:
        em = f"{s['error_mean']:.4f}" if s['error_mean'] is not None else "n/a"
        emd = f"{s['error_median']:.4f}" if s['error_median'] is not None else "n/a"
        es = f"{s['error_std']:.4f}" if s['error_std'] is not None else "n/a"
        lines.append(f"| {s['label']} | {s['n_samples']:,} | {em} | {emd} | {es} |")
    lines.append("")

    # Verdict
    means = [s["error_mean"] for s in summary if s["error_mean"] is not None and s["n_samples"] >= 10]
    monotone = all(means[i] <= means[i + 1] + 0.05 for i in range(len(means) - 1)) if len(means) >= 2 else False
    spread = (means[-1] - means[0]) if len(means) >= 2 else 0
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"- Bins with ≥10 samples: {sum(1 for s in summary if s['n_samples'] >= 10)} / {len(summary)}")
    lines.append(f"- Mean error progression (low → high shortfall): {[f'{m:.3f}' for m in means]}")
    lines.append(f"- Monotone (with 0.05 tolerance): {monotone}")
    lines.append(f"- Spread (high - low bin mean): {spread:.4f}")
    lines.append("")
    if monotone and spread > 0.05:
        lines.append("**PASS**: error_mean monotonically increases with shortfall and shows meaningful spread. Curve hypothesis supported.")
    elif monotone and spread <= 0.05:
        lines.append("**MARGINAL**: monotone but flat. Curve breakpoints may need to be tightened.")
    else:
        lines.append("**FAIL**: not monotone OR no signal. Curve must be re-spec'd.")
    lines.append("")
    lines.append("## Operator's proposed curve (for comparison)")
    lines.append("")
    lines.append("| shortfall | DDD value |")
    lines.append("|---|---|")
    lines.append("| 0 | 0.00 |")
    lines.append("| 0.0 - 0.10 | linear 0% → 2% |")
    lines.append("| 0.10 - 0.25 | linear 2% → 5% |")
    lines.append("| 0.25 - 0.40 | linear 5% → 8% |")
    lines.append("| > 0.40 | 9% (cap) |")
    lines.append("")

    md_path = PHASE1_RESULTS / "p2_4_curve_breakpoints.md"
    md_path.write_text("\n".join(lines) + "\n")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"cities processed: {cities_processed}")
    print(f"bins with samples: {sum(1 for s in summary if s['n_samples'] >= 10)} / {len(summary)}")
    print(f"means: {[f'{m:.3f}' for m in means]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
