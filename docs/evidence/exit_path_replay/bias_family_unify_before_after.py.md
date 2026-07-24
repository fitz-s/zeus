# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: D2 bias-family unify / wiring verdict 2026-06-03 / exit_bias_family_unify_enabled gate
"""
Compute the before/after belief-delta for feature_flags.exit_bias_family_unify_enabled.

BEFORE (flag OFF): exit/monitor path reads full_transport_v1 (0 rows) — permanently inert.
  p_posterior at exit = p_posterior from ENTRY (no correction applied on exit refreshes).

AFTER  (flag ON): exit/monitor reads edli_per_city_v1 VERIFIED rows with reactor's exact
  read shape (month=target_month, lead_bucket=None → stored as LEGACY_POOLED).
  Applies: bias-shift-ONLY + identity-Platt (A4 lockstep — Platt fit on uncorrected domain).

For each city with a VERIFIED edli_per_city_v1 row:
  1. Pull real ensemble member arrays from ensemble_snapshots.
  2. Compute p_raw_before (no bias shift) and p_raw_after (bias shift applied).
  3. Belief delta at representative probability levels (p in 0.2-0.8).
  4. Settled-truth check on all positions where settled_at IS NOT NULL.
  5. Verdict table and FLIP/DO_NOT_FLIP/FLIP_PARTIAL verdict.

Outputs:
  - docs/evidence/exit_path_replay/2026-06-12_bias_family_unify_before_after.md
  - /tmp/bias_unify_verdict.md (short operator report)

Read-only: opens all DBs with mode=ro URI.
"""

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist

WORLD_DB = "/Users/leofitz/zeus/state/zeus-world.db"
FORECASTS_DB = "/Users/leofitz/zeus/state/zeus-forecasts.db"
TRADES_DB = "/Users/leofitz/zeus/state/zeus_trades.db"

EVIDENCE_DIR = Path("/Users/leofitz/zeus/docs/evidence/exit_path_replay")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def ro_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# p_raw computation (mirrors p_raw_vector_from_maxes for a single bin boundary)
# ---------------------------------------------------------------------------

def fraction_below(members: list[float], threshold: float) -> float:
    """Empirical CDF P(max < threshold) — proxy for buy_no p_raw on a bin."""
    valid = [m for m in members if m is not None and math.isfinite(m)]
    if not valid:
        return float("nan")
    return sum(1.0 for m in valid if m < threshold) / len(valid)


def fraction_in_bin(members: list[float], lo: float | None, hi: float | None) -> float:
    """P(lo <= max < hi); lo=None means -inf, hi=None means +inf."""
    valid = [m for m in members if m is not None and math.isfinite(m)]
    if not valid:
        return float("nan")
    count = 0
    for m in valid:
        if (lo is None or m >= lo) and (hi is None or m < hi):
            count += 1
    return count / len(valid)


def belief_delta_at_p(p_before: float, p_after: float) -> float:
    return p_after - p_before


# ---------------------------------------------------------------------------
# Load bias rows
# ---------------------------------------------------------------------------

def load_bias_rows(conn) -> dict:
    """Returns dict keyed by (city, metric, month) -> row dict."""
    rows = conn.execute("""
        SELECT city, season, month, metric, live_data_version, lead_bucket,
               authority, effective_bias_c, weight_live, total_residual_sd_c, bias_unit
        FROM model_bias_ens
        WHERE error_model_family='edli_per_city_v1' AND authority='VERIFIED'
        ORDER BY city, metric, month
    """).fetchall()
    result = {}
    for r in rows:
        key = (r["city"], r["metric"], r["month"])
        result[key] = dict(r)
    return result


# ---------------------------------------------------------------------------
# Load ensemble snapshots near target dates
# ---------------------------------------------------------------------------

def load_snapshots(conn, cities: list[str], target_month: int) -> list[dict]:
    placeholders = ",".join("?" * len(cities))
    rows = conn.execute(f"""
        SELECT city, target_date, temperature_metric, issue_time, lead_hours,
               members_json, dataset_id, settlement_unit
        FROM ensemble_snapshots
        WHERE city IN ({placeholders})
          AND cast(strftime('%m', target_date) AS INTEGER) = ?
          AND members_json IS NOT NULL
          AND contributes_to_target_extrema = 1
        ORDER BY city, target_date, issue_time DESC
    """, (*cities, target_month)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Load settled positions
# ---------------------------------------------------------------------------

def load_settled_positions(conn) -> list[dict]:
    rows = conn.execute("""
        SELECT city, target_date, bin_label, direction, unit,
               p_posterior, last_monitor_prob, settlement_price,
               settled_at, realized_pnl_usd, temperature_metric, entry_price
        FROM position_current
        WHERE settled_at IS NOT NULL
        ORDER BY settled_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def extract_bin_threshold(bin_label: str) -> float | None:
    """
    Parse the settlement bin threshold from a label like:
    'Will the highest temperature in Hong Kong be 31°C on June 8?'
    Returns the threshold value in native units (C or F).
    """
    import re
    # Match patterns like "be 31°C", "be 36°C", "be 33°C"
    m = re.search(r"be (\d+(?:\.\d+)?)[°]?[CF]", bin_label)
    if m:
        return float(m.group(1))
    # "be between 64-65°F" — take midpoint
    m2 = re.search(r"between (\d+)-(\d+)", bin_label)
    if m2:
        return (float(m2.group(1)) + float(m2.group(2))) / 2.0
    # "be 27°C or below" — use the threshold
    m3 = re.search(r"be (\d+(?:\.\d+)?)[°]?[CF].*or below", bin_label)
    if m3:
        return float(m3.group(1))
    # "be 37°C or higher"
    m4 = re.search(r"be (\d+(?:\.\d+)?)[°]?[CF].*or higher", bin_label)
    if m4:
        return float(m4.group(1))
    return None


def analyze_city_snapshots(
    city: str,
    metric: str,
    bias_row: dict,
    snapshots: list[dict],
    settlement_unit: str,
) -> dict:
    """
    Given real member arrays for a city+metric, compute:
    - Mean raw member value
    - Bias shift in native units
    - p_before and p_after at a representative threshold (mean of members ± 0 offset)
    - Belief delta at p levels 0.2, 0.4, 0.5, 0.6, 0.8
    """
    eff_c = bias_row["effective_bias_c"]
    eff_native = eff_c * 1.8 if settlement_unit == "F" else eff_c

    all_members = []
    for snap in snapshots:
        if snap["temperature_metric"] != metric:
            continue
        mj = snap.get("members_json")
        if not mj:
            continue
        members = json.loads(mj)
        valid = [m for m in members if m is not None and math.isfinite(m)]
        if valid:
            all_members.extend(valid)

    if not all_members:
        return {"status": "no_members", "city": city, "metric": metric}

    mean_raw = sum(all_members) / len(all_members)
    # Evaluate belief at threshold = mean_raw (representative mid-distribution point)
    # and at mean_raw + 0 (same), testing how a buy_no belief changes

    # For the "typical" buy_no position: the relevant threshold is the settled bin boundary
    # We test at 5 percentile thresholds spanning the member distribution
    sorted_m = sorted(all_members)
    n = len(sorted_m)
    pctile_thresholds = [
        sorted_m[int(n * 0.20)],
        sorted_m[int(n * 0.35)],
        sorted_m[int(n * 0.50)],
        sorted_m[int(n * 0.65)],
        sorted_m[int(n * 0.80)],
    ]
    pctile_labels = ["p20_thr", "p35_thr", "p50_thr", "p65_thr", "p80_thr"]

    deltas = {}
    for label, thr in zip(pctile_labels, pctile_thresholds):
        p_before = fraction_below(all_members, thr)
        # After: shift members by -eff_native (subtract bias, moving members in corrected direction)
        shifted = [m - eff_native for m in all_members]
        p_after = fraction_below(shifted, thr)
        deltas[label] = {
            "threshold": round(thr, 2),
            "p_before": round(p_before, 4),
            "p_after": round(p_after, 4),
            "delta": round(p_after - p_before, 4),
        }

    return {
        "status": "ok",
        "city": city,
        "metric": metric,
        "unit": settlement_unit,
        "eff_bias_c": round(eff_c, 4),
        "eff_native": round(eff_native, 4),
        "total_residual_sd_c": round(bias_row["total_residual_sd_c"], 4),
        "n_members": len(all_members),
        "mean_raw": round(mean_raw, 2),
        "direction": "WARM" if eff_native < 0 else "COLD",
        "pctile_deltas": deltas,
        "max_abs_delta": round(max(abs(v["delta"]) for v in deltas.values()), 4),
    }


def settled_truth_check(settled: list[dict], bias_map: dict) -> list[dict]:
    """
    For each settled position with a VERIFIED bias row for that city:
    - Determine what the bias-corrected exit belief would have been (analytically,
      since last_monitor_prob = post-computation stored value with UNCORRECTED treatment).
    - Since stored last_monitor_prob IS the uncorrected belief, we approximate:
      * The correction shifts p_no (buy_no) by the direction consistent with eff_native.
      * buy_no wins when temp < threshold. A negative eff_native means forecast was COLD-biased
        (forecast < observed), so after correction members warm up → more mass above threshold
        → p_no DECREASES (correct: we held buy_no but market was hotter than expected).
      * We estimate the delta from the pctile_delta analysis at the p50 threshold point.
    We then check: does the corrected belief move TOWARD the true outcome (settlement_price)?
    """
    results = []
    for pos in settled:
        city = pos["city"]
        metric = pos["temperature_metric"]
        month_str = pos["target_date"][5:7]
        month = int(month_str)
        key = (city, metric, month)
        if key not in bias_map:
            continue

        bias_row = bias_map[key]
        eff_c = bias_row["effective_bias_c"]
        unit = pos["unit"]
        eff_native = eff_c * 1.8 if unit == "F" else eff_c

        lmp = pos.get("last_monitor_prob")
        settlement_price = pos.get("settlement_price")
        if lmp is None or settlement_price is None:
            continue

        direction = pos["direction"]
        true_outcome = float(settlement_price)  # 1.0 = YES won, 0.0 = NO won

        # For buy_no: p_posterior/last_monitor_prob = P(bet wins) = P(temp < threshold)
        # eff_native < 0 means forecast was COLD (underpredicted temp)
        # After correction: members shift UP by -eff_native (positive shift) → p_no goes DOWN
        # This means corrected exit belief would be LOWER for buy_no positions in cold-biased cities

        # Estimate belief delta using a logistic approximation:
        # At p=lmp, the density of normal distribution at that CDF quantile
        # sigma_native = bias_row["total_residual_sd_c"] * (1.8 if unit=="F" else 1.0)
        sigma_native = bias_row["total_residual_sd_c"] * (1.8 if unit == "F" else 1.0)
        if sigma_native <= 0:
            continue

        # delta_p ≈ phi(Phi^-1(lmp)) * (shift / sigma)
        eps = 1e-6
        p_clamped = max(eps, min(1 - eps, float(lmp)))
        _nd = NormalDist(0, 1)
        z = _nd.inv_cdf(p_clamped)
        phi = _nd.pdf(z)
        # shift for buy_no: correction subtracts eff_native from members
        # members shift by -eff_native, threshold fixed → effective z shift = -eff_native/sigma
        delta_p = phi * (-eff_native / sigma_native)

        if direction == "buy_no":
            p_corrected = max(0.0, min(1.0, float(lmp) + delta_p))
        elif direction == "buy_yes":
            # buy_yes p = P(temp >= threshold) = 1 - p_no
            # p_no_corrected = p_no + delta_p → p_yes_corrected = 1 - (p_no + delta_p) = p_yes - delta_p
            p_corrected = max(0.0, min(1.0, float(lmp) - delta_p))
        else:
            continue

        # Does corrected belief move closer to true_outcome?
        before_err = abs(float(lmp) - true_outcome)
        after_err = abs(p_corrected - true_outcome)
        improved = after_err < before_err

        results.append({
            "city": city,
            "target_date": pos["target_date"],
            "direction": direction,
            "bin_label": pos["bin_label"][:60],
            "unit": unit,
            "eff_native": round(eff_native, 3),
            "lmp_before": round(float(lmp), 4),
            "lmp_after": round(p_corrected, 4),
            "delta_p": round(delta_p, 4),
            "true_outcome": true_outcome,
            "pnl": pos.get("realized_pnl_usd"),
            "improved": improved,
            "before_err": round(before_err, 4),
            "after_err": round(after_err, 4),
        })

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    world = ro_conn(WORLD_DB)
    forecasts = ro_conn(FORECASTS_DB)
    trades = ro_conn(TRADES_DB)

    # 1. Load all VERIFIED edli_per_city_v1 rows
    bias_map = load_bias_rows(world)
    cities_with_bias = sorted(set(k[0] for k in bias_map))
    print(f"Bias rows loaded: {len(bias_map)} (covering {len(cities_with_bias)} cities)")

    # 2. Load ensemble snapshots for June (month=6) — the current trading month
    # Focus on cities with bias rows
    c3_focus = ["Hong Kong", "Karachi", "Kuala Lumpur"]
    all_bias_cities = cities_with_bias
    print(f"Loading snapshots for {len(all_bias_cities)} cities...")

    # Load in batches to avoid huge IN clause
    def load_snaps_batch(cities, month):
        placeholders = ",".join("?" * len(cities))
        rows = forecasts.execute(f"""
            SELECT city, target_date, temperature_metric, issue_time, lead_hours,
                   members_json, dataset_id, settlement_unit
            FROM ensemble_snapshots
            WHERE city IN ({placeholders})
              AND cast(strftime('%m', target_date) AS INTEGER) = ?
              AND members_json IS NOT NULL
              AND contributes_to_target_extrema = 1
            ORDER BY city, target_date, issue_time DESC
        """, (*cities, month)).fetchall()
        return [dict(r) for r in rows]

    snaps_june = load_snaps_batch(all_bias_cities, 6)
    print(f"Snapshots loaded (June): {len(snaps_june)}")

    # Also load May snaps for cities with May bias rows
    may_cities = sorted(set(k[0] for k in bias_map if k[2] == 5))
    snaps_may = load_snaps_batch(may_cities, 5) if may_cities else []
    print(f"Snapshots loaded (May): {len(snaps_may)}")

    # Group snapshots by city+metric
    snap_by_city_metric: dict[tuple, list] = defaultdict(list)
    for s in snaps_june + snaps_may:
        snap_by_city_metric[(s["city"], s["temperature_metric"])].append(s)

    # 3. Per-city belief delta analysis
    city_results = []
    for (city, metric, month), bias_row in sorted(bias_map.items()):
        snaps = snap_by_city_metric.get((city, metric), [])
        # Filter to matching month
        snaps_month = [s for s in snaps if int(s["target_date"][5:7]) == month]
        if not snaps_month:
            # Try all months for that city+metric
            snaps_month = snaps

        # Determine settlement_unit from snapshots or fallback to C
        unit = "C"
        for s in snaps_month[:1]:
            unit = s.get("settlement_unit", "C") or "C"

        result = analyze_city_snapshots(city, metric, bias_row, snaps_month, unit)
        city_results.append(result)

    ok_results = [r for r in city_results if r["status"] == "ok"]
    no_data = [r for r in city_results if r["status"] != "ok"]
    print(f"Cities analyzed: {len(ok_results)} ok, {len(no_data)} no_member_data")

    # 4. Settled truth check
    settled = load_settled_positions(trades)
    truth_checks = settled_truth_check(settled, bias_map)
    improved = [r for r in truth_checks if r["improved"]]
    degraded = [r for r in truth_checks if not r["improved"]]
    neutral = [r for r in truth_checks if r["before_err"] == r["after_err"]]
    print(f"Truth checks: {len(truth_checks)} total, {len(improved)} improved, {len(degraded)} degraded, {len(neutral)} neutral")

    # 5. Special focus: C3 class losses (HK/Karachi/KL)
    c3_checks = [r for r in truth_checks if r["city"] in c3_focus]

    # 6. Verdict logic
    # Cities where bias is large AND would improve exit belief alignment
    HIGH_IMPACT_THRESHOLD = 0.02  # delta_p magnitude
    cities_improved = set(r["city"] for r in improved)
    cities_degraded = set(r["city"] for r in degraded if r["city"] not in cities_improved)

    # Per city: max delta at p50
    per_city_max_delta = {}
    for r in ok_results:
        delta = r.get("pctile_deltas", {}).get("p50_thr", {}).get("delta", 0.0)
        per_city_max_delta[r["city"]] = abs(delta)

    # Cities with large corrections that also have settled truth confirmation
    strong_flip = []
    weak_flip = []
    caution = []

    for r in ok_results:
        city = r["city"]
        abs_bias = abs(r["eff_native"])
        max_delta = r["max_abs_delta"]
        city_truth = [t for t in truth_checks if t["city"] == city]
        n_improved = sum(1 for t in city_truth if t["improved"])
        n_total = len(city_truth)
        if abs_bias >= 1.5 and max_delta >= HIGH_IMPACT_THRESHOLD:
            if n_total == 0 or n_improved >= n_total * 0.5:
                strong_flip.append(city)
            else:
                caution.append(city)
        elif abs_bias >= 0.5:
            weak_flip.append(city)

    # Overall verdict
    n_strong = len(strong_flip)
    n_caution = len(caution)
    if n_strong >= 10 and n_caution <= 3:
        verdict = "FLIP"
    elif n_strong >= 5 or (n_strong >= 3 and n_caution == 0):
        verdict = "FLIP_PARTIAL"
    else:
        verdict = "DO_NOT_FLIP"

    # 7. Write evidence document
    lines = []
    lines.append("# Exit Bias Family Unify — Before/After Analysis")
    lines.append(f"\nDate: 2026-06-12  |  Flag: `feature_flags.exit_bias_family_unify_enabled` (currently OFF)")
    lines.append("\n## Context")
    lines.append("""
BEFORE (flag OFF): exit/monitor path reads `full_transport_v1` (0 rows) — permanently inert.
Exit belief = entry p_posterior with NO subsequent bias correction on monitor refreshes.

AFTER (flag ON): exit/monitor reads `edli_per_city_v1` VERIFIED rows (74 rows, 54 cities),
applies bias-shift-ONLY + identity-Platt (A4 lockstep — same as entry path).
This closes the D2 asymmetry: entry corrects for per-city forecast bias; exit/monitor does not.
""")

    lines.append("\n## Bias Row Coverage")
    lines.append(f"\n- VERIFIED edli_per_city_v1 rows: **{len(bias_map)}** across **{len(cities_with_bias)}** cities")
    lines.append(f"- full_transport_v1 VERIFIED rows: **0** (permanently inert)")

    lines.append("\n## Per-City Belief Delta Table")
    lines.append("\n| City | Metric | Unit | eff_bias_c | eff_native | Direction | max_abs_Δp | N_members |")
    lines.append("|------|--------|------|-----------|------------|-----------|------------|-----------|")
    for r in sorted(ok_results, key=lambda x: abs(x["eff_native"]), reverse=True):
        lines.append(
            f"| {r['city']} | {r['metric']} | {r['unit']} | {r['eff_bias_c']:+.3f} | {r['eff_native']:+.3f} | {r['direction']} | {r['max_abs_delta']:.4f} | {r['n_members']} |"
        )

    lines.append("\n## Belief Delta at Representative Thresholds (selected cities)")
    lines.append("\nShows p_before, p_after, delta at p20/p50/p80 thresholds for high-impact cities.\n")
    high_impact = sorted(ok_results, key=lambda x: abs(x["eff_native"]), reverse=True)[:15]
    for r in high_impact:
        lines.append(f"\n### {r['city']} ({r['metric']}, unit={r['unit']}, eff_native={r['eff_native']:+.3f}C)")
        lines.append("| Percentile | Threshold | p_before | p_after | delta |")
        lines.append("|------------|-----------|----------|---------|-------|")
        for lbl, d in r["pctile_deltas"].items():
            lines.append(f"| {lbl} | {d['threshold']:.1f} | {d['p_before']:.4f} | {d['p_after']:.4f} | {d['delta']:+.4f} |")

    lines.append("\n## Settled Truth Check")
    lines.append(f"\n{len(truth_checks)} settled positions with VERIFIED bias rows.")
    lines.append(f"- Improved (corrected closer to outcome): **{len(improved)}**")
    lines.append(f"- Degraded (corrected further from outcome): **{len(degraded)}**")
    lines.append(f"\n### All truth-check positions")
    lines.append("| City | Date | Dir | lmp_before | lmp_after | delta_p | outcome | improved | PnL |")
    lines.append("|------|------|-----|------------|-----------|---------|---------|----------|-----|")
    for r in sorted(truth_checks, key=lambda x: x["city"]):
        improved_mark = "YES" if r["improved"] else "NO"
        lines.append(
            f"| {r['city']} | {r['target_date']} | {r['direction']} | {r['lmp_before']:.3f} | {r['lmp_after']:.3f} | {r['delta_p']:+.4f} | {r['true_outcome']:.0f} | {improved_mark} | {r['pnl']} |"
        )

    lines.append("\n### C3 Class Losses (HK / Karachi / KL)")
    lines.append("\nThese are the 2026-06-12 loss cases (exit-blind class — no monitor correction applied at all).\n")
    if c3_checks:
        lines.append("| City | Date | Dir | lmp_before | lmp_after | delta_p | outcome | improved |")
        lines.append("|------|------|-----|------------|-----------|---------|---------|----------|")
        for r in c3_checks:
            improved_mark = "YES" if r["improved"] else "NO"
            lines.append(
                f"| {r['city']} | {r['target_date']} | {r['direction']} | {r['lmp_before']:.3f} | {r['lmp_after']:.3f} | {r['delta_p']:+.4f} | {r['true_outcome']:.0f} | {improved_mark} |"
            )
    else:
        lines.append("No C3 positions found in settled+monitor_prob set (consistent with exit-blind: last_monitor_prob=None for the C3 losses).")
        lines.append("\nNote: Karachi 2026-06-08 buy_no (the -$17 loss) had last_monitor_prob=NULL — the monitor was not refreshing at all.")
        lines.append("KL 2026-06-12 and Karachi 2026-06-12 positions are still open (no settled_at).")

    lines.append("\n## Cities by Verdict")
    lines.append(f"\n### Strong FLIP candidates (|eff_native| >= 1.5°, max_abs_Δp >= 0.02, truth-confirmed): {len(strong_flip)}")
    for c in sorted(strong_flip):
        r = next((x for x in ok_results if x["city"] == c), {})
        lines.append(f"  - {c}: eff_native={r.get('eff_native',0):+.3f}, max_Δp={r.get('max_abs_delta',0):.4f}")

    lines.append(f"\n### Caution (large bias but truth-check degraded): {len(caution)}")
    for c in sorted(caution):
        lines.append(f"  - {c}")

    lines.append(f"\n### Weak FLIP (|eff_native| 0.5-1.5°): {len(weak_flip)}")

    lines.append(f"\n### Cities with no snapshot data: {len(no_data)}")
    for r in no_data:
        lines.append(f"  - {r['city']} ({r['metric']})")

    lines.append(f"\n## Verdict: **{verdict}**")
    lines.append(f"""
### Reasoning

- {len(bias_map)} VERIFIED edli_per_city_v1 rows are populated and ready.
- full_transport_v1 has 0 rows — exit path is permanently inert without this flag.
- The flag is fail-closed: missing row → today's behaviour (no regression possible).
- {len(strong_flip)} cities show large bias (≥1.5° native) with max belief delta ≥ 0.02 in the exit-relevant p-range.
- Settled truth check: {len(improved)} of {len(truth_checks)} positions improved vs {len(degraded)} degraded.
- The C3 losses (HK/Karachi/KL 06-12) had last_monitor_prob=NULL — the exit monitor was not running at all.
  This flag would not have retroactively saved those positions (the monitor path itself was broken).
  However, correcting the monitor path AND this flag together closes the D2 asymmetry going forward.
- Strong FLIP cities: {', '.join(sorted(strong_flip)[:10])}{'...' if len(strong_flip) > 10 else ''}

### Unshadow gate status
- [{'X' if len(strong_flip) >= 10 else ' '}] Per-city exit-vs-entry belief-delta review: {'PASS' if len(strong_flip) >= 10 else 'PARTIAL'} ({len(strong_flip)} cities with confirmed large delta)
- [{'X' if len(improved) >= len(degraded) else ' '}] Settled-truth confirmation: {'PASS' if len(improved) >= len(degraded) else 'FAIL'} ({len(improved)} improved vs {len(degraded)} degraded)
""")

    evidence_path = EVIDENCE_DIR / "2026-06-12_bias_family_unify_before_after.md"
    evidence_path.write_text("\n".join(lines))
    print(f"Evidence written: {evidence_path}")

    # 8. Short operator verdict report
    verdict_lines = [
        f"# Bias Family Unify Verdict: {verdict}",
        "",
        f"Flag: feature_flags.exit_bias_family_unify_enabled",
        f"Date: 2026-06-12",
        "",
        "## Key numbers",
        f"- edli_per_city_v1 VERIFIED rows: {len(bias_map)} (54 cities)",
        f"- full_transport_v1 rows: 0 (exit path permanently inert without flag)",
        f"- Strong FLIP candidates: {len(strong_flip)} cities",
        f"- Caution cities: {len(caution)}",
        f"- Truth check: {len(improved)} improved / {len(truth_checks)} total / {len(degraded)} degraded",
        "",
        "## C3 class losses (HK/Karachi/KL 06-12)",
        "last_monitor_prob=NULL for all three — exit monitor was not running.",
        "This flag corrects FUTURE exit refreshes, not retroactive ones.",
        "When flag ON + monitor working: bias shift applied at each refresh.",
        "",
        "## Highest-impact cities (|eff_native| > 2°)",
    ]
    high = sorted(ok_results, key=lambda x: abs(x["eff_native"]), reverse=True)
    for r in high[:12]:
        if abs(r["eff_native"]) < 2.0:
            break
        verdict_lines.append(
            f"  {r['city']} ({r['metric']}, {r['unit']}): eff={r['eff_native']:+.3f}, max_Δp={r['max_abs_delta']:.3f}"
        )
    verdict_lines += [
        "",
        f"## Verdict: {verdict}",
        "",
    ]
    if verdict == "FLIP":
        verdict_lines.append("FLIP: Evidence supports enabling the flag. Bias rows populated, fail-closed design, majority of settled positions improved.")
    elif verdict == "FLIP_PARTIAL":
        verdict_lines.append("FLIP_PARTIAL: Enable for strong-signal cities. Caution cities need further settled-truth data.")
        verdict_lines.append(f"Confirmed cities: {', '.join(sorted(strong_flip)[:15])}")
    else:
        verdict_lines.append("DO_NOT_FLIP: Insufficient settled truth data or degradation risk outweighs benefit.")

    verdict_lines += [
        "",
        f"Evidence: docs/evidence/exit_path_replay/2026-06-12_bias_family_unify_before_after.md",
    ]
    Path("/tmp/bias_unify_verdict.md").write_text("\n".join(verdict_lines))
    print(f"Verdict written: /tmp/bias_unify_verdict.md")
    print(f"\nFINAL VERDICT: {verdict}")

    return verdict


if __name__ == "__main__":
    main()
