"""AWC METAR daily-extreme reference for every audited city, from WORLD.observation_prints (source_channel
'aviationweather_metar', mode=ro) since 2026-07-16 (start of AWC history in WORLD).

Purpose: the ceiling. METAR integer-degree extremes are the same raw message the settlement resolver (NOAA WRH
timeseries, half-hourly METAR-derived) reads from 2026-08-24, so a candidate cannot be expected to agree with
settlement more often than METAR does. Earlier settled days (before 2026-08-24) were written from WU/ogimet history
(see SUMMARY.md truth-era note) and are reported separately.

Observation instant: METAR DDHHMMZ group resolved against fetched_at (same logic as the reference reveal.py).
Duplicates by instant collapse (last wins). Coverage: >= 80% of the routine slots (the station's top-k most common
report minutes, k = 3600/cadence) in the local day; SPECIs add extremes but not coverage.
Sao Paulo (item 9): this IS the METAR, so it matches the resolver trivially; the row here only shows the same
test for completeness. Moscow: the Meteo-Via channel has 3 live prints, so the AWC METAR of the same station is the
identical-content proxy for what that channel would carry.
"""
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dae_common import (COVERAGE_MIN, LAST_SETTLED, UTC, WORLD_URI, city_tz, compare, contract, local_day_bounds, save_candidate,
                        settled)

STATIONS = [("Helsinki", "EFHK"), ("Munich", "EDDM"), ("Warsaw", "EPWA"), ("Amsterdam", "EHAM"), ("London", "EGLC"),
            ("Tokyo", "RJTT"), ("Toronto", "CYYZ"), ("Ankara", "LTAC"), ("Istanbul", "LTFM"), ("Lucknow", "VILK"),
            ("Moscow", "UUWW"), ("Madrid", "LEMD"), ("Singapore", "WSSS"), ("Sao Paulo", "SBGR")]
SINCE = "2026-07-16"
WRH_ERA_START = "2026-08-24"  # first settlement day written from noaa_wrh_timeseries_v1 (Istanbul/Moscow/Toronto/SBGR from 08-23)

W = sqlite3.connect(WORLD_URI, uri=True, timeout=10)
W.execute("pragma query_only=1")
TZ = city_tz()


def t(s):
    d = datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def instant(raw, fetched):
    m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", raw or "")
    if not m:
        return None
    dd, hh, mm = map(int, m.groups())
    for k in (0, -1):
        mo = (fetched.replace(day=1) + timedelta(days=32 * k)).replace(day=1)
        try:
            c = mo.replace(day=dd, hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            continue
        if timedelta(0) <= fetched - c <= timedelta(days=2):
            return c
    return None


def main():
    for city, stn in STATIONS:
        tz = ZoneInfo(TZ[city])
        seen = {}
        for raw, val, fe in W.execute(
                "select raw_report, value_native, fetched_at_utc from observation_prints "
                "where city=? and station_id=? and source_channel='aviationweather_metar' and publish_ts_utc>=?", (city, stn, SINCE)):
            o = instant(raw, t(fe))
            if o:
                seen[o] = float(val)
        mins = Counter(o.minute for o in seen)
        # cadence: hourly if one dominant minute, else half-hourly (EFHK/EDDM/EHAM/EGLC/EPWA/RJTT/LEMD/WSSS/... report :xx and :xx+30)
        top = [m for m, _ in mins.most_common(2)]
        share2 = sum(c for m, c in mins.most_common(2)) / max(1, sum(mins.values()))
        k = 2 if (len(top) == 2 and mins[top[1]] > 0.4 * mins[top[0]]) else 1
        routine = {m for m, _ in mins.most_common(k)}
        by_day = defaultdict(list)
        slots = defaultdict(set)
        for o, v in seen.items():
            d = o.astimezone(tz).date().isoformat()
            by_day[d].append(v)
            if o.minute in routine:
                slots[d].add(o)
        days = {}
        for d, vals in by_day.items():
            a, b = local_day_bounds(d, TZ[city])
            expected = int((b - a).total_seconds() // (3600 // k))
            cov = len(slots[d]) / expected
            days[d] = dict(n=len(slots[d]), n_all=len(vals), expected=expected, coverage=round(cov, 4),
                           max_raw=max(vals), min_raw=min(vals), max_contract=contract(max(vals)), min_contract=contract(min(vals)))
        truth = settled(city)
        res = {"daily": days}
        first_full = SINCE
        # a partial first day (history starts 07-16 00:00Z) is dropped by coverage, not hidden
        for metric in ("high", "low"):
            sub = {m: {d: v for d, v in truth[m].items() if SINCE <= d <= LAST_SETTLED} for m in truth}
            rows, agg = compare(days, sub, metric)
            # split by truth era
            for era, pred in (("wrh_era", lambda d: d >= WRH_ERA_START), ("pre_wrh_era", lambda d: d < WRH_ERA_START)):
                used = [r for r in rows if r["used"] and pred(r["date"])]
                agg[era] = dict(n=len(used), eq=sum(r["diff"] == 0 for r in used),
                                dangerous=sum(r["dangerous"] for r in used))
            res[metric] = dict(agg=agg, rows=rows)
            print(f"{city:9s} AWC {stn} {metric:4s} n={agg['n_days']:3d} drop={agg['dropped_coverage']:2d} eq={agg['eq']:3d} "
                  f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']} "
                  f"wrh_era={agg['wrh_era']} pre={agg['pre_wrh_era']}")
        save_candidate(f"awc_metar_{stn}", dict(
            city=city, station=stn, channel="aviationweather_metar", tz=TZ[city], since=SINCE, last_day=LAST_SETTLED,
            routine_minutes=sorted(routine), slots_per_hour=k, coverage_min=COVERAGE_MIN, rounding="floor(v+0.5)",
            note="METAR integer deg C; instant from DDHHMMZ"), res)


if __name__ == "__main__":
    main()
