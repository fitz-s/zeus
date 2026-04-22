# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 antibody A3 (.omc/plans/observation-instants-
#                  migration-iter3.md L121); step2 Phase 0 file #8.
"""Antibody A3: tier resolver must match cities.json at runtime.

The resolver reads ``cities_by_name`` at import time. These tests verify
that what the resolver produces exactly matches what the current
``config/cities.json`` declares, so a silent config drift (e.g. adding a
city without a ``settlement_source_type``) cannot leak through.

The tests also pin the HK/VHHH-error lesson: Hong Kong MUST be
``Tier.HKO_NATIVE``, never WU, never OpenMeteo.
"""
from __future__ import annotations

import pytest

from src.config import cities_by_name
from src.data.tier_resolver import (
    ALLOWED_SOURCES_BY_CITY,
    TIER_ALLOWED_SOURCES,
    TIER_SCHEDULE,
    Tier,
    UnsupportedTierError,
    allowed_sources_for_city,
    allowed_sources_for_tier,
    cities_in_tier,
    expected_source_for_city,
    tier_for_city,
)


def test_every_city_maps_to_exactly_one_tier():
    """I1: no city is missing, none has None."""
    missing = [name for name in cities_by_name if name not in TIER_SCHEDULE]
    assert missing == [], f"cities without tier: {missing}"
    for name, tier in TIER_SCHEDULE.items():
        assert isinstance(tier, Tier), f"{name} has non-Tier value {tier!r}"


def test_wu_icao_tier_matches_sstype_filter():
    """I2: Tier.WU_ICAO ↔ settlement_source_type == 'wu_icao'."""
    expected = {c.name for c in cities_by_name.values() if c.settlement_source_type == "wu_icao"}
    actual = set(cities_in_tier(Tier.WU_ICAO))
    assert actual == expected, f"WU_ICAO mismatch: +{actual - expected} -{expected - actual}"


def test_ogimet_metar_tier_matches_sstype_filter():
    """I3: Tier.OGIMET_METAR ↔ settlement_source_type == 'noaa'."""
    expected = {c.name for c in cities_by_name.values() if c.settlement_source_type == "noaa"}
    actual = set(cities_in_tier(Tier.OGIMET_METAR))
    assert actual == expected, f"OGIMET_METAR mismatch: +{actual - expected} -{expected - actual}"


def test_hko_native_tier_is_exactly_hong_kong():
    """I4: the only Tier.HKO_NATIVE city is Hong Kong.

    Pinned separately from I3 because HK is the highest-risk category:
    the airport_name='Hong Kong Observatory Headquarters' is NOT an
    airport but the settlement station identity, and mis-routing to
    VHHH WU is the exact bug plan v3 exists to prevent.
    """
    assert set(cities_in_tier(Tier.HKO_NATIVE)) == {"Hong Kong"}
    assert tier_for_city("Hong Kong") is Tier.HKO_NATIVE


def test_hong_kong_never_routed_to_wu():
    """Explicit regression test for the VHHH/WU mis-route category error."""
    assert tier_for_city("Hong Kong") is not Tier.WU_ICAO
    assert tier_for_city("Hong Kong") is not Tier.OGIMET_METAR


def test_unknown_city_raises():
    """Resolving an unregistered city name must fail fast."""
    with pytest.raises(UnsupportedTierError):
        tier_for_city("Atlantis")


def test_no_openmeteo_tier_member():
    """Structural invariant: Tier enum must NOT include an OpenMeteo tier.

    If this test ever fails, someone has reintroduced grid-snap routing.
    Plan v3 L29 explicitly rejects OpenMeteo as a Tier 4. The failure of
    this test is the antibody — it makes the category impossible.
    """
    tier_names = {t.name for t in Tier}
    assert "OPENMETEO" not in tier_names
    assert "OPENMETEO_GRID" not in tier_names
    assert "GRID_SNAP" not in tier_names


def test_allowed_sources_whitelist_non_empty_per_tier():
    """Every tier has a non-empty allowed-sources frozenset."""
    for tier in Tier:
        allowed = allowed_sources_for_tier(tier)
        assert isinstance(allowed, frozenset)
        assert len(allowed) > 0, f"{tier} has empty allowed-sources"


def test_allowed_sources_no_openmeteo_prefix():
    """No tier accepts a source string containing 'openmeteo' — A4 complement.

    Day-0 ghost-trade root was openmeteo fallback writing off-station rows.
    Writer-level A2 will reject, but this pins the config too.
    """
    for tier, sources in TIER_ALLOWED_SOURCES.items():
        for s in sources:
            assert "openmeteo" not in s.lower(), f"{tier} accepts {s!r} (contains openmeteo)"


def test_target_date_param_is_accepted_and_ignored():
    """Forward-compat: the ``target_date`` parameter exists and is ignored today.

    When a future migration needs date-range tiers, the signature won't
    have to change — callers already pass ``target_date``.
    """
    from datetime import date

    t1 = tier_for_city("Chicago")
    t2 = tier_for_city("Chicago", target_date=date(2024, 1, 1))
    t3 = tier_for_city("Chicago", target_date=date(2030, 12, 31))
    assert t1 == t2 == t3 == Tier.WU_ICAO


def test_schedule_has_51_cities():
    """Pins the pilot-era city count: 51 cities total.

    A change in count is not a bug, but should trip a deliberate update
    to this test alongside a cities.json edit. Zero-cost tripwire.
    """
    assert len(TIER_SCHEDULE) == 51, f"expected 51 cities, got {len(TIER_SCHEDULE)}"


def test_tier_split_matches_plan_v3():
    """Phase 0 plan v3 declares 47 WU + 3 Ogimet + 1 HKO = 51."""
    assert len(cities_in_tier(Tier.WU_ICAO)) == 47
    assert len(cities_in_tier(Tier.OGIMET_METAR)) == 3
    assert len(cities_in_tier(Tier.HKO_NATIVE)) == 1


def test_ogimet_tier_cities_are_istanbul_moscow_tel_aviv():
    """Pins the Tier 2 membership to the three NOAA-settled cities."""
    assert set(cities_in_tier(Tier.OGIMET_METAR)) == {"Istanbul", "Moscow", "Tel Aviv"}


# ----------------------------------------------------------------------
# ALLOWED_SOURCES_BY_CITY — C1 fix (DST gap fallback)
# ----------------------------------------------------------------------


def test_wu_cities_allow_both_primary_and_ogimet_fallback():
    """Tier 1 cities accept either wu_icao_history or ogimet_metar_<icao>.

    The Ogimet fallback exists because WU has silent upstream gaps on
    DST-spring-forward days (verified 2026-04-22: KORD 2024-03-10 returned
    2 obs instead of 23). Ogimet mirrors raw NOAA METAR for the same
    station and fills the gap. Both source tags are equally legitimate.
    """
    for city_name, city in __import__("src.config", fromlist=["cities_by_name"]).cities_by_name.items():
        if city.settlement_source_type != "wu_icao":
            continue
        allowed = allowed_sources_for_city(city_name)
        assert "wu_icao_history" in allowed, f"{city_name} missing WU primary"
        assert len(allowed) >= 2, f"{city_name} has no Ogimet fallback"
        # Ogimet fallback tag must match ICAO
        expected_ogimet = f"ogimet_metar_{city.wu_station.lower()}"
        assert expected_ogimet in allowed, (
            f"{city_name} missing Ogimet fallback {expected_ogimet!r}; got {sorted(allowed)}"
        )


def test_ogimet_cities_have_no_fallback():
    """Tier 2 cities are already Ogimet — no secondary source defined."""
    for city in ("Istanbul", "Moscow", "Tel Aviv"):
        allowed = allowed_sources_for_city(city)
        assert len(allowed) == 1
        assert expected_source_for_city(city) in allowed


def test_hong_kong_has_no_fallback():
    """HKO is the only source for HK; no fallback is valid under A6."""
    allowed = allowed_sources_for_city("Hong Kong")
    assert allowed == frozenset({"hko_hourly_accumulator"})


def test_allowed_sources_primary_is_subset():
    """For every city, the primary source MUST be in the allowed set."""
    for city_name in TIER_SCHEDULE:
        primary = expected_source_for_city(city_name)
        allowed = allowed_sources_for_city(city_name)
        assert primary in allowed, (
            f"{city_name}: primary {primary!r} not in allowed {sorted(allowed)}"
        )


def test_allowed_sources_never_contains_openmeteo():
    """No tier-level escape hatch to grid-snap routing, even via fallback."""
    for city, allowed in ALLOWED_SOURCES_BY_CITY.items():
        for src in allowed:
            assert "openmeteo" not in src.lower(), (
                f"{city}: fallback source {src!r} must not reference openmeteo"
            )
