"""Tests for config loader and city metadata."""

import json
import tempfile
from pathlib import Path

import pytest

from src.config import Settings, City, load_cities


def test_settings_loads_all_required_keys():
    s = Settings()
    assert s.mode == "paper"
    assert s.capital_base_usd == 150.0
    assert s["discovery"]["opening_hunt_interval_min"] == 30
    assert s["sizing"]["kelly_multiplier"] == 0.25


def test_settings_missing_key_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"capital_base_usd": 100}, f)
        f.flush()
        with pytest.raises(KeyError, match="Missing required config key"):
            Settings(path=Path(f.name))


def test_settings_no_fallback_pattern():
    """Settings must raise KeyError on missing nested keys, not return a default."""
    s = Settings()
    with pytest.raises(KeyError):
        _ = s["nonexistent_section"]


def test_cities_load():
    cities = load_cities()
    assert len(cities) == 16  # 16 cities in validated config
    names = {c.name for c in cities}
    assert "NYC" in names
    assert "London" in names
    assert "Paris" in names
    assert "Seoul" in names
    assert "Austin" in names


def test_city_settlement_units():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].settlement_unit == "F"
    assert by_name["London"].settlement_unit == "C"
    assert by_name["Paris"].settlement_unit == "C"
    assert by_name["Seoul"].settlement_unit == "C"
    assert by_name["Tokyo"].settlement_unit == "C"


def test_city_clusters():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].cluster == "US-Northeast"
    assert by_name["Chicago"].cluster == "US-Midwest"
    assert by_name["Atlanta"].cluster == "US-Southeast"
    assert by_name["London"].cluster == "Europe"
    assert by_name["Seoul"].cluster == "Asia"
    assert by_name["Denver"].cluster == "US-Mountain"


def test_city_has_timezone():
    cities = load_cities()
    for c in cities:
        assert c.timezone is not None
        assert "/" in c.timezone  # IANA format


def test_city_airport_coordinates():
    """Coordinates must be airport (settlement station), not city center."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}

    # NYC: LaGuardia (40.7772), NOT Manhattan (40.7128)
    nyc = by_name["NYC"]
    assert abs(nyc.lat - 40.7772) < 0.01
    assert abs(nyc.lon - (-73.8726)) < 0.01

    # Chicago: O'Hare (~41.97), NOT downtown (~41.88)
    chi = by_name["Chicago"]
    assert abs(chi.lat - 41.9742) < 0.01

    # London: Heathrow (~51.48), NOT city center (~51.51)
    lon = by_name["London"]
    assert abs(lon.lat - 51.4775) < 0.01


def test_city_wu_station_icao():
    """WU stations must be ICAO codes, not PWS IDs."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].wu_station == "KLGA"
    assert by_name["Chicago"].wu_station == "KORD"
    assert by_name["Seattle"].wu_station == "KSEA"
    assert by_name["London"].wu_station == "EGLL"


def test_city_aliases():
    """Each city should have aliases for market title matching."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert "New York City" in by_name["NYC"].aliases
    assert "LA" in by_name["Los Angeles"].aliases
    assert "SF" in by_name["San Francisco"].aliases
