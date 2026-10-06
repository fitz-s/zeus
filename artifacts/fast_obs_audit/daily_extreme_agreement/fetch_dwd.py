"""Munich EDDM: DWD CDC open data 10-minute air_temperature, station 01262 (registry provider_station).

Directories: recent (10minutenwerte_TU_01262_akt.zip, ~500 days to yesterday) and now (today).
No documented rate limit on opendata.dwd.de; we make 2 requests.
TT_10 = air temperature 2 m, deg C. MESS_DATUM is UTC (YYYYMMDDHHMM), end of the 10-minute interval.
QN_9 quality flag and -999 missing are filtered.
"""
import csv
import io
import time
import zipfile
from datetime import datetime

import httpx

from dae_common import RAW, UA, UTC, compute_and_save, first_settled, write_raw

CITY, TZNAME, STATION = "Munich", "Europe/Berlin", 1262
BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature"
FILES = [f"{BASE}/recent/10minutenwerte_TU_01262_akt.zip", f"{BASE}/now/10minutenwerte_TU_01262_now.zip"]


def parse(body: bytes) -> dict[datetime, float]:
    out = {}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = [n for n in z.namelist() if n.startswith("produkt_") and n.endswith(".txt")]
        assert len(names) == 1, names
        text = z.read(names[0]).decode("utf-8-sig")
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        row = {k.strip(): v.strip() for k, v in row.items()}
        assert int(row["STATIONS_ID"]) == STATION
        v = float(row["TT_10"])
        if v <= -990:
            continue
        out[datetime.strptime(row["MESS_DATUM"], "%Y%m%d%H%M").replace(tzinfo=UTC)] = v
    return out


def main():
    rows, errs, got = {}, [], []
    RAW.mkdir(exist_ok=True)
    with httpx.Client(headers=UA, follow_redirects=False) as c:
        for url in FILES:
            try:
                r = c.get(url, timeout=120)
                r.raise_for_status()
                d = parse(r.content)
                got.append((url.rsplit("/", 1)[1], len(r.content), len(d), min(d).isoformat(), max(d).isoformat()))
                rows.update(d)
            except Exception as e:
                errs.append(f"{url}: {type(e).__name__}: {str(e)[:160]}")
            time.sleep(2)
    data = sorted(rows.items())
    print("DWD files:", got, "errors:", errs)
    write_raw("dwd_eddm", [(k.isoformat(), v) for k, v in data])
    compute_and_save("dwd_eddm", city=CITY, tzname=TZNAME, cadence_s=600, readings=data,
                     meta=dict(provider="DWD CDC 10-min air_temperature recent+now", station="01262 (EDDM)",
                               files=got, errors=errs, source_channel="dwd_cdc_temperature"))


if __name__ == "__main__":
    main()
