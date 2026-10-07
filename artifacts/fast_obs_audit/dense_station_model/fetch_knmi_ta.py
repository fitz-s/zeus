"""Amsterdam EHAM dense series: KNMI 10-minute in-situ files, station 06240 (Schiphol), variables ta (1-min mean at the
stamp), tx / tn (max / min over the 10-min interval ending at the stamp).

Only the stamps that bracket the METAR instants are fetched: :20 and :30 (around :25), :50 and :00 (around :55).
Hard cap: 790 API /url calls (operator cap 800). Most recent full Amsterdam local days first.
Run under the netCDF4 venv:
  /private/tmp/claude-501/-Users-leofitz-zeus/58464645-9a59-4320-acce-dec6b037962b/scratchpad/knmi/venv/bin/python fetch_knmi_ta.py
The API key is read by knmi_client from the macOS keychain, kept in memory, never printed or written.
Output: raw/knmi_06240_bracket.csv.gz (ts_utc, ta, tx, tn)
"""
import csv
import gzip
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "daily_extreme_agreement"))
import knmi_client as K  # noqa: E402
from netCDF4 import Dataset  # noqa: E402

DS, VER, STN = "10-minute-in-situ-meteorological-observations", "1.0", "06240"
CAP = 790
UTC = timezone.utc
ATTEMPTS = [0]
_orig_open = K._open


def _counting_open(url, auth, timeout):
    if auth:  # every authenticated call (incl. 429/5xx retries) counts against the operator cap
        ATTEMPTS[0] += 1
    return _orig_open(url, auth, timeout)


K._open = _counting_open


def read(stamp):
    fn = "KMDS__OPER_P___10M_OBS_L2_" + stamp.strftime("%Y%m%d%H%M") + ".nc"
    body = K.download(DS, VER, fn)
    with Dataset("x", memory=body) as d:
        ids = [str(s) for s in d.variables["station"][:].tolist()]
        i = ids.index(STN)
        out = []
        for v in ("ta", "tx", "tn"):
            x = d.variables[v][i, 0]
            out.append(None if getattr(x, "mask", False) is True or str(x) == "--" else round(float(x), 2))
        return out


def main():
    # Amsterdam local days 2026-09-29 .. 2026-10-06 (CEST, UTC+2): [d-1 22:00Z, d 22:00Z]
    end = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
    start = end - timedelta(days=8)
    stamps = []
    t = end
    while t >= start:  # newest first so a cap cut keeps the most recent days
        if t.minute in (0, 20, 30, 50):
            stamps.append(t)
        t -= timedelta(minutes=10)
    rows, errs = {}, []
    for s in stamps:
        if ATTEMPTS[0] >= CAP - 3:  # a download may retry up to 3 times
            errs.append(f"cap {CAP} reached at {s.isoformat()}")
            break
        try:
            rows[s] = read(s)
        except Exception as e:
            errs.append(f"{s.isoformat()}: {type(e).__name__}: {str(e)[:120]}")
        if len(rows) % 100 == 0:
            print(f"[knmi] files={len(rows)} attempts={ATTEMPTS[0]} remaining={K.STATE['remaining']}", flush=True)
    with gzip.open(HERE / "raw" / "knmi_06240_bracket.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(("ts_utc", "ta", "tx", "tn"))
        for k in sorted(rows):
            w.writerow((k.isoformat(), *rows[k]))
    print("KNMI files", len(rows), "api attempts", ATTEMPTS[0], "ok calls", K.STATE["calls"], "limit", K.STATE["limit"], "errors", len(errs), errs[:10], flush=True)


if __name__ == "__main__":
    main()
