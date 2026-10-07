"""Historical forecast path f_t for the state-space model: Open-Meteo previous-runs API, model ecmwf_ifs,
hourly temperature_2m_previous_day1 (value predicted 24 h before valid time; fixed lead, strictly causal for every
decision time on the valid local day). Same model id, coordinates (config/cities.json lat/lon) and cell_selection=land
as the live day0_hourly_vectors ecmwf_ifs rows.

Why not day0_hourly_vectors: that table starts 2026-10-04 (3 days); the ifs9 raw manifests on disk cover ~12 cycle
dates. A 2026-03..10 back-test needs a historical path; previous_day1 is the causal stand-in (lead 24-48 h, so it is
LESS sharp than the live day0 vector -- arms A0/A/B all share it, so the A/B comparison is unaffected).
One batched request for 8 locations (~8 x 16 = 128 Open-Meteo units). Output: raw/ecmwf_ifs_previous_day1.json.gz
"""
import gzip
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CITIES = ("Helsinki", "Munich", "Warsaw", "Amsterdam", "Tokyo", "Singapore", "Toronto", "Madrid")


def main():
    cj = json.load(open(REPO / "config" / "cities.json"))
    cj = {c["name"]: c for c in (cj["cities"] if isinstance(cj, dict) else cj)}
    lat = ",".join(str(cj[c]["lat"]) for c in CITIES)
    lon = ",".join(str(cj[c]["lon"]) for c in CITIES)
    q = urllib.parse.urlencode(dict(latitude=lat, longitude=lon, hourly="temperature_2m_previous_day1", models="ecmwf_ifs",
                                    start_date="2025-11-30", end_date="2026-10-08", timezone="UTC", cell_selection="land",
                                    temperature_unit="celsius"))
    url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + q
    at = datetime.now(timezone.utc).isoformat()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "zeus-obs-research (read-only audit)"}), timeout=120) as r:
        body = json.loads(r.read())
    body = body if isinstance(body, list) else [body]
    out = dict(fetched_at=at, url=url, cities={c: dict(latitude=b["latitude"], longitude=b["longitude"], elevation=b.get("elevation"),
                                                       time=b["hourly"]["time"], temp=b["hourly"]["temperature_2m_previous_day1"])
                                               for c, b in zip(CITIES, body)})
    (HERE / "raw" / "ecmwf_ifs_previous_day1.json.gz").write_bytes(gzip.compress(json.dumps(out).encode()))
    for c, v in out["cities"].items():
        n = sum(x is not None for x in v["temp"])
        print(c, len(v["time"]), "non-null", n, v["time"][0], v["time"][-1])


if __name__ == "__main__":
    main()
