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
    assert len(cities) == 10
    names = {c.name for c in cities}
    assert "NYC" in names
    assert "London" in names
    assert "Paris" in names


def test_city_settlement_units():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].settlement_unit == "F"
    assert by_name["London"].settlement_unit == "C"
    assert by_name["Paris"].settlement_unit == "C"


def test_city_clusters():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].cluster == "US-Northeast"
    assert by_name["Chicago"].cluster == "US-Midwest"
    assert by_name["Atlanta"].cluster == "US-Southeast"
    assert by_name["London"].cluster == "Europe"


def test_city_has_timezone():
    cities = load_cities()
    for c in cities:
        assert c.timezone is not None
        assert "/" in c.timezone  # IANA format
