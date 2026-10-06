"""Munich EDDM: DWD CDC 10-minute EXTREMA product (extreme_temperature/recent+now): TX_10 = max and TN_10 = min of air
temperature at 2 m within each 10-minute interval (vs TT_10, the instantaneous value the live dwd_cdc adapter reads).
Same station 01262, MESS_DATUM UTC. The daily max/min from this product is the station's true sub-10-min extreme,
so it is the fair analogue of what a resolver reading continuous data would see. 2 requests, 2 s sleep.
"""
import csv
import io
import time
import zipfile
from datetime import datetime

import httpx

from dae_common import UA, UTC, LAST_SETTLED, COVERAGE_MIN, compare, contract, day_extremes, first_settled, save_candidate, settled

CITY, TZNAME, STATION = "Munich", "Europe/Berlin", 1262
BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/extreme_temperature"
FILES = [f"{BASE}/recent/10minutenwerte_extrema_temp_01262_akt.zip", f"{BASE}/now/10minutenwerte_extrema_temp_01262_now.zip"]


def parse(body):
    tx, tn = {}, {}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = [n for n in z.namelist() if n.startswith("produkt_") and n.endswith(".txt")]
        assert len(names) == 1, names
        text = z.read(names[0]).decode("utf-8-sig")
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        row = {k.strip(): v.strip() for k, v in row.items()}
        assert int(row["STATIONS_ID"]) == STATION
        ts = datetime.strptime(row["MESS_DATUM"], "%Y%m%d%H%M").replace(tzinfo=UTC)
        if float(row["TX_10"]) > -990:
            tx[ts] = float(row["TX_10"])
        if float(row["TN_10"]) > -990:
            tn[ts] = float(row["TN_10"])
    return tx, tn


def main():
    TX, TN, errs, got = {}, {}, [], []
    with httpx.Client(headers=UA) as c:
        for url in FILES:
            try:
                r = c.get(url, timeout=120)
                r.raise_for_status()
                a, b = parse(r.content)
                TX.update(a)
                TN.update(b)
                got.append((url.rsplit("/", 1)[1], len(r.content), len(a), len(b)))
            except Exception as e:
                errs.append(f"{url}: {type(e).__name__}: {str(e)[:160]}")
            time.sleep(2)
    first = first_settled(CITY)
    dx = day_extremes(sorted(TX.items()), TZNAME, 600, first)
    dn = day_extremes(sorted(TN.items()), TZNAME, 600, first)
    days = {}
    for d in dx:
        ok = dx[d]["coverage"] >= COVERAGE_MIN and dn[d]["coverage"] >= COVERAGE_MIN
        days[d] = dict(n=min(dx[d]["n"], dn[d]["n"]), expected=dx[d]["expected"], coverage=min(dx[d]["coverage"], dn[d]["coverage"]),
                       max_raw=dx[d]["max_raw"], min_raw=dn[d]["min_raw"],
                       max_contract=dx[d]["max_contract"], min_contract=dn[d]["min_contract"])
    truth = settled(CITY)
    res = {"daily": days}
    for metric in ("high", "low"):
        rows, agg = compare(days, truth, metric)
        res[metric] = dict(agg=agg, rows=rows)
        print(f"dwd_eddm_extrema {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
    print(got, errs)
    save_candidate("dwd_eddm_extrema", dict(
        provider="DWD CDC 10-min extreme_temperature (TX_10/TN_10) recent+now", station="01262 (EDDM)", city=CITY, tz=TZNAME,
        files=got, errors=errs, first_day=first, last_day=LAST_SETTLED, coverage_min=COVERAGE_MIN, rounding="floor(v+0.5)",
        caveat="TX_10/TN_10 are not read by the live dwd_cdc adapter (it reads TT_10)",
        source_channel="dwd_cdc_temperature (different column)"), res)


if __name__ == "__main__":
    main()
