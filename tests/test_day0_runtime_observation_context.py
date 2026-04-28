"""Runtime contract tests for Day0 observation context propagation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import src.engine.cycle_runtime as cycle_runtime
import src.engine.monitor_refresh as monitor_refresh
from src.engine.discovery_mode import DiscoveryMode


def test_execute_discovery_phase_passes_target_date_and_decision_time_to_day0_getter():
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
            "city": SimpleNamespace(name="NYC"),
            "target_date": "2026-04-01",
            "hours_since_open": 1.0,
            "hours_to_resolution": 6.0,
            "temperature_metric": "high",
            "outcomes": [],
        }],
        evaluate_candidate=lambda *args, **kwargs: [],
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
        env="paper",
        deps=deps,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert captured["target_date"] == "2026-04-01"
    assert captured["reference_time"] == decision_time


def test_execute_discovery_phase_falls_back_for_legacy_day0_getter_signature():
    captured = {"legacy_calls": 0}

    def legacy_getter(city):
        captured["legacy_calls"] += 1
        return {"high_so_far": 72.0, "current_temp": 70.0, "observation_time": "2026-04-01T12:00:00+00:00"}

    deps = SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.DAY0_CAPTURE: {}},
        DiscoveryMode=DiscoveryMode,
        get_current_observation=legacy_getter,
        find_weather_markets=lambda **kwargs: [{
            "city": SimpleNamespace(name="NYC"),
            "target_date": "2026-04-01",
            "hours_since_open": 1.0,
            "hours_to_resolution": 6.0,
            "temperature_metric": "high",
            "outcomes": [],
        }],
        evaluate_candidate=lambda *args, **kwargs: [],
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
        env="paper",
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
