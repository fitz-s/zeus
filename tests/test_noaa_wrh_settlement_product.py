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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import cities_by_name, validate_cities_config
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.noaa_wrh_timeseries import (
    MAX_REQUEST_WINDOW_DAYS,
    WrhWindowTooOld,
    daily_extreme,
    recent_minutes_for_local_day,
    request_url_without_token,
    rows_from_payload,
)

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


def test_recent_window_covers_the_local_day():
    minutes = recent_minutes_for_local_day(
        date(2026, 9, 11), "America/New_York",
        now_utc=datetime(2026, 9, 12, 5, 30, tzinfo=timezone.utc),
    )
    # Local midnight 2026-09-11 is 04:00Z; 25.5h elapsed plus the 3h margin.
    assert minutes == 25 * 60 + 30 + 180


def test_recent_window_refuses_a_day_it_cannot_reach_instead_of_clamping():
    """A clamped window returns the day's TAIL, which looks like a whole day.

    Measured on the KHOU feed: for a target date seven days old the clamped
    window starts after the day's true minimum, so the low reads 80.96 degF
    instead of 78.98 and would be written VERIFIED. The only safe shape is a
    typed refusal, because a truncated row set and a complete one are otherwise
    indistinguishable to the caller.
    """
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    # 5 and 6 days old still fit inside the cap.
    for age in (5, 6):
        target = date(2026, 9, 12) - timedelta(days=age)
        minutes = recent_minutes_for_local_day(
            target, "America/New_York", now_utc=now,
        )
        assert minutes <= MAX_REQUEST_WINDOW_DAYS * 24 * 60

    # 7 and 8 days old cannot, and must raise rather than clamp.
    for age in (7, 8):
        target = date(2026, 9, 12) - timedelta(days=age)
        with pytest.raises(WrhWindowTooOld):
            recent_minutes_for_local_day(target, "America/New_York", now_utc=now)


def test_truncated_window_would_have_given_a_wrong_low_for_khou():
    """Pin the exact defect the refusal prevents, so it cannot be re-introduced.

    If a future change clamps again instead of raising, this test still shows
    what the clamped window computes: a low 1.98 degF above the real one.
    """
    rows = _rows("KHOU")
    target = date(2026, 9, 9)
    full = daily_extreme(
        rows, target_date_local=target, view="hourly", metric="low",
    )
    assert full.value == pytest.approx(78.98)

    # The window a 7-day-old clamp would have produced.
    now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    window_start = now - timedelta(minutes=MAX_REQUEST_WINDOW_DAYS * 24 * 60)
    truncated = daily_extreme(
        [row for row in rows if row.utc >= window_start],
        target_date_local=target, view="hourly", metric="low",
    )
    # Non-empty, so the "station dark" guard would NOT have fired.
    assert truncated is not None
    assert truncated.value == pytest.approx(80.96)
    assert truncated.value > full.value

    # And the live lane refuses that day rather than writing it.
    with pytest.raises(WrhWindowTooOld):
        recent_minutes_for_local_day(target, "America/Chicago", now_utc=now)


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


def _attached(db_path: Path, world_path: Path) -> sqlite3.Connection:
    """Open a forecasts file with world ATTACHed, as every writer here does.

    The observation and its world-class data_coverage row land in one SAVEPOINT,
    so a single-file connection cannot service the write at all. A file also
    cannot ATTACH itself, which is why the fixture is always a pair.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    return conn


def _observations_conn(tmp_path: Path) -> sqlite3.Connection:
    """A forecasts connection on the live settlement schema, world ATTACHed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return _attached(*_live_schema_db_pair(tmp_path))


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
def test_settlement_lookup_prefers_the_page_row_over_the_ogimet_row(module_path, tmp_path):
    """Both harvester copies must route to the page product, in either row order.

    The ingest-side writer is a verbatim copy of the live one, so a precedence
    fix that lands in only one of them would let the two lanes settle the same
    market on different numbers.
    """
    import importlib

    module = importlib.import_module(module_path)
    city = cities_by_name["NYC"]

    for order in (("noaa_wrh_klga", "ogimet_metar_klga"), ("ogimet_metar_klga", "noaa_wrh_klga")):
        conn = _observations_conn(tmp_path / f"order_{'_'.join(order)}")
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
def test_ogimet_row_still_settles_when_no_page_row_exists(module_path, tmp_path):
    """The page feed can refuse or find no rows; the mirror must still settle."""
    import importlib

    module = importlib.import_module(module_path)
    conn = _observations_conn(tmp_path)
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


def _live_schema() -> dict[str, list[str]]:
    """DDL captured verbatim from the live DBs' sqlite_master.

    Hand-written DDL would miss what live history put there: three of the four
    forecasts tables are recorded as `CREATE TABLE "name"` (quoted, the residue
    of past ALTERs), and `settlements` and `settlement_outcomes` carry six
    triggers that gate authority transitions and VERIFIED-row integrity. A
    fixture built from the schema initialiser alone would let a write pass that
    the live DB rejects.
    """
    return json.loads((FIXTURE_DIR / "live_settlement_schema.json").read_text())


def _live_schema_db_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Build an empty (forecasts, world) pair with the live settlement schema."""
    schema = _live_schema()
    paths = {}
    for label, filename in (("forecasts", "zeus-forecasts.db"), ("world", "zeus-world.db")):
        path = tmp_path / filename
        conn = sqlite3.connect(path)
        for statement in schema[label]:
            conn.execute(statement)
        conn.commit()
        conn.close()
        paths[label] = path
    return paths["forecasts"], paths["world"]


def _temp_forecasts_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A live-schema pair carrying the rows the dry-run compares against."""
    db_path, world_path = _live_schema_db_pair(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    return db_path, world_path


def _load_backfill_module():
    import importlib

    return importlib.import_module("scripts.backfill_noaa_wrh")


def test_backfill_dry_run_reports_containment_changes_without_writing(tmp_path):
    """The dry-run must name the label change, not just the value change."""
    module = _load_backfill_module()
    db_path, world_path = _temp_forecasts_pair(tmp_path)
    conn = _attached(db_path, world_path)

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
    db_path, world_path = _temp_forecasts_pair(tmp_path)
    conn = _attached(db_path, world_path)

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
    conn = _attached(*_temp_forecasts_pair(tmp_path))
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


def test_backfill_writes_the_forecasts_db_not_the_world_ghost():
    """The CLI must target the forecasts DB; the world copy is an empty ghost.

    `observations` is forecast-class post-K1 (architecture/db_table_ownership.yaml
    marks the world copy `legacy_archived`), so a run against world would write
    rows no settlement reader ever looks at — a silent no-op that still prints a
    success summary. This pins the connection helper by name rather than trusting
    the summary.
    """
    module = _load_backfill_module()
    source = (REPO_ROOT / "scripts" / "backfill_noaa_wrh.py").read_text()

    assert "get_forecasts_connection_with_world" in source
    assert hasattr(module, "get_forecasts_connection_with_world")
    # The world schema initialiser must never run against a forecasts file, and
    # the world-only connection helper must not be reachable from here.
    assert "init_schema" not in source
    assert "get_world_connection(" not in source
    # ZEUS_WORLD_DB_PATH is referenced, but only to REFUSE an explicit path that
    # names it — never to open it. Pin that distinction rather than the bare name,
    # so the assertion keeps its meaning instead of breaking on a legitimate use.
    assert "get_world_connection" not in source.replace(
        "get_forecasts_connection_with_world", ""
    )
    for line in source.splitlines():
        if "ZEUS_WORLD_DB_PATH" in line:
            assert "resolve()" in line or "import" in line, (
                f"ZEUS_WORLD_DB_PATH used for something other than the "
                f"canonical-path refusal: {line.strip()!r}"
            )


def test_explicit_db_paths_still_take_both_writer_locks(tmp_path, monkeypatch):
    """The --db branch must not trade the writer flock for convenience.

    The canonical path gets its locks from get_forecasts_connection_with_world.
    An earlier version of the explicit branch took none, so a run pointed at real
    files would have written with no protection against the live ingest daemon's
    writers — the WAL write-lock collision the lock discipline exists to prevent.
    """
    module = _load_backfill_module()
    from src.state import db_writer_lock as dwl

    # A subdirectory, not tmp_path itself: the conftest DB-isolation fixture
    # points the canonical path constants at tmp_path/zeus-forecasts.db, so
    # building the fixture pair there would trip the canonical-path refusal.
    pair_dir = tmp_path / "explicit_pair"
    pair_dir.mkdir()
    db_path, world_path = _live_schema_db_pair(pair_dir)
    taken: list[str] = []
    real_lock = dwl.db_writer_lock

    import contextlib as _contextlib

    @_contextlib.contextmanager
    def spy(path, write_class, **kwargs):
        taken.append(Path(str(path)).name)
        with real_lock(path, write_class, **kwargs) as held:
            yield held

    monkeypatch.setattr(dwl, "db_writer_lock", spy)
    with module._open_target(str(db_path), str(world_path)) as conn:
        assert [row[1] for row in conn.execute("PRAGMA database_list")] == [
            "main", "world",
        ]
    # Both files, forecasts before world per canonical_lock_order.
    assert taken == [db_path.name, world_path.name]


def test_explicit_db_paths_refuse_to_name_a_canonical_database():
    """Naming the live files explicitly can only be a mistake; refuse it.

    The no-flag path already writes the live pair under both locks, so the only
    effect of naming them here would be a second lock holder contending with the
    daemon rather than cooperating with it.
    """
    module = _load_backfill_module()
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH

    assert module._canonical_db_refusal(
        str(ZEUS_FORECASTS_DB_PATH), str(ZEUS_WORLD_DB_PATH)
    )
    # Either side alone is enough to refuse.
    assert module._canonical_db_refusal(str(ZEUS_FORECASTS_DB_PATH), "/tmp/w.db")
    assert module._canonical_db_refusal("/tmp/f.db", str(ZEUS_WORLD_DB_PATH))
    # A fixture pair is fine.
    assert module._canonical_db_refusal("/tmp/f.db", "/tmp/w.db") is None
    # And the CLI exits non-zero rather than writing.
    assert module.main([
        "--start", "2026-09-11", "--end", "2026-09-11",
        "--db", str(ZEUS_FORECASTS_DB_PATH),
        "--world-db", str(ZEUS_WORLD_DB_PATH),
    ]) == 2


def test_backfill_requires_db_and_world_db_together():
    """A forecasts file alone cannot service the world-class coverage write."""
    module = _load_backfill_module()
    assert module.main(
        ["--start", "2026-09-11", "--end", "2026-09-11", "--db", "/tmp/x.db"]
    ) == 2


# ---------------------------------------------------------------------------
# Operator sequence, end to end
# ---------------------------------------------------------------------------


def test_operator_sequence_heals_houston_through_the_ingest_truth_writer(
    tmp_path, monkeypatch,
):
    """Backfill then the ingest truth writer flips Houston 2026-09-11 to VERIFIED.

    This is the packet's landing sequence on a live-schema fixture: the Ogimet
    row settles 93 against the chain's 94-95 bin (DISPUTED), the page row settles
    94, and re-running the truth writer must rewrite settlements AND
    settlement_outcomes to VERIFIED with the page's data_version. Gamma is stubbed
    so the test makes no network call; everything below the paginator is the real
    write path, including SettlementSemantics and the live triggers.
    """
    from src.ingest import harvester_truth_writer as tw

    db_path, world_path = _live_schema_db_pair(tmp_path)
    conn = _attached(db_path, world_path)

    # Pre-state: the wrong value, disputed against the chain's bin.
    _insert_observation(
        conn, city="Houston", target_date="2026-09-11",
        source="ogimet_metar_khou", station_id="KHOU", high=93.2, low=78.8,
    )
    conn.execute(
        """INSERT INTO settlements
           (city, target_date, temperature_metric, settlement_value, pm_bin_lo,
            pm_bin_hi, winning_bin, authority, unit, data_version)
           VALUES ('Houston', '2026-09-11', 'high', 93.0, 94.0, 95.0, '93°F',
                   'DISPUTED', 'F', 'ogimet_metar')""",
    )
    conn.commit()

    # Step 1: the page product lands as a second observation row.
    backfill = _load_backfill_module()
    summary = backfill.backfill(
        conn, start=date(2026, 9, 11), end=date(2026, 9, 11),
        city_filter=["Houston"], apply_writes=True, fixture_dir=FIXTURE_DIR,
    )
    assert summary["days_written"] == 1

    # Step 2: the ingest truth writer re-resolves the settled row. Gamma is
    # stubbed with the event the chain actually resolved for that cell.
    event = {
        "slug": "highest-temperature-in-houston-on-september-11-2026",
        "title": "Highest temperature in Houston on September 11?",
        "markets": [
            {
                "question": "Will the high temperature in Houston be 94-95°F?",
                "conditionId": "cond-94-95",
                "clobTokenIds": '["yes-94", "no-94"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
                "umaResolutionStatus": "resolved",
                "closed": True,
            },
            {
                "question": "Will the high temperature in Houston be 92-93°F?",
                "conditionId": "cond-92-93",
                "clobTokenIds": '["yes-92", "no-92"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0", "1"]',
                "umaResolutionStatus": "resolved",
                "closed": True,
            },
        ],
    }
    monkeypatch.setattr(tw, "_fetch_open_settling_markets", lambda: [event])

    result = tw.write_settlement_truth_for_open_markets(conn)
    assert result["errors"] == 0
    assert result["markets_resolved"] == 1

    settled = conn.execute(
        """SELECT settlement_value, authority, data_version, winning_bin,
                  pm_bin_lo, pm_bin_hi
             FROM settlements
            WHERE city = 'Houston' AND target_date = '2026-09-11'
              AND temperature_metric = 'high'"""
    ).fetchone()
    assert settled["authority"] == "VERIFIED"
    assert settled["settlement_value"] == 94.0
    assert (settled["pm_bin_lo"], settled["pm_bin_hi"]) == (94.0, 95.0)
    assert settled["data_version"] == "noaa_wrh_timeseries_v1"

    outcome = conn.execute(
        """SELECT settlement_value, authority, settlement_unit
             FROM settlement_outcomes
            WHERE city = 'Houston' AND target_date = '2026-09-11'
              AND temperature_metric = 'high'"""
    ).fetchone()
    assert outcome is not None
    assert outcome["authority"] == "VERIFIED"
    assert outcome["settlement_value"] == 94.0
    assert outcome["settlement_unit"] == "F"
    conn.close()


def test_backfill_chunks_never_exceed_the_request_cap(tmp_path):
    """Every window, after the local-day widening, must fit the provider cap.

    An earlier version advanced by chunk_days and then widened by a day on each
    side, so the default 7 produced an 8d23h span. fetch_wrh_timeseries rejects
    that with a bare ValueError, which is not a WrhError, so it escaped the
    per-window handler and aborted the entire run on chunk 1 — every backfill
    longer than about a week died before its first request.
    """
    module = _load_backfill_module()
    start, end = date(2026, 8, 23), date(2026, 9, 11)

    for chunk_days in range(1, MAX_REQUEST_WINDOW_DAYS + 1):
        windows = module._chunks(start, end, chunk_days)
        for window_start, window_end in windows:
            # _fetch_window turns a window into start 00:00Z .. end 23:59Z.
            span = (
                datetime(
                    window_end.year, window_end.month, window_end.day,
                    23, 59, tzinfo=timezone.utc,
                )
                - datetime(
                    window_start.year, window_start.month, window_start.day,
                    tzinfo=timezone.utc,
                )
            )
            assert span <= timedelta(days=MAX_REQUEST_WINDOW_DAYS), (
                f"chunk_days={chunk_days} window "
                f"{window_start}..{window_end} spans {span}"
            )
        # Every target date must still sit inside some window.
        for offset in range((end - start).days + 1):
            target = start + timedelta(days=offset)
            assert any(a <= target <= b for a, b in windows), (
                f"chunk_days={chunk_days} leaves {target} uncovered"
            )


def test_backfill_walks_a_twenty_day_range_with_default_flags(tmp_path, monkeypatch):
    """The commit's own replay range must run end to end without raising.

    The fixture path short-circuits before the request validator, so this test
    routes each window through the real ``fetch_wrh_timeseries`` window check
    first and only then serves fixture rows. Without that, a chunking regression
    would pass here and only fail in production.
    """
    module = _load_backfill_module()
    from src.data import noaa_wrh_timeseries as wrh

    seen: list[tuple[date, date]] = []
    real_rows = _rows("KHOU")

    def validating_fetch(station, start_utc=None, end_utc=None, **kwargs):
        # Reuse the shipped cap check rather than restating it here.
        if end_utc - start_utc > timedelta(days=wrh.MAX_REQUEST_WINDOW_DAYS):
            raise ValueError(
                f"window {start_utc.isoformat()}..{end_utc.isoformat()} exceeds "
                f"the {wrh.MAX_REQUEST_WINDOW_DAYS}-day request cap"
            )
        seen.append((start_utc.date(), end_utc.date()))
        return real_rows

    monkeypatch.setattr(wrh, "fetch_wrh_timeseries", validating_fetch)
    monkeypatch.setattr(module, "fetch_wrh_timeseries", validating_fetch)
    monkeypatch.setattr(module, "fetch_wrh_token", lambda: "token")

    db_path, world_path = _temp_forecasts_pair(tmp_path)
    conn = _attached(db_path, world_path)
    summary = module.backfill(
        conn,
        start=date(2026, 8, 23),
        end=date(2026, 9, 11),
        city_filter=["Houston"],
        apply_writes=False,
        fixture_dir=None,
    )
    assert summary["refused_at"] is None
    assert summary["days_seen"] == 20
    assert not [line for line in summary["lines"] if "FETCH_FAILED" in line]
    assert seen, "no window was requested"
    conn.close()


def test_window_over_the_cap_is_a_wrh_error_not_a_bare_value_error():
    """A chunking mistake must degrade to one reported window, not kill the run."""
    module = _load_backfill_module()
    from src.data.noaa_wrh_timeseries import WrhError

    with pytest.raises(WrhError):
        module._fetch_window(
            "KLGA", (date(2026, 8, 1), date(2026, 8, 20)),
            unit="F", token="token", fixture_dir=None,
        )


# ---------------------------------------------------------------------------
# Hole-scanner registry
# ---------------------------------------------------------------------------


def test_hole_scanner_expects_a_page_feed_row_for_every_noaa_city():
    """A missed page-feed day must become a visible hole.

    Without the page lane in the registry the scanner never emits a MISSING row
    for it, so the day keeps only its Ogimet row and settles on the
    reconstruction this product replaces — silently, with nothing for an
    operator to see.
    """
    from src.data.hole_scanner import SOURCES_BY_TABLE, _source_applies_to_city
    from src.state.data_coverage import DataTable

    sources = SOURCES_BY_TABLE[DataTable.OBSERVATIONS]
    page_sources = {s for s in sources if s.startswith("noaa_wrh_")}
    noaa_cities = [
        c for c in cities_by_name.values() if c.settlement_source_type == "noaa"
    ]
    assert len(page_sources) == len(noaa_cities)
    assert page_sources == {
        f"noaa_wrh_{c.wu_station.strip().lower()}" for c in noaa_cities
    }

    # A NOAA city expects BOTH of its lanes for a post-migration date, and no
    # other city's station.
    nyc = cities_by_name["NYC"]
    post = date(2026, 9, 11)
    assert _source_applies_to_city("noaa_wrh_klga", nyc, post)
    assert _source_applies_to_city("ogimet_metar_klga", nyc, post)
    assert not _source_applies_to_city("noaa_wrh_khou", nyc, post)
    # A WU city never expects a page-feed row.
    assert not _source_applies_to_city("noaa_wrh_klga", cities_by_name["Jinan"], post)
    # Nor does a NOAA city for a date before it migrated to NOAA.
    assert not _source_applies_to_city("noaa_wrh_klga", nyc, date(2026, 8, 1))


def test_page_feed_sources_carry_a_retro_start_so_history_is_not_a_hole():
    """The product starts at the provider migration, not at the global floor."""
    from src.data.hole_scanner import ExceptionsConfig

    config = ExceptionsConfig.load()
    retro = {
        source: start
        for source, start in config.model_retro_starts.items()
        if source.startswith("noaa_wrh_")
    }
    noaa_cities = [
        c for c in cities_by_name.values() if c.settlement_source_type == "noaa"
    ]
    assert len(retro) == len(noaa_cities)
    assert set(retro.values()) == {date(2026, 8, 23)}


# ---------------------------------------------------------------------------
# Token rotation
# ---------------------------------------------------------------------------


def test_a_refused_request_retries_once_with_a_freshly_read_token(monkeypatch):
    """A mid-run upstream rotation must not refuse a station until restart.

    The token is cached for the life of the daemon and a 403 cannot distinguish
    "quota" from "the token rotated under us", so the only way to tell is to
    re-read apiKey.js once and see whether the token changed.
    """
    from src.data import daily_obs_append as appender
    from src.data.noaa_wrh_timeseries import WrhTokenRefused

    calls: list[str] = []

    def fake_fetch(station, **kwargs):
        calls.append(kwargs["token"])
        if kwargs["token"] == "stale":
            raise WrhTokenRefused("Invalid request per token rules")
        return ["row"]

    monkeypatch.setattr(
        "src.data.noaa_wrh_timeseries.fetch_wrh_timeseries", fake_fetch,
    )
    monkeypatch.setattr(
        "src.data.noaa_wrh_timeseries.fetch_wrh_token", lambda refresh=False: "fresh",
    )

    rows = appender._fetch_wrh_rows_with_token_refresh(
        "KLGA", unit="F", token="stale", recent_minutes=120,
    )
    assert rows == ["row"]
    assert calls == ["stale", "fresh"]


def test_a_quota_refusal_still_raises_when_the_token_did_not_change(monkeypatch):
    """A genuine quota refusal must stop the run, not burn a second request."""
    from src.data import daily_obs_append as appender
    from src.data.noaa_wrh_timeseries import WrhTokenRefused

    calls: list[str] = []

    def always_refused(station, **kwargs):
        calls.append(kwargs["token"])
        raise WrhTokenRefused("Invalid request per token rules")

    monkeypatch.setattr(
        "src.data.noaa_wrh_timeseries.fetch_wrh_timeseries", always_refused,
    )
    monkeypatch.setattr(
        "src.data.noaa_wrh_timeseries.fetch_wrh_token", lambda refresh=False: "same",
    )

    with pytest.raises(WrhTokenRefused):
        appender._fetch_wrh_rows_with_token_refresh(
            "KLGA", unit="F", token="same", recent_minutes=120,
        )
    # Exactly one request: the re-read returned the same token, so retrying
    # would just spend another request against a live quota.
    assert calls == ["same"]


# ---------------------------------------------------------------------------
# Rebuild dedup
# ---------------------------------------------------------------------------


def _obs_row(city: str, target_date: str, source: str, high: float) -> dict:
    return {
        "city": city, "target_date": target_date, "source": source,
        "high_temp": high, "low_temp": high - 10.0, "unit": "F",
        "authority": "VERIFIED",
    }


def test_rebuild_dedup_never_lets_a_foreign_family_row_displace_a_valid_one():
    """A legacy wu_icao_history row must not evict a NOAA city's real row.

    Ranking by NOAA prefix alone gave a foreign source rank 0 — the same rank as
    the most-preferred one — so with a strict < tie-break an arbitrary row order
    decided the winner. On the live table that discarded the valid sibling for
    517 city/date pairs, and each of those days then failed family validation and
    rebuilt nothing where it previously rebuilt correctly.
    """
    import importlib

    rs = importlib.import_module("scripts.rebuild_settlements")

    # Foreign row first: the Ogimet row must still win.
    kept = rs._preferred_rows_per_city_date([
        _obs_row("Amsterdam", "2026-08-01", "wu_icao_history", 20.0),
        _obs_row("Amsterdam", "2026-08-01", "ogimet_metar_eham", 22.0),
    ])
    assert [r["source"] for r in kept] == ["ogimet_metar_eham"]

    # Foreign row first against the page row: the page row must win.
    kept = rs._preferred_rows_per_city_date([
        _obs_row("Atlanta", "2026-09-05", "wu_icao_history", 90.0),
        _obs_row("Atlanta", "2026-09-05", "noaa_wrh_katl", 97.0),
    ])
    assert [r["source"] for r in kept] == ["noaa_wrh_katl"]


@pytest.mark.parametrize(
    "order",
    [
        ("noaa_wrh_katl", "ogimet_metar_katl"),
        ("ogimet_metar_katl", "noaa_wrh_katl"),
    ],
)
def test_rebuild_dedup_prefers_the_page_row_in_either_row_order(order):
    import importlib

    rs = importlib.import_module("scripts.rebuild_settlements")
    kept = rs._preferred_rows_per_city_date(
        [_obs_row("Atlanta", "2026-09-05", source, 95.0) for source in order]
    )
    assert [r["source"] for r in kept] == ["noaa_wrh_katl"]


def test_rebuild_dedup_keeps_an_invalid_row_when_nothing_valid_exists():
    """The caller must still see and count the family mismatch it always did."""
    import importlib

    rs = importlib.import_module("scripts.rebuild_settlements")
    rows = [_obs_row("Atlanta", "2026-09-06", "wu_icao_history", 88.0)]
    kept = rs._preferred_rows_per_city_date(rows)
    assert [r["source"] for r in kept] == ["wu_icao_history"]
    with pytest.raises(rs.SettlementRebuildSkip):
        rs._validate_source_family(kept[0], cities_by_name["Atlanta"])


def test_rebuild_dedup_leaves_a_wu_city_alone():
    import importlib

    rs = importlib.import_module("scripts.rebuild_settlements")
    kept = rs._preferred_rows_per_city_date(
        [_obs_row("Jinan", "2026-09-05", "wu_icao_history", 30.0)]
    )
    assert [r["source"] for r in kept] == ["wu_icao_history"]


def test_backfill_is_registered_in_the_script_manifest():
    import yaml

    manifest = yaml.safe_load(
        (REPO_ROOT / "architecture" / "script_manifest.yaml").read_text()
    )
    entry = manifest["scripts"]["backfill_noaa_wrh.py"]
    assert entry["apply_flag"] == "--apply"
    assert entry["dry_run_default"] is True
    assert "observations" in entry["write_targets"]
