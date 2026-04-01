"""Tests for ensemble client caching and request behavior."""

from datetime import datetime, timezone

import numpy as np

from src.config import City
from src.data import ensemble_client


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="US-Northeast",
    settlement_unit="F",
    wu_station="KLGA",
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _payload():
    return {
        "hourly": {
            "time": ["2026-04-01T00:00", "2026-04-01T01:00"],
            "temperature_2m": [40.0, 41.0],
            "temperature_2m_member01": [39.0, 40.0],
            "temperature_2m_member02": [41.0, 42.0],
        }
    }


def test_fetch_ensemble_uses_cache(monkeypatch):
    ensemble_client._ENSEMBLE_CACHE.clear()
    calls = {"n": 0}

    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda endpoint="": None)

    def _get(*args, **kwargs):
        calls["n"] += 1
        return _Response(_payload())

    monkeypatch.setattr(ensemble_client.httpx, "get", _get)

    first = ensemble_client.fetch_ensemble(NYC, forecast_days=4, model="ecmwf_ifs025")
    second = ensemble_client.fetch_ensemble(NYC, forecast_days=4, model="ecmwf_ifs025")

    assert calls["n"] == 1
    assert first is not None and second is not None
    np.testing.assert_array_equal(first["members_hourly"], second["members_hourly"])


def test_fetch_ensemble_cache_key_includes_model(monkeypatch):
    ensemble_client._ENSEMBLE_CACHE.clear()
    calls = {"n": 0}

    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda endpoint="": None)

    def _get(*args, **kwargs):
        calls["n"] += 1
        return _Response(_payload())

    monkeypatch.setattr(ensemble_client.httpx, "get", _get)

    ensemble_client.fetch_ensemble(NYC, forecast_days=4, model="ecmwf_ifs025")
    ensemble_client.fetch_ensemble(NYC, forecast_days=4, model="gfs025")

    assert calls["n"] == 2
