"""PHASE 1 §2.3 — σ_window ACF analysis.

Created: 2026-05-03
Authority: PLAN.md §2.3 + canonical reference §5.2 (σ-band rule)

## Hypothesis

Daily directional coverage variance is Poisson-like white noise that does
not persist across days. If the autocorrelation function (ACF) of daily
coverage drops to near-zero within 3-5 days, a short sigma_window (30 days)
is sufficient to compute σ. If the ACF persists for 30+ days, we need a
longer window (60 or 90 days).

## Acceptance criteria

- **PASS**: ACF(lag=k) < 0.2 for all k ≥ 5 days → 30-day window sufficient
- **MARGINAL**: ACF(lag=k) crosses 0.2 between 5 and 30 days → use 60 days
- **FAIL**: ACF persists at >0.2 beyond 30 days → use 90 days, or σ-band
  signal is too noisy to be useful (need to revisit architecture)

Additionally, σ over the chosen window must be smaller than the typical
shortfall we care about (~0.10) to be a useful absorber. If σ > 0.10 on
many cities, the σ-band absorbs all anomalies — SNR too low.

## Method

For each "interesting" city (mix of stable + thin), compute:
  1. Per-day directional coverage in 2025 H2 + 2026 H1 (full available)
  2. ACF up to lag=14 days
  3. σ over 30-day, 60-day, 90-day rolling windows; report distribution

## Outputs

  phase1_results/p2_3_sigma_window_acf.{json,md}
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PHASE1_RESULTS = REPO / "docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results"
DB_PATH = REPO / "state" / "zeus-world.db"
CITIES_JSON = REPO / "config" / "cities.json"

# Mix of stable + thin cities — sample of 10 covering the regime spectrum
PROBE_CITIES = [
    "Tokyo",       # stable (1.0 every day)
    "Singapore",   # stable
    "Wellington",  # stable
    "Denver",      # stable + 1 outlier
    "NYC",         # stable
    "Lagos",       # high σ (0.178)
    "Shenzhen",    # mid σ
    "Jakarta",     # high σ
    "Lucknow",     # mid-high σ
    "Houston",     # stable
]


def directional_window(peak: float | None, radius: int = 3) -> list[int]:
    if peak is None:
        return list(range(24))
    c = int(round(peak))
    return [(h % 24) for h in range(c - radius, c + radius + 1)]


def per_day_coverage_series(
    conn: sqlite3.Connection, city: str, target_hours: list[int],
    start: str, end: str,
) -> list[tuple[str, float]]:
    """Return time-ordered list of (date_iso, coverage)."""
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
        ORDER BY target_date ASC
        """,
        (city, start, end),
    ).fetchall()
    return [(d, hrs / n_target) for d, hrs in rows]


def acf(values: list[float], max_lag: int) -> list[float]:
    """Autocorrelation function up to max_lag, computed as Pearson r at each lag."""
    n = len(values)
    if n < max_lag + 2:
        return [float("nan")] * (max_lag + 1)
    mean = sum(values) / n
    deviations = [v - mean for v in values]
    var = sum(d * d for d in deviations) / n
    if var == 0:
        # Constant series — ACF undefined
        return [1.0] + [0.0] * max_lag
    out = [1.0]
    for lag in range(1, max_lag + 1):
        cov = sum(deviations[i] * deviations[i + lag] for i in range(n - lag)) / n
        out.append(cov / var)
    return out


def rolling_sigma(values: list[float], window: int) -> list[float]:
    """Population std over each `window`-length tail."""
    out = []
    for i in range(window, len(values) + 1):
        chunk = values[i - window : i]
        m = sum(chunk) / window
        v = sum((x - m) ** 2 for x in chunk) / window
        out.append(math.sqrt(v))
    return out


def main() -> int:
    PHASE1_RESULTS.mkdir(parents=True, exist_ok=True)
    with open(CITIES_JSON) as f:
        cities_d = json.load(f)
    city_cfg = {c["name"]: c for c in cities_d["cities"]}

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    out: dict = {}
    for city in PROBE_CITIES:
        peak = city_cfg[city].get("historical_peak_hour")
        target_hours = directional_window(peak, 3)
        # Full available range — 2025-07-01 to 2026-04-30
        series = per_day_coverage_series(
            conn, city, target_hours, "2025-07-01", "2026-04-30"
        )
        if len(series) < 100:
            out[city] = {"status": "INSUFFICIENT_DATA", "n": len(series)}
            continue
        values = [v for _, v in series]
        # Compute ACF up to 14 days
        acf_vals = acf(values, max_lag=14)
        # Rolling σ for 30/60/90-day windows
        sigma_30 = rolling_sigma(values, 30) if len(values) >= 30 else []
        sigma_60 = rolling_sigma(values, 60) if len(values) >= 60 else []
        sigma_90 = rolling_sigma(values, 90) if len(values) >= 90 else []
        out[city] = {
            "n_days": len(values),
            "mean_cov": statistics.mean(values),
            "global_sigma": statistics.stdev(values) if len(values) > 1 else 0.0,
            "acf_lag_0_14": acf_vals,  # index 0=1.0, index 1=lag1, ...
            "sigma_30_stats": {
                "min": min(sigma_30) if sigma_30 else None,
                "median": statistics.median(sigma_30) if sigma_30 else None,
                "max": max(sigma_30) if sigma_30 else None,
                "n_windows": len(sigma_30),
            },
            "sigma_60_stats": {
                "min": min(sigma_60) if sigma_60 else None,
                "median": statistics.median(sigma_60) if sigma_60 else None,
                "max": max(sigma_60) if sigma_60 else None,
                "n_windows": len(sigma_60),
            },
            "sigma_90_stats": {
                "min": min(sigma_90) if sigma_90 else None,
                "median": statistics.median(sigma_90) if sigma_90 else None,
                "max": max(sigma_90) if sigma_90 else None,
                "n_windows": len(sigma_90),
            },
        }
    conn.close()

    json_path = PHASE1_RESULTS / "p2_3_sigma_window_acf.json"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Markdown report
    lines = []
    lines.append("# Phase 1 §2.3 — σ_window ACF Analysis")
    lines.append("")
    lines.append("Created: 2026-05-03 (executed)")
    lines.append("Authority: PLAN.md §2.3 + canonical reference §5.2")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("For each probe city, computed:")
    lines.append("- ACF of daily directional coverage at lags 1-14")
    lines.append("- Rolling σ over 30/60/90-day windows; report min/median/max")
    lines.append("")
    lines.append("Probe cities span the regime spectrum: stable (Tokyo, NYC), thin (Lagos, Jakarta), intermediate.")
    lines.append("")
    lines.append("## ACF table — does coverage variance persist?")
    lines.append("")
    lines.append("If ACF(lag=k) < 0.2 for all k ≥ 5, white noise dominates → 30-day window sufficient.")
    lines.append("")
    lines.append("| city | n_days | mean | σ_global | ACF(1) | ACF(2) | ACF(3) | ACF(5) | ACF(7) | ACF(14) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for city in PROBE_CITIES:
        s = out.get(city, {})
        if s.get("status") == "INSUFFICIENT_DATA":
            lines.append(f"| {city} | — | — | — | — | — | — | — | — | — |")
            continue
        a = s["acf_lag_0_14"]
        lines.append(
            f"| {city} | {s['n_days']} | {s['mean_cov']:.3f} | {s['global_sigma']:.3f} | "
            f"{a[1]:.3f} | {a[2]:.3f} | {a[3]:.3f} | {a[5]:.3f} | {a[7]:.3f} | {a[14]:.3f} |"
        )
    lines.append("")

    lines.append("## Rolling σ comparison — does longer window matter?")
    lines.append("")
    lines.append(
        "Each cell is `min/median/max` of σ over the rolling windows of given length. "
        "For runtime σ-band, σ ≈ median value is what gets used in shortfall computation."
    )
    lines.append("")
    lines.append("| city | σ_30 (min/med/max) | σ_60 (min/med/max) | σ_90 (min/med/max) |")
    lines.append("|---|---|---|---|")
    for city in PROBE_CITIES:
        s = out.get(city, {})
        if s.get("status") == "INSUFFICIENT_DATA":
            continue
        s30, s60, s90 = s["sigma_30_stats"], s["sigma_60_stats"], s["sigma_90_stats"]

        def fmt(triple_dict):
            if triple_dict["median"] is None:
                return "n/a"
            return f"{triple_dict['min']:.3f} / {triple_dict['median']:.3f} / {triple_dict['max']:.3f}"

        lines.append(f"| {city} | {fmt(s30)} | {fmt(s60)} | {fmt(s90)} |")
    lines.append("")

    # Verdict
    lines.append("## Verdict")
    lines.append("")
    # Collect ACF values at lag 5 across cities
    lag5_vals = []
    lag7_vals = []
    lag14_vals = []
    for city in PROBE_CITIES:
        s = out.get(city, {})
        if s.get("status") == "INSUFFICIENT_DATA":
            continue
        # Skip cities with constant series (Tokyo etc) — their ACF is degenerate
        if s["global_sigma"] < 0.001:
            continue
        a = s["acf_lag_0_14"]
        lag5_vals.append(abs(a[5]))
        lag7_vals.append(abs(a[7]))
        lag14_vals.append(abs(a[14]))

    if not lag5_vals:
        lines.append("All probe cities had constant coverage (σ_global ≈ 0) — ACF analysis degenerate. "
                     "Defaulting to 30-day window.")
        recommended_window = 30
    else:
        max_lag5 = max(lag5_vals)
        max_lag14 = max(lag14_vals)
        lines.append(f"- Across non-constant probe cities, max |ACF(lag=5)| = {max_lag5:.3f}")
        lines.append(f"- Across non-constant probe cities, max |ACF(lag=14)| = {max_lag14:.3f}")
        if max_lag5 < 0.2 and max_lag14 < 0.1:
            lines.append("- **PASS**: ACF decays quickly; 30-day window sufficient.")
            recommended_window = 30
        elif max_lag14 < 0.2:
            lines.append("- **MARGINAL**: ACF persists past lag 5; recommend 60-day window.")
            recommended_window = 60
        else:
            lines.append("- **WARN**: ACF persists past lag 14 in some cities; 90-day window recommended.")
            recommended_window = 90
    lines.append("")
    lines.append(f"**Recommended sigma_window = {recommended_window} days**")
    lines.append("")

    # SNR check
    lines.append("## SNR check — is σ small enough to be a useful absorber?")
    lines.append("")
    lines.append("Typical 'shortfall we care about' ≈ 0.10. If median σ > 0.10, the σ-band swallows all anomalies (false negatives).")
    lines.append("")
    snr_ok = True
    for city in PROBE_CITIES:
        s = out.get(city, {})
        if s.get("status") == "INSUFFICIENT_DATA":
            continue
        med = s[f"sigma_{recommended_window}_stats"]["median"]
        if med is None: continue
        if med > 0.10:
            snr_ok = False
            lines.append(f"- ⚠ {city}: σ_{recommended_window}_median = {med:.3f} > 0.10 — σ-band may absorb real anomalies")
    if snr_ok:
        lines.append("- All probe cities have σ_median ≤ 0.10 — σ-band is below shortfall threshold (good SNR).")
    lines.append("")

    md_path = PHASE1_RESULTS / "p2_3_sigma_window_acf.md"
    md_path.write_text("\n".join(lines) + "\n")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"recommended sigma_window = {recommended_window} days")
    if not snr_ok:
        print("⚠ SNR concern on some thin cities (σ > 0.10)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
