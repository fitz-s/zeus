"""PHASE 1 §2.1b — Per-city candidate-floor sensitivity analysis.

Created: 2026-05-03
Authority: PLAN.md §2.1 + operator Ruling 3 (must be data-driven).

The first pass (p2_1) produced raw percentile stats and showed that ~40 cities
have train P10 = 1.000 (completely flat). Setting floor = train_P10 is wrong
for them: any non-100% test day (which can be DST boundary, momentary upstream
hiccup, etc.) would fire CAUTION. We need a different floor for "naturally
fully-covered" cities than for "naturally thin" cities.

This pass evaluates each candidate floor [0.35, 0.50, 0.65, 0.75, 0.85, 0.95]
against:
  - train false-positive rate (% of train days below floor — should be small,
    operator target < 1%)
  - test catch rate on extreme days (% of test days where coverage < 0.35 —
    these are unambiguous catastrophic outages)
  - per-city train-min (the lowest historical normal seen)

The deliverable is a comparison table that lets operator pick the right floor
per regime, plus a recommendation per city following these rules:
  - For "stable" cities (train_min >= 0.85): hard_floor = 0.85 (absolute
    physics-based, catches > 1-hour-out-of-7 unexpected outage)
  - For "thin" cities (train_min < 0.85): hard_floor = train_P05 rounded down
    to nearest 0.05 (data-driven baseline; doesn't punish their routine
    sparsity)

Operator can override per city. The recommendation is what gets written to
`hard_floor_for_settlement` field in cities.json (Phase 2).
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PHASE1_RESULTS = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"
DB_PATH = REPO / "state" / "zeus-world.db"

# Candidate floor levels to evaluate
CANDIDATE_FLOORS = [0.35, 0.50, 0.65, 0.75, 0.85, 0.95]

# Recommendation thresholds (operator-tunable)
STABLE_TRAIN_MIN = 0.85   # if train_min >= this, city is "stable" → floor=0.85
ABSOLUTE_PHYSICS_FLOOR = 0.35  # uniform Day-0 hard kill (Ruling 2)


def load_stats() -> dict:
    p = PHASE1_RESULTS / "p2_1_per_city_coverage_stats.json"
    return json.loads(p.read_text())


def load_raw_coverage(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    """Load all (city, date) → coverage pairs from p2_1's underlying query.

    Re-running the coverage query directly because we need per-day values for
    bucket counting (the JSON only has percentiles)."""
    out: dict[tuple[str, str], float] = {}
    rows = conn.execute(
        """
        SELECT city, target_date, peak_hour_int,
               hours_in_window
        FROM (
          SELECT o.city, o.target_date,
                 -- We don't store peak_hour in DB; use the existing JSON for window logic.
                 NULL AS peak_hour_int,
                 COUNT(DISTINCT CAST(o.local_hour AS INTEGER)) AS hours_in_window
          FROM observation_instants_v2 o
          WHERE o.source = 'wu_icao_history'
            AND o.data_version = 'v1.wu-native'
            AND o.target_date >= '2025-07-01' AND o.target_date <= '2026-04-30'
          GROUP BY o.city, o.target_date
        )
        """
    ).fetchall()
    return out  # placeholder; we re-query per city below for window-aware count


def per_day_coverage_for_city(
    conn: sqlite3.Connection,
    city: str,
    target_hours: list[int],
    start: str,
    end: str,
) -> dict[str, float]:
    n_target = len(target_hours)
    target_in = ",".join(str(h) for h in target_hours)
    rows = conn.execute(
        f"""
        SELECT target_date,
               COUNT(DISTINCT CAST(local_hour AS INTEGER)) AS hrs
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
    return {date: hrs / n_target for date, hrs in rows}


def evaluate_floor(coverages: list[float], floor: float) -> float:
    """Return fraction of days strictly below the floor."""
    if not coverages:
        return 0.0
    return sum(1 for c in coverages if c < floor) / len(coverages)


def round_floor_down(v: float, step: float = 0.05) -> float:
    """Round down to nearest `step` (e.g., 0.572 → 0.55)."""
    return math.floor(v / step) * step


def recommend_floor_by_fp_rate(
    train_vals: list[float], target_fp_pct: float = 1.0
) -> tuple[float, str]:
    """Per-city floor recommendation per operator's <1% false-positive criterion.

    Strategy:
      Find the LARGEST candidate floor where the training false-positive rate
      (% days strictly below floor) is ≤ target_fp_pct.

      This maximizes outage-detection sensitivity subject to keeping routine
      false positives below 1%. Bounded below by absolute physics floor (0.35);
      bounded above by 0.85 (preventing >1-hour-out-of-7 over-tightness on
      stable cities).

    Returns (floor, rationale).
    """
    if not train_vals:
        return (ABSOLUTE_PHYSICS_FLOOR, "no train data → physics floor")

    UPPER_CAP = 0.85  # max useful floor: catches loss of >1 hour out of 7
    candidate_levels = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]

    best_floor = ABSOLUTE_PHYSICS_FLOOR
    for f in candidate_levels:
        fp_rate_pct = sum(1 for v in train_vals if v < f) / len(train_vals) * 100.0
        if fp_rate_pct <= target_fp_pct:
            best_floor = f
        else:
            break  # higher floors will only have higher FP rate

    capped = min(best_floor, UPPER_CAP)
    fp_at_cap = sum(1 for v in train_vals if v < capped) / len(train_vals) * 100.0
    return (
        capped,
        f"largest floor with train FP ≤ {target_fp_pct}% → {best_floor:.2f}, "
        f"capped at {UPPER_CAP} → {capped:.2f} (actual train FP {fp_at_cap:.2f}%)",
    )


def main() -> int:
    stats = load_stats()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    # We need per-day coverage values for sensitivity analysis. Re-query per city.
    # (The p2_1 JSON only has summary stats, not raw values.)
    sensitivity: dict[str, dict] = {}

    for city, s in stats.items():
        if s.get("status") == "NO_TRAIN_DATA":
            continue
        peak = s["peak_hour"]
        target_hours = s["window_hours_local"]

        train_cov = per_day_coverage_for_city(
            conn, city, target_hours, "2025-07-01", "2025-12-31"
        )
        test_cov = per_day_coverage_for_city(
            conn, city, target_hours, "2026-01-01", "2026-04-30"
        )

        train_vals = list(train_cov.values())
        test_vals = list(test_cov.values())

        per_floor = {}
        for f in CANDIDATE_FLOORS:
            per_floor[f] = {
                "train_below_pct": evaluate_floor(train_vals, f) * 100.0,
                "test_below_pct": evaluate_floor(test_vals, f) * 100.0,
                "test_below_n": sum(1 for c in test_vals if c < f),
            }
        # Test days < absolute physics floor — these are unambiguous catastrophes
        catastrophic_test_days = sorted(
            [(d, c) for d, c in test_cov.items() if c < ABSOLUTE_PHYSICS_FLOOR]
        )

        recommended_floor, rationale = recommend_floor_by_fp_rate(train_vals, target_fp_pct=1.0)

        sensitivity[city] = {
            "peak_hour": peak,
            "window_hours_local": target_hours,
            "train_min": s["train"]["min"],
            "train_p05": s["train"]["p05"],
            "train_p10": s["train"]["p10"],
            "train_n_days": len(train_vals),
            "test_n_days": len(test_vals),
            "test_min": s["test"]["min"],
            "candidate_floors": per_floor,
            "catastrophic_test_days": catastrophic_test_days,
            "recommended_floor": recommended_floor,
            "rationale": rationale,
        }

    conn.close()

    # Persist results
    out_json = PHASE1_RESULTS / "p2_1b_floor_sensitivity.json"
    out_json.write_text(json.dumps(sensitivity, indent=2, ensure_ascii=False))

    # Markdown report
    md_lines: list[str] = []
    md_lines.append("# Phase 1 §2.1b — Floor Sensitivity & Recommendation")
    md_lines.append("")
    md_lines.append("Created: 2026-05-03 (executed)")
    md_lines.append("Authority: PLAN.md §2.1 + operator Ruling 3")
    md_lines.append("")
    md_lines.append("## Recommendation rule (refined per operator <1% FP criterion)")
    md_lines.append("")
    md_lines.append(
        "For each city, recommended `hard_floor` = the **largest** candidate "
        "from `[0.35, 0.40, ..., 0.85]` where training false-positive rate "
        "(% days below floor) ≤ 1.0%. Capped at 0.85 to avoid over-tight "
        "fire on stable cities. Floored at 0.35 (Day-0 §7 rail 2 absolute physics)."
    )
    md_lines.append("")
    md_lines.append(
        "**Interpretation**: this maximizes outage detection sensitivity "
        "subject to keeping routine false-positive triggers below 1%. "
        "Stable cities (Tokyo, Singapore, ...) all converge to the 0.85 cap "
        "because they have 0 train days below any candidate floor up to 0.85, "
        "so the largest qualifying floor is the cap. Thin cities (Lagos, Jakarta, "
        "Shenzhen) get lower floors because their routine variance forces the "
        "1%-FP criterion below 0.85."
    )
    md_lines.append("")
    md_lines.append("## Per-city recommended hard_floor")
    md_lines.append("")
    md_lines.append(
        "| city | train_min | train_P05 | train_P10 | recommended | rationale |"
    )
    md_lines.append("|---|---|---|---|---|---|")

    # Sort thin cities first (most informative)
    sortable = sorted(
        sensitivity.items(),
        key=lambda kv: kv[1]["train_min"],
    )
    for city, s in sortable:
        md_lines.append(
            f"| {city} | {s['train_min']:.3f} | {s['train_p05']:.3f} | "
            f"{s['train_p10']:.3f} | **{s['recommended_floor']:.2f}** | {s['rationale']} |"
        )
    md_lines.append("")

    md_lines.append("## Floor sensitivity — false-positive rate per candidate")
    md_lines.append("")
    md_lines.append(
        "Each cell is `% of days BELOW the candidate floor`. "
        "Operator target: < 1% on train. Higher in test ⇒ outage caught."
    )
    md_lines.append("")
    md_lines.append("| city | train_min | f<0.35 train | f<0.50 train | f<0.65 train | f<0.85 train | f<0.95 train |")
    md_lines.append("|---|---|---|---|---|---|---|")
    for city, s in sortable:
        cf = s["candidate_floors"]
        md_lines.append(
            f"| {city} | {s['train_min']:.3f} | "
            f"{cf[0.35]['train_below_pct']:.1f}% | "
            f"{cf[0.50]['train_below_pct']:.1f}% | "
            f"{cf[0.65]['train_below_pct']:.1f}% | "
            f"{cf[0.85]['train_below_pct']:.1f}% | "
            f"{cf[0.95]['train_below_pct']:.1f}% |"
        )
    md_lines.append("")
    md_lines.append("## Catastrophic test days (coverage < 0.35 absolute physics floor)")
    md_lines.append("")
    md_lines.append("These are days where the directional window has < 35% coverage — Day-0 §7 rail 2 hard kills regardless of city. List below shows whether the absolute kill catches them.")
    md_lines.append("")
    catastrophic_count = 0
    for city, s in sortable:
        if s["catastrophic_test_days"]:
            catastrophic_count += len(s["catastrophic_test_days"])
            md_lines.append(f"### {city} ({len(s['catastrophic_test_days'])} days)")
            md_lines.append("")
            for date_iso, cov in s["catastrophic_test_days"]:
                md_lines.append(f"- {date_iso}: {cov:.3f}")
            md_lines.append("")
    md_lines.append(f"**Total catastrophic days across all cities in test window: {catastrophic_count}**")
    md_lines.append("")

    out_md = PHASE1_RESULTS / "p2_1b_floor_sensitivity.md"
    out_md.write_text("\n".join(md_lines) + "\n")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(f"cities analyzed: {len(sensitivity)}")
    print(f"catastrophic test days: {catastrophic_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
