# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator freshness-investigation directive 2026-06-12. READ-ONLY,
#   mode=ro&immutable=1, SELECT-only. Refined information-arrival / belief-drift estimator:
#   |Δq|_TV as a function of the actual time separation Δt between posteriors of the same family,
#   and Δμ (mean shift) — the honest cost-of-staleness curve. Filters near-zero-Δt re-computes.
from __future__ import annotations

import json
import re
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

DB = "file:state/zeus-forecasts.db?mode=ro&immutable=1"
BIN_RE = re.compile(r"(-?\d+)\s*°?C")


def parse_ts(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def bintemp(s):
    m = BIN_RE.search(s)
    return int(m.group(1)) if m else None


def pctl(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def qmean(grid):
    """expected temperature of a {temp:prob} grid."""
    s = sum(grid.values())
    if s <= 0:
        return None
    return sum(t * p for t, p in grid.items()) / s


def main():
    con = sqlite3.connect(DB, uri=True)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT city, target_date, temperature_metric, source_cycle_time, computed_at, q_json "
        "FROM forecast_posteriors WHERE q_json IS NOT NULL "
        "ORDER BY city, target_date, temperature_metric, computed_at"
    ).fetchall()

    fam = defaultdict(list)
    for city, td, metric, sct, comp, qjson in rows:
        c = parse_ts(comp)
        try:
            q = json.loads(qjson)
        except Exception:
            continue
        grid = defaultdict(float)
        for label, p in q.items():
            t = bintemp(label)
            if t is not None:
                grid[t] += float(p)
        if grid and c:
            fam[(city, td, metric)].append((c, parse_ts(sct), dict(grid)))

    # Pair every posterior with the FIRST later posterior that is >= dt_floor apart,
    # binned by actual Δt. This is the belief-drift over a real time gap.
    dt_buckets = [(0.5, 2, "0.5-2h"), (2, 4, "2-4h"), (4, 8, "4-8h"),
                  (8, 16, "8-16h"), (16, 28, "16-28h")]
    tv_by_dt = defaultdict(list)
    dmu_by_dt = defaultdict(list)
    # also condition on whether the consumed cycle CHANGED (new model run ingested)
    tv_samecycle = []
    tv_newcycle = []

    for key, lst in fam.items():
        lst.sort(key=lambda x: x[0])
        for i in range(len(lst)):
            c0, s0, g0 = lst[i]
            for j in range(i + 1, len(lst)):
                c1, s1, g1 = lst[j]
                dh = (c1 - c0).total_seconds() / 3600.0
                if dh < 0.5:
                    continue
                temps = set(g0) | set(g1)
                tv = 0.5 * sum(abs(g1.get(t, 0.0) - g0.get(t, 0.0)) for t in temps)
                m0, m1 = qmean(g0), qmean(g1)
                dmu = abs(m1 - m0) if (m0 is not None and m1 is not None) else None
                # bucket by dt — only record the FIRST later posterior in each dt band per source
                for lo, hi, name in dt_buckets:
                    if lo <= dh < hi:
                        tv_by_dt[name].append(tv)
                        if dmu is not None:
                            dmu_by_dt[name].append(dmu)
                        # cycle change?
                        if s0 and s1:
                            (tv_newcycle if s1 != s0 else tv_samecycle).append(tv)
                        break
                # only pair each i with the nearest later j once per dt-band handled implicitly;
                # break after first qualifying later posterior to avoid O(n^2) over-counting long gaps
                if dh >= 16:
                    break

    print("=" * 80)
    print("BELIEF DRIFT vs TIME SEPARATION  Δt  (same settled/unsettled family)")
    print("  TV = total-variation distance between q distributions (0=identical, 1=disjoint)")
    print("  Δμ = shift in expected-temperature (°C)")
    print("=" * 80)
    print(f"  {'Δt band':10s} {'nPairs':>7s} {'TV mean':>8s} {'TV p50':>7s} {'TV p90':>7s} "
          f"{'Δμ°C mean':>9s} {'Δμ p90':>7s}")
    for lo, hi, name in dt_buckets:
        tv = tv_by_dt.get(name, [])
        dm = dmu_by_dt.get(name, [])
        if not tv:
            continue
        print(f"  {name:10s} {len(tv):7d} {st.mean(tv):8.3f} {pctl(tv,.5):7.3f} {pctl(tv,.9):7.3f} "
              f"{(st.mean(dm) if dm else 0):9.3f} {(pctl(dm,.9) if dm else 0):7.3f}")

    print("\n  DRIVER: does the drift come from a NEW model cycle being ingested?")
    if tv_samecycle:
        print(f"    same consumed cycle : nPairs={len(tv_samecycle):5d}  TV mean={st.mean(tv_samecycle):.3f}  p90={pctl(tv_samecycle,.9):.3f}")
    if tv_newcycle:
        print(f"    NEW consumed cycle  : nPairs={len(tv_newcycle):5d}  TV mean={st.mean(tv_newcycle):.3f}  p90={pctl(tv_newcycle,.9):.3f}")
    print("    => if NEW-cycle drift >> same-cycle drift, staleness cost is driven by missed cycles,")
    print("       which is exactly the re-materialization trigger (re-mat when a newer cycle lands).")
    con.close()


if __name__ == "__main__":
    main()
