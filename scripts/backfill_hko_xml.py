#!/usr/bin/env python3
# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: architecture/preflight_overrides_2026-04-28.yaml (hko_canonical
#   release block) + docs/archives/packets/task_2026-05-02_hk_paris_release/work_log.md
"""Backfill Hong Kong daily high+low from the HKO `dailyExtract` XML endpoint.

The original `scripts/backfill_hko_daily.py` uses the HKO `opendata.php`
CLMMAXT/CLMMINT API, which only publishes the prior month's archive once
HKO's monthly publication cycle finishes — historically ~2 months after
month-end. The `dailyExtract_YYYYMM.xml` endpoint at
`https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_YYYYMM.xml` is the
same source the HKO website renders today's table from, so it carries
the current month's data with only ~1 day publication lag instead of
~30+ days.

Despite the `.xml` extension the response body is JSON. Format
(verified 2026-05-02 against `dailyExtract_202604.xml`):

    {"stn": {"data": [{"month": 4,
        "dayData": [
            ["01", "1012.3", "27.3", "24.5", "22.4", "19.8", "76", "85", "  0.6"],
            ...
        ]}]}}

Each `dayData` row is positional: `[day, pressure_hPa, temp_max_C,
temp_mean_C, temp_min_C, dewpoint_C, RH_percent_AM, RH_percent_PM,
rainfall_mm]`. We use **column 0** for day, **column 2** for daily
high temp, **column 4** for daily low temp. Column meaning was
cross-validated against existing March 2026 rows from CLMMAXT/CLMMINT
(target_date 2026-03-01..05 in DB matches XML columns [2] and [4] to
0.1°C — see work_log.md).

The end-of-month "Mean/Total" and "Normal" rows are skipped because
their "day" field is non-numeric.

K1-C contract: writes go through the same `_build_atom_pair` +
`write_daily_observation_with_revision` path as `backfill_hko_daily.py`,
with these provenance distinctions:

* `source` = `hko_daily_api` (matches existing HKO rows so calibration
  + scanner logic doesn't need a special case).
* `data_source_version` = `hko_xml_v1_2026` (vs `hko_opendata_v1_2026`
  for the opendata.php path) so we can trace which endpoint produced
  any given row in audits.
* Provenance metadata records the actual endpoint URL + payload hash.
* Days where daily high < daily low are rejected as
  IngestionRejected (HKO publishes Trace strings for rainfall in same
  shape, so type errors are also logged).

Usage:
    python scripts/backfill_hko_xml.py --start 2026-04 --end 2026-04
    python scripts/backfill_hko_xml.py --start 2026-04 --end 2026-04 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from datetime import date, datetime, timedelta
from datetime import timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.calibration.manager import season_from_date
from src.config import cities_by_name
from src.data.daily_observation_writer import (
    INSERTED,
    write_daily_observation_with_revision,
)
from src.data.ingestion_guard import (
    IngestionGuard,
    IngestionRejected,
)
from src.state.db import get_world_connection, init_schema
from src.types.observation_atom import ObservationAtom

logger = logging.getLogger(__name__)

HKO_XML_URL_TEMPLATE = (
    "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{year:04d}{month:02d}.xml"
)
HKO_STATION = "HKO"
HKO_DAILY_PARSER_VERSION = "hko_xml_backfill_v1"
SLEEP_BETWEEN_REQUESTS = 0.5
FETCH_RETRY_COUNT = 2
FETCH_RETRY_BACKOFF_SEC = 3.0

_CITY_NAME = "Hong Kong"
_GUARD = IngestionGuard()


def _hemisphere_for_lat(lat: float) -> str:
    return "N" if lat >= 0 else "S"


def _sha256_payload(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Fetch + parse layer
# ---------------------------------------------------------------------------


def _fetch_hko_xml_month(
    year: int,
    month: int,
) -> tuple[dict[tuple[int, int, int], tuple[float, float]], str, str]:
    """Fetch one month's `dailyExtract_YYYYMM.xml` payload and parse it.

    Returns
    -------
    (rows, source_url, payload_hash)
        rows: dict keyed by (year, month, day) -> (high_C, low_C)
        source_url: full request URL for provenance
        payload_hash: 'sha256:<hex>' of raw response bytes
    """
    url = HKO_XML_URL_TEMPLATE.format(year=year, month=month)
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    payload_hash = _sha256_payload(resp.content)
    body = resp.json()  # 'XML' file is actually JSON

    out: dict[tuple[int, int, int], tuple[float, float]] = {}
    stn = body.get("stn") or {}
    data_blocks = stn.get("data") or []
    for block in data_blocks:
        block_month = block.get("month")
        if block_month is None or int(block_month) != month:
            # Defensive: dailyExtract files always have one month block
            # but verify before trusting positional indexing.
            continue
        day_rows = block.get("dayData") or []
        for row in day_rows:
            if not row or len(row) < 5:
                continue
            day_token = str(row[0]).strip()
            if not day_token.isdigit():
                # 'Mean/Total' and 'Normal' summary rows fall here.
                continue
            try:
                day = int(day_token)
            except ValueError:
                continue
            try:
                # Column 2 = daily max temperature in °C
                high = float(row[2])
                # Column 4 = daily min temperature in °C
                low = float(row[4])
            except (ValueError, TypeError, IndexError) as e:
                logger.warning(
                    "HKO XML %d/%d day %s: parse failed (row=%r): %s",
                    year, month, day_token, row, e,
                )
                continue
            out[(year, month, day)] = (high, low)
    return out, url, payload_hash


def _fetch_hko_xml_with_retry(
    year: int,
    month: int,
) -> tuple[dict[tuple[int, int, int], tuple[float, float]], str, str, str | None]:
    """Wrapper with bounded retries on transient HTTP errors."""
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            rows, url, payload_hash = _fetch_hko_xml_month(year, month)
            return rows, url, payload_hash, None
        except httpx.HTTPError as e:
            if attempt < FETCH_RETRY_COUNT:
                wait = FETCH_RETRY_BACKOFF_SEC * (attempt + 1)
                logger.warning(
                    "HKO XML retry %d/%d %d/%d after %.1fs: %s",
                    attempt + 1, FETCH_RETRY_COUNT, year, month, wait, e,
                )
                time.sleep(wait)
                continue
            return {}, "", "", f"http error after {FETCH_RETRY_COUNT + 1} tries: {e}"
        except Exception as e:
            return {}, "", "", f"unexpected error: {type(e).__name__}: {e}"
    return {}, "", "", "exhausted retries"


# ---------------------------------------------------------------------------
# Provenance + write helpers
# ---------------------------------------------------------------------------


def _build_xml_provenance(
    *,
    target_date: date,
    source_url: str,
    payload_hash: str,
) -> dict[str, object]:
    return {
        "source": "hko_daily_api",
        "endpoint_family": "hko_dailyextract_xml",
        "station": HKO_STATION,
        "station_id": HKO_STATION,
        "dataType": ["dailyExtract"],
        "payload_hash": payload_hash,
        "source_url": source_url,
        "parser_version": HKO_DAILY_PARSER_VERSION,
        "target_date": target_date.isoformat(),
    }


def _write_atom(conn, atom: ObservationAtom, atom_low: ObservationAtom) -> str:
    """Same write contract as backfill_hko_daily._write_atom_to_observations."""
    if atom_low is None:
        raise ValueError("HKO XML backfill requires both high and low atoms")
    assert atom.value_type == "high", f"expected high atom, got {atom.value_type!r}"
    assert atom_low.value_type == "low", f"expected low atom, got {atom_low.value_type!r}"
    assert atom.city == atom_low.city
    assert atom.target_date == atom_low.target_date
    assert atom.source == atom_low.source
    assert atom.target_unit == atom_low.target_unit
    return write_daily_observation_with_revision(
        conn,
        atom,
        atom_low,
        writer="scripts.backfill_hko_xml._write_atom",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


_STAT_KEYS = (
    "months_fetched",
    "days_published",
    "days_inserted",
    "days_skipped_out_of_range",
    "guard_rejected",
    "fetch_errors",
    "insert_errors",
)


def _new_stats() -> dict[str, int]:
    return {k: 0 for k in _STAT_KEYS}


def _iter_months(start: date, end: date):
    """Yield (year, month) tuples covering [start, end] inclusive."""
    current = date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    while current <= stop:
        yield current.year, current.month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def run_backfill(
    start: date,
    end: date,
    *,
    conn,
    rebuild_run_id: str,
    dry_run: bool = False,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
) -> dict:
    city_cfg = cities_by_name.get(_CITY_NAME)
    if city_cfg is None:
        raise RuntimeError(f"{_CITY_NAME} not in cities.json — cannot backfill HKO")
    if city_cfg.settlement_unit != "C":
        raise RuntimeError(
            f"{_CITY_NAME} settlement_unit is {city_cfg.settlement_unit!r}, "
            f"expected 'C' for HKO (HKO publishes in Celsius only)"
        )

    tz = ZoneInfo(city_cfg.timezone)
    hemisphere = _hemisphere_for_lat(city_cfg.lat)
    peak_hour_raw = city_cfg.historical_peak_hour
    _peak_h = int(peak_hour_raw)
    _peak_m = int((peak_hour_raw - _peak_h) * 60)

    stats = _new_stats()

    for year, month in _iter_months(start, end):
        stats["months_fetched"] += 1
        print(f"\n[{_CITY_NAME}] {year}-{month:02d} (XML)")

        rows_map, source_url, payload_hash, err = _fetch_hko_xml_with_retry(year, month)
        if err:
            stats["fetch_errors"] += 1
            logger.error("HKO XML fetch failed %d/%d: %s", year, month, err)
            time.sleep(sleep_seconds)
            continue

        stats["days_published"] += len(rows_map)
        month_inserted = 0
        month_guard_rej = 0

        for ymd in sorted(rows_map.keys()):
            high_val, low_val = rows_map[ymd]
            y, m, d = ymd
            target_d = date(y, m, d)
            if target_d < start or target_d > end:
                stats["days_skipped_out_of_range"] += 1
                continue

            target_str = target_d.isoformat()
            fetch_utc = datetime.now(_tz.utc)

            local_time = datetime(
                target_d.year, target_d.month, target_d.day,
                _peak_h, _peak_m, tzinfo=tz,
            )
            from src.signal.diurnal import _is_missing_local_hour as _is_missing
            is_missing_local = _is_missing(local_time, tz)
            is_ambiguous = bool(getattr(local_time, "fold", 0))
            dst_offset = local_time.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
            utc_offset = local_time.utcoffset()
            utc_offset_min = (
                int(utc_offset.total_seconds() // 60) if utc_offset is not None else 0
            )

            window_start_local = datetime(
                target_d.year, target_d.month, target_d.day, 0, 0, tzinfo=tz,
            )
            window_end_local = datetime(
                target_d.year, target_d.month, target_d.day, 23, 59, 59, tzinfo=tz,
            )
            window_start_utc = window_start_local.astimezone(_tz.utc)
            window_end_utc = window_end_local.astimezone(_tz.utc)

            season = season_from_date(target_str, lat=city_cfg.lat)

            try:
                _GUARD.check_unit_consistency(
                    city=_CITY_NAME, raw_value=high_val, raw_unit="C",
                    declared_unit="C", target_date=target_d,
                )
                _GUARD.check_unit_consistency(
                    city=_CITY_NAME, raw_value=low_val, raw_unit="C",
                    declared_unit="C", target_date=target_d,
                )
                if low_val > high_val:
                    raise IngestionRejected(
                        f"{_CITY_NAME}/{target_str}: HKO XML low={low_val}°C > "
                        f"high={high_val}°C (internal HKO dataset inconsistency)"
                    )
                _GUARD.check_collection_timing(
                    city=_CITY_NAME, fetch_utc=fetch_utc, target_date=target_d,
                    peak_hour=peak_hour_raw,
                )
                _GUARD.check_dst_boundary(city=_CITY_NAME, local_time=local_time)
            except IngestionRejected as e:
                stats["guard_rejected"] += 1
                month_guard_rej += 1
                logger.warning("Guard rejected HK/%s: %s", target_str, e)
                continue

            _atom_common = dict(
                city=_CITY_NAME,
                target_date=target_d,
                target_unit="C",
                raw_unit="C",
                source="hko_daily_api",
                station_id=HKO_STATION,
                api_endpoint=source_url,
                fetch_utc=fetch_utc,
                local_time=local_time,
                collection_window_start_utc=window_start_utc,
                collection_window_end_utc=window_end_utc,
                timezone=city_cfg.timezone,
                utc_offset_minutes=utc_offset_min,
                dst_active=dst_active,
                is_ambiguous_local_hour=is_ambiguous,
                is_missing_local_hour=is_missing_local,
                hemisphere=hemisphere,
                season=season,
                month=target_d.month,
                rebuild_run_id=rebuild_run_id,
                data_source_version="hko_xml_v1_2026",
                authority="VERIFIED",
                validation_pass=True,
                provenance_metadata=_build_xml_provenance(
                    target_date=target_d,
                    source_url=source_url,
                    payload_hash=payload_hash,
                ),
            )
            atom_high = ObservationAtom(
                value_type="high", value=high_val, raw_value=high_val, **_atom_common,
            )
            atom_low = ObservationAtom(
                value_type="low", value=low_val, raw_value=low_val, **_atom_common,
            )

            if not dry_run:
                try:
                    outcome = _write_atom(conn, atom_high, atom_low)
                    if outcome == INSERTED:
                        stats["days_inserted"] += 1
                        month_inserted += 1
                except Exception as e:
                    stats["insert_errors"] += 1
                    logger.error("Insert failed HK/%s: %s", target_str, e)
            else:
                stats["days_inserted"] += 1
                month_inserted += 1

        if not dry_run:
            conn.commit()

        print(
            f"  published={len(rows_map)} inserted={month_inserted} "
            f"guard_rej={month_guard_rej}"
        )
        time.sleep(sleep_seconds)

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", required=True, help="Start month YYYY-MM")
    parser.add_argument("--end", required=True, help="End month YYYY-MM")
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS)
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and validate but no DB writes")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    start_parts = args.start.split("-")
    start = date(int(start_parts[0]), int(start_parts[1]), 1)

    end_parts = args.end.split("-")
    end_year, end_month = int(end_parts[0]), int(end_parts[1])
    if end_month == 12:
        end = date(end_year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(end_year, end_month + 1, 1) - timedelta(days=1)

    rebuild_run_id = (
        f"backfill_hko_xml_"
        f"{datetime.now(_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    if args.dry_run:
        print("[DRY RUN] No rows will be written.")
    print("=== HKO Daily Backfill via dailyExtract XML ===")
    print(f"Run ID:  {rebuild_run_id}")
    print(f"Range:   {start.isoformat()} → {end.isoformat()}")
    print(f"Source:  {HKO_XML_URL_TEMPLATE.format(year=start.year, month=start.month)} (template)")
    print(f"Station: {HKO_STATION}")

    conn = get_world_connection(write_class="bulk")
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)

    stats = run_backfill(
        start=start,
        end=end,
        conn=conn,
        rebuild_run_id=rebuild_run_id,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
    )

    conn.close()

    print("\n=== Summary ===")
    for k in _STAT_KEYS:
        print(f"  {k:30s} {stats[k]}")

    rc = 0
    if stats["fetch_errors"] or stats["insert_errors"]:
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
