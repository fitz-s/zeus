# Created: 2026-09-17
# Purpose: The settlement cutover wrote the page's daily extreme into
#   `observations` but published nothing to the `observation_prints` ledger the
#   Day0 intraday lane derives its running bound from. Day0 therefore priced and
#   exited on the whole-degree Ogimet reconstruction while settlement resolved
#   off the page value — the same 93-vs-94 mismatch the cutover removed from
#   settlement, still live on the Day0 side.
# Reuse: Run when append_noaa_wrh_city, the prints ledger contract, or the Day0
#   source authorisation for NOAA cities changes.
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.daily_obs_append import (
    _append_noaa_wrh_prints,
    noaa_wrh_source_tag,
)


class _Row:
    """The fields _append_noaa_wrh_prints reads off a WrhRow."""

    def __init__(self, local_timestamp, utc, air_temp, is_official_report, raw_metar=None):
        self.local_timestamp = local_timestamp
        self.utc = utc
        self.air_temp = air_temp
        self.is_official_report = is_official_report
        self.raw_metar = raw_metar


@pytest.fixture
def prints_conn(tmp_path):
    import sqlite3

    from src.state.schema.observation_prints_schema import ensure_table

    conn = sqlite3.connect(tmp_path / "prints.db")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    return conn


def _rows_for(day: str):
    return [
        _Row(f"{day}T05:53:00-0500", datetime(2026, 9, 11, 10, 53, tzinfo=timezone.utc), 78.08, True, "KHOU 111053Z"),
        _Row(f"{day}T15:51:00-0500", datetime(2026, 9, 11, 20, 51, tzinfo=timezone.utc), 93.92, True, "KHOU 112051Z"),
        _Row(f"{day}T16:12:00-0500", datetime(2026, 9, 11, 21, 12, tzinfo=timezone.utc), 93.02, False, "SPECI unofficial"),
    ]


def test_official_rows_are_published_with_the_pages_own_clock(prints_conn):
    """The hourly view publishes only official reports, keyed on page time."""
    written = _append_noaa_wrh_prints(
        prints_conn,
        city_name="Houston",
        station="KHOU",
        unit="F",
        rows=_rows_for("2026-09-11"),
        target_date_local=date(2026, 9, 11),
        view="hourly",
        fetch_utc=datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc),
    )
    assert written == 2  # the unofficial SPECI is excluded by the hourly view

    rows = prints_conn.execute(
        "SELECT source_channel, publish_ts_utc, value_native, unit, station_id "
        "FROM observation_prints ORDER BY publish_ts_utc"
    ).fetchall()
    assert [r["source_channel"] for r in rows] == [noaa_wrh_source_tag("KHOU")] * 2
    assert [float(r["value_native"]) for r in rows] == [78.08, 93.92]
    assert {r["unit"] for r in rows} == {"F"}
    assert {r["station_id"] for r in rows} == {"KHOU"}
    # The page's own publication clock, never our fetch wall clock.
    assert rows[1]["publish_ts_utc"].startswith("2026-09-11T20:51")


def test_all_data_view_publishes_every_row(prints_conn):
    """The 37 non-clause cities resolve off every row the page shows."""
    written = _append_noaa_wrh_prints(
        prints_conn,
        city_name="Houston",
        station="KHOU",
        unit="F",
        rows=_rows_for("2026-09-11"),
        target_date_local=date(2026, 9, 11),
        view="all",
        fetch_utc=datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc),
    )
    assert written == 3


def test_rows_outside_the_target_local_date_are_not_published(prints_conn):
    """The window carries a margin of the next day's reports; they are not ours."""
    written = _append_noaa_wrh_prints(
        prints_conn,
        city_name="Houston",
        station="KHOU",
        unit="F",
        rows=_rows_for("2026-09-12"),
        target_date_local=date(2026, 9, 11),
        view="all",
        fetch_utc=datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc),
    )
    assert written == 0


def test_republishing_the_same_fetch_is_a_no_op(prints_conn):
    """Append-only dedup: a re-fetch must not duplicate a seen reading."""
    args = dict(
        city_name="Houston",
        station="KHOU",
        unit="F",
        rows=_rows_for("2026-09-11"),
        target_date_local=date(2026, 9, 11),
        view="hourly",
        fetch_utc=datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc),
    )
    assert _append_noaa_wrh_prints(prints_conn, **args) == 2
    assert _append_noaa_wrh_prints(prints_conn, **args) == 0
    assert prints_conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()[0] == 2


def test_the_published_channel_is_authorised_day0_evidence_for_a_noaa_city():
    """The ledger channel must pass the Day0 source gate, or this is dead data."""
    from src.events.triggers.day0_extreme_updated import _source_matches_config

    assert _source_matches_config(noaa_wrh_source_tag("KHOU"), "noaa") is True
    # The Ogimet mirror stays authorised as the fallback lane.
    assert _source_matches_config("ogimet_metar_khou", "noaa") is True
    # And the widening must not admit a foreign family.
    assert _source_matches_config("wu_icao_history", "noaa") is False


def test_the_published_reading_is_an_absorbing_day0_fact():
    """A page print must be able to truncate payoff support, like the METAR lane."""
    from src.events.day0_authority import (
        DAY0_ABSORBING_FINALITIES,
        day0_evidence_finality,
    )

    finality = day0_evidence_finality(
        {"settlement_source": noaa_wrh_source_tag("KHOU")}
    )
    assert finality in DAY0_ABSORBING_FINALITIES
