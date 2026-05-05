"""PHASE 1 §2.1 — Empirical hard_floor_for_settlement[city] derivation.

Created: 2026-05-03
Authority basis: docs/operations/task_2026-05-03_ddd_implementation_plan/PLAN.md §2.1
                 + operator Ruling 3 (2026-05-03): data-driven, no pre-approved values
                 + zeus_oracle_density_discount_reference.md §5.1 (Boiled Frog)

Procedure (per Ruling 1 — time-window split):
  TRAIN: 2025-07-01 to 2025-12-31 — derive candidate floors from percentiles
  TEST:  2026-01-01 to 2026-04-30 — verify floor catches known outages

Deliverables:
  - phase1_results/p2_1_per_city_coverage_stats.json
  - phase1_results/p2_1_hard_floor_calibration.md  (human-readable report)

This is research code, not production. Sole writer to phase1_results/.
Run from repo root: `.venv/bin/python -m \
  docs.operations.task_2026-05-03_ddd_implementation_plan.phase1.p2_1_hard_floor_calibration`
or directly: `.venv/bin/python <path-to-this-file>`.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from pathlib import Path

# repo root
REPO = Path(__file__).resolve().parents[4]
DB_PATH = REPO / "state" / "zeus-world.db"
CITIES_JSON = REPO / "config" / "cities.json"
OUT_DIR = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"

# Per Ruling 1 — time-window split
TRAIN_START = "2025-07-01"
TRAIN_END = "2025-12-31"
TEST_START = "2026-01-01"
TEST_END = "2026-04-30"

# Directional window radius (per zeus_oracle_density_discount_reference.md §3)
# Phase 1 §2.6 will tune this; ±3 hours is the operator-set initial value.
WINDOW_RADIUS = 3


def load_cities() -> list[dict]:
    with open(CITIES_JSON) as f:
        d = json.load(f)
    return d["cities"]


def directional_window_hours(peak_hour: float | None, radius: int) -> list[int]:
    """Return the set of local hours that count toward directional coverage."""
    if peak_hour is None:
        return list(range(24))  # fall back to whole-day
    center = int(round(peak_hour))
    return [(h % 24) for h in range(center - radius, center + radius + 1)]


def per_day_coverage(
    conn: sqlite3.Connection,
    city: str,
    start: str,
    end: str,
    peak_hour: float | None,
    radius: int,
) -> dict[str, float]:
    """Return {target_date_iso: coverage_ratio} for the directional window.

    Coverage = distinct local-hours covered IN THE WINDOW / window-size.
    Source: wu_icao_history primary only (per Ruling 3 — fallbacks not authoritative
    for settlement, so they cannot stand in for floor-detection of primary outage).
    """
    target_hours = directional_window_hours(peak_hour, radius)
    n_target = len(target_hours)
    target_in = ",".join(str(h) for h in target_hours)
    rows = conn.execute(
        f"""
        SELECT target_date,
               COUNT(DISTINCT CAST(local_hour AS INTEGER)) AS hrs_in_window
        FROM observation_instants_v2
        WHERE city = ?
          AND source = 'wu_icao_history'
          AND data_version = 'v1.wu-native'
          AND target_date >= ? AND target_date <= ?
          AND CAST(local_hour AS INTEGER) IN ({target_in})
        GROUP BY target_date
        """,
        (city, start, end),
    ).fetchall()
    return {date_iso: hrs / n_target for date_iso, hrs in rows}


def percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolation percentile. p in [0, 100]."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def compute_per_city_stats(
    conn: sqlite3.Connection, cities: list[dict]
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in cities:
        name = c["name"]
        peak = c.get("historical_peak_hour")
        train_cov = per_day_coverage(
            conn, name, TRAIN_START, TRAIN_END, peak, WINDOW_RADIUS
        )
        test_cov = per_day_coverage(
            conn, name, TEST_START, TEST_END, peak, WINDOW_RADIUS
        )

        # cities with no WU data in the train window are skipped
        if not train_cov:
            out[name] = {"status": "NO_TRAIN_DATA", "peak_hour": peak}
            continue

        train_vals = list(train_cov.values())
        test_vals = list(test_cov.values()) if test_cov else []

        # Days with zero coverage (no observations in window) — must be flagged
        zero_train = sum(1 for v in train_vals if v == 0)
        zero_test = sum(1 for v in test_vals if v == 0)

        out[name] = {
            "peak_hour": peak,
            "window_hours_local": directional_window_hours(peak, WINDOW_RADIUS),
            "train": {
                "n_days": len(train_vals),
                "min": min(train_vals),
                "p05": percentile(train_vals, 5),
                "p10": percentile(train_vals, 10),
                "p25": percentile(train_vals, 25),
                "p50": percentile(train_vals, 50),
                "p75": percentile(train_vals, 75),
                "mean": statistics.mean(train_vals),
                "stddev": statistics.stdev(train_vals) if len(train_vals) > 1 else 0.0,
                "zero_cov_days": zero_train,
            },
            "test": {
                "n_days": len(test_vals),
                "min": min(test_vals) if test_vals else None,
                "p05": percentile(test_vals, 5) if test_vals else None,
                "p10": percentile(test_vals, 10) if test_vals else None,
                "p25": percentile(test_vals, 25) if test_vals else None,
                "p50": percentile(test_vals, 50) if test_vals else None,
                "mean": statistics.mean(test_vals) if test_vals else None,
                "zero_cov_days": zero_test,
            },
        }
    return out


def write_markdown_report(stats: dict[str, dict]) -> str:
    lines: list[str] = []
    lines.append("# Phase 1 §2.1 — Hard Floor Calibration Results")
    lines.append("")
    lines.append("Created: 2026-05-03 (executed)")
    lines.append("Authority: PLAN.md §2.1 + operator Ruling 3 (2026-05-03)")
    lines.append(
        "Source: `wu_icao_history` primary only "
        "(fallbacks are not authoritative for settlement-floor detection)"
    )
    lines.append(
        f"Train window: {TRAIN_START} → {TRAIN_END}; "
        f"Test window: {TEST_START} → {TEST_END}"
    )
    lines.append(f"Directional window: peak_hour ± {WINDOW_RADIUS} (HIGH track default)")
    lines.append("")

    # Cities sorted by train P10 ascending (most fragile first)
    sortable = [
        (name, s)
        for name, s in stats.items()
        if s.get("status") != "NO_TRAIN_DATA" and s["train"]["p10"] is not None
    ]
    sortable.sort(key=lambda kv: kv[1]["train"]["p10"])

    lines.append(
        "## Per-city directional coverage (HIGH window, primary source only)"
    )
    lines.append("")
    lines.append(
        "| city | peak_hr | n train | min | P05 | P10 | P25 | P50 | mean | "
        "zero days | n test | test min | test P05 |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    def fmt(v: float | None) -> str:
        return f"{v:.3f}" if v is not None else "n/a"

    for name, s in sortable:
        t = s["train"]
        u = s["test"]
        lines.append(
            f"| {name} | {s['peak_hour']} | {t['n_days']} | "
            f"{fmt(t['min'])} | {fmt(t['p05'])} | {fmt(t['p10'])} | "
            f"{fmt(t['p25'])} | {fmt(t['p50'])} | {fmt(t['mean'])} | "
            f"{t['zero_cov_days']} | {u['n_days']} | "
            f"{fmt(u['min'])} | {fmt(u['p05'])} |"
        )
    lines.append("")

    # Cities with NO_TRAIN_DATA are listed separately
    skipped = [n for n, s in stats.items() if s.get("status") == "NO_TRAIN_DATA"]
    if skipped:
        lines.append("### Cities with no `wu_icao_history` data in train window")
        lines.append("")
        for n in sorted(skipped):
            lines.append(f"- {n} (peak_hour={stats[n].get('peak_hour')})")
        lines.append("")

    # Recommendation table — proposed hard_floor as P10 of train (per Ruling 3,
    # data-driven). Operator decides whether to ratchet up via override.
    lines.append("## Proposed `hard_floor_for_settlement` per city")
    lines.append("")
    lines.append(
        "**Rule used**: hard_floor = train P10 (90% of routine days are above this)."
    )
    lines.append(
        "Per Ruling 3, `cities.json` reserves a `hard_floor_for_settlement` field "
        "as override interface; default `null` → uses this data-derived value."
    )
    lines.append("")
    lines.append("| city | data-derived floor (train P10) | sanity vs test min |")
    lines.append("|---|---|---|")
    for name, s in sortable:
        floor = s["train"]["p10"]
        test_min = s["test"]["min"]
        sanity = ""
        if test_min is not None and test_min < floor:
            sanity = f"⚠ test min {test_min:.3f} < floor — outage detected in test"
        lines.append(
            f"| {name} | {floor:.3f} | "
            f"{f'{test_min:.3f}' if test_min is not None else 'n/a'}{(' ' + sanity) if sanity else ''} |"
        )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    if not DB_PATH.exists():
        print(f"FATAL: DB not found at {DB_PATH}", file=sys.stderr)
        return 2
    if not CITIES_JSON.exists():
        print(f"FATAL: cities.json not found at {CITIES_JSON}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cities = load_cities()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        stats = compute_per_city_stats(conn, cities)
    finally:
        conn.close()

    json_path = OUT_DIR / "p2_1_per_city_coverage_stats.json"
    json_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    md_path = OUT_DIR / "p2_1_hard_floor_calibration.md"
    md_path.write_text(write_markdown_report(stats))

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"cities analyzed: {len(stats)}")
    no_data = sum(1 for s in stats.values() if s.get('status') == 'NO_TRAIN_DATA')
    if no_data:
        print(f"  (skipped {no_data} cities with no train-window data)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
