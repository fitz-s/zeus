#!/usr/bin/env python3
# Lifecycle: created=2026-04-13; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Backfill WU daily observation rows with provenance and completeness guardrails.
# Reuse: Run topology and current source/data checks before changing source, write, or guardrail semantics.
"""Backfill WU daily high temperatures for all configured cities.

Uses the WU v1/location/{ICAO}:9:{CC}/observations/historical.json endpoint
to fetch actual WU settlement-source daily highs from ICAO airport stations.

This is the REAL settlement data source — same as what WU page shows and
what Polymarket settles on.

Usage:
    cd zeus && .venv/bin/python scripts/backfill_wu_daily_all.py --all --days 90
    cd zeus && .venv/bin/python scripts/backfill_wu_daily_all.py --cities Beijing Toronto --days 30
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.state.db import get_world_connection, init_schema
from src.config import cities_by_name
from src.data.ingestion_guard import IngestionGuard
from src.types.observation_atom import ObservationAtom, IngestionRejected
from src.calibration.manager import hemisphere_for_lat, season_from_date
from scripts.backfill_completeness import (
    add_completeness_args,
    emit_manifest_footer,
    evaluate_completeness,
    resolve_manifest_path,
    write_manifest,
)


def _write_atom_to_observations(
    conn,
    atom: ObservationAtom,
    atom_low: ObservationAtom | None = None,
) -> None:
    """Single authoritative write path for observations. Uses K1 atom schema.

    When `atom_low` is provided, writes BOTH high and low values to the same
    observation row:
      - `atom` must have value_type='high'; its value goes into `high_temp`
      - `atom_low` must have value_type='low'; its value goes into `low_temp`
    Both atoms must share identical city, target_date, source, and target_unit
    (asserted). This is required because day0 decisions consume daily low via
    `src/engine/monitor_refresh.py` and `src/signal/day0_signal.py` — omitting
    low was a K1-C oversight fixed 2026-04-13 in the WU rebuild.

    When `atom_low` is None (legacy single-value path), `low_temp` is NULL.
    """
    if atom_low is not None:
        assert atom.value_type == "high", (
            f"expected high atom, got value_type={atom.value_type!r}"
        )
        assert atom_low.value_type == "low", (
            f"expected low atom, got value_type={atom_low.value_type!r}"
        )
        assert atom.city == atom_low.city, (
            f"city mismatch: {atom.city} vs {atom_low.city}"
        )
        assert atom.target_date == atom_low.target_date, (
            f"target_date mismatch: {atom.target_date} vs {atom_low.target_date}"
        )
        assert atom.source == atom_low.source, (
            f"source mismatch: {atom.source} vs {atom_low.source}"
        )
        assert atom.target_unit == atom_low.target_unit, (
            f"target_unit mismatch: {atom.target_unit} vs {atom_low.target_unit}"
        )
        low_temp_value = atom_low.value
    else:
        low_temp_value = None

    conn.execute("""
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
    """, (
        atom.city, atom.target_date.isoformat(), atom.source,
        atom.value, low_temp_value, atom.target_unit,
        atom.station_id, atom.fetch_utc.isoformat(),
        atom.raw_value, atom.raw_unit, atom.target_unit, atom.value_type,
        atom.fetch_utc.isoformat(), atom.local_time.isoformat(),
        atom.collection_window_start_utc.isoformat(), atom.collection_window_end_utc.isoformat(),
        atom.timezone, atom.utc_offset_minutes, int(atom.dst_active),
        int(atom.is_ambiguous_local_hour), int(atom.is_missing_local_hour),
        atom.hemisphere, atom.season, atom.month,
        atom.rebuild_run_id, atom.data_source_version,
        atom.authority, json.dumps(atom.provenance_metadata),
    ))

logger = logging.getLogger(__name__)

# WU ICAO public web key — see src/data/daily_obs_append.py for the full
# rationale. Same key wunderground.com embeds in its browser UI. Operator
# can override via WU_API_KEY env var to route through a paid account.
_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
WU_API_KEY = os.environ.get("WU_API_KEY") or _WU_PUBLIC_WEB_KEY


def _require_wu_api_key() -> None:
    """Defensive assertion — public fallback guarantees WU_API_KEY is never
    empty, but the check is kept so future refactors that strip the fallback
    surface loudly."""
    assert WU_API_KEY, "WU_API_KEY resolved empty; _WU_PUBLIC_WEB_KEY fallback broken?"
# Default preserves existing behavior; set WU_API_KEY env var to override.
WU_ICAO_HISTORY_URL = "https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json"
WU_DAILY_PARSER_VERSION = "wu_icao_daily_backfill_v2"
COMPLETENESS_MANIFEST_PREFIX = "backfill_manifest_wu_daily_all"

# Module-level guard instance (loads config/city_monthly_bounds.json once)
_GUARD = IngestionGuard()
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Complete mapping: city → (ICAO, country_code, unit)
CITY_STATIONS = {
    # US cities
    "NYC":           ("KLGA", "US", "F"),
    "Chicago":       ("KORD", "US", "F"),
    "Atlanta":       ("KATL", "US", "F"),
    "Austin":        ("KAUS", "US", "F"),
    "Dallas":        ("KDAL", "US", "F"),
    "Denver":        ("KBKF", "US", "F"),
    "Houston":       ("KHOU", "US", "F"),
    "Los Angeles":   ("KLAX", "US", "F"),
    "Miami":         ("KMIA", "US", "F"),
    "San Francisco": ("KSFO", "US", "F"),
    "Seattle":       ("KSEA", "US", "F"),
    # Americas
    "Buenos Aires":  ("SAEZ", "AR", "C"),
    "Mexico City":   ("MMMX", "MX", "C"),
    "Sao Paulo":     ("SBGR", "BR", "C"),
    "Toronto":       ("CYYZ", "CA", "C"),
    # Europe
    "London":        ("EGLC", "GB", "C"),  # 2026-04-14: corrected from EGLL (Heathrow) to EGLC (City Airport) per Polymarket market text
    "Paris":         ("LFPG", "FR", "C"),
    "Munich":        ("EDDM", "DE", "C"),
    "Madrid":        ("LEMD", "ES", "C"),
    "Milan":         ("LIMC", "IT", "C"),
    "Warsaw":        ("EPWA", "PL", "C"),
    "Amsterdam":     ("EHAM", "NL", "C"),  # 2026-04-14: added per Polymarket market text (Schiphol)
    "Helsinki":      ("EFHK", "FI", "C"),  # 2026-04-14: added per Polymarket market text (Vantaa)
    # Moscow is handled by NOAA weather.gov (UUWW/Vnukovo), not WU.
    # Do not re-add under WU — intentionally excluded per settlement_source_type="noaa".
    # "Moscow":        ("UUEE", "RU", "C"),
    # Istanbul is handled by NOAA weather.gov (LTFM), not WU (WU API rejects LTFM).
    # Do not re-add under WU — intentionally excluded per settlement_source_type="noaa".
    # "Istanbul":      ("LTFM", "TR", "C"),
    "Ankara":        ("LTAC", "TR", "C"),
    # Asia
    "Beijing":       ("ZBAA", "CN", "C"),
    "Shanghai":      ("ZSPD", "CN", "C"),
    "Shenzhen":      ("ZGSZ", "CN", "C"),
    "Chengdu":       ("ZUUU", "CN", "C"),
    "Chongqing":     ("ZUCK", "CN", "C"),
    "Wuhan":         ("ZHHH", "CN", "C"),
    "Guangzhou":     ("ZGGG", "CN", "C"),  # 2026-04-14: added per Polymarket market text (Baiyun)
    "Taipei":        ("RCSS", "TW", "C"),
    # Hong Kong is handled by a separate HKO fetcher
    # (scripts/backfill_hko_daily.py). HKO is the authoritative Polymarket
    # settlement source for HK, not WU/VHHH. The VHHH airport (Chek Lap Kok)
    # station differs from HKO Central (Tsim Sha Tsui) by 1-3°C due to the
    # urban heat island, so using WU would systematically mis-match
    # Polymarket HK settlements. See K0 contamination diagnosis #4 (296 rows
    # of hko_daily_extract with F-unit label on °C data from a prior ETL).
    # "Hong Kong":     ("VHHH", "HK", "C"),  # intentionally commented out
    "Tokyo":         ("RJTT", "JP", "C"),
    "Seoul":         ("RKSI", "KR", "C"),
    "Singapore":     ("WSSS", "SG", "C"),
    "Lucknow":       ("VILK", "IN", "C"),
    "Karachi":       ("OPKC", "PK", "C"),  # 2026-04-14: added per Polymarket market text (Jinnah Intl)
    "Manila":        ("RPLL", "PH", "C"),  # 2026-04-14: added per Polymarket market text (Ninoy Aquino Intl)
    # Oceania
    "Wellington":    ("NZWN", "NZ", "C"),
    "Auckland":      ("NZAA", "NZ", "C"),
    # Africa
    "Lagos":         ("DNMM", "NG", "C"),
    "Cape Town":     ("FACT", "ZA", "C"),
    # Middle East
    "Jeddah":        ("OEJN", "SA", "C"),
    # Southeast Asia
    "Kuala Lumpur":  ("WMKK", "MY", "C"),
    "Jakarta":       ("WIHH", "ID", "C"),  # 2026-04-14: corrected from WIII (Soekarno-Hatta) to WIHH (Halim Perdanakusuma) per Polymarket market text
    # Asia-Northeast
    "Busan":         ("RKPK", "KR", "C"),
    # Latin America
    "Panama City":   ("MPMG", "PA", "C"),  # 2026-04-14: corrected from MPTO (Tocumen) to MPMG (Marcos A. Gelabert) per Polymarket market text
}


def _fetch_wu_icao_daily_highs_lows(
    icao: str,
    cc: str,
    start_date: date,
    end_date: date,
    unit: str,
    timezone_name: str,
) -> dict[str, tuple[float, float, dict[str, object]]]:
    """Fetch local-date daily (high, low) from WU ICAO station history.

    Returns dict keyed by ISO date string → (high, low, provenance) tuple in
    the requested unit. Both values are derived from the same hourly observation
    series for the day; dates with a single observation will have high==low.

    Daily low is required by `src/engine/monitor_refresh.py:419` (day0
    temp_persistence window adjustment) and day0 signal generation. Omitting
    low was a K1-C oversight fixed 2026-04-13.
    """
    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
    unit_code = "m" if unit == "C" else "e"
    source_url = _build_wu_source_url(
        icao=icao,
        cc=cc,
        unit_code=unit_code,
        start_date=start_date,
        end_date=end_date,
    )

    try:
        resp = requests.get(
            url,
            params={"apiKey": WU_API_KEY, "units": unit_code,
                    "startDate": start_date.strftime("%Y%m%d"),
                    "endDate": end_date.strftime("%Y%m%d")},
            timeout=30, headers=HEADERS,
        )
        if resp.status_code != 200:
            return {}
        payload_hash = _sha256_payload(resp.content)

        observations = resp.json().get("observations", [])
        if not observations:
            return {}

        tz = ZoneInfo(timezone_name)
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}
        for obs in observations:
            temp = obs.get("temp")
            epoch = obs.get("valid_time_gmt")
            if temp is None or epoch is None:
                continue
            local_date = datetime.fromtimestamp(int(epoch), _tz.utc).astimezone(tz).date()
            if local_date < start_date or local_date > end_date:
                continue
            key = local_date.isoformat()
            t = float(temp)
            highs[key] = max(highs.get(key, float("-inf")), t)
            lows[key] = min(lows.get(key, float("inf")), t)

        result: dict[str, tuple[float, float, dict[str, object]]] = {}
        for key, high in highs.items():
            low = lows[key]
            if high != float("-inf") and low != float("inf"):
                target_date = date.fromisoformat(key)
                result[key] = (
                    high,
                    low,
                    _build_wu_daily_provenance(
                        icao=icao,
                        cc=cc,
                        unit=unit,
                        unit_code=unit_code,
                        start_date=start_date,
                        end_date=end_date,
                        target_date=target_date,
                        payload_hash=payload_hash,
                        source_url=source_url,
                    ),
                )
        return result

    except Exception as e:
        logger.debug("WU ICAO fetch failed %s:%s %s..%s: %s", icao, cc, start_date, end_date, e)
        return {}


def _sha256_payload(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _build_wu_source_url(
    *,
    icao: str,
    cc: str,
    unit_code: str,
    start_date: date,
    end_date: date,
) -> str:
    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
    return (
        f"{url}?units={unit_code}"
        f"&startDate={start_date:%Y%m%d}"
        f"&endDate={end_date:%Y%m%d}"
    )


def _build_wu_daily_provenance(
    *,
    icao: str,
    cc: str,
    unit: str,
    unit_code: str,
    start_date: date,
    end_date: date,
    target_date: date,
    payload_hash: str,
    source_url: str,
) -> dict[str, object]:
    return {
        "source": "wu_icao_history",
        "station_id": icao,
        "country_code": cc,
        "unit": unit,
        "unit_code": unit_code,
        "payload_hash": payload_hash,
        "payload_scope": "chunk",
        "source_url": source_url,
        "api_key_redacted": True,
        "parser_version": WU_DAILY_PARSER_VERSION,
        "request_start_date": start_date.isoformat(),
        "request_end_date": end_date.isoformat(),
        "target_date": target_date.isoformat(),
    }


def _date_chunks(start_date: date, end_date: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks = []
    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def _dates_in_range(start_date: date, end_date: date) -> list[date]:
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _contiguous_date_chunks(dates: list[date], chunk_days: int) -> list[tuple[date, date]]:
    if not dates:
        return []
    chunks = []
    chunk_start = dates[0]
    previous = dates[0]
    chunk_len = 1
    for current in dates[1:]:
        if current == previous + timedelta(days=1) and chunk_len < chunk_days:
            previous = current
            chunk_len += 1
            continue
        chunks.append((chunk_start, previous))
        chunk_start = current
        previous = current
        chunk_len = 1
    chunks.append((chunk_start, previous))
    return chunks


def _dates_needing_fetch(conn, city_name: str, start_date: date, end_date: date) -> list[date]:
    existing_wu = {
        row[0]
        for row in conn.execute(
            """
            SELECT target_date
            FROM observations
            WHERE city = ?
              AND source = 'wu_icao_history'
              AND target_date BETWEEN ? AND ?
            """,
            (city_name, start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    }
    valued_settlements = {
        row[0]
        for row in conn.execute(
            """
            SELECT target_date
            FROM settlements
            WHERE city = ?
              AND settlement_value IS NOT NULL
              AND settlement_value != ''
              AND target_date BETWEEN ? AND ?
            """,
            (city_name, start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    }
    return [
        target
        for target in _dates_in_range(start_date, end_date)
        if target.isoformat() not in existing_wu
        or target.isoformat() not in valued_settlements
    ]


def backfill_city(
    city_name: str,
    days_back: int,
    conn,
    *,
    rebuild_run_id: str | None = None,
    chunk_days: int = 31,
    sleep_seconds: float = 0.5,
    missing_only: bool = False,
    dry_run: bool = False,
) -> dict:
    """Backfill WU daily observations for one city using ICAO station data.

    When dry_run=True, all API fetches and guard validations run as normal but
    no rows are written to the DB. Returns fetched/passed/rejected counts.
    """
    info = CITY_STATIONS.get(city_name)
    if not info:
        print(f"  SKIP: {city_name} not in CITY_STATIONS")
        return {"city": city_name, "collected": 0, "skip": 0, "err": 0, "guard_rejected": 0}

    icao, cc, unit = info
    city_cfg = cities_by_name.get(city_name)
    timezone_name = city_cfg.timezone if city_cfg is not None else "UTC"
    collected = 0
    skip_count = 0
    err_count = 0
    guard_rejected = 0
    request_count = 0
    if rebuild_run_id is None:
        rebuild_run_id = (
            f"backfill_wu_daily_all_"
            f"{datetime.now(_tz.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )

    end_date = date.today() - timedelta(days=2)
    start_date = end_date - timedelta(days=days_back - 1)
    target_dates = (
        _dates_needing_fetch(conn, city_name, start_date, end_date)
        if missing_only
        else _dates_in_range(start_date, end_date)
    )
    target_date_keys = {target.isoformat() for target in target_dates}
    for chunk_start, chunk_end in _contiguous_date_chunks(target_dates, chunk_days):
        request_count += 1
        highs_lows = _fetch_wu_icao_daily_highs_lows(
            icao,
            cc,
            chunk_start,
            chunk_end,
            unit,
            timezone_name,
        )
        if not highs_lows:
            err_count += sum(
                1
                for target in target_dates
                if chunk_start <= target <= chunk_end
            )
            time.sleep(sleep_seconds)
            continue

        current = chunk_start
        while current <= chunk_end:
            target_str = current.isoformat()
            if target_str not in target_date_keys:
                current += timedelta(days=1)
                continue
            pair = highs_lows.get(target_str)
            if pair is None:
                err_count += 1
                current += timedelta(days=1)
                continue
            high, low, provenance = pair

            # Settlement derivation moved to K4 rebuild_settlements.py (Revision 3 plan)

            existing = conn.execute(
                "SELECT high_temp FROM observations WHERE city = ? AND target_date = ? AND source = 'wu_icao_history'",
                (city_name, target_str),
            ).fetchone()

            target_d = date.fromisoformat(target_str)
            tz = ZoneInfo(timezone_name)
            fetch_utc = datetime.now(_tz.utc)

            # Fix 3: local_time is the expected peak time on the target date in local tz,
            # NOT the script runtime converted to local tz. This gives semantically correct
            # provenance for historical atoms.
            from src.signal.diurnal import _is_missing_local_hour as _is_missing
            peak_hour_raw = city_cfg.historical_peak_hour if city_cfg else 15.0
            _peak_h = int(peak_hour_raw)
            _peak_m = int((peak_hour_raw - _peak_h) * 60)
            local_time = datetime(
                target_d.year, target_d.month, target_d.day,
                _peak_h, _peak_m,
                tzinfo=tz,
            )

            # Fix 2: compute is_missing_local_hour from actual local_time rather than hardcoding False
            is_missing_local = _is_missing(local_time, tz)
            is_ambiguous = bool(getattr(local_time, "fold", 0))
            dst_offset = local_time.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
            utc_offset = local_time.utcoffset()
            utc_offset_min = int(utc_offset.total_seconds() // 60) if utc_offset is not None else 0

            window_start_local = datetime(target_d.year, target_d.month, target_d.day, 0, 0, tzinfo=tz)
            window_end_local = datetime(target_d.year, target_d.month, target_d.day, 23, 59, 59, tzinfo=tz)
            window_start_utc = window_start_local.astimezone(_tz.utc)
            window_end_utc = window_end_local.astimezone(_tz.utc)

            hemisphere = hemisphere_for_lat(city_cfg.lat) if city_cfg else "N"
            season = season_from_date(target_str, lat=city_cfg.lat if city_cfg else 90.0)

            try:
                # K1-C+ (2026-04-13): skip Layer 2 (check_physical_bounds)
                # because city_monthly_bounds.json is derived from TIGGE
                # forecast ensemble distributions, which systematically
                # under-represent observation tails — this is precisely why
                # Platt calibration exists. Running Layer 2 against real WU
                # daily max rejects legitimate hot-day readings (e.g., 84°F
                # NYC September, 94°F Atlanta September).
                #
                # Daily LOW validation (2026-04-13 fix): Layer 1 check_unit
                # catches garbage via Earth records and city.settlement_unit
                # cross-check. Layer 3 (seasonal envelope) is SKIPPED on low
                # because _N_ENVELOPE / _S_ENVELOPE bounds are tuned for
                # daily MAX and would mis-reject legitimate mid-latitude
                # winter cold-night lows. Additionally: low <= high is an
                # internal consistency check on the WU hourly series.
                _GUARD.check_unit_consistency(
                    city=city_name,
                    raw_value=high,
                    raw_unit=unit,
                    declared_unit=(city_cfg.settlement_unit if city_cfg else unit),
                    target_date=target_d,
                )
                _GUARD.check_unit_consistency(
                    city=city_name,
                    raw_value=low,
                    raw_unit=unit,
                    declared_unit=(city_cfg.settlement_unit if city_cfg else unit),
                    target_date=target_d,
                )
                if low > high:
                    raise IngestionRejected(
                        f"{city_name}/{target_str}: low={low} > high={high} — "
                        f"WU observation series internally inconsistent"
                    )
                # Layer 3 deleted 2026-04-13 — hemisphere-uniform envelope
                # was a category error (22 false positives in a single run).
                # See src/data/ingestion_guard.py module docstring.
                _GUARD.check_collection_timing(
                    city=city_name,
                    fetch_utc=fetch_utc,
                    target_date=target_d,
                    peak_hour=city_cfg.historical_peak_hour if city_cfg else 15.0,
                )
                _GUARD.check_dst_boundary(
                    city=city_name,
                    local_time=local_time,
                )
            except IngestionRejected as e:
                guard_rejected += 1
                logger.warning("Guard rejected %s/%s: %s", city_name, target_str, e)
                current += timedelta(days=1)
                continue

            # Build both atoms (high + low). Day0 decisions consume daily low
            # via monitor_refresh.py:419 and src/signal/day0_signal.py. The
            # original K1-C WU port only wrote high_temp, leaving low_temp
            # NULL — this was fixed 2026-04-13.
            _atom_common = dict(
                city=city_name,
                target_date=target_d,
                target_unit=unit,
                raw_unit=unit,
                source="wu_icao_history",
                station_id=icao,
                api_endpoint=f"https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json",
                fetch_utc=fetch_utc,
                local_time=local_time,
                collection_window_start_utc=window_start_utc,
                collection_window_end_utc=window_end_utc,
                timezone=timezone_name,
                utc_offset_minutes=utc_offset_min,
                dst_active=dst_active,
                is_ambiguous_local_hour=is_ambiguous,
                is_missing_local_hour=is_missing_local,
                hemisphere=hemisphere,
                season=season,
                month=target_d.month,
                rebuild_run_id=rebuild_run_id,
                data_source_version="wu_icao_v1_2026",
                authority="VERIFIED",
                validation_pass=True,
                provenance_metadata=provenance,
            )
            atom_high = ObservationAtom(
                value_type="high",
                value=high,
                raw_value=high,
                **_atom_common,
            )
            atom_low = ObservationAtom(
                value_type="low",
                value=low,
                raw_value=low,
                **_atom_common,
            )

            if not dry_run:
                _write_atom_to_observations(conn, atom_high, atom_low=atom_low)

            if existing is None:
                collected += 1
            else:
                skip_count += 1
            current += timedelta(days=1)

        if not dry_run:
            conn.commit()
        chunk_label = "[DRY RUN] " if dry_run else ""
        print(
            f"    {chunk_label}{chunk_start} -> {chunk_end}: "
            f"collected={collected} skip={skip_count} err={err_count} guard_rejected={guard_rejected}"
        )
        time.sleep(sleep_seconds)


    if not dry_run:
        conn.commit()
    return {"city": city_name, "collected": collected, "skip": skip_count, "err": err_count, "guard_rejected": guard_rejected, "requests": request_count}


def main(argv: list[str] | None = None) -> int:
    _require_wu_api_key()
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=None)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--missing-only", action="store_true", help="Fetch only dates missing WU observations or valued settlements")
    parser.add_argument("--all", action="store_true", help="All configured cities")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate but do not write to DB; print per-city summary")
    add_completeness_args(
        parser,
        manifest_prefix=COMPLETENESS_MANIFEST_PREFIX,
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    conn = get_world_connection()
    # Enable WAL journal mode so IngestionGuard's `_log_availability_failure`
    # secondary connection can write during backfill writes without BUSY-lock
    # silent drops. Default rollback journal serializes writers and the
    # guard's try/except swallowed the BUSY on failure, producing the
    # observability gap where guard rejections never reached availability_fact.
    # WAL is SQLite best practice for concurrent-ish access anyway.
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)

    if args.cities:
        targets = args.cities
    elif args.all:
        targets = list(CITY_STATIONS.keys())
    else:
        # Default: cities without wu_icao_history observations
        covered = {r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM observations WHERE source = 'wu_icao_history'"
        ).fetchall()}
        targets = [c for c in CITY_STATIONS if c not in covered]

    dry_run = args.dry_run
    run_id = datetime.now(_tz.utc).strftime("backfill_wu_daily_all_%Y%m%dT%H%M%SZ")
    if dry_run:
        print("[DRY RUN] No rows will be written to the DB.")
    print(
        f"=== WU ICAO Station History Backfill "
        f"({len(targets)} cities, {args.days} days, run_id={run_id}) ==="
    )

    results = []
    for city_name in targets:
        icao, cc, unit = CITY_STATIONS[city_name]
        print(f"\n[{city_name}] {icao}:{cc} unit={unit}")
        r = backfill_city(
            city_name,
            args.days,
            conn,
            rebuild_run_id=run_id,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep,
            missing_only=args.missing_only,
            dry_run=dry_run,
        )
        results.append(r)
        print(f"  → collected={r['collected']} skip={r['skip']} err={r['err']} guard_rejected={r['guard_rejected']}")

    conn.close()

    if dry_run:
        print("\n=== Dry Run Summary (nothing written) ===")
        print(f"{'city':<22} {'fetched':>7} {'passed':>7} {'rejected':>9} {'would_write':>12}")
        print("-" * 62)
        for r in results:
            fetched = r["collected"] + r["skip"] + r["guard_rejected"]
            passed = r["collected"] + r["skip"]
            print(f"  {r['city']:<20} {fetched:>7} {passed:>7} {r['guard_rejected']:>9} {r['collected']:>12}")
    else:
        print("\n=== Summary ===")
        total = sum(r["collected"] for r in results)
        print(f"Total collected: {total}")
        for r in results:
            if r["collected"] > 0:
                print(f"  {r['city']:20s} +{r['collected']}")

    total_passed = sum(r["collected"] + r["skip"] for r in results)
    total_failed = sum(r["err"] + r["guard_rejected"] for r in results)
    total_attempted = total_passed + total_failed
    completeness = evaluate_completeness(
        actual_count=total_passed,
        failed_count=total_failed,
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
        script_name="backfill_wu_daily_all.py",
        run_id=run_id,
        dry_run=args.dry_run,
        inputs={
            "cities": targets,
            "days": args.days,
            "chunk_days": args.chunk_days,
            "sleep": args.sleep,
            "missing_only": args.missing_only,
            "all": args.all,
            "expected_count": args.expected_count,
            "fail_threshold_percent": args.fail_threshold_percent,
        },
        counters={
            "unit_kind": "target_day",
            "passed": total_passed,
            "failed": total_failed,
            "attempted": total_attempted,
            "new_count": sum(r["collected"] for r in results),
            "replaced_or_existing_count": sum(r["skip"] for r in results),
            "fetch_or_missing_errors": sum(r["err"] for r in results),
            "guard_rejected": sum(r["guard_rejected"] for r in results),
            "requests": sum(r.get("requests", 0) for r in results),
            "city_count": len(results),
            "per_city": results,
        },
        completeness=completeness,
    )
    emit_manifest_footer(manifest_path, completeness)
    return int(completeness["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
