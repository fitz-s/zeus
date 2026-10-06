"""Amsterdam EHAM: KNMI Data Platform, station 06240 (Schiphol).

Run under the netCDF4 venv:
  /private/tmp/claude-501/-Users-leofitz-zeus/58464645-9a59-4320-acce-dec6b037962b/scratchpad/knmi/venv/bin/python fetch_knmi.py

Catalog findings (probed 2026-10-06):
  * daily-in-situ-meteorological-observations-validated has TX/TN/TXH/TNH, but its day is the UTC day
    ("end of the daily measurement interval", 00:00Z). It is NOT the Amsterdam local day, so it cannot
    be compared with a local-day settlement without bleeding 1-2 h across the boundary. Used only as a
    cross-check: its TX equals the 10-minute file's Tx24 at 00:00Z (3/3 days spot-checked).
  * hourly-in-situ-meteorological-observations-validated carries only the hourly spot T (no TX/TN).
  * 10-minute-in-situ-meteorological-observations files carry, per station, rolling-window extremes of the
    station's own sampling: Tx6/Tx12/Tx24 and Tn6/Tn12/Tn14 (max/min of the last N hours), plus tx/tn per
    10-minute interval and ta (1-min mean at the stamp).

Method: for every local day [a, b) (24 h days only; Amsterdam has no DST transition between 2026-04-03 and
2026-10-05) fetch two files: stamp a+12h and stamp b. Local-day max = max(Tx12@a+12h, Tx12@b); local-day min =
min(Tn12@a+12h, Tn12@b). Those windows tile (a, b] exactly. Coverage = 1 when all four window values are
present, else 0 (day dropped). 2 calls per day, ~372 calls total against the observed X-Ratelimit-Limit 1000
(rolling hour; knmi_client sleeps to the reset when fewer than 25 remain).

This is the station's TRUE sub-10-minute extreme, so it is an upper bound for what the live `ta` (10-min
1-min-mean sample) adapter would show. The live adapter's ta-sampled extreme was NOT measured (needs 144
files/day).
"""
import time
from datetime import datetime, timedelta

from netCDF4 import Dataset

import knmi_client as K
from dae_common import UTC, RAW, LAST_SETTLED, compare, contract, first_settled, local_day_bounds, save_candidate, settled

CITY, TZNAME = "Amsterdam", "Europe/Amsterdam"
DS, VER, STN = "10-minute-in-situ-meteorological-observations", "1.0", "06240"
VARS = ("Tx12", "Tn12", "Tx24", "Tn14")


def read(stamp: datetime):
    fn = "KMDS__OPER_P___10M_OBS_L2_" + stamp.strftime("%Y%m%d%H%M") + ".nc"
    body = K.download(DS, VER, fn)
    with Dataset("x", memory=body) as d:
        ids = [str(s) for s in d.variables["station"][:].tolist()]
        i = ids.index(STN)
        out = {}
        for v in VARS:
            x = d.variables[v][i, 0]
            out[v] = None if getattr(x, "mask", False) is True or str(x) == "--" else float(x)
        return out


def main():
    truth = settled(CITY)
    first = first_settled(CITY)
    days, errs = {}, []
    cur = datetime.fromisoformat(first).date()
    last = datetime.fromisoformat(LAST_SETTLED).date()
    while cur <= last:
        k = cur.isoformat()
        a, b = local_day_bounds(k, TZNAME)
        rec = dict(n=0, expected=2, coverage=0.0, max_raw=None, min_raw=None, max_contract=None, min_contract=None)
        try:
            if b - a != timedelta(hours=24):
                raise ValueError("non-24h local day (DST)")
            noon, end = read(a + timedelta(hours=12)), read(b)
            xs = [noon["Tx12"], end["Tx12"]]
            ns = [noon["Tn12"], end["Tn12"]]
            rec["win"] = dict(noon=noon, end=end)
            if None not in xs and None not in ns:
                rec.update(n=2, coverage=1.0, max_raw=max(xs), min_raw=min(ns),
                           max_contract=contract(max(xs)), min_contract=contract(min(ns)))
                # internal consistency: Tx24 at local midnight must equal max of the two Tx12 windows
                rec["tx24_matches"] = end["Tx24"] is not None and abs(end["Tx24"] - max(xs)) < 1e-6
        except Exception as e:
            errs.append(f"{k}: {type(e).__name__}: {str(e)[:140]}")
        days[k] = rec
        cur += timedelta(days=1)
        time.sleep(0.2)
        if len(days) % 25 == 0:
            print(f"[knmi] {len(days)} days, api calls={K.STATE['calls']}, remaining={K.STATE['remaining']}", flush=True)
    res = {"daily": days}
    for metric in ("high", "low"):
        rows, agg = compare(days, truth, metric)
        res[metric] = dict(agg=agg, rows=rows)
        print(f"knmi_eham {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
    tx24 = [v.get("tx24_matches") for v in days.values() if v["coverage"] == 1.0]
    print("Tx24@midnight == max(Tx12 windows):", sum(bool(x) for x in tx24), "/", len(tx24))
    print("errors:", len(errs), errs[:8])
    save_candidate("knmi_eham", dict(
        provider="KNMI Data Platform 10-minute-in-situ-meteorological-observations v1.0 (rolling Tx12/Tn12 windows)",
        station="06240 Schiphol", city=CITY, tz=TZNAME, coverage_rule="all four 12h-window values present",
        api_calls=K.STATE["calls"], ratelimit_limit=K.STATE["limit"], errors=errs,
        tx24_consistency=[sum(bool(x) for x in tx24), len(tx24)],
        rounding="floor(v+0.5) via round_wmo_half_up_value", first_day=first, last_day=LAST_SETTLED,
        caveat="true station extreme (sub-10-min sampling); live ta adapter sample-extreme not measured",
        source_channel="knmi_station_temperature (no live WORLD prints exist)"), res)


if __name__ == "__main__":
    main()
