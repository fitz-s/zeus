"""Regression tests for observation/temperature contracts on pre-live."""

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.config import load_cities
from src.data.observation_client import _resolve_observation_context, _select_local_day_samples


def test_load_cities_preserves_zero_unit_specific_diurnal_amplitude(tmp_path):
    path = tmp_path / "cities.json"
    path.write_text(
        json.dumps(
            {
                "cities": [
                    {
                        "name": "Test City",
                        "lat": 1,
                        "lon": 2,
                        "timezone": "UTC",
                        "unit": "F",
                        "cluster": "US-Test",
                        "wu_station": "KTEST",
                        "diurnal_amplitude_f": 0.0,
                        "diurnal_amplitude_c": 9.0,
                    }
                ]
            }
        )
    )

    cities = load_cities(path=path)
    assert cities[0].diurnal_amplitude == 0.0


def test_resolve_observation_context_uses_city_local_date():
    class DummyCity:
        timezone = "Asia/Tokyo"

    target_day, _, reference_local, _ = _resolve_observation_context(
        DummyCity(), target_date=None, reference_time=datetime(2026, 4, 1, 0, 30, tzinfo=timezone.utc)
    )

    assert target_day == date(2026, 4, 1)
    assert reference_local.date() == date(2026, 4, 1)


def test_select_local_day_samples_excludes_previous_day_and_future_hours():
    tz = ZoneInfo("America/New_York")
    target_day = date(2026, 4, 1)
    reference_local = datetime(2026, 4, 1, 12, 0, tzinfo=tz)

    samples = [
        (70.0, datetime(2026, 3, 31, 23, 0, tzinfo=tz), "prev-day"),
        (72.0, datetime(2026, 4, 1, 8, 0, tzinfo=tz), "morning"),
        (75.0, datetime(2026, 4, 1, 18, 0, tzinfo=tz), "future"),
    ]

    selected = _select_local_day_samples(samples, target_day, reference_local)

    assert [row[2] for row in selected] == ["morning"]
    assert max(temp for temp, _, _ in selected) == 72.0
