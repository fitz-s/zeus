# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect AIFS ENS sampled-2t extrema to market-bin probability bridge for replacement shadow research.
# Reuse: Run before changing AIFS q_aifs construction or soft-anchor posterior inputs.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow integration.
"""AIFS sampled-2t bin probability bridge tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.ecmwf_aifs_sampled_2t_localday import (
    HIGH_DATA_VERSION,
    LOW_DATA_VERSION,
    AifsInstantSample,
    extract_aifs_sampled_2t_localday,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (
    AifsTemperatureBin,
    build_aifs_sampled_2t_bin_probabilities,
    build_openmeteo_ifs9_aifs_soft_anchor_result,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import build_soft_anchor_posterior, selected_bin


UTC = timezone.utc


def _dt(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _extraction():
    return extract_aifs_sampled_2t_localday(
        (
            AifsInstantSample("cf", _dt(2026, 6, 6, 0), 16.0, "C"),
            AifsInstantSample("cf", _dt(2026, 6, 6, 6), 21.0, "C"),
            AifsInstantSample("pf001", _dt(2026, 6, 6, 0), 11.0, "C"),
            AifsInstantSample("pf001", _dt(2026, 6, 6, 6), 13.0, "C"),
            AifsInstantSample("pf002", _dt(2026, 6, 6, 0), 23.0, "C"),
            AifsInstantSample("pf002", _dt(2026, 6, 6, 6), 27.0, "C"),
            AifsInstantSample("pf003", _dt(2026, 6, 6, 0), 14.0, "C"),
            AifsInstantSample("pf003", _dt(2026, 6, 6, 6), 17.0, "C"),
        ),
        city_timezone="UTC",
        target_local_date=date(2026, 6, 6),
        source_cycle_time=_dt(2026, 6, 5, 0),
        min_samples_per_member=2,
    )


def _bins() -> tuple[AifsTemperatureBin, ...]:
    return (
        AifsTemperatureBin("cold", upper_c=14.0, center_c=13.0),
        AifsTemperatureBin("mild", lower_c=15.0, upper_c=21.0),
        AifsTemperatureBin("hot", lower_c=22.0, center_c=23.0),
    )


def _anchor() -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="UTC",
        target_local_date=date(2026, 6, 6),
        source_cycle_time=_dt(2026, 6, 5, 0),
        high_c=23.0,
        low_c=12.0,
        sample_count=2,
        contributing_local_times=(_dt(2026, 6, 6, 0), _dt(2026, 6, 6, 6)),
        contributing_valid_times_utc=(_dt(2026, 6, 6, 0), _dt(2026, 6, 6, 6)),
    )


def test_aifs_extrema_members_become_high_bin_probabilities() -> None:
    result = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())

    assert result.data_version == HIGH_DATA_VERSION
    assert result.probability_source == "aifs_sampled_2t_member_frequency"
    assert result.trade_authority_status == "SHADOW_ONLY"
    assert result.training_allowed is False
    assert result.probabilities == pytest.approx({"cold": 0.25, "mild": 0.50, "hot": 0.25})
    assert result.member_assignments == {"cf": "mild", "pf001": "cold", "pf002": "hot", "pf003": "mild"}
    assert sum(result.probabilities.values()) == pytest.approx(1.0)


def test_aifs_extrema_members_become_low_bin_probabilities_with_separate_identity() -> None:
    result = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="low", bins=_bins())

    assert result.data_version == LOW_DATA_VERSION
    assert result.probabilities == pytest.approx({"cold": 0.50, "mild": 0.25, "hot": 0.25})
    assert result.member_values_c == {"cf": 16.0, "pf001": 11.0, "pf002": 23.0, "pf003": 14.0}


def test_aifs_bin_probabilities_feed_soft_anchor_posterior_without_manual_prior() -> None:
    aifs = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())

    posterior = build_soft_anchor_posterior(
        aifs_probabilities=aifs.probabilities,
        bins=aifs.soft_anchor_bins,
        anchor_c=23.0,
    )

    assert selected_bin(aifs.probabilities) == "mild"
    assert selected_bin(posterior.probabilities) == "hot"
    assert posterior.probabilities["hot"] > aifs.probabilities["hot"]
    assert posterior.probabilities["cold"] < aifs.probabilities["cold"]


def test_composed_research_result_uses_matching_openmeteo_anchor_metric() -> None:
    high_result = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(),
        metric="high",
        bins=_bins(),
    )
    low_result = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(),
        metric="low",
        bins=_bins(),
    )

    assert high_result.anchor_value_c == pytest.approx(23.0)
    assert low_result.anchor_value_c == pytest.approx(12.0)
    assert high_result.aifs_probabilities.data_version == HIGH_DATA_VERSION
    assert low_result.aifs_probabilities.data_version == LOW_DATA_VERSION
    assert selected_bin(high_result.posterior.probabilities) == "hot"
    assert selected_bin(low_result.posterior.probabilities) == "cold"
    assert high_result.trade_authority_status == "SHADOW_ONLY"
    assert high_result.training_allowed is False


def test_aifs_probability_bridge_supports_open_shoulders_with_explicit_centers() -> None:
    bins = (
        AifsTemperatureBin("below_10", upper_c=9.0, center_c=8.0),
        AifsTemperatureBin("ten_to_twenty", lower_c=10.0, upper_c=20.0),
        AifsTemperatureBin("above_21", lower_c=21.0, center_c=22.0),
    )

    result = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=bins)

    assert result.probabilities == pytest.approx({"below_10": 0.0, "ten_to_twenty": 0.50, "above_21": 0.50})
    assert result.soft_anchor_bins[0].center_c == pytest.approx(8.0)
    assert result.soft_anchor_bins[2].center_c == pytest.approx(22.0)


def test_aifs_probability_bridge_fails_closed_on_bad_bin_topology() -> None:
    with pytest.raises(ValueError, match="has a gap"):
        build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric="high",
            bins=(
                AifsTemperatureBin("too_cold", upper_c=10.0, center_c=9.0),
                AifsTemperatureBin("too_hot", lower_c=20.0, center_c=21.0),
            ),
            settlement_step_c=1.0,
        )

    with pytest.raises(ValueError, match="overlaps"):
        build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric="high",
            bins=(
                AifsTemperatureBin("low", upper_c=19.0, center_c=18.0),
                AifsTemperatureBin("wide", lower_c=20.0, upper_c=30.0),
                AifsTemperatureBin("overlap", lower_c=20.0, center_c=31.0),
            ),
        )

    with pytest.raises(ValueError, match="requires center_c"):
        build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric="high",
            bins=(AifsTemperatureBin("cold", upper_c=14.0), AifsTemperatureBin("warm", lower_c=15.0)),
        )


def test_aifs_probability_bridge_requires_full_market_bin_family() -> None:
    with pytest.raises(ValueError, match="lower open shoulder"):
        build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric="high",
            bins=(
                AifsTemperatureBin("mild", lower_c=15.0, upper_c=21.0),
                AifsTemperatureBin("hot", lower_c=22.0, center_c=23.0),
            ),
        )

    with pytest.raises(ValueError, match="upper open shoulder"):
        build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric="high",
            bins=(
                AifsTemperatureBin("cold", upper_c=14.0, center_c=13.0),
                AifsTemperatureBin("mild", lower_c=15.0, upper_c=21.0),
            ),
        )

    with pytest.raises(ValueError, match="has a gap"):
        build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric="high",
            bins=(
                AifsTemperatureBin("cold", upper_c=10.0, center_c=9.0),
                AifsTemperatureBin("mild", lower_c=15.0, upper_c=21.0),
                AifsTemperatureBin("hot", lower_c=22.0, center_c=23.0),
            ),
        )


def test_aifs_probability_bridge_rejects_wrong_metric_or_transcript_shorthand() -> None:
    with pytest.raises(ValueError, match="metric"):
        build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="mean", bins=_bins())

    with pytest.raises(ValueError, match="transcript shorthand"):
        AifsTemperatureBin("bad_" + "h" + "3", lower_c=1.0, upper_c=2.0)
