# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Authority basis: Phase 1D forecast source policy + first-principles safety implementation 2026-04-30
"""Tests for ensemble client caching and request behavior."""

from datetime import datetime, timezone

import numpy as np
import pytest

from src.config import City
from src.data import ensemble_client
from src.data.forecast_source_registry import SourceNotEnabled


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="NYC",
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

    first = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=4,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )
    second = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=4,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )

    assert calls["n"] == 1
    assert first is not None and second is not None
    np.testing.assert_array_equal(first["members_hourly"], second["members_hourly"])
    assert first["degradation_level"] == "DEGRADED_FORECAST_FALLBACK"
    assert first["forecast_source_role"] == "monitor_fallback"


def test_fetch_ensemble_blocks_openmeteo_for_entry_primary_role():
    ensemble_client._ENSEMBLE_CACHE.clear()

    with pytest.raises(SourceNotEnabled, match="entry_primary"):
        ensemble_client.fetch_ensemble(NYC, forecast_days=4, model="ecmwf_ifs025", role="entry_primary")


def test_fetch_ensemble_cache_key_includes_model(monkeypatch):
    ensemble_client._ENSEMBLE_CACHE.clear()
    calls = {"n": 0}

    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda endpoint="": None)

    def _get(*args, **kwargs):
        calls["n"] += 1
        return _Response(_payload())

    monkeypatch.setattr(ensemble_client.httpx, "get", _get)

    ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=4,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )
    ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=4,
        model="gfs025",
        role="monitor_fallback",
    )

    assert calls["n"] == 2


def test_validate_ensemble_rejects_insufficient_finite_members_for_required_hours():
    members = np.ones((51, 4), dtype=np.float64)
    members[:2, 1] = np.nan
    result = {
        "members_hourly": members,
        "n_members": 51,
    }

    assert not ensemble_client.validate_ensemble(
        result,
        expected_members=51,
        required_hour_indices=[1, 2],
    )


def test_validate_ensemble_ignores_non_target_hour_nans_for_required_hours():
    members = np.ones((51, 4), dtype=np.float64)
    members[:, 3] = np.nan
    result = {
        "members_hourly": members,
        "n_members": 51,
    }

    assert ensemble_client.validate_ensemble(
        result,
        expected_members=51,
        required_hour_indices=[1, 2],
    )


def test_validate_ensemble_rejects_extra_nonfinite_rows_for_required_hours():
    members = np.ones((52, 4), dtype=np.float64)
    members[51, 1] = np.nan
    result = {
        "members_hourly": members,
        "n_members": 52,
    }

    assert not ensemble_client.validate_ensemble(
        result,
        expected_members=51,
        required_hour_indices=[1, 2],
    )


def test_validate_ensemble_ignores_irrelevant_hour_nans_for_required_hours():
    members = np.ones((51, 10), dtype=np.float64)
    members[:, [0, 3, 4, 5, 6, 7, 8, 9]] = np.nan
    result = {
        "members_hourly": members,
        "n_members": 51,
    }

    assert ensemble_client.validate_ensemble(
        result,
        expected_members=51,
        required_hour_indices=[1, 2],
    )


def test_fetch_ensemble_cache_key_includes_role(monkeypatch):
    ensemble_client._ENSEMBLE_CACHE.clear()
    calls = {"n": 0}

    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda endpoint="": None)

    def _get(*args, **kwargs):
        calls["n"] += 1
        return _Response(_payload())

    monkeypatch.setattr(ensemble_client.httpx, "get", _get)

    monitor = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=4,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )
    diagnostic = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=4,
        model="ecmwf_ifs025",
        role="diagnostic",
    )

    assert calls["n"] == 2
    assert monitor is not None and diagnostic is not None
    assert monitor["forecast_source_role"] == "monitor_fallback"
    assert diagnostic["forecast_source_role"] == "diagnostic"
