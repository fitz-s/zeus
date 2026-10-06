"""Minimal KNMI Open Data API client for the audit. Must run under the netCDF4 venv:
  /private/tmp/claude-501/-Users-leofitz-zeus/58464645-9a59-4320-acce-dec6b037962b/scratchpad/knmi/venv/bin/python

The API key is read from the macOS keychain (service zeus-obs/knmi/api-key) at import time, kept in memory only,
sent only as the Authorization header to api.dataplatform.knmi.nl, and never printed or written.

Rate limit (observed X-Ratelimit-Limit header): 1000 API calls per window. Every /url call decrements the
counter; the presigned download (S3) does not. We read X-Ratelimit-Remaining / -Reset on each call and sleep
to the reset when fewer than SAFETY calls remain.
"""
import json
import subprocess
import time
import urllib.error
import urllib.request

ROOT = "https://api.dataplatform.knmi.nl/open-data/v1/datasets"
SAFETY = 25
_KEY = subprocess.check_output(["security", "find-generic-password", "-w", "-s", "zeus-obs/knmi/api-key"]).decode().strip()
STATE = {"remaining": None, "limit": None, "reset": None, "calls": 0}


def _open(url, auth, timeout):
    h = {"User-Agent": "zeus-obs-research (read-only audit)"}
    if auth:
        h["Authorization"] = _KEY
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def _pace():
    r, reset = STATE["remaining"], STATE["reset"]
    if r is not None and r < SAFETY and reset:
        wait = max(0, reset - time.time()) + 5
        print(f"[knmi] rate window nearly spent (remaining={r}); sleeping {wait:.0f}s", flush=True)
        time.sleep(wait)
        STATE["remaining"] = None


def download(dataset: str, version: str, filename: str, retries: int = 3) -> bytes:
    _pace()
    last = None
    for attempt in range(retries):
        try:
            with _open(f"{ROOT}/{dataset}/versions/{version}/files/{filename}/url", True, 60) as r:
                STATE["calls"] += 1
                STATE["remaining"] = int(r.headers.get("X-Ratelimit-Remaining", "9999"))
                STATE["limit"] = int(r.headers.get("X-Ratelimit-Limit", "0"))
                STATE["reset"] = int(r.headers.get("X-Ratelimit-Reset", "0"))
                url = json.loads(r.read())["temporaryDownloadUrl"]
            with _open(url, False, 120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                reset = int(e.headers.get("X-Ratelimit-Reset", "0") or 0)
                wait = max(30, reset - time.time() + 5)
                print(f"[knmi] 429; sleeping {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            if e.code in (404, 403):
                raise
            time.sleep(5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise last
