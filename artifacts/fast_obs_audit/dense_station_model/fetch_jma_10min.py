"""Tokyo RJTT dense history: JMA past-weather 10-minute table for AMeDAS Haneda (prec_no 44, block 0371 = AMeDAS 44166,
the live jma_amedas_temperature provider station). One page per JST day, 2.5 s between requests, no key.

Page: https://www.data.jma.go.jp/stats/etrn/view/10min_a1.php?prec_no=44&block_no=0371&year=Y&month=M&day=D&view=
Rows are labelled 00:10 .. 24:00 JST (end of each 10-min step); column 3 is air temperature (deg C).
Cells carrying ')' / ']' (quasi-normal / reference) are kept; '///', '#', '×' and blanks are treated as missing.
Output: raw/jma_haneda_10min.csv.gz  (ts_utc, value)
"""
import csv
import gzip
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
UA = {"User-Agent": "zeus-obs-research (read-only audit)"}
URL = "https://www.data.jma.go.jp/stats/etrn/view/10min_a1.php?prec_no=44&block_no=0371&year={y}&month={m}&day={d}&view="
JST = timezone(timedelta(hours=9))
FIRST, LAST = date(2026, 3, 9), date(2026, 10, 6)


def page(d):
    last = None
    for a in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL.format(y=d.year, m=d.month, d=d.day), headers=UA), timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(10 * (a + 1))
    raise last


def main():
    rows, errs, flagged = {}, [], 0
    d = FIRST
    while d <= LAST:
        try:
            t = page(d)
            assert "羽田" in t, "station name not in page"
            for r in re.findall(r'<tr class="mtx"[^>]*>(.*?)</tr>', t, flags=re.S):
                tds = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)]
                if len(tds) < 3 or not re.fullmatch(r"\d{2}:\d{2}", tds[0]):
                    continue
                hh, mm = map(int, tds[0].split(":"))
                cell = tds[2]
                num = re.sub(r"[^0-9.\-]", "", cell)
                if not num or num in ("-", ".") or "/" in cell or "#" in cell or "×" in cell:
                    continue
                flagged += bool(re.search(r"[)\]]", cell))
                ts = (datetime(d.year, d.month, d.day, tzinfo=JST) + timedelta(hours=hh, minutes=mm)).astimezone(timezone.utc)
                rows[ts] = float(num)
        except Exception as e:
            errs.append(f"{d}: {type(e).__name__}: {str(e)[:150]}")
        d += timedelta(days=1)
        time.sleep(2.5)
    RAW.mkdir(exist_ok=True)
    with gzip.open(RAW / "jma_haneda_10min.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(("ts_utc", "value"))
        for k in sorted(rows):
            w.writerow((k.isoformat(), rows[k]))
    print("JMA 10-min rows", len(rows), "flagged", flagged, "errors", len(errs), errs[:10], flush=True)


if __name__ == "__main__":
    main()
