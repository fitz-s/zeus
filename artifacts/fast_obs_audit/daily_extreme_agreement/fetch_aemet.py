"""Madrid LEMD: AEMET public website XML (no key), station 3129 "Madrid Aeropuerto" (id_s 08221), as used in Round 4.

Endpoints (Round 4 URL pattern: .../api-eltiempo/udat/tablas-graficas/<horario|diario>/9/3129):
  horario -> last 24 hourly periods only (no archive).
  diario  -> last 7 local days of daily tmax/tmin, each with the 10-minute `hora_local` at which it occurred.
Probed variants (horario/1, /72, /0) return the same 24-period window; there is no deeper history exposed.
So at most 7 settled days are comparable per run (history accrues only if this script is run daily; it is not
persisted anywhere live). Compared as published; AEMET's `periodo local` day is taken as the local calendar day
(check column `tmax_hora_local` date == day in the output rows).
"""
import gzip
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from dae_common import RAW, UA, COVERAGE_MIN, LAST_SETTLED, compare, contract, save_candidate, settled

CITY = "Madrid"
BASE = "https://www.aemet.es/es/api-eltiempo/udat/tablas-graficas"


def main():
    RAW.mkdir(exist_ok=True)
    errs = []
    days = {}
    with httpx.Client(headers=UA) as c:
        for kind in ("diario", "horario"):
            try:
                r = c.get(f"{BASE}/{kind}/9/3129", timeout=30)
                r.raise_for_status()
                with gzip.open(RAW / f"aemet_lemd_{kind}_fetched_{datetime.utcnow():%Y%m%dT%H%MZ}.xml.gz", "wb") as f:
                    f.write(r.content)
                if kind == "diario":
                    root = ET.fromstring(r.content)
                    est = root.find("estacion")
                    assert est.get("id_c") == "3129" and est.get("nombre") == "Madrid Aeropuerto", est.attrib
                    for p in est.findall("periodo"):
                        day = p.get("local")[:10]
                        tx, tn = p.find("tmax"), p.find("tmin")
                        ok = tx is not None and tn is not None and tx.text and tn.text
                        days[day] = dict(
                            n=1 if ok else 0, expected=1, coverage=1.0 if ok else 0.0,
                            max_raw=float(tx.text) if ok else None, min_raw=float(tn.text) if ok else None,
                            max_contract=contract(float(tx.text)) if ok else None,
                            min_contract=contract(float(tn.text)) if ok else None,
                            tmax_hora_local=tx.get("hora_local") if tx is not None else None,
                            tmin_hora_local=tn.get("hora_local") if tn is not None else None)
            except Exception as e:
                errs.append(f"{kind}: {type(e).__name__}: {str(e)[:160]}")
            time.sleep(2)
    truth = settled(CITY)
    res = {"daily": days}
    for metric in ("high", "low"):
        # restrict to the days the 7-day window can cover; other settled days are "not exposed", not "dropped for coverage"
        sub = {m: {d: v for d, v in truth[m].items() if d in days} for m in truth}
        rows, agg = compare(days, sub, metric)
        agg["history_limited_to_days"] = sorted(days)
        res[metric] = dict(agg=agg, rows=rows)
        print(f"aemet_lemd {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
    print("days exposed:", sorted(days), "errors:", errs)
    save_candidate("aemet_lemd", dict(
        provider="AEMET public XML diario (7-day window)", station="3129 Madrid Aeropuerto (id_s 08221)", city=CITY,
        tz="Europe/Madrid", errors=errs, rounding="floor(v+0.5)", first_day=min(days) if days else None, last_day=LAST_SETTLED,
        caveat="API exposes only the last 7 days; n_days is capped at 7 per run, far below the 30-day bar",
        source_channel="aemet_station_xml (Round 4 only; not a live registry channel)"), res)


if __name__ == "__main__":
    main()
