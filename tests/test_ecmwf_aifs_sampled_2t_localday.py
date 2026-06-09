# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect AIFS ENS sampled-2t city-local-day extraction for replacement shadow research.
# Reuse: Run before changing AIFS sampled-2t local-day extrema extraction or product identity.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow integration.
"""AIFS sampled-2t local-day extraction tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.ecmwf_aifs_sampled_2t_localday import (
    AGGREGATION_WINDOW_POLICY,
    HIGH_DATA_VERSION,
    LOW_DATA_VERSION,
    PHYSICAL_QUANTITY,
    PRODUCT_ID,
    SOURCE_ID,
    AifsInstantSample,
    expected_aifs_sample_steps_for_local_day,
    extract_aifs_sampled_2t_localday,
)


UTC = timezone.utc


def _dt(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_aifs_sampled_2t_extracts_member_high_low_inside_city_local_day() -> None:
    samples = (
        AifsInstantSample("cf", _dt(2026, 6, 4, 18), 280.15, "K"),  # outside Shanghai 2026-06-06
        AifsInstantSample("cf", _dt(2026, 6, 5, 18), 298.15, "K"),
        AifsInstantSample("cf", _dt(2026, 6, 6, 0), 31.0, "C"),
        AifsInstantSample("cf", _dt(2026, 6, 6, 12), 83.3, "F"),
        AifsInstantSample("pf001", _dt(2026, 6, 5, 18), 20.0, "C"),
        AifsInstantSample("pf001", _dt(2026, 6, 6, 6), 34.0, "C"),
        AifsInstantSample("pf001", _dt(2026, 6, 6, 18), 10.0, "C"),  # outside Shanghai 2026-06-06
    )

    extraction = extract_aifs_sampled_2t_localday(
        samples,
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 6),
        source_cycle_time=_dt(2026, 6, 5, 0),
        min_samples_per_member=2,
    )

    assert extraction.source_id == SOURCE_ID
    assert extraction.product_id == PRODUCT_ID
    assert extraction.high_data_version == HIGH_DATA_VERSION
    assert extraction.low_data_version == LOW_DATA_VERSION
    assert extraction.physical_quantity == PHYSICAL_QUANTITY
    assert extraction.aggregation_window_policy == AGGREGATION_WINDOW_POLICY
    assert extraction.trade_authority_status == "SHADOW_ONLY"
    assert extraction.training_allowed is False
    assert extraction.target_window_start_utc == _dt(2026, 6, 5, 16)
    assert extraction.target_window_end_utc == _dt(2026, 6, 6, 16)

    by_member = {member.member_id: member for member in extraction.members}
    assert by_member["cf"].high_c == pytest.approx(31.0)
    assert by_member["cf"].low_c == pytest.approx(25.0)
    assert by_member["cf"].sample_count == 3
    assert by_member["pf001"].high_c == pytest.approx(34.0)
    assert by_member["pf001"].low_c == pytest.approx(20.0)
    assert by_member["pf001"].contributing_valid_times_utc == (_dt(2026, 6, 5, 18), _dt(2026, 6, 6, 6))


def test_expected_sample_steps_follow_local_day_and_dst_duration() -> None:
    spring_steps = expected_aifs_sample_steps_for_local_day(
        source_cycle_time=_dt(2026, 3, 28, 0),
        city_timezone="Europe/London",
        target_local_date=date(2026, 3, 29),
    )
    fall_steps = expected_aifs_sample_steps_for_local_day(
        source_cycle_time=_dt(2026, 10, 24, 0),
        city_timezone="Europe/London",
        target_local_date=date(2026, 10, 25),
    )

    assert spring_steps == (24, 30, 36, 42)
    assert fall_steps == (24, 30, 36, 42)


def test_aifs_sampled_2t_rejects_incomplete_or_bad_units() -> None:
    with pytest.raises(ValueError, match="temperature_unit"):
        AifsInstantSample("pf001", _dt(2026, 6, 6, 0), 300.0, "rankine")

    with pytest.raises(ValueError, match="timezone-aware"):
        AifsInstantSample("pf001", datetime(2026, 6, 6, 0), 300.0, "K")

    with pytest.raises(ValueError, match="insufficient"):
        extract_aifs_sampled_2t_localday(
            (AifsInstantSample("pf001", _dt(2026, 6, 6, 12), 22.0, "C"),),
            city_timezone="UTC",
            target_local_date=date(2026, 6, 6),
            min_samples_per_member=2,
        )


def test_aifs_sampled_2t_identity_cannot_masquerade_as_period_extrema() -> None:
    for identifier in (SOURCE_ID, PRODUCT_ID, HIGH_DATA_VERSION, LOW_DATA_VERSION):
        assert ("h" + "3") not in identifier.lower()
    assert "sampled_2t_6h" in HIGH_DATA_VERSION
    assert "sampled_2t_6h" in LOW_DATA_VERSION
    assert "mx2t" not in HIGH_DATA_VERSION
    assert "mn2t" not in LOW_DATA_VERSION

    with pytest.raises(ValueError, match="period-extrema"):
        extraction = extract_aifs_sampled_2t_localday(
            (AifsInstantSample("pf001", _dt(2026, 6, 6, 0), 22.0, "C"),),
            city_timezone="UTC",
            target_local_date=date(2026, 6, 6),
        )
        type(extraction)(
            city_timezone=extraction.city_timezone,
            target_local_date=extraction.target_local_date,
            source_cycle_time=extraction.source_cycle_time,
            target_window_start_utc=extraction.target_window_start_utc,
            target_window_end_utc=extraction.target_window_end_utc,
            members=extraction.members,
            high_data_version="ecmwf_aifs_mx2t3_bad",
        )
