# Created: 2026-09-17
# Purpose: A NOAA city carries BOTH the settlement page row and the Ogimet
#   mirror as VERIFIED observations for the same city/date. The calibration
#   fitter's truth fallback must rank the page first; before this it gave both
#   rows the same preference, so SQLite's tie-break decided which value trained
#   the model and a whole-degree reconstruction could become the label for a day
#   the market resolves off the page.
# Reuse: Run when _FIT_QUERY's truth CTE or the NOAA source tags change.
from __future__ import annotations

import sqlite3

import pytest


def _fit_query() -> str:
    import importlib

    mod = importlib.import_module("scripts.fit_source_clock_city_weights")
    return mod._FIT_QUERY


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT, authority TEXT
        );
        CREATE TABLE observations (
            city TEXT, target_date TEXT, source TEXT, unit TEXT,
            high_temp REAL, low_temp REAL, authority TEXT
        );
        CREATE TABLE raw_model_forecasts (
            city TEXT, metric TEXT, model TEXT, target_date TEXT,
            lead_days INTEGER, forecast_value_c REAL, endpoint TEXT
        );
        """
    )
    # One NOAA city-day with BOTH lanes VERIFIED and no settlement outcome:
    # the page says 93.92 F (rounds to 94), the mirror says 93.20 F (rounds 93).
    # Mirror inserted FIRST on purpose: with both rows at the same preference the
    # tie is resolved by scan order, so an unranked fallback returns 93.20 here.
    c.execute(
        "INSERT INTO observations VALUES ('Houston','2026-09-11','ogimet_metar_khou','F',93.20,78.80,'VERIFIED')"
    )
    c.execute(
        "INSERT INTO observations VALUES ('Houston','2026-09-11','noaa_wrh_khou','F',93.92,78.08,'VERIFIED')"
    )
    c.execute(
        "INSERT INTO raw_model_forecasts VALUES "
        "('Houston','high','ecmwf','2026-09-11',1,34.0,'previous_runs')"
    )
    c.commit()
    return c


def test_the_page_row_is_the_training_label_not_the_mirror(conn):
    """With both lanes VERIFIED, the fitter must train on the page value."""
    rows = conn.execute(_fit_query(), ("2026-12-31",)).fetchall()
    assert len(rows) == 1
    assert rows[0]["settlement_value"] == pytest.approx(93.92)
    assert rows[0]["settlement_unit"] == "F"


def test_a_verified_settlement_outcome_still_outranks_both_observations(conn):
    """The authority of record wins over either observation lane."""
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES "
        "('Houston','2026-09-11','high',94.0,'F','VERIFIED')"
    )
    conn.commit()
    rows = conn.execute(_fit_query(), ("2026-12-31",)).fetchall()
    assert len(rows) == 1
    assert rows[0]["settlement_value"] == pytest.approx(94.0)


def test_the_mirror_still_trains_a_city_the_page_never_covered(conn):
    """Non-NOAA and page-dark days must keep their existing truth source."""
    conn.execute(
        "INSERT INTO observations VALUES ('Shanghai','2026-09-11','ogimet_metar_zspd','C',34.0,26.0,'VERIFIED')"
    )
    conn.execute(
        "INSERT INTO raw_model_forecasts VALUES "
        "('Shanghai','high','ecmwf','2026-09-11',1,33.5,'previous_runs')"
    )
    conn.commit()
    rows = {
        r["city"]: r
        for r in conn.execute(_fit_query(), ("2026-12-31",)).fetchall()
    }
    assert rows["Shanghai"]["settlement_value"] == pytest.approx(34.0)
    assert rows["Houston"]["settlement_value"] == pytest.approx(93.92)
