#!/usr/bin/env python3
# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-03_ddd_implementation_plan/RERUN_PLAN_v2.md
#                  + MATH_REALITY_OPTIMUM_ANALYSIS.md (operator decision: implement v2; build comparison test)
"""DDD v1 → v2 historical replay & comparison.

For every (city, target_date, metric ∈ {high, low}) in the test window
2026-01-01 → 2026-04-30, compute side-by-side what v1 (σ-band + 5-seg curve)
and v2 (Two-Rail + linear curve) would emit, plus the actual winning-bucket
Platt probability (for an EV proxy).

Excluded:
- Paris (workstream A DB resync still pending)
- HK / Istanbul / Moscow / Tel Aviv (no wu_icao_history primary data)

Outputs:
- phase1_results/v1_vs_v2_replay.json   per-decision rows + aggregates
- phase1_results/v1_vs_v2_replay.md     human-readable report
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PHASE1_RESULTS = (
    ROOT
    / "docs"
    / "operations"
    / "task_2026-05-03_ddd_implementation_plan"
    / "phase1_results"
)
DB_PATH = ROOT / "state" / "zeus-world.db"
CITIES_JSON = ROOT / "config" / "cities.json"

V1_FLOORS_PATH = PHASE1_RESULTS / "p2_1_FINAL_per_city_floors.json"
V2_FLOORS_PATH = PHASE1_RESULTS / "p2_1_FINAL_v2_per_city_floors.json"
H1_DATA_PATH = PHASE1_RESULTS / "p2_rerun_v2_h1_fix.json"

OUT_JSON = PHASE1_RESULTS / "v1_vs_v2_replay.json"
OUT_MD = PHASE1_RESULTS / "v1_vs_v2_replay.md"

TRAIN_START = "2025-07-01"
TEST_START = "2026-01-01"
TEST_END = "2026-04-30"
SIGMA_LOOKBACK_DAYS = 90
WINDOW_RADIUS = 3  # ±3 hours (HIGH/LOW directional window)

EXCLUDE_NULL_FLOOR = {"Hong Kong", "Istanbul", "Moscow", "Tel Aviv"}
# Paris was excluded pre-2026-05-03 pending workstream A LFPB resync;
# resync completed (agent a4c238d864a25ed71) → Paris included from now on.
EXCLUDE_PARIS: set[str] = set()

# v2 constants (mirror src/oracle/data_density_discount.py)
ABSOLUTE_KILL_FLOOR = 0.35
WINDOW_ELAPSED_THRESHOLD = 0.50
LINEAR_ALPHA = 0.20
MAX_DISCOUNT = 0.09


# ── helpers ────────────────────────────────────────────────────────────────────

def date_iter(start: str, end: str):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while s <= e:
        yield s.isoformat()
        s += timedelta(days=1)


def directional_window(peak: float | None, radius: int = WINDOW_RADIUS) -> list[int]:
    if peak is None:
        return list(range(0, 24))
    p = round(peak)
    return [(p + d) % 24 for d in range(-radius, radius + 1)]


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


def derive_low_window(conn, city, fallback_peak) -> list[int]:
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
        low_h = round((float(fallback_peak) - 12) % 24)
        return directional_window(float(low_h))
    return directional_window(float(rows[0][0]))


def rolling_sigma_90(cov_by_date: dict[str, float], dates: list[str]) -> dict[str, float]:
    """Per-date σ_90: stdev of [D-90, D-1] cov values from full-calendar series.

    Mirrors p2_rerun_v2_h1_fix.py's σ_90 logic.
    """
    out: dict[str, float] = {}
    for d_iso in dates:
        d = date.fromisoformat(d_iso)
        lo = (d - timedelta(days=SIGMA_LOOKBACK_DAYS)).isoformat()
        hi = (d - timedelta(days=1)).isoformat()
        window_vals = [
            v for k, v in cov_by_date.items()
            if lo <= k <= hi
        ]
        if len(window_vals) >= 2:
            out[d_iso] = statistics.stdev(window_vals)
        else:
            out[d_iso] = 0.0
    return out


# ── v1 / v2 discount computation ─────────────────────────────────────────────

def v1_discount(floor_v1: float, cov: float, sigma_90: float) -> tuple[float, float]:
    """v1: σ-band trigger + 5-segment piecewise linear.

    shortfall_v1 = max(0, floor - cov - σ_90)
    Returns (discount, shortfall_v1).
    """
    sf = max(0.0, floor_v1 - cov - sigma_90)
    # 5-segment: 0/2/5/8/9 caps at break 0.05, 0.10, 0.20, 0.30
    if sf <= 0.0:
        d = 0.0
    elif sf < 0.05:
        # linear 0 → 0.02
        d = 0.02 * (sf / 0.05)
    elif sf < 0.10:
        # linear 0.02 → 0.05
        d = 0.02 + (0.05 - 0.02) * ((sf - 0.05) / 0.05)
    elif sf < 0.20:
        # linear 0.05 → 0.08
        d = 0.05 + (0.08 - 0.05) * ((sf - 0.10) / 0.10)
    elif sf < 0.30:
        # linear 0.08 → 0.09
        d = 0.08 + (0.09 - 0.08) * ((sf - 0.20) / 0.10)
    else:
        d = 0.09
    return d, sf


def v2_action(floor_v2: float, cov: float) -> tuple[str, float, float]:
    """v2: Two-Rail. Halt if cov<0.35 (assume window_elapsed>0.5 always for replay).

    Returns (action, discount, shortfall_v2).
    """
    if cov < ABSOLUTE_KILL_FLOOR:
        return "HALT", 0.0, 0.0
    sf = max(0.0, floor_v2 - cov)
    d = min(MAX_DISCOUNT, LINEAR_ALPHA * sf)
    return "DISCOUNT", d, sf


# ── core ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"[DDD-replay] DB: {DB_PATH}")
    if not DB_PATH.exists():
        print(f"  ERROR: {DB_PATH} not found", file=sys.stderr)
        return 1

    cities = json.loads(CITIES_JSON.read_text())
    if isinstance(cities, dict) and "cities" in cities:
        cities = cities["cities"]

    v1_floors_doc = json.loads(V1_FLOORS_PATH.read_text())
    v1_floors = v1_floors_doc.get("per_city_floors", v1_floors_doc)
    v2_floors_doc = json.loads(V2_FLOORS_PATH.read_text())
    v2_per_city = v2_floors_doc["per_city"]

    conn = open_db()
    rows: list[dict] = []
    skipped_reasons: dict[str, int] = defaultdict(int)
    halt_dates_per_city: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    archetype_records: dict[str, list[dict]] = {
        "Lagos": [], "Denver": [], "Jakarta": [], "Tokyo": [],
    }

    for ci in cities:
        city = ci["name"]
        if city in EXCLUDE_NULL_FLOOR:
            skipped_reasons["null_floor"] += 1
            continue
        if city in EXCLUDE_PARIS:
            skipped_reasons["paris_pending"] += 1
            continue

        v2_entry = v2_per_city.get(city, {})
        if "status" in v2_entry:
            skipped_reasons[f"v2_status_{v2_entry['status']}"] += 1
            continue
        floor_v2 = float(v2_entry["final_floor"])
        floor_v1 = v1_floors.get(city)
        if floor_v1 is None:
            skipped_reasons["v1_floor_null"] += 1
            continue
        floor_v1 = float(floor_v1)

        peak_h = ci.get("historical_peak_hour")
        if peak_h is None:
            skipped_reasons["no_peak_hour"] += 1
            continue
        high_hrs = directional_window(peak_h)
        explicit_low = ci.get("historical_low_hour")
        if explicit_low is not None:
            low_hrs = directional_window(explicit_low)
        else:
            low_hrs = derive_low_window(conn, city, peak_h)

        # full-calendar cov for σ_90 lookback + test window
        sigma_start = (
            date.fromisoformat(TEST_START) - timedelta(days=SIGMA_LOOKBACK_DAYS)
        ).isoformat()
        hcov_full = fetch_cov_full(conn, city, high_hrs, sigma_start, TEST_END)
        lcov_full = fetch_cov_full(conn, city, low_hrs, sigma_start, TEST_END)

        test_dates = list(date_iter(TEST_START, TEST_END))
        h_sigma = rolling_sigma_90(hcov_full, test_dates)
        l_sigma = rolling_sigma_90(lcov_full, test_dates)

        # winning-bucket Platt probabilities (per metric, per date)
        cal = conn.execute(
            """
            SELECT target_date, temperature_metric, p_raw
            FROM calibration_pairs_v2
            WHERE city = ? AND authority = 'VERIFIED'
              AND training_allowed = 1 AND outcome = 1
              AND target_date >= ? AND target_date <= ?
            """,
            (city, TEST_START, TEST_END),
        ).fetchall()
        p_high: dict[str, list[float]] = defaultdict(list)
        p_low: dict[str, list[float]] = defaultdict(list)
        for td, m, p_raw in cal:
            if m == "high":
                p_high[td].append(p_raw)
            elif m == "low":
                p_low[td].append(p_raw)

        for d_iso in test_dates:
            for metric, cov_full, sigma_d, p_map in (
                ("high", hcov_full, h_sigma, p_high),
                ("low", lcov_full, l_sigma, p_low),
            ):
                cov = cov_full.get(d_iso, 0.0)
                sigma_90 = sigma_d.get(d_iso, 0.0)
                p_winning = (
                    statistics.median(p_map[d_iso]) if p_map.get(d_iso) else None
                )
                d_v1, sf_v1 = v1_discount(floor_v1, cov, sigma_90)
                action_v2, d_v2, sf_v2 = v2_action(floor_v2, cov)

                row = {
                    "city": city,
                    "date": d_iso,
                    "metric": metric,
                    "cov": round(cov, 6),
                    "sigma_90": round(sigma_90, 6),
                    "floor_v1": floor_v1,
                    "floor_v2": floor_v2,
                    "shortfall_v1": round(sf_v1, 6),
                    "shortfall_v2": round(sf_v2, 6),
                    "discount_v1": round(d_v1, 6),
                    "discount_v2": round(d_v2, 6),
                    "action_v2": action_v2,
                    "delta": round(d_v2 - d_v1, 6),  # +ve: v2 stricter
                    "p_winning": round(p_winning, 6) if p_winning is not None else None,
                    "had_winner": p_winning is not None,
                }
                rows.append(row)
                if action_v2 == "HALT":
                    halt_dates_per_city[city].append((d_iso, metric, cov))
                if city in archetype_records:
                    archetype_records[city].append(row)

    conn.close()

    print(f"[DDD-replay] rows: {len(rows)}; skipped: {dict(skipped_reasons)}")
    return _write_outputs(rows, halt_dates_per_city, archetype_records, skipped_reasons)


# ── aggregation + report ─────────────────────────────────────────────────────

def _write_outputs(
    rows: list[dict],
    halt_dates_per_city: dict,
    archetype_records: dict,
    skipped_reasons: dict,
) -> int:
    n_total = len(rows)
    n_diff = sum(1 for r in rows if abs(r["delta"]) > 1e-9)
    n_halt = sum(1 for r in rows if r["action_v2"] == "HALT")
    # Within DISCOUNT-mode rows only — HALT is always strictest (discount==0 placeholder
    # but the trade is killed). Counting HALT rows under "looser" because raw delta<0
    # is misleading.
    n_v2_stricter = sum(
        1 for r in rows
        if r["action_v2"] == "DISCOUNT" and r["delta"] > 1e-9
    )
    n_v2_looser_genuine = sum(
        1 for r in rows
        if r["action_v2"] == "DISCOUNT" and r["delta"] < -1e-9
    )
    n_v2_halt_v1_discount = sum(
        1 for r in rows
        if r["action_v2"] == "HALT" and r["discount_v1"] > 1e-9
    )
    n_v2_halt_v1_zero = sum(
        1 for r in rows
        if r["action_v2"] == "HALT" and r["discount_v1"] <= 1e-9
    )
    deltas_nonzero = [r["delta"] for r in rows if abs(r["delta"]) > 1e-9]
    mean_abs_delta = statistics.mean(abs(d) for d in deltas_nonzero) if deltas_nonzero else 0.0
    median_abs_delta = statistics.median(abs(d) for d in deltas_nonzero) if deltas_nonzero else 0.0

    # per-city aggregates
    by_city: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "n_halt": 0, "n_diff": 0,
        "sum_d_v1": 0.0, "sum_d_v2": 0.0, "sum_delta": 0.0,
        "max_pos_delta": 0.0, "max_neg_delta": 0.0,
        "n_v1_zero_v2_zero": 0, "n_v1_pos_v2_zero": 0,
        "n_v1_zero_v2_pos": 0, "n_both_pos": 0,
    })
    for r in rows:
        c = r["city"]
        st = by_city[c]
        st["n"] += 1
        if r["action_v2"] == "HALT":
            st["n_halt"] += 1
        if abs(r["delta"]) > 1e-9:
            st["n_diff"] += 1
        st["sum_d_v1"] += r["discount_v1"]
        st["sum_d_v2"] += r["discount_v2"]
        st["sum_delta"] += r["delta"]
        if r["delta"] > st["max_pos_delta"]:
            st["max_pos_delta"] = r["delta"]
        if r["delta"] < st["max_neg_delta"]:
            st["max_neg_delta"] = r["delta"]
        v1z = r["discount_v1"] < 1e-9
        v2z = r["discount_v2"] < 1e-9 and r["action_v2"] != "HALT"
        if v1z and v2z:
            st["n_v1_zero_v2_zero"] += 1
        elif (not v1z) and v2z:
            st["n_v1_pos_v2_zero"] += 1
        elif v1z and (not v2z):
            st["n_v1_zero_v2_pos"] += 1
        else:
            st["n_both_pos"] += 1

    per_city_table = []
    for c, st in sorted(by_city.items()):
        n = max(st["n"], 1)
        per_city_table.append({
            "city": c,
            "n": st["n"],
            "n_halt": st["n_halt"],
            "n_diff": st["n_diff"],
            "mean_d_v1": st["sum_d_v1"] / n,
            "mean_d_v2": st["sum_d_v2"] / n,
            "mean_delta": st["sum_delta"] / n,
            "max_pos_delta": st["max_pos_delta"],
            "max_neg_delta": st["max_neg_delta"],
        })

    # EV/Kelly proxy: notional kelly_v1 = (1 - d_v1); kelly_v2 = 0 if HALT else (1 - d_v2)
    # Restrict to rows where we have winning-bucket Platt prob (p_winning).
    ev_rows = [r for r in rows if r["had_winner"]]
    n_ev = len(ev_rows)

    def _kelly(r):
        kv1 = max(0.0, 1.0 - r["discount_v1"])
        kv2 = 0.0 if r["action_v2"] == "HALT" else max(0.0, 1.0 - r["discount_v2"])
        return kv1, kv2

    sum_kelly_v1 = sum(_kelly(r)[0] for r in ev_rows)
    sum_kelly_v2 = sum(_kelly(r)[1] for r in ev_rows)
    # signed by (p_winning - 0.5) as a directional EV proxy: positive p_winning → kelly worth more
    sum_signed_v1 = sum(_kelly(r)[0] * (r["p_winning"] - 0.5) for r in ev_rows)
    sum_signed_v2 = sum(_kelly(r)[1] * (r["p_winning"] - 0.5) for r in ev_rows)

    archetype_summaries = {}
    for c, recs in archetype_records.items():
        if not recs:
            continue
        n_h = sum(1 for r in recs if r["action_v2"] == "HALT")
        n_zero_cov = sum(1 for r in recs if r["cov"] < 1e-9)
        sum_d_v1 = sum(r["discount_v1"] for r in recs)
        sum_d_v2 = sum(r["discount_v2"] for r in recs)
        archetype_summaries[c] = {
            "n": len(recs),
            "n_halt": n_h,
            "n_zero_cov": n_zero_cov,
            "mean_d_v1": sum_d_v1 / len(recs),
            "mean_d_v2": sum_d_v2 / len(recs),
        }

    out = {
        "_metadata": {
            "created": "2026-05-03",
            "authority": "RERUN_PLAN_v2.md (post-implementation comparison test)",
            "test_window": [TEST_START, TEST_END],
            "v1_curve": "5-segment piecewise (0/2/5/8/9% at break 0.05/0.10/0.20/0.30)",
            "v2_curve": "linear D = min(0.09, 0.20 × shortfall) + Two-Rail kill",
            "skipped_reasons": dict(skipped_reasons),
            "n_rows": n_total,
        },
        "aggregate": {
            "n_total": n_total,
            "n_diff": n_diff,
            "n_halt_v2": n_halt,
            "n_v2_stricter_discount": n_v2_stricter,
            "n_v2_genuinely_looser": n_v2_looser_genuine,
            "n_v2_halt_v1_would_discount": n_v2_halt_v1_discount,
            "n_v2_halt_v1_was_zero": n_v2_halt_v1_zero,
            "mean_abs_delta_when_diff": mean_abs_delta,
            "median_abs_delta_when_diff": median_abs_delta,
            "n_with_winner": n_ev,
            "sum_kelly_v1": sum_kelly_v1,
            "sum_kelly_v2": sum_kelly_v2,
            "kelly_delta_pct": (
                (sum_kelly_v2 - sum_kelly_v1) / sum_kelly_v1 * 100
                if sum_kelly_v1 > 0 else 0.0
            ),
            "sum_signed_kelly_v1": sum_signed_v1,
            "sum_signed_kelly_v2": sum_signed_v2,
        },
        "per_city": per_city_table,
        "halt_dates": {
            c: [{"date": d, "metric": m, "cov": round(cov, 4)} for d, m, cov in v]
            for c, v in halt_dates_per_city.items()
        },
        "archetype_summaries": archetype_summaries,
        "archetype_records": archetype_records,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # markdown report
    lines = [
        "# DDD v1 → v2 Replay Comparison",
        "",
        "Created: 2026-05-03  Authority: RERUN_PLAN_v2.md",
        "",
        f"Test window: {TEST_START} → {TEST_END}",
        "Excluded: Paris (workstream A pending); HK/Istanbul/Moscow/Tel Aviv (no train data)",
        "",
        "## Headline",
        "",
        f"- Total decisions: **{n_total:,}**",
        f"- v2 differs from v1 on: **{n_diff:,}** decisions ({n_diff/n_total*100:.1f}%)",
        f"  - v2 stricter discount (DISCOUNT-mode, Δ>0): **{n_v2_stricter:,}**",
        f"  - v2 genuinely looser (DISCOUNT-mode, Δ<0): **{n_v2_looser_genuine:,}**",
        f"  - v2 HALT where v1 would have discounted: **{n_v2_halt_v1_discount:,}**",
        f"  - v2 HALT where v1 emitted 0%: **{n_v2_halt_v1_zero:,}**",
        f"- v2 total HALT count: **{n_halt:,}** (all stricter than any v1 outcome)",
        f"- mean |Δ discount| (when differ): **{mean_abs_delta:.4f}**",
        f"- median |Δ discount| (when differ): **{median_abs_delta:.4f}**",
        "",
        "## Kelly Notional Proxy",
        "",
        f"- decisions with winning-bucket Platt prob: **{n_ev:,}**",
        f"- sum(1 - discount_v1) = **{sum_kelly_v1:.2f}**",
        f"- sum(1 - discount_v2)  [HALT → 0] = **{sum_kelly_v2:.2f}**",
        f"- v2 Kelly notional vs v1: **{(sum_kelly_v2 - sum_kelly_v1) / sum_kelly_v1 * 100:+.2f}%**",
        f"- p-weighted (signed by p−0.5) v1 = **{sum_signed_v1:.2f}**, v2 = **{sum_signed_v2:.2f}**",
        "",
        "## Per-city Breakdown",
        "",
        "| city | n | n_halt | n_diff | mean_d_v1 | mean_d_v2 | mean_Δ | max_+Δ | max_−Δ |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in per_city_table:
        lines.append(
            f"| {r['city']} | {r['n']} | {r['n_halt']} | {r['n_diff']} "
            f"| {r['mean_d_v1']:.4f} | {r['mean_d_v2']:.4f} | {r['mean_delta']:+.4f} "
            f"| {r['max_pos_delta']:+.4f} | {r['max_neg_delta']:+.4f} |"
        )
    lines += [
        "",
        "## Archetype Summary",
        "",
        "| city | n | n_halt | n_zero_cov | mean_d_v1 | mean_d_v2 |",
        "|---|---|---|---|---|---|",
    ]
    for c, s in archetype_summaries.items():
        lines.append(
            f"| {c} | {s['n']} | {s['n_halt']} | {s['n_zero_cov']} "
            f"| {s['mean_d_v1']:.4f} | {s['mean_d_v2']:.4f} |"
        )

    # archetype tables — show only diff or HALT rows for brevity
    for c, recs in archetype_records.items():
        if not recs:
            continue
        notable = [
            r for r in recs
            if r["action_v2"] == "HALT" or abs(r["delta"]) > 1e-9
        ]
        if not notable:
            continue
        lines += [
            "",
            f"### {c} — notable rows (HALT or v1≠v2)",
            "",
            "| date | metric | cov | σ_90 | floor_v1 | floor_v2 | d_v1 | d_v2 | action_v2 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in notable[:60]:  # cap output
            lines.append(
                f"| {r['date']} | {r['metric']} | {r['cov']:.3f} | {r['sigma_90']:.3f} "
                f"| {r['floor_v1']:.3f} | {r['floor_v2']:.3f} "
                f"| {r['discount_v1']:.4f} | {r['discount_v2']:.4f} | {r['action_v2']} |"
            )
        if len(notable) > 60:
            lines.append(f"| ... ({len(notable) - 60} more) |||||||||")

    lines += [
        "",
        "## Halt-day Inventory",
        "",
        f"v2 halts on **{n_halt}** decisions. Breakdown by city:",
        "",
    ]
    for c, halts in sorted(halt_dates_per_city.items()):
        lines.append(f"- **{c}**: {len(halts)} halts")
        if c in {"Lagos"} and halts:
            for d, m, cov in halts[:30]:
                lines.append(f"  - {d} {m}: cov={cov:.3f}")

    lines += [
        "",
        "## What this answers",
        "",
        "1. **v2 protection delta** — see Kelly notional + halt count.",
        "   v2 strictly larger when it halts a day v1 would have only discounted.",
        "2. **Healthy cities** — per-city mean_d_v1 / mean_d_v2 should both be near 0",
        "   for cities at floor=1.0 with no zero-cov days.",
        "3. **Archetype handling** — see Lagos / Denver / Jakarta / Tokyo sections.",
        "4. **Kelly direction** — kelly_delta_pct shows aggregate trade-volume change.",
        "",
    ]

    OUT_MD.write_text("\n".join(lines))
    print(f"[DDD-replay] wrote {OUT_JSON}")
    print(f"[DDD-replay] wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
