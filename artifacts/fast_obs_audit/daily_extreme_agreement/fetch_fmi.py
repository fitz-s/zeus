"""Helsinki EFHK: FMI open WFS 10-minute temperature history (fmisid 100968), 7-day chunks.

Reuses src/data/fmi_airport_temperature.py for endpoint, parameter metadata check and station identity.
Documented limits (config/physical_current_sources.json): 20000 req/day, 600 req/5min; we sleep 1.5 s.
"""
import time
from datetime import datetime, timedelta

import httpx

from dae_common import UA, UTC, compute_and_save, first_settled, write_raw
from src.data import fmi_airport_temperature as fmi

CITY, TZNAME = "Helsinki", "Europe/Helsinki"


def main():
    first = first_settled(CITY)
    start = datetime.fromisoformat(first).replace(tzinfo=UTC) - timedelta(days=1)
    end = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    rows, errs, reqs = {}, [], 0
    with httpx.Client(headers=UA) as client:
        meta = client.get(fmi.TEMPERATURE_PROPERTY, timeout=20)
        meta.raise_for_status()
        fmi.parse_temperature_metadata(meta.text)
        t = start
        while t < end:
            t2 = min(t + timedelta(days=7), end)
            try:
                r = client.get(fmi.ENDPOINT, params={
                    "service": "WFS", "version": "2.0.0", "request": "getFeature",
                    "storedquery_id": "fmi::observations::weather::multipointcoverage",
                    "fmisid": fmi.FMISID, "starttime": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endtime": t2.strftime("%Y-%m-%dT%H:%M:%SZ"), "parameters": "temperature", "timestep": "10",
                }, timeout=60)
                reqs += 1
                r.raise_for_status()
                for p in fmi.parse_temperature_coverage(r.text, fetched_at=datetime.now(UTC)):
                    rows[p.observed_at] = p.temperature_c
            except Exception as e:  # recorded verbatim in summary
                errs.append(f"{t.date()}..{t2.date()}: {type(e).__name__}: {str(e)[:160]}")
            t = t2
            time.sleep(1.5)
    data = sorted(rows.items())
    print(f"FMI requests={reqs} readings={len(data)} errors={len(errs)}")
    for e in errs:
        print("  ERR", e)
    write_raw("fmi_efhk", [(k.isoformat(), v) for k, v in data])
    compute_and_save("fmi_efhk", city=CITY, tzname=TZNAME, cadence_s=600, readings=data,
                     meta=dict(provider="FMI open WFS", station="EFHK fmisid 100968", cadence_s=600,
                               requests=reqs, errors=errs, source_channel="fmi_airport_temperature"))


if __name__ == "__main__":
    main()
