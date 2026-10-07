# Created: 2026-09-12
# Last reused/audited: 2026-10-03
# Lifecycle: created=2026-09-12; last_reviewed=2026-10-06; last_reused=2026-10-06
# Purpose: Pin the weather.gov/wrh/timeseries settlement product — page render law,
#   per-city view selection, settlement-source precedence, and the backfill report.
# Reuse: Read src/data/noaa_wrh_timeseries.py's measured facts and
#   docs/operations/current/noaa_settlement_page_truth/evidence.md first.
# Authority basis: docs/operations/current/noaa_settlement_page_truth/{PLAN.md,evidence.md}
#   docs/operations/current/finite_evidence_probability_symmetry/PLAN.md WRH metadata slice
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
    WrhStationIdentityInvalid,
    WrhWindowTooOld,
    daily_extreme,
    recent_minutes_for_local_day,
    request_url_without_token,
    rows_from_payload,
)

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "noaa_wrh"


def _native_station_body(*, unit="C", metadata=None):
    station = {
        "STID": "MPMG", "STATUS": "ACTIVE", "LATITUDE": "8.98330",
        "LONGITUDE": "-79.51670", "ELEVATION": "43.0", "ELEV_DEM": "42.7",
        "TIMEZONE": "America/Panama",
        "OBSERVATIONS": {
            "date_time": ["2026-10-02T12:00:00-0500", "2026-10-02T13:00:00-0500"],
            "air_temp_set_1": [28.5, 29.0],
            "metar_set_1": ["MPMG 021700Z 28/24", "MPMG 021800Z 29/24"],
        },
    }
    if metadata is not None:
        station.update(metadata)
    return json.dumps({
        "SUMMARY": {"RESPONSE_CODE": 1},
        "UNITS": {"air_temp": {"C": "Celsius", "F": "Fahrenheit"}[unit], "elevation": "ft"},
        "STATION": [station],
    }).encode()


@pytest.mark.parametrize("unit", ["C", "F"])
def test_native_station_reference_preserves_raw_metadata_without_physical_promotion(unit):
    import hashlib
    from src.data.noaa_wrh_timeseries import product_from_response

    body = _native_station_body(unit=unit)
    receipt = datetime(2026, 10, 3, 1, 36, 47, 568256, tzinfo=timezone.utc)
    product = product_from_response(body, "MPMG", unit=unit, fetched_at=receipt,
                                    source_response_sha256=hashlib.sha256(body).hexdigest())
    reference = product.station_reference.to_provenance()
    assert reference["raw_fields"] == {
        k: v for k, v in json.loads(body)["STATION"][0].items() if k != "OBSERVATIONS"
    }
    assert reference["raw_units"]["elevation"] == "ft"
    assert reference["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert reference["hash_kind"] == "HTTP_RESPONSE_BODY_BYTES"
    assert reference["native_body_sha256"] == reference["response_sha256"]
    assert reference["fetched_at_utc"] == receipt.isoformat()
    assert reference["evidence_role"] == "provider_reported_station_reference"
    assert reference["source_issued_at_utc"] is None
    assert reference["source_issued_at_status"] == "UNKNOWN"
    assert reference["vertical_datum"] == reference["height_role"] == "UNKNOWN"
    assert product.rows == rows_from_payload(json.loads(body), "MPMG")
    assert [r.air_temp for r in product.rows] == [28.5, 29.0]
    for metric, expected in (("high", 29.0), ("low", 28.5)):
        assert daily_extreme(product.rows, target_date_local="2026-10-02", view="all", metric=metric).value == expected


@pytest.mark.parametrize("metadata", [
    {"LATITUDE": None, "LONGITUDE": "bad", "ELEVATION": {"unexpected": "shape"}, "ELEV_DEM": False},
    {"LATITUDE": "0", "LONGITUDE": "0", "ELEVATION": "0", "ELEV_DEM": "0"},
])
def test_optional_station_metadata_is_raw_evidence_not_temperature_validation(metadata):
    from src.data.noaa_wrh_timeseries import product_from_response

    body = _native_station_body(metadata=metadata)
    product = product_from_response(body, "MPMG", unit="C")
    reference = product.station_reference.to_provenance()
    assert all(reference["raw_fields"][key] == value for key, value in metadata.items())
    assert reference["fetched_at_utc"] is None
    assert reference["fetched_at_basis"] == "UNKNOWN"
    assert reference["hash_kind"] == "PARSER_INPUT_BYTES"
    assert reference["native_body_sha256"] is None
    assert reference["height_role"] == reference["vertical_datum"] == "UNKNOWN"
    assert [row.air_temp for row in product.rows] == [28.5, 29.0]


def test_sparse_station_metadata_retains_unknowns_and_explicit_empty_semantics():
    from src.data.noaa_wrh_timeseries import product_from_response

    body = _empty_product_body()
    product = product_from_response(body, "KHOU", unit="F")
    reference = product.station_reference.to_provenance()
    assert reference["raw_fields"] == {"STID": "KHOU"}
    assert "elevation" not in reference["raw_units"]
    assert product.confirms_empty(target_date_local="2026-09-11", view="hourly")


def test_nonfinite_station_metadata_retains_unknown_diagnostic_without_changing_rows():
    from src.data.noaa_wrh_timeseries import product_from_response

    body = _native_station_body(metadata={"ELEVATION": "1e999"}).replace(b'"1e999"', b'1e999')
    product = product_from_response(body, "MPMG", unit="C")
    reference = product.station_reference.to_provenance()
    assert reference["raw_fields"]["ELEVATION"] == {
        "value_status": "UNKNOWN", "raw_type": "float", "raw_value_repr": "inf",
    }
    json.dumps(reference, allow_nan=False)
    assert [row.air_temp for row in product.rows] == [28.5, 29.0]


def test_wrh_metadata_receipt_is_http_completion_not_date_header_token_or_row_clock(monkeypatch):
    from src.data import noaa_wrh_timeseries as wrh

    receipt = datetime(2026, 10, 3, 1, 36, 47, 568256, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return receipt

    class Response:
        status_code = 200
        content = _native_station_body()
        headers = {"Date": "Thu, 01 Jan 1970 00:00:00 GMT"}

    monkeypatch.setattr(wrh, "datetime", Clock)
    monkeypatch.setattr(wrh.httpx, "get", lambda *a, **k: Response())
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: None)
    monkeypatch.setattr(wrh, "_token_fetched_at", datetime(2020, 1, 1, tzinfo=timezone.utc))
    product = wrh.fetch_wrh_product("MPMG", unit="C", token="fixture-token", recent_minutes=180)
    reference = product.station_reference.to_provenance()
    assert reference["fetched_at_utc"] == receipt.isoformat()
    assert reference["fetched_at_basis"] == "HTTP_RESPONSE_COMPLETION"
    assert reference["hash_kind"] == "HTTP_RESPONSE_BODY_BYTES"
    assert reference["source_issued_at_utc"] is None
    assert reference["fetched_at_utc"] != product.rows[-1].utc.isoformat()


def test_daily_high_low_metadata_roundtrip_preserves_temperature_identity_and_raw_metar(tmp_path, monkeypatch):
    from src.data import daily_obs_append as appender, noaa_wrh_timeseries as wrh
    from src.state.schema.observation_prints_schema import ensure_table

    body = _native_station_body()
    receipt = datetime(2026, 10, 3, 1, 36, 47, 568256, tzinfo=timezone.utc)
    product = wrh.product_from_response(body, "MPMG", unit="C", fetched_at=receipt)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture-token")
    monkeypatch.setattr(appender, "_fetch_wrh_product_with_token_refresh", lambda *a, **k: product)
    forecasts_path, world_path = _live_schema_db_pair(tmp_path)
    world_conn = sqlite3.connect(world_path)
    ensure_table(world_conn)
    world_conn.commit()
    world_conn.close()
    conn = _attached(forecasts_path, world_path)
    try:
        stats = appender.append_noaa_wrh_city("Panama City", [date(2026, 10, 2)], conn, now_utc=receipt)
        assert stats["inserted"] == 1 and stats["print_errors"] == 0
        row = conn.execute("SELECT * FROM observations WHERE city='Panama City'").fetchone()
        high = json.loads(row["high_provenance_metadata"])
        low = json.loads(row["low_provenance_metadata"])
        assert high["station_reference"] == low["station_reference"] == product.station_reference.to_provenance()
        original_hash = high["payload_hash"]
        assert (row["high_temp"], row["low_temp"], row["unit"]) == (29.0, 28.5, "C")
        assert [r[0] for r in conn.execute("SELECT raw_report FROM observation_prints ORDER BY publish_ts_utc")] == [r.raw_metar for r in product.rows]
        changed = wrh.product_from_response(_native_station_body(metadata={"ELEVATION": "999"}), "MPMG", unit="C", fetched_at=receipt + timedelta(seconds=1))
        monkeypatch.setattr(appender, "_fetch_wrh_product_with_token_refresh", lambda *a, **k: changed)
        appender.append_noaa_wrh_city("Panama City", [date(2026, 10, 2)], conn, now_utc=receipt)
        assert conn.execute("SELECT COUNT(*) FROM daily_observation_revisions").fetchone()[0] == 0
        unchanged = json.loads(conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0])
        assert unchanged["payload_hash"] == original_hash
        assert unchanged["station_reference"] == high["station_reference"]
    finally:
        conn.close()


def test_daily_station_metadata_write_rolls_back_with_coverage_failure(tmp_path, monkeypatch):
    from src.data import daily_obs_append as appender, noaa_wrh_timeseries as wrh

    receipt = datetime(2026, 10, 3, 1, 36, 47, tzinfo=timezone.utc)
    product = wrh.product_from_response(_native_station_body(), "MPMG", unit="C", fetched_at=receipt)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture-token")
    monkeypatch.setattr(appender, "_fetch_wrh_product_with_token_refresh", lambda *a, **k: product)

    def fail_coverage(*a, **k):
        raise RuntimeError("private fixture coverage failure")

    monkeypatch.setattr(appender, "record_written", fail_coverage)
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        stats = appender.append_noaa_wrh_city("Panama City", [date(2026, 10, 2)], conn, now_utc=receipt)
        assert stats["inserted"] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM daily_observation_revisions").fetchone()[0] == 0
    finally:
        conn.close()

#: The exact set of cities whose market descriptions carry the "Show Hourly
#: Data" clause, from the 2026-09-12 gamma-api census of 270 active events.
HOURLY_VIEW_CITIES = {
    "Atlanta", "Austin", "Chicago", "Dallas", "Denver", "Houston",
    "Los Angeles", "Miami", "NYC", "San Francisco", "Seattle",
}


def _rows(station: str):
    payload = json.loads((FIXTURE_DIR / f"syn_{station}.json").read_text())
    return rows_from_payload(payload, station)


@pytest.mark.parametrize("response_station", (None, "", "OTHER", ["KHOU"]))
def test_page_response_requires_the_requested_station_identity(response_station):
    payload = json.loads((FIXTURE_DIR / "syn_KHOU.json").read_text())
    if response_station is None:
        del payload["STATION"][0]["STID"]
    else:
        payload["STATION"][0]["STID"] = response_station
    with pytest.raises(WrhStationIdentityInvalid):
        rows_from_payload(payload, "KHOU")


@pytest.mark.parametrize("stations", ([], [{"STID": "KHOU"}, {"STID": "KHOU"}], [None]))
def test_page_response_rejects_missing_or_ambiguous_station_objects(stations):
    payload = json.loads((FIXTURE_DIR / "syn_KHOU.json").read_text())
    payload["STATION"] = stations
    with pytest.raises(WrhStationIdentityInvalid):
        rows_from_payload(payload, "KHOU")


def test_page_response_normalizes_station_case_without_changing_rows():
    payload = json.loads((FIXTURE_DIR / "syn_KHOU.json").read_text())
    payload["STATION"][0]["STID"] = " khou "
    assert rows_from_payload(payload, " KHOU ") == _rows("KHOU")


def test_one_valid_station_with_no_observations_remains_a_dark_day():
    payload = {"STATION": [{"STID": "KHOU", "OBSERVATIONS": {"date_time": []}}]}
    assert rows_from_payload(payload, "KHOU") == []


@pytest.mark.parametrize("observations", [{}, None, "missing"])
def test_station_without_an_observation_clock_array_is_incomplete(observations):
    """Only the documented explicit-empty shape (date_time []) means no rows."""
    from src.data.noaa_wrh_timeseries import WrhPayloadInvalid

    station = {"STID": "KHOU"}
    if observations != "missing":
        station["OBSERVATIONS"] = observations
    with pytest.raises(WrhPayloadInvalid):
        rows_from_payload({"STATION": [station]}, "KHOU")


def test_wrong_station_http_response_cannot_write_atoms_prints_or_success_coverage(
    tmp_path, monkeypatch,
):
    from src.data import daily_obs_append as appender
    from src.data import noaa_wrh_timeseries as wrh

    payload = json.loads((FIXTURE_DIR / "syn_KHOU.json").read_text())
    payload["STATION"][0]["STID"] = "OTHER"

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()

    monkeypatch.setattr(wrh.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: None)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture-token")

    def forbidden(*args, **kwargs):
        pytest.fail("wrong-station response reached atom or print writer")

    monkeypatch.setattr(appender, "_build_atom_pair", forbidden)
    monkeypatch.setattr(appender, "_append_noaa_wrh_prints", forbidden)
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        stats = appender.append_noaa_wrh_city(
            "Houston", [date(2026, 9, 11)], conn,
            now_utc=datetime(2026, 9, 12, 15, tzinfo=timezone.utc),
        )
        assert (stats["inserted"], stats["prints_written"], stats["fetch_errors"]) == (0, 0, 1)
        assert conn.execute(
            "SELECT COUNT(*) FROM observations WHERE source = 'noaa_wrh_khou'"
        ).fetchone()[0] == 0
        assert conn.execute(
            """SELECT status FROM world.data_coverage
               WHERE city = 'Houston' AND data_source = 'noaa_wrh_khou'
                 AND target_date = '2026-09-11'"""
        ).fetchone()[0] == "FAILED"
    finally:
        conn.close()


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
    fetched_at: str = "2026-09-12T00:00:00+00:00",
) -> None:
    conn.execute(
        """INSERT INTO observations
           (city, target_date, source, high_temp, low_temp, unit, station_id,
            authority, fetched_at, high_local_time, low_local_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?)""",
        (
            city, target_date, source, high, low, unit, station_id,
            fetched_at,
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
def test_settled_at_replay_seattle_09_13_lands_before_09z_not_18z(module_path, tmp_path):
    """Seattle 2026-09-12 replay (T-truthlag): the OLD alphabetical daily
    shard (`daily_obs_append._ogimet_city_shard_for_hour`, now replaced by
    `_noaa_daily_target_dates_due`) fetched Seattle's WRH page at its shard
    hour 18, so ``settled_at`` (= ``fetched_at``, this module's own
    ``settled_at = obs_row.get("fetched_at")``) landed 18:05:05Z -- 11h05m
    after Seattle's local day end (07:00Z). The new local-day-end anchor
    selects Seattle at the first daily_tick (cron minute=5) at/after
    day_end+1h = 08:00Z, i.e. 08:05Z. This test proves the settlement-truth
    half of that fix: an ``observations`` row stamped at the NEW fetch time
    (08:05Z) reads back through the unmodified ``_lookup_settlement_obs``
    exactly as ``settled_at`` -- landing under 09:00Z, not at the old
    18:05Z.
    """
    import importlib

    module = importlib.import_module(module_path)
    conn = _observations_conn(tmp_path)
    new_fetch_at = "2026-09-13T08:05:11+00:00"
    _insert_observation(
        conn, city="Seattle", target_date="2026-09-12", source="noaa_wrh_ksea",
        station_id="KSEA", high=55.0, low=48.0, fetched_at=new_fetch_at,
    )

    obs = module._lookup_settlement_obs(
        conn, cities_by_name["Seattle"], "2026-09-12", temperature_metric="high",
    )

    assert obs is not None
    assert obs["fetched_at"] == new_fetch_at
    settled_at = datetime.fromisoformat(obs["fetched_at"])
    assert settled_at < datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
    # The old shard's measured value (T-truthlag) would have failed this bound.
    old_shard_settled_at = datetime(2026, 9, 13, 18, 5, 5, tzinfo=timezone.utc)
    assert old_shard_settled_at >= datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
    conn.close()


@pytest.mark.parametrize(
    "module_path",
    ["src.execution.harvester", "src.ingest.harvester_truth_writer"],
)
def test_ogimet_row_cannot_replace_the_contract_named_fallback(module_path, tmp_path):
    """A missing local page row is not authority to settle from Ogimet.

    The observed market contracts name WU after an explicit ET deadline;
    tests/test_settlement_fallback_hierarchy.py protects that positive route.
    """
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
    assert obs is None
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
    # The captured live DDL predates the absence-proof column; apply the
    # shipped migration exactly as deploy does.
    conn = sqlite3.connect(paths["world"])
    _evidence_migration().up(conn)
    conn.close()
    return paths["forecasts"], paths["world"]


def _evidence_migration():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts/migrations/202610_data_coverage_evidence_json.py"
    spec = importlib.util.spec_from_file_location("migration_202610_evidence_json", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        "src.data.noaa_wrh_timeseries.fetch_wrh_product", fake_fetch,
    )
    monkeypatch.setattr(
        "src.data.noaa_wrh_timeseries.fetch_wrh_token", lambda refresh=False: "fresh",
    )

    rows = appender._fetch_wrh_product_with_token_refresh(
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
        "src.data.noaa_wrh_timeseries.fetch_wrh_product", always_refused,
    )
    monkeypatch.setattr(
        "src.data.noaa_wrh_timeseries.fetch_wrh_token", lambda refresh=False: "same",
    )

    with pytest.raises(WrhTokenRefused):
        appender._fetch_wrh_product_with_token_refresh(
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


# ---------------------------------------------------------------------------
# Malformed product is uncertainty, never resolver absence
# ---------------------------------------------------------------------------


def _khou_payload():
    return json.loads((FIXTURE_DIR / "syn_KHOU.json").read_text())


@pytest.mark.parametrize("corrupt", [
    "all_invalid_timestamps", "one_invalid_timestamp", "short_temperature_array",
    "string_temperature", "nonfinite_temperature", "observations_not_object",
    "rows_without_temperature",
])
def test_malformed_or_partial_arrays_raise_instead_of_dropping_rows(corrupt):
    from src.data.noaa_wrh_timeseries import WrhPayloadInvalid

    payload = _khou_payload()
    obs = payload["STATION"][0]["OBSERVATIONS"]
    if corrupt == "all_invalid_timestamps":
        obs["date_time"] = ["invalid"] * len(obs["date_time"])
    elif corrupt == "one_invalid_timestamp":
        obs["date_time"][5] = "2026-09-11 05:00"
    elif corrupt == "short_temperature_array":
        obs["air_temp_set_1"] = obs["air_temp_set_1"][:-1]
    elif corrupt == "string_temperature":
        obs["air_temp_set_1"][3] = "82.4"
    elif corrupt == "nonfinite_temperature":
        obs["air_temp_set_1"][3] = float("nan")
    elif corrupt == "observations_not_object":
        payload["STATION"][0]["OBSERVATIONS"] = ["x"]
    else:
        del obs["air_temp_set_1"]
    with pytest.raises(WrhPayloadInvalid):
        rows_from_payload(payload, "KHOU")


def _empty_product_body(*, response_code=1, unit_label="Fahrenheit", rows=None):
    obs = {"date_time": [], "air_temp_set_1": []}
    if rows:
        obs = {"date_time": [r[0] for r in rows], "air_temp_set_1": [r[1] for r in rows]}
    return json.dumps({
        "SUMMARY": {"RESPONSE_CODE": response_code, "RESPONSE_MESSAGE": "OK"},
        "UNITS": {"air_temp": unit_label},
        "STATION": [{"STID": "KHOU", "OBSERVATIONS": obs}],
    }).encode()


def _run_houston_after_deadline(tmp_path, monkeypatch, body):
    from src.data import daily_obs_append as appender
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.settlement_observation_selection import (
        fallback_deadline, noaa_absence_witness, observation_selection,
    )

    class Response:
        status_code = 200
        content = body

    monkeypatch.setattr(wrh.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: None)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture-token")
    monkeypatch.setattr(appender, "_build_atom_pair",
                        lambda *a, **k: pytest.fail("no extreme may be written"))
    day = date(2026, 9, 11)
    conn = _attached(*_live_schema_db_pair(tmp_path))
    stats = appender.append_noaa_wrh_city(
        "Houston", [day], conn, now_utc=day_start_plus(day, hours=30),
    )
    # Coverage rows are stamped with the real wall clock; read them as of now.
    after = datetime.now(timezone.utc)
    assert after > fallback_deadline(day)
    reasons = [r[0] for r in conn.execute(
        "SELECT reason FROM world.data_coverage WHERE city='Houston' "
        "AND data_source='noaa_wrh_khou' AND target_date='2026-09-11'")]
    witness = noaa_absence_witness(conn, cities_by_name["Houston"], "2026-09-11", as_of=after)
    selection = observation_selection(conn, cities_by_name["Houston"], "2026-09-11",
                                      "wu_icao_history", as_of=after)
    conn.close()
    return stats, reasons, witness, selection


def day_start_plus(day, *, hours):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=hours)


@pytest.mark.parametrize("body", [
    _empty_product_body(rows=[("invalid", 70.0)]),
    b"not json",
    _empty_product_body(response_code=2),
    _empty_product_body(unit_label="Celsius"),
    _empty_product_body(response_code=True),
    _empty_product_body(unit_label={"unexpected": "object"}),
    # A present UNITS that is not an object is malformed, not "unlabelled".
    *(json.dumps({**json.loads(_empty_product_body()), "UNITS": bad}).encode()
      for bad in ([], ["Fahrenheit"], "Fahrenheit", 1, True, None)),
    json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Fahrenheit"},
                "STATION": [{"STID": "KHOU"}]}).encode(),
    json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Fahrenheit"},
                "STATION": [{"STID": "KHOU", "OBSERVATIONS": None}]}).encode(),
    json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Fahrenheit"},
                "STATION": [{"STID": "KHOU", "OBSERVATIONS": {}}]}).encode(),
])
def test_invalid_product_after_deadline_writes_no_absence_and_admits_no_fallback(
    tmp_path, monkeypatch, body,
):
    from src.data import settlement_observation_selection as selection
    from src.data.settlement_observation_selection import fallback_deadline

    monkeypatch.setattr(selection, "datetime", _FrozenAfterDeadline)
    stats, reasons, witness, chosen = _run_houston_after_deadline(tmp_path, monkeypatch, body)
    assert stats["inserted"] == 0
    assert "SOURCE_CONFIRMED_EMPTY_AFTER_CONTRACT_DEADLINE" not in reasons
    assert witness is None and chosen is None


def test_documented_sparse_empty_shape_still_confirms_empty():
    """showemptyvars=0 omits empty variable keys; date_time [] stays valid."""
    from src.data.noaa_wrh_timeseries import product_from_response

    body = json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {},
                       "STATION": [{"STID": "KHOU", "OBSERVATIONS": {"date_time": []}}]})
    product = product_from_response(body.encode(), "KHOU", unit="F")
    assert product.confirms_empty(target_date_local="2026-09-11", view="hourly")
    absent = json.dumps({"SUMMARY": {"RESPONSE_CODE": 1},
                         "STATION": [{"STID": "KHOU", "OBSERVATIONS": {"date_time": []}}]})
    product = product_from_response(absent.encode(), "KHOU", unit="F")
    assert product.confirms_empty(target_date_local="2026-09-11", view="hourly")


def test_valid_explicit_empty_product_after_deadline_mints_the_witness(tmp_path, monkeypatch):
    from src.data import settlement_observation_selection as selection

    monkeypatch.setattr(selection, "datetime", _FrozenAfterDeadline)
    stats, reasons, witness, chosen = _run_houston_after_deadline(
        tmp_path, monkeypatch, _empty_product_body(),
    )
    assert reasons == ["SOURCE_CONFIRMED_EMPTY_AFTER_CONTRACT_DEADLINE"]
    assert witness is not None and witness["station_id"] == "KHOU"
    assert chosen is not None and chosen[1]["selected"] == "FALLBACK_WU"


class _FrozenAfterDeadline(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 13, 4, 10, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# A WRH outage that outlives the recent= window still recovers to the primary
# ---------------------------------------------------------------------------


def _stub_wrh_http(monkeypatch, responses):
    """Serve ``responses`` in order; a BaseException instance is raised."""
    from src.data import noaa_wrh_timeseries as wrh

    calls = []

    class Response:
        status_code = 200

        def __init__(self, body):
            self.content = body

    def get(url, *, params, **kwargs):
        calls.append(dict(params))
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return Response(item)

    monkeypatch.setattr(wrh.httpx, "get", get)
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: None)
    monkeypatch.setattr(wrh.time, "sleep", lambda *_: None)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda **_: "fixture-token")
    return calls


def _wrh_coverage(conn, target):
    return conn.execute(
        "SELECT status, reason FROM world.data_coverage WHERE city='Houston' "
        "AND data_source='noaa_wrh_khou' AND target_date=?", (target,),
    ).fetchone()


def test_outage_past_recent_window_fetches_the_explicit_day_window(tmp_path, monkeypatch):
    from src.data import daily_obs_append as appender

    import httpx

    body = (FIXTURE_DIR / "syn_KHOU.json").read_bytes()
    # Ten days after the target: no recent= window can hold the whole day.
    now = datetime(2026, 9, 20, 15, tzinfo=timezone.utc)
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        calls = _stub_wrh_http(monkeypatch, [httpx.ConnectError("outage")] * 3)
        stats = appender.append_noaa_wrh_city("Houston", [date(2026, 9, 10)], conn, now_utc=now)
        # Transport failure stays retryable uncertainty, never a permanent gap.
        assert stats["fetch_errors"] == 1 and stats["explicit_window"] == 1
        assert tuple(_wrh_coverage(conn, "2026-09-10")) == ("FAILED", "NETWORK_ERROR")
        assert all("recent" not in c and c["start"] == "202609090000"
                   and c["end"] == "202609112359" for c in calls)

        calls = _stub_wrh_http(monkeypatch, [body])
        stats = appender.append_noaa_wrh_city("Houston", [date(2026, 9, 10)], conn, now_utc=now)
        assert stats["inserted"] == 1 and len(calls) == 1
        assert tuple(_wrh_coverage(conn, "2026-09-10")) == ("WRITTEN", None)
        assert conn.execute(
            "SELECT COUNT(*) FROM observations WHERE city='Houston' "
            "AND target_date='2026-09-10' AND source='noaa_wrh_khou'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_restart_catch_up_recovers_an_aged_failed_wrh_day(tmp_path, monkeypatch):
    """A FAILED day older than the recent= horizon is retried by the scheduled
    hole-scanner/startup catch-up, not left for an operator backfill."""
    from src.data import daily_obs_append as appender
    from src.state.data_coverage import CoverageReason, DataTable, record_failed

    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        record_failed(
            conn, data_table=DataTable.OBSERVATIONS, city="Houston",
            data_source="noaa_wrh_khou", target_date=date(2026, 9, 10),
            reason=CoverageReason.NETWORK_ERROR,
            retry_after=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )
        conn.commit()
        calls = _stub_wrh_http(monkeypatch, [(FIXTURE_DIR / "syn_KHOU.json").read_bytes()])
        totals = appender.catch_up_missing(conn, days_back=100_000)
        assert totals["noaa_wrh_inserted"] == 1 and "recent" not in calls[0]
        assert tuple(_wrh_coverage(conn, "2026-09-10")) == ("WRITTEN", None)
    finally:
        conn.close()


def test_aged_valid_empty_product_mints_absence_only_after_the_deadline(tmp_path, monkeypatch):
    from src.data import daily_obs_append as appender

    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        _stub_wrh_http(monkeypatch, [_empty_product_body()])
        stats = appender.append_noaa_wrh_city(
            "Houston", [date(2026, 9, 10)], conn,
            now_utc=datetime(2026, 9, 20, 15, tzinfo=timezone.utc),
        )
        assert stats["no_rows"] == 1 and stats["inserted"] == 0
        # Wall clock is past the 2026-09-11 23:59 ET contract deadline.
        assert tuple(_wrh_coverage(conn, "2026-09-10")) == (
            "FAILED", "SOURCE_CONFIRMED_EMPTY_AFTER_CONTRACT_DEADLINE")
    finally:
        conn.close()


def test_pre_fix_outside_window_gap_reopens_for_wrh_only(tmp_path, monkeypatch):
    """Only the obsolete WRH OUTSIDE_LANE_REQUEST_WINDOW gap is reopened."""
    from src.data import daily_obs_append as appender
    from src.state.data_coverage import CoverageReason, DataTable, record_legitimate_gap

    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        for source, day, reason in (
            ("noaa_wrh_khou", "2026-09-11", CoverageReason.OUTSIDE_LANE_REQUEST_WINDOW),
            ("ogimet_metar_khou", "2026-09-11", CoverageReason.OUTSIDE_LANE_REQUEST_WINDOW),
            ("noaa_wrh_khou", "2026-09-10", CoverageReason.GUARD_REJECTED),
        ):
            record_legitimate_gap(
                conn, data_table=DataTable.OBSERVATIONS, city="Houston",
                data_source=source, target_date=day, reason=reason,
            )
        conn.commit()
        calls = []

        def collect(city, dates, *args, **kwargs):
            calls.append((city, list(dates)))
            return {"inserted": 0, "guard_rejected": 0}

        monkeypatch.setattr(appender, "append_noaa_wrh_city", collect)
        monkeypatch.setattr(appender, "append_ogimet_city",
                            lambda *a, **k: pytest.fail("non-WRH gap reopened"))
        appender.catch_up_missing(conn, days_back=3650)
        appender.catch_up_missing(conn, days_back=3650)  # reopen is idempotent
        assert calls == [("Houston", [date(2026, 9, 11)])] * 2
        rows = {
            (r[0], r[1]): r[2] for r in conn.execute(
                "SELECT data_source, target_date, status FROM world.data_coverage "
                "WHERE city='Houston'")
        }
        assert rows == {
            ("noaa_wrh_khou", "2026-09-11"): "MISSING",
            ("ogimet_metar_khou", "2026-09-11"): "LEGITIMATE_GAP",
            ("noaa_wrh_khou", "2026-09-10"): "LEGITIMATE_GAP",
        }
    finally:
        conn.close()


def _current_product(*, values=(32.0, 28.0), receipt="2026-10-06T02:00:00+00:00", view="all"):
    import hashlib
    from dataclasses import replace
    from src.data import noaa_wrh_timeseries as wrh
    body = json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Celsius"},
        "STATION": [{"STID": "WSSS", "OBSERVATIONS": {
            "date_time": [f"2026-10-06T{hour:02}:00:00+0800" for hour in range(8, 8 + len(values))],
            "air_temp_set_1": list(values), "sea_level_pressure_set_1": [1010] * len(values),
        }}]}).encode()
    received = datetime.fromisoformat(receipt)
    product = wrh.product_from_response(body, "WSSS", unit="C", fetched_at=received,
                                       source_response_sha256=hashlib.sha256(body).hexdigest())
    return replace(product, request_started_at=received - timedelta(seconds=1),
                   coverage_start_utc=datetime(2026, 10, 5, 15, tzinfo=timezone.utc),
                   coverage_end_utc=received - timedelta(seconds=1))


def test_current_wrh_snapshot_updates_retracts_and_replays_without_forecast(tmp_path):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.state.schema.observation_prints_schema import ensure_table
    from types import SimpleNamespace
    city = cities_by_name["Singapore"]
    forecasts, world = _live_schema_db_pair(tmp_path)
    with sqlite3.connect(world) as wc:
        ensure_table(wc)
    conn = _attached(forecasts, world)
    moment = datetime(2026, 10, 6, 2, tzinfo=timezone.utc)
    position = SimpleNamespace(target_date="2026-10-06", direction="buy_yes", temperature_metric="high",
                               bin_label="30°C", trade_id="test-current")
    try:
        for values, minute, expected in (((32.0, 28.0), 0, 32.0), ((29.0, 28.0), 1, 29.0),
                                         ((32.0, 28.0), 2, 32.0), ((29.0, 28.0), 3, 29.0),
                                         ((), 4, None)):
            now = moment + timedelta(minutes=minute)
            product = _current_product(values=values, receipt=now.isoformat())
            status = append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
            conn.commit()
            assert status == ("inserted" if minute == 0 else "revision")
            owned, snapshot = read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=now)
            assert owned is True and snapshot is not None
            extreme = snapshot.extreme("high")
            assert (extreme.value if extreme else None) == expected
            if expected is None:
                from src.contracts.exceptions import ObservationUnavailableError
                with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
                    _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-06",
                        temperature_metric="high", decision_time=now, require_settlement_channel=True)
            else:
                fact = _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-06",
                    temperature_metric="high", decision_time=now, require_settlement_channel=True)
                assert fact["observed_extreme_native"] == expected
            verdict = evaluate_hard_fact_exit(position=position, city=city, now=now, world_conn=conn, durable_only=True)
            assert (verdict.action if verdict else None) == ("EXIT_DEAD_BIN" if expected == 32.0 else None)
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 4
        for minute, expected in ((0, 32.0), (1, 29.0), (2, 32.0), (3, 29.0), (4, None)):
            owned, snapshot = read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06",
                                                           as_of=moment + timedelta(minutes=minute))
            assert owned is True and snapshot is not None
            value = snapshot.extreme("high")
            assert (value.value if value else None) == expected
    finally:
        conn.close()


@pytest.mark.parametrize("mutation", ["station", "unit", "view", "partial", "future", "body_hash", "membership", "official_flag"])
def test_current_wrh_snapshot_rejects_unbound_product_before_write(tmp_path, mutation):
    from dataclasses import replace
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data import noaa_wrh_timeseries as wrh
    city = cities_by_name["Singapore"]
    product = _current_product()
    now = product.station_reference.fetched_at
    if mutation == "station": product = replace(product, station="ZBAA")
    elif mutation == "unit": product = replace(product, unit="F")
    elif mutation == "partial": product = replace(product, coverage_start_utc=datetime(2026, 10, 6, 0, tzinfo=timezone.utc))
    elif mutation == "future": now -= timedelta(seconds=1)
    elif mutation == "body_hash": product = replace(product, response_sha256="b" * 64)
    elif mutation == "membership": product = replace(product, rows=[replace(product.rows[0], air_temp=99.0), *product.rows[1:]])
    elif mutation == "official_flag": product = replace(product, rows=[replace(product.rows[0], is_official_report=False), *product.rows[1:]])
    else:
        proof = wrh.current_snapshot_from_product(product, city=city, target_date="2026-10-06", as_of=now).provenance()
        proof["view"] = "hourly"
        with pytest.raises(ValueError): wrh.replay_current_snapshot(proof, city=city, target_date="2026-10-06", as_of=now, _native_body=product.native_body)
        return
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        with pytest.raises((ValueError, wrh.WrhError)):
            append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        conn.close()


def test_current_wrh_repoll_metadata_change_and_old_receipt_do_not_renew_authority(tmp_path):
    import hashlib
    from dataclasses import replace
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data import noaa_wrh_timeseries as wrh
    from src.config import state_path
    city = cities_by_name["Singapore"]
    first = _current_product()
    now = first.station_reference.fetched_at
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first, as_of=now) == "inserted"
        payload = json.loads(first.native_body)
        payload["SUMMARY"]["response_duration"] = 0.25
        body = json.dumps(payload).encode()
        newer = replace(wrh.product_from_response(body, "WSSS", unit="C", fetched_at=now + timedelta(minutes=1),
                                                  source_response_sha256=hashlib.sha256(body).hexdigest()),
                        request_started_at=now + timedelta(seconds=59), coverage_start_utc=first.coverage_start_utc,
                        coverage_end_utc=now + timedelta(seconds=59))
        assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=newer, as_of=now + timedelta(minutes=1)) == "noop"
        assert conn.execute("SELECT fetched_at FROM observations").fetchone()[0] == now.isoformat()
        assert not (state_path("noaa_wrh_response_bodies") / (newer.response_sha256 + ".zlib")).exists()
        receipt = json.loads(conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0])["wrh_latest_confirmation"]
        assert receipt["retained_semantic_body_sha256"] == first.response_sha256
        assert receipt["validated_response_sha256"] == newer.response_sha256
        assert "rows" not in receipt and "native_body_ref" not in receipt
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 0
        late = _current_product(values=(31.0, 28.0), receipt=(now - timedelta(seconds=1)).isoformat())
        assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=late, as_of=now + timedelta(minutes=1)) == "older_or_ambiguous_receipt"
        assert conn.execute("SELECT high_temp FROM observations").fetchone()[0] == 32.0
    finally:
        conn.close()


def test_current_wrh_source_body_corruption_and_missing_body_do_not_resurrect_ledger(tmp_path):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.config import state_path
    product = _current_product(values=(33.2, 27.1))
    city = cities_by_name["Singapore"]
    now = product.station_reference.fetched_at
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
        path = state_path("noaa_wrh_response_bodies") / (product.response_sha256 + ".zlib")
        original = path.read_bytes()
        try:
            path.write_bytes(b"corrupt fixture")
            assert read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=now) == (True, None)
            path.unlink()
            assert read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=now) == (True, None)
        finally:
            path.write_bytes(original)
    finally:
        conn.close()


def test_current_wrh_empty_and_deleted_latest_cannot_resurrect_point_or_extreme(tmp_path):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    city = cities_by_name["Singapore"]
    forecasts, world = _live_schema_db_pair(tmp_path)
    with sqlite3.connect(world) as wc:
        ensure_table(wc)
        append_print(wc, city=city.name, station_id="WSSS", source_channel="noaa_wrh_wsss",
                     publish_ts_utc="2026-10-06T01:00:00+00:00", value_native=35.0, unit="C",
                     fetched_at_utc="2026-10-06T01:01:00+00:00", raw_report="old page projection")
    conn = _attached(forecasts, world)
    try:
        for minute, values, expected in ((0, (29.0,), 29.0), (1, (), None)):
            now = datetime(2026, 10, 6, 2, minute, tzinfo=timezone.utc)
            append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06",
                                           product=_current_product(values=values, receipt=now.isoformat()), as_of=now)
            conn.commit()
            if expected is None:
                from src.contracts.exceptions import ObservationUnavailableError
                with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
                    read_day0_current_temperature_state(conn=conn, city=city, target_date="2026-10-06", decision_time=now)
                with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
                    _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-06",
                        temperature_metric="high", decision_time=now, require_settlement_channel=True)
            else:
                point = read_day0_current_temperature_state(conn=conn, city=city, target_date="2026-10-06", decision_time=now)
                fact = _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-06",
                    temperature_metric="high", decision_time=now, require_settlement_channel=True)
                assert point.value_native == fact["observed_extreme_native"] == expected
    finally:
        conn.close()


def test_historical_noaa_append_caller_still_quarantines_changed_source_body(tmp_path, monkeypatch):
    from src.data import daily_obs_append as appender, noaa_wrh_timeseries as wrh
    # Historical append gets a valid actual body; explicit backfill run remains
    # the original disputed-write route, even after a newer source receipt.
    first = _current_product(receipt="2026-10-07T02:00:00+00:00")
    second = _current_product(values=(29.0, 28.0), receipt="2026-10-07T03:00:00+00:00")
    chosen = [first]
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture-token")
    monkeypatch.setattr(appender, "_fetch_wrh_product_with_token_refresh", lambda *a, **kw: chosen[0])
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        for product in (first, second):
            chosen[0] = product
            appender.append_noaa_wrh_city("Singapore", [date(2026, 10, 6)], conn,
                rebuild_run_id="explicit_historical_backfill", now_utc=product.station_reference.fetched_at)
        assert conn.execute("SELECT high_temp FROM observations").fetchone()[0] == 32.0
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 1
        assert "wrh_current_snapshot" not in conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0]
    finally:
        conn.close()


def test_current_wrh_body_capacity_and_db_failure_preserve_prior_state(tmp_path, monkeypatch):
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first = _current_product(values=(32.41, 27.43))
    now = first.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first, as_of=now)
        conn.commit()
        second = _current_product(values=(30.41, 27.43), receipt="2026-10-06T02:01:00+00:00")
        monkeypatch.setattr(wrh, "_CURRENT_BODY_MAX_FILES", 0)
        with pytest.raises(ValueError, match="BODY_CAPACITY"):
            append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=second,
                                           as_of=second.station_reference.fetched_at)
        assert conn.execute("SELECT high_temp FROM observations").fetchone()[0] == 32.41
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 0
        monkeypatch.setattr(wrh, "_CURRENT_BODY_MAX_FILES", 65536)
        conn.execute("CREATE TEMP TRIGGER test_snapshot_failure BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, 'fixture update failed'); END")
        with pytest.raises(sqlite3.IntegrityError, match="fixture update failed"):
            append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=second,
                                           as_of=second.station_reference.fetched_at)
        assert conn.execute("SELECT high_temp FROM observations").fetchone()[0] == 32.41
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 0
    finally:
        conn.close()


def test_current_wrh_late_older_request_cannot_overwrite_newer_correction(tmp_path):
    from dataclasses import replace
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        newer = _current_product(values=(29.2, 28.0), receipt="2026-10-06T02:01:00+00:00")
        assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=newer,
                                             as_of=newer.station_reference.fetched_at) == "inserted"
        older = replace(_current_product(values=(33.0, 28.0), receipt="2026-10-06T02:02:00+00:00"),
                        request_started_at=datetime(2026, 10, 6, 2, tzinfo=timezone.utc),
                        coverage_end_utc=datetime(2026, 10, 6, 2, tzinfo=timezone.utc))
        assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=older,
                                             as_of=older.station_reference.fetched_at) == "older_or_ambiguous_receipt"
        assert conn.execute("SELECT high_temp FROM observations").fetchone()[0] == 29.2
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 0
    finally:
        conn.close()


def test_current_wrh_empty_stops_real_q_adapter_from_reusing_old_event(tmp_path):
    from types import SimpleNamespace
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.engine.event_reactor_adapter import _global_day0_execution_payload
    from src.events.day0_authority import DAY0_LIVE_AUTHORITY_MATCHES
    from src.contracts.exceptions import ObservationUnavailableError
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    product = _current_product(values=())
    now = product.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
        event = SimpleNamespace(payload_json=json.dumps({**DAY0_LIVE_AUTHORITY_MATCHES,
            "city": city.name, "target_date": "2026-10-06", "metric": "high",
            "settlement_source": "noaa_wrh_wsss", "station_id": "WSSS",
            "raw_value": 35.0, "rounded_value": 35, "observation_time": "2026-10-06T01:00:00+00:00"}))
        with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
            _global_day0_execution_payload(event,
                family=SimpleNamespace(city=city.name, target_date="2026-10-06", metric="high"),
                resolution=SimpleNamespace(measurement_unit="C", station_id="WSSS"),
                conditioning=None, observation_conn=conn, decision_time=now, posterior_id="old-carrier")
    finally:
        conn.close()


def test_current_wrh_noop_confirmation_fences_late_older_changed_request(tmp_path):
    from dataclasses import replace
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        first = _current_product(values=(29.1, 28.0), receipt="2026-10-06T02:00:00+00:00")
        confirmed = _current_product(values=(29.1, 28.0), receipt="2026-10-06T02:02:00+00:00")
        older = replace(_current_product(values=(35.0, 28.0), receipt="2026-10-06T02:03:00+00:00"),
                        request_started_at=datetime(2026, 10, 6, 2, 1, tzinfo=timezone.utc),
                        coverage_end_utc=datetime(2026, 10, 6, 2, 1, tzinfo=timezone.utc))
        for product, status in ((first, "inserted"), (confirmed, "noop"), (older, "older_or_ambiguous_receipt")):
            assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product,
                                                 as_of=product.station_reference.fetched_at) == status
        value, receipt, metadata = conn.execute("SELECT high_temp,fetched_at,high_provenance_metadata FROM observations").fetchone()
        assert value == 29.1 and receipt == first.station_reference.fetched_at.isoformat()
        assert json.loads(metadata)["wrh_latest_confirmation"]["received_at"] == confirmed.station_reference.fetched_at.isoformat()
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("module_name", ["src.execution.harvester", "src.ingest.harvester_truth_writer"])
@pytest.mark.parametrize("metric", ["high", "low"])
def test_current_wrh_cannot_enter_either_settlement_reader_before_complete_day(tmp_path, monkeypatch, module_name, metric):
    import importlib
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data import settlement_observation_selection as selection
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    day = "2026-10-06"
    final = _current_product(values=(34.0, 26.0), receipt="2026-10-07T02:00:00+00:00")
    now = final.station_reference.fetched_at
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now
    monkeypatch.setattr(selection, "datetime", Clock)
    lookup = importlib.import_module(module_name)._lookup_settlement_obs
    try:
        first = _current_product()
        append_current_noaa_wrh_product(conn, city=city, target_date=day, product=first,
                                       as_of=first.station_reference.fetched_at)
        assert conn.execute("SELECT authority FROM observations").fetchone()[0] == "UNVERIFIED"
        assert lookup(conn, city, day, temperature_metric=metric) is None
        append_current_noaa_wrh_product(conn, city=city, target_date=day, product=final, as_of=now)
        conn.commit()
        assert conn.execute("SELECT authority FROM observations").fetchone()[0] == "VERIFIED"
        found = lookup(conn, city, day, temperature_metric=metric)
        assert found is not None and found["observed_temp"] == (34.0 if metric == "high" else 26.0)
        empty = _current_product(values=(), receipt="2026-10-07T02:01:00+00:00")
        now = empty.station_reference.fetched_at
        append_current_noaa_wrh_product(conn, city=city, target_date=day, product=empty, as_of=now)
        assert lookup(conn, city, day, temperature_metric=metric) is None
    finally:
        conn.close()


def test_current_wrh_same_body_refetch_repairs_custody_without_reclocking(tmp_path):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.data.physical_current_delivery import _current_noaa_snapshot_revision
    from src.config import state_path
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first = _current_product(values=(32.14, 27.42))
    now = first.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first, as_of=now)
        before = _current_noaa_snapshot_revision(conn, city=city, target="2026-10-06", now=now)
        path = state_path("noaa_wrh_response_bodies") / (first.response_sha256 + ".zlib")
        for minute, broken in ((1, "missing"), (2, "corrupt")):
            if broken == "missing": path.unlink()
            else: path.write_bytes(b"corrupt fixture body")
            assert read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=now) == (True, None)
            newer = _current_product(values=(32.14, 27.42), receipt=(now + timedelta(minutes=minute)).isoformat())
            assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=newer,
                                                 as_of=newer.station_reference.fetched_at) == "noop"
            owned, restored = read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06",
                                                           as_of=newer.station_reference.fetched_at)
            assert owned is True and restored.received_at == now
            after = _current_noaa_snapshot_revision(conn, city=city, target="2026-10-06", now=newer.station_reference.fetched_at)
            assert after != before  # transport recovery hint, never a renewed source receipt
            before = after
            assert restored.response_sha256 == first.response_sha256
    finally:
        conn.close()


def test_current_wrh_two_city_native_batch_acquisition_replays_both_owners(tmp_path, monkeypatch):
    import httpx
    import hashlib
    from zoneinfo import ZoneInfo
    from src.data import station_temperature_adapters as adapters, noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.config import state_path
    cities = (cities_by_name["Singapore"], cities_by_name["Tokyo"])
    now = datetime(2026, 10, 6, 3, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now
    monkeypatch.setattr(adapters, "datetime", Clock)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture-token")
    stations = []
    for index, city in enumerate(cities):
        observed = datetime(2026, 10, 6, 1, tzinfo=timezone.utc).astimezone(ZoneInfo(city.timezone))
        stations.append({"STID": city.wu_station, "OBSERVATIONS": {"date_time": [observed.isoformat()],
            "air_temp_set_1": [27.25 + index], "sea_level_pressure_set_1": [1010]}})
    body = json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Celsius"}, "STATION": stations}).encode()
    seen = []
    def handler(request):
        seen.append(request)
        assert set(request.url.params["STID"].split(",")) == {city.wu_station for city in cities}
        assert int(request.url.params["recent"]) > 180
        return httpx.Response(200, content=body)
    adapters._WRH_CURRENT_PRODUCT_CACHE.clear()
    conn = _attached(*_live_schema_db_pair(tmp_path))
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            products = adapters.fetch_current_noaa_wrh_products(tuple((city, "2026-10-06") for city in cities), client=client)
            repeated = adapters.fetch_current_noaa_wrh_products(tuple((city, "2026-10-06") for city in cities), client=client)
        assert len(seen) == 1 and len(products) == len(repeated) == 2
        for city, day, product in products:
            assert product.native_body == body and product.response_sha256 == hashlib.sha256(body).hexdigest()
            append_current_noaa_wrh_product(conn, city=city, target_date=day, product=product, as_of=now)
            owned, snapshot = read_current_noaa_wrh_snapshot(conn, city=city, target_date=day, as_of=now)
            assert owned is True and snapshot is not None
            assert snapshot.extreme("high").value == (27.25 if city.name == "Singapore" else 28.25)
        assert len(list(state_path("noaa_wrh_response_bodies").glob(hashlib.sha256(body).hexdigest() + ".zlib"))) == 1
    finally:
        adapters._WRH_CURRENT_PRODUCT_CACHE.clear()
        conn.close()


@pytest.mark.parametrize("values", [(32.0, 28.0), (31.0, 28.0)])
def test_current_wrh_prepared_cas_has_no_body_io_inside_transaction(tmp_path, monkeypatch, values):
    from src.data.daily_obs_append import append_current_noaa_wrh_product, prepare_current_noaa_wrh_product
    from src.data import noaa_wrh_timeseries as wrh
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first = _current_product()
    try:
        for index, product in enumerate((first, _current_product(values=values, receipt="2026-10-06T02:01:00+00:00"))):
            now = product.station_reference.fetched_at
            prepared = prepare_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
            with monkeypatch.context() as patch:
                patch.setattr(wrh, "persist_current_snapshot_body", lambda *a, **k: pytest.fail("body write under canonical transaction"))
                patch.setattr(wrh, "read_current_snapshot_body", lambda *a, **k: pytest.fail("body read under canonical transaction"))
                conn.execute("BEGIN IMMEDIATE")
                result = append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product,
                                                        as_of=now, prepared=prepared)
                conn.commit()
                assert result == ("inserted" if index == 0 else "noop" if values == (32.0, 28.0) else "revision")
        stale = prepare_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first,
                                                as_of=first.station_reference.fetched_at + timedelta(hours=1))
        conn.execute("UPDATE observations SET data_source_version='concurrent_writer'")
        conn.commit()
        with pytest.raises(ValueError, match="WRH_CURRENT_PREPARED_ROW_CHANGED"):
            append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first,
                                           as_of=stale.as_of, prepared=stale)
    finally:
        conn.close()


def test_current_wrh_empty_snapshot_wake_is_independent_of_raw_scan_failure(tmp_path, monkeypatch):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data import physical_current_delivery as delivery
    city = cities_by_name["Singapore"]
    forecasts_path, world_path = _live_schema_db_pair(tmp_path)
    conn = _attached(forecasts_path, world_path)
    product = _current_product(values=())
    now = product.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
    finally:
        conn.close()
    monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda **k: sqlite3.connect(world_path))
    monkeypatch.setattr("src.state.db.get_forecasts_connection_read_only", lambda **k: sqlite3.connect(forecasts_path))
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    def unavailable(*a, **k):
        raise TimeoutError("raw ledger busy")
    monkeypatch.setattr(delivery, "_current_temperature_ledger_revision", unavailable)
    result = delivery.publish_current_temperature_wakes(cities=(city,), scopes=((city.name, "2026-10-06", "high"),), now=now)
    assert result["published"] == 1
    assert result["status"] == "WAKE_DEFERRED"


def _current_wrh_ingest_fixture(monkeypatch, tmp_path, *, now):
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace
    import src.ingest_main as ingest
    from src.state import db, write_coordinator as coordinator
    from src.data import physical_current_delivery as delivery, replacement_forecast_production as production
    from src.state.schema.observation_prints_schema import ensure_table
    paths = _live_schema_db_pair(tmp_path)
    with sqlite3.connect(paths[1]) as conn:
        ensure_table(conn)
    mutex = threading.Lock()
    @contextmanager
    def attached(**kwargs):
        conn = _attached(*paths)
        try:
            yield conn
        finally:
            conn.close()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now
    class Lease:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def record_commit(self, **kwargs): pass
    monkeypatch.setattr(ingest, "datetime", Clock)
    monkeypatch.setattr(db, "get_forecasts_connection_with_world", attached)
    monkeypatch.setattr(db, "get_forecasts_connection_with_world_read_only", attached)
    monkeypatch.setattr(db, "get_world_connection", lambda **kw: sqlite3.connect(paths[1]))
    monkeypatch.setattr(db, "get_world_connection_read_only", lambda **kw: sqlite3.connect(paths[1]))
    monkeypatch.setattr(db, "world_write_mutex", lambda: mutex)
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator", lambda: SimpleNamespace(lease=lambda *a, **kw: Lease()))
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(delivery, "current_temperature_priority_families", lambda: {})
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: {})
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda *a, **kw: {})
    return paths, mutex


def test_current_wrh_scheduled_completion_drains_only_its_unfinished_owner(tmp_path, monkeypatch):
    import src.ingest_main as ingest
    from src.data import daily_obs_append as appender, station_temperature_adapters as adapters
    city = cities_by_name["Singapore"]
    first = _current_product()
    final = _current_product(values=(34.4, 25.5), receipt="2026-10-07T02:00:00+00:00")
    now = final.station_reference.fetched_at
    paths, mutex = _current_wrh_ingest_fixture(monkeypatch, tmp_path, now=now)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {city.name: city})
    with _attached(*paths) as conn:
        appender.append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first,
                                               as_of=first.station_reference.fetched_at)
    scopes_seen = []
    def products(scopes):
        scopes_seen.extend(scopes)
        yield city, "2026-10-06", final
    monkeypatch.setattr(adapters, "iter_current_noaa_wrh_products", products)
    assert ingest._day0_current_noaa_wrh_tick.__wrapped__()["committed"] == 1
    assert scopes_seen == [(city, "2026-10-06")]
    with _attached(*paths) as conn:
        assert tuple(conn.execute("SELECT high_temp,low_temp,authority FROM observations").fetchone()) == (34.4, 25.5, "VERIFIED")


@pytest.mark.parametrize("slow", ["wrh", "other"])
def test_current_wrh_provider_lanes_commit_and_wake_before_unrelated_response(tmp_path, monkeypatch, slow):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import src.ingest_main as ingest
    from src.data import station_temperature_adapters as adapters, physical_current_sources as routes, physical_current_delivery as delivery
    from src.data import noaa_wrh_timeseries as wrh
    from src.runtime import reactor_wake
    from src.data.scheduler_adapter import executor_class_for
    from src.data.source_job_registry import JOB_REGISTRY
    city, other = cities_by_name["Singapore"], cities_by_name["Helsinki"]
    now = datetime(2026, 10, 6, 2, tzinfo=timezone.utc)
    paths, mutex = _current_wrh_ingest_fixture(monkeypatch, tmp_path, now=now)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {city.name: city, other.name: other})
    monkeypatch.setattr(delivery, "current_temperature_priority_families", lambda: {(city.name, "2026-10-06", "high"): 0})
    route = next(r for r in routes.load_physical_current_sources()[0] if r.provider == "fmi" or r.source_channel == "fmi_airport_temperature")
    monkeypatch.setattr(routes, "physical_current_sources_for_city", lambda selected: (route,) if selected.name == other.name else ())
    entered, release = threading.Event(), threading.Event()
    received, wakes = [], []
    def pause(name):
        if name == slow:
            entered.set()
            assert release.wait(5)
        received.append(name)
    def products(scopes):
        pause("wrh")
        yield city, "2026-10-06", _current_product()
    def sample(*args, **kwargs):
        pause("other")
        from src.data.fmi_airport_temperature import FmiTemperaturePrint
        return (FmiTemperaturePrint(now-timedelta(minutes=1), now, 10.0, "fixture FMI"),)
    monkeypatch.setattr(adapters, "iter_current_noaa_wrh_products", products)
    monkeypatch.setattr(adapters, "fetch_station_temperature", sample)
    original_persist, original_read = wrh.persist_current_snapshot_body, wrh.read_current_snapshot_body
    def unlocked(fn):
        def wrapped(*args, **kwargs):
            assert not mutex.locked(), "native body I/O under WORLD mutex"
            return fn(*args, **kwargs)
        return wrapped
    monkeypatch.setattr(wrh, "persist_current_snapshot_body", unlocked(original_persist))
    monkeypatch.setattr(wrh, "read_current_snapshot_body", unlocked(original_read))
    original_publish = reactor_wake.publish_reactor_wake
    def publish(**kwargs):
        family = kwargs["forecast_families"][0]
        with _attached(*paths) as conn:
            table = "observations" if family[0] == city.name else "world.observation_prints"
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE city=?", (family[0],)).fetchone()[0] == 1
        assert not mutex.locked()
        result = original_publish(**kwargs)
        wakes.append("wrh" if family[0] == city.name else "other")
        return result
    monkeypatch.setattr(reactor_wake, "publish_reactor_wake", publish)
    assert executor_class_for(JOB_REGISTRY["ingest_day0_noaa_wrh_current"]) != executor_class_for(JOB_REGISTRY["ingest_day0_fmi_temperature"])
    functions = {"wrh": ingest._day0_current_noaa_wrh_tick.__wrapped__, "other": ingest._day0_fmi_temperature_tick.__wrapped__}
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(functions[slow])
        try:
            assert entered.wait(5)
            fast = "other" if slow == "wrh" else "wrh"
            pool.submit(functions[fast]).result(5)
            assert received == [fast] and fast in wakes and slow not in wakes
        finally:
            release.set()
        blocked.result(5)


def test_current_wrh_different_body_recovers_lost_custody_and_preserves_new_receipt(tmp_path):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.config import state_path
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first, newer = _current_product(), _current_product(values=(30.0, 28.0), receipt="2026-10-06T02:01:00+00:00")
    try:
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first, as_of=first.station_reference.fetched_at)
        (state_path("noaa_wrh_response_bodies") / (first.response_sha256 + ".zlib")).unlink()
        assert read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=newer.station_reference.fetched_at) == (True, None)
        assert append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=newer, as_of=newer.station_reference.fetched_at) == "revision"
        owned, recovered = read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=newer.station_reference.fetched_at)
        assert owned is True and recovered.received_at == newer.station_reference.fetched_at
        assert recovered.extreme("high").value == 30.0
    finally:
        conn.close()


def test_current_wrh_prepared_values_cannot_be_changed_after_native_validation(tmp_path):
    from dataclasses import replace
    from src.data.daily_obs_append import append_current_noaa_wrh_product, prepare_current_noaa_wrh_product
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    product = _current_product()
    now = product.station_reference.fetched_at
    try:
        prepared = prepare_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now)
        incoming = json.loads(prepared.incoming_json)
        incoming["high_temp"] = 40
        prepared = replace(prepared, incoming_json=json.dumps(incoming))
        with pytest.raises(ValueError, match="WRH_CURRENT_PREPARED_CONTENT_MISMATCH"):
            append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=product, as_of=now, prepared=prepared)
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        conn.close()


def test_current_wrh_old_absence_cannot_revive_after_nonempty_owner_loses_body(tmp_path, monkeypatch):
    from src.data import settlement_observation_selection as selection
    from src.state import data_coverage
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.config import state_path
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    after = datetime(2026, 10, 8, 4, 10, tzinfo=timezone.utc)
    try:
        empty = _current_product(values=(), receipt=after.isoformat())
        monkeypatch.setattr(data_coverage, "_now_utc_iso", lambda: after.isoformat())
        assert selection.record_confirmed_empty(conn, city=city, target_date="2026-10-06", product=empty,
            request_url="https://www.weather.gov/wrh/timeseries?site=WSSS", retry_after=after+timedelta(minutes=5), now=after)
        conn.commit()
        assert selection.observation_selection(conn, city, "2026-10-06", "wu_icao_history", as_of=after) is not None
        newer = _current_product(values=(33.182, 27.291), receipt=(after+timedelta(minutes=1)).isoformat())
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=newer, as_of=newer.station_reference.fetched_at)
        assert selection.observation_selection(conn, city, "2026-10-06", "wu_icao_history", as_of=newer.station_reference.fetched_at) is None
        (state_path("noaa_wrh_response_bodies") / (newer.response_sha256+".zlib")).unlink()
        assert selection.observation_selection(conn, city, "2026-10-06", "wu_icao_history", as_of=newer.station_reference.fetched_at) is None
        later = after+timedelta(minutes=2)
        latest_empty = _current_product(values=(), receipt=later.isoformat())
        append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=latest_empty, as_of=later)
        assert selection.observation_selection(conn, city, "2026-10-06", "wu_icao_history", as_of=later) is None
        monkeypatch.setattr(data_coverage, "_now_utc_iso", lambda: later.isoformat())
        assert selection.record_confirmed_empty(conn, city=city, target_date="2026-10-06", product=latest_empty,
            request_url="https://www.weather.gov/wrh/timeseries?site=WSSS", retry_after=later+timedelta(minutes=5), now=later)
        assert selection.observation_selection(conn, city, "2026-10-06", "wu_icao_history", as_of=later) is not None
    finally:
        conn.close()


def test_current_wrh_restart_recovers_one_aged_typed_owner_without_backfill_promotion(tmp_path, monkeypatch):
    from dataclasses import replace
    import src.ingest_main as ingest
    from src.data import daily_obs_append as appender, station_temperature_adapters as adapters
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    city = cities_by_name["Singapore"]
    now = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
    paths, mutex = _current_wrh_ingest_fixture(monkeypatch, tmp_path, now=now)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {city.name: city})
    first = _current_product()
    with _attached(*paths) as conn:
        appender.append_current_noaa_wrh_product(conn, city=city, target_date="2026-10-06", product=first, as_of=first.station_reference.fetched_at)
        conn.execute("INSERT INTO observations(city,target_date,source,high_temp,low_temp,unit,station_id,fetched_at,authority,rebuild_run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (city.name,"2026-10-05", "noaa_wrh_wsss",30,25,"C","WSSS",first.station_reference.fetched_at.isoformat(),"QUARANTINED","legacy_backfill"))
    final = replace(_current_product(values=(34.4, 25.5), receipt=now.isoformat()),
                    coverage_start_utc=datetime(2026,10,5,16,tzinfo=timezone.utc),
                    coverage_end_utc=datetime(2026,10,6,16,tzinfo=timezone.utc))
    order = []
    def current(scopes):
        order.append("current_complete")
        return iter(())
    def old(scope):
        assert order == ["current_complete"]
        assert scope == (city, "2026-10-06")
        yield city, "2026-10-06", final
    monkeypatch.setattr(adapters, "iter_current_noaa_wrh_products", current)
    monkeypatch.setattr(adapters, "iter_noaa_wrh_completed_owner_recovery", old)
    assert ingest._day0_current_noaa_wrh_tick.__wrapped__()["committed"] == 1
    with _attached(*paths) as conn:
        owned, result = read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=now)
        assert owned is True and result.complete_day and result.received_at == now
        _, prior = read_current_noaa_wrh_snapshot(conn, city=city, target_date="2026-10-06", as_of=now-timedelta(seconds=1))
        assert prior.received_at == first.station_reference.fetched_at and not prior.complete_day
        assert conn.execute("SELECT authority FROM observations WHERE target_date='2026-10-05'").fetchone()[0] == "QUARANTINED"


def test_current_wrh_aged_recovery_uses_bounded_explicit_day_request(monkeypatch):
    import httpx
    from src.data import station_temperature_adapters as adapters, noaa_wrh_timeseries as wrh
    city = cities_by_name["Singapore"]
    now = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now
    monkeypatch.setattr(adapters, "datetime", Clock)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "fixture")
    body = _current_product().native_body
    calls = []
    def handler(request):
        calls.append(request)
        assert "recent" not in request.url.params
        assert request.url.params["start"] == "202610051600"
        assert request.url.params["end"] == "202610061600"
        return httpx.Response(200, content=body)
    adapters._WRH_CURRENT_PRODUCT_CACHE.clear()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        products = tuple(adapters.iter_noaa_wrh_completed_owner_recovery((city,"2026-10-06"), client=client))
    assert len(calls) == len(products) == 1
    snapshot = wrh.current_snapshot_from_product(products[0][2], city=city, target_date="2026-10-06", as_of=now)
    assert snapshot.complete_day and snapshot.received_at == now


def test_current_wrh_missing_semantic_body_is_not_masked_by_readable_noop_confirmation(tmp_path):
    import hashlib
    from dataclasses import replace
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.config import state_path
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first = _current_product()
    def metadata_only(minute):
        data = json.loads(first.native_body)
        data["SUMMARY"]["RESPONSE_TIME"] = minute
        body = json.dumps(data).encode()
        received = first.station_reference.fetched_at+timedelta(minutes=minute)
        parsed = wrh.product_from_response(body,"WSSS",unit="C",fetched_at=received,source_response_sha256=hashlib.sha256(body).hexdigest())
        return replace(parsed,request_started_at=received-timedelta(seconds=1),
                       coverage_start_utc=first.coverage_start_utc,coverage_end_utc=received-timedelta(seconds=1))
    try:
        append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=first.station_reference.fetched_at)
        confirmation = metadata_only(1)
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=confirmation,as_of=confirmation.station_reference.fetched_at) == "noop"
        (state_path("noaa_wrh_response_bodies")/(first.response_sha256+".zlib")).unlink()
        incoming = metadata_only(2)
        assert read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=incoming.station_reference.fetched_at) == (True,None)
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=incoming,as_of=incoming.station_reference.fetched_at) == "revision"
        owned, result = read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=incoming.station_reference.fetched_at)
        assert owned is True and result.response_sha256 == incoming.response_sha256
    finally:
        conn.close()


def test_current_wrh_malformed_native_station_scope_returns_claimed_unavailable(tmp_path):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first = _current_product()
    now = first.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=now)
        metadata = json.loads(conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0])
        metadata["wrh_current_snapshot"]["request_station_ids"] = ["ZBAA"]
        encoded = json.dumps(metadata)
        conn.execute("UPDATE observations SET high_provenance_metadata=?,low_provenance_metadata=?",(encoded,encoded))
        assert read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=now) == (True,None)
    finally:
        conn.close()


def test_current_wrh_duplicate_ticks_do_not_overlap_in_registered_pool(tmp_path, monkeypatch):
    import threading
    import src.ingest_main as ingest
    from src.data import station_temperature_adapters as adapters, physical_current_delivery as delivery
    from src.data.scheduler_adapter import registry_executor_pools
    city = cities_by_name["Singapore"]
    now = datetime(2026,10,6,2,tzinfo=timezone.utc)
    _current_wrh_ingest_fixture(monkeypatch,tmp_path,now=now)
    monkeypatch.setattr("src.config.runtime_cities_by_name",lambda:{city.name:city})
    monkeypatch.setattr(delivery,"current_temperature_priority_families",lambda:{(city.name,"2026-10-06","high"):0})
    entered, release = threading.Event(), threading.Event()
    calls=[]
    def products(scopes):
        calls.append(1)
        if len(calls)==1:
            entered.set()
            assert release.wait(5)
        yield city,"2026-10-06",_current_product()
    monkeypatch.setattr(adapters,"iter_current_noaa_wrh_products",products)
    pools=registry_executor_pools()
    pool=pools["noaa_wrh_source_clock_db"]._pool
    try:
        first=pool.submit(ingest._day0_current_noaa_wrh_tick.__wrapped__)
        assert entered.wait(5)
        second=pool.submit(ingest._day0_current_noaa_wrh_tick.__wrapped__)
        assert not second.done() and len(calls)==1
        release.set()
        assert first.result(5)["committed"]==1
        assert second.result(5)["committed"]==0
    finally:
        release.set()
        for executor in pools.values(): executor.shutdown(wait=True)


def test_current_wrh_refused_old_owner_does_not_starve_later_aged_scope(tmp_path, monkeypatch):
    import hashlib
    from dataclasses import replace
    import src.ingest_main as ingest
    from src.data import daily_obs_append as appender, station_temperature_adapters as adapters, noaa_wrh_timeseries as wrh
    city = cities_by_name["Singapore"]
    now = datetime(2026,10,20,2,tzinfo=timezone.utc)
    paths, _mutex = _current_wrh_ingest_fixture(monkeypatch,tmp_path,now=now)
    monkeypatch.setattr("src.config.runtime_cities_by_name",lambda:{city.name:city})
    monkeypatch.setattr(ingest,"_WRH_OLD_OWNER_CURSOR",None)
    first=_current_product()
    with _attached(*paths) as conn:
        appender.append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=first.station_reference.fetched_at)
        body=first.native_body.replace(b"2026-10-06",b"2026-10-07")
        received=first.station_reference.fetched_at+timedelta(days=1)
        second=replace(wrh.product_from_response(body,"WSSS",unit="C",fetched_at=received,source_response_sha256=hashlib.sha256(body).hexdigest()),
            request_started_at=received-timedelta(seconds=1),coverage_start_utc=first.coverage_start_utc+timedelta(days=1),coverage_end_utc=received-timedelta(seconds=1))
        appender.append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-07",product=second,as_of=received)
    seen=[]
    monkeypatch.setattr(adapters,"iter_current_noaa_wrh_products",lambda scopes: iter(()))
    def refused(scope):
        seen.append(scope[1])
        return iter(())
    monkeypatch.setattr(adapters,"iter_noaa_wrh_completed_owner_recovery",refused)
    for _ in range(3):
        result=ingest._day0_current_noaa_wrh_tick.__wrapped__()
        assert result["source_unavailable"]==1 and result["committed"]==0
    assert seen==["2026-10-06","2026-10-07","2026-10-06"]


def test_current_wrh_complete_empty_owner_retries_until_later_nonempty_without_exposure(tmp_path, monkeypatch):
    import src.ingest_main as ingest
    from src.data import daily_obs_append as appender, station_temperature_adapters as adapters
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    city=cities_by_name["Singapore"]
    empty=_current_product(values=(),receipt="2026-10-07T02:00:00+00:00")
    final=_current_product(values=(34.4,25.5),receipt="2026-10-07T03:00:00+00:00")
    now=final.station_reference.fetched_at
    paths,_mutex=_current_wrh_ingest_fixture(monkeypatch,tmp_path,now=now)
    monkeypatch.setattr("src.config.runtime_cities_by_name",lambda:{city.name:city})
    with _attached(*paths) as conn:
        appender.append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=empty,as_of=empty.station_reference.fetched_at)
        _,snapshot=read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=now)
        assert snapshot.complete_day is True and snapshot.extreme("high") is None
    seen=[]
    def products(scopes):
        seen.extend(scopes)
        yield city,"2026-10-06",final
    monkeypatch.setattr(adapters,"iter_current_noaa_wrh_products",products)
    assert ingest._day0_current_noaa_wrh_tick.__wrapped__()["committed"]==1
    assert seen==[(city,"2026-10-06")]
    with _attached(*paths) as conn:
        assert conn.execute("SELECT authority FROM observations").fetchone()[0]=="VERIFIED"


def _wrh_metadata_only_product(first, minute):
    import hashlib
    from dataclasses import replace
    from src.data import noaa_wrh_timeseries as wrh
    data = json.loads(first.native_body)
    data["SUMMARY"]["RESPONSE_TIME"] = minute
    body = json.dumps(data).encode()
    received = first.station_reference.fetched_at + timedelta(minutes=minute)
    parsed = wrh.product_from_response(body,"WSSS",unit="C",fetched_at=received,
                                       source_response_sha256=hashlib.sha256(body).hexdigest())
    return replace(parsed,request_started_at=received-timedelta(seconds=1),
                   coverage_start_utc=first.coverage_start_utc,coverage_end_utc=received-timedelta(seconds=1))


def test_current_wrh_noop_order_receipts_do_not_consume_semantic_custody_capacity(tmp_path, monkeypatch):
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.data.physical_current_delivery import _current_noaa_snapshot_revision
    from src.execution.day0_hard_fact_exit import _noaa_wrh_hard_fact_evidence
    monkeypatch.setattr("src.config.state_path",lambda name:tmp_path/name)
    monkeypatch.setattr(wrh,"_CURRENT_BODY_MAX_FILES",3)
    city=cities_by_name["Singapore"]
    paths=_live_schema_db_pair(tmp_path)
    conn=_attached(*paths)
    first=_current_product()
    now=first.station_reference.fetched_at
    try:
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=now)=="inserted"
        before_wake=_current_noaa_snapshot_revision(conn,city=city,target="2026-10-06",now=now)
        before_q=_noaa_wrh_hard_fact_evidence(city=city,target_date="2026-10-06",metric="high",now=now,world_conn=conn).as_dict()
        for minute in range(1,12):
            current=_wrh_metadata_only_product(first,minute)
            assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=current,as_of=current.station_reference.fetched_at)=="noop"
            conn.close()  # Restart from durable receipts, not a process-local floor.
            conn=_attached(*paths)
            owned,snapshot=read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=current.station_reference.fetched_at)
            assert owned is True and snapshot.received_at==now and snapshot.response_sha256==first.response_sha256
            assert _current_noaa_snapshot_revision(conn,city=city,target="2026-10-06",now=current.station_reference.fetched_at)==before_wake
            assert _noaa_wrh_hard_fact_evidence(city=city,target_date="2026-10-06",metric="high",now=current.station_reference.fetched_at,world_conn=conn).as_dict()==before_q
        assert len(list((tmp_path/"noaa_wrh_response_bodies").glob("*.zlib")))==1
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0]==0
        correction=_current_product(values=(29.0,28.0),receipt="2026-10-06T02:12:00+00:00")
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=correction,as_of=correction.station_reference.fetched_at)=="revision"
        assert len(list((tmp_path/"noaa_wrh_response_bodies").glob("*.zlib")))==2
        _,snapshot=read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=now)
        assert snapshot.response_sha256==first.response_sha256
    finally:
        conn.close()


@pytest.mark.parametrize("mutation", ["scope", "identity", "source_body", "content", "order", "naive", "digest", "authority_fields"])
def test_current_wrh_noop_control_mutations_cannot_become_source_authority(tmp_path, mutation):
    import hashlib
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot, _json_dumps
    city=cities_by_name["Singapore"]
    conn=_attached(*_live_schema_db_pair(tmp_path))
    first=_current_product()
    second=_wrh_metadata_only_product(first,1)
    now=second.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=first.station_reference.fetched_at)
        append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=second,as_of=now)
        metadata=json.loads(conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0])
        receipt=metadata["wrh_latest_confirmation"]
        if mutation=="scope": receipt["scope"]["station"]="ZBAA"
        elif mutation=="identity": receipt["semantic_snapshot_identity"]="b"*64
        elif mutation=="source_body": receipt["retained_semantic_body_sha256"]="b"*64
        elif mutation=="content": receipt["semantic_content_identity"]="b"*64
        elif mutation=="order": receipt["request_started_at"]="2026-10-06T01:00:00+00:00"
        elif mutation=="naive": receipt["received_at"]="2026-10-06T02:01:00"
        elif mutation=="digest": receipt["validated_response_sha256"]="not-a-digest"
        else: receipt.update(rows=first.rows,complete_day=True,native_body_ref=first.response_sha256)
        if mutation=="authority_fields": receipt["rows"]=[]
        # Even recomputing the control checksum cannot grant a mismatched scope,
        # noncausal order, or an authoritative field outside its typed schema.
        receipt["receipt_identity"]=hashlib.sha256(_json_dumps({k:v for k,v in receipt.items() if k!="receipt_identity"}).encode()).hexdigest()
        encoded=json.dumps(metadata)
        conn.execute("UPDATE observations SET high_provenance_metadata=?,low_provenance_metadata=?",(encoded,encoded))
        conn.commit()
        assert read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=now)==(True,None)
        with pytest.raises(ValueError,match="WRH_ACQUISITION_CONTROL"):
            append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=_wrh_metadata_only_product(first,2),as_of=now+timedelta(minutes=1))
    finally:
        conn.close()


def test_current_wrh_legacy_full_confirmation_retains_body_and_request_fence(tmp_path,monkeypatch):
    from dataclasses import replace
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    monkeypatch.setattr("src.config.state_path",lambda name:tmp_path/name)
    city=cities_by_name["Singapore"]
    conn=_attached(*_live_schema_db_pair(tmp_path))
    first=_current_product()
    confirmation=_wrh_metadata_only_product(first,2)
    try:
        append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=first.station_reference.fetched_at)
        legacy=wrh.current_snapshot_from_product(confirmation,city=city,target_date="2026-10-06",as_of=confirmation.station_reference.fetched_at)
        wrh.persist_current_snapshot_body(confirmation.native_body)
        metadata=json.loads(conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0])
        metadata["wrh_latest_confirmation"]=legacy.provenance()
        encoded=json.dumps(metadata)
        conn.execute("UPDATE observations SET high_provenance_metadata=?,low_provenance_metadata=?",(encoded,encoded))
        conn.commit()
        owned,current=read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=confirmation.station_reference.fetched_at)
        assert owned is True and current.response_sha256==first.response_sha256
        late=replace(_current_product(values=(35,28),receipt="2026-10-06T02:03:00+00:00"),request_started_at=first.station_reference.fetched_at+timedelta(seconds=30))
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=late,as_of=late.station_reference.fetched_at)=="older_or_ambiguous_receipt"
        changed=_current_product(values=(29,28),receipt="2026-10-06T02:04:00+00:00")
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=changed,as_of=changed.station_reference.fetched_at)=="revision"
        assert (tmp_path/"noaa_wrh_response_bodies"/(confirmation.response_sha256+".zlib")).exists()
        _,prior=read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=confirmation.station_reference.fetched_at)
        assert prior.response_sha256==first.response_sha256
        history=json.loads(conn.execute("SELECT existing_row_json FROM world.daily_observation_revisions").fetchone()[0])
        assert json.loads(history["high_provenance_metadata"])["wrh_latest_confirmation"]==legacy.provenance()
    finally:
        conn.close()


def test_current_wrh_legacy_confirmation_upgrades_without_deleting_or_bypassing_capacity(tmp_path,monkeypatch):
    from dataclasses import replace
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    monkeypatch.setattr("src.config.state_path",lambda name:tmp_path/name)
    monkeypatch.setattr(wrh,"_CURRENT_BODY_MAX_FILES",3)
    city=cities_by_name["Singapore"]
    conn=_attached(*_live_schema_db_pair(tmp_path))
    first=_current_product()
    legacy=_wrh_metadata_only_product(first,1)
    try:
        append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=first,as_of=first.station_reference.fetched_at)
        wrh.persist_current_snapshot_body(legacy.native_body)
        metadata=json.loads(conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()[0])
        metadata["wrh_latest_confirmation"]=wrh.current_snapshot_from_product(legacy,city=city,target_date="2026-10-06",as_of=legacy.station_reference.fetched_at).provenance()
        encoded=json.dumps(metadata)
        conn.execute("UPDATE observations SET high_provenance_metadata=?,low_provenance_metadata=?",(encoded,encoded));conn.commit()
        control=_wrh_metadata_only_product(first,2)
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=control,as_of=control.station_reference.fetched_at)=="noop"
        assert len(list((tmp_path/"noaa_wrh_response_bodies").glob("*.zlib")))==2
        late=replace(_current_product(values=(35,28),receipt="2026-10-06T02:03:00+00:00"),request_started_at=first.station_reference.fetched_at+timedelta(seconds=90))
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=late,as_of=late.station_reference.fetched_at)=="older_or_ambiguous_receipt"
        correction=_current_product(values=(29,28),receipt="2026-10-06T02:04:00+00:00")
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=correction,as_of=correction.station_reference.fetched_at)=="revision"
        assert len(list((tmp_path/"noaa_wrh_response_bodies").glob("*.zlib")))==3
        assert (tmp_path/"noaa_wrh_response_bodies"/(legacy.response_sha256+".zlib")).exists()
        noop=_wrh_metadata_only_product(correction,1)
        assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=noop,as_of=noop.station_reference.fetched_at)=="noop"
        excess=_current_product(values=(30,28),receipt="2026-10-06T02:06:00+00:00")
        with pytest.raises(ValueError,match="WRH_SNAPSHOT_BODY_CAPACITY"):
            append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=excess,as_of=excess.station_reference.fetched_at)
        _,current=read_current_noaa_wrh_snapshot(conn,city=city,target_date="2026-10-06",as_of=excess.station_reference.fetched_at)
        assert current.response_sha256==correction.response_sha256
    finally:
        conn.close()


def test_current_wrh_quarantined_owner_does_not_retain_uncommittable_bodies(tmp_path,monkeypatch):
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import append_current_noaa_wrh_product, prepare_current_noaa_wrh_product
    monkeypatch.setattr("src.config.state_path",lambda name:tmp_path/name)
    monkeypatch.setattr(wrh,"_CURRENT_BODY_MAX_FILES",3)
    city=cities_by_name["Singapore"]
    conn=_attached(*_live_schema_db_pair(tmp_path))
    try:
        conn.execute("INSERT INTO observations(city,target_date,source,high_temp,low_temp,unit,station_id,fetched_at,authority,rebuild_run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (city.name,"2026-10-06","noaa_wrh_wsss",30,25,"C","WSSS","2026-10-06T01:50:00+00:00","QUARANTINED","disputed_backfill"))
        conn.commit()
        before=tuple(conn.execute("SELECT * FROM observations").fetchone())
        for minute in range(5):
            product=_current_product(values=(32+minute,28),receipt=f"2026-10-06T02:0{minute}:00+00:00")
            assert append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=product,
                as_of=product.station_reference.fetched_at)=="existing_disputed"
        assert not list((tmp_path/"noaa_wrh_response_bodies").glob("*.zlib"))
        assert tuple(conn.execute("SELECT * FROM observations").fetchone())==before
        assert conn.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0]==0
        prepared=prepare_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=product,
                                                  as_of=product.station_reference.fetched_at)
        conn.execute("UPDATE observations SET authority='UNVERIFIED'");conn.commit()
        with pytest.raises(ValueError,match="WRH_CURRENT_PREPARED_ROW_CHANGED"):
            append_current_noaa_wrh_product(conn,city=city,target_date="2026-10-06",product=product,
                as_of=product.station_reference.fetched_at,prepared=prepared)
        assert not list((tmp_path/"noaa_wrh_response_bodies").glob("*.zlib"))
    finally:
        conn.close()
