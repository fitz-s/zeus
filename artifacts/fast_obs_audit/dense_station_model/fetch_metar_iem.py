"""METAR + SPECI history per station from the IEM ASOS archive (free, no key).

One request per station, 2 s between requests. report_type=3 (routine) and 4 (specials) are both requested.
IEM's labels are NOT used to identify SPECIs: for half-hourly stations IEM files the second routine report of the
hour under type 4 (probed 2026-10-07: WSSS type 3 = :00 only, type 4 = :30 plus off-cycle reports; EFHK type 4 = :20).
The model therefore classifies a report as routine iff its minute is one of the station's dominant cadence minutes.
Raw body saved verbatim (gzip) as raw/metar_iem_<ICAO>.csv.gz.
"""
import gzip
import time
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
UA = {"User-Agent": "zeus-obs-research (read-only audit)"}
URL = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station={s}&data=metar"
       "&year1={y1}&month1={m1}&day1={d1}&year2=2026&month2=10&day2=8&tz=Etc/UTC&format=onlycomma"
       "&latlon=no&missing=M&trace=T&direct=no&report_type=3&report_type=4")
START = {"CYYZ": (2025, 12, 1)}  # Toronto truth starts 2025-12-06; everything else from 2026-03-01


def main():
    RAW.mkdir(exist_ok=True)
    for i, s in enumerate(("EFHK", "EDDM", "EPWA", "EHAM", "RJTT", "WSSS", "CYYZ", "LEMD")):
        if i:
            time.sleep(2)
        y1, m1, d1 = START.get(s, (2026, 3, 1))
        url = URL.format(s=s, y1=y1, m1=m1, d1=d1)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                body = r.read()
            (RAW / f"metar_iem_{s}.csv.gz").write_bytes(gzip.compress(body))
            print(s, len(body), body.count(b"\n"), "rows", flush=True)
        except Exception as e:
            print(s, "ERROR", type(e).__name__, str(e)[:200], flush=True)


if __name__ == "__main__":
    main()
