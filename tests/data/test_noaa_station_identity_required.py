"""A city's settlement station is per-city data; no default may stand in for it.

`source_priority_for_city` fell back, for a NOAA city with no station, to the ogimet
entries of `_DEFAULT_SOURCE_PRIORITY` — `ltfm`, `uuww`, `llbg`, the only NOAA cities when
that tuple was written. The reader would then have answered with ANOTHER city's
temperature, which is worse than answering with nothing. And `validate_cities_config`
required `wu_station` only for `wu_icao`, so a stationless NOAA city was a configurable
state rather than a rejected one — both families name their settlement station in the same
field, because the WU history page and the weather.gov station page read the same ICAO id.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config import City, cities_by_name, load_cities, validate_cities_config
from src.data.day0_observation_reader import source_priority_for_city


def _city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def test_stationless_noaa_city_yields_no_source_rather_than_a_foreign_one():
    """Absence must read as absence, never as another city's station."""
    stationless = SimpleNamespace(
        name="Nowhere",
        wu_station="",
        settlement_source_type="noaa",
        timezone="UTC",
    )
    assert source_priority_for_city(stationless, "2026-09-17") == ()


def test_each_family_resolves_to_its_own_settlement_channel():
    """The priority is derived from the city, per family."""
    ankara = _city("Ankara")
    assert source_priority_for_city(ankara, "2026-09-17") == (
        f"ogimet_metar_{str(ankara.wu_station).lower()}",
    )
    auckland = _city("Auckland")
    if str(getattr(auckland, "settlement_source_type", "")).strip() == "wu_icao":
        assert source_priority_for_city(auckland, "2026-09-17") == ("wu_icao_history",)
    hong_kong = _city("Hong Kong")
    if str(getattr(hong_kong, "settlement_source_type", "")).strip() == "hko":
        assert source_priority_for_city(hong_kong, "2026-09-17") == (
            "hko_hourly_accumulator",
        )


def test_no_noaa_city_ever_resolves_to_another_citys_station():
    """Every configured NOAA city must name only its own mirror."""
    for city in load_cities():
        if str(getattr(city, "settlement_source_type", "")).strip() != "noaa":
            continue
        station = str(getattr(city, "wu_station", "") or "").lower()
        assert station, f"{city.name} has no settlement station"
        assert source_priority_for_city(city, "2026-09-17") == (
            f"ogimet_metar_{station}",
        ), city.name


def test_validation_requires_a_station_for_a_noaa_city():
    """A stationless NOAA city must be a rejected config, not a silent one."""
    stationless = City(
        **{
            **{
                field: getattr(_city("Ankara"), field)
                for field in _city("Ankara").__dataclass_fields__
            },
            "name": "Stationless",
            "wu_station": "",
        }
    )
    warnings = validate_cities_config([stationless])
    assert any("wu_station is empty" in w for w in warnings), warnings


def test_the_live_config_has_no_station_warning():
    """The shipped config must satisfy the widened rule."""
    assert not [w for w in validate_cities_config() if "wu_station is empty" in w]
