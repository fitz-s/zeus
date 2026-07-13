"""Runtime contract tests for Day0 observation context propagation.

Legacy-pipeline retirement (Phase 2, 2026-07-06): the two
``test_execute_discovery_phase_*`` tests that used to live here (validating
the LEGACY day0 mode-axis dispatch: mode -> fetch, pinned to
``ZEUS_MARKET_PHASE_DISPATCH=0`` per the 2026-05-04 A6 audit) were removed
alongside ``src.engine.cycle_runtime.execute_discovery_phase`` itself. The
remaining tests below cover ``monitor_refresh._fetch_day0_observation``,
``observation_client.get_current_observation``, and ``day0_nowcast_context``
directly — none of which depend on the discovery pipeline.
"""
# Created: 2026-04-30
# Last reused/audited: 2026-07-06

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import src.data.observation_client as observation_client
import src.engine.monitor_refresh as monitor_refresh
from src.config import City
from src.contracts.exceptions import ObservationUnavailableError
from src.signal.forecast_uncertainty import day0_nowcast_context


def test_monitor_refresh_day0_helper_passes_target_date_and_reference_time(monkeypatch):
    captured: dict[str, object] = {}

    def getter(city, target_date=None, reference_time=None):
        captured["city"] = city
        captured["target_date"] = target_date
        captured["reference_time"] = reference_time
        return {"high_so_far": 72.0, "current_temp": 70.0, "observation_time": "2026-04-01T12:00:00+00:00"}

    monkeypatch.setattr(monitor_refresh, "get_current_observation", getter)
    city = SimpleNamespace(name="NYC")

    result = monitor_refresh._fetch_day0_observation(city, date(2026, 4, 1))

    assert result["high_so_far"] == 72.0
    assert captured["target_date"] == date(2026, 4, 1)
    assert isinstance(captured["reference_time"], datetime)
    assert captured["reference_time"].tzinfo is not None


def test_monitor_refresh_day0_helper_falls_back_for_legacy_getter(monkeypatch):
    captured = {"legacy_calls": 0}

    def legacy_getter(city):
        captured["legacy_calls"] += 1
        return {"high_so_far": 72.0, "current_temp": 70.0, "observation_time": "2026-04-01T12:00:00+00:00"}

    monkeypatch.setattr(monitor_refresh, "get_current_observation", legacy_getter)

    result = monitor_refresh._fetch_day0_observation(SimpleNamespace(name="NYC"), date(2026, 4, 1))

    assert result["current_temp"] == 70.0
    assert captured["legacy_calls"] == 1


def _city(**overrides) -> City:
    base = {
        "name": "Test City",
        "lat": 40.0,
        "lon": -73.0,
        "timezone": "UTC",
        "settlement_unit": "F",
        "cluster": "test",
        "wu_station": "KNYC",
        "settlement_source_type": "wu_icao",
    }
    base.update(overrides)
    return City(**base)


def test_day0_executable_observation_rejects_unsupported_source_class(monkeypatch):
    def should_not_fetch_wu(*args, **kwargs):
        raise AssertionError("non-WU settlement sources must not call WU geocode")

    monkeypatch.setattr(observation_client, "_fetch_wu_observation", should_not_fetch_wu)

    with pytest.raises(ObservationUnavailableError, match="unsupported"):
        observation_client.get_current_observation(
            _city(name="Unsupported", settlement_source_type="noaa", wu_station=""),
            target_date=date(2026, 4, 1),
            reference_time=datetime(2026, 4, 1, 16, tzinfo=timezone.utc),
        )


def test_day0_hko_observation_reads_official_since_midnight_extrema(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            temp_current REAL,
            running_max REAL,
            running_min REAL,
            station_id TEXT,
            temp_unit TEXT,
            imported_at TEXT,
            source_role TEXT,
            authority TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO observation_instants (
            city, target_date, source, utc_timestamp, temp_current,
            running_max, running_min, station_id, temp_unit, imported_at,
            source_role, authority, data_version, training_allowed,
            causality_status, provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "Hong Kong", "2026-06-17", "hko_hourly_accumulator",
                "2026-06-17T12:00:00+00:00", 34.0, 33.8, 29.0,
                "HKO", "C", "2026-06-17T12:00:05+00:00",
                "runtime_monitoring", "ICAO_STATION_NATIVE",
                "v1.hk-extrema", 0, "OK",
                '{"observation_basis":"hko_since_midnight_extrema_1min_mean"}',
            ),
            (
                "Hong Kong", "2026-06-17", "hko_hourly_accumulator",
                "2026-06-17T11:00:00+00:00", 34.0, 34.0, 34.0,
                "HKO", "C", "2026-06-17T11:00:05+00:00",
                "runtime_monitoring", "ICAO_STATION_NATIVE",
                "v1.hk-legacy", 0, "REQUIRES_SOURCE_REAUDIT", "{}",
            ),
        ],
    )
    conn.commit()

    monkeypatch.setattr(
        "src.state.db.get_world_connection_read_only",
        lambda: conn,
    )

    obs = observation_client.get_current_observation(
        _city(
            name="Hong Kong",
            timezone="Asia/Hong_Kong",
            settlement_source_type="hko",
            settlement_unit="C",
            wu_station="",
        ),
        target_date=date(2026, 6, 17),
        reference_time=datetime(2026, 6, 17, 12, 30, tzinfo=timezone.utc),
    )

    assert obs.source == "hko_hourly_accumulator"
    assert obs.station_id == "HKO"
    assert obs.unit == "C"
    assert obs.current_temp == pytest.approx(34.0)
    assert obs.high_so_far == pytest.approx(33.8)
    assert obs.low_so_far == pytest.approx(29.0)
    assert obs.sample_count == 1
    assert obs.first_sample_time is None
    assert obs.last_sample_time == "2026-06-17T12:00:00+00:00"
    assert obs.coverage_status == "LOW_COVERAGE"


def test_day0_wu_observation_rejects_station_mismatch(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "observations": [
                    {
                        "temp": 72.0,
                        "valid_time_gmt": 1775059200,
                        "obs_id": "KJFK",
                    }
                ]
            }

    monkeypatch.setattr(observation_client.httpx, "get", lambda *args, **kwargs: Response())

    with pytest.raises(ObservationUnavailableError, match="All observation providers failed"):
        observation_client.get_current_observation(
            _city(wu_station="KNYC"),
            target_date=date(2026, 4, 1),
            reference_time=datetime(2026, 4, 1, 16, 30, tzinfo=timezone.utc),
        )


def test_day0_wu_observation_preserves_station_and_iso_time(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "observations": [
                    {
                        "temp": 70.0,
                        "valid_time_gmt": 1775055600,
                        "obs_id": "KNYC",
                    },
                    {
                        "temp": 74.0,
                        "valid_time_gmt": 1775059200,
                        "obs_id": "KNYC",
                    },
                ]
            }

    monkeypatch.setattr(observation_client.httpx, "get", lambda *args, **kwargs: Response())

    obs = observation_client.get_current_observation(
        _city(wu_station="KNYC"),
        target_date=date(2026, 4, 1),
        reference_time=datetime(2026, 4, 1, 16, 30, tzinfo=timezone.utc),
    )

    assert obs.source == "wu_api"
    assert obs.station_id == "KNYC"
    assert obs.sample_count == 2
    assert obs.high_so_far == 74.0
    assert obs.low_so_far == 70.0
    assert obs.observation_time.endswith("+00:00")


def test_day0_nowcast_context_parses_epoch_timestamps():
    observed_at = datetime(2026, 4, 1, 15, tzinfo=timezone.utc)
    current_at = datetime(2026, 4, 1, 15, 30, tzinfo=timezone.utc)

    context = day0_nowcast_context(
        hours_remaining=2.0,
        observation_source="wu_api",
        observation_time=int(observed_at.timestamp()),
        current_utc_timestamp=str(int(current_at.timestamp())),
    )

    assert context["age_hours"] == pytest.approx(0.5)
    assert context["fresh_observation"] is True
    assert context["blend_weight"] > 0.0


@pytest.mark.parametrize(
    "source",
    ["wu_icao_history", "hko_hourly_accumulator", "ogimet_metar_ltfm"],
)
def test_day0_nowcast_context_trusts_canonical_observation_sources(source):
    observed_at = datetime(2026, 7, 1, 15, tzinfo=timezone.utc)
    current_at = datetime(2026, 7, 1, 15, 30, tzinfo=timezone.utc)

    context = day0_nowcast_context(
        hours_remaining=2.0,
        observation_source=source,
        observation_time=observed_at.isoformat(),
        current_utc_timestamp=current_at.isoformat(),
    )

    assert context["trusted_source"] is True
    assert context["fresh_observation"] is True
    assert context["blend_weight"] > 0.0
