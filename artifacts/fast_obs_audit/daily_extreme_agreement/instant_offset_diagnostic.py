"""Diagnostic for WHY a candidate's daily extreme disagrees: same-instant offset of the candidate's raw decimal reading
versus the AWC METAR integer temperature (WORLD, since 2026-07-16), at METAR observation instants (exact-minute match).

Reports per candidate: n paired instants, share where contract(cand) == METAR, mean and median of (cand_raw - METAR),
and the share of pairs with cand_contract = METAR -1 / +1. A candidate whose raw readings sit systematically below the
METAR (negative mean offset) produces `under` on highs and agrees on lows; a candidate that samples between METARs
produces `over` on highs and `under` on lows without any instrument offset (see subsample_at_resolver_instants.py).
Uses raw files fetched by the provider scripts; reads WORLD mode=ro; no network.
"""
import json
import re
import sqlite3
import statistics as st
from datetime import datetime, timedelta

from dae_common import HERE, UTC, WORLD_URI, contract, read_raw

SINCE = "2026-07-16"
SPECS = [("fmi_efhk", "EFHK", "Helsinki"), ("dwd_eddm", "EDDM", "Munich"), ("imgw_epwa", "EPWA", "Warsaw"),
         ("nea_s24_wsss", "WSSS", "Singapore"), ("eccc_cyyz_hourly", "CYYZ", "Toronto")]
W = sqlite3.connect(WORLD_URI, uri=True, timeout=10)
W.execute("pragma query_only=1")


def t(s):
    d = datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def instant(raw, fetched):
    m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", raw or "")
    if not m:
        return None
    dd, hh, mm = map(int, m.groups())
    for k in (0, -1):
        mo = (fetched.replace(day=1) + timedelta(days=32 * k)).replace(day=1)
        try:
            c = mo.replace(day=dd, hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            continue
        if timedelta(0) <= fetched - c <= timedelta(days=2):
            return c


def main():
    out = {}
    for name, stn, city in SPECS:
        try:
            cand = dict(read_raw(name))
        except FileNotFoundError:
            continue
        metar = {}
        for raw, val, fe in W.execute("select raw_report, value_native, fetched_at_utc from observation_prints where city=? and station_id=? and "
                                      "source_channel='aviationweather_metar' and publish_ts_utc>=?", (city, stn, SINCE)):
            o = instant(raw, t(fe))
            if o:
                metar[o] = float(val)
        diffs, ce = [], []
        for o, m in metar.items():
            if o in cand:
                diffs.append(cand[o] - m)
                ce.append(contract(cand[o]) - int(m))
        n = len(diffs)
        if n == 0:
            out[name] = dict(n_pairs=0)
            continue
        out[name] = dict(n_pairs=n, exact_contract_equal=round(sum(x == 0 for x in ce) / n, 4),
                         cand_minus_metar_contract=dict(minus1=sum(x == -1 for x in ce), zero=sum(x == 0 for x in ce), plus1=sum(x == 1 for x in ce),
                                                        other=sum(abs(x) > 1 for x in ce)),
                         mean_raw_offset=round(st.mean(diffs), 3), median_raw_offset=round(st.median(diffs), 3))
        print(name, out[name])
    json.dump(out, open(HERE / "per_candidate" / "_instant_offset_vs_metar.json", "w"), indent=1)


if __name__ == "__main__":
    main()
