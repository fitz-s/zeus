"""Daily-extreme agreement + reveal lead from WORLD.observation_prints (read-only, mode=ro).

Candidates: every live registry channel with WORLD history (FMI, DWD, IMGW, JMA, ECCC SWOB, IMD OLBS, MGM x2, Meta-Via).
Baseline  : aviationweather_metar (AWC) for the same city/station, scored with the identical daily-extreme test.

Daily-extreme test (per candidate and metric):
  local day from publish_ts_utc (the observation instant) in config/cities.json timezone;
  keep days with >= 80% of the expected readings for the channel's native cadence;
  compare floor(max+0.5) / floor(min+0.5) with the VERIFIED settlement.

Reveal lead (minutes, from fetched_at_utc), per day where the candidate's own extreme == settled:
  t_cand = first fetch at which the candidate showed a reading whose contract value reached the settled extreme
           (high: value >= M ; low: value <= M)
  t_awc  = the same on AWC METAR, observation instant parsed from the METAR DDHHMMZ group (inside the local day)
  lead   = t_awc - t_cand           positive = candidate revealed the final extreme earlier than AWC.
  Sign convention follows the reference reveal.py: lead = t_awc - t_cand, so POSITIVE means the candidate showed the
  final extreme EARLIER than AWC.
  Both the all-fetches variant (as specified) and a no-backfill variant (drop prints fetched later than 3 x that
  channel's own p90 fetch lag, min 15 min, i.e. daemon-restart catch-up rather than live polling) are reported.
"""
import json
import re
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dae_common import (COVERAGE_MIN, LAST_SETTLED, UTC, WORLD_URI, city_tz, compare, contract, day_extremes, save_candidate,
                        settled)

CANDS = [  # city, station, channel, expected cadence seconds
    ("Helsinki", "EFHK", "fmi_airport_temperature", 600),
    ("Munich", "EDDM", "dwd_cdc_temperature", 600),
    ("Warsaw", "EPWA", "imgw_synop_temperature", 3600),
    ("Tokyo", "RJTT", "jma_amedas_temperature", 600),
    ("Toronto", "CYYZ", "eccc_swob_temperature", 3600),
    ("Lucknow", "VILK", "imd_olbs_metar_temperature", 1800),
    ("Ankara", "LTAC", "mgm_metar_temperature", 1800),
    ("Istanbul", "LTFM", "mgm_metar_temperature", 1800),
    ("Moscow", "UUWW", "metaviatelecom_metar_temperature", 1800),
]
AWC_CADENCE = {"Toronto": 3600}  # CYYZ reports hourly; everything else here reports half-hourly
SINCE = "2026-09-20"

W = sqlite3.connect(WORLD_URI, uri=True, timeout=10)
W.execute("pragma query_only=1")
TZ = city_tz()


def t(s):
    d = datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def metar_instant(raw, fetched):
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


def cand_rows(city, stn, ch):
    out = []  # (obs_utc, fetched_utc, raw_value)
    for pub, val, fe in W.execute(
            "select publish_ts_utc, value_native, fetched_at_utc from observation_prints "
            "where city=? and station_id=? and source_channel=? and publish_ts_utc>=?", (city, stn, ch, SINCE)):
        out.append((t(pub), t(fe), float(val)))
    return out


def awc_rows(city, stn):
    out = []
    for raw, val, fe in W.execute(
            "select raw_report, value_native, fetched_at_utc from observation_prints "
            "where city=? and station_id=? and source_channel='aviationweather_metar' and publish_ts_utc>=?", (city, stn, SINCE)):
        f = t(fe)
        o = metar_instant(raw, f)
        if o:
            out.append((o, f, float(val)))
    return out


def backfill_cut(rows):
    """Per-channel no-backfill cutoff: 3 x the channel's own p90 fetch lag, at least 15 min (DWD's native lag is ~40 min)."""
    lags = sorted((f - o).total_seconds() / 60 for o, f, _ in rows)
    return timedelta(minutes=max(15.0, 3 * lags[int(0.9 * (len(lags) - 1))])) if lags else timedelta(minutes=15)


def first_reach(rows, tz, day, metric, M, no_backfill):
    best = None
    cut = backfill_cut(rows) if no_backfill else None
    for obs, fet, v in rows:
        if obs.astimezone(tz).date().isoformat() != day:
            continue
        if no_backfill and (fet - obs) > cut:
            continue
        c = contract(v)
        if (c >= M) if metric == "high" else (c <= M):
            if best is None or fet < best:
                best = fet
    return best


def pct(a, p):
    a = sorted(a)
    return a[int(p * (len(a) - 1))]


def lead_stats(xs):
    if not xs:
        return dict(n=0)
    return dict(n=len(xs), p10=round(pct(xs, .1), 1), p50=round(st.median(xs), 1), p90=round(pct(xs, .9), 1),
                earlier=sum(x > 0 for x in xs), tie=sum(x == 0 for x in xs), later=sum(x < 0 for x in xs))


def run(city, stn, ch, cadence, truth, awc_all):
    tzname = TZ[city]
    tz = ZoneInfo(tzname)
    rows = cand_rows(city, stn, ch)
    name = f"world_{ch}_{stn}"
    if not rows:
        save_candidate(name, dict(city=city, station=stn, channel=ch, note="no WORLD rows"), {})
        return None
    first_day = min(r[0].astimezone(tz).date().isoformat() for r in rows)
    days = day_extremes([(o, v) for o, _, v in rows], tzname, cadence, first_day)
    days = {d: e for d, e in days.items() if e["n"] > 0}
    res = {"daily": days}
    leads = {"high": {"all": [], "live": []}, "low": {"all": [], "live": []}}
    for metric in ("high", "low"):
        sub = {m: {d: v for d, v in truth[m].items() if first_day <= d <= LAST_SETTLED} for m in truth}
        # days before the first live print are "not observed", not coverage drops
        cmp_rows, agg = compare(days, sub, metric)
        agg["first_live_day"] = first_day
        detail = []
        for r in cmp_rows:
            if not r["used"] or r["diff"] != 0:
                continue
            M = r["settled"]
            for variant, nb in (("all", False), ("live", True)):
                tc = first_reach(rows, tz, r["date"], metric, M, nb)
                ta = first_reach(awc_all, tz, r["date"], metric, M, nb)
                if tc and ta:
                    lead = (ta - tc).total_seconds() / 60
                    leads[metric][variant].append(lead)
                    if variant == "all":
                        r["t_cand"], r["t_awc"], r["lead_min"] = tc.isoformat(), ta.isoformat(), round(lead, 1)
        agg["reveal_lead_min_all"] = lead_stats(leads[metric]["all"])
        agg["reveal_lead_min_nobackfill"] = lead_stats(leads[metric]["live"])
        res[metric] = dict(agg=agg, rows=cmp_rows)
        print(f"{city:9s} {ch:34s} {metric:4s} n={agg['n_days']:3d} drop={agg['dropped_coverage']:2d} eq={agg['eq']:3d} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} lead_all={agg['reveal_lead_min_all']} "
              f"lead_nobackfill={agg['reveal_lead_min_nobackfill']}")
    save_candidate(name, dict(city=city, station=stn, channel=ch, tz=tzname, cadence_s=cadence, coverage_min=COVERAGE_MIN,
                              first_live_day=first_day, last_day=LAST_SETTLED, rounding="floor(v+0.5)",
                              source="WORLD.observation_prints (mode=ro)",
                              n_prints=len(rows),
                              backfill_cut_min_candidate=round(backfill_cut([(o, f, v) for o, f, v in rows]).total_seconds() / 60, 1),
                              backfill_cut_min_awc=round(backfill_cut(awc_all).total_seconds() / 60, 1)), res)
    return res


def run_baseline(city, stn, truth, awc_all, first_day):
    tzname = TZ[city]
    cadence = AWC_CADENCE.get(city, 1800)
    seen = {}
    for o, _, v in awc_all:  # dedupe by observation instant (last wins)
        seen[o] = v
    days = day_extremes(list(seen.items()), tzname, cadence, first_day)
    days = {d: e for d, e in days.items() if e["n"] > 0}
    res = {"daily": days}
    for metric in ("high", "low"):
        sub = {m: {d: v for d, v in truth[m].items() if first_day <= d <= LAST_SETTLED} for m in truth}
        rows_, agg = compare(days, sub, metric)
        res[metric] = dict(agg=agg, rows=rows_)
        print(f"{city:9s} {'aviationweather_metar (baseline)':34s} {metric:4s} n={agg['n_days']:3d} drop={agg['dropped_coverage']:2d} "
              f"eq={agg['eq']:3d} {agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']}")
    save_candidate(f"world_awc_baseline_{stn}", dict(city=city, station=stn, channel="aviationweather_metar", tz=tzname,
                                                      cadence_s=cadence, first_day=first_day, last_day=LAST_SETTLED,
                                                      coverage_min=COVERAGE_MIN, rounding="floor(v+0.5)",
                                                      note="METAR integer deg C, instant from DDHHMMZ group"), res)


def main():
    for city, stn, ch, cad in CANDS:
        truth = settled(city)
        awc = awc_rows(city, stn)
        res = run(city, stn, ch, cad, truth, awc)
        if res and res.get("daily"):
            run_baseline(city, stn, truth, awc, min(res["daily"]))


if __name__ == "__main__":
    main()
