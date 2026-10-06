"""London EGLC: Met Office Weather DataHub land observations (free plan, 360 calls/day; hard cap here: 20).

Key: macOS keychain `zeus-obs/metoffice/api-key`, sent only as the `apikey` header to data.hub.api.metoffice.gov.uk;
never printed or written. Calls made by this script: <= 3 (geohash gcpvj0 hourly, plus identity lookup of the
nearest geohash to EGLC lat/lon 51.505/0.055).
The endpoint returns ~48 h of hourly SPOT temperature at a geohash (not a station extreme), so a daily max
from it is a LOWER bound of the true max and a daily min an UPPER bound.
"""
import gzip
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

from dae_common import RAW, UA, UTC, compute_and_save, local_day_bounds

CITY, TZNAME = "London", "Europe/London"
KEY = subprocess.check_output(["security", "find-generic-password", "-w", "-s", "zeus-obs/metoffice/api-key"]).decode().strip()
BASE = "https://data.hub.api.metoffice.gov.uk/observation-land/1"
CALLS = {"n": 0, "cap": 20}


def call(path):
    assert CALLS["n"] < CALLS["cap"], "call cap reached"
    CALLS["n"] += 1
    req = urllib.request.Request(BASE + path, headers={**UA, "apikey": KEY, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            hdr = {k: v for k, v in r.headers.items() if "rate" in k.lower() or "limit" in k.lower() or "quota" in k.lower()}
            return r.status, hdr, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k: v for k, v in e.headers.items() if "rate" in k.lower() or "limit" in k.lower()}, e.read()


def main(replay: str | None = None):
    """replay: path of a saved raw payload (zero API calls). The first live run made 2 calls:
    GET /nearest?lat=51.505&lon=0.055 -> 400 'at most 2 decimal places' (lat/lon was given with 3 decimals; not retried),
    GET /gcpvj0 -> 200, 48 hourly points."""
    RAW.mkdir(exist_ok=True)
    errs = []
    if replay:
        b2 = gzip.open(replay, "rb").read()
        s1, h1, s2, h2 = 400, {}, 200, {}
        errs.append("nearest-geohash lookup: HTTP 400 (3-decimal lat/lon rejected; geohash gcpvj0 taken from earlier probe)")
        CALLS["n"] = 2
    else:
        s1, h1, b1 = call("/nearest?lat=51.505&lon=0.055")
        time.sleep(2)
        s2, h2, b2 = call("/gcpvj0")
        print("nearest", s1, h1, b1[:200].decode("utf-8", "replace"))
        print("gcpvj0", s2, h2, len(b2))
        if s2 != 200:
            errs.append(f"gcpvj0: HTTP {s2} {b2[:200].decode('utf-8', 'replace')}")
            raise SystemExit(errs)
        with gzip.open(RAW / f"metoffice_gcpvj0_fetched_{datetime.now(UTC):%Y%m%dT%H%MZ}.json.gz", "wb") as f:
            f.write(b2)
    data = json.loads(b2)
    readings = {}
    for r in data:
        if r.get("temperature") is not None:
            readings[datetime.fromisoformat(r["datetime"].replace("Z", "+00:00"))] = float(r["temperature"])
    pts = sorted(readings.items())
    print("hourly points", len(pts), pts[0][0], pts[-1][0])
    # Only local days wholly inside the returned window are scored; coverage vs 24 expected hourly values.
    first_full = None
    for k in ("2026-10-04", "2026-10-05", "2026-10-06"):
        a, b = local_day_bounds(k, TZNAME)
        n = sum(1 for t, _ in pts if a <= t < b)
        print(k, "points in local day", n)
    compute_and_save("metoffice_london_gcpvj0", city=CITY, tzname=TZNAME, cadence_s=3600, readings=pts,
                     first_day="2026-10-04", only_days={"2026-10-04", "2026-10-05"},
                     meta=dict(provider="Met Office DataHub observation-land/1 geohash gcpvj0 (hourly spot)",
                               api_calls=CALLS["n"], call_cap=CALLS["cap"], errors=errs,
                               nearest_lookup=dict(status=s1, ratelimit_headers=h1),
                               hourly_ratelimit_headers=h2,
                               caveat="~48 h window only; hourly spot values, high is a lower bound, low an upper bound; "
                                      "geohash is a grid cell, not the EGLC station",
                               source_channel="none (not a live channel)"))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
