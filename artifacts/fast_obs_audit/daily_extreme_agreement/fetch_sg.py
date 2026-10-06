"""Singapore WSSS: NEA / data.gov.sg v1 real-time air-temperature, station S24 (the Round 3 comparison station).

GET https://api.data.gov.sg/v1/environment/air-temperature?date=YYYY-MM-DD  -> ~1-minute readings for that SGT day
(no key). Round 3/4 used S24 "Changi Meteorological Station". Identity caveat found during this audit: in the same
endpoint S24 was named "Upper Changi Road North" (lon 103.9826) on 2025-12..2026-06 and "Changi Meteorological
Station" (lon 103.9823) on 2026-09; the per-day station name/coordinates are recorded in the output meta.
Rate: no documented limit for the keyless API; one request per day with a 3 s sleep and 429 back-off.
Singapore has no DST, so every local day is 1440 minutes.
"""
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta

from dae_common import UA, UTC, compute_and_save, first_settled, write_raw, LAST_SETTLED

CITY, TZNAME, STN = "Singapore", "Asia/Singapore", "S24"
URL = "https://api.data.gov.sg/v1/environment/air-temperature?date="


def fetch_day(d: str):
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL + d, headers=UA), timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            time.sleep(60 if e.code == 429 else 10 * (attempt + 1))
            if e.code in (400, 404):
                break
        except Exception as e:
            last = e
            time.sleep(10 * (attempt + 1))
    raise last


def main():
    first = first_settled(CITY)
    rows, errs, names = {}, [], {}
    d, end = date.fromisoformat(first), date.fromisoformat(LAST_SETTLED)
    n_req = 0
    while d <= end:
        k = d.isoformat()
        try:
            j = fetch_day(k)
            n_req += 1
            st = {s["id"]: s for s in j["metadata"]["stations"]}.get(STN)
            names[k] = (st or {}).get("name"), (st or {}).get("location")
            for it in j["items"]:
                ts = datetime.fromisoformat(it["timestamp"]).astimezone(UTC)
                for rd in it["readings"]:
                    if rd["station_id"] == STN and rd["value"] is not None:
                        rows[ts] = float(rd["value"])
        except Exception as e:
            errs.append(f"{k}: {type(e).__name__}: {str(e)[:150]}")
        d += timedelta(days=1)
        time.sleep(3)
        if n_req % 20 == 0:
            print(f"[sg] {k} requests={n_req} readings={len(rows)} errors={len(errs)}", flush=True)
    data = sorted(rows.items())
    ident = Counter((n, json.dumps(loc, sort_keys=True)) for n, loc in names.values())
    print("SG readings", len(data), "errors", len(errs), errs[:5], "identity:", dict(ident))
    write_raw("nea_s24_wsss", [(k.isoformat(), v) for k, v in data])
    compute_and_save("nea_s24_wsss", city=CITY, tzname=TZNAME, cadence_s=60, readings=data,
                     meta=dict(provider="NEA via data.gov.sg v1 air-temperature (S24)", station="S24",
                               requests=n_req, errors=errs,
                               s24_identity_by_day={f"{n} {loc}": c for (n, loc), c in ident.items()},
                               source_channel="not a live registry channel (Round 3/4 PREVIOUS_MEASURED_MISMATCH_RETAINED)"))


if __name__ == "__main__":
    main()
