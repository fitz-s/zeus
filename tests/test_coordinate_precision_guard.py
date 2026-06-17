# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator spec zeus_source_access_validation_v3.xlsx GridCorrectionMath
#   rule 1 (CoordinatePrecisionGuard: <4 decimals FAIL; text-decimal aware; loader
#   restores precise coords from config/cities.json, flags <4-decimal cities
#   REQUIRES_PRECISE_RESTORE). RED-on-revert: each assertion fails if the guard is
#   reverted to a float-rounded / >=anything check.
"""RED-on-revert tests for the text-decimal CoordinatePrecisionGuard (v3 rule 1)."""
from __future__ import annotations

import os

import pytest

from src.forecast.coordinate_precision_guard import (
    MIN_DECIMALS,
    RESTORE_ACTION,
    CoordinatePrecisionGuard,
    count_decimals,
    cities_requiring_restore,
    guard_pair,
    load_city_coordinates,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CITIES_JSON = os.path.join(REPO_ROOT, "config", "cities.json")


def test_min_decimals_is_four():
    """The operator threshold is exactly 4 decimal places."""
    assert MIN_DECIMALS == 4


def test_count_decimals_is_text_decimal_not_float():
    """Precision is counted in the WRITTEN string, not a float round-trip."""
    assert count_decimals("39.12") == 2
    assert count_decimals("39.1234") == 4
    assert count_decimals("39.12000") == 5  # trailing zeros COUNT as written precision
    assert count_decimals("-104") == 0
    assert count_decimals("-58.536") == 3


def test_count_decimals_rejects_exponent_and_junk():
    """Exponent form hides written precision and must be rejected, not silently 0-dec."""
    with pytest.raises(ValueError):
        count_decimals("3.9e1")
    with pytest.raises(ValueError):
        count_decimals("not_a_coord")


def test_guard_fails_two_decimal_passes_four_decimal():
    """39.12 (2-dec) FAILS; 39.1234 (4-dec) PASSES — the spec's canonical example."""
    fail = CoordinatePrecisionGuard("39.12")
    assert fail.status == "FAIL"
    assert not fail.passed
    assert fail.decimals == 2
    assert fail.reason == "FAIL_INPUT_TRUNCATED"
    assert fail.action == RESTORE_ACTION

    ok = CoordinatePrecisionGuard("39.1234")
    assert ok.status == "PASS"
    assert ok.passed
    assert ok.decimals == 4


def test_guard_boundary_exactly_three_fails_exactly_four_passes():
    """3 decimals FAIL (the 38-city case), exactly 4 PASS — boundary is < 4."""
    assert CoordinatePrecisionGuard("32.995").status == "FAIL"   # 3 dec
    assert CoordinatePrecisionGuard("32.9950").status == "PASS"  # 4 dec (written)


def test_guard_pair_fails_when_either_coord_truncated():
    """A pair PASSES only when BOTH coordinates pass."""
    _, _, both_ok = guard_pair("39.1234", "116.6030")
    assert both_ok is True
    _, _, both_bad = guard_pair("39.1234", "116.603")  # lon 3-dec
    assert both_bad is False


def test_loader_reads_text_decimal_and_flags_truncated_cities():
    """Loader restores text-decimal coords from cities.json and flags <4-dec cities.

    The operator audit marks 38/54 cities <4-decimal. This asserts the loader counts
    decimals from the WRITTEN form (it would be wrong if it parsed to float first) and
    flags exactly those needing restore with the operator action — never fabricating.
    """
    records = load_city_coordinates(CITIES_JSON)
    assert len(records) == 54
    needs_restore = cities_requiring_restore(records)
    assert len(needs_restore) == 38  # matches the operator CityBestSources audit
    for r in needs_restore:
        assert r.restore_status == "REQUIRES_PRECISE_RESTORE"
        assert r.action == RESTORE_ACTION
        # Coordinates are kept as TEXT (never coerced); the loader did not invent digits.
        assert isinstance(r.lat_text, str) and isinstance(r.lon_text, str)
        assert min(r.lat_verdict.decimals, r.lon_verdict.decimals) < MIN_DECIMALS


def test_loader_passing_cities_have_four_plus_decimals_both():
    """Every PASS city has >=4 written decimals on BOTH coordinates."""
    records = load_city_coordinates(CITIES_JSON)
    passing = [r for r in records if r.status == "PASS"]
    assert len(passing) == 16  # 54 - 38
    for r in passing:
        assert r.lat_verdict.decimals >= MIN_DECIMALS
        assert r.lon_verdict.decimals >= MIN_DECIMALS
