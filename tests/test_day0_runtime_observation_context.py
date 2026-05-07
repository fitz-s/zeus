"""Runtime contract tests for Day0 observation context propagation.

A6 audit (2026-05-04): the two ``test_execute_discovery_phase_*`` tests
below validate the LEGACY day0 dispatch (mode → fetch). After A6 flipped
``ZEUS_MARKET_PHASE_DISPATCH`` default to ON, the runtime instead routes
the fetch decision through ``should_fetch_settlement_day_observation``,
which (correctly) refuses to fetch when the market is already in
POST_TRADING phase. The test fixtures use ``target_date=2026-04-01`` and
``decision_time=2026-04-01T15:30Z`` — POST_TRADING for NYC. Under flag
ON the getter is never called and the captured fields stay empty.

These two tests are pinned to flag=OFF because they assert the
legacy-mode-axis dispatch contract. Phase-axis equivalents should be
added in a future packet (PLAN_v3 §6 follow-up); they need fixtures
whose decision_time falls inside the SETTLEMENT_DAY window.
"""
# Created: 2026-04-30
# Last reused/audited: 2026-05-04
# Authority basis: Day0 runtime observation context relationship protection;
#   A6 follow-up (rebuild fixes branch) — pin the two flag-sensitive tests
#   to legacy-mode-axis until phase-axis fixtures land.

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import src.data.observation_client as observation_client
import src.engine.cycle_runtime as cycle_runtime
import src.engine.monitor_refresh as monitor_refresh
from src.config import City
from src.contracts.exceptions import ObservationUnavailableError
from src.engine.discovery_mode import DiscoveryMode
from src.signal.forecast_uncertainty import day0_nowcast_context


def test_execute_discovery_phase_passes_target_date_and_decision_time_to_day0_getter(monkeypatch):
    # Pin legacy mode-axis dispatch — see module docstring §A6 audit.
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    captured: dict[str, object] = {}

    def getter(city, target_date=None, reference_time=None):
        captured["city"] = city
        captured["target_date"] = target_date
        captured["reference_time"] = reference_time
        return {"high_so_far": 72.0, "current_temp": 70.0, "observation_time": "2026-04-01T12:00:00+00:00"}

    deps = SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.DAY0_CAPTURE: {}},
        DiscoveryMode=DiscoveryMode,
        get_current_observation=getter,
        find_weather_markets=lambda **kwargs: [{
            "city": SimpleNamespace(name="NYC", timezone="America/New_York"),
            "target_date": "2026-04-01",
            "hours_since_open": 1.0,
            "hours_to_resolution": 6.0,
            "temperature_metric": "high",
            "outcomes": [],
        }],
        evaluate_candidate=lambda *args, **kwargs: [],
        get_last_scan_authority=lambda: "VERIFIED",
        logger=SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None),
        NoTradeCase=object,
    )

    summary = {"candidates": 0, "no_trades": 0, "trades": 0}
    decision_time = datetime(2026, 4, 1, 15, 30, tzinfo=timezone.utc)

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_discovery_phase(
        conn=None,
        clob=None,
        portfolio=None,
        artifact=SimpleNamespace(),
        tracker=None,
        limits=None,
        mode=DiscoveryMode.DAY0_CAPTURE,
        summary=summary,
        entry_bankroll=0.0,
        decision_time=decision_time,
        env="legacy_env",
        deps=deps,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert captured["target_date"] == "2026-04-01"
    assert captured["reference_time"] == decision_time


def test_execute_discovery_phase_falls_back_for_legacy_day0_getter_signature(monkeypatch):
    # Pin legacy mode-axis dispatch — see module docstring §A6 audit.
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    captured = {"legacy_calls": 0}

    def legacy_getter(city):
        captured["legacy_calls"] += 1
        return {"high_so_far": 72.0, "current_temp": 70.0, "observation_time": "2026-04-01T12:00:00+00:00"}

    deps = SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.DAY0_CAPTURE: {}},
        DiscoveryMode=DiscoveryMode,
        get_current_observation=legacy_getter,
        find_weather_markets=lambda **kwargs: [{
            "city": SimpleNamespace(name="NYC", timezone="America/New_York"),
            "target_date": "2026-04-01",
            "hours_since_open": 1.0,
            "hours_to_resolution": 6.0,
            "temperature_metric": "high",
            "outcomes": [],
        }],
        evaluate_candidate=lambda *args, **kwargs: [],
        get_last_scan_authority=lambda: "VERIFIED",
        logger=SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None),
        NoTradeCase=object,
    )

    cycle_runtime.execute_discovery_phase(
        conn=None,
        clob=None,
        portfolio=None,
        artifact=SimpleNamespace(),
        tracker=None,
        limits=None,
        mode=DiscoveryMode.DAY0_CAPTURE,
        summary={"candidates": 0, "no_trades": 0, "trades": 0},
        entry_bankroll=0.0,
        decision_time=datetime(2026, 4, 1, 15, 30, tzinfo=timezone.utc),
        env="legacy_env",
        deps=deps,
    )

    assert captured["legacy_calls"] == 1


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


def test_day0_executable_observation_rejects_non_wu_source_class(monkeypatch):
    def should_not_fetch_wu(*args, **kwargs):
        raise AssertionError("non-WU settlement sources must not call WU geocode")

    monkeypatch.setattr(observation_client, "_fetch_wu_observation", should_not_fetch_wu)

    with pytest.raises(ObservationUnavailableError, match="unsupported"):
        observation_client.get_current_observation(
            _city(name="Hong Kong", settlement_source_type="hko", wu_station=""),
            target_date=date(2026, 4, 1),
            reference_time=datetime(2026, 4, 1, 16, tzinfo=timezone.utc),
        )


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
