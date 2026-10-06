"""Shared helpers for the daily-extreme agreement audit (read-only measurement).

Truth     : FORECASTS.settlement_outcomes, authority='VERIFIED' and settlement_value not null.
Rounding  : src.contracts.settlement_semantics.round_wmo_half_up_value (floor(v + 0.5);
            asymmetric on the number line, so -0.5 -> 0, -1.5 -> -1).
Local day : config/cities.json `timezone`; expected readings per day are counted on the
            real local-day length, so 23h/25h DST days are handled.
"""
from __future__ import annotations

import csv
import gzip
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

try:
    from src.contracts.settlement_semantics import round_wmo_half_up_value  # noqa: E402
    ROUNDING_IMPL = "src.contracts.settlement_semantics.round_wmo_half_up_value"
except ModuleNotFoundError:  # netCDF venv has no scipy (src.types.temperature imports it)
    import math

    def round_wmo_half_up_value(value: float, precision: float = 1.0) -> float:
        """Formula-identical copy of src round_wmo_half_up_values for precision=1.0; parity is
        asserted against the real function by check_rounding_parity.py in the main interpreter."""
        return float(math.floor(float(value) / precision + 0.5) * precision)
    ROUNDING_IMPL = "inline copy of floor(v+0.5) (scipy-less venv); parity-tested"

LIVE_STATE = Path("/Users/leofitz/zeus/state")
FORECASTS_URI = f"file:{LIVE_STATE}/zeus-forecasts.db?mode=ro"
WORLD_URI = f"file:{LIVE_STATE}/zeus-world.db?mode=ro"
RAW = HERE / "raw"
OUT = HERE / "per_candidate"
UA = {"User-Agent": "zeus-obs-research (read-only audit)"}
UTC = timezone.utc
COVERAGE_MIN = 0.80
LAST_SETTLED = "2026-10-05"


def contract(v: float) -> int:
    return int(round_wmo_half_up_value(float(v)))


def city_tz() -> dict[str, str]:
    cj = json.load(open(REPO / "config" / "cities.json"))
    cj = cj["cities"] if isinstance(cj, dict) else cj
    return {c["name"]: c["timezone"] for c in cj}


def settled(city: str) -> dict[str, dict[str, int]]:
    """{'high': {date: value}, 'low': {...}} from VERIFIED settlements."""
    con = sqlite3.connect(FORECASTS_URI, uri=True, timeout=10)
    con.execute("pragma query_only=1")
    out: dict[str, dict[str, int]] = {"high": {}, "low": {}}
    for d, m, v in con.execute(
        "select target_date, temperature_metric, settlement_value from settlement_outcomes "
        "where city=? and authority='VERIFIED' and settlement_value is not null", (city,)):
        out[m][d] = contract(v)
    con.close()
    return out


def first_settled(city: str) -> str:
    s = settled(city)
    return min(list(s["high"]) + list(s["low"]))


def local_day_bounds(day: str, tzname: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tzname)
    d = date.fromisoformat(day)
    a = datetime.combine(d, time(0), tzinfo=tz).astimezone(UTC)
    b = datetime.combine(d + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    return a, b


def write_raw(name: str, rows: list[tuple[str, float]], header=("ts_utc", "value")) -> Path:
    RAW.mkdir(exist_ok=True)
    p = RAW / f"{name}.csv.gz"
    with gzip.open(p, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


def read_raw(name: str) -> list[tuple[datetime, float]]:
    p = RAW / f"{name}.csv.gz"
    out = []
    with gzip.open(p, "rt") as f:
        r = csv.reader(f)
        next(r)
        for ts, v in r:
            out.append((datetime.fromisoformat(ts), float(v)))
    return out


def day_extremes(readings, tzname: str, cadence_s: int, first_day: str, last_day: str = LAST_SETTLED):
    """Group (utc_datetime, value) by local day.

    Returns {day: dict(n, expected, coverage, max_raw, min_raw, max_contract, min_contract)}.
    Duplicated instants are collapsed (last wins).
    """
    tz = ZoneInfo(tzname)
    by_day: dict[str, dict[datetime, float]] = {}
    for ts, v in readings:
        by_day.setdefault(ts.astimezone(tz).date().isoformat(), {})[ts] = v
    out = {}
    d = date.fromisoformat(first_day)
    end = date.fromisoformat(last_day)
    while d <= end:
        k = d.isoformat()
        a, b = local_day_bounds(k, tzname)
        expected = int((b - a).total_seconds() // cadence_s)
        vals = list(by_day.get(k, {}).values())
        if vals:
            out[k] = dict(n=len(vals), expected=expected, coverage=round(len(vals) / expected, 4),
                          max_raw=max(vals), min_raw=min(vals),
                          max_contract=contract(max(vals)), min_contract=contract(min(vals)))
        else:
            out[k] = dict(n=0, expected=expected, coverage=0.0, max_raw=None, min_raw=None,
                          max_contract=None, min_contract=None)
        d += timedelta(days=1)
    return out


def compare(days: dict[str, dict], truth: dict[str, dict[str, int]], metric: str):
    """Per-day rows + aggregate for one metric. days: day -> extremes dict."""
    rows, hist = [], Counter()
    n = dropped_cov = eq = worse = other = 0
    no_truth = 0
    for day, s in sorted(truth[metric].items()):
        e = days.get(day)
        if e is None or e["n"] == 0 or e["coverage"] < COVERAGE_MIN:
            dropped_cov += 1
            rows.append(dict(date=day, settled=s, cand_raw=None if e is None else e[f"{metric_key(metric)}_raw"],
                             cand_contract=None, n=0 if e is None else e["n"],
                             expected=None if e is None else e["expected"],
                             coverage=0.0 if e is None else e["coverage"], used=False, note="dropped_coverage"))
            continue
        c = e[f"{metric_key(metric)}_contract"]
        diff = c - s
        n += 1
        hist[diff] += 1
        eq += diff == 0
        # dangerous side: high -> candidate above settled (false floor); low -> candidate below (false ceiling)
        bad = diff > 0 if metric == "high" else diff < 0
        worse += bad
        other += (diff < 0) if metric == "high" else (diff > 0)
        rows.append(dict(date=day, settled=s, cand_raw=e[f"{metric_key(metric)}_raw"], cand_contract=c, diff=diff,
                         n=e["n"], expected=e["expected"], coverage=e["coverage"], used=True,
                         dangerous=bool(bad), note=e.get("note")))
    agg = dict(metric=metric, n_days=n, dropped_coverage=dropped_cov, eq=eq,
               dangerous=worse, dangerous_name="over" if metric == "high" else "under",
               opposite=other, opposite_name="under" if metric == "high" else "over",
               diff_hist={str(k): hist[k] for k in sorted(hist)})
    return rows, agg


def metric_key(metric: str) -> str:
    return "max" if metric == "high" else "min"


def save_candidate(name: str, meta: dict, results: dict) -> Path:
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.json"
    json.dump(dict(meta=meta, **results), open(p, "w"), indent=1, sort_keys=True, default=str)
    return p


def compute_and_save(name: str, *, city: str, tzname: str, cadence_s: int, readings, meta: dict,
                     first_day: str | None = None, extra: dict | None = None,
                     only_days: set[str] | None = None) -> dict:
    """readings: iterable of (utc datetime, raw value). Writes per_candidate/<name>.json.

    only_days: settled days the source can expose at all (e.g. a 48 h API window). Days outside it are
    'not exposed', not 'dropped for coverage', so they are removed from the truth set instead of counted."""
    truth = settled(city)
    if only_days is not None:
        truth = {m: {d: v for d, v in t.items() if d in only_days} for m, t in truth.items()}
    first_day = first_day or first_settled(city)
    days = day_extremes(list(readings), tzname, cadence_s, first_day)
    res = {"daily": days}
    for metric in ("high", "low"):
        rows, agg = compare(days, truth, metric)
        res[metric] = dict(agg=agg, rows=rows)
        print(f"{name} {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
              f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
    if extra:
        res.update(extra)
    meta = dict(meta, city=city, tz=tzname, rounding="floor(v+0.5) via round_wmo_half_up_value",
                coverage_min=COVERAGE_MIN, cadence_s=cadence_s, first_day=first_day, last_day=LAST_SETTLED)
    save_candidate(name, meta, res)
    return res
