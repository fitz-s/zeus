"""Tokyo RJTT: the LIVE channel's own endpoint, JMA AMeDAS 10-minute point files for station 44166 (Haneda), pulled for
the whole retention window (probed 2026-10-06: files exist from 2026-09-27, 404 before).

URL (same as src/data/station_temperature_adapters.py): https://www.jma.go.jp/bosai/amedas/data/point/44166/YYYYMMDD_HH.json
with HH in {00,03,...,21} (JST); each file carries 3 h of 10-minute rows {stamp: {"temp": [value, qualityflag]}}.
Quality flag 0 = normal (same filter as the live adapter). Stamps are JST.
Rate: 8 files/day x 10 days = ~80 requests, 1.5 s sleep, no key.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from dae_common import UA, UTC, compute_and_save, write_raw

CITY, TZNAME, ST = "Tokyo", "Asia/Tokyo", "44166"
JST = timezone(timedelta(hours=9))
FIRST = date(2026, 9, 27)
LAST = date(2026, 10, 6)


def main():
    rows, errs, n404, nreq, qbad = {}, [], 0, 0, 0
    d = FIRST
    while d <= LAST:
        for hh in range(0, 24, 3):
            url = f"https://www.jma.go.jp/bosai/amedas/data/point/{ST}/{d:%Y%m%d}_{hh:02d}.json"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                    body = json.load(r)
                nreq += 1
                for stamp, row in body.items():
                    t = row.get("temp")
                    if isinstance(t, list) and len(t) == 2:
                        if t[1] == 0:
                            rows[datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=JST).astimezone(UTC)] = float(t[0])
                        else:
                            qbad += 1
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    n404 += 1  # future hour or expired file
                else:
                    errs.append(f"{url}: HTTP {e.code}")
            except Exception as e:
                errs.append(f"{url}: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(1.5)
        d += timedelta(days=1)
    data = sorted(rows.items())
    first_day = data[0][0].astimezone(JST).date().isoformat()
    print("JMA AMeDAS 10-min readings", len(data), "range", data[0][0], data[-1][0], "404s", n404, "bad-quality", qbad, "errors", errs)
    write_raw("jma_amedas_44166_10min", [(k.isoformat(), v) for k, v in data])
    # first local day is only complete if the first file starts at 00:00 JST
    compute_and_save("jma_amedas_44166_10min", city=CITY, tzname=TZNAME, cadence_s=600, readings=data,
                     first_day=(datetime.fromisoformat(first_day).date() + timedelta(days=1 if data[0][0].astimezone(JST).hour else 0)).isoformat(),
                     only_days={(FIRST + timedelta(days=i)).isoformat() for i in range(0, 10)},
                     meta=dict(provider="JMA AMeDAS bosai 10-minute point files (live channel endpoint)", station="44166 Haneda",
                               requests=nreq, http404=n404, bad_quality_rows=qbad, errors=errs,
                               source_channel="jma_amedas_temperature"))


if __name__ == "__main__":
    main()
