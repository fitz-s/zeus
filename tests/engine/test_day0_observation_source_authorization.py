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
)


def _city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def test_static_noaa_set_no_longer_pins_individual_stations():
    """A station name in the literal set is the defect shape; keep it out."""
    static = DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE["noaa"]
    assert static == frozenset({"aviationweather_metar"})

