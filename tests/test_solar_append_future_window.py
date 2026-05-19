# Created: 2026-05-19
# Last reused/audited: 2026-05-19
# Authority basis: Day0 solar-daily forward-window stall fix (2026-05-19 alpha-loss postmortem)
#
# Relationship test: when daily_tick runs, the forward-window NOAA path must
# write rows for target_date > today so that day0_capture never starves.
# Invariant: MAX(target_date) in solar_daily == today + future_days after one tick.
"""Antibody tests for solar_append forward-window fix.

T1: daily_tick writes today + 14 future rows per city (15 total) on empty DB.
T2: Calling daily_tick twice is idempotent (no row count growth).
T3: NOAA-path row matches _noaa_sunrise_sunset_utc reference for a future date.
T4: Past/present branch (Open-Meteo) is invoked for target_date == today.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.config import cities_by_name
from src.data.solar_append import (
    _noaa_sunrise_sunset_utc,
    _build_noaa_row,
    daily_tick,
)
from src.state.db import init_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKYO = cities_by_name["Tokyo"]
_TEST_CITIES = [_TOKYO]


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _today_archive_row(city: City, today: date) -> list[dict]:
    """Return one synthetic archive row for today (mocks OpenMeteo response)."""
    from datetime import timezone as tz_
    from zoneinfo import ZoneInfo
    noaa_sunrise, noaa_sunset = _noaa_sunrise_sunset_utc(today, city.lat, city.lon)
    local_tz = ZoneInfo(city.timezone)
    sr_local = noaa_sunrise.astimezone(local_tz)
    ss_local = noaa_sunset.astimezone(local_tz)
    utc_offset = sr_local.utcoffset()
    dst_offset = sr_local.dst()
    return [{
        "city": city.name,
        "target_date": today.isoformat(),
        "timezone": city.timezone,
        "lat": float(city.lat),
        "lon": float(city.lon),
        "sunrise_local": sr_local.isoformat(),
        "sunset_local": ss_local.isoformat(),
        "sunrise_utc": noaa_sunrise.isoformat(),
        "sunset_utc": noaa_sunset.isoformat(),
        "utc_offset_minutes": int(utc_offset.total_seconds() / 60) if utc_offset else 0,
        "dst_active": 1 if (dst_offset and dst_offset.total_seconds() > 0) else 0,
    }]


# ---------------------------------------------------------------------------
# T1: 15 rows per city (today + 14 future)
# ---------------------------------------------------------------------------

def test_t1_daily_tick_writes_today_plus_14_rows(monkeypatch):
    """daily_tick on an empty DB should produce 15 rows per city."""
    conn = _make_db()
    now_utc = datetime(2026, 6, 1, 5, 30, tzinfo=timezone.utc)
    today = now_utc.date()

    def mock_fetch_chunk(city, start_date, end_date):
        # Only today is requested via archive path
        assert start_date == today
        assert end_date == today
        return _today_archive_row(city, today)

    with patch("src.data.solar_append._fetch_solar_chunk", side_effect=mock_fetch_chunk):
        result = daily_tick(conn, now_utc=now_utc, cities=_TEST_CITIES, future_days=14)

    rows = conn.execute(
        "SELECT target_date FROM solar_daily WHERE city = ? ORDER BY target_date",
        (_TOKYO.name,),
    ).fetchall()
    dates = [r["target_date"] for r in rows]

    assert len(dates) == 15, f"Expected 15 rows, got {len(dates)}: {dates}"
    assert dates[0] == today.isoformat()
    assert dates[-1] == (today + timedelta(days=14)).isoformat()
    assert result["cities_processed"] == 1
    assert result["inserted"] == 15


# ---------------------------------------------------------------------------
# T2: Idempotency — second tick does not grow row count
# ---------------------------------------------------------------------------

def test_t2_daily_tick_idempotent(monkeypatch):
    """Calling daily_tick twice should not increase total row count."""
    conn = _make_db()
    now_utc = datetime(2026, 6, 1, 5, 30, tzinfo=timezone.utc)
    today = now_utc.date()

    def mock_fetch_chunk(city, start_date, end_date):
        return _today_archive_row(city, today)

    with patch("src.data.solar_append._fetch_solar_chunk", side_effect=mock_fetch_chunk):
        daily_tick(conn, now_utc=now_utc, cities=_TEST_CITIES, future_days=14)
        daily_tick(conn, now_utc=now_utc, cities=_TEST_CITIES, future_days=14)

    count = conn.execute(
        "SELECT COUNT(*) FROM solar_daily WHERE city = ?", (_TOKYO.name,)
    ).fetchone()[0]
    assert count == 15, f"Expected exactly 15 rows after two ticks, got {count}"


# ---------------------------------------------------------------------------
# T3: NOAA-path row matches _noaa_sunrise_sunset_utc reference
# ---------------------------------------------------------------------------

def test_t3_noaa_row_matches_reference():
    """_build_noaa_row for a future date must round-trip through _noaa_sunrise_sunset_utc."""
    future_date = date(2026, 6, 15)
    row = _build_noaa_row(_TOKYO, future_date)

    ref_sunrise_utc, ref_sunset_utc = _noaa_sunrise_sunset_utc(
        future_date, _TOKYO.lat, _TOKYO.lon
    )

    # sunrise_utc stored as ISO string; parse and compare
    stored_sunrise = datetime.fromisoformat(row["sunrise_utc"])
    stored_sunset = datetime.fromisoformat(row["sunset_utc"])

    assert stored_sunrise == ref_sunrise_utc, (
        f"Sunrise mismatch: stored={stored_sunrise} ref={ref_sunrise_utc}"
    )
    assert stored_sunset == ref_sunset_utc, (
        f"Sunset mismatch: stored={stored_sunset} ref={ref_sunset_utc}"
    )
    assert row["city"] == _TOKYO.name
    assert row["target_date"] == future_date.isoformat()


# ---------------------------------------------------------------------------
# T4: Open-Meteo archive path is invoked for target_date == today
# ---------------------------------------------------------------------------

def test_t4_archive_path_called_for_today(monkeypatch):
    """The Open-Meteo archive fetch must be called with start_date == end_date == today."""
    conn = _make_db()
    now_utc = datetime(2026, 6, 1, 5, 30, tzinfo=timezone.utc)
    today = now_utc.date()

    call_log: list[tuple[date, date]] = []

    def mock_fetch_chunk(city, start_date, end_date):
        call_log.append((start_date, end_date))
        return _today_archive_row(city, today)

    with patch("src.data.solar_append._fetch_solar_chunk", side_effect=mock_fetch_chunk):
        daily_tick(conn, now_utc=now_utc, cities=_TEST_CITIES, future_days=14)

    assert len(call_log) == 1, f"Expected 1 archive call, got {len(call_log)}: {call_log}"
    start, end = call_log[0]
    assert start == today, f"Archive call start_date={start} expected {today}"
    assert end == today, f"Archive call end_date={end} expected {today}"
