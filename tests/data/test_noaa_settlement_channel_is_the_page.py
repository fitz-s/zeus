# Created: 2026-09-17
# Purpose: For a NOAA city the settlement channel must be the page, not the
#   Ogimet METAR reconstruction. A whole-degree Celsius report converts onto a
#   fixed grid of Fahrenheit values that straddles the market's integer bin
#   edges, so the mirror rounds to a different settlement integer on 17-58% of
#   days per city (p99 |delta| = 1 full degree over 1,153 paired city-days). It
#   must remain current physical evidence and never alone create absorbing
#   certainty — the same split the HKO spot reading already has.
# Reuse: Run when _latest_authorized_day0_fact's channel selection or the NOAA
#   source tags change.
from __future__ import annotations

import inspect

import pytest

from src.data import replacement_forecast_current_target_plan as plan


def _channel_block() -> str:
    src = inspect.getsource(plan._latest_authorized_day0_fact)
    start = src.index('source_type == "noaa" and expected_station')
    return src[start : start + 1400]


def test_the_page_is_the_only_noaa_settlement_channel() -> None:
    """Absorbing certainty may come from the settlement product alone."""
    block = _channel_block()
    assert "settlement_channels = {page_channel}" in block


def test_the_metar_mirror_stays_physical_evidence() -> None:
    """It may advance refresh/redecision, so it must remain a physical channel."""
    block = _channel_block()
    assert "physical_channels = {page_channel, ogimet_channel}" in block


def test_the_mirror_is_not_a_settlement_channel() -> None:
    """The precise defect: ogimet alone must not grant q=0/1 certainty."""
    block = _channel_block()
    settlement_line = next(
        line for line in block.splitlines() if "settlement_channels =" in line
    )
    assert "ogimet_channel" not in settlement_line


def test_this_mirrors_the_hko_split() -> None:
    """HKO already separates the two roles for the same revision reason."""
    src = inspect.getsource(plan._latest_authorized_day0_fact)
    assert 'physical_channels = {"hko_rhrread_spot"}' in src
    hko_at = src.index('physical_channels = {"hko_rhrread_spot"}')
    # HKO's settlement_channels is the empty set immediately above it.
    assert "settlement_channels = set()" in src[max(0, hko_at - 200) : hko_at]


@pytest.mark.parametrize("station", ["KHOU", "KBKF", "KMDW"])
def test_the_channel_names_match_the_writer_tags(station: str) -> None:
    """The channel the reader asks for must be the tag the producer writes."""
    from src.data.daily_obs_append import noaa_wrh_source_tag

    assert noaa_wrh_source_tag(station) == f"noaa_wrh_{station.lower()}"
