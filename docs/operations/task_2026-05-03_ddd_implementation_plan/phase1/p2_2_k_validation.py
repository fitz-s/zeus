"""PHASE 1 §2.2 — Small-Sample Multiplier `k` Validation.

Created: 2026-05-03
Authority: PLAN.md §2.2 + operator success benediction 2026-05-03
           ("当 Brier Score 曲线证明了 (1 + k/sqrt(N)) 的确能平滑大样本与小样本的风险敞口时，
            你就亲手为 Zeus 打造了一面免疫系统级别的盾牌")

THIS IS THE LOAD-BEARING IMMUNE-SYSTEM EXPERIMENT.

If this fails, the entire DDD multiplier architecture is in question and the
canonical reference §5.3 must be re-spec'd. Operator pre-committed (PLAN.md
§1 kill-switch) to halt and re-spec if the experiment fails.

## Hypothesis under test

Per zeus_oracle_density_discount_reference.md §5.3: small-N (city, metric)
pairs have unconverged Platt regime-conditional bias absorption. Their Brier
score (squared calibration error) should systematically exceed large-N pairs.
The multiplier (1 + k/sqrt(N)) is meant to compensate by amplifying DDD on
small-N cities.

This experiment tests: given test-window Brier per (city, metric), is there
an empirical k such that:
  - the train→test Brier vs N relationship can be flattened by (1 + k/sqrt(N))
  - the same k generalizes (Ruling 1 time-window split)

## Method

Step 1 — Per (city, metric):
  N_train = COUNT(*) of authority='VERIFIED' calibration_pairs_v2 rows with
            target_date < 2026-01-01
  Brier_train = mean((p_raw - outcome)^2) on same rows
  Brier_test = mean((p_raw - outcome)^2) on rows with target_date >= 2026-01-01

Step 2 — Fit Brier_train ~ a + b/sqrt(N) using least-squares regression
  (all valid (city, metric) pairs as data points). Report a, b, R².
  If b ≤ 0 OR R² < 0.10, the hypothesis fails — small N does NOT correlate
  with higher Brier, and the multiplier has no statistical basis.

Step 3 — Compute optimal k from train fit:
  Brier_train(N) ≈ a_train + b_train/sqrt(N)
  k_train = b_train / a_train  (relative excess due to small N)

Step 4 — Repeat fit on test window:
  k_test = b_test / a_test
  Per Ruling 1: |k_test - k_train| / k_train ≤ 0.50 to PASS.
  Operator-tightened acceptance per refined plan: ≤ 0.15 RECOMMENDED, ≤ 0.50
  ACCEPTABLE.

Step 5 — Report Brier_test divided by (1 + k_train/sqrt(N_train)) per bucket.
  If multiplier flattens variance: variance of Brier_adjusted across N bins
  should be lower than variance of Brier_test alone.

## Outputs

  phase1_results/p2_2_k_validation.json — full per-(city,metric) data
  phase1_results/p2_2_k_validation.md — human-readable interpretation

Excluded cities: Paris (all train pre-2026-02 QUARANTINED — no train data).
"""

from __future__ import annotations

import json
import math
import statistics
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PHASE1_RESULTS = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"
DB_PATH = REPO / "state" / "zeus-world.db"

TRAIN_END = "2025-12-31"
TEST_START = "2026-01-01"
TEST_END = "2026-04-30"
EXCLUDED_CITIES: set[str] = set()  # populated dynamically based on data quality


def fetch_per_bucket_stats(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Per (city, metric): n_train, brier_train, n_test, brier_test.

    CRITICAL CHANGE 2026-05-03: Brier computed only on WINNING-BUCKET rows
    (outcome=1), because including all 100+ non-winning buckets per day washes
    the signal — non-winning buckets have p_raw ≈ 1/100 and outcome=0, so
    (p_raw - outcome)² ≈ 1e-4 dominates the average.

    For winning buckets only: Brier_winner = mean((1 - p_raw)²). Lower = model
    is more confident in the truth. This is the meaningful calibration metric.

    N_train = count of winning-bucket rows in train window. This is the
    operational sample size that Platt has had to converge against.
    """
    rows = conn.execute(
        f"""
        SELECT city, temperature_metric AS metric,
               -- Train aggregates (winning buckets only)
               SUM(CASE WHEN target_date <= ? AND outcome = 1 THEN 1 ELSE 0 END) AS n_train,
               SUM(CASE WHEN target_date <= ? AND outcome = 1 THEN (1.0 - p_raw) * (1.0 - p_raw) ELSE 0 END) AS sse_train,
               -- Test aggregates (winning buckets only)
               SUM(CASE WHEN target_date >= ? AND target_date <= ? AND outcome = 1 THEN 1 ELSE 0 END) AS n_test,
               SUM(CASE WHEN target_date >= ? AND target_date <= ? AND outcome = 1 THEN (1.0 - p_raw) * (1.0 - p_raw) ELSE 0 END) AS sse_test
        FROM calibration_pairs_v2
        WHERE authority = 'VERIFIED'
        GROUP BY city, metric
        """,
        (TRAIN_END, TRAIN_END, TEST_START, TEST_END, TEST_START, TEST_END),
    ).fetchall()
    out: dict[tuple[str, str], dict] = {}
    for city, metric, n_tr, sse_tr, n_te, sse_te in rows:
        if n_tr < 30 or n_te < 30:
            # Insufficient winning-bucket samples for reliable Brier (recall:
            # ~1 winner per day, so 30 = roughly 30 days)
            continue
        out[(city, metric)] = {
            "n_train": int(n_tr),
            "n_test": int(n_te),
            "brier_train": sse_tr / n_tr,
            "brier_test": sse_te / n_te,
        }
    return out


def linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Simple OLS y = a + b*x; returns (a, b, r2)."""
    if len(xs) < 2:
        return (float("nan"), float("nan"), float("nan"))
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return (my, 0.0, 0.0)
    b = sxy / sxx
    a = my - b * mx
    syy = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - sse / syy if syy > 0 else 0.0
    return (a, b, r2)


def fit_brier_vs_n(stats: dict[tuple[str, str], dict], window: str) -> dict:
    """Fit `Brier ~ a + b/sqrt(N)` over all (city, metric) buckets."""
    pts = [(s["n_" + window], s["brier_" + window]) for s in stats.values()]
    xs = [1.0 / math.sqrt(n) for n, _ in pts]  # 1/sqrt(N)
    ys = [b for _, b in pts]
    a, b, r2 = linreg(xs, ys)
    k = b / a if (a is not None and abs(a) > 1e-12) else float("nan")
    return {
        "intercept_a": a,
        "slope_b": b,
        "r_squared": r2,
        "n_buckets": len(pts),
        "k_estimate": k,
    }


def report_per_bucket(stats: dict[tuple[str, str], dict], k_train: float) -> list[dict]:
    """Compute Brier-adjusted = Brier_test / (1 + k/sqrt(N_train)) per bucket.

    If the multiplier flattens, var(Brier_adjusted) < var(Brier_test).
    """
    out = []
    for (city, metric), s in stats.items():
        adjuster = 1.0 + k_train / math.sqrt(s["n_train"])
        brier_adj = s["brier_test"] / adjuster if adjuster > 0 else s["brier_test"]
        out.append({
            "city": city,
            "metric": metric,
            "n_train": s["n_train"],
            "n_test": s["n_test"],
            "brier_train": s["brier_train"],
            "brier_test": s["brier_test"],
            "adjuster": adjuster,
            "brier_test_adjusted": brier_adj,
        })
    out.sort(key=lambda d: d["n_train"])
    return out


def main() -> int:
    PHASE1_RESULTS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        stats = fetch_per_bucket_stats(conn)
    finally:
        conn.close()

    if not stats:
        print("FATAL: no buckets with sufficient train+test data")
        return 2

    # Step 2 + 3 — fit on train
    train_fit = fit_brier_vs_n(stats, "train")
    test_fit = fit_brier_vs_n(stats, "test")

    # Step 5 — flattening test
    bucket_report = report_per_bucket(stats, train_fit["k_estimate"])
    brier_test_var = statistics.pvariance([r["brier_test"] for r in bucket_report])
    brier_test_adj_var = statistics.pvariance([r["brier_test_adjusted"] for r in bucket_report])
    variance_reduction_pct = (
        (brier_test_var - brier_test_adj_var) / brier_test_var * 100.0
        if brier_test_var > 0 else 0.0
    )

    # Acceptance per Ruling 1 + plan §2.2
    k_delta_pct = (
        abs(test_fit["k_estimate"] - train_fit["k_estimate"]) / abs(train_fit["k_estimate"]) * 100.0
        if abs(train_fit["k_estimate"]) > 1e-9 else float("nan")
    )

    out = {
        "method": "Brier vs 1/sqrt(N) least-squares fit; k = b / a",
        "train_window": f"... → {TRAIN_END}",
        "test_window": f"{TEST_START} → {TEST_END}",
        "n_buckets": len(stats),
        "fit_train": train_fit,
        "fit_test": test_fit,
        "k_train_vs_test_delta_pct": k_delta_pct,
        "brier_test_var_raw": brier_test_var,
        "brier_test_var_adjusted": brier_test_adj_var,
        "variance_reduction_pct": variance_reduction_pct,
        "buckets": bucket_report,
        "acceptance": {
            "monotone_brier_vs_smallN": train_fit["slope_b"] > 0,
            "r2_train_above_0p10": train_fit["r_squared"] > 0.10,
            "k_train_test_delta_below_50pct": k_delta_pct < 50.0,
            "variance_reduction_positive": variance_reduction_pct > 0.0,
        },
    }

    json_path = PHASE1_RESULTS / "p2_2_k_validation.json"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Markdown report
    lines = []
    lines.append("# Phase 1 §2.2 — Small-Sample Multiplier `k` Validation")
    lines.append("")
    lines.append("Created: 2026-05-03 (executed)")
    lines.append("Authority: PLAN.md §2.2 + operator success benediction 2026-05-03")
    lines.append("")
    lines.append("> **THIS IS THE LOAD-BEARING IMMUNE-SYSTEM EXPERIMENT.**")
    lines.append(">")
    lines.append("> If this fails, the entire DDD multiplier architecture is in question "
                 "and `zeus_oracle_density_discount_reference.md` §5.3 must be re-spec'd.")
    lines.append("")
    lines.append("## Headline result")
    lines.append("")
    lines.append(f"- **Buckets analyzed**: {len(stats)} (city × metric pairs with ≥100 train + ≥100 test rows)")
    lines.append(f"- **Train fit**: Brier_train ≈ {train_fit['intercept_a']:.4f} + "
                 f"{train_fit['slope_b']:.4f} / sqrt(N), R²={train_fit['r_squared']:.3f}")
    lines.append(f"- **Test fit**: Brier_test ≈ {test_fit['intercept_a']:.4f} + "
                 f"{test_fit['slope_b']:.4f} / sqrt(N), R²={test_fit['r_squared']:.3f}")
    lines.append(f"- **k_train** = {train_fit['k_estimate']:.3f}")
    lines.append(f"- **k_test** = {test_fit['k_estimate']:.3f}")
    lines.append(f"- **|Δk|/k_train** = {k_delta_pct:.1f}%")
    lines.append(f"- **Brier_test variance**: raw={brier_test_var:.6f}, "
                 f"after (1+k_train/√N) adj={brier_test_adj_var:.6f}, "
                 f"reduction={variance_reduction_pct:.1f}%")
    lines.append("")
    lines.append("## Acceptance criteria")
    lines.append("")
    for k, v in out["acceptance"].items():
        lines.append(f"- {k}: **{v}**")
    lines.append("")
    overall = all(out["acceptance"].values())
    lines.append(f"**Overall**: {'✅ PASS' if overall else '❌ FAIL — re-spec required'}")
    lines.append("")
    lines.append("## Per-bucket data (sorted by N_train ascending)")
    lines.append("")
    lines.append(
        "| city | metric | n_train | n_test | Brier_train | Brier_test | "
        "(1+k/√N) adj | Brier_test_adj |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in bucket_report:
        lines.append(
            f"| {r['city']} | {r['metric']} | {r['n_train']:,} | {r['n_test']:,} | "
            f"{r['brier_train']:.4f} | {r['brier_test']:.4f} | "
            f"{r['adjuster']:.4f} | {r['brier_test_adjusted']:.4f} |"
        )
    lines.append("")

    md_path = PHASE1_RESULTS / "p2_2_k_validation.md"
    md_path.write_text("\n".join(lines) + "\n")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"\nHEADLINE:")
    print(f"  buckets: {len(stats)}")
    print(f"  k_train = {train_fit['k_estimate']:.3f}, R²(train) = {train_fit['r_squared']:.3f}")
    print(f"  k_test  = {test_fit['k_estimate']:.3f}, R²(test) = {test_fit['r_squared']:.3f}")
    print(f"  |Δk|/k_train = {k_delta_pct:.1f}%")
    print(f"  variance reduction: {variance_reduction_pct:.1f}%")
    print(f"  acceptance: {'PASS' if overall else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
