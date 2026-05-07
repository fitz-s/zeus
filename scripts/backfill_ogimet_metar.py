#!/usr/bin/env python3
# Lifecycle: created=2026-04-14; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Backfill Ogimet daily observations with provenance and completeness guardrails.
# Reuse: Run topology and current source/data checks before changing Ogimet source, write, or guardrail semantics.
"""Backfill daily high/low temperatures from Ogimet METAR/SYNOP archive.

Why this exists (2026-04-14)
----------------------------
Zeus has three cities whose Polymarket settlement source is NOT Weather
Underground: Istanbul (NOAA LTFM), Moscow (NOAA UUWW), and Taipei (Taiwan
CWA station 46692). NOAA's public `weather.gov/wrh/timeseries` endpoint is
server-rendered HTML; CWA's open data API requires an authorization token.
Ogimet is a free, unauthenticated public mirror of the same global METAR
(for ICAO airports) and SYNOP (for WMO synoptic stations) streams that
feed NOAA and CWA. For LTFM and UUWW the underlying METAR report is
byte-identical to what NOAA serves; for Taipei 46692 the SYNOP report is
byte-identical to what CWA archives. Using Ogimet is a provenance-chain
shortcut, not a source change — the physical observation is the same.

Limitations
-----------
- METAR reports come at ~30-minute cadence; daily high is the max across
  all reports in the local day. This is how NOAA and Polymarket would
  compute it too, so we match their number exactly.
- SYNOP reports come at 3-hour cadence (00/03/06/09/12/15/18/21 UTC).
  This is Taipei's only free option — the 3-hour gap can under-estimate
  the true peak by a small amount if the peak fell between reports, but
  this is inherent to SYNOP-as-source and matches what CWA station
  46692 actually reports internally (CWA's real-time observations at
  that station are also ~3-hourly SYNOP-derived).

Usage
-----
    python scripts/backfill_ogimet_metar.py --cities Istanbul Moscow Taipei \
        --start 2024-01-01 --end 2026-04-14

    # Dry-run (no DB writes, prints per-day summary):
    python scripts/backfill_ogimet_metar.py --cities Istanbul --start 2024-01-01 \
        --end 2024-01-31 --dry-run

Contract
--------
- source column written as `ogimet_metar` (Istanbul/Moscow) or
  `ogimet_synop` (Taipei). Downstream code can differentiate.
- authority='VERIFIED' is written because Ogimet is a trusted mirror of
  the same upstream the settlement source uses; a cross-unit sanity check
  rejects any per-day daily-high outside plausible climatology.
- Chunks of up to 30 days per HTTP request (Ogimet soft limit).
- Respects city.timezone for local-day grouping (DST-aware via zoneinfo).
- INSERT OR REPLACE keyed on (city, target_date, source) uniqueness.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import City, load_cities  # noqa: E402
from src.state.db import get_world_connection, init_schema  # noqa: E402
from scripts.backfill_completeness import (  # noqa: E402
    add_completeness_args,
    emit_manifest_footer,
    evaluate_completeness,
    resolve_manifest_path,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Source registry — extend here if new non-WU/HKO cities are added
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OgimetTarget:
    city_name: str
    station: str              # ICAO (METAR) or WMO block (SYNOP)
    kind: str                 # "metar" | "synop"
    unit: str                 # always "C" for Ogimet parsed values
    source_tag: str           # value written to observations.source column


# Built from cities.json at runtime (keyed by city_name).
# Declaring here rather than reading cities.json makes the mapping
# explicit and reviewable — cities.json changes still require updating
# this dict, which is intentional.
OGIMET_TARGETS: dict[str, OgimetTarget] = {
    "Istanbul": OgimetTarget(
        city_name="Istanbul",
        station="LTFM",
        kind="metar",
        unit="C",
        source_tag="ogimet_metar_ltfm",
    ),
    "Moscow": OgimetTarget(
        city_name="Moscow",
        station="UUWW",
        kind="metar",
        unit="C",
        source_tag="ogimet_metar_uuww",
    ),
    "Tel Aviv": OgimetTarget(
        city_name="Tel Aviv",
        station="LLBG",
        kind="metar",
        unit="C",
        source_tag="ogimet_metar_llbg",
    ),
}


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

OGIMET_METAR_URL = "https://www.ogimet.com/cgi-bin/getmetar"
OGIMET_SYNOP_URL = "https://www.ogimet.com/cgi-bin/getsynop"
HEADERS = {"User-Agent": "zeus-ogimet-backfill/1.0 (research; contact via repo)"}
CHUNK_DAYS = 30
RETRY_PAUSE_SECONDS = 5.0
MAX_RETRIES = 3
COMPLETENESS_MANIFEST_PREFIX = "backfill_manifest_ogimet_metar"


def _fetch_window(
    target: OgimetTarget,
    begin: datetime,
    end: datetime,
) -> str:
    """One HTTP request for a <=30-day window. Returns raw CSV body."""
    params = {
        "icao" if target.kind == "metar" else "block": target.station,
        "begin": begin.strftime("%Y%m%d%H%M"),
        "end": end.strftime("%Y%m%d%H%M"),
    }
    if target.kind == "synop":
        params["lang"] = "en"
    url = OGIMET_METAR_URL if target.kind == "metar" else OGIMET_SYNOP_URL
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=45)
            if resp.status_code == 200:
                return resp.text
            print(
                f"  WARN {target.station} {begin.date()}-{end.date()}: "
                f"HTTP {resp.status_code}, retry {attempt + 1}/{MAX_RETRIES}"
            )
        except requests.RequestException as e:
            print(
                f"  WARN {target.station} {begin.date()}-{end.date()}: "
                f"{type(e).__name__}: {e}, retry {attempt + 1}/{MAX_RETRIES}"
            )
        time.sleep(RETRY_PAUSE_SECONDS * (attempt + 1))
    print(
        f"  ERROR {target.station} {begin.date()}-{end.date()}: "
        f"all {MAX_RETRIES} retries failed"
    )
    return ""


# ---------------------------------------------------------------------------
# METAR parser
# ---------------------------------------------------------------------------

# METAR temp/dewpoint group: one or two digits (optionally prefixed with M
# for negative) separated by /. Examples: "10/08", "M05/M08", "M10/08".
_METAR_TEMP_RE = re.compile(r"\s(M?\d{1,2})/(M?\d{1,2})\s")


def _parse_metar_temp(metar_body: str) -> Optional[float]:
    """Extract temperature in °C from a raw METAR body, or None if absent."""
    m = _METAR_TEMP_RE.search(" " + metar_body + " ")
    if not m:
        return None
    raw = m.group(1)
    negative = raw.startswith("M")
    try:
        v = int(raw[1:] if negative else raw)
    except ValueError:
        return None
    return float(-v if negative else v)


def _parse_metar_line(line: str) -> Optional[tuple[datetime, float]]:
    """Parse one CSV line `ICAO,YYYY,MM,DD,HH,MI,<METAR body>`. Returns (utc, temp_c)."""
    parts = line.split(",", 6)
    if len(parts) < 7:
        return None
    try:
        year, month, day, hour, minute = map(int, parts[1:6])
        obs_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    temp = _parse_metar_temp(parts[6])
    if temp is None:
        return None
    return obs_utc, temp


# ---------------------------------------------------------------------------
# SYNOP parser (Section 1 group 1sTTT only)
# ---------------------------------------------------------------------------

# SYNOP Section 1 temperature group `1sTTT` is position-sensitive, not
# pattern-free. Naive `1[01]\d{3}` matching falsely captures the
# `iRiXhVV` cloud/visibility group (also 5-char, also commonly starts
# with 1 when ir=1, e.g. `11665`, `12662`). That bug produced physically
# impossible lows like -76.2°C during the 2026-04-14 Taipei smoke test.
#
# Structure of a standard land SYNOP AAXX report:
#   tokens[0] = "AAXX"
#   tokens[1] = YYGGi      (day-hour-windSpeedIndicator)
#   tokens[2] = IIiii      (WMO block+station)
#   tokens[3] = iRiXhVV    (precipInd/weatherInd/cloudBase/visibility)
#   tokens[4] = Nddff      (totalCloud/windDir/windSpeed)
#   tokens[5..] = Section 1 groups in order: 1sTTT, 2sTdTdTd, 3PPPP,
#                 4PPPP, 5appp, 6RRRtr, 7wwW, 8NhCLCMCH, 9GGgg, then
#                 section-indicator tokens (222/333/444/...) delimit
#                 subsequent sections.
#
# Note: if wind speed >= 99 knots, an optional `00fff` group is inserted
# immediately after Nddff, pushing the 1sTTT group to position 6.
#
# Parser strategy: start scanning at position 5 (or 6 if position 5 is
# `00fff`), find the first token that matches `1[01]\d{3}` exactly,
# stopping if a section marker is hit first.
_TEMP_GROUP_RE = re.compile(r"^1([01])(\d{3})$")
_HIGH_WIND_RE = re.compile(r"^00\d{3}$")
_SECTION_MARKERS = frozenset({
    "222", "333", "444", "555", "666", "777", "888", "999",
})


def _parse_synop_temp(report_body: str) -> Optional[float]:
    """Extract Section 1 air temperature in °C from a SYNOP report."""
    body = report_body.rstrip("=").strip()
    tokens = body.split()
    if len(tokens) < 6 or tokens[0] != "AAXX":
        return None
    # Skip header (AAXX YYGGi IIiii iRiXhVV Nddff) = first 5 tokens.
    start_idx = 5
    # Optional `00fff` high-wind group inserted after Nddff.
    if start_idx < len(tokens) and _HIGH_WIND_RE.match(tokens[start_idx]):
        start_idx += 1
    for tok in tokens[start_idx:]:
        if tok in _SECTION_MARKERS:
            return None
        m = _TEMP_GROUP_RE.match(tok)
        if m:
            sign = m.group(1)  # "0" = positive, "1" = negative
            tenths = int(m.group(2))
            temp = tenths / 10.0
            return -temp if sign == "1" else temp
    return None


def _parse_synop_line(line: str) -> Optional[tuple[datetime, float]]:
    parts = line.split(",", 6)
    if len(parts) < 7:
        return None
    try:
        year, month, day, hour, minute = map(int, parts[1:6])
        obs_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    temp = _parse_synop_temp(parts[6])
    if temp is None:
        return None
    return obs_utc, temp


# ---------------------------------------------------------------------------
# Daily-high aggregation
# ---------------------------------------------------------------------------

def _group_by_local_day(
    observations: list[tuple[datetime, float]],
    tz: ZoneInfo,
) -> dict[date, dict]:
    """Bucket observations into local-day -> {temps, reports}."""
    out: dict[date, dict] = {}
    for obs_utc, temp in observations:
        local_date = obs_utc.astimezone(tz).date()
        bucket = out.setdefault(
            local_date,
            {"temps": [], "count": 0, "first_utc": obs_utc, "last_utc": obs_utc},
        )
        bucket["temps"].append(temp)
        bucket["count"] += 1
        if obs_utc < bucket["first_utc"]:
            bucket["first_utc"] = obs_utc
        if obs_utc > bucket["last_utc"]:
            bucket["last_utc"] = obs_utc
    return out


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

INSERT_SQL = """
INSERT OR REPLACE INTO observations (
    city, target_date, source, high_temp, low_temp, unit, station_id, fetched_at,
    raw_value, raw_unit, target_unit, value_type,
    fetch_utc, local_time, collection_window_start_utc, collection_window_end_utc,
    timezone, utc_offset_minutes, dst_active,
    is_ambiguous_local_hour, is_missing_local_hour,
    hemisphere, season, month,
    rebuild_run_id, data_source_version,
    authority, provenance_metadata
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?
)
"""


def _season_from_date(d: date, lat: float) -> str:
    """Meteorological season (northern or southern hemisphere)."""
    m = d.month
    if lat >= 0:
        # Northern hemisphere: DJF / MAM / JJA / SON
        if m in (12, 1, 2):
            return "DJF"
        if m in (3, 4, 5):
            return "MAM"
        if m in (6, 7, 8):
            return "JJA"
        return "SON"
    else:
        # Southern hemisphere: offset by 6 months
        if m in (6, 7, 8):
            return "DJF"
        if m in (9, 10, 11):
            return "MAM"
        if m in (12, 1, 2):
            return "JJA"
        return "SON"


def _write_day(
    conn,
    city: City,
    target: OgimetTarget,
    local_date: date,
    bucket: dict,
    run_id: str,
) -> None:
    """Insert one daily row computed from all reports for a local day."""
    temps = bucket["temps"]
    high = max(temps)
    low = min(temps)
    tz = ZoneInfo(city.timezone)
    # Synthesize a representative local_time at a generic local-afternoon
    # anchor (14:00). The City dataclass does not expose historical_peak_hour
    # (it lives in cities.json but is not loaded into the dataclass as of
    # 2026-04-14) and downstream code reads target_date rather than the exact
    # local_time, so this constant is low-stakes for the observations row.
    local_peak_naive = datetime(
        local_date.year, local_date.month, local_date.day,
        14, 0, 0,
    )
    local_time = local_peak_naive.replace(tzinfo=tz)
    offset = local_time.utcoffset() or timedelta(0)
    utc_offset_minutes = int(offset.total_seconds() // 60)
    dst_active = bool(local_time.dst() and local_time.dst().total_seconds() > 0)
    fetch_utc = datetime.now(timezone.utc)
    hemisphere = "N" if city.lat >= 0 else "S"
    season = _season_from_date(local_date, city.lat)
    provenance = {
        "station": target.station,
        "kind": target.kind,
        "unit_parsed": target.unit,
        "report_count": bucket["count"],
        "window_first_utc": bucket["first_utc"].isoformat(),
        "window_last_utc": bucket["last_utc"].isoformat(),
        "upstream": "ogimet",
        "note": (
            "Ogimet is a trusted free mirror of the same global METAR/SYNOP "
            "stream that NOAA weather.gov and Taiwan CWA re-distribute."
        ),
    }
    conn.execute(INSERT_SQL, (
        city.name, local_date.isoformat(), target.source_tag,
        high, low, city.settlement_unit,
        target.station, fetch_utc.isoformat(),
        # value_type CHECK constraint accepts 'high' | 'low' | 'mean' only.
        # We anchor the row on the daily high (raw_value column carries it),
        # with low_temp populated as a sibling field — matches the WU
        # backfill convention.
        high, target.unit, city.settlement_unit, "high",
        fetch_utc.isoformat(), local_time.isoformat(),
        bucket["first_utc"].isoformat(), bucket["last_utc"].isoformat(),
        city.timezone, utc_offset_minutes, int(dst_active),
        0, 0,  # is_ambiguous/is_missing local hour — N/A for daily aggregate
        hemisphere, season, local_date.month,
        run_id, f"ogimet_v1_2026_04_14",
        "VERIFIED", json.dumps(provenance),
    ))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _daterange_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _plausible(temp: float, unit: str) -> bool:
    if unit == "C":
        return -60.0 <= temp <= 60.0
    return -80.0 <= temp <= 140.0


def backfill_city(
    conn,
    city: City,
    target: OgimetTarget,
    start: date,
    end: date,
    *,
    dry_run: bool,
    run_id: str,
) -> dict:
    tz = ZoneInfo(city.timezone)
    print(f"\n[{city.name}] {target.station} ({target.kind}) {start} .. {end}")
    observations: list[tuple[datetime, float]] = []
    for chunk_start, chunk_end in _daterange_chunks(start, end, CHUNK_DAYS):
        # Pad the window by 1 day on each side so we capture reports that
        # fall just outside the chunk boundary but belong to our target-date
        # set in the city's local timezone.
        win_start = datetime.combine(
            chunk_start - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        )
        win_end = datetime.combine(
            chunk_end + timedelta(days=1), datetime.max.time().replace(microsecond=0),
            tzinfo=timezone.utc,
        )
        body = _fetch_window(target, win_start, win_end)
        if not body:
            continue
        parser = _parse_metar_line if target.kind == "metar" else _parse_synop_line
        lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
        parsed = 0
        for line in lines:
            result = parser(line)
            if result is not None:
                observations.append(result)
                parsed += 1
        print(
            f"  chunk {chunk_start}..{chunk_end}: "
            f"{len(lines)} lines -> {parsed} parsed"
        )
        # Respect the upstream: small pause between chunks.
        time.sleep(1.0)

    if not observations:
        print(f"  [{city.name}] no parsable observations; skipping")
        requested_days = (end - start).days + 1
        return {
            "city": city.name,
            "days_written": 0,
            "days_skipped": requested_days,
        }

    buckets = _group_by_local_day(observations, tz)
    # Trim to the requested date range.
    buckets = {d: b for d, b in buckets.items() if start <= d <= end}
    days_written = 0
    days_skipped = 0
    for local_date in sorted(buckets):
        bucket = buckets[local_date]
        high = max(bucket["temps"])
        low = min(bucket["temps"])
        if not (_plausible(high, target.unit) and _plausible(low, target.unit)):
            print(
                f"  IMPLAUSIBLE {city.name}/{local_date}: "
                f"high={high} low={low} {target.unit} -- skipped"
            )
            days_skipped += 1
            continue
        if dry_run:
            print(
                f"  [dry-run] {city.name}/{local_date}: "
                f"high={high:.1f} low={low:.1f} {target.unit} "
                f"(reports={bucket['count']})"
            )
        else:
            _write_day(conn, city, target, local_date, bucket, run_id)
        days_written += 1
    missing_days = ((end - start).days + 1) - len(buckets)
    if missing_days > 0:
        days_skipped += missing_days
        print(f"  [{city.name}] missing_days={missing_days} -- counted as skipped")
    if not dry_run:
        conn.commit()
    print(f"  [{city.name}] days_written={days_written} days_skipped={days_skipped}")
    return {"city": city.name, "days_written": days_written, "days_skipped": days_skipped}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=None,
                        help="City names to backfill (default: all registered in OGIMET_TARGETS)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    parser.add_argument("--db", default=None, help="DB path override")
    add_completeness_args(
        parser,
        manifest_prefix=COMPLETENESS_MANIFEST_PREFIX,
    )
    args = parser.parse_args(argv)

    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as e:
        print(f"ERROR parsing dates: {e}", file=sys.stderr)
        return 2
    if end < start:
        print("ERROR: --end < --start", file=sys.stderr)
        return 2

    targets = args.cities or list(OGIMET_TARGETS.keys())
    unknown = [t for t in targets if t not in OGIMET_TARGETS]
    if unknown:
        print(f"ERROR: unknown cities {unknown}. Known: {list(OGIMET_TARGETS)}", file=sys.stderr)
        return 2

    cities_by_name = {c.name: c for c in load_cities()}
    run_id = f"ogimet_backfill_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    conn = get_world_connection(write_class="bulk") if not args.db else None
    if args.db:
        import sqlite3
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
    init_schema(conn)

    try:
        print(f"=== Ogimet backfill run_id={run_id} ===")
        print(f"range: {start} .. {end}  dry_run={args.dry_run}")
        print(f"targets: {targets}")
        summaries = []
        for name in targets:
            city = cities_by_name.get(name)
            if city is None:
                print(f"  WARN {name}: not in cities.json, skipping")
                continue
            target = OGIMET_TARGETS[name]
            summary = backfill_city(
                conn, city, target, start, end,
                dry_run=args.dry_run, run_id=run_id,
            )
            summaries.append(summary)
        print("\n=== Summary ===")
        for s in summaries:
            print(
                f"  {s['city']:12s} written={s['days_written']:4d} "
                f"skipped={s['days_skipped']:4d}"
            )
    finally:
        conn.close()

    total_written = sum(s["days_written"] for s in summaries)
    total_skipped = sum(s["days_skipped"] for s in summaries)
    total_attempted = total_written + total_skipped
    completeness = evaluate_completeness(
        actual_count=total_written,
        failed_count=total_skipped,
        attempted_count=total_attempted,
        expected_count=args.expected_count,
        fail_threshold_percent=args.fail_threshold_percent,
    )
    manifest_path = resolve_manifest_path(
        args.completeness_manifest,
        manifest_prefix=COMPLETENESS_MANIFEST_PREFIX,
        run_id=run_id,
    )
    write_manifest(
        manifest_path,
        script_name="backfill_ogimet_metar.py",
        run_id=run_id,
        dry_run=args.dry_run,
        inputs={
            "cities": targets,
            "start": start,
            "end": end,
            "db": args.db,
            "expected_count": args.expected_count,
            "fail_threshold_percent": args.fail_threshold_percent,
        },
        counters={
            "unit_kind": "target_day",
            "days_written": total_written,
            "days_skipped": total_skipped,
            "attempted": total_attempted,
            "city_count": len(summaries),
            "per_city": summaries,
        },
        completeness=completeness,
    )
    emit_manifest_footer(manifest_path, completeness)
    return int(completeness["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
