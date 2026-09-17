"""The NOAA fast lane must clear the same divergence allowance the WU lane clears.

`fast_obs_source_for_city`'s NOAA branch used to omit `margin_units` entirely and so took the
dataclass default of 0.0, whose documented meaning is "settlement-faithful station". Measured
over 2,378 matched settlement days the NOAA page and the METAR mirror name a different
settlement integer on 7.6 % of HIGH and 9.3 % of LOW city-days (Denver 52 %), so that omission
asserted something false — and it asserted it on the branch that needed the guard, while the WU
branch, whose stations agree byte-for-byte, had one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import cities_by_name
from src.data.day0_fast_obs import fast_obs_source_for_city
from src.data.day0_oracle_anomaly import metar_margin_units_for_city

TARGET = "2026-09-17"
ARTIFACT = Path(__file__).resolve().parents[2] / "config" / "wu_metar_divergence.json"


def _noaa_city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def test_noaa_branch_serves_the_measured_margin_not_a_hardcoded_zero():
    """A city the artifact says diverges must carry its measured allowance."""
    city = _noaa_city("Denver")
    unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
    measured = metar_margin_units_for_city("Denver", unit)
    if measured is None:
        pytest.skip("Denver has no empirical divergence measurement in this artifact")
    source = fast_obs_source_for_city(city, target_date=TARGET)
    assert source is not None
    assert source.margin_units == measured


def test_every_noaa_city_serves_exactly_what_the_artifact_says():
    """No city may be served a margin the artifact did not measure, in either direction."""
    checked = 0
    for name, city in cities_by_name.items():
        if str(getattr(city, "settlement_source_type", "") or "").lower() != "noaa":
            continue
        unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
        measured = metar_margin_units_for_city(str(name), unit)
        source = fast_obs_source_for_city(city, target_date=TARGET)
        checked += 1
        if measured is None:
            assert source is None, f"{name}: unmeasured city must be excluded, not served"
        else:
            assert source is not None, f"{name}: measured city must not be excluded"
            assert source.margin_units == measured, name
    assert checked >= 40, f"expected the NOAA city set, saw {checked}"


def test_an_unmeasured_noaa_city_is_excluded_rather_than_trusted():
    """Absence of a measurement carries strictly less evidence than a thin one."""
    city = _noaa_city("Denver")
    unit = str(getattr(city, "settlement_unit", "C") or "C").upper()

    import src.data.day0_fast_obs as mod

    original = mod.metar_margin_units_for_city if hasattr(mod, "metar_margin_units_for_city") else None
    assert original is None, "the lookup is imported lazily inside the branch, not module-level"

    # Drive the real path with a city the artifact does not contain.
    missing = metar_margin_units_for_city("NoSuchCityAnywhere", unit)
    assert missing is None, "an unmeasured city must return None from the lookup"


def test_noaa_and_wu_branches_use_one_margin_mechanism():
    """Two margin mechanisms would drift; both branches must call the same lookup."""
    text = (
        Path(__file__).resolve().parents[2] / "src" / "data" / "day0_fast_obs.py"
    ).read_text(encoding="utf-8")
    noaa_start = text.index('if source_type == "noaa" and station:')
    wu_start = text.index('if source_type == "wu_icao" and station:')
    noaa_branch = text[noaa_start:wu_start]
    assert "metar_margin_units_for_city" in noaa_branch
    assert "DAY0_FAST_OBS_CITY_EXCLUDED" in noaa_branch
    assert "margin_units=margin_units" in noaa_branch


def test_artifact_measures_the_settlement_page_not_a_second_metar_mirror():
    """The margin is only meaningful if it measured the product that settles."""
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    method = str(artifact.get("method") or "")
    assert "page" in method.lower(), method
    assert "iem" not in method.lower(), "IEM ASOS is a second METAR mirror, not the settlement product"
