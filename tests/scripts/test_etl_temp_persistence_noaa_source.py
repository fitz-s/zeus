# Created: 2026-09-17
# Purpose: The ETL that feeds temp_persistence (ENS anomaly detection / CI
#   widening) matched only the Ogimet mirror for NOAA cities, so it silently
#   dropped the settlement-page rows the market actually resolves off. With both
#   families now admitted, the page row must be the one that wins the
#   one-row-per-city-date contract.
# Reuse: Run when the ETL's source-family contract or its ordering changes.
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def etl():
    return importlib.import_module("scripts.etl_temp_persistence")


def test_the_page_source_is_a_canonical_noaa_daily_observation(etl):
    """A noaa_wrh row must not be dropped as a foreign family."""
    assert (
        etl._is_canonical_daily_observation(
            "Houston", "noaa_wrh_khou", "KHOU", target_date="2026-09-11"
        )
        is True
    )


def test_the_ogimet_mirror_stays_canonical(etl):
    """The fallback lane keeps its standing for page-dark days."""
    assert (
        etl._is_canonical_daily_observation(
            "Houston", "ogimet_metar_khou", "KHOU", target_date="2026-09-11"
        )
        is True
    )


def test_a_foreign_family_is_still_rejected_for_a_noaa_city(etl):
    """The widening must not admit a different provider family."""
    assert (
        etl._is_canonical_daily_observation(
            "Houston", "wu_icao_history", "KHOU", target_date="2026-09-11"
        )
        is False
    )


def test_a_mismatched_station_is_still_rejected(etl):
    """Station identity still gates, independent of the family widening."""
    assert (
        etl._is_canonical_daily_observation(
            "Houston", "noaa_wrh_kbkf", "KBKF", target_date="2026-09-11"
        )
        is False
    )


def test_the_query_ranks_the_page_ahead_of_the_mirror(etl):
    """One row per city-date wins by scan order, so the order must be explicit.

    Relying on `noaa_wrh_` sorting before `ogimet_metar_` alphabetically would
    make the settlement law depend on a source tag's spelling.
    """
    import inspect

    src = inspect.getsource(etl.run_etl)
    assert "noaa_wrh_%" in src
    page_rank = src.index("noaa_wrh_%")
    order_by = src.index("ORDER BY")
    assert order_by < page_rank, "the page preference must live in the ORDER BY"
