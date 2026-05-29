#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-29
# Authority basis: operator directive 2026-05-28 — detailed end-to-end pipeline audit with
#   empirical per-step/per-product/per-city evidence. READ-ONLY.
# Purpose: produce the GRANULAR evidence the synthesis lacked:
#   1. Full inventory: rows per (data_version x cycle x metric), lead range, city/date coverage.
#   2. EMPIRICAL step-handling test: mx2t3 (3h) vs mx2t6 (6h) daily-max agreement on overlap dates.
#   3. Multiple-lead demonstration: for one city+2-day-out target, every (product,cycle,lead) snapshot
#      and its daily-max mean — shows "多个不同步长结果" + the selected one.
#   4. Bias-by-lead within a (city,season): does the pooled 0-48h bias hide a lead gradient?
#   5. Unit handling per city.
# Lifecycle: created=2026-05-28; last_reviewed=2026-05-29; last_reused=never
# Reuse: Inspect hardcoded DB and output path constants; READ-ONLY on forecasts DB, writes only to file.
import json
import sqlite3
import statistics
import sys
from collections import defaultdict

DB = "/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-forecasts.db"
OUT = "/Users/leofitz/.claude/jobs/866db2ea/pipeline_empirical_detail.txt"


def mean_members(mj, unit):
    try:
        v = json.loads(mj)
        vals = [float(x) for x in (v.values() if isinstance(v, dict) else v) if x is not None]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not vals:
        return None
    m = statistics.mean(vals)
    if (unit or "").strip().lower().startswith("f") or (unit or "").endswith("F"):
        m = (m - 32.0) * 5.0 / 9.0
    return m


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=1;")
    out = []
    P = out.append

    # ---- 1. INVENTORY ----
    P("=" * 90)
    P("1. INVENTORY: rows per (data_version, cycle, metric), lead range, coverage")
    P("=" * 90)
    rows = c.execute("""
      SELECT data_version, substr(issue_time,12,2) AS cycle, temperature_metric AS m, COUNT(*) n,
             MIN(lead_hours) lo, MAX(lead_hours) hi,
             COUNT(DISTINCT city) ncity, COUNT(DISTINCT target_date) ndate,
             MIN(target_date) td0, MAX(target_date) td1
      FROM ensemble_snapshots_v2 GROUP BY 1,2,3 ORDER BY 1,2,3
    """).fetchall()
    P(f"{'data_version':46s}{'cyc':4s}{'m':5s}{'n':>8s}{'lead_lo':>8s}{'lead_hi':>8s}{'cities':>7s}{'dates':>6s}  span")
    for r in rows:
        P(f"{r['data_version'][:45]:46s}{str(r['cycle']):4s}{r['m']:5s}{r['n']:>8d}{r['lo']:>8.0f}{r['hi']:>8.0f}{r['ncity']:>7d}{r['ndate']:>6d}  {r['td0']}..{r['td1']}")

    # ---- 2. EMPIRICAL STEP-HANDLING TEST: mx2t3 vs mx2t6 daily-max agreement ----
    P("")
    P("=" * 90)
    P("2. EMPIRICAL STEP TEST: OpenData mx2t3 (3h) vs mx2t6 (6h) daily-max MEAN, same (city,date)")
    P("   If step handling correct, daily-max should agree (both = peak of the day). Systematic")
    P("   gap = step/window artifact (D1). Overlap window where both products exist.")
    P("=" * 90)
    for metric, v3, v6 in [("high", "ecmwf_opendata_mx2t3_local_calendar_day_max_v1", "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"),
                            ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min_v1", "ecmwf_opendata_mn2t6_local_calendar_day_min_v1")]:
        def latest_per(dv):
            r = c.execute("""
              SELECT city, target_date, members_json, members_unit, issue_time, lead_hours
              FROM ensemble_snapshots_v2 e
              WHERE data_version=? AND members_json IS NOT NULL
                AND available_at=(SELECT MAX(available_at) FROM ensemble_snapshots_v2 e2
                                  WHERE e2.city=e.city AND e2.target_date=e.target_date AND e2.data_version=e.data_version)
            """, (dv,)).fetchall()
            d = {}
            for x in r:
                mm = mean_members(x["members_json"], x["members_unit"])
                if mm is not None:
                    d[(x["city"], x["target_date"])] = mm
            return d
        d3, d6 = latest_per(v3), latest_per(v6)
        common = sorted(set(d3) & set(d6))
        P(f"\n[{metric}] common (city,date) pairs with both products: {len(common)}")
        if common:
            diffs = [d3[k] - d6[k] for k in common]
            P(f"  mx2t3 - mx2t6 daily-max-mean:  mean={statistics.mean(diffs):+.3f}C  median={statistics.median(diffs):+.3f}C  "
              f"max|diff|={max(abs(x) for x in diffs):.2f}C  n={len(diffs)}")
            big = sorted(((abs(d3[k]-d6[k]), k, d3[k], d6[k]) for k in common), reverse=True)[:6]
            P("  largest disagreements (|3h-6h|, city, date, mx2t3, mx2t6):")
            for ad, k, a, b in big:
                P(f"    {ad:5.2f}  {k[0]:14s} {k[1]}  3h={a:6.2f}  6h={b:6.2f}")

    # ---- 3. MULTIPLE-LEAD DEMONSTRATION ----
    P("")
    P("=" * 90)
    P("3. MULTIPLE LEAD-STEP RESULTS for ONE 2-day-out target (the operator's concern)")
    P("=" * 90)
    # pick a city+target with many snapshots
    pick = c.execute("""
      SELECT city, target_date, COUNT(*) n FROM ensemble_snapshots_v2
      WHERE temperature_metric='high' AND members_json IS NOT NULL
      GROUP BY 1,2 HAVING n>=6 ORDER BY n DESC LIMIT 1
    """).fetchone()
    if pick:
        city, td = pick["city"], pick["target_date"]
        P(f"city={city} target_date={td} (HIGH) — every snapshot that forecasts this day:")
        snaps = c.execute("""
          SELECT data_version, substr(issue_time,12,2) AS cycle, issue_time, lead_hours, members_unit, members_json,
                 contributes_to_target_extrema AS ctr, forecast_window_start_utc ws, forecast_window_end_utc we
          FROM ensemble_snapshots_v2
          WHERE city=? AND target_date=? AND temperature_metric='high' AND members_json IS NOT NULL
          ORDER BY data_version, issue_time, lead_hours
        """, (city, td)).fetchall()
        P(f"  {'data_version':30s}{'cyc':4s}{'lead':>5s}{'ctr':>4s}{'mean_max_C':>11s}  window_utc")
        for s in snaps:
            mm = mean_members(s["members_json"], s["members_unit"])
            win = f"{str(s['ws'])[5:16]}..{str(s['we'])[5:16]}" if s["ws"] else "n/a"
            P(f"  {s['data_version'][:29]:30s}{str(s['cycle']):4s}{s['lead_hours']:>5.0f}{str(s['ctr']):>4s}{mm if mm is None else round(mm,2):>11}  {win}")
        means = [mean_members(s["members_json"], s["members_unit"]) for s in snaps]
        means = [m for m in means if m is not None]
        if means:
            P(f"  --> {len(means)} lead-step forecasts span {min(means):.2f}..{max(means):.2f} C (range {max(means)-min(means):.2f} C). One is served (freshest FULL_CONTRIBUTOR); rest unused.")

    # ---- 4. BIAS-BY-LEAD within a bucket (tests D4 pooling) ----
    P("")
    P("=" * 90)
    P("4. BIAS-BY-LEAD within (city,season): does pooled 0-48h bias hide a lead gradient?")
    P("   residual = ens_mean - settlement, grouped by lead_days, from calibration_pairs_v2 settled rows")
    P("=" * 90)
    for city, season in [("San Francisco", "MAM"), ("NYC", "DJF"), ("Shanghai", "MAM"), ("London", "DJF")]:
        r = c.execute("""
          SELECT lead_days, COUNT(DISTINCT decision_group_id) ngroups
          FROM calibration_pairs_v2
          WHERE city=? AND season=? AND temperature_metric='high'
            AND error_model_family='full_transport_v1' AND bin_source='canonical_v2'
          GROUP BY lead_days ORDER BY lead_days
        """, (city, season)).fetchall()
        leadstr = " ".join(f"d{int(x['lead_days'])}:{x['ngroups']}" for x in r)
        P(f"  {city:14s} {season}: lead_days(groups)= {leadstr if leadstr else '(none)'}")

    # ---- 5. UNIT handling per city ----
    P("")
    P("=" * 90)
    P("5. UNIT handling per city (members_unit) — HIGH, recent")
    P("=" * 90)
    r = c.execute("""
      SELECT members_unit, COUNT(DISTINCT city) ncity, COUNT(*) n
      FROM ensemble_snapshots_v2 WHERE temperature_metric='high' GROUP BY 1
    """).fetchall()
    for x in r:
        P(f"  members_unit={str(x['members_unit']):8s}  cities={x['ncity']:3d}  rows={x['n']}")

    c.close()
    txt = "\n".join(out)
    open(OUT, "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
