"""Warsaw EPWA: IMGW public archive (danepubliczne.imgw.pl) synop hourly data, station 12375 / NSP 352200375 WARSZAWA.

The live API (api/data/synop/id/12375) is current-only; the archive terminowe/synop/<year>/<year>_<mm>_s.zip
carries every synop hour (GG, UTC) as TEMP (deg C). Cadence is hourly, so the daily max/min is a
LOWER/UPPER BOUND on the continuous extreme. Months published only through 2026-09 (October is not yet
archived) -> 2026-10-01..05 are dropped for coverage.
Rate: 7 monthly zips, 3 s sleep.
"""
import csv
import io
import time
import zipfile
from datetime import datetime

import httpx

from dae_common import UA, UTC, compute_and_save, write_raw

CITY, TZNAME, NSP = "Warsaw", "Europe/Warsaw", "352200375"
BASE = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne/terminowe/synop"
HEADER = ("NSP,POST,ROK,MC,DZ,GG,HPOD,WHPOD,HPON,WHPON,HPOW,WHPOW,HTXT,POM1,POM2,WID,WWID,WIDO,WWIDO,WIDA,WWIDA,"
          "NOG,WNOG,KRWR,WKRWR,FWR,WFWR,PORW,WPORW,TEMP,WTEMP").split(",")
I_TEMP, I_WTEMP = HEADER.index("TEMP"), HEADER.index("WTEMP")


def main():
    rows, errs, got = {}, [], []
    with httpx.Client(headers=UA, follow_redirects=False) as c:
        for m in range(3, 11):
            name = f"2026_{m:02d}_s.zip"
            url = f"{BASE}/2026/{name}"
            try:
                for attempt in range(3):  # IMGW drops connections intermittently
                    try:
                        r = c.get(url, timeout=180)
                        break
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                        time.sleep(15)
                r.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    inner = [n for n in z.namelist() if n.endswith(".csv")]
                    assert len(inner) == 1, inner
                    text = z.read(inner[0]).decode("cp1250")
                n = 0
                for row in csv.reader(io.StringIO(text)):
                    if row[0] != NSP or row[I_TEMP] == "" or row[I_WTEMP] not in ("", "8"):
                        continue  # status 8 = measurement-not-made code in IMGW convention; blank = ok
                    ts = datetime(int(row[2]), int(row[3]), int(row[4]), int(row[5]), tzinfo=UTC)
                    rows[ts] = float(row[I_TEMP])
                    n += 1
                got.append((name, len(r.content), n))
            except Exception as e:
                errs.append(f"{url}: {type(e).__name__}: {str(e)[:160]}")
            time.sleep(3)
    data = sorted(rows.items())
    print("IMGW files:", got, "errors:", errs)
    write_raw("imgw_epwa", [(k.isoformat(), v) for k, v in data])
    compute_and_save("imgw_epwa", city=CITY, tzname=TZNAME, cadence_s=3600, readings=data,
                     meta=dict(provider="IMGW public archive terminowe/synop (hourly)", station="12375 / NSP 352200375 WARSZAWA",
                               files=got, errors=errs, source_channel="imgw_synop_temperature",
                               caveat="hourly synop values only: daily max is a lower bound, daily min an upper bound"))


if __name__ == "__main__":
    main()
