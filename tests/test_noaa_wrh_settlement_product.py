# Created: 2026-09-12
# Last audited: 2026-09-12
# Purpose: Pin the weather.gov/wrh/timeseries settlement product — page render law,
#   per-city view selection, settlement-source precedence, and the backfill report.
# Reuse: Read src/data/noaa_wrh_timeseries.py's measured facts and
#   docs/operations/current/noaa_settlement_page_truth/evidence.md first.
# Authority basis: docs/operations/current/noaa_settlement_page_truth/{PLAN.md,evidence.md}
"""The settlement product for NOAA cities must reproduce the page, exactly.

Every expected number here was read off the market's own resolution surface and
checked against the chain-winning bin for that day, so a failure means the
product has drifted from the contract — not that the fixture needs updating.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import cities_by_name, validate_cities_config
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.noaa_wrh_timeseries import (
    MAX_REQUEST_WINDOW_DAYS,
    daily_extreme,
    recent_minutes_for_local_day,
    request_url_without_token,
    rows_from_payload,
)
from src.state.db import init_schema

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "noaa_wrh"

#: The exact set of cities whose market descriptions carry the "Show Hourly
#: Data" clause, from the 2026-09-12 gamma-api census of 270 active events.
HOURLY_VIEW_CITIES = {
    "Atlanta", "Austin", "Chicago", "Dallas", "Denver", "Houston",
    "Los Angeles", "Miami", "NYC", "San Francisco", "Seattle",
}


def _rows(station: str):
    payload = json.loads((FIXTURE_DIR / f"syn_{station}.json").read_text())
    return rows_from_payload(payload, station)


def _rounded(city_name: str, value: float) -> float:
    return SettlementSemantics.for_city(cities_by_name[city_name]).round_single(value)


# ---------------------------------------------------------------------------
# Page render law
# ---------------------------------------------------------------------------


def test_hourly_view_reproduces_the_page_for_klga_2026_09_11():
    """NYC 2026-09-11 under the contract's view: high 80, low 72.

    The chain settled high 80-81 and low 72-73, so both values land in-bin.
    """
    rows = _rows("KLGA")
    high = daily_extreme(
        rows, target_date_local=date(2026, 9, 11), view="hourly", metric="high",
    )
    low = daily_extreme(
        rows, target_date_local=date(2026, 9, 11), view="hourly", metric="low",
    )

    assert _rounded("NYC", high.value) == 80.0
    assert _rounded("NYC", low.value) == 72.0
    # The extremum is a routine hourly METAR, and the hourly view shows exactly
    # the 24 routine reports of that local day.
    assert high.local_timestamp == "2026-09-11T15:51:00-0400"
    assert low.local_timestamp == "2026-09-11T06:51:00-0400"
    assert (high.n_rows, high.n_official) == (24, 24)


def test_all_data_view_differs_from_the_hourly_view_on_the_same_day():
    """The view is a settlement choice: it changes the number, not the display.

    KLGA 2026-09-11 reads 81 across every row and 80 across the shown rows; the
    5-minute AUTO rows that produce 81 are the ones the page hides under "Show
    Hourly Data".
    """
    rows = _rows("KLGA")
    target = date(2026, 9, 11)
    hourly = daily_extreme(rows, target_date_local=target, view="hourly", metric="high")
    every = daily_extreme(rows, target_date_local=target, view="all", metric="high")

    assert _rounded("NYC", hourly.value) == 80.0
    assert _rounded("NYC", every.value) == 81.0
    assert every.n_rows == 312
    assert every.n_official == 24


def test_hourly_view_picks_the_chain_bin_where_all_data_view_misses():
    """Houston 2026-09-10 high: the hourly view is in-bin, all-data is not.

    The chain settled the 88-89 bin. The hourly view gives 89; every-row gives
    91. This is the per-day shape of the 15-37% disagreement that motivated the
    product.
    """
    rows = _rows("KHOU")
    target = date(2026, 9, 10)
    hourly = daily_extreme(rows, target_date_local=target, view="hourly", metric="high")
    every = daily_extreme(rows, target_date_local=target, view="all", metric="high")

    assert _rounded("Houston", hourly.value) == 89.0
    assert 88.0 <= _rounded("Houston", hourly.value) <= 89.0
    assert _rounded("Houston", every.value) == 91.0


def test_houston_2026_09_11_high_is_the_chain_value_not_the_ogimet_value():
    """94, matching the chain's 94-95 bin; the Ogimet row said 93 and DISPUTED."""
    high = daily_extreme(
        _rows("KHOU"),
        target_date_local=date(2026, 9, 11), view="hourly", metric="high",
    )
    assert _rounded("Houston", high.value) == 94.0


def test_speci_row_value_comes_from_the_feed_not_from_a_metar_reparse():
    """KATL 2026-09-05 low settles 73 from the feed; a T-group re-parse says 75.

    The extremum is the SPECI at 19:35 local. Synoptic's air_temp for the row is
    73.4 degF, which settles to 73, and the chain's bin for that cell is 72-73.
    The same report's own text carries `T02390211` — 23.9 degC, i.e. 75.02 degF,
    which would settle 75 and fall outside that bin. The page shows the feed
    value, so the product stores it verbatim and keeps the METAR text in
    provenance only; re-deriving the temperature from the text would reintroduce
    the disagreement this product exists to remove.
    """
    rows = _rows("KATL")
    low = daily_extreme(
        rows, target_date_local=date(2026, 9, 5), view="hourly", metric="low",
    )
    assert _rounded("Atlanta", low.value) == 73.0
    assert low.value == pytest.approx(73.4)
    assert low.raw_metar is not None
    assert low.raw_metar.startswith("KATL")
    # A SPECI is shown only because its text starts with the station id; it
    # carries no sea-level pressure, which is what marks a routine report.
    shown = [row for row in rows if row.local_timestamp == low.local_timestamp]
    assert shown and not shown[0].is_routine_metar
    assert shown[0].is_official_report
    # The T-group in that text would round to a different, out-of-bin value.
    t_group = re.search(r"\bT(\d)(\d{3})(\d)(\d{3})\b", low.raw_metar)
    assert t_group is not None
    reparsed_c = int(t_group.group(2)) / 10.0
    reparsed_f = reparsed_c * 9 / 5 + 32
    assert _rounded("Atlanta", reparsed_f) == 75.0


def test_katl_speci_low_is_also_the_page_value_on_2026_08_27():
    """A second SPECI-extremum day, where feed and re-parse happen to agree.

    Kept alongside the discriminating case so the fixture covers both: this day
    reads 72 either way, which is why it cannot stand in for the test above.
    """
    low = daily_extreme(
        _rows("KATL"),
        target_date_local=date(2026, 8, 27), view="hourly", metric="low",
    )
    assert _rounded("Atlanta", low.value) == 72.0
    assert low.value == pytest.approx(71.6)


def test_station_dark_day_returns_none_and_writes_nothing():
    """No rows for the local date is not a value; it must stay unwritten.

    The contract resolves a no-data day to the lowest bracket. Zeus must never
    reproduce that guess — the caller writes nothing and the market stays
    DISPUTED.
    """
    rows = _rows("KLGA")
    assert daily_extreme(
        rows, target_date_local=date(2026, 8, 1), view="hourly", metric="high",
    ) is None
    assert daily_extreme(
        rows, target_date_local=date(2026, 8, 1), view="all", metric="low",
    ) is None


def test_metric_view_city_reads_celsius_from_the_same_law():
    """London (EGLC, all-data view) reads 21/16 degC off the metric feed."""
    rows = _rows("EGLC")
    high = daily_extreme(
        rows, target_date_local=date(2026, 9, 11), view="all", metric="high",
    )
    low = daily_extreme(
        rows, target_date_local=date(2026, 9, 11), view="all", metric="low",
    )
    assert _rounded("London", high.value) == 21.0
    assert _rounded("London", low.value) == 16.0


def test_unknown_view_or_metric_is_rejected():
    rows = _rows("KLGA")
    with pytest.raises(ValueError):
        daily_extreme(
            rows, target_date_local=date(2026, 9, 11), view="whatever", metric="high",
        )
    with pytest.raises(ValueError):
        daily_extreme(
            rows, target_date_local=date(2026, 9, 11), view="hourly", metric="mean",
        )


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_fahrenheit_view_sends_units_and_metric_view_does_not():
    """The two views are one units parameter apart; a third view does not exist."""
    f_url = request_url_without_token("KLGA", unit="F", recent_minutes=120)
    c_url = request_url_without_token("EGLC", unit="C", recent_minutes=120)
    assert "units=temp|F,speed|kts,english" in f_url
    assert "units=" not in c_url
    for url in (f_url, c_url):
        assert "obtimezone=local" in url
        assert "token=REDACTED" in url


def test_recent_window_covers_the_local_day_and_respects_the_request_cap():
    minutes = recent_minutes_for_local_day(
        date(2026, 9, 11), "America/New_York",
        now_utc=datetime(2026, 9, 12, 5, 30, tzinfo=timezone.utc),
    )
    # Local midnight 2026-09-11 is 04:00Z; 25.5h elapsed plus the 3h margin.
    assert minutes == 25 * 60 + 30 + 180
    capped = recent_minutes_for_local_day(
        date(2026, 1, 1), "America/New_York",
        now_utc=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assert capped == MAX_REQUEST_WINDOW_DAYS * 24 * 60


# ---------------------------------------------------------------------------
# Config contract
# ---------------------------------------------------------------------------


def test_hourly_page_view_is_exactly_the_eleven_clause_carrying_cities():
    configured = {
        name for name, city in cities_by_name.items()
        if city.settlement_page_view == "hourly"
    }
    assert configured == HOURLY_VIEW_CITIES
    for city in cities_by_name.values():
        assert city.settlement_page_view in ("hourly", "all")
        if city.settlement_page_view == "hourly":
            assert city.settlement_source_type == "noaa"


def test_live_cities_config_has_no_page_view_warning():
    warnings = [w for w in validate_cities_config() if "settlement_page_view" in w]
    assert warnings == []


def test_page_view_warnings_fire_on_a_bad_value_and_a_non_noaa_city():
    from dataclasses import replace

    nyc = cities_by_name["NYC"]
    bad_value = replace(nyc, settlement_page_view="Hourly Data")
    assert any(
        "settlement_page_view" in w for w in validate_cities_config([bad_value])
    )

    wu_city = replace(
        cities_by_name["Jinan"], settlement_page_view="hourly",
    )
    assert any(
        "settlement_page_view='hourly' requires" in w
        for w in validate_cities_config([wu_city])
    )


# ---------------------------------------------------------------------------
# Settlement source precedence
# ---------------------------------------------------------------------------


def _observations_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_observation(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    source: str,
    station_id: str,
    high: float,
    low: float,
    unit: str = "F",
) -> None:
    conn.execute(
        """INSERT INTO observations
           (city, target_date, source, high_temp, low_temp, unit, station_id,
            authority, fetched_at, high_local_time, low_local_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?)""",
        (
            city, target_date, source, high, low, unit, station_id,
            "2026-09-12T00:00:00+00:00",
            f"{target_date}T15:51:00-04:00",
            f"{target_date}T06:51:00-04:00",
        ),
    )
    conn.commit()


@pytest.mark.parametrize(
    "module_path",
    ["src.execution.harvester", "src.ingest.harvester_truth_writer"],
)
def test_settlement_lookup_prefers_the_page_row_over_the_ogimet_row(module_path):
    """Both harvester copies must route to the page product, in either row order.

    The ingest-side writer is a verbatim copy of the live one, so a precedence
    fix that lands in only one of them would let the two lanes settle the same
    market on different numbers.
    """
    import importlib

    module = importlib.import_module(module_path)
    city = cities_by_name["NYC"]

    for order in (("noaa_wrh_klga", "ogimet_metar_klga"), ("ogimet_metar_klga", "noaa_wrh_klga")):
        conn = _observations_conn()
        values = {"noaa_wrh_klga": (80.0, 72.0), "ogimet_metar_klga": (80.6, 71.6)}
        for source in order:
            high, low = values[source]
            _insert_observation(
                conn, city="NYC", target_date="2026-09-11", source=source,
                station_id="KLGA", high=high, low=low,
            )

        obs = module._lookup_settlement_obs(
            conn, city, "2026-09-11", temperature_metric="high",
        )
        assert obs is not None
        assert obs["source"] == "noaa_wrh_klga"
        assert obs["observed_temp"] == pytest.approx(80.0)
        assert obs["data_version"] == "noaa_wrh_timeseries_v1"
        conn.close()


@pytest.mark.parametrize(
    "module_path",
    ["src.execution.harvester", "src.ingest.harvester_truth_writer"],
)
def test_ogimet_row_still_settles_when_no_page_row_exists(module_path):
    """The page feed can refuse or find no rows; the mirror must still settle."""
    import importlib

    module = importlib.import_module(module_path)
    conn = _observations_conn()
    _insert_observation(
        conn, city="NYC", target_date="2026-09-11", source="ogimet_metar_klga",
        station_id="KLGA", high=80.6, low=71.6,
    )

    obs = module._lookup_settlement_obs(
        conn, cities_by_name["NYC"], "2026-09-11", temperature_metric="high",
    )
    assert obs is not None
    assert obs["source"] == "ogimet_metar_klga"
    assert obs["data_version"] == "ogimet_metar"
    conn.close()


@pytest.mark.parametrize(
    "module_path",
    ["src.execution.harvester", "src.ingest.harvester_truth_writer"],
)
def test_page_source_is_settlement_family_valid_for_noaa_only(module_path):
    import importlib

    module = importlib.import_module(module_path)
    assert module._source_matches_settlement_family("noaa_wrh_klga", "noaa")
    assert module._source_matches_settlement_family("ogimet_metar_klga", "noaa")
    assert not module._source_matches_settlement_family("noaa_wrh_klga", "wu_icao")
    assert not module._source_matches_settlement_family("noaa_wrh_klga", "hko")


# ---------------------------------------------------------------------------
# Backfill CLI
# ---------------------------------------------------------------------------


def _temp_world_db(tmp_path: Path) -> Path:
    """A schema-complete DB carrying the rows the dry-run compares against."""
    db_path = tmp_path / "wrh-backfill.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for city, station, target_date, high, low in (
        ("NYC", "KLGA", "2026-09-11", 80.6, 71.6),
        ("Houston", "KHOU", "2026-09-11", 93.2, 78.8),
        ("Houston", "KHOU", "2026-09-10", 89.6, 78.8),
    ):
        _insert_observation(
            conn, city=city, target_date=target_date,
            source=f"ogimet_metar_{station.lower()}", station_id=station,
            high=high, low=low,
        )
    for city, target_date, metric, value, lo, hi, authority in (
        ("NYC", "2026-09-11", "high", 81.0, 80.0, 81.0, "VERIFIED"),
        ("Houston", "2026-09-11", "high", 93.0, 94.0, 95.0, "DISPUTED"),
        ("Houston", "2026-09-10", "high", 90.0, 88.0, 89.0, "DISPUTED"),
    ):
        conn.execute(
            """INSERT INTO settlements
               (city, target_date, temperature_metric, settlement_value,
                pm_bin_lo, pm_bin_hi, winning_bin, authority, unit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'F')""",
            (city, target_date, metric, value, lo, hi, f"{int(value)}°F", authority),
        )
    conn.commit()
    conn.close()
    return db_path


def _load_backfill_module():
    import importlib

    return importlib.import_module("scripts.backfill_noaa_wrh")


def test_backfill_dry_run_reports_containment_changes_without_writing(tmp_path):
    """The dry-run must name the label change, not just the value change."""
    module = _load_backfill_module()
    db_path = _temp_world_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    summary = module.backfill(
        conn,
        start=date(2026, 9, 10),
        end=date(2026, 9, 11),
        city_filter=["NYC", "Houston"],
        apply_writes=False,
        fixture_dir=FIXTURE_DIR,
    )
    lines = "\n".join(summary["lines"])

    assert summary["days_written"] == 0
    assert summary["days_seen"] == 4
    # Houston's two DISPUTED highs become in-bin under the page's law.
    assert "Houston 2026-09-11" in lines and "page=94" in lines
    assert "Houston 2026-09-10" in lines and "page=89" in lines
    assert lines.count("OUT->in FIXES") >= 2
    # NYC 2026-09-11 is already in-bin either way, so nothing is claimed fixed.
    assert "NYC 2026-09-11" in lines and "page=80" in lines
    assert "in->OUT REGRESSES" not in lines
    # Nothing was written.
    assert conn.execute(
        "SELECT COUNT(*) FROM observations WHERE source LIKE 'noaa_wrh_%'"
    ).fetchone()[0] == 0
    conn.close()


def test_backfill_apply_writes_page_rows_that_settlement_then_prefers(tmp_path):
    module = _load_backfill_module()
    db_path = _temp_world_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    summary = module.backfill(
        conn,
        start=date(2026, 9, 11),
        end=date(2026, 9, 11),
        city_filter=["Houston"],
        apply_writes=True,
        fixture_dir=FIXTURE_DIR,
    )
    assert summary["days_written"] == 1

    row = conn.execute(
        """SELECT high_temp, low_temp, unit, authority, data_source_version,
                  station_id, high_local_time
             FROM observations
            WHERE city = 'Houston' AND target_date = '2026-09-11'
              AND source = 'noaa_wrh_khou'"""
    ).fetchone()
    assert row is not None
    assert row["authority"] == "VERIFIED"
    assert row["unit"] == "F"
    assert row["data_source_version"] == "noaa_wrh_timeseries_v1"
    assert row["station_id"] == "KHOU"
    # The extremum's own local instant, not a synthesized peak hour.
    assert row["high_local_time"].startswith("2026-09-11T14:53:00")
    assert _rounded("Houston", float(row["high_temp"])) == 94.0

    from src.execution import harvester

    obs = harvester._lookup_settlement_obs(
        conn, cities_by_name["Houston"], "2026-09-11", temperature_metric="high",
    )
    assert obs["source"] == "noaa_wrh_khou"
    assert obs["data_version"] == "noaa_wrh_timeseries_v1"
    conn.close()


def test_backfill_rejects_a_non_noaa_city_and_an_oversized_chunk(tmp_path):
    module = _load_backfill_module()
    conn = sqlite3.connect(_temp_world_db(tmp_path))
    conn.row_factory = sqlite3.Row
    with pytest.raises(ValueError, match="not NOAA-settled"):
        module.backfill(
            conn, start=date(2026, 9, 11), end=date(2026, 9, 11),
            city_filter=["Hong Kong"], fixture_dir=FIXTURE_DIR,
        )
    with pytest.raises(ValueError, match="chunk_days"):
        module.backfill(
            conn, start=date(2026, 9, 11), end=date(2026, 9, 11),
            chunk_days=MAX_REQUEST_WINDOW_DAYS + 1, fixture_dir=FIXTURE_DIR,
        )
    conn.close()


def test_backfill_is_registered_in_the_script_manifest():
    import yaml

    manifest = yaml.safe_load(
        (REPO_ROOT / "architecture" / "script_manifest.yaml").read_text()
    )
    entry = manifest["scripts"]["backfill_noaa_wrh.py"]
    assert entry["apply_flag"] == "--apply"
    assert entry["dry_run_default"] is True
    assert "observations" in entry["write_targets"]
