# Created: 2026-04-21
# Last reused/audited: 2026-05-02 (PR 36 review: local UTC bounds + DST fold)
# Authority basis: plan v3 Phase 0 files #4/#5; extremum-preservation
#                  correction 2026-04-21 (operator).
"""Networkless parse + aggregate tests for WU/Ogimet hourly clients.

Two invariant families:
1. METAR/CSV parsing correctness (regex + field extraction).
2. Extremum-preserving hourly aggregation — the contract that each UTC
   hour bucket emits one row carrying the max AND min observed across
   all raw reports in that hour, with their raw timestamps preserved.

A regression in #2 would reintroduce the "closest-to-HH:00 snap" bug
that would erase intra-hour SPECI peaks (fatal for PM daily-max
settlement and for Day-0 stop-loss monitoring).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import src.data.ogimet_hourly_client as ogimet_client
from src.data.ogimet_hourly_client import (
    _aggregate as ogimet_aggregate,
    _detect_missing_local_hour as ogimet_detect_missing_local_hour,
    _local_date_range_to_utc_window,
    _parse_metar_csv_line,
    _parse_metar_temp_c,
)
from src.data.wu_hourly_client import (
    HourlyObservation,
    _aggregate_hourly,
    _detect_missing_local_hour as wu_detect_missing_local_hour,
)


# ----------------------------------------------------------------------
# METAR temperature parse (unchanged)
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
# CSV line parse (Ogimet format, unchanged)
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
# WU extremum-preserving aggregation (the critical contract)
# ----------------------------------------------------------------------


def _wu_obs(hour: int, minute: int, temp: float, *, month: int = 1, day: int = 15) -> dict:
    dt = datetime(2024, month, day, hour, minute, tzinfo=timezone.utc)
    return {"valid_time_gmt": int(dt.timestamp()), "temp": temp}


def test_wu_aggregate_emits_hour_max_and_min_from_same_bucket():
    """The KEY extremum invariant: a SPECI peak MUST land in hour_max.

    Scenario: 14:00 METAR=79°F, 14:35 SPECI=82°F, 15:00 METAR=80°F.
    The 14:00 bucket carries MAX=82°F (the SPECI), not 79°F.
    The old snap-to-HH:00 logic would have kept 79°F and the 82°F
    would be erased forever — fatal for PM settlement alignment.
    """
    raw = [_wu_obs(14, 0, 79.0), _wu_obs(14, 35, 82.0), _wu_obs(15, 0, 80.0)]
    out = _aggregate_hourly(
        raw,
        icao="KORD",
        unit="F",
        timezone_name="America/Chicago",
        city_name="Chicago",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
    )
    by_hour = {o.utc_timestamp: o for o in out}
    bucket_14 = by_hour["2024-01-15T14:00:00+00:00"]
    bucket_15 = by_hour["2024-01-15T15:00:00+00:00"]
    # 14:00 bucket: max=82 (the SPECI), min=79 (the METAR)
    assert bucket_14.hour_max_temp == 82.0
    assert bucket_14.hour_min_temp == 79.0
    assert bucket_14.hour_max_raw_ts.startswith("2024-01-15T14:35:00")
    assert bucket_14.hour_min_raw_ts.startswith("2024-01-15T14:00:00")
    assert bucket_14.observation_count == 2
    # 15:00 bucket has a single obs; max == min == 80
    assert bucket_15.hour_max_temp == 80.0
    assert bucket_15.hour_min_temp == 80.0
    assert bucket_15.observation_count == 1


def test_wu_aggregate_daily_max_equals_settlement():
    """MAX(hour_max_temp) across the day == the WU daily settlement high.

    If this invariant breaks, Platt calibration learns a phantom bias.
    """
    # Mixed-hour day with one clear SPECI peak inside hour 14.
    raw = [
        _wu_obs(10, 0, 75.0),
        _wu_obs(11, 0, 77.0),
        _wu_obs(12, 0, 79.0),
        _wu_obs(13, 0, 80.0),
        _wu_obs(14, 0, 79.0),
        _wu_obs(14, 35, 82.0),  # <-- the peak, an off-hour SPECI
        _wu_obs(15, 0, 80.0),
        _wu_obs(16, 0, 78.0),
    ]
    out = _aggregate_hourly(
        raw,
        icao="KORD",
        unit="F",
        timezone_name="America/Chicago",
        city_name="Chicago",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
    )
    daily_max = max(o.hour_max_temp for o in out if o.target_date == "2024-01-15")
    assert daily_max == 82.0  # NOT 80.0 (the snap-to-HH:00 value)


def test_wu_aggregate_multi_obs_bucket_tracks_both_max_and_min():
    """Bucket with 4 obs: hour_max and hour_min come from correct rows."""
    raw = [
        _wu_obs(14, 0, 70.0),
        _wu_obs(14, 15, 75.0),
        _wu_obs(14, 30, 78.0),
        _wu_obs(14, 45, 72.0),
    ]
    out = _aggregate_hourly(
        raw, icao="KORD", unit="F", timezone_name="America/Chicago",
        city_name="Chicago", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    [bucket] = [o for o in out if o.utc_timestamp == "2024-01-15T14:00:00+00:00"]
    assert bucket.hour_max_temp == 78.0
    assert bucket.hour_max_raw_ts.startswith("2024-01-15T14:30:00")
    assert bucket.hour_min_temp == 70.0
    assert bucket.hour_min_raw_ts.startswith("2024-01-15T14:00:00")
    assert bucket.observation_count == 4


def test_wu_aggregate_single_obs_bucket_max_equals_min():
    raw = [_wu_obs(14, 0, 32.0)]
    [bucket] = _aggregate_hourly(
        raw, icao="KORD", unit="F", timezone_name="America/Chicago",
        city_name="Chicago", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    assert bucket.hour_max_temp == 32.0
    assert bucket.hour_min_temp == 32.0
    assert bucket.observation_count == 1


def test_wu_aggregate_attaches_all_fields():
    raw = [_wu_obs(14, 0, 32.0), _wu_obs(14, 35, 35.0)]
    [bucket] = _aggregate_hourly(
        raw, icao="KORD", unit="F", timezone_name="America/Chicago",
        city_name="Chicago", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    assert bucket.city == "Chicago"
    assert bucket.station_id == "KORD"
    assert bucket.temp_unit == "F"
    assert bucket.time_basis == "utc_hour_bucket_extremum"
    assert bucket.utc_timestamp == "2024-01-15T14:00:00+00:00"
    assert bucket.local_timestamp.startswith("2024-01-15T08:00:00")  # CST = UTC-6
    assert bucket.local_hour == 8.0
    assert bucket.utc_offset_minutes == -360


def test_wu_aggregate_filters_by_local_date_window():
    """Obs whose local date is outside the window are dropped."""
    raw = [
        _wu_obs(5, 0, 30.0),   # Chicago CST: 2024-01-14 23:00 (drop)
        _wu_obs(14, 0, 32.0),  # Chicago CST: 2024-01-15 08:00 (keep)
    ]
    out = _aggregate_hourly(
        raw, icao="KORD", unit="F", timezone_name="America/Chicago",
        city_name="Chicago", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    assert len(out) == 1
    assert out[0].target_date == "2024-01-15"


def test_wu_aggregate_determinism_on_temp_tie():
    """If two obs in the same bucket have equal temp, the earlier wins for max AND min.

    Pins the tiebreak so provenance_json.hour_max_raw_ts is stable across
    re-runs.
    """
    raw = [
        _wu_obs(14, 15, 32.0),  # earlier
        _wu_obs(14, 45, 32.0),  # later, same temp
    ]
    [bucket] = _aggregate_hourly(
        raw, icao="KORD", unit="F", timezone_name="America/Chicago",
        city_name="Chicago", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    assert bucket.hour_max_raw_ts.startswith("2024-01-15T14:15:00")
    assert bucket.hour_min_raw_ts.startswith("2024-01-15T14:15:00")


# ----------------------------------------------------------------------
# Ogimet extremum-preserving aggregation
# ----------------------------------------------------------------------


def test_ogimet_aggregate_preserves_extremum():
    rows = [
        (datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 10.0),
        (datetime(2024, 1, 15, 14, 35, tzinfo=timezone.utc), 13.0),  # SPECI peak
        (datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc), 11.0),
    ]
    out = list(
        ogimet_aggregate(
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
    by_hour = {o.utc_timestamp: o for o in out}
    bucket_14 = by_hour["2024-01-15T14:00:00+00:00"]
    assert bucket_14.hour_max_temp == 13.0
    assert bucket_14.hour_min_temp == 10.0
    assert bucket_14.observation_count == 2


def test_ogimet_aggregate_emits_celsius_natively():
    rows = [(datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 10.0)]
    [bucket] = ogimet_aggregate(
        rows, station="UUWW", unit_out="C", timezone_name="Europe/Moscow",
        city_name="Moscow", source_tag="ogimet_metar_uuww",
        start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    assert bucket.hour_max_temp == 10.0
    assert bucket.temp_unit == "C"
    assert bucket.station_id == "UUWW"


def test_ogimet_aggregate_converts_to_fahrenheit_on_request():
    rows = [(datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 0.0)]
    [bucket] = ogimet_aggregate(
        rows, station="UUWW", unit_out="F", timezone_name="Europe/Moscow",
        city_name="Moscow", source_tag="ogimet_metar_uuww",
        start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
    )
    assert bucket.hour_max_temp == 32.0
    assert bucket.temp_unit == "F"


def test_ogimet_aggregate_filters_by_local_date_window():
    """Moscow is UTC+3. 2024-01-15 22:00 UTC = 2024-01-16 01:00 local (drop)."""
    rows = [
        (datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), 10.0),  # keep
        (datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc), 5.0),  # drop
    ]
    out = list(
        ogimet_aggregate(
            rows, station="UUWW", unit_out="C", timezone_name="Europe/Moscow",
            city_name="Moscow", source_tag="ogimet_metar_uuww",
            start_date=date(2024, 1, 15), end_date=date(2024, 1, 15),
        )
    )
    assert len(out) == 1
    assert out[0].hour_max_temp == 10.0


def test_ogimet_query_window_uses_local_date_midnight_in_utc():
    start_utc, end_utc = _local_date_range_to_utc_window(
        date(2024, 1, 15),
        date(2024, 1, 15),
        "Europe/Moscow",
    )

    assert start_utc == datetime(2024, 1, 14, 21, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2024, 1, 15, 20, 59, 59, tzinfo=timezone.utc)


def test_fetch_ogimet_requests_local_date_utc_bounds(monkeypatch):
    calls: list[tuple[datetime, datetime]] = []

    def fake_fetch_one_chunk(station, begin, end, timeout_seconds):
        calls.append((begin, end))
        return ogimet_client._ChunkResult()

    monkeypatch.setattr(ogimet_client, "_fetch_one_chunk", fake_fetch_one_chunk)

    result = ogimet_client.fetch_ogimet_hourly(
        "UUWW",
        date(2024, 1, 15),
        date(2024, 1, 15),
        city_name="Moscow",
        timezone_name="Europe/Moscow",
        source_tag="ogimet_metar_uuww",
    )

    assert result.failed is False
    assert calls == [
        (
            datetime(2024, 1, 14, 21, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 15, 20, 59, 59, tzinfo=timezone.utc),
        )
    ]


# ----------------------------------------------------------------------
# DST regression
# ----------------------------------------------------------------------


def test_wu_aggregate_handles_dst_spring_forward_correctly():
    """2024-03-10 Chicago: 07:00 UTC = 01:00 CST, 08:00 UTC = 03:00 CDT.

    The local 02:00 wall-clock is non-existent. Our UTC-first aggregation
    never synthesizes that hour — we just have the UTC buckets.
    """
    raw = [_wu_obs(8, 0, 35.0, month=3, day=10)]  # 2024-03-10 08:00 UTC = 03:00 CDT
    out = _aggregate_hourly(
        raw, icao="KORD", unit="F", timezone_name="America/Chicago",
        city_name="Chicago", start_date=date(2024, 3, 10), end_date=date(2024, 3, 10),
    )
    [bucket] = out
    assert bucket.local_hour == 3.0  # jumped from 01:00 CST to 03:00 CDT
    assert bucket.dst_active == 1
    assert bucket.is_missing_local_hour == 0  # UTC-first never hits 02:00 local


def test_missing_local_hour_detection_preserves_fallback_fold():
    """2024-11-03 07:00 UTC is Chicago's second 01:00 hour (fold=1)."""
    tz = ZoneInfo("America/Chicago")
    utc_dt = datetime(2024, 11, 3, 7, 0, tzinfo=timezone.utc)
    local_dt = utc_dt.astimezone(tz)

    assert local_dt.hour == 1
    assert local_dt.fold == 1
    assert wu_detect_missing_local_hour(utc_dt, tz) is False
    assert ogimet_detect_missing_local_hour(utc_dt, tz) is False


# ----------------------------------------------------------------------
# Negative: the old snap-to-HH:00 bug pattern must not reappear
# ----------------------------------------------------------------------


def test_no_hourly_observation_field_named_temp_current():
    """Regression antibody against the broken snap semantics.

    The old HourlyObservation had a single ``temp_current`` field which
    carried the closest-to-HH:00 obs (erasing extrema). The new struct
    carries hour_max_temp + hour_min_temp instead. If someone re-adds
    temp_current to the struct, this test fails and forces the PR
    author to confront the extremum semantics before merging.
    """
    fields = set(HourlyObservation.__dataclass_fields__.keys())
    assert "temp_current" not in fields, (
        "HourlyObservation.temp_current re-introduced — this was the "
        "snap-to-HH:00 field whose removal is enforced by "
        "extremum-preservation semantics. Use hour_max_temp + "
        "hour_min_temp instead."
    )
    assert "hour_max_temp" in fields
    assert "hour_min_temp" in fields
    assert "hour_max_raw_ts" in fields
    assert "hour_min_raw_ts" in fields
