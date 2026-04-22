# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 Phase 0 files #4/#5 (.omc/plans/observation-
#                  instants-migration-iter3.md L86-93).
"""Networkless parse + snap tests for WU/Ogimet hourly clients.

End-to-end HTTP tests live in the backfill driver's own live-probe
script — this file pins the parse/snap logic deterministically so a
CI without network can verify behavior.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.data.ogimet_hourly_client import (
    _parse_metar_csv_line,
    _parse_metar_temp_c,
    _snap as ogimet_snap,
)
from src.data.wu_hourly_client import HourlyObservation, _snap_to_hourly


# ----------------------------------------------------------------------
# METAR temperature parse
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, expected",
    [
        ("METAR UUWW 211830Z 11004MPS 9999 FEW030 10/08 Q1013", 10.0),
        ("METAR LLBG 211830Z 34008KT 9999 FEW020 M05/M08 Q1020", -5.0),
        ("METAR EGLC 211830Z CALM 9999 SCT015 M01/02 Q1018", -1.0),
        ("METAR RJTT 211830Z 08010KT CAVOK 25/20 Q1013", 25.0),
    ],
)
def test_parse_metar_temp(body, expected):
    assert _parse_metar_temp_c(body) == expected


def test_parse_metar_temp_missing_group():
    assert _parse_metar_temp_c("METAR UUWW 211830Z NOSIG") is None


# ----------------------------------------------------------------------
# CSV line parse (Ogimet format)
# ----------------------------------------------------------------------


def test_parse_csv_line_valid():
    line = "UUWW,2024,01,15,14,30,METAR UUWW 151430Z 10004MPS 9999 FEW030 05/M02 Q1015="
    parsed = _parse_metar_csv_line(line)
    assert parsed is not None
    assert parsed[0] == datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
    assert parsed[1] == 5.0


def test_parse_csv_line_missing_temp_group_returns_none():
    line = "UUWW,2024,01,15,14,30,METAR UUWW 151430Z NOSIG="
    assert _parse_metar_csv_line(line) is None


def test_parse_csv_line_bad_date_returns_none():
    line = "UUWW,xxxx,01,15,14,30,METAR UUWW 151430Z 05/M02"
    assert _parse_metar_csv_line(line) is None


# ----------------------------------------------------------------------
# WU snap: sub-hourly → one per top-of-hour
# ----------------------------------------------------------------------


def test_wu_snap_picks_closest_to_top_of_hour():
    """Two obs in the same hour bucket: the one closer to HH:00 wins."""
    # Both in the 14:00 UTC bucket; one at 14:05, one at 14:58.
    raw = [
        {"valid_time_gmt": int(datetime(2024, 1, 15, 14, 5, tzinfo=timezone.utc).timestamp()), "temp": 10.0},
        {"valid_time_gmt": int(datetime(2024, 1, 15, 14, 58, tzinfo=timezone.utc).timestamp()), "temp": 12.0},
    ]
    snapped = _snap_to_hourly(
        raw,
        icao="KORD",
        unit="F",
        timezone_name="America/Chicago",
        city_name="Chicago",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
    )
    # 14:05 is 5 min from 14:00, 14:58 is 2 min from 15:00 (goes to 15:00 bucket).
    # So 14:00 gets 10.0, and 15:00 gets 12.0.
    by_hour = {o.utc_timestamp: o.temp_current for o in snapped}
    assert by_hour["2024-01-15T14:00:00+00:00"] == 10.0
    assert by_hour["2024-01-15T15:00:00+00:00"] == 12.0


def test_wu_snap_drops_obs_outside_snap_window():
    """An obs at HH:31 is equidistant to HH:00 and (HH+1):00 — still snaps."""
    # At exactly HH:30 the code snaps to next hour. Try HH:31 — still 29 min
    # from next hour top, so should snap to HH+1:00.
    raw = [
        {"valid_time_gmt": int(datetime(2024, 1, 15, 14, 31, tzinfo=timezone.utc).timestamp()), "temp": 7.0},
    ]
    snapped = _snap_to_hourly(
        raw,
        icao="KORD",
        unit="F",
        timezone_name="America/Chicago",
        city_name="Chicago",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
    )
    # Should snap to 15:00 (29 min from 14:31)
    by_hour = {o.utc_timestamp: o.temp_current for o in snapped}
    assert "2024-01-15T15:00:00+00:00" in by_hour


def test_wu_snap_attaches_all_fields():
    """Ensures the HourlyObservation has every field the writer needs."""
    raw = [
        {
            "valid_time_gmt": int(
                datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc).timestamp()
            ),
            "temp": 32.0,
        },
    ]
    [obs] = _snap_to_hourly(
        raw,
        icao="KORD",
        unit="F",
        timezone_name="America/Chicago",
        city_name="Chicago",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 16),
    )
    assert obs.city == "Chicago"
    assert obs.station_id == "KORD"
    assert obs.temp_unit == "F"
    assert obs.time_basis == "utc_hour_aligned"
    assert obs.utc_timestamp == "2024-01-15T14:00:00+00:00"
    assert obs.local_timestamp.startswith("2024-01-15T08:00:00")  # CST = UTC-6
    assert obs.local_hour == 8.0
    assert obs.utc_offset_minutes == -360
    assert obs.observation_count == 1
    assert obs.is_ambiguous_local_hour == 0
    assert obs.is_missing_local_hour == 0


# ----------------------------------------------------------------------
# Ogimet snap: unit conversion + emit
# ----------------------------------------------------------------------


def test_ogimet_snap_emits_celsius_natively():
    rows = [
        (datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 10.0),
    ]
    out = list(
        ogimet_snap(
            rows,
            station="UUWW",
            unit_out="C",
            timezone_name="Europe/Moscow",
            city_name="Moscow",
            source_tag="ogimet_metar_uuww",
            start_date=date(2024, 1, 15),
            end_date=date(2024, 1, 15),
        )
    )
    assert len(out) == 1
    obs = out[0]
    assert obs.temp_current == 10.0
    assert obs.temp_unit == "C"
    assert obs.station_id == "UUWW"


def test_ogimet_snap_converts_to_fahrenheit_on_request():
    rows = [
        (datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 0.0),  # 0°C → 32°F
    ]
    out = list(
        ogimet_snap(
            rows,
            station="UUWW",
            unit_out="F",
            timezone_name="Europe/Moscow",
            city_name="Moscow",
            source_tag="ogimet_metar_uuww",
            start_date=date(2024, 1, 15),
            end_date=date(2024, 1, 15),
        )
    )
    assert len(out) == 1
    assert out[0].temp_current == 32.0
    assert out[0].temp_unit == "F"


def test_ogimet_snap_filters_by_local_date_window():
    """Observations whose local date falls outside the window are dropped."""
    # Moscow is UTC+3. 2024-01-15 22:00 UTC = 2024-01-16 01:00 local
    # → should be excluded when end_date=2024-01-15.
    rows = [
        (datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 10.0),  # keep
        (datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc), 5.0),  # drop
    ]
    out = list(
        ogimet_snap(
            rows,
            station="UUWW",
            unit_out="C",
            timezone_name="Europe/Moscow",
            city_name="Moscow",
            source_tag="ogimet_metar_uuww",
            start_date=date(2024, 1, 15),
            end_date=date(2024, 1, 15),
        )
    )
    assert len(out) == 1
    assert out[0].temp_current == 10.0


def test_ogimet_snap_same_hour_keeps_closer_obs():
    """Two obs in the 14:00 bucket: the closer wins."""
    rows = [
        (datetime(2024, 1, 15, 14, 2, tzinfo=timezone.utc), 5.0),  # closer
        (datetime(2024, 1, 15, 14, 28, tzinfo=timezone.utc), 7.0),  # farther
    ]
    out = list(
        ogimet_snap(
            rows,
            station="UUWW",
            unit_out="C",
            timezone_name="Europe/Moscow",
            city_name="Moscow",
            source_tag="ogimet_metar_uuww",
            start_date=date(2024, 1, 15),
            end_date=date(2024, 1, 15),
        )
    )
    assert len(out) == 1
    assert out[0].temp_current == 5.0  # the 14:02 obs


# ----------------------------------------------------------------------
# DST handling — Chicago spring-forward on 2024-03-10
# ----------------------------------------------------------------------


def test_wu_snap_detects_missing_local_hour_on_dst_spring_forward():
    """2024-03-10 07:00 UTC = 01:00 CST (standard), 08:00 UTC = 03:00 CDT.

    The 2:00 local wall-clock hour doesn't exist. Our algorithm snaps
    to UTC hours, so the raw row at 07:30 UTC lands in the 07:00 bucket
    which maps to local 01:00 (fine) or 08:00 bucket mapping to 03:00
    (fine). A "missing local hour" is flagged when the round-trip
    local→UTC doesn't match. For CST→CDT at 08:00 UTC the local is
    03:00 CDT which round-trips correctly, so is_missing=0. The only
    time this flag fires is if someone tries to stamp 02:00 local —
    which our UTC-first algorithm never does.
    """
    raw = [
        {
            "valid_time_gmt": int(
                datetime(2024, 3, 10, 8, 0, tzinfo=timezone.utc).timestamp()
            ),
            "temp": 35.0,
        },
    ]
    [obs] = _snap_to_hourly(
        raw,
        icao="KORD",
        unit="F",
        timezone_name="America/Chicago",
        city_name="Chicago",
        start_date=date(2024, 3, 10),
        end_date=date(2024, 3, 10),
    )
    assert obs.local_hour == 3.0  # Correctly lands at 03:00 CDT
    assert obs.dst_active == 1  # DST now in effect
    assert obs.is_missing_local_hour == 0  # UTC-first snap never produces 02:00 local
