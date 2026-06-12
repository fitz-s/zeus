# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator freshness-investigation directive 2026-06-12
#   ("新鲜度给我调查清楚...如果我们获取不到最新的准确forecast对于评估来说是一个衰减").
#   READ-ONLY probe. Opens zeus-forecasts.db via file:...?mode=ro&immutable=1 URI,
#   SELECT-only, writes nothing to any DB. Q2: observed ingest + materialization latency.
#   NOTE: if ever run under pytest, register this path in
#   src/state/db_writer_lock.SQLITE_CONNECT_ALLOWLIST (read_only_ro_uri).
"""Q2 — observed ingest and materialization latency.

Part A: per (model, endpoint) distribution of (captured_at - source_cycle_time)
        and (source_available_at - source_cycle_time) over the live window.
Part B: per-family forecast_posteriors computed_at refresh-interval distribution
        (median / p10 / p90) and lag from last-input-arrival to posterior computed_at.
"""
from __future__ import annotations

import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

DB = "file:state/zeus-forecasts.db?mode=ro&immutable=1"
# live-ingest window starts when open-meteo provider rows begin (B0 seed is 06-08T00:00)
LIVE_FROM = "2026-06-08T19:00:00+00:00"


def parse(ts):
    if ts is None:
        return None
    ts = ts.strip()
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        try:
            d = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def hours(a, b):
    if a is None or b is None:
        return None
    return (a - b).total_seconds() / 3600.0


def pctl(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def fmt(x):
    return "  n/a" if x is None else f"{x:6.2f}"


def main():
    con = sqlite3.connect(DB, uri=True)
    cur = con.cursor()

    print("=" * 90)
    print("Q2A — INGEST LATENCY: (captured_at - source_cycle_time) and dissemination "
          "(source_available_at - source_cycle_time), live window only, provider=open-meteo")
    print("=" * 90)
    rows = cur.execute(
        """SELECT model, endpoint, source_cycle_time, source_available_at, captured_at
           FROM raw_model_forecasts
           WHERE provider='open-meteo' AND captured_at >= ?""",
        (LIVE_FROM,),
    ).fetchall()

    cap = defaultdict(list)   # (model,endpoint) -> capture lag hrs
    dis = defaultdict(list)   # (model,endpoint) -> dissemination lag hrs
    cyc = defaultdict(set)    # (model,endpoint) -> set of source_cycle_time seen
    for model, endpoint, sct, savail, capd in rows:
        c = parse(sct)
        h = hours(parse(capd), c)
        if h is not None and -2 < h < 240:
            cap[(model, endpoint)].append(h)
        d = hours(parse(savail), c)
        if d is not None and -2 < d < 240:
            dis[(model, endpoint)].append(d)
        cyc[(model, endpoint)].add(sct)

    print(f"{'model':32s} {'endpoint':14s} {'n':>6s} "
          f"{'capLag p10':>10s} {'p50':>7s} {'p90':>7s}  "
          f"{'disLag p50':>10s}  {'#cycles':>7s}")
    for key in sorted(cap, key=lambda k: -len(cap[k])):
        m, e = key
        cl = cap[key]
        dl = dis.get(key, [])
        print(f"{m:32s} {e:14s} {len(cl):6d} "
              f"{fmt(pctl(cl,.10))} {fmt(pctl(cl,.50))} {fmt(pctl(cl,.90))}  "
              f"{fmt(pctl(dl,.50))}  {len(cyc[key]):7d}")

    print()
    print("=== distinct source_cycle_time hours-of-day seen (live, all models) ===")
    cyc_hours = defaultdict(int)
    for (m, e), s in cyc.items():
        for sct in s:
            d = parse(sct)
            if d:
                cyc_hours[d.hour] += 1
    for h in sorted(cyc_hours):
        print(f"   cycle hour {h:02d}Z : {cyc_hours[h]} (model,endpoint) groups")

    print()
    print("=" * 90)
    print("Q2B — MATERIALIZATION CADENCE: forecast_posteriors per-family computed_at intervals")
    print("=" * 90)
    prows = cur.execute(
        """SELECT family_id, city, target_date, temperature_metric,
                  source_cycle_time, source_available_at, computed_at,
                  dependency_source_run_ids_json
           FROM forecast_posteriors
           WHERE computed_at >= ?
           ORDER BY family_id, computed_at""",
        (LIVE_FROM,),
    ).fetchall()

    # group by family key
    fam = defaultdict(list)
    for r in prows:
        fid, city, td, metric, sct, savail, comp, deps = r
        key = (city, td, metric)
        fam[key].append((parse(comp), parse(sct), parse(savail)))

    # refresh intervals per family with >=2 posteriors
    intervals = []
    fam_refresh_counts = []
    # lag from newest input cycle to computed_at
    cycle_to_comp = []
    for key, lst in fam.items():
        lst.sort()
        comps = [c for c, _, _ in lst if c]
        fam_refresh_counts.append(len(comps))
        for i in range(1, len(comps)):
            dh = hours(comps[i], comps[i - 1])
            if dh and dh > 0:
                intervals.append(dh)
        for comp, sct, savail in lst:
            h = hours(comp, sct)
            if h is not None and -2 < h < 240:
                cycle_to_comp.append(h)

    print(f"families (city,target,metric) seen live : {len(fam)}")
    print(f"families with >=2 posteriors            : {sum(1 for c in fam_refresh_counts if c>=2)}")
    print(f"posteriors per family  : p10={pctl(fam_refresh_counts,.10):.1f} "
          f"p50={pctl(fam_refresh_counts,.50):.1f} p90={pctl(fam_refresh_counts,.90):.1f} "
          f"max={max(fam_refresh_counts)}")
    print()
    print("REFRESH INTERVAL between consecutive posteriors of SAME family (hours):")
    print(f"   n={len(intervals)}  p10={fmt(pctl(intervals,.10))} p25={fmt(pctl(intervals,.25))} "
          f"p50={fmt(pctl(intervals,.50))} p75={fmt(pctl(intervals,.75))} "
          f"p90={fmt(pctl(intervals,.90))} max={fmt(max(intervals) if intervals else None)}")
    print()
    print("LAG newest-consumed-cycle -> computed_at (source_cycle_time to computed_at, hours):")
    print(f"   n={len(cycle_to_comp)}  p10={fmt(pctl(cycle_to_comp,.10))} "
          f"p50={fmt(pctl(cycle_to_comp,.50))} p90={fmt(pctl(cycle_to_comp,.90))}")

    # per-metric refresh interval split (high vs low)
    print()
    print("REFRESH INTERVAL split by metric:")
    for metric in ("high", "low"):
        iv = []
        for (city, td, m), lst in fam.items():
            if m != metric:
                continue
            comps = sorted(c for c, _, _ in lst if c)
            for i in range(1, len(comps)):
                dh = hours(comps[i], comps[i - 1])
                if dh and dh > 0:
                    iv.append(dh)
        if iv:
            print(f"   {metric:5s} n={len(iv):4d}  p50={fmt(pctl(iv,.50))} "
                  f"p90={fmt(pctl(iv,.90))} mean={fmt(st.mean(iv))}")

    con.close()


if __name__ == "__main__":
    main()
