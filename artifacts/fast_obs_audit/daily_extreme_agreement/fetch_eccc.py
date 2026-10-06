"""Toronto CYYZ: ECCC climate.weather.gc.ca bulk data, station 51459 (TORONTO INTL A, climate ID 6158731). No key.

Two products:
  daily  (timeframe=2, one request per year): Max/Min Temp per LST day. ECCC days run in Local STANDARD Time
         (UTC-5, no DST). Toronto's settlement day is the DST-observing calendar day, so between
         2026-03-08 and 2026-11-01 the ECCC day is shifted by +1 h relative to the local calendar day
         (ECCC day = 01:00..00:59 local clock). We compare it as published and flag the offset.
  hourly (timeframe=1, one request per month): hourly spot temperature in LST -> converted to UTC ->
         local calendar day. Hourly spot values: daily max is a lower bound, min an upper bound.
Requests: 2 daily + 11 hourly; 3 s sleep.
"""
import csv
import io
import time
from datetime import datetime, timedelta, timezone

import httpx

from dae_common import UA, UTC, compute_and_save, compare, contract, first_settled, settled, save_candidate, write_raw, LAST_SETTLED, COVERAGE_MIN, local_day_bounds

CITY, TZNAME, STN = "Toronto", "America/Toronto", 51459
URL = "https://climate.weather.gc.ca/climate_data/bulk_data_e.html"
LST = timezone(timedelta(hours=-5))


def get(c, **p):
    last = None
    for attempt in range(3):
        try:
            r = c.get(URL, params=dict(format="csv", stationID=STN, submit="Download Data", **p), timeout=120)
            r.raise_for_status()
            return r.content.decode("utf-8-sig")
        except Exception as e:
            last = e
            time.sleep(10)
    raise last


def main():
    errs = []
    first = first_settled(CITY)
    y0 = int(first[:4])
    daily, hourly = {}, {}
    with httpx.Client(headers=UA) as c:
        for y in range(y0, 2027):
            try:
                text = get(c, Year=y, Month=1, Day=1, timeframe=2)
                for r in csv.DictReader(io.StringIO(text)):
                    assert r["Climate ID"] == "6158731", r["Climate ID"]
                    daily[r["Date/Time"]] = (r["Max Temp (°C)"], r["Min Temp (°C)"], r["Max Temp Flag"], r["Min Temp Flag"])
            except Exception as e:
                errs.append(f"daily {y}: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(3)
        y, m = int(first[:4]), int(first[5:7])
        while (y, m) <= (2026, 10):
            try:
                text = get(c, Year=y, Month=m, Day=1, timeframe=1)
                for r in csv.DictReader(io.StringIO(text)):
                    assert r["Climate ID"] == "6158731", r["Climate ID"]
                    if r.get("Temp (°C)") and r.get("Date/Time (LST)"):  # current month carries short/empty future rows
                        ts = datetime.strptime(r["Date/Time (LST)"], "%Y-%m-%d %H:%M").replace(tzinfo=LST).astimezone(UTC)
                        hourly[ts] = float(r["Temp (°C)"])
            except Exception as e:
                errs.append(f"hourly {y}-{m:02d}: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(3)
            m += 1
            if m == 13:
                y, m = y + 1, 1
    print("ECCC daily rows", len(daily), "hourly rows", len(hourly), "errors", errs)
    data = sorted(hourly.items())
    write_raw("eccc_cyyz_hourly", [(k.isoformat(), v) for k, v in data])
    compute_and_save("eccc_cyyz_hourly", city=CITY, tzname=TZNAME, cadence_s=3600, readings=data,
                     meta=dict(provider="ECCC climate bulk hourly (station 51459, LST->UTC)", errors=errs,
                               caveat="hourly spot values: high is a lower bound, low an upper bound",
                               source_channel="eccc_swob_temperature (different product)"))
    # daily product, compared as published
    truth = settled(CITY)
    days = {}
    for d, (tx, tn, fx, fn) in daily.items():
        if d < first or d > LAST_SETTLED:
            continue
        ok = tx != "" and tn != ""
        days[d] = dict(n=1 if ok else 0, expected=1, coverage=1.0 if ok else 0.0,
                       max_raw=float(tx) if tx != "" else None, min_raw=float(tn) if tn != "" else None,
                       max_contract=contract(float(tx)) if tx != "" else None,
                       min_contract=contract(float(tn)) if tn != "" else None, flags=[fx, fn])
    res = {"daily": days}
    for metric in ("high", "low"):
        rows, agg = compare(days, truth, metric)
        res[metric] = dict(agg=agg, rows=rows)
        print(f"eccc_cyyz_daily {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
    save_candidate("eccc_cyyz_daily", dict(provider="ECCC climate bulk daily Max/Min (LST day)", station="51459 TORONTO INTL A",
                                           city=CITY, tz=TZNAME, errors=errs, first_day=first, last_day=LAST_SETTLED,
                                           caveat="ECCC day is local STANDARD time (UTC-5); during DST it is shifted +1 h vs the settlement calendar day",
                                           rounding="floor(v+0.5)", source_channel="eccc_swob_temperature (different product)"), res)


if __name__ == "__main__":
    main()
