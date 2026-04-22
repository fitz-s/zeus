# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 antibody A7 (.omc/plans/observation-instants-
#                  migration-iter3.md L125); step2 Phase 0 file #11.
"""Antibody A7: backfill scripts must match the live config.

Phase -1 (commit d9c998f) removed 4 stale entries whose tier no longer
matched cities.json after the 2026-04-15 Tel Aviv (wu_icao→noaa) and
Taipei (cwa_station→wu_icao) migrations. This antibody prevents that
class of drift from reappearing: a city can only live in a backfill
script's map if its settlement_source_type agrees.

The tests import the backfill scripts as modules (no subprocess, no
HTTP) and compare their hard-coded maps against ``cities_by_name``
filtered by ``settlement_source_type``.

If these tests fail after a cities.json edit, the fix is NOT to relax
the assertion — it is to update the backfill script to match the new
source-of-truth. Failure = migration-not-completed; assertion relaxation
re-opens the exact DRIFT category Phase -1 closed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.config import cities_by_name


REPO_ROOT = Path(__file__).resolve().parent.parent
WU_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_wu_daily_all.py"
OGIMET_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_ogimet_metar.py"


def _load_module_by_path(path: Path, name: str):
    """Load a script module without adding it to sys.modules permanently.

    Scripts in ``scripts/`` are not a package; use spec loader.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"failed to load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    # Register transiently so relative imports inside the script work.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Leave the module registered so subsequent tests can reuse;
        # pytest isolates tests enough that this is safe.
        pass


@pytest.fixture(scope="module")
def wu_backfill_module():
    return _load_module_by_path(WU_BACKFILL_PATH, "zeus_backfill_wu_daily_all")


@pytest.fixture(scope="module")
def ogimet_backfill_module():
    return _load_module_by_path(OGIMET_BACKFILL_PATH, "zeus_backfill_ogimet_metar")


# ----------------------------------------------------------------------
# A7: WU backfill map matches sstype=='wu_icao' city set
# ----------------------------------------------------------------------


def test_wu_backfill_city_stations_matches_wu_icao_cities(wu_backfill_module):
    """CITY_STATIONS keys == set of wu_icao cities from cities.json."""
    live_wu_icao = {
        c.name for c in cities_by_name.values() if c.settlement_source_type == "wu_icao"
    }
    backfill_keys = set(wu_backfill_module.CITY_STATIONS.keys())
    missing_in_backfill = live_wu_icao - backfill_keys
    extra_in_backfill = backfill_keys - live_wu_icao
    assert not missing_in_backfill, (
        f"cities.json has wu_icao cities not in "
        f"backfill_wu_daily_all.CITY_STATIONS: {sorted(missing_in_backfill)}. "
        "The WU backfill will skip these cities."
    )
    assert not extra_in_backfill, (
        f"backfill_wu_daily_all.CITY_STATIONS has cities not in "
        f"cities.json (or flipped sstype): {sorted(extra_in_backfill)}. "
        "This is the DRIFT pattern Phase -1 (commit d9c998f) closed."
    )


def test_wu_backfill_icao_matches_cities_json(wu_backfill_module):
    """For every wu_icao city, CITY_STATIONS[name][0] == city.wu_station."""
    mismatches: list[tuple[str, str, str]] = []
    for city in cities_by_name.values():
        if city.settlement_source_type != "wu_icao":
            continue
        entry = wu_backfill_module.CITY_STATIONS.get(city.name)
        if entry is None:
            continue  # covered by the keys test above
        icao_in_backfill = entry[0]
        if icao_in_backfill != city.wu_station:
            mismatches.append((city.name, city.wu_station, icao_in_backfill))
    assert not mismatches, (
        "ICAO drift between cities.json and backfill_wu_daily_all:\n"
        + "\n".join(f"  {n}: cities.json={j!r}, backfill={b!r}" for n, j, b in mismatches)
    )


# ----------------------------------------------------------------------
# A7: Ogimet backfill map matches sstype=='noaa' city set
# ----------------------------------------------------------------------


def test_ogimet_backfill_targets_matches_noaa_cities(ogimet_backfill_module):
    """OGIMET_TARGETS keys == set of noaa cities from cities.json.

    After Phase -1 cleanup: both sides should be {Istanbul, Moscow, Tel Aviv}.
    """
    live_noaa = {
        c.name for c in cities_by_name.values() if c.settlement_source_type == "noaa"
    }
    backfill_keys = set(ogimet_backfill_module.OGIMET_TARGETS.keys())
    assert backfill_keys == live_noaa, (
        f"Drift between cities.json noaa-sstype and "
        f"backfill_ogimet_metar.OGIMET_TARGETS:\n"
        f"  only in cities.json: {sorted(live_noaa - backfill_keys)}\n"
        f"  only in backfill:    {sorted(backfill_keys - live_noaa)}"
    )


def test_ogimet_backfill_station_tags_match_tier_resolver(ogimet_backfill_module):
    """Per-city source tag in Ogimet backfill == tier_resolver expected source.

    Complements A2 at the backfill-script level: if someone edits the
    source_tag for Moscow from 'ogimet_metar_uuww' to something else,
    the v2 writer would reject the write; this test catches it earlier.
    """
    from src.data.tier_resolver import EXPECTED_SOURCE_BY_CITY

    mismatches: list[tuple[str, str, str]] = []
    for name, target in ogimet_backfill_module.OGIMET_TARGETS.items():
        expected = EXPECTED_SOURCE_BY_CITY.get(name)
        actual = target.source_tag
        if expected is None:
            continue  # would already fail the keys test above
        if expected != actual:
            mismatches.append((name, expected, actual))
    assert not mismatches, (
        "source_tag drift between tier_resolver and Ogimet backfill:\n"
        + "\n".join(
            f"  {n}: tier_resolver={e!r}, backfill={a!r}" for n, e, a in mismatches
        )
    )


# ----------------------------------------------------------------------
# Regression pin: Phase -1 DRIFT targets
# ----------------------------------------------------------------------


def test_tel_aviv_not_in_wu_backfill(wu_backfill_module):
    """Phase -1 deleted Tel Aviv from CITY_STATIONS; must stay gone."""
    assert "Tel Aviv" not in wu_backfill_module.CITY_STATIONS


@pytest.mark.parametrize("stale_city", ["Taipei", "Cape Town", "Lucknow"])
def test_stale_cities_not_in_ogimet_backfill(ogimet_backfill_module, stale_city):
    """Phase -1 deleted Taipei/Cape Town/Lucknow; must stay gone."""
    assert stale_city not in ogimet_backfill_module.OGIMET_TARGETS
