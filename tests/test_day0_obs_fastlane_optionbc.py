# Created: 2026-06-12
# Last reused/audited: 2026-07-19
# Authority basis: day0_obs_fastlane_plan.md §4.2 (Option B) and §4.3 (Option C);
#   operator task brief /tmp/day0_obs_fastlane_plan.md.
"""Antibody tests for Day0 observation fast-lane Options B and C.

Option B: _fetch_wu_observation may use the same-station fast-tail in-process
  memo when WU distribution is stale, absent, or coverage-incomplete.

Option C: ingest_k2_obs_fast_tick scheduler registration + _active_window_cities
  city-predicate unit tests.

Relationship contracts tested:
  B1. stale WU + fresh memo → context served with same_station_fast_tail source.
  B2. stale WU + stale memo (cache > FAST_LANE_ENTRY_MAX_CACHE_AGE_S) → honest
      stale rejection unchanged (no context returned from fast lane).
  B3. non-wu_icao city → same-station source never serves.
  B4. station mismatch → fast lane returns None (faithfulness gate).
  B5. coverage-incomplete WU + fast lane has good first_obs_time →
      coverage_status reflects METAR-computed value.
  B6. coverage-incomplete WU + fast lane has late first_obs_time →
      coverage_status remains WINDOW_INCOMPLETE.
  B7. fresh WU result (not stale, not coverage-incomplete) → fast lane NOT
      consulted; WU result returned.

  C1. ingest_k2_obs_fast_tick is registered in the APScheduler job list.
  C2. _active_window_cities returns only cities in the local active window.
  C3. _active_window_cities returns empty list when all cities are past
      peak_hour+6h local time.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _wu_icao_city(*, name="Denver", station="KBKF", tz="America/Denver", unit="F"):
    return SimpleNamespace(
        name=name,
        timezone=tz,
        settlement_unit=unit,
        wu_station=station,
        settlement_source_type="wu_icao",
        lat=39.7,
        lon=-104.8,
        historical_peak_hour=15.0,
    )


def _non_wu_city(*, name="Istanbul", station="LTFM"):
    return SimpleNamespace(
        name=name,
        timezone="Europe/Istanbul",
        settlement_unit="C",
        wu_station=station,
        settlement_source_type="noaa",
        lat=41.0,
        lon=28.8,
        historical_peak_hour=14.0,
    )


def _make_fast_extremes(
    *,
    city="Denver",
    station="KBKF",
    unit="F",
    target_date="2026-06-12",
    high_so_far=85.0,
    low_so_far=62.0,
    current_temp=83.0,
    first_obs_time_hours_after_midnight: float = 0.5,
    last_obs_time_hours_ago: float = 0.05,
    sample_count: int = 10,
    last_receipt_time_hours_ago: float = 0.08,
    reference_utc: Optional[datetime] = None,
    sample_times_utc=None,
):
    """Build a FastObsExtremes-like SimpleNamespace for testing."""
    ref = reference_utc or datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Denver")
    local_midnight = datetime(2026, 6, 12, 0, 0, tzinfo=tz)
    first_obs = local_midnight + timedelta(hours=first_obs_time_hours_after_midnight)
    last_obs = ref - timedelta(hours=last_obs_time_hours_ago)
    last_receipt = ref - timedelta(hours=last_receipt_time_hours_ago)
    if sample_times_utc is None:
        if sample_count <= 1:
            sample_times_utc = (first_obs.astimezone(UTC),)
        else:
            span = (last_obs - first_obs) / (sample_count - 1)
            sample_times_utc = tuple(
                (first_obs + span * index).astimezone(UTC)
                for index in range(sample_count)
            )
    return SimpleNamespace(
        city=city,
        station_id=station,
        target_date=target_date,
        unit=unit,
        high_so_far=high_so_far,
        low_so_far=low_so_far,
        current_temp=current_temp,
        first_obs_time=first_obs.astimezone(UTC),
        last_obs_time=last_obs.astimezone(UTC),
        last_receipt_time=last_receipt.astimezone(UTC),
        sample_count=sample_count,
        skipped_unit_law=0,
        held_implausible=0,
        sample_times_utc=tuple(sample_times_utc),
    )


# ---------------------------------------------------------------------------
# Option B: _wu_result_needs_fast_tail
# ---------------------------------------------------------------------------

class TestWuResultNeedsFastTail:
    """Unit tests for the same-station tail predicate."""

    def _fn(self, result, ref_utc):
        from src.data.observation_client import _wu_result_needs_fast_tail
        return _wu_result_needs_fast_tail(result, reference_utc=ref_utc)

    def test_none_result_needs_fast_tail(self):
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        assert self._fn(None, ref) == "wu_result_none"

    def test_fresh_result_no_fast_tail(self):
        from src.data.observation_client import Day0ObservationContext
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        obs_time = (ref - timedelta(minutes=30)).isoformat()
        ctx = Day0ObservationContext(
            current_temp=80.0, high_so_far=82.0, low_so_far=65.0,
            source="wu_api", observation_time=obs_time, unit="F",
            coverage_status="OK",
            observation_available_at=ref.isoformat(),
        )
        assert self._fn(ctx, ref) is None

    def test_stale_result_needs_fast_tail(self):
        from src.data.observation_client import Day0ObservationContext
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        obs_time = (ref - timedelta(hours=1, minutes=30)).isoformat()
        ctx = Day0ObservationContext(
            current_temp=80.0, high_so_far=82.0, low_so_far=65.0,
            source="wu_api", observation_time=obs_time, unit="F",
            coverage_status="OK",
            observation_available_at=ref.isoformat(),
        )
        reason = self._fn(ctx, ref)
        assert reason is not None
        assert "wu_stale" in reason

    def test_coverage_incomplete_needs_fast_tail(self):
        from src.data.observation_client import Day0ObservationContext
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        obs_time = (ref - timedelta(minutes=20)).isoformat()
        ctx = Day0ObservationContext(
            current_temp=80.0, high_so_far=82.0, low_so_far=65.0,
            source="wu_api", observation_time=obs_time, unit="F",
            coverage_status="WINDOW_INCOMPLETE",
            observation_available_at=ref.isoformat(),
        )
        reason = self._fn(ctx, ref)
        assert reason == "wu_coverage_window_incomplete"


# ---------------------------------------------------------------------------
# Option B: _fetch_same_station_fast_tail_observation
# ---------------------------------------------------------------------------

class TestFetchMetarFastLaneObservation:
    """Unit tests for the METAR fast-lane observation builder."""

    def _call(self, city, target_day, reference_utc, extremes):
        from src.data import observation_client as oc
        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = extremes
        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            return oc._fetch_same_station_fast_tail_observation(
                city, target_day=target_day, reference_utc=reference_utc
            )

    def test_returns_none_for_non_wu_icao_city(self):
        """B3: non-wu_icao city -> same-station source never serves."""
        city = _non_wu_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        # fast_obs_source_for_city returns None for noaa settlement_source_type;
        # latest_extremes returns None for unsupported city — either way result is None.
        result = self._call(city, date(2026, 6, 12), ref, extremes=None)
        assert result is None

    def test_returns_context_with_same_station_fast_tail_source(self):
        """B1: stale WU + fresh memo → context served with same_station_fast_tail source."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        extremes = _make_fast_extremes(reference_utc=ref)

        from src.data import observation_client as oc
        from unittest.mock import MagicMock
        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = extremes

        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            result = oc._fetch_same_station_fast_tail_observation(
                city, target_day=date(2026, 6, 12), reference_utc=ref
            )

        assert result is not None
        assert result.source == "same_station_fast_tail"
        assert result.high_so_far == 85.0
        assert result.low_so_far == 62.0
        assert result.current_temp == 83.0
        assert result.station_id == "KBKF"
        assert result.unit == "F"

    def test_provenance_annotation_in_provider_reported_time(self):
        """B1: returned context carries provenance annotation for honest receipts."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        extremes = _make_fast_extremes(reference_utc=ref)

        from src.data import observation_client as oc
        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = extremes

        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            result = oc._fetch_same_station_fast_tail_observation(
                city, target_day=date(2026, 6, 12), reference_utc=ref
            )

        assert result is not None
        ann = result.provider_reported_time or ""
        assert "day0_obs_source=same_station_fast_tail" in ann
        assert "KBKF" in ann

    def test_returns_none_when_extremes_is_none(self):
        """B2: stale memo → fast lane returns None → honest stale rejection unchanged."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)

        from src.data import observation_client as oc
        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = None

        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            result = oc._fetch_same_station_fast_tail_observation(
                city, target_day=date(2026, 6, 12), reference_utc=ref
            )

        assert result is None

    def test_coverage_ok_when_metar_first_obs_in_grace_window(self):
        """B5: coverage-incomplete WU + METAR first_obs within grace window → coverage OK."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        # first_obs 0.5h after midnight — within the 2h grace window
        extremes = _make_fast_extremes(
            reference_utc=ref,
            first_obs_time_hours_after_midnight=0.5,
            sample_count=10,
        )

        from src.data import observation_client as oc
        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = extremes

        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            result = oc._fetch_same_station_fast_tail_observation(
                city, target_day=date(2026, 6, 12), reference_utc=ref
            )

        assert result is not None
        assert result.coverage_status in ("OK", "LOW_COVERAGE")

    def test_coverage_incomplete_when_metar_first_obs_outside_grace_window(self):
        """B6: METAR first_obs > 2h after midnight → coverage_status WINDOW_INCOMPLETE."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        # first_obs 3h after midnight — outside the 2h grace window
        extremes = _make_fast_extremes(
            reference_utc=ref,
            first_obs_time_hours_after_midnight=3.0,
            sample_count=10,
        )

        from src.data import observation_client as oc
        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = extremes

        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            result = oc._fetch_same_station_fast_tail_observation(
                city, target_day=date(2026, 6, 12), reference_utc=ref
            )

        assert result is not None
        assert result.coverage_status == "WINDOW_INCOMPLETE"

    def test_internal_high_window_gap_is_metric_attributed(self):
        city = _wu_icao_city()
        ref = datetime(2026, 6, 13, 2, 0, tzinfo=UTC)  # Jun 12 20:00 Denver
        tz = ZoneInfo("America/Denver")
        sample_times = tuple(
            datetime(2026, 6, 12, hour, tzinfo=tz).astimezone(UTC)
            for hour in [*range(0, 10), 18, 19]
        )
        extremes = _make_fast_extremes(
            reference_utc=ref,
            first_obs_time_hours_after_midnight=0.0,
            sample_count=len(sample_times),
            sample_times_utc=sample_times,
        )

        from src.data import observation_client as oc

        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = extremes
        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter):
            result = oc._fetch_same_station_fast_tail_observation(
                city, target_day=date(2026, 6, 12), reference_utc=ref
            )

        assert result is not None
        assert result.coverage_status == "GAP_SUSPECT"
        assert result.gap_suspect_metrics == ("high",)
        assert result.max_gap_minutes == pytest.approx(540.0)


# ---------------------------------------------------------------------------
# Option B: get_current_observation fast-lane integration
# ---------------------------------------------------------------------------

class TestGetCurrentObservationFastTail:
    """Integration tests: get_current_observation uses same-station tail when WU stale."""

    def _wu_stale_context(self, ref_utc):
        """Build a WU context whose observation_time is >1h old."""
        from src.data.observation_client import Day0ObservationContext
        obs_time = (ref_utc - timedelta(hours=1, minutes=30)).isoformat()
        return Day0ObservationContext(
            current_temp=80.0, high_so_far=82.0, low_so_far=65.0,
            source="wu_api", observation_time=obs_time, unit="F",
            coverage_status="OK",
            observation_available_at=ref_utc.isoformat(),
        )

    def test_stale_wu_fresh_memo_returns_fast_tail_context(self):
        """B1 integration: stale WU + fresh memo → same_station_fast_tail context returned."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        stale_wu = self._wu_stale_context(ref)
        fast_extremes = _make_fast_extremes(reference_utc=ref)

        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = fast_extremes

        with (
            patch("src.data.observation_client._fetch_wu_observation", return_value=stale_wu),
            patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter),
        ):
            from src.data.observation_client import get_current_observation
            result = get_current_observation(city, target_date=date(2026, 6, 12), reference_time=ref)

        assert result.source == "same_station_fast_tail"

    def test_stale_wu_stale_memo_returns_wu_context(self):
        """B2: stale WU + stale memo → WU context returned (no upgrade possible)."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        stale_wu = self._wu_stale_context(ref)

        mock_emitter = MagicMock()
        mock_emitter.latest_extremes.return_value = None  # memo empty / stale

        with (
            patch("src.data.observation_client._fetch_wu_observation", return_value=stale_wu),
            patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter),
        ):
            from src.data.observation_client import get_current_observation
            result = get_current_observation(city, target_date=date(2026, 6, 12), reference_time=ref)

        assert result.source == "wu_api"

    def test_fresh_wu_fast_lane_not_consulted(self):
        """B7: fresh WU result → fast lane NOT consulted."""
        city = _wu_icao_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        from src.data.observation_client import Day0ObservationContext
        fresh_obs_time = (ref - timedelta(minutes=20)).isoformat()
        fresh_wu = Day0ObservationContext(
            current_temp=80.0, high_so_far=82.0, low_so_far=65.0,
            source="wu_api", observation_time=fresh_obs_time, unit="F",
            coverage_status="OK",
            observation_available_at=ref.isoformat(),
        )

        mock_emitter = MagicMock()

        with (
            patch("src.data.observation_client._fetch_wu_observation", return_value=fresh_wu),
            patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter),
        ):
            from src.data.observation_client import get_current_observation
            result = get_current_observation(city, target_date=date(2026, 6, 12), reference_time=ref)

        assert result.source == "wu_api"
        mock_emitter.latest_extremes.assert_not_called()

    def test_non_wu_icao_city_fast_lane_never_fires(self):
        """B3: non-wu_icao city → ObservationUnavailableError, fast lane never consulted."""
        from src.contracts.exceptions import ObservationUnavailableError
        city = _non_wu_city()
        ref = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
        mock_emitter = MagicMock()

        with (
            patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=mock_emitter),
        ):
            from src.data.observation_client import get_current_observation
            with pytest.raises(ObservationUnavailableError):
                get_current_observation(city, target_date=date(2026, 6, 12), reference_time=ref)

        mock_emitter.latest_extremes.assert_not_called()


# ---------------------------------------------------------------------------
# Option B: latest_extremes API on Day0FastObsEmitter
# ---------------------------------------------------------------------------

class TestDay0FastObsEmitterLatestExtremes:
    """Unit tests for the new latest_extremes() method."""

    def _make_emitter_with_cache(self, reports, cache_age_s=10.0):
        """Return an emitter with pre-loaded cache."""
        from src.data.day0_fast_obs import Day0FastObsEmitter
        emitter = Day0FastObsEmitter()
        # Seed internal cache directly (thread-safe via lock)
        with emitter._lock:
            emitter._cached_reports = list(reports)
            emitter._cache_fetched_monotonic = time.monotonic() - cache_age_s
        return emitter

    def _make_metar_report(self, station, temp_c, hours_ago=0.5, with_t_group=True,
                           reference_utc: Optional[datetime] = None):
        from src.data.day0_fast_obs import MetarReport
        # Use real now so that as_of=datetime.now(UTC) in latest_extremes includes the report.
        ref = reference_utc or datetime.now(UTC)
        ts = ref - timedelta(hours=hours_ago)
        # T-group format: T followed by exactly 8 digits (sign+3digit temp tenths, sign+3digit dewpoint tenths)
        # e.g. T02800150 for 28.0C / 15.0C
        t_group = f"T0{int(abs(temp_c) * 10):03d}0150" if with_t_group else ""
        raw = f"METAR {station} 121853Z {t_group} RMK AO2"
        return MetarReport(
            station_id=station,
            obs_time=ts,
            receipt_time=ts + timedelta(minutes=3),
            temp_c=temp_c,
            metar_type="METAR",
            raw=raw,
        )

    def _local_today_iso(self, tz_name: str) -> str:
        """Return today's local date ISO string for the given timezone."""
        from zoneinfo import ZoneInfo
        return datetime.now(UTC).astimezone(ZoneInfo(tz_name)).date().isoformat()

    def test_returns_none_when_cache_empty(self):
        """Empty cache → None returned."""
        from src.data.day0_fast_obs import Day0FastObsEmitter
        emitter = Day0FastObsEmitter()
        city = _wu_icao_city()
        target = self._local_today_iso("America/Denver")
        result = emitter.latest_extremes(city, target)
        assert result is None

    def test_returns_none_when_cache_stale(self):
        """Cache older than FAST_LANE_ENTRY_MAX_CACHE_AGE_S → None returned."""
        from src.data.day0_fast_obs import FAST_LANE_ENTRY_MAX_CACHE_AGE_S
        reports = [self._make_metar_report("KBKF", 28.0)]
        emitter = self._make_emitter_with_cache(
            reports, cache_age_s=FAST_LANE_ENTRY_MAX_CACHE_AGE_S + 60.0
        )
        city = _wu_icao_city()
        target = self._local_today_iso("America/Denver")
        result = emitter.latest_extremes(city, target)
        assert result is None

    def test_returns_extremes_when_cache_fresh(self):
        """Fresh cache → FastObsExtremes returned with sample_count > 0."""
        reports = [
            self._make_metar_report("KBKF", 28.0, hours_ago=2.0),
            self._make_metar_report("KBKF", 30.0, hours_ago=1.0),
            self._make_metar_report("KBKF", 29.0, hours_ago=0.5),
        ]
        emitter = self._make_emitter_with_cache(reports, cache_age_s=30.0)
        city = _wu_icao_city()
        target = self._local_today_iso("America/Denver")
        result = emitter.latest_extremes(city, target)
        assert result is not None
        assert result.sample_count > 0
        assert result.station_id == "KBKF"

    def test_returns_none_for_non_wu_icao_city(self):
        """B3: fast_obs_source_for_city returns None for noaa city → None."""
        reports = [self._make_metar_report("LTFM", 28.0)]
        emitter = self._make_emitter_with_cache(reports, cache_age_s=30.0)
        city = _non_wu_city()
        target = self._local_today_iso("Europe/Istanbul")
        result = emitter.latest_extremes(city, target)
        assert result is None

    def test_returns_none_when_no_station_match_in_cache(self):
        """B4: station mismatch → no reports match → None returned."""
        # Cache has LTBA reports but city expects KBKF
        reports = [self._make_metar_report("LTBA", 28.0)]
        emitter = self._make_emitter_with_cache(reports, cache_age_s=30.0)
        city = _wu_icao_city(station="KBKF")
        target = self._local_today_iso("America/Denver")
        result = emitter.latest_extremes(city, target)
        assert result is None


# ---------------------------------------------------------------------------
# Option C: _active_window_cities city predicate
# ---------------------------------------------------------------------------

class TestActiveWindowCities:
    """Unit tests for the city local-time active window predicate."""

    def _cities_with_one_active(self, local_hour: float):
        """Build a minimal cities_by_name with one city at the given local hour."""
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        # UTC offset for America/Denver is -6 in summer (MDT).
        # At local_hour = 14.5 (2:30 PM MDT), UTC = 20:30.
        denver_tz = ZoneInfo("America/Denver")
        # Construct a UTC time that yields the desired local hour in Denver.
        local_midnight_utc = datetime(2026, 6, 12, 6, 0, tzinfo=UTC)  # midnight MDT = 06:00 UTC
        now_utc = local_midnight_utc + timedelta(hours=local_hour)
        return now_utc

    def test_city_in_active_window_included(self):
        """City at local 10:00 (< peak_hour+6=21h) is included."""
        from src.ingest_main import _active_window_cities

        denver = SimpleNamespace(
            name="Denver", timezone="America/Denver",
            historical_peak_hour=15.0,
        )
        # Local 10:00 MDT → UTC 16:00
        now_utc = datetime(2026, 6, 12, 16, 0, tzinfo=UTC)
        with patch("src.config.cities_by_name", {"Denver": denver}):
            result = _active_window_cities(now_utc)

        assert "Denver" in result

    def test_city_past_active_window_excluded(self):
        """City at local 23:00 (> peak_hour+6=21h) is excluded."""
        from src.ingest_main import _active_window_cities

        denver = SimpleNamespace(
            name="Denver", timezone="America/Denver",
            historical_peak_hour=15.0,
        )
        # Local 23:00 MDT → UTC next day 05:00
        now_utc = datetime(2026, 6, 13, 5, 0, tzinfo=UTC)
        with patch("src.config.cities_by_name", {"Denver": denver}):
            result = _active_window_cities(now_utc)

        assert "Denver" not in result

    def test_city_at_midnight_included(self):
        """City at local 00:00 (window start) is included."""
        from src.ingest_main import _active_window_cities

        karachi = SimpleNamespace(
            name="Karachi", timezone="Asia/Karachi",
            historical_peak_hour=15.0,
        )
        # Local midnight PKT = 19:00 UTC previous day
        now_utc = datetime(2026, 6, 11, 19, 0, tzinfo=UTC)
        with patch("src.config.cities_by_name", {"Karachi": karachi}):
            result = _active_window_cities(now_utc)

        assert "Karachi" in result

    def test_empty_cities_returns_empty(self):
        """No cities → empty list."""
        from src.ingest_main import _active_window_cities
        now_utc = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
        with patch("src.config.cities_by_name", {}):
            result = _active_window_cities(now_utc)
        assert result == []

    def test_city_with_empty_timezone_skipped(self):
        """City with empty timezone string is silently skipped."""
        from src.ingest_main import _active_window_cities

        bad_city = SimpleNamespace(
            name="BadCity", timezone="",
            historical_peak_hour=15.0,
        )
        now_utc = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
        with patch("src.config.cities_by_name", {"BadCity": bad_city}):
            result = _active_window_cities(now_utc)
        assert "BadCity" not in result


# ---------------------------------------------------------------------------
# Option C: ingest_k2_obs_fast_tick scheduler registration
# ---------------------------------------------------------------------------

class TestObsFastTickSchedulerRegistration:
    """C1: ingest_k2_obs_fast_tick must be in the job specs and pass the boot guard.

    Uses _ingest_main_job_specs() directly (does not run main() which would
    require live DBs — same approach as test_writer_jobs_registry_guard.py).
    """

    def _get_spec_ids(self) -> list[str]:
        """Return the job IDs from _ingest_main_job_specs."""
        import src.ingest_main as im
        specs = im._ingest_main_job_specs()
        # Each spec is (callable, trigger_type, kwargs_dict) — extract id from kwargs
        ids = []
        for _fn, _trigger, kwargs in specs:
            job_id = kwargs.get("id")
            if job_id:
                ids.append(job_id)
        return ids

    def test_ingest_k2_obs_fast_tick_in_job_specs(self):
        """C1: ingest_k2_obs_fast_tick must appear in _ingest_main_job_specs."""
        ids = self._get_spec_ids()
        assert "ingest_k2_obs_fast_tick" in ids, (
            f"Expected ingest_k2_obs_fast_tick in job specs; got: {sorted(ids)}"
        )

    def test_boot_guard_passes_with_new_job(self):
        """C1: assert_writer_jobs_registered must not raise after adding the fast tick."""
        from src.state.table_registry import assert_writer_jobs_registered
        assert_writer_jobs_registered()  # must not raise

    def test_obs_fast_tick_uses_interval_trigger(self):
        """C1: fast tick spec must use interval trigger (15-min), not cron."""
        import src.ingest_main as im
        specs = im._ingest_main_job_specs()
        fast_tick_specs = [
            (fn, trigger, kwargs)
            for fn, trigger, kwargs in specs
            if kwargs.get("id") == "ingest_k2_obs_fast_tick"
        ]
        assert fast_tick_specs, "ingest_k2_obs_fast_tick not found in _ingest_main_job_specs"
        _fn, trigger, kwargs = fast_tick_specs[0]
        assert trigger == "interval", (
            f"Expected interval trigger for fast tick, got: {trigger!r}"
        )
        assert kwargs.get("minutes") == 15, (
            f"Expected minutes=15 for fast tick, got: {kwargs.get('minutes')}"
        )

    def test_obs_fast_tick_decorator_registered(self):
        """C1: @_scheduler_job('ingest_k2_obs_fast_tick') decorator must exist."""
        import src.ingest_main as im
        # The _scheduler_job decorator stashes itself on the function via __wrapped__
        # and the function is importable by name.
        assert hasattr(im, "_k2_obs_fast_tick"), (
            "_k2_obs_fast_tick function not found in ingest_main"
        )

    def test_day0_metar_source_clock_uses_five_second_default(self, monkeypatch):
        import src.ingest_main as im

        monkeypatch.delenv(im.DAY0_METAR_POLL_SECONDS_ENV, raising=False)
        specs = im._ingest_main_job_specs()
        _fn, trigger, kwargs = next(
            spec
            for spec in specs
            if spec[2].get("id") == "ingest_day0_metar_source_clock"
        )

        assert trigger == "interval"
        assert kwargs["seconds"] == 5.0
        assert kwargs["max_instances"] == 1
        assert kwargs["coalesce"] is True
        assert kwargs["next_run_time"] is not None

        monkeypatch.setenv(im.DAY0_METAR_POLL_SECONDS_ENV, "0.1")
        specs = im._ingest_main_job_specs()
        kwargs = next(
            spec[2]
            for spec in specs
            if spec[2].get("id") == "ingest_day0_metar_source_clock"
        )
        assert kwargs["seconds"] == 1.0

        assert not any(
            spec[2].get("id") == "ingest_day0_metar_commit_retry"
            for spec in specs
        )

    def test_day0_metar_source_clock_has_one_cadence_authority(self, monkeypatch):
        import src.ingest_main as im

        constructed = []

        class _Emitter:
            def __init__(self, *, min_fetch_interval_s):
                constructed.append(min_fetch_interval_s)

        monkeypatch.setattr(im, "_DAY0_METAR_EMITTER", None)
        monkeypatch.setattr("src.data.day0_fast_obs.Day0FastObsEmitter", _Emitter)

        assert isinstance(im._day0_metar_emitter(), _Emitter)
        assert constructed == [0.0]

    def test_day0_oracle_guard_is_separate_from_source_clock_lane(self):
        import src.ingest_main as im

        specs = im._ingest_main_job_specs()
        source_kwargs = next(
            spec[2]
            for spec in specs
            if spec[2].get("id") == "ingest_day0_metar_source_clock"
        )
        kwargs = next(
            spec[2]
            for spec in specs
            if spec[2].get("id") == "ingest_day0_oracle_anomaly"
        )

        assert kwargs["seconds"] == 10
        assert kwargs["max_instances"] == 1
        assert kwargs["coalesce"] is True
        assert (
            kwargs["next_run_time"] - source_kwargs["next_run_time"]
        ).total_seconds() == pytest.approx(2.5)


class TestDay0MetarSourceClockTick:
    @staticmethod
    def _enable(monkeypatch):
        import src.ingest_main as im

        monkeypatch.setattr(im, "_DAY0_METAR_PENDING_COMMITS", [])
        monkeypatch.setattr(im, "_DAY0_METAR_COMMIT_LOCK", threading.Lock())
        monkeypatch.setattr(im, "_day0_source_family_admission", lambda _eligible: None)
        monkeypatch.setattr(im, "_day0_priority_scopes", lambda: frozenset())
        monkeypatch.setattr(
            "src.config.settings",
            {
                "edli": {
                    "enabled": True,
                    "event_writer_enabled": True,
                    "day0_extreme_trigger_enabled": True,
                    "day0_fast_obs_lane_enabled": True,
                    "edli_live_scope": "forecast_plus_day0",
                }
            },
        )
        monkeypatch.setattr("src.config.runtime_cities", lambda: [_wu_icao_city()])

        class _Lease:
            def __enter__(self):
                return SimpleNamespace(record_commit=lambda **_kw: None)

            def __exit__(self, _exc_type, _exc, _tb):
                return False

        monkeypatch.setattr(
            "src.state.write_coordinator.default_runtime_write_coordinator",
            lambda: SimpleNamespace(lease=lambda *_args, **_kwargs: _Lease()),
        )

    @staticmethod
    def _primed(emitter):
        emitter.ledger_report_keys_loaded = lambda: True
        return emitter

    def test_source_current_does_not_open_world_db(self, monkeypatch):
        import src.ingest_main as im

        self._enable(monkeypatch)
        prefetch = SimpleNamespace(
            ledger_reports=(),
            freshness_status="fresh_fetch",
            reports=(object(),),
        )
        emitter = self._primed(SimpleNamespace(prefetch=lambda **_kw: prefetch))
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("DB opened for unchanged payload")
            ),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result["status"] == "SOURCE_CURRENT"

    def test_cold_start_seeds_ledger_identities_before_fetch(self, monkeypatch):
        import src.ingest_main as im

        self._enable(monkeypatch)
        order: list[str] = []
        prefetch = SimpleNamespace(
            ledger_reports=(),
            freshness_status="fresh_fetch",
            reports=(object(),),
        )

        class _Emitter:
            def ledger_report_keys_loaded(self):
                return False

            def sync_ledger_report_keys(self, conn, cities, *, as_of):
                assert conn is read_conn
                assert len(cities) == 1
                assert as_of.tzinfo is not None
                order.append("sync")
                return 400

            def prefetch(self, **_kw):
                order.append("fetch")
                return prefetch

        read_conn = MagicMock()
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: _Emitter())
        monkeypatch.setattr(
            "src.state.db.get_world_connection_read_only",
            lambda: read_conn,
        )
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("writer DB opened for unchanged payload")
            ),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result["status"] == "SOURCE_CURRENT"
        assert order == ["sync", "fetch"]
        read_conn.close.assert_called_once_with()

    def test_strict_settings_shape_enables_source_clock(self, monkeypatch):
        import src.ingest_main as im

        class StrictSettings:
            def __getitem__(self, key):
                assert key == "edli"
                return {
                    "enabled": True,
                    "event_writer_enabled": True,
                    "day0_extreme_trigger_enabled": True,
                    "day0_fast_obs_lane_enabled": True,
                    "edli_live_scope": "forecast_plus_day0",
                }

        prefetch = SimpleNamespace(
            ledger_reports=(),
            freshness_status="fresh_fetch",
            reports=(object(),),
        )
        emitter = self._primed(SimpleNamespace(prefetch=lambda **_kw: prefetch))
        monkeypatch.setattr("src.config.settings", StrictSettings())
        monkeypatch.setattr("src.config.runtime_cities", lambda: [_wu_icao_city()])
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("DB opened for unchanged payload")
            ),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result["status"] == "SOURCE_CURRENT"

    def test_source_family_admission_keeps_only_marketed_or_held_metrics(
        self,
        monkeypatch,
    ):
        import src.ingest_main as im

        class _Conn:
            def __init__(self, rows):
                self.rows = rows
                self.closed = False

            def execute(self, _sql, _params=()):
                return SimpleNamespace(fetchall=lambda: self.rows)

            def close(self):
                self.closed = True

        forecasts = _Conn([("Denver", "2026-06-12", "high")])
        trades = _Conn([("Paris", "2026-06-12", "low")])
        monkeypatch.setattr(
            "src.state.db.get_forecasts_connection_read_only",
            lambda: forecasts,
        )
        monkeypatch.setattr(
            "src.state.db.get_trade_connection_read_only",
            lambda: trades,
        )

        admit = im._day0_source_family_admission(
            (
                (_wu_icao_city(name="Denver"), object(), "2026-06-12"),
                (_wu_icao_city(name="Paris"), object(), "2026-06-12"),
            )
        )

        assert admit is not None
        assert admit({"city": "Denver", "target_date": "2026-06-12", "metric": "high"})
        assert admit({"city": "Paris", "target_date": "2026-06-12", "metric": "low"})
        assert not admit({"city": "Denver", "target_date": "2026-06-12", "metric": "low"})
        assert forecasts.closed is True
        assert trades.closed is True

    def test_commits_before_publishing_reactor_wake(self, monkeypatch):
        import src.ingest_main as im

        self._enable(monkeypatch)
        order: list[str] = []
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )

        class _Emitter:
            def prefetch(self, **_kw):
                return prefetch

            def hydrate_event_memos_from_events(
                self,
                _conn,
                _eligible,
                *,
                family_admission,
            ):
                self.hydrate_admission = family_admission
                order.append("memo_hydrate")

            def emit_prefetched(self, **_kw):
                self.family_admission = _kw["family_admission"]
                assert _kw["persist_ledger"] is False
                order.append("emit")
                _kw["inserted_event_ids"].extend(("event-b", "event-a"))
                _kw["inserted_families"].extend(
                    (("Paris", "2026-06-12", "high"),)
                )
                return 2

            def persist_prefetched_ledger(self, **_kw):
                assert _kw["prefetch"] is prefetch
                order.append("ledger")
                return True

        class _Conn:
            total_changes = 0

            def execute(self, sql, _params=()):
                if "opportunity_events" in sql:
                    raise AssertionError("inserted event IDs must not be recovered by history scan")
                if sql == "BEGIN IMMEDIATE":
                    order.append("begin")
                return self

            def commit(self):
                order.append("commit")

            def rollback(self):
                order.append("rollback")

            def close(self):
                order.append("close")

        class _Mutex:
            def acquire(self, *, timeout):
                order.append(f"acquire:{timeout}")
                return True

            def release(self):
                order.append("release")

        emitter = self._primed(_Emitter())
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        admission = object()
        monkeypatch.setattr(
            im,
            "_day0_source_family_admission",
            lambda _eligible: admission,
        )
        def _open_world(**_kw):
            order.append("db_open")
            return _Conn()

        monkeypatch.setattr("src.state.db.get_world_connection", _open_world)
        monkeypatch.setattr(
            "src.state.db.get_world_connection_read_only",
            lambda: SimpleNamespace(close=lambda: order.append("read_close")),
        )
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: _Mutex())

        class _Lease:
            def __enter__(self):
                order.append("gate_enter")
                return SimpleNamespace(
                    record_commit=lambda **_kw: order.append("record_commit")
                )

            def __exit__(self, _exc_type, _exc, _tb):
                order.append("gate_exit")
                return False

        monkeypatch.setattr(
            "src.state.write_coordinator.default_runtime_write_coordinator",
            lambda: SimpleNamespace(lease=lambda *_args, **_kwargs: _Lease()),
        )
        monkeypatch.setattr(
            "src.runtime.reactor_wake.publish_reactor_wake",
            lambda **kwargs: order.append(
                f"wake:{','.join(kwargs['event_ids'])}:"
                f"{kwargs['forecast_families']}"
            ),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result == {
            "status": "COMMITTED",
            "pending_reports": 1,
            "events_emitted": 2,
        }
        wake_entry = next(item for item in order if item.startswith("wake:"))
        acquire_entry = next(item for item in order if item.startswith("acquire:"))
        assert order.index("memo_hydrate") < order.index(acquire_entry)
        assert order.index("gate_enter") < order.index("db_open") < order.index("begin")
        assert order.index("commit") < order.index(wake_entry)
        assert (
            order.index("commit")
            < order.index("gate_exit")
            < order.index(wake_entry)
        )
        assert "ledger" not in order
        assert "wake:event-b,event-a" in wake_entry
        assert "(('Paris', '2026-06-12', 'high'),)" in wake_entry
        assert emitter.hydrate_admission is admission
        assert emitter.family_admission is admission

    def test_non_emitting_pass_flushes_deferred_ledger(self, monkeypatch):
        import src.ingest_main as im

        self._enable(monkeypatch)
        order: list[str] = []
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )

        class _Emitter:
            def prefetch(self, **_kw):
                return prefetch

            def emit_prefetched(self, **_kw):
                assert _kw["persist_ledger"] is False
                order.append("emit")
                return 0

            def persist_prefetched_ledger(self, **_kw):
                assert _kw["prefetch"] is prefetch
                order.append("ledger")
                return True

        class _Conn:
            total_changes = 0

            def execute(self, _sql, _params=()):
                return self

            def commit(self):
                order.append("commit")

            def rollback(self):
                order.append("rollback")

            def close(self):
                order.append("close")

        class _Mutex:
            def acquire(self, *, timeout):
                return True

            def release(self):
                order.append("release")

        emitter = self._primed(_Emitter())
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr("src.state.db.get_world_connection", lambda **_kw: _Conn())
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: _Mutex())
        monkeypatch.setattr(
            "src.runtime.reactor_wake.publish_reactor_wake",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("non-emitting pass must not wake the reactor")
            ),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result == {
            "status": "COMMITTED",
            "pending_reports": 1,
            "events_emitted": 0,
        }
        assert order.index("commit") < order.index("ledger")

    def test_already_evaluated_publications_bypass_event_writer(self, monkeypatch):
        import src.ingest_main as im

        self._enable(monkeypatch)
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
        )

        class _Emitter:
            def prefetch(self, **_kw):
                return prefetch

            def prefetched_events_evaluated(self, value):
                assert value is prefetch
                return True

        emitter = self._primed(_Emitter())
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr(
            im,
            "_persist_day0_metar_ledger_after_wake",
            lambda value: value is prefetch,
        )
        monkeypatch.setattr(
            im,
            "_stage_day0_metar_commit",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("evaluated publications must bypass event staging")
            ),
        )

        assert im._day0_metar_source_clock_tick.__wrapped__() == {
            "status": "LEDGER_FLUSHED",
            "pending_reports": 1,
        }

    def test_committed_event_survives_reactor_wake_failure(
        self, monkeypatch, caplog
    ):
        import src.ingest_main as im

        self._enable(monkeypatch)
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )

        class _Emitter:
            def prefetch(self, **_kw):
                return prefetch

            def emit_prefetched(self, **_kw):
                _kw["inserted_event_ids"].extend(("event-b", "event-a"))
                return 2

        class _Conn:
            total_changes = 0

            def execute(self, _sql, _params=()):
                return self

            def commit(self):
                return None

            def rollback(self):
                raise AssertionError("committed source event must not roll back")

            def close(self):
                return None

        class _Mutex:
            def acquire(self, *, timeout):
                return True

            def release(self):
                return None

        emitter = self._primed(_Emitter())
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr("src.state.db.get_world_connection", lambda **_kw: _Conn())
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: _Mutex())
        monkeypatch.setattr(
            "src.runtime.reactor_wake.publish_reactor_wake",
            lambda **_kw: (_ for _ in ()).throw(OSError("sidecar unavailable")),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result == {
            "status": "COMMITTED",
            "pending_reports": 1,
            "events_emitted": 2,
        }
        assert "periodic reactor scan remains authoritative" in caplog.text

    def test_writer_contention_defers_without_emitting(self, monkeypatch):
        import src.ingest_main as im

        self._enable(monkeypatch)
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )
        emitter = self._primed(
            SimpleNamespace(
                prefetch=lambda **_kw: prefetch,
                emit_prefetched=lambda **_kw: (_ for _ in ()).throw(
                    AssertionError("emit called while writer lock was contended")
                ),
            )
        )
        conn = MagicMock()
        mutex = MagicMock()
        mutex.acquire.return_value = False
        scheduled = []
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr("src.state.db.get_world_connection", lambda **_kw: conn)
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: mutex)
        monkeypatch.setattr(
            im,
            "_scheduler",
            SimpleNamespace(add_job=lambda *args, **kwargs: scheduled.append((args, kwargs))),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result == {"status": "WRITE_CONTENDED", "pending_reports": 1}
        conn.close.assert_not_called()
        mutex.release.assert_not_called()
        assert len(scheduled) == 1
        args, kwargs = scheduled[0]
        assert args == (im._day0_metar_commit_retry_tick, "date")
        assert kwargs["id"] == "ingest_day0_metar_commit_retry"
        assert kwargs["executor"] == "source_clock_db"
        assert kwargs["replace_existing"] is True
        assert kwargs["run_date"] is not None

    def test_unified_writer_gate_contention_defers_before_db_open(
        self,
        monkeypatch,
    ):
        import src.ingest_main as im
        from src.state.write_coordinator import WriteLeaseTimeout

        self._enable(monkeypatch)
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )
        emitter = self._primed(SimpleNamespace(prefetch=lambda **_kw: prefetch))
        mutex = MagicMock()
        mutex.acquire.return_value = True
        scheduled = []

        class _ContendedLease:
            def __enter__(self):
                raise WriteLeaseTimeout("WORLD gate busy")

            def __exit__(self, _exc_type, _exc, _tb):
                return False

        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: mutex)
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("DB opened before unified WORLD gate")
            ),
        )
        monkeypatch.setattr(
            "src.state.write_coordinator.default_runtime_write_coordinator",
            lambda: SimpleNamespace(
                lease=lambda *_args, **_kwargs: _ContendedLease()
            ),
        )
        monkeypatch.setattr(
            im,
            "_scheduler",
            SimpleNamespace(
                add_job=lambda *args, **kwargs: scheduled.append((args, kwargs))
            ),
        )

        result = im._day0_metar_source_clock_tick.__wrapped__()

        assert result == {"status": "WRITE_CONTENDED", "pending_reports": 1}
        mutex.release.assert_called_once_with()
        assert len(scheduled) == 1
        assert im._DAY0_METAR_PENDING_COMMITS[0][0] is prefetch

    def test_commit_retry_reuses_prefetch_after_writer_contention(
        self,
        monkeypatch,
    ):
        import src.ingest_main as im

        self._enable(monkeypatch)
        order: list[str] = []
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            freshness_status="fresh_fetch",
            reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )

        class _Emitter:
            def ledger_report_keys_loaded(self):
                return True

            def prefetch(self, **_kw):
                order.append("fetch")
                return prefetch

            def emit_prefetched(self, **kwargs):
                order.append("emit")
                kwargs["inserted_event_ids"].append("event-day0")
                return 1

        class _Conn:
            total_changes = 0

            def execute(self, sql, _params=()):
                if sql == "BEGIN IMMEDIATE":
                    order.append("begin")
                return self

            def commit(self):
                order.append("commit")

            def rollback(self):
                order.append("rollback")

            def close(self):
                order.append("close")

        class _Mutex:
            def __init__(self):
                self.attempts = iter((False, True))

            def acquire(self, *, timeout):
                order.append(f"acquire:{timeout}")
                return next(self.attempts)

            def release(self):
                order.append("release")

        emitter = _Emitter()
        mutex = _Mutex()
        scheduled = []
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr("src.state.db.get_world_connection", lambda **_kw: _Conn())
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: mutex)
        monkeypatch.setattr(
            im,
            "_scheduler",
            SimpleNamespace(add_job=lambda *args, **kwargs: scheduled.append((args, kwargs))),
        )
        monkeypatch.setattr(
            "src.runtime.reactor_wake.publish_reactor_wake",
            lambda **kwargs: order.append(f"wake:{','.join(kwargs['event_ids'])}"),
        )

        first = im._day0_metar_source_clock_tick.__wrapped__()
        retried = im._day0_metar_commit_retry_tick.__wrapped__()

        assert first == {"status": "WRITE_CONTENDED", "pending_reports": 1}
        assert retried == {
            "status": "COMMITTED",
            "pending_reports": 1,
            "events_emitted": 1,
        }
        assert order.count("fetch") == 1
        assert order.count("emit") == 1
        assert order.index("commit") < order.index("wake:event-day0")
        assert not im._DAY0_METAR_PENDING_COMMITS
        assert len(scheduled) == 1

    def test_commit_failure_does_not_advance_memo_before_retry(
        self,
        monkeypatch,
    ):
        import sqlite3

        import src.ingest_main as im

        self._enable(monkeypatch)
        prefetch = SimpleNamespace(
            ledger_reports=(object(),),
            eligible=((_wu_icao_city(), object(), "2026-06-12"),),
        )
        applied = []

        class _Emitter:
            def emit_prefetched(self, **kwargs):
                kwargs["deferred_memo_updates"][(
                    "Paris",
                    "2026-06-12",
                    "high",
                )] = (29, 29)
                kwargs["inserted_event_ids"].append("event-day0")
                return 1

            def apply_memo_updates(self, updates):
                applied.append(dict(updates))

        class _Conn:
            total_changes = 2

            def __init__(self, *, fail_commit):
                self.fail_commit = fail_commit
                self.rolled_back = False

            def execute(self, _sql, _params=()):
                return self

            def commit(self):
                if self.fail_commit:
                    raise sqlite3.OperationalError("database is locked")

            def rollback(self):
                self.rolled_back = True

            def close(self):
                return None

        connections = iter((_Conn(fail_commit=True), _Conn(fail_commit=False)))
        mutex = SimpleNamespace(
            acquire=lambda **_kw: True,
            release=lambda: None,
        )
        emitter = _Emitter()
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: next(connections),
        )
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: mutex)
        monkeypatch.setattr(
            "src.runtime.reactor_wake.publish_reactor_wake",
            lambda **_kw: None,
        )
        im._stage_day0_metar_commit(
            prefetch,
            received_at="2026-06-12T00:00:00+00:00",
            day0_is_tradeable=True,
        )

        first = im._commit_pending_day0_metar(origin="test")

        assert first == {"status": "WRITE_CONTENDED", "pending_reports": 1}
        assert applied == []
        assert im._DAY0_METAR_PENDING_COMMITS[0][0] is prefetch

        second = im._commit_pending_day0_metar(origin="retry")

        assert second == {
            "status": "COMMITTED",
            "pending_reports": 1,
            "events_emitted": 1,
        }
        assert applied == [
            {("Paris", "2026-06-12", "high"): (29, 29)}
        ]
        assert not im._DAY0_METAR_PENDING_COMMITS

    def test_commit_retry_without_pending_fact_does_no_db_work(
        self,
        monkeypatch,
    ):
        import src.ingest_main as im

        self._enable(monkeypatch)
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("empty commit retry must not open the DB")
            ),
        )

        scheduled = []
        monkeypatch.setattr(
            im,
            "_scheduler",
            SimpleNamespace(add_job=lambda *args, **kwargs: scheduled.append((args, kwargs))),
        )

        assert im._day0_metar_commit_retry_tick.__wrapped__() == {
            "status": "SOURCE_CURRENT"
        }
        assert scheduled == []

    def test_commit_staging_preserves_oldest_and_coalesces_latest(
        self,
        monkeypatch,
    ):
        import src.ingest_main as im

        self._enable(monkeypatch)
        prefetched = [SimpleNamespace(name=name) for name in ("old", "middle", "new")]
        for prefetch in prefetched:
            im._stage_day0_metar_commit(
                prefetch,
                received_at=prefetch.name,
                day0_is_tradeable=True,
            )

        assert len(im._DAY0_METAR_PENDING_COMMITS) == 2
        assert im._DAY0_METAR_PENDING_COMMITS[0][0] is prefetched[0]
        assert im._DAY0_METAR_PENDING_COMMITS[1][0] is prefetched[2]


class TestDay0OracleAnomalyTick:
    def test_current_cache_does_not_open_world_db(self, monkeypatch):
        import src.ingest_main as im

        TestDay0MetarSourceClockTick._enable(monkeypatch)
        emitter = SimpleNamespace(cached_anomaly_actions=lambda **_kw: ())
        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr(
            "src.state.db.get_world_connection",
            lambda **_kw: (_ for _ in ()).throw(
                AssertionError("DB opened without anomaly action")
            ),
        )

        result = im._day0_oracle_anomaly_tick.__wrapped__()

        assert result == {"status": "CURRENT"}

    def test_cached_action_commits_in_short_write_phase(self, monkeypatch):
        import src.ingest_main as im

        TestDay0MetarSourceClockTick._enable(monkeypatch)
        action = SimpleNamespace(action="clear", city="Tokyo", target_date="2026-06-12")
        emitter = SimpleNamespace(cached_anomaly_actions=lambda **_kw: (action,))
        order: list[str] = []

        class _Conn:
            def execute(self, sql, _params=()):
                if sql == "BEGIN IMMEDIATE":
                    order.append("begin")
                return self

            def commit(self):
                order.append("commit")

            def rollback(self):
                order.append("rollback")

            def close(self):
                order.append("close")

        class _Mutex:
            def acquire(self, *, timeout):
                order.append("acquire")
                return True

            def release(self):
                order.append("release")

        monkeypatch.setattr(im, "_day0_metar_emitter", lambda: emitter)
        monkeypatch.setattr("src.state.db.get_world_connection", lambda **_kw: _Conn())
        monkeypatch.setattr("src.state.db.world_write_mutex", lambda: _Mutex())
        monkeypatch.setattr(
            "src.data.day0_oracle_anomaly.apply_day0_oracle_anomaly_action",
            lambda applied, *, conn: order.append(f"apply:{applied.city}"),
        )

        result = im._day0_oracle_anomaly_tick.__wrapped__()

        assert result == {"status": "COMMITTED", "actions": 1}
        assert order.index("begin") < order.index("apply:Tokyo") < order.index("commit")
        assert order[-2:] == ["release", "close"]


# ---------------------------------------------------------------------------
# Prefix fusion: WU coverage-prover + METAR fresh tail (Denver 2026-06-12)
# ---------------------------------------------------------------------------

class TestWuPrefixMetarTailFusion:
    """The process-lifetime METAR memo can never prove local-day coverage after
    a daemon restart (WINDOW_INCOMPLETE forever) — the fused context must carry
    WU's coverage claim with the union extremes and METAR's freshness clock."""

    def _ctx(self, **kw):
        from src.data.observation_client import Day0ObservationContext
        base = dict(
            current_temp=88.0, high_so_far=90.0, low_so_far=60.0,
            source="wu_api", observation_time="2026-06-12T20:00:00+00:00",
            unit="F", coverage_status="OK", sample_count=14,
            first_sample_time="2026-06-12T06:10:00+00:00",
            last_sample_time="2026-06-12T20:00:00+00:00",
        )
        base.update(kw)
        if "sample_times_utc" not in kw:
            first = datetime.fromisoformat(str(base["first_sample_time"]))
            last = datetime.fromisoformat(str(base["observation_time"]))
            count = int(base["sample_count"])
            step = (last - first) / max(1, count - 1)
            base["sample_times_utc"] = tuple(
                first + step * index for index in range(count)
            )
        return Day0ObservationContext(**base)

    def test_fusion_unions_extremes_and_keeps_wu_coverage(self):
        from src.data.observation_client import _fuse_wu_prefix_with_same_station_tail
        wu = self._ctx()  # stale tail, full prefix, max 90
        metar = self._ctx(
            source="same_station_fast_tail", coverage_status="WINDOW_INCOMPLETE",
            high_so_far=91.0, low_so_far=75.0, current_temp=91.0,
            observation_time="2026-06-12T22:05:00+00:00", sample_count=8,
            first_sample_time="2026-06-12T21:40:00+00:00",
            provider_reported_time="day0_obs_source=same_station_fast_tail;age_s=120",
        )
        fused = _fuse_wu_prefix_with_same_station_tail(wu, metar)
        assert fused is not None
        assert fused.high_so_far == 91.0       # union max (METAR tail higher)
        assert fused.low_so_far == 60.0        # union min (WU morning low)
        assert fused.coverage_status == "OK"   # WU proves the prefix
        assert fused.current_temp == 91.0      # METAR freshness
        assert fused.observation_time == "2026-06-12T22:05:00+00:00"
        assert fused.source == "wu_api+same_station_fast_tail"
        assert "prefix=wu_api" in (fused.provider_reported_time or "")
        assert fused.sample_count == 22

    def test_no_fusion_when_wu_cannot_prove_prefix(self):
        from src.data.observation_client import _fuse_wu_prefix_with_same_station_tail
        wu = self._ctx(coverage_status="WINDOW_INCOMPLETE")
        metar = self._ctx(source="same_station_fast_tail", coverage_status="WINDOW_INCOMPLETE")
        assert _fuse_wu_prefix_with_same_station_tail(wu, metar) is None

    def test_no_fusion_on_unit_mismatch(self):
        from src.data.observation_client import _fuse_wu_prefix_with_same_station_tail
        wu = self._ctx(unit="F")
        metar = self._ctx(source="same_station_fast_tail", unit="C",
                          coverage_status="WINDOW_INCOMPLETE")
        assert _fuse_wu_prefix_with_same_station_tail(wu, metar) is None

    def test_get_current_observation_returns_fused_context(self, monkeypatch):
        """Integration: stale WU + incomplete METAR -> fused OK context, so the
        day0 quality gate no longer starves the monitor on settlement day."""
        import src.data.observation_client as oc
        from types import SimpleNamespace

        city = SimpleNamespace(
            name="Denver", timezone="America/Denver",
            settlement_source_type="wu_icao", settlement_unit="F",
            wu_station="KDEN", country_code="US",
        )
        ref = datetime(2026, 6, 12, 22, 10, tzinfo=UTC)
        wu = self._ctx()  # 20:00 obs -> 2.2h old (stale), coverage OK
        metar = self._ctx(
            source="same_station_fast_tail", coverage_status="WINDOW_INCOMPLETE",
            high_so_far=91.0, current_temp=91.0,
            observation_time="2026-06-12T22:05:00+00:00",
            provider_reported_time="day0_obs_source=same_station_fast_tail;age_s=120",
        )
        monkeypatch.setattr(oc, "_fetch_wu_observation", lambda *a, **k: wu)
        monkeypatch.setattr(
            oc, "_fetch_same_station_fast_tail_observation", lambda *a, **k: metar
        )
        out = oc.get_current_observation(city, target_date=date(2026, 6, 12),
                                         reference_time=ref)
        assert out.coverage_status == "OK"
        assert out.high_so_far == 91.0
        assert out.low_so_far == 60.0
        assert "prefix=wu_api" in (out.provider_reported_time or "")


class TestFastTickCityRotation:
    """No-data-holes machinery (operator law 2026-06-12): rate-limit truncation
    eats the tail of every run, so a FIXED city order permanently starves the
    same tail cities. The 15-min slot rotation must put every city at the front
    of the queue within len(cities) consecutive slots."""

    def test_every_city_reaches_front_within_full_rotation(self):
        cities = [f"c{i:02d}" for i in range(49)]
        fronts = set()
        base = 1_760_000_000  # any epoch
        for slot in range(len(cities)):
            ts = base + slot * 900
            offset = (ts // 900) % len(cities)
            rotated = cities[offset:] + cities[:offset]
            fronts.add(rotated[0])
            assert sorted(rotated) == sorted(cities)  # never drops a city
        assert fronts == set(cities)

    def test_rotation_formula_matches_ingest_main(self):
        """Pin the formula actually used in _k2_obs_fast_tick."""
        import inspect
        import src.ingest_main as im
        src_text = inspect.getsource(im._k2_obs_fast_tick)
        assert "% len(city_filter)" in src_text
        assert "city_filter[offset:] + city_filter[:offset]" in src_text
