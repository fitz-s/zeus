"""Record which source each VERIFIED settlement day was resolved from (provenance_json.data_version), per city.

Finding: before 2026-08-23/24 the daily truth was written from WU ICAO history (or ogimet METAR for Istanbul/Moscow);
from then on from NOAA WRH timeseries (half-hourly METAR-derived, integer C). The summary reports every comparison
on all VERIFIED days (as specified) AND on the WRH era alone, because the WRH era is the live resolver.
Output: per_candidate/_truth_eras.json  {city: {"wrh_start": date, "by_source": {data_version: [first,last,n]}}}
Read-only (forecasts DB mode=ro).
"""
import json
import sqlite3

from dae_common import FORECASTS_URI, OUT

CITIES = ("Helsinki", "Munich", "Warsaw", "Amsterdam", "London", "Tokyo", "Toronto", "Ankara", "Istanbul", "Lucknow", "Moscow",
          "Madrid", "Singapore", "Sao Paulo")


def main():
    con = sqlite3.connect(FORECASTS_URI, uri=True, timeout=10)
    con.execute("pragma query_only=1")
    out = {}
    for city in CITIES:
        by, wrh = {}, None
        for d, prov in con.execute("select target_date, provenance_json from settlement_outcomes where city=? and authority='VERIFIED' "
                                   "and settlement_value is not null", (city,)):
            dv = (json.loads(prov) if prov else {}).get("data_version") or "none"
            lo, hi, n = by.get(dv, (d, d, 0))
            by[dv] = (min(lo, d), max(hi, d), n + 1)
        wrh = by.get("noaa_wrh_timeseries_v1", (None,))[0]
        out[city] = dict(wrh_start=wrh, by_source={k: list(v) for k, v in by.items()})
        print(city, wrh, {k: v for k, v in by.items()})
    OUT.mkdir(exist_ok=True)
    json.dump(out, open(OUT / "_truth_eras.json", "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
