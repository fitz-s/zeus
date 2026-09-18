"""Per-station channel authorization must be derived, never enumerated.

`DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE["noaa"]` listed three ogimet
channels by name — `ogimet_metar_ltfm`, `_uuww`, `_llbg` — which were the only NOAA cities
when it was written. After the 2026-09-12 migration 48 cities settle off NOAA, so the set
authorized 3 stations and rejected the other 45 for the IDENTICAL source shape: Istanbul and
Moscow passed only because their ICAO codes had been typed in, while Ankara, Madrid and
Denver were refused. `_day0_observation_source_rejection_reason` is what the executable
entry lane consults, so a refusal there blocks the entry outright.

The settlement page `noaa_wrh_<icao>` was absent for every city, even though
`day0_authority.day0_evidence_finality` classifies it as a monotone settlement bound — the
strongest class it has. Two layers disagreed about the same reading.

A channel name is a function of the city's own station, so it is derived here. The
derivation stays strictly per-city: admitting another city's station would be a
cross-station contamination worse than the over-restriction it replaces.
"""
from __future__ import annotations

import pytest

from src.config import cities_by_name, load_cities
from src.engine.evaluator import (
    DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE,
    _day0_observation_source_rejection_reason,
    _day0_station_scoped_observation_sources,
)


def _city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def _authorized(city, source: str) -> bool:
    return _day0_observation_source_rejection_reason(city, {"source": source}) is None


def test_every_noaa_city_may_use_its_own_mirror_and_settlement_page():
    """The defect: only the three typed-in stations were authorized."""
    checked = 0
    for city in load_cities():
        if str(getattr(city, "settlement_source_type", "")).strip() != "noaa":
            continue
        station = str(getattr(city, "wu_station", "") or "").lower()
        if not station:
            continue
        checked += 1
        assert _authorized(city, f"ogimet_metar_{station}"), city.name
        assert _authorized(city, f"noaa_wrh_{station}"), city.name
        assert _authorized(city, "aviationweather_metar"), city.name
    assert checked >= 40, f"expected the migrated NOAA universe, saw {checked}"


def test_a_noaa_city_never_accepts_another_citys_station():
    """The derivation must not widen into cross-station contamination."""
    ankara = _city("Ankara")
    madrid = _city("Madrid")
    ankara_station = str(ankara.wu_station).lower()
    madrid_station = str(madrid.wu_station).lower()
    assert ankara_station != madrid_station
    assert not _authorized(ankara, f"ogimet_metar_{madrid_station}")
    assert not _authorized(ankara, f"noaa_wrh_{madrid_station}")
    assert not _authorized(madrid, f"ogimet_metar_{ankara_station}")
    assert not _authorized(madrid, f"noaa_wrh_{ankara_station}")


def test_noaa_city_still_refuses_the_wu_settlement_channel():
    """A family may not read another family's settlement product."""
    assert not _authorized(_city("Ankara"), "wu_icao_history")


def test_wu_city_is_unchanged_by_the_derivation():
    """Widening the NOAA family must not touch the wu_icao contract."""
    city = _city("Auckland")
    if str(getattr(city, "settlement_source_type", "")).strip() != "wu_icao":
        pytest.skip("Auckland is no longer a wu_icao city")
    station = str(city.wu_station).lower()
    assert _authorized(city, "wu_icao_history")
    assert not _authorized(city, f"ogimet_metar_{station}")
    assert not _authorized(city, f"noaa_wrh_{station}")


def test_derivation_is_scoped_to_the_noaa_family_and_needs_a_station():
    """Other families derive nothing; a missing station derives nothing."""
    assert _day0_station_scoped_observation_sources("noaa", "LTAC") == frozenset(
        {"ogimet_metar_ltac", "noaa_wrh_ltac"}
    )
    assert _day0_station_scoped_observation_sources("noaa", "") == frozenset()
    assert _day0_station_scoped_observation_sources("wu_icao", "NZAA") == frozenset()
    assert _day0_station_scoped_observation_sources("hko", "VHHH") == frozenset()


def test_static_noaa_set_no_longer_pins_individual_stations():
    """A station name in the literal set is the defect shape; keep it out."""
    static = DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE["noaa"]
    assert static == frozenset({"aviationweather_metar"})


def test_settlement_page_authority_and_authorization_agree():
    """The page is a monotone settlement bound; the entry lane must admit it."""
    from src.events.day0_authority import (
        DAY0_ABSORBING_FINALITIES,
        day0_evidence_finality,
    )

    city = _city("Ankara")
    station = str(city.wu_station).lower()
    page = f"noaa_wrh_{station}"
    assert day0_evidence_finality({"settlement_source": page}) in DAY0_ABSORBING_FINALITIES
    assert _authorized(city, page)
