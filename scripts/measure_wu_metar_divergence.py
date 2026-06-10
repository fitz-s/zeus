# Created: 2026-06-10
# Lifecycle: created=2026-06-10; last_reviewed=2026-06-10; last_reused=2026-06-10
# Purpose: Offline measurement: same-station timestamp-matched WU-vs-METAR divergence -> config/wu_metar_divergence.json (anomaly thresholds + faithfulness).
# Reuse: Re-run to extend city coverage or refresh thresholds (IEM-cached, paced); read-only (mode=ro URIs), writes config/docs artifacts only.
# Last reused or audited: 2026-06-10
# Authority basis: operator correction 2026-06-10 (the 1.5F/1.0C anomaly-guard
#   threshold was A GUESS — measure the actual WU-vs-METAR divergence per
#   station and set per-city empirical thresholds). WU side: zeus-world.db
#   observation_instants source='wu_icao_history' (WU's own record of the
#   station obs, settlement unit, whole degrees). METAR side: IEM ASOS archive
#   (mesonet.agron.iastate.edu, free, full METAR history per ICAO).
"""Measure same-station, timestamp-matched WU vs METAR temperature divergence.

Method
------
- WU pairs: each wu_icao_history hour-bucket row carries the raw METAR
  timestamp(s) where its hour max/min were observed (provenance_json
  hour_max_raw_ts / hour_min_raw_ts) and the bucket extremes in the city's
  settlement unit (whole degrees — WU's settlement convention).
- METAR pairs: IEM ASOS archive (report_type 3=routine, 4=special), tmpf for
  F-settled cities / tmpc for C-settled, matched at the exact valid time
  (fallback nearest within +-6 min).
- Deltas per matched pair:
    raw_delta     = wu_value - metar_value          (METAR may carry tenths)
    rounded_delta = wu_value - round_half_up(metar) (settlement-aligned: WU
                    values are already whole-degree; rounding METAR the same
                    way isolates true feed divergence from quantization)
- Per city: n pairs, median|raw|, p95/p99/max of |raw| and |rounded|, and the
  disagreement rate |rounded_delta| >= 1.0 unit.
- Threshold (operator formula): max(p99(|rounded|) + 1 quantum, floor) with
  quantum = floor = 1.0 settlement unit. Tight where feeds agree (sharp
  tamper detection), wider where legitimate spread exists.
- settlement_faithful verdict: a city whose feeds systematically diverge
  (p99(|rounded|) > 1.0 OR disagree rate > 2%) is marked NOT settlement-
  faithful — the fast lane must not drive bin-kill decisions there.

IEM politeness: one request per station for the whole window, >=8 s spacing,
retry-once on rate limit, on-disk response cache (rerun-safe).

Usage:
  PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python scripts/measure_wu_metar_divergence.py \
      [--days 30] [--cities "NYC,Chicago,..."] [--out config/wu_metar_divergence.json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
CACHE_DIR = Path("/tmp/iem_metar_cache")

#: Default measurement set: the 15 top-traded cities from the latency table
#: + SF/Seattle (operator-named F-cities) + the remaining majors.
DEFAULT_CITIES = (
    "NYC,Chicago,Miami,Dallas,Denver,Atlanta,Los Angeles,Houston,Austin,"
    "San Francisco,Seattle,London,Paris,Amsterdam,Milan,Munich,Madrid,"
    "Seoul,Tokyo,Singapore,Taipei,Toronto"
)

MATCH_TOLERANCE_S = 360.0  # nearest-report fallback window (+-6 min)
QUANTUM = 1.0              # settlement quantum (1 degree, both units)
FLOOR = 1.0                # threshold floor (1 settlement unit)
FAITHFUL_P99_MAX = 1.0     # p99(|rounded|) above this -> not settlement-faithful
FAITHFUL_RATE_MAX = 0.02   # disagree rate (>=1 unit) above this -> not faithful


def round_half_up(value: float) -> float:
    """WMO half-up — WU's settlement rounding convention (floor(x + 0.5))."""
    import math

    return float(math.floor(float(value) + 0.5))


def _parse_ts(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def wu_pairs_for_city(world_db: Path, city: str, since_iso: str) -> list[tuple[datetime, float]]:
    """(raw METAR timestamp, WU value in settlement unit) pairs from WU's record."""
    conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT running_max, running_min, provenance_json
            FROM observation_instants
            WHERE source = 'wu_icao_history' AND city = ? AND utc_timestamp >= ?
            """,
            (city, since_iso),
        ).fetchall()
    finally:
        conn.close()
    pairs: dict[datetime, float] = {}
    for row in rows:
        try:
            prov = json.loads(row["provenance_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for ts_key, value in (("hour_max_raw_ts", row["running_max"]), ("hour_min_raw_ts", row["running_min"])):
            ts = _parse_ts(prov.get(ts_key))
            if ts is None or value is None:
                continue
            # same ts twice (single-report bucket) carries the same value; a
            # genuine collision with different values is dropped (ambiguous).
            if ts in pairs and pairs[ts] != float(value):
                pairs.pop(ts, None)
                continue
            pairs[ts] = float(value)
    return sorted(pairs.items())


def _iem_station_candidates(icao: str) -> list[str]:
    icao = icao.strip().upper()
    out = [icao]
    if len(icao) == 4 and icao.startswith("K"):
        out.insert(0, icao[1:])  # US stations are 3-letter on IEM
    return out


def fetch_iem_series(
    icao: str,
    *,
    unit: str,
    start: datetime,
    end: datetime,
    pause_s: float = 8.0,
) -> list[tuple[datetime, float]]:
    """One cached IEM request per station for the window. [] on failure.

    ALWAYS fetches tmpc and converts to the settlement unit at full precision
    in-script. (First measurement pass used IEM's tmpf directly and produced a
    FALSE 3.3% Denver divergence: IEM displays tmpf rounded to 2dp — KBKF
    T0147 = 14.7C = 58.46F shown as "58.50" — and half-up of the display
    artifact flipped the settlement integer. Converting from tmpc reproduces
    the fast lane's own C->F path, so this measurement doubles as validation
    of that conversion.)
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    var = "tmpc"
    cache = CACHE_DIR / f"{icao}_{var}_{start.date()}_{end.date()}.csv"
    text = ""
    if cache.exists() and cache.stat().st_size > 100:
        text = cache.read_text(encoding="utf-8")
    else:
        for station in _iem_station_candidates(icao):
            params = [
                ("station", station), ("data", var),
                ("year1", start.year), ("month1", start.month), ("day1", start.day),
                ("year2", end.year), ("month2", end.month), ("day2", end.day),
                ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
                ("missing", "M"), ("report_type", 3), ("report_type", 4),
            ]
            for attempt in range(3):
                try:
                    time.sleep(pause_s)
                    resp = httpx.get(IEM_URL, params=params, timeout=120.0)
                    body = resp.text
                    if resp.status_code == 200 and body.startswith("station"):
                        if body.count("\n") > 5:  # has data rows
                            text = body
                        break
                    if "Too many requests" in body:
                        print(f"  IEM rate-limited for {station}, backing off…")
                        time.sleep(30.0 * (attempt + 1))
                        continue
                    break
                except httpx.HTTPError as exc:
                    print(f"  IEM fetch error {station}: {exc}")
                    time.sleep(15.0)
            if text:
                cache.write_text(text, encoding="utf-8")
                break
    out: list[tuple[datetime, float]] = []
    to_f = unit.upper() == "F"
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 3 or parts[2] in ("M", "", "T"):
            continue
        try:
            ts = datetime.strptime(parts[1], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
            value_c = float(parts[2])
            out.append((ts, value_c * 9.0 / 5.0 + 32.0 if to_f else value_c))
        except ValueError:
            continue
    out.sort()
    return out


def match_pairs(
    wu: list[tuple[datetime, float]],
    metar: list[tuple[datetime, float]],
) -> list[tuple[datetime, float, float]]:
    """(ts, wu_value, metar_value) — exact valid-time match, else nearest <=6 min."""
    metar_by_ts = dict(metar)
    metar_ts_sorted = [t for t, _ in metar]
    import bisect

    out: list[tuple[datetime, float, float]] = []
    for ts, wu_value in wu:
        m = metar_by_ts.get(ts)
        if m is None and metar_ts_sorted:
            i = bisect.bisect_left(metar_ts_sorted, ts)
            best = None
            for j in (i - 1, i):
                if 0 <= j < len(metar_ts_sorted):
                    cand = metar_ts_sorted[j]
                    dt = abs((cand - ts).total_seconds())
                    if dt <= MATCH_TOLERANCE_S and (best is None or dt < best[0]):
                        best = (dt, cand)
            if best is not None:
                m = metar_by_ts[best[1]]
        if m is not None:
            out.append((ts, wu_value, m))
    return out


def city_stats(matched: list[tuple[datetime, float, float]]) -> dict:
    raw = [wu - mv for _, wu, mv in matched]
    rounded = [wu - round_half_up(mv) for _, wu, mv in matched]
    abs_raw = sorted(abs(d) for d in raw)
    abs_rounded = sorted(abs(d) for d in rounded)

    def pct(sorted_values, q):
        if not sorted_values:
            return None
        return round(sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))], 3)

    n = len(matched)
    disagree = sum(1 for d in abs_rounded if d >= 1.0)
    p99_rounded = pct(abs_rounded, 0.99) if n else None
    rate = round(disagree / n, 5) if n else None
    threshold = max((p99_rounded or 0.0) + QUANTUM, FLOOR) if n else None
    faithful = (
        bool(p99_rounded is not None and p99_rounded <= FAITHFUL_P99_MAX and rate is not None and rate <= FAITHFUL_RATE_MAX)
        if n
        else None
    )
    return {
        "matched_pairs": n,
        "median_abs_raw_delta": round(statistics.median(abs_raw), 3) if n else None,
        "p95_abs_raw_delta": pct(abs_raw, 0.95),
        "p99_abs_raw_delta": pct(abs_raw, 0.99),
        "max_abs_raw_delta": round(abs_raw[-1], 3) if n else None,
        "p95_abs_rounded_delta": pct(abs_rounded, 0.95),
        "p99_abs_rounded_delta": p99_rounded,
        "max_abs_rounded_delta": round(abs_rounded[-1], 3) if n else None,
        "disagree_rate_ge_1unit": rate,
        "empirical_threshold": round(threshold, 2) if threshold is not None else None,
        "threshold_provenance": "empirical" if n >= 100 else ("thin_sample" if n else "no_data"),
        "settlement_faithful": faithful,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--cities", default=DEFAULT_CITIES)
    ap.add_argument("--world-db", default=str(REPO_ROOT / "state" / "zeus-world.db"))
    ap.add_argument("--out", default=str(REPO_ROOT / "config" / "wu_metar_divergence.json"))
    ap.add_argument("--md", default=str(
        REPO_ROOT / "docs" / "evidence" / "2026_06_10_day0_first_principles" / "wu_metar_divergence_table.md"
    ))
    args = ap.parse_args()

    from src.config import load_cities

    wanted = [c.strip() for c in args.cities.split(",") if c.strip()]
    cities = {c.name: c for c in load_cities() if c.name in wanted and c.settlement_source_type == "wu_icao"}
    end = datetime.now(UTC)
    start = end - timedelta(days=int(args.days))
    since_iso = start.isoformat()

    results: dict[str, dict] = {}
    for name in wanted:
        city = cities.get(name)
        if city is None:
            print(f"{name}: skipped (not a wu_icao city)")
            continue
        wu = wu_pairs_for_city(Path(args.world_db), name, since_iso)
        if not wu:
            print(f"{name}: no WU pairs in window")
            continue
        metar = fetch_iem_series(
            str(city.wu_station), unit=str(city.settlement_unit), start=start, end=end
        )
        matched = match_pairs(wu, metar)
        stats = city_stats(matched)
        stats.update({
            "station_id": str(city.wu_station),
            "unit": str(city.settlement_unit),
            "wu_pairs": len(wu),
            "metar_reports": len(metar),
        })
        results[name] = stats
        print(
            f"{name:15s} pairs={stats['matched_pairs']:5d} med|raw|={stats['median_abs_raw_delta']} "
            f"p99|round|={stats['p99_abs_rounded_delta']} rate>=1={stats['disagree_rate_ge_1unit']} "
            f"thr={stats['empirical_threshold']} faithful={stats['settlement_faithful']}"
        )

    artifact = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": int(args.days),
        "window": [start.isoformat(), end.isoformat()],
        "method": "same-station timestamp-matched WU(wu_icao_history bucket extremes @ raw METAR ts)"
                  " vs IEM ASOS archive METAR (report_type 3/4); rounded_delta after WMO half-up on METAR",
        "threshold_formula": f"max(p99(|rounded_delta|) + {QUANTUM}, {FLOOR}) per settlement unit",
        "defaults": {"F": 1.5, "C": 1.0, "provenance": "default_guess_pre_measurement"},
        "cities": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# WU vs METAR divergence (same station, timestamp-matched)",
        "",
        f"Window: {start.date()} → {end.date()} ({args.days} days). Generated {artifact['generated_at']}.",
        "",
        "| city | station | unit | pairs | med abs raw delta | p99 abs raw | p99 abs rounded | max abs rounded | rate >=1 unit | empirical threshold | faithful |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in results.items():
        lines.append(
            f"| {name} | {s['station_id']} | {s['unit']} | {s['matched_pairs']} | {s['median_abs_raw_delta']} "
            f"| {s['p99_abs_raw_delta']} | {s['p99_abs_rounded_delta']} | {s['max_abs_rounded_delta']} "
            f"| {s['disagree_rate_ge_1unit']} | {s['empirical_threshold']} | {s['settlement_faithful']} |"
        )
    md = Path(args.md)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} and {md}")


if __name__ == "__main__":
    main()
