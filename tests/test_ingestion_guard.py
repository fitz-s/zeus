"""Ingestion guard regression tests (K1-B).

Covers:
  - 5 Phase-3 contamination regression cases from k0-contamination-diagnosis.md
  - Guard tests 8-14 from k1-freeze.md section 8
  - Monthly-bounds JSON shape/provenance tests (tests 6-7 from k1-freeze.md section 8,
    mirrored here and unskipped in test_observation_atom.py)

See .omc/plans/k0-contamination-diagnosis.md (Phase 3) and
    .omc/plans/k1-freeze.md section 8 (tests 8-14).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.data.ingestion_guard import (
    DstBoundaryViolation,
    IngestionGuard,
    PhysicalBoundsViolation,
    UnitConsistencyViolation,
)
from src.types.observation_atom import IngestionRejected


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def guard() -> IngestionGuard:
    """IngestionGuard loaded from the generated config/city_monthly_bounds.json."""
    bounds_path = Path(__file__).parent.parent / "config" / "city_monthly_bounds.json"
    return IngestionGuard(bounds_path=bounds_path)


# ---------------------------------------------------------------------------
# Contamination regression tests (k0-contamination-diagnosis.md Phase 3)
# ---------------------------------------------------------------------------

def test_guard_rejects_lagos_89c_settlement(guard: IngestionGuard) -> None:
    """Contamination 1: Lagos 89u00b0C stored in observations (should be 89u00b0F = 31.7u00b0C).

    89u00b0C in Lagos is physically impossible (max record ~42u00b0C in Africa).
    Expect PhysicalBoundsViolation or UnitConsistencyViolation.
    Either layer catching this is acceptable.
    """
    with pytest.raises(IngestionRejected):
        guard.validate(
            city="Lagos",
            raw_value=89.0,
            raw_unit="C",
            fetch_utc=datetime(2025, 11, 25, 3, 0, tzinfo=timezone.utc),
            target_date=date(2025, 11, 25),
            peak_hour=14.0,
            local_time=datetime(2025, 11, 25, 14, 0),
            hemisphere="N",
        )


def test_guard_rejects_wellington_out_of_range_settlement(guard: IngestionGuard) -> None:
    """Contamination 2: Wellington -40u00b0C in month=7 (southern hemisphere winter).

    -40u00b0C = -40u00b0F; Wellington's TIGGE p01 for July is well above -40u00b0C.
    Since Layer 3 was deleted 2026-04-13, the rejection path is now
    PhysicalBoundsViolation (TIGGE p01 guard, Layer 2). Accept any
    IngestionRejected subclass to keep the test robust against future layer
    rearrangements; the important invariant is that this contamination
    never reaches the DB, not which specific layer catches it.
    """
    with pytest.raises(IngestionRejected):
        guard.validate(
            city="Wellington",
            raw_value=-40.0,
            raw_unit="C",
            fetch_utc=datetime(2026, 2, 18, 6, 0, tzinfo=timezone.utc),
            target_date=date(2026, 7, 18),
            peak_hour=14.0,
            local_time=datetime(2026, 7, 18, 14, 0),
            hemisphere="S",
        )


def test_guard_rejects_f_unit_observations_for_c_city_buenos_aires(guard: IngestionGuard) -> None:
    """Contamination 3: Buenos Aires 107 rows with unit=F for a C-declared city.

    Buenos Aires city.settlement_unit = 'C' in cities.json.
    Storing a value with raw_unit='F' must trigger UnitConsistencyViolation.
    """
    # The validate() method computes declared_unit from city config automatically.
    # We force a scenario where raw_unit != settlement_unit.
    # Since validate() uses city_obj.settlement_unit as declared_unit, we must
    # call check_unit_consistency directly with the wrong declared_unit to replay
    # the contamination, OR confirm validate() catches it via the city cross-check.
    with pytest.raises(UnitConsistencyViolation, match="settlement_unit"):
        guard.check_unit_consistency(
            city="Buenos Aires",
            raw_value=99.9,
            raw_unit="F",
            declared_unit="F",  # what the bad ingest path declared
        )


def test_guard_rejects_hko_extract_f_unit(guard: IngestionGuard) -> None:
    """Contamination 4: Hong Kong hko_daily_extract stored unit=F; city unit=C.

    The ETL stored 29.8 with unit='F' while HKO values are Celsius.
    cross-check: declared_unit='F' but city.settlement_unit='C' u2192 UnitConsistencyViolation.
    """
    with pytest.raises(UnitConsistencyViolation, match="settlement_unit"):
        guard.check_unit_consistency(
            city="Hong Kong",
            raw_value=30.0,
            raw_unit="F",
            declared_unit="F",  # what the bad hko_daily_extract path stored
        )


def test_guard_rejects_toronto_f_unit_c_city(guard: IngestionGuard) -> None:
    """Contamination 5: Toronto wu_pws_ITORON112 stored unit=F; city unit=C.

    Same cross-check as contaminations 3 and 4.
    """
    with pytest.raises(UnitConsistencyViolation, match="settlement_unit"):
        guard.check_unit_consistency(
            city="Toronto",
            raw_value=75.0,
            raw_unit="F",
            declared_unit="F",
        )


# ---------------------------------------------------------------------------
# Guard tests 6-11 from k1-freeze.md section 8 (tests 8-14 in the plan numbering)
# ---------------------------------------------------------------------------

def test_guard_rejects_houston_160f_outlier(guard: IngestionGuard) -> None:
    """Houston 160u00b0F exceeds Earth record of 134u00b0F u2192 UnitConsistencyViolation.

    From k1-freeze.md test 9 / contamination replay.
    """
    with pytest.raises(UnitConsistencyViolation, match="Earth record"):
        guard.check_unit_consistency(
            city="Houston",
            raw_value=160.0,
            raw_unit="F",
            declared_unit="F",
        )


def test_guard_rejection_increments_count(guard: IngestionGuard) -> None:
    """Any failing validate() call must increment IngestionGuard.rejected_count."""
    # Reset class counter to a known baseline
    baseline = IngestionGuard.rejected_count
    with pytest.raises(IngestionRejected):
        guard.check_unit_consistency(
            city="Houston",
            raw_value=160.0,
            raw_unit="F",
            declared_unit="F",
        )
    assert IngestionGuard.rejected_count == baseline + 1


def test_guard_collection_timing_before_peak_hour(guard: IngestionGuard) -> None:
    """LA fetched at UTC 12:00 (= ~04:00-05:00 PDT) is before peak_hour=16 u2192 CollectionTimingViolation.

    From k1-freeze.md test 11.
    """
    from src.data.ingestion_guard import CollectionTimingViolation

    target = date(2026, 6, 15)  # PDT: UTC-7, so UTC 12:00 = local 05:00
    fetch_utc = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(CollectionTimingViolation):
        guard.check_collection_timing(
            city="Los Angeles",
            fetch_utc=fetch_utc,
            target_date=target,
            peak_hour=16.0,
        )


def test_guard_dst_missing_hour_london_spring_forward(guard: IngestionGuard) -> None:
    """London 2025-03-30 01:30 local is in the spring-forward gap u2192 DstBoundaryViolation.

    From k1-freeze.md test 12.
    """
    with pytest.raises(DstBoundaryViolation):
        guard.check_dst_boundary(
            city="London",
            local_time=datetime(2025, 3, 30, 1, 30),
        )


def test_monthly_bounds_json_shape_and_samples() -> None:
    """config/city_monthly_bounds.json must have 46 cities, 12 months each.

    Every non-null entry must have sample_count >= 30.
    From k1-freeze.md test 6.
    """
    bounds_path = Path(__file__).parent.parent / "config" / "city_monthly_bounds.json"
    assert bounds_path.exists(), f"Bounds file not found: {bounds_path}"
    with open(bounds_path) as f:
        d = json.load(f)

    cities = d["cities"]
    assert len(cities) == 46, f"Expected 46 cities, got {len(cities)}: {sorted(cities.keys())}"

    for city_name, months_data in cities.items():
        assert len(months_data) == 12, (
            f"{city_name}: expected 12 month entries, got {len(months_data)}"
        )
        for month_str, entry in months_data.items():
            if entry is None:
                continue  # null = below threshold; guard falls back to lat-band
            sc = entry.get("sample_count")
            assert sc is not None, f"{city_name} month={month_str}: missing sample_count"
            assert sc >= 30, (
                f"{city_name} month={month_str}: sample_count={sc} < 30 but entry is non-null"
            )


def test_monthly_bounds_json_has_generation_provenance() -> None:
    """config/city_monthly_bounds.json must have top-level provenance fields.

    From k1-freeze.md test 7.
    """
    bounds_path = Path(__file__).parent.parent / "config" / "city_monthly_bounds.json"
    with open(bounds_path) as f:
        d = json.load(f)

    assert "generated_at" in d, "Missing top-level 'generated_at'"
    assert "source" in d, "Missing top-level 'source'"
    assert "script" in d, "Missing top-level 'script'"

    # generated_at must be ISO-like (starts with digit, contains T)
    ga = d["generated_at"]
    assert ga[0].isdigit() and "T" in ga, (
        f"generated_at does not look like an ISO timestamp: {ga!r}"
    )

    # Every non-null month entry must have tigge_date_range
    for city_name, months_data in d["cities"].items():
        for month_str, entry in months_data.items():
            if entry is None:
                continue
            assert "tigge_date_range" in entry, (
                f"{city_name} month={month_str}: missing tigge_date_range in entry"
            )
            tdr = entry["tigge_date_range"]
            assert "from" in tdr and "to" in tdr, (
                f"{city_name} month={month_str}: tigge_date_range missing 'from' or 'to'"
            )
