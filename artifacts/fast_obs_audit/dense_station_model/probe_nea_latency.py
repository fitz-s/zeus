"""Singapore WSSS: live availability lag of NEA S24 (Changi Meteorological Station) 1-minute air temperature.

There is no live WORLD channel for NEA, so this polls the keyless data.gov.sg v1 latest endpoint every 30 s for
DURATION_MIN minutes (~200 requests) and records, for every S24 minute stamp, the first local receipt time.
Availability lag = first receipt - stamp (upper-bounded by the 30 s poll interval). The AWC receipt of the WSSS
METARs in the same window is read later from WORLD (mode=ro) by the runner.
Output: raw/nea_s24_live_probe.json
"""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "raw" / "nea_s24_live_probe.json"
URL = "https://api.data.gov.sg/v1/environment/air-temperature"
UA = {"User-Agent": "zeus-obs-research (read-only audit)"}
DURATION_MIN, EVERY_S = 100, 30


def main():
    first_seen, polls, errs = {}, [], []
    end = time.time() + DURATION_MIN * 60
    while time.time() < end:
        req = datetime.now(timezone.utc)
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=20) as r:
                j = json.load(r)
            rec = datetime.now(timezone.utc)
            for it in j.get("items", []):
                for rd in it["readings"]:
                    if rd["station_id"] == "S24":
                        ts = datetime.fromisoformat(it["timestamp"]).astimezone(timezone.utc).isoformat()
                        first_seen.setdefault(ts, dict(receipt=rec.isoformat(), value=rd["value"]))
            polls.append(dict(request=req.isoformat(), receipt=rec.isoformat(),
                              item_ts=[it["timestamp"] for it in j.get("items", [])]))
        except Exception as e:
            errs.append(f"{req.isoformat()}: {type(e).__name__}: {str(e)[:150]}")
        OUT.write_text(json.dumps(dict(first_seen=first_seen, polls=polls, errors=errs, every_s=EVERY_S), indent=1))
        time.sleep(EVERY_S)
    print("NEA probe stamps", len(first_seen), "polls", len(polls), "errors", len(errs))


if __name__ == "__main__":
    main()
