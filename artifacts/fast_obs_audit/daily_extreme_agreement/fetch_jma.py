"""Tokyo RJTT: JMA past-weather-data pages (www.data.jma.go.jp/stats/etrn), AMeDAS station Haneda (prec_no 44, block 0371,
AMeDAS id 44166 = the registry provider_station). Daily table: highest/lowest air temperature per JST day (JST has no DST,
so the JMA day IS the settlement calendar day). No key; one request per month with a 3 s sleep.

Cell flags in the page: a trailing ')' marks a value computed from a partly missing day ("quasi-normal"), ']' marks a
reference value; both are kept but counted in meta (n_flagged). '///' means missing -> day dropped for coverage.
"""
import re
import time
import urllib.request
from datetime import date

from dae_common import LAST_SETTLED, UA, compare, contract, first_settled, save_candidate, settled

CITY = "Tokyo"
URL = "https://www.data.jma.go.jp/stats/etrn/view/daily_a1.php?prec_no=44&block_no=0371&year={y}&month={m}&day=1&view="


def month(y, m):
    last = None
    for a in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL.format(y=y, m=m), headers=UA), timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(10 * (a + 1))
    raise last


def cell(c):
    c = re.sub(r"<[^>]+>", "", c).strip()
    flag = bool(re.search(r"[)\]]", c))
    num = re.sub(r"[^0-9.\-]", "", c)
    return (float(num) if num not in ("", "-", ".") and "///" not in c else None), flag


def main():
    first = first_settled(CITY)
    y, m = int(first[:4]), int(first[5:7])
    days, errs, flagged, nreq = {}, [], 0, 0
    while (y, m) <= (2026, 10):
        try:
            t = month(y, m)
            nreq += 1
            assert "羽田" in t, "station name 羽田 not in page"
            for r in re.findall(r'<tr class="mtx"[^>]*>(.*?)</tr>', t, flags=re.S):
                tds = re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)
                if len(tds) < 7 or not re.fullmatch(r"\d+", re.sub(r"<[^>]+>", "", tds[0]).strip()):
                    continue
                d = date(y, m, int(re.sub(r"<[^>]+>", "", tds[0]).strip())).isoformat()
                (mx, fx), (mn, fn) = cell(tds[5]), cell(tds[6])
                ok = mx is not None and mn is not None
                flagged += (fx or fn) and ok
                days[d] = dict(n=1 if ok else 0, expected=1, coverage=1.0 if ok else 0.0, max_raw=mx, min_raw=mn,
                               max_contract=contract(mx) if mx is not None else None,
                               min_contract=contract(mn) if mn is not None else None, flagged=bool(fx or fn))
        except Exception as e:
            errs.append(f"{y}-{m:02d}: {type(e).__name__}: {str(e)[:150]}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
        time.sleep(3)
    truth = settled(CITY)
    res = {"daily": days}
    for metric in ("high", "low"):
        rows, agg = compare(days, truth, metric)
        res[metric] = dict(agg=agg, rows=rows)
        print(f"jma_haneda {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
    print("requests", nreq, "flagged days", flagged, "errors", errs)
    save_candidate("jma_haneda_daily", dict(
        provider="JMA etrn daily table (Haneda AMeDAS 44166, block 0371)", station="44166 Haneda", city=CITY, tz="Asia/Tokyo",
        requests=nreq, errors=errs, flagged_days=int(flagged), first_day=first, last_day=LAST_SETTLED, rounding="floor(v+0.5)",
        caveat="JMA's published daily extreme at 0.1 C; the live jma_amedas adapter reads 10-minute spot points only",
        source_channel="jma_amedas_temperature (daily product, different from live 10-min points)"), res)


if __name__ == "__main__":
    main()
