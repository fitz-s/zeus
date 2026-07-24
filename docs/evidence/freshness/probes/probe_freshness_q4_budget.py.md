# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator freshness-investigation directive 2026-06-12. READ-ONLY,
#   mode=ro&immutable=1, SELECT-only. Q4: staleness budget inputs — refresh-interval tail
#   (starvation), and how stale the consumed cycle was vs the freshest AVAILABLE cycle at
#   compute time (the missed-cycle gap that the re-materialization trigger must close).
from __future__ import annotations

import re
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

DB = "file:state/zeus-forecasts.db?mode=ro&immutable=1"
LIVE_FROM = "2026-06-08T19:00:00+00:00"


def parse_ts(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def pctl(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main():
    con = sqlite3.connect(DB, uri=True)
    cur = con.cursor()

    # ---- refresh-interval tail per family (starvation detection) ----
    prows = cur.execute(
        "SELECT city, target_date, temperature_metric, computed_at, source_cycle_time "
        "FROM forecast_posteriors WHERE computed_at >= ? ORDER BY 1,2,3,4", (LIVE_FROM,)
    ).fetchall()
    fam = defaultdict(list)
    for city, td, m, comp, sct in prows:
        fam[(city, td, m)].append((parse_ts(comp), parse_ts(sct)))

    max_gap = []   # per-family longest gap between consecutive posteriors while still pre-target
    for key, lst in fam.items():
        comps = sorted(c for c, _ in lst if c)
        gaps = [(comps[i] - comps[i - 1]).total_seconds() / 3600 for i in range(1, len(comps))]
        if gaps:
            max_gap.append((max(gaps), key, len(comps)))
    max_gap.sort(reverse=True)
    print("=" * 80)
    print("REFRESH STARVATION — worst per-family max gap between consecutive posteriors (live)")
    print("=" * 80)
    print("  top 12 longest single gaps:")
    for g, key, n in max_gap[:12]:
        print(f"    {g:6.1f}h  {key[0]:12s} {key[1]} {key[2]:5s}  (#posteriors={n})")
    gaps_all = [g for g, _, _ in max_gap]
    print(f"\n  per-family MAX gap distribution: p50={pctl(gaps_all,.5):.1f}h "
          f"p90={pctl(gaps_all,.9):.1f}h p99={pctl(gaps_all,.99):.1f}h max={max(gaps_all):.1f}h")

    # ---- consumed-cycle staleness: at each posterior's compute time, what was the freshest
    #      model cycle ALREADY INGESTED (raw_model_forecasts.source_cycle_time <= computed_at)
    #      vs the cycle the posterior actually consumed? The gap = missed-cycle staleness. ----
    # Build sorted list of (captured_at, source_cycle_time) for the anchor model ecmwf_ifs single_runs.
    ing = cur.execute(
        "SELECT captured_at, source_cycle_time FROM raw_model_forecasts "
        "WHERE provider='open-meteo' AND model='ecmwf_ifs' AND endpoint='single_runs' "
        "AND captured_at >= ? ORDER BY captured_at", (LIVE_FROM,)
    ).fetchall()
    ingest_events = []
    for cap, sct in ing:
        c, s = parse_ts(cap), parse_ts(sct)
        if c and s:
            ingest_events.append((c, s))
    ingest_events.sort()

    def freshest_cycle_ingested_by(t):
        best = None
        for cap, sct in ingest_events:
            if cap <= t and (best is None or sct > best):
                best = sct
        return best

    missed = []
    for key, lst in fam.items():
        for comp, consumed in lst:
            if not comp or not consumed:
                continue
            avail = freshest_cycle_ingested_by(comp)
            if avail is None:
                continue
            gap_h = (avail - consumed).total_seconds() / 3600.0
            missed.append(gap_h)
    print("\n" + "=" * 80)
    print("MISSED-CYCLE GAP at compute time  (freshest ecmwf_ifs cycle already ingested")
    print("  MINUS the cycle the posterior actually consumed), hours of cycle-time")
    print("=" * 80)
    if missed:
        pos = [m for m in missed if m > 0.5]
        print(f"  n={len(missed)}  share with a fresher cycle already available (>0.5h): "
              f"{len(pos)/len(missed)*100:.1f}%")
        print(f"  gap p50={pctl(missed,.5):.2f}h p90={pctl(missed,.9):.2f}h max={max(missed):.2f}h")
        print("  (gap>0 => at compute time a newer anchor cycle was ALREADY in raw_model_forecasts")
        print("   than the one the posterior used — i.e. born partially stale)")
    con.close()


if __name__ == "__main__":
    main()
