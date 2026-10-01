# Created: 2026-09-17
# Purpose: The NOAA settlement page is the product the 48 NOAA markets resolve
#   against. Day0 finality must classify it as a monotone settlement bound, the
#   same class as the Ogimet reconstruction it replaced — not UNKNOWN, which
#   made the authoritative source weaker than the one it superseded and blocked
#   replacement materialization outright.
# Reuse: Run when day0_authority's source ladder or the settlement source tag
#   (`daily_obs_append.noaa_wrh_source_tag`) changes.
from __future__ import annotations

import pytest

from src.events.day0_authority import (
    DAY0_ABSORBING_FINALITIES,
    DAY0_FINAL_DAILY_SETTLEMENT,
    DAY0_MONOTONE_SETTLEMENT_BOUND,
    DAY0_PROVISIONAL_CURRENT_SNAPSHOT,
    DAY0_UNKNOWN_FINALITY,
    day0_evidence_finality,
)


@pytest.mark.parametrize(
    "source",
    [
        "noaa_wrh_khou",
        "noaa_wrh_kbkf",
        "noaa_wrh_klga",
        "observation_prints:noaa_wrh_khou",
    ],
)
def test_page_source_is_a_monotone_settlement_bound(source: str) -> None:
    """The page product bounds settlement and must be absorbing evidence.

    Within a running local day the page reports the extreme of the rows shown
    so far, so for a HIGH it is non-decreasing and truncates payoff support
    exactly as the METAR reconstruction did.
    """
    finality = day0_evidence_finality({"settlement_source": source})
    assert finality == DAY0_MONOTONE_SETTLEMENT_BOUND
    assert finality in DAY0_ABSORBING_FINALITIES


def test_page_source_is_not_a_final_daily_value() -> None:
    """It is not hko_daily_api: an in-progress day is a bound, not the close."""
    assert (
        day0_evidence_finality({"settlement_source": "noaa_wrh_khou"})
        != DAY0_FINAL_DAILY_SETTLEMENT
    )


@pytest.mark.parametrize("source", [
    "aviationweather_metar", "ogimet_metar_WSSS", "same_station_fast_tail",
    "aviationweather_metar:durable_monotone_bound",
    "observation_prints:aviationweather_metar",
    "observation_prints:ogimet_metar_WSSS",
])
@pytest.mark.parametrize("declared", [None, DAY0_MONOTONE_SETTLEMENT_BOUND, DAY0_FINAL_DAILY_SETTLEMENT])
def test_raw_station_feed_cannot_claim_resolver_product_finality(source, declared):
    # Same station does not imply the same accepted rows: the WRH all-data
    # product can settle 31C while the raw METAR stream contains a 32C print.
    assert day0_evidence_finality({
        "settlement_source": source, "evidence_finality": declared,
    }) == DAY0_PROVISIONAL_CURRENT_SNAPSHOT


def test_live_source_tag_shape_is_the_one_classified() -> None:
    """Pin the classifier to the tag the writer actually emits."""
    from src.data.daily_obs_append import noaa_wrh_source_tag

    tag = noaa_wrh_source_tag("KHOU")
    assert tag == "noaa_wrh_khou"
    assert day0_evidence_finality({"settlement_source": tag}) in DAY0_ABSORBING_FINALITIES


def test_a_declared_provisional_snapshot_still_downgrades() -> None:
    """An explicitly provisional payload is not promoted by the source alone."""
    assert (
        day0_evidence_finality(
            {
                "settlement_source": "noaa_wrh_khou",
                "evidence_finality": DAY0_PROVISIONAL_CURRENT_SNAPSHOT,
            }
        )
        == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
    )


def test_unrelated_sources_are_untouched() -> None:
    """The widening must not capture anything else."""
    assert day0_evidence_finality({"settlement_source": "hko_hourly_accumulator"}) == (
        DAY0_PROVISIONAL_CURRENT_SNAPSHOT
    )
    assert day0_evidence_finality({"settlement_source": "wu"}) == (
        DAY0_PROVISIONAL_CURRENT_SNAPSHOT
    )
    assert day0_evidence_finality({"settlement_source": "noaa_other_product"}) == (
        DAY0_UNKNOWN_FINALITY
    )


@pytest.mark.parametrize(
    ("source", "key"),
    [
        ("noaa_wrh_ksfo", "day0_conditioning"),
        ("observation_prints:noaa_wrh_klax", "day0_conditioning"),
        ("hko_daily_api", "day0_conditioning"),
        ("wu_api+same_station_fast_tail", "day0_provisional_observation"),
        ("aviationweather_metar", "day0_provisional_observation"),
        ("wu_icao_history", "day0_provisional_observation"),
        ("hko_hourly_accumulator", "day0_provisional_observation"),
    ],
)
def test_writer_and_pinned_replay_share_one_conditioning_key(source, key) -> None:
    """H = max(H_confirmed, H_remaining): an absorbing settlement-channel bound
    is H_confirmed and lives under day0_conditioning; every other source is a
    provisional overlay. The materializer and the held pinned replay both
    decide by day0_conditioning_key, so a valid writer shape always replays."""
    from types import SimpleNamespace

    from src.data.replacement_forecast_materializer import (
        _day0_absorbing_observed_extreme_c,
    )
    from src.engine.event_reactor_adapter import _held_pinned_day0_conditioning_key
    from src.events.day0_authority import day0_conditioning_key

    assert day0_conditioning_key(source) == key
    absorbing = _day0_absorbing_observed_extreme_c(
        SimpleNamespace(day0_observed_extreme_c=13.3, day0_observed_extreme_source=source)
    )
    assert (absorbing is not None) == (key == "day0_conditioning")

    observation = {"active": True, "metric": "high", "unit": "F", "source": source}
    bundle = SimpleNamespace(provenance_json={key: observation})
    assert _held_pinned_day0_conditioning_key(bundle, metric="high", unit="F") == key

    other = (
        "day0_provisional_observation"
        if key == "day0_conditioning"
        else "day0_conditioning"
    )
    misfiled = SimpleNamespace(provenance_json={other: observation})
    with pytest.raises(ValueError, match="GLOBAL_HELD_PINNED_CONDITIONING_KEY_MISMATCH"):
        _held_pinned_day0_conditioning_key(misfiled, metric="high", unit="F")
    both = SimpleNamespace(provenance_json={key: observation, other: observation})
    with pytest.raises(ValueError, match="GLOBAL_HELD_PINNED_CONDITIONING_MISSING"):
        _held_pinned_day0_conditioning_key(both, metric="high", unit="F")
