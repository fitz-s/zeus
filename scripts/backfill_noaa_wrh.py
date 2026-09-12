#!/usr/bin/env python3
# Created: 2026-09-12
# Last audited: 2026-09-12
# Lifecycle: created=2026-09-12; last_reviewed=2026-09-12; last_reused=never
# Purpose: Backfill the weather.gov/wrh/timeseries settlement product into observations.
# Reuse: Read src/data/noaa_wrh_timeseries.py's measured request facts and the
#   packet at docs/operations/current/noaa_settlement_page_truth/ before changing
#   window sizes, pacing, or the view law.
# Authority basis: docs/operations/current/noaa_settlement_page_truth/{PLAN.md,evidence.md}
"""Backfill daily high/low from the page every NOAA market resolves off.

Writes ``observations`` rows with source ``noaa_wrh_<station>`` through the same
atom-pair sink the live daily tick uses, so a backfilled day and a live day are
the same row shape. Dry-run is the default and prints, per city and date, the
existing Ogimet-derived values against the page's values and whether the change
moves the rounded value out of the bin the chain already settled — which is the
only number that decides whether a rebuild changes a label.

Request discipline (see src/data/noaa_wrh_timeseries.py for the measurements):
one request per station per chunk of at most ``--chunk-days`` (default and cap 7)
days, at least two seconds apart. A refused token (HTTP 403) stops the run and
prints how far it got so an operator can resume with a later ``--start`` rather
than hammering a quota that is already refusing.

Usage
-----
    python3 scripts/backfill_noaa_wrh.py --start 2026-08-23 --end 2026-09-11
    python3 scripts/backfill_noaa_wrh.py --start 2026-09-09 --end 2026-09-11 \
        --city NYC --city Houston --db /tmp/copy.db --apply

``--fixture-dir`` reads saved ``syn_<STID>.json`` response bodies instead of
fetching, for offline replay and tests.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name, settlement_source_type_for_city  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data.daily_obs_append import (  # noqa: E402
    NOAA_WRH_DATA_SOURCE_VERSION,
    _build_atom_pair,
    _write_atom_with_coverage,
    noaa_wrh_source_tag,
)
from src.data.noaa_wrh_timeseries import (  # noqa: E402
    MAX_REQUEST_WINDOW_DAYS,
    WrhError,
    WrhRow,
    WrhTokenRefused,
    daily_extreme,
    fetch_wrh_timeseries,
    fetch_wrh_token,
    request_url_without_token,
    rows_from_payload,
    token_fetched_at,
)
from src.data.ingestion_guard import IngestionRejected  # noqa: E402
from src.state.db import ZEUS_WORLD_DB_PATH, get_world_connection, init_schema  # noqa: E402
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

SCRIPT_ID = "scripts/backfill_noaa_wrh.py"


def _noaa_cities() -> dict[str, Any]:
    return {
        name: city
        for name, city in cities_by_name.items()
        if city.settlement_source_type == "noaa"
    }


def _chunks(
    start: date, end: date, chunk_days: int
) -> list[tuple[date, date]]:
    """Split [start, end] into windows of at most chunk_days, widened by a day.

    The extra day on each side covers the local-vs-UTC edge: the caller filters
    rows by the local date the feed itself reports, so the window only has to
    contain every candidate row, not align with a calendar boundary.
    """
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        windows.append((cursor - timedelta(days=1), chunk_end + timedelta(days=1)))
        cursor = chunk_end + timedelta(days=1)
    return windows


def _fetch_window(
    station: str,
    window: tuple[date, date],
    *,
    unit: str,
    token: Optional[str],
    fixture_dir: Optional[Path],
) -> list[WrhRow]:
    if fixture_dir is not None:
        path = fixture_dir / f"syn_{station.upper()}.json"
        if not path.exists():
            raise WrhError(f"fixture {path} not found")
        return rows_from_payload(json.loads(path.read_text()), station)
    assert token is not None
    start_utc = datetime(
        window[0].year, window[0].month, window[0].day, tzinfo=timezone.utc,
    )
    end_utc = datetime(
        window[1].year, window[1].month, window[1].day, 23, 59, tzinfo=timezone.utc,
    )
    return fetch_wrh_timeseries(
        station, start_utc, end_utc, unit=unit, token=token,
    )


def _existing_rows(
    conn: sqlite3.Connection, city_name: str, target_date: str
) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """SELECT source, high_temp, low_temp, unit, authority
             FROM observations
            WHERE city = ? AND target_date = ?""",
        (city_name, target_date),
    ).fetchall()
    return {str(r["source"]): r for r in rows}


def _settled_bins(
    conn: sqlite3.Connection, city_name: str, target_date: str
) -> dict[str, sqlite3.Row]:
    try:
        rows = conn.execute(
            """SELECT temperature_metric, pm_bin_lo, pm_bin_hi, settlement_value,
                      authority
                 FROM settlements
                WHERE city = ? AND target_date = ?""",
            (city_name, target_date),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(r["temperature_metric"]): r for r in rows}


def _bin_contains(value: float, lo: Any, hi: Any) -> Optional[bool]:
    if lo is None and hi is None:
        return None
    if lo is not None and value < float(lo):
        return False
    if hi is not None and value > float(hi):
        return False
    return True


def _containment_note(
    rounded_new: float,
    rounded_old: Optional[float],
    settled: Optional[sqlite3.Row],
) -> str:
    if settled is None:
        return "no settled row"
    lo, hi = settled["pm_bin_lo"], settled["pm_bin_hi"]
    new_in = _bin_contains(rounded_new, lo, hi)
    if new_in is None:
        return "settled row carries no bin"
    old_in = (
        None if rounded_old is None else _bin_contains(rounded_old, lo, hi)
    )
    bin_text = f"bin[{lo},{hi}]"
    if old_in is None:
        return f"{bin_text} new={'in' if new_in else 'OUT'}"
    if old_in == new_in:
        return f"{bin_text} unchanged ({'in' if new_in else 'OUT'})"
    return f"{bin_text} {'OUT->in FIXES' if new_in else 'in->OUT REGRESSES'}"


def backfill(
    conn: sqlite3.Connection,
    *,
    start: date,
    end: date,
    city_filter: list[str] | None = None,
    apply_writes: bool = False,
    chunk_days: int = MAX_REQUEST_WINDOW_DAYS,
    fixture_dir: Optional[Path] = None,
    rebuild_run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch and (optionally) write the page product for a date range."""
    if end < start:
        raise ValueError("end < start")
    if chunk_days < 1 or chunk_days > MAX_REQUEST_WINDOW_DAYS:
        raise ValueError(
            f"chunk_days must be in 1..{MAX_REQUEST_WINDOW_DAYS}"
        )

    cities = _noaa_cities()
    if city_filter:
        unknown = [name for name in city_filter if name not in cities]
        if unknown:
            raise ValueError(f"not NOAA-settled cities: {unknown}")
        cities = {name: cities[name] for name in city_filter}
    if rebuild_run_id is None:
        rebuild_run_id = (
            "noaa_wrh_backfill_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )

    token = None if fixture_dir is not None else fetch_wrh_token()
    conn.row_factory = sqlite3.Row

    summary: dict[str, Any] = {
        "apply": apply_writes,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cities": len(cities),
        "days_seen": 0,
        "days_written": 0,
        "days_no_rows": 0,
        "days_changed_value": 0,
        "days_changed_containment": 0,
        "refused_at": None,
        "lines": [],
    }

    for city_name, city in sorted(cities.items()):
        station = str(city.wu_station).strip().upper()
        view = city.settlement_page_view
        unit = city.settlement_unit
        tz = ZoneInfo(city.timezone)
        semantics = SettlementSemantics.for_city(city)
        rows: list[WrhRow] = []
        for window in _chunks(start, end, chunk_days):
            try:
                rows.extend(
                    _fetch_window(
                        station, window,
                        unit=unit, token=token, fixture_dir=fixture_dir,
                    )
                )
            except WrhTokenRefused as exc:
                summary["refused_at"] = f"{city_name} {window[0]}..{window[1]}: {exc}"
                summary["lines"].append(f"REFUSED {summary['refused_at']}")
                return summary
            except WrhError as exc:
                summary["lines"].append(
                    f"FETCH_FAILED {city_name} {window[0]}..{window[1]}: {exc}"
                )

        by_timestamp = {row.local_timestamp: row for row in rows}
        rows = sorted(by_timestamp.values(), key=lambda row: row.local_timestamp)

        cursor = start
        while cursor <= end:
            target_date = cursor
            cursor += timedelta(days=1)
            if settlement_source_type_for_city(city, target_date) != "noaa":
                continue
            summary["days_seen"] += 1
            iso = target_date.isoformat()
            high = daily_extreme(
                rows, target_date_local=target_date, view=view, metric="high",
            )
            low = daily_extreme(
                rows, target_date_local=target_date, view=view, metric="low",
            )
            if high is None or low is None:
                summary["days_no_rows"] += 1
                summary["lines"].append(
                    f"{city_name} {iso}: no {view}-view rows (station dark) "
                    "-> nothing written, market stays DISPUTED"
                )
                continue

            existing = _existing_rows(conn, city_name, iso)
            settled = _settled_bins(conn, city_name, iso)
            old = existing.get(f"ogimet_metar_{station.lower()}")
            new_high = semantics.assert_settlement_value(
                high.value, context=f"{SCRIPT_ID}/{city_name}/{iso}/high",
            )
            new_low = semantics.assert_settlement_value(
                low.value, context=f"{SCRIPT_ID}/{city_name}/{iso}/low",
            )
            old_high = (
                semantics.round_single(float(old["high_temp"]))
                if old is not None and old["high_temp"] is not None
                else None
            )
            old_low = (
                semantics.round_single(float(old["low_temp"]))
                if old is not None and old["low_temp"] is not None
                else None
            )
            if old_high != new_high or old_low != new_low:
                summary["days_changed_value"] += 1
            high_note = _containment_note(new_high, old_high, settled.get("high"))
            low_note = _containment_note(new_low, old_low, settled.get("low"))
            if "FIXES" in high_note or "REGRESSES" in high_note:
                summary["days_changed_containment"] += 1
            if "FIXES" in low_note or "REGRESSES" in low_note:
                summary["days_changed_containment"] += 1
            summary["lines"].append(
                f"{city_name} {iso} view={view} n={high.n_rows}/{high.n_official}: "
                f"HIGH ogimet={_fmt(old_high)} page={new_high:.0f} "
                f"(raw {high.value:.2f} @ {high.local_timestamp}) {high_note} | "
                f"LOW ogimet={_fmt(old_low)} page={new_low:.0f} "
                f"(raw {low.value:.2f} @ {low.local_timestamp}) {low_note}"
            )

            if not apply_writes:
                continue

            fetch_utc = datetime.now(timezone.utc)
            token_at = token_fetched_at()
            request_url = (
                f"fixture://{fixture_dir}/syn_{station}.json"
                if fixture_dir is not None
                else request_url_without_token(
                    station,
                    unit=unit,
                    start_utc=datetime.combine(
                        start, datetime.min.time(), tzinfo=timezone.utc,
                    ),
                    end_utc=datetime.combine(
                        end, datetime.max.time(), tzinfo=timezone.utc,
                    ),
                )
            )
            provenance = {
                "station": station,
                "upstream": "weather.gov_wrh_timeseries",
                "settlement_page_view": view,
                "n_rows": high.n_rows,
                "n_official": high.n_official,
                "high_raw_metar": high.raw_metar,
                "low_raw_metar": low.raw_metar,
                "high_local_timestamp": high.local_timestamp,
                "low_local_timestamp": low.local_timestamp,
                "token_fetched_at": token_at.isoformat() if token_at else None,
                "request_url": request_url,
                "backfill_script": SCRIPT_ID,
            }
            try:
                atom_high, atom_low = _build_atom_pair(
                    city_name=city_name,
                    target_d=target_date,
                    high_val=high.value,
                    low_val=low.value,
                    raw_unit=unit,
                    target_unit=unit,
                    station_id=station,
                    source=noaa_wrh_source_tag(station),
                    rebuild_run_id=rebuild_run_id,
                    data_source_version=NOAA_WRH_DATA_SOURCE_VERSION,
                    api_endpoint=request_url,
                    provenance=provenance,
                    fetch_utc=fetch_utc,
                    high_local_time=high.local_timestamp,
                    low_local_time=low.local_timestamp,
                )
            except IngestionRejected as exc:
                summary["lines"].append(f"GUARD_REJECTED {city_name} {iso}: {exc}")
                continue
            _write_atom_with_coverage(
                conn, atom_high, atom_low, data_source=noaa_wrh_source_tag(station),
            )
            conn.commit()
            summary["days_written"] += 1

    return summary


def _fmt(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.0f}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", required=True, help="First target date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last target date YYYY-MM-DD")
    parser.add_argument(
        "--city", action="append", dest="cities", default=None,
        help="Restrict to this city; repeatable",
    )
    parser.add_argument("--db", default=None, help="DB path override")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write observations rows; without it the run only reports",
    )
    parser.add_argument(
        "--chunk-days", type=int, default=MAX_REQUEST_WINDOW_DAYS,
        help=f"Days per request, 1..{MAX_REQUEST_WINDOW_DAYS}",
    )
    parser.add_argument(
        "--fixture-dir", default=None,
        help="Read saved syn_<STID>.json bodies instead of fetching",
    )
    args = parser.parse_args(argv)

    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as exc:
        print(f"ERROR parsing dates: {exc}", file=sys.stderr)
        return 2
    if end < start:
        print("ERROR: --end < --start", file=sys.stderr)
        return 2

    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else None
    lock_path = Path(args.db) if args.db else ZEUS_WORLD_DB_PATH
    with db_writer_lock(lock_path, WriteClass.BULK):
        if args.db:
            conn = sqlite3.connect(args.db)
            conn.row_factory = sqlite3.Row
        else:
            conn = get_world_connection(write_class="bulk")
        try:
            init_schema(conn)
            print(f"=== noaa_wrh backfill {start}..{end} apply={args.apply} ===")
            try:
                summary = backfill(
                    conn,
                    start=start,
                    end=end,
                    city_filter=args.cities,
                    apply_writes=args.apply,
                    chunk_days=args.chunk_days,
                    fixture_dir=fixture_dir,
                )
            except (ValueError, WrhError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            for line in summary.pop("lines"):
                print(line)
            print(json.dumps(summary, indent=2, sort_keys=True))
            if summary["refused_at"]:
                print(
                    "Synoptic refused the request; resume with a later --start "
                    "once the per-IP quota window has passed.",
                    file=sys.stderr,
                )
                return 1
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
