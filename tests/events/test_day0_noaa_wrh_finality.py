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


def test_page_source_matches_the_source_it_replaced() -> None:
    """Parity with the Ogimet lane this cutover superseded, station for station."""
    assert day0_evidence_finality({"settlement_source": "noaa_wrh_khou"}) == (
        day0_evidence_finality({"settlement_source": "ogimet_metar_KHOU"})
    )


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
