"""Amsterdam EHAM: KNMI 10-minute file publication lag from ONE listing call (lastModified of the newest 60 files).

Publication lag = lastModified - file stamp. This is the provider-side floor on any live KNMI channel's latency; there
is no live WORLD channel for KNMI, so Zeus's own polling overhead is not included. Uses knmi_client (keychain key,
memory only). Output: raw/knmi_publication_lag.json
"""
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "daily_extreme_agreement"))
import knmi_client as K  # noqa: E402

BASE = f"{K.ROOT}/10-minute-in-situ-meteorological-observations/versions/1.0/files"


def main():
    q = urllib.parse.urlencode({"maxKeys": 60, "sorting": "desc", "orderBy": "filename"})
    probe_at = datetime.now(timezone.utc)
    with K._open(f"{BASE}?{q}", True, 60) as r:
        body = json.loads(r.read())
    rows = []
    for f in body.get("files", []):
        name = f["filename"]
        stamp = datetime.strptime(name.rsplit("_", 1)[1][:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        lm = datetime.fromisoformat(f["lastModified"].replace("Z", "+00:00"))
        rows.append(dict(file=name, stamp=stamp.isoformat(), last_modified=lm.isoformat(),
                         lag_min=round((lm - stamp).total_seconds() / 60, 2)))
    lags = sorted(r["lag_min"] for r in rows)
    out = dict(probe_at=probe_at.isoformat(), n=len(rows), api_calls=1,
               lag_min_p10=lags[int(0.1 * (len(lags) - 1))], lag_min_p50=lags[len(lags) // 2],
               lag_min_p90=lags[int(0.9 * (len(lags) - 1))], files=rows)
    (HERE / "raw" / "knmi_publication_lag.json").write_text(json.dumps(out, indent=1))
    print({k: v for k, v in out.items() if k != "files"})


if __name__ == "__main__":
    main()
