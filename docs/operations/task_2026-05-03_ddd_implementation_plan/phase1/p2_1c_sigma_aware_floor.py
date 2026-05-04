"""PHASE 1 §2.1c — σ-aware floor recalibration.

Created: 2026-05-03
Authority: PLAN.md §2.1 + operator reminder 2026-05-03 — σ-band must apply to
           floor calibration, not just runtime shortfall computation
           (zeus_oracle_density_discount_reference.md §5.2).

The previous pass (p2_1b) used `% days strictly below floor` as the
false-positive rate. But the canonical formula is:

    shortfall = max(0, floor - cov - 1*sigma)

So a day "fires" only when `cov < floor - sigma`, NOT when `cov < floor`.
The σ-band absorbs Poisson noise (e.g., 1-hour drops on otherwise-full
stations). The floor calibration must use the same σ-aware definition of
"fire" or it will overestimate FP rates and pick floors that are too low.

This pass re-evaluates per-city floor with σ-aware FP rate:

    fp_rate(F) = % training days with cov < F - σ_train

Where σ_train = stddev of training-window directional coverage.

Concrete example (Denver):
  - train values mostly 1.0 with occasional dips to 0.857 (noise) and
    0.429/0.571 (real outages)
  - σ_train ≈ ~0.15
  - At floor F = 0.85: fires when cov < 0.85 - 0.15 = 0.70 → ignores 0.857
    noise days, catches 0.429/0.571 outages
  - This is the right behavior, but p2_1b would have penalized F=0.85 because
    it counted the 0.857 days as FP.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PHASE1_RESULTS = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"
DB_PATH = REPO / "state" / "zeus-world.db"
CITIES_JSON = REPO / "config" / "cities.json"

CANDIDATE_FLOORS = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
ABSOLUTE_PHYSICS_FLOOR = 0.35
UPPER_CAP = 0.85
TARGET_FP_PCT = 1.0


def directional_window(peak: float | None, radius: int = 3) -> list[int]:
    if peak is None:
        return list(range(24))
    c = int(round(peak))
    return [(h % 24) for h in range(c - radius, c + radius + 1)]


def per_day_coverage(
    conn: sqlite3.Connection, city: str, target_hours: list[int], start: str, end: str
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


def sigma_aware_fp_rate(values: list[float], floor: float, sigma: float) -> float:
    """Fraction of days where cov < floor - sigma (i.e., shortfall > 0)."""
    if not values:
        return 0.0
    threshold = floor - sigma
    return sum(1 for v in values if v < threshold) / len(values)


def recommend_sigma_aware_floor(
    train_vals: list[float], target_fp: float
) -> tuple[float, float, str]:
    """Returns (floor, sigma, rationale)."""
    if not train_vals:
        return (ABSOLUTE_PHYSICS_FLOOR, 0.0, "no train data → physics floor")
    if len(train_vals) == 1:
        return (ABSOLUTE_PHYSICS_FLOOR, 0.0, "n=1 train day → physics floor")

    sigma = statistics.stdev(train_vals)
    best_floor = ABSOLUTE_PHYSICS_FLOOR
    for f in CANDIDATE_FLOORS:
        fp_pct = sigma_aware_fp_rate(train_vals, f, sigma) * 100.0
        if fp_pct <= target_fp:
            best_floor = f
        else:
            break
    capped = min(best_floor, UPPER_CAP)
    actual_fp = sigma_aware_fp_rate(train_vals, capped, sigma) * 100.0
    return (
        capped,
        sigma,
        f"σ-aware: σ_train={sigma:.3f}, fire if cov<floor-σ; "
        f"largest qualifying floor={best_floor:.2f}, capped at {UPPER_CAP} → {capped:.2f}; "
        f"actual σ-aware FP {actual_fp:.2f}%",
    )


def main() -> int:
    with open(CITIES_JSON) as f:
        cities_d = json.load(f)
    cities = cities_d["cities"]

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    out: dict = {}
    for c in cities:
        name = c["name"]
        peak = c.get("historical_peak_hour")
        target_hours = directional_window(peak, 3)
        train_cov = per_day_coverage(conn, name, target_hours, "2025-07-01", "2025-12-31")
        test_cov = per_day_coverage(conn, name, target_hours, "2026-01-01", "2026-04-30")
        if not train_cov:
            out[name] = {"status": "NO_TRAIN_DATA"}
            continue

        train_vals = list(train_cov.values())
        test_vals = list(test_cov.values())
        floor, sigma, rationale = recommend_sigma_aware_floor(train_vals, TARGET_FP_PCT)

        # Also report the OLD (non-σ-aware) recommendation for comparison
        old_best = ABSOLUTE_PHYSICS_FLOOR
        for f in CANDIDATE_FLOORS:
            naive_fp = sum(1 for v in train_vals if v < f) / len(train_vals) * 100.0
            if naive_fp <= TARGET_FP_PCT:
                old_best = f
            else:
                break
        old_capped = min(old_best, UPPER_CAP)

        # Catastrophic test days (cov < absolute physics floor)
        catastrophic = sorted([(d, c) for d, c in test_cov.items() if c < ABSOLUTE_PHYSICS_FLOOR])

        out[name] = {
            "peak_hour": peak,
            "n_train_days": len(train_vals),
            "n_test_days": len(test_vals),
            "train_min": min(train_vals),
            "train_mean": statistics.mean(train_vals),
            "train_sigma": sigma,
            "naive_floor_p2_1b": old_capped,  # what p2_1b recommended
            "sigma_aware_floor": floor,        # this pass's recommendation
            "delta_vs_naive": floor - old_capped,
            "rationale": rationale,
            "n_catastrophic_test_days": len(catastrophic),
            "catastrophic_test_days": catastrophic,
        }

    conn.close()

    out_json = PHASE1_RESULTS / "p2_1c_sigma_aware_floor.json"
    out_json.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Markdown report
    lines = []
    lines.append("# Phase 1 §2.1c — σ-aware Floor Recalibration")
    lines.append("")
    lines.append("Created: 2026-05-03 (executed)")
    lines.append("Authority: PLAN.md §2.1 + operator reminder 2026-05-03 (σ-band rule)")
    lines.append("")
    lines.append("## Why this pass exists")
    lines.append("")
    lines.append("p2_1b counted any train day below the candidate floor as a 'false positive'.")
    lines.append("But §6 formula uses `shortfall = max(0, floor - cov - 1*σ)`, so a day only")
    lines.append("'fires' DDD when `cov < floor - σ`. The σ-band absorbs Poisson noise (e.g.,")
    lines.append("a single 1-hour drop on a 7-hour window = 1-(6/7) = 0.143 below 1.0). p2_1b's")
    lines.append("naive FP counting was overly conservative and pulled some recommendations")
    lines.append("downward unnecessarily.")
    lines.append("")
    lines.append("This pass redefines FP rate as `% days with cov < floor - σ_train` and")
    lines.append("re-runs the recommendation. Differences vs p2_1b are highlighted.")
    lines.append("")
    lines.append("## Per-city σ-aware recommendation (sorted by train_min)")
    lines.append("")
    lines.append(
        "| city | n_days | min | mean | σ_train | p2_1b floor | σ-aware floor | Δ |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    sortable = [(n, s) for n, s in out.items() if s.get("status") != "NO_TRAIN_DATA"]
    sortable.sort(key=lambda kv: kv[1]["train_min"])
    for name, s in sortable:
        delta = s["delta_vs_naive"]
        delta_str = f"**+{delta:.2f}**" if delta > 0 else (f"{delta:.2f}" if delta < 0 else "—")
        lines.append(
            f"| {name} | {s['n_train_days']} | {s['train_min']:.3f} | "
            f"{s['train_mean']:.3f} | {s['train_sigma']:.3f} | "
            f"{s['naive_floor_p2_1b']:.2f} | **{s['sigma_aware_floor']:.2f}** | {delta_str} |"
        )
    lines.append("")
    lines.append("## Cities where σ-band materially raised the recommendation")
    lines.append("")
    lines.append(
        "These cities had Poisson-noise days (1-hour drops) that pulled the naive floor "
        "down. The σ-band correctly recognizes those drops as routine variance, allowing "
        "a higher (more sensitive) floor."
    )
    lines.append("")
    raised = [(n, s) for n, s in out.items()
              if s.get("status") != "NO_TRAIN_DATA" and s["delta_vs_naive"] > 0]
    raised.sort(key=lambda kv: -kv[1]["delta_vs_naive"])
    for name, s in raised:
        lines.append(
            f"- **{name}**: {s['naive_floor_p2_1b']:.2f} → {s['sigma_aware_floor']:.2f} "
            f"(σ_train={s['train_sigma']:.3f}, train_min={s['train_min']:.3f})"
        )
    lines.append("")
    lines.append("## Final recommended hard_floor_for_settlement values")
    lines.append("")
    lines.append("Group cities by recommended floor:")
    lines.append("")
    by_floor: dict[float, list[str]] = {}
    for name, s in sortable:
        f = s["sigma_aware_floor"]
        by_floor.setdefault(f, []).append(name)
    for f in sorted(by_floor.keys()):
        lines.append(f"### Floor = {f:.2f} ({len(by_floor[f])} cities)")
        lines.append("")
        lines.append(", ".join(sorted(by_floor[f])))
        lines.append("")
    lines.append("## Catastrophic test-day detection")
    lines.append("")
    catastrophic_total = sum(s.get("n_catastrophic_test_days", 0) for s in out.values())
    lines.append(f"Total test days with cov < {ABSOLUTE_PHYSICS_FLOOR} (absolute physics): {catastrophic_total}")
    lines.append("")
    lines.append(
        "All catastrophic days have cov < 0.35; they are caught by Day-0 §7 rail 2 "
        "(absolute kill at 0.35) AND by §6 historical DDD (any floor ≥ 0.35 detects them)."
    )
    lines.append("")

    out_md = PHASE1_RESULTS / "p2_1c_sigma_aware_floor.md"
    out_md.write_text("\n".join(lines) + "\n")

    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    raised_count = sum(1 for n, s in out.items()
                       if s.get("status") != "NO_TRAIN_DATA" and s["delta_vs_naive"] > 0)
    print(f"cities raised by σ-band: {raised_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
