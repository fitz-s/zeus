"""Relationship tests for Phase 3 source registry collapse — R-G.

Phase: 3 (Observation client low_so_far + source registry collapse)
R-numbers covered:
  R-G (single source of station truth): daily_obs_append.py no longer declares
       a local CITY_STATIONS parallel map. cities.json via src.config.cities_by_name
       is the only place station identifiers live.

These tests MUST FAIL today (2026-04-16) because:
  - src/data/daily_obs_append.py currently declares CITY_STATIONS at module level
    (line 111-171), a parallel map duplicating station data already in cities.json.
  - After Phase 3, that map must be deleted; the module must read station config
    exclusively from cities_by_name.

First commit that should make this green: exec-carol's Phase 3 implementation
(deletes CITY_STATIONS from daily_obs_append.py, rewrites append_wu_city and
daily_tick to consume cities_by_name instead).
"""
from __future__ import annotations

import unittest


# ---------------------------------------------------------------------------
# R-G — Single source of station truth: CITY_STATIONS deleted from daily_obs_append
# ---------------------------------------------------------------------------


class TestCityStationsDeletedFromDailyObsAppend(unittest.TestCase):
    """R-G: daily_obs_append.py no longer declares a local CITY_STATIONS dict."""

    def test_city_stations_absent_from_daily_obs_append(self):
        """R-G: importing daily_obs_append must NOT expose a CITY_STATIONS attribute.

        Today this FAILS because CITY_STATIONS is declared at module level
        (lines 111-171 in daily_obs_append.py). After Phase 3 exec-carol
        deletes it; station config comes exclusively from cities_by_name.
        """
        import src.data.daily_obs_append as mod

        self.assertFalse(
            hasattr(mod, "CITY_STATIONS"),
            "CITY_STATIONS must be deleted from daily_obs_append.py — "
            "cities.json / cities_by_name is the sole source of station identifiers (R-G)",
        )

    def test_daily_obs_append_imports_cities_by_name(self):
        """R-G: daily_obs_append must import cities_by_name from src.config.

        After Phase 3, station lookup goes through cities_by_name. This test
        verifies the import exists (and is not shadowed by a local CITY_STATIONS).
        """
        import src.data.daily_obs_append as mod
        import inspect

        source = inspect.getsource(mod)
        self.assertIn(
            "cities_by_name",
            source,
            "daily_obs_append.py must use cities_by_name from src.config as its station registry",
        )

    def test_append_wu_city_does_not_use_local_city_stations(self):
        """R-G: append_wu_city must resolve station identifiers from cities_by_name, not CITY_STATIONS.

        Today append_wu_city starts with `info = CITY_STATIONS.get(city_name)` (line 863).
        After Phase 3, that lookup must use cities_by_name exclusively.
        """
        import src.data.daily_obs_append as mod
        import inspect

        source = inspect.getsource(mod.append_wu_city)

        self.assertNotIn(
            "CITY_STATIONS",
            source,
            "append_wu_city must not reference CITY_STATIONS after Phase 3 (R-G violation)",
        )

    def test_daily_tick_does_not_iterate_city_stations(self):
        """R-G: daily_tick must iterate cities_by_name (not CITY_STATIONS) for the WU loop.

        Today daily_tick contains `for city_name in CITY_STATIONS:` (line 1412).
        After Phase 3, that loop must iterate cities_by_name or a derived view.
        """
        import src.data.daily_obs_append as mod
        import inspect

        source = inspect.getsource(mod.daily_tick)

        self.assertNotIn(
            "CITY_STATIONS",
            source,
            "daily_tick must not iterate CITY_STATIONS after Phase 3 (R-G violation)",
        )

    def test_cities_by_name_is_the_registry_for_wu_icao_lookup(self):
        """R-G: cities_by_name entries carry the ICAO station identifier needed by WU.

        After Phase 3, cities_by_name is the sole source of station config.
        This test asserts that at least one known WU city (NYC) can have its
        station identifier resolved via cities_by_name — without CITY_STATIONS.
        """
        from src.config import cities_by_name

        nyc = cities_by_name.get("NYC")
        self.assertIsNotNone(nyc, "NYC must be present in cities_by_name")

        # After Phase 3, the City dataclass must expose wu_station or icao_station
        # so append_wu_city can replace its CITY_STATIONS lookup.
        has_station_field = (
            hasattr(nyc, "wu_station") or
            hasattr(nyc, "icao_station") or
            hasattr(nyc, "icao")
        )
        self.assertTrue(
            has_station_field,
            "City config for NYC must expose a station identifier field (wu_station / icao_station / icao) "
            "so daily_obs_append can drop CITY_STATIONS entirely",
        )

    def test_no_parallel_station_map_in_module_source(self):
        """R-G: daily_obs_append.py source must not contain a dict literal keyed by city names.

        This is a structural check: a dict mapping city names → (ICAO, cc, unit) tuples
        is exactly the shape of the deleted CITY_STATIONS. Any re-emergence under a
        different name would violate R-G.
        """
        import src.data.daily_obs_append as mod
        import inspect

        source = inspect.getsource(mod)

        # CITY_STATIONS used string keys like '"NYC"' and tuple values like '("KLGA", "US", "F")'
        # Check that the ICAO codes that were in CITY_STATIONS don't appear in a new local map.
        # We look for the characteristic pattern: a city name followed by ICAO tuple.
        import re
        # Pattern: dict entry like "NYC": ("KLGA", "US", "F")
        parallel_map_pattern = re.compile(
            r'"(NYC|Chicago|Atlanta|London|Tokyo|Seoul|Singapore)"\s*:\s*\(',
        )
        match = parallel_map_pattern.search(source)
        self.assertIsNone(
            match,
            f"Found a parallel city→station dict in daily_obs_append.py after Phase 3 "
            f"(matched: {match.group() if match else 'none'}). "
            f"cities.json must be the sole source of station identifiers (R-G).",
        )


class TestCitiesJsonIsAuthoritative(unittest.TestCase):
    """R-G: cities.json / cities_by_name is the canonical station registry post-Phase-3."""

    def test_cities_by_name_importable(self):
        """R-G prerequisite: src.config.cities_by_name is importable."""
        try:
            from src.config import cities_by_name  # noqa: F401
        except ImportError:
            self.fail("src.config.cities_by_name must be importable")

    def test_cities_by_name_covers_known_wu_cities(self):
        """R-G: cities_by_name must contain the cities previously in CITY_STATIONS.

        After Phase 3, daily_obs_append relies entirely on cities_by_name for the
        WU ICAO lane. If cities_by_name is missing a city, that city silently drops
        out of the WU collection loop. This test catches that regression.
        """
        from src.config import cities_by_name

        # Subset of cities that were in CITY_STATIONS (not exhaustive, but representative)
        expected = [
            "NYC", "Chicago", "Atlanta", "Los Angeles", "Miami",
            "London", "Paris", "Tokyo", "Seoul", "Singapore",
        ]
        missing = [c for c in expected if c not in cities_by_name]
        self.assertEqual(
            missing,
            [],
            f"cities_by_name is missing cities that were in CITY_STATIONS: {missing}. "
            f"Phase 3 requires cities_by_name to be the complete station registry.",
        )


if __name__ == "__main__":
    unittest.main()
