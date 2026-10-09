# Created: 2026-10-09
# Last audited: 2026-10-09
# Authority basis: docs/operations/current/plans/task_2026-10-09_us_asos5_ingest.md (coordinator
#   decisions 1-7); G9 (noaa_page_absorbing_value_f) and G10 (record_page_print_absences) of
#   task_2026-10-06_fast_obs_closure.
# Purpose: The degF page batch's ASOS 5-minute rows land in WORLD as asos5_<icao>, a derived channel
#   of the existing noaa_wrh tick: page prints byte-identical, G10 blind to them, own savepoint, no
#   trace, and no existing reader admits them.
# Reuse: Run when parse_station_payload's noaa_wrh branch, fetch_station_temperature, the
#   physical-current tick, or any observation_prints reader's channel set changes.
"""The page feed's 5-minute ASOS rows are their own channel, invisible to every current reader."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data import station_temperature_adapters as adapters
from src.data.physical_current_sources import load_physical_current_sources
from src.state.schema.fact_revocations_schema import ensure_table as ensure_revocations
from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc
FIXTURE = Path(__file__).parents[1] / "fixtures" / "noaa_wrh" / "asos5_batch_kdal_ksea_kmia.json"
RECEIPT = datetime(2026, 10, 7, 19, 6, 7, 123456, tzinfo=UTC)
# Page prints of the origin/live 1e3db865f parser on this fixture at RECEIPT (see _digest).
PAGE_DIGESTS = {
    "KDAL": (6, "4d76dbd59ea524ef4aa8d3d12195d4f5d93f31f862f7637cb07edeeab1dbfc1a"),
    "KSEA": (6, "e4d4cff2b69951ed4eff179c5ebc3bebf456bedc7f9e6713460d2a1c0182effd"),
    "KMIA": (2, "d0fb27496af8b3f86d2160bf4d6a070edd69bd11f958a2334ea3b0d61a03aaf9"),
}


def _route(station: str):
    return next(r for r in load_physical_current_sources()[0]
                if r.provider == "noaa_wrh" and r.station_id == station)


def _batch() -> dict:
    return json.loads(FIXTURE.read_text())


def _station_body(station: str) -> bytes:
    batch = _batch()
    return json.dumps({"UNITS": batch["UNITS"],
                       "STATION": [s for s in batch["STATION"] if s["STID"] == station]}).encode()


def _parse(station: str):
    return adapters.parse_station_payload(_route(station), _station_body(station), received_at=RECEIPT,
                                          source_response_sha256="f" * 64)


def _digest(prints) -> str:
    h = hashlib.sha256()
    for p in prints:
        h.update(json.dumps([p.observed_at.isoformat(), p.fetched_at.isoformat(), p.value_native,
                             p.unit, p.raw_report]).encode() + b"\n")
    return h.hexdigest()


# --- parser split ------------------------------------------------------------------------------

@pytest.mark.parametrize("station", sorted(PAGE_DIGESTS))
def test_page_prints_are_byte_identical_to_the_pre_asos5_parser(station):
    prints = _parse(station)
    assert (len(prints), _digest(prints)) == PAGE_DIGESTS[station]
    assert all(json.loads(p.raw_report)["source_channel"] == f"noaa_wrh_{station.lower()}" for p in prints)


@pytest.mark.parametrize("station", sorted(PAGE_DIGESTS))
def test_asos5_rows_are_exactly_the_off_report_five_minute_rows(station):
    from src.data.noaa_wrh_timeseries import rows_from_payload

    rows = rows_from_payload(json.loads(_station_body(station)), station)
    expected = [(r.utc, r.air_temp) for r in rows if not r.is_official_report and r.utc.minute % 5 == 0]
    asos5 = _parse(station).asos5
    assert [(s.observed_at, s.value_native) for s in asos5] == sorted(expected)
    assert expected and all(s.unit == "F" and s.fetched_at == RECEIPT for s in asos5)
    page_clocks = {p.observed_at for p in _parse(station)}
    # A SPECI that sits on the 5-minute grid stays a page print; the channels never share a clock.
    assert page_clocks.isdisjoint(s.observed_at for s in asos5)
    for sample in asos5:
        record = json.loads(sample.raw_report)
        assert record["source_channel"] == f"asos5_{station.lower()}"
        assert record["station_id"] == record["provider_station"] == station
        assert record["value_native"] == sample.value_native  # served value, no reconversion
        # Whole degC served in degF (80.6 = 27 C): the 5-minute record's own precision.
        assert abs((sample.value_native - 32) / 1.8 - round((sample.value_native - 32) / 1.8)) < 1e-9
        assert "station_reference" not in record


def test_kdal_speci_on_the_grid_stays_on_the_page_channel():
    prints = _parse("KDAL")
    clocks = {p.observed_at for p in prints}
    for local in ("2026-10-02T13:40:00+00:00", "2026-10-04T12:45:00+00:00"):
        assert datetime.fromisoformat(local) in clocks
    assert not any(s.observed_at in clocks for s in prints.asos5)


def test_metric_all_view_routes_emit_no_asos5():
    body = (Path(__file__).parents[1] / "fixtures" / "station_temperature" /
            "wrh_metric_batch_eddm_rjtt.json").read_bytes()
    batch = json.loads(body)
    for station in ("EDDM", "RJTT"):
        route = _route(station)
        assert route.identity["resolver_view"] == "all"
        one = {"UNITS": batch["UNITS"], "STATION": [s for s in batch["STATION"] if s["STID"] == station]}
        prints = adapters.parse_station_payload(route, json.dumps(one).encode(), received_at=RECEIPT)
        assert prints and prints.asos5 == ()


def test_asos5_validates_station_identity_with_the_page_route():
    from src.data.noaa_wrh_timeseries import WrhStationIdentityInvalid

    batch = _batch()
    one = {"UNITS": batch["UNITS"], "STATION": [s for s in batch["STATION"] if s["STID"] == "KDAL"]}
    with pytest.raises(WrhStationIdentityInvalid):
        adapters.parse_station_payload(_route("KSEA"), json.dumps(one).encode(), received_at=RECEIPT)


def test_batch_fetch_returns_page_prints_unchanged_and_asos5_from_the_same_receipt():
    import httpx
    from src.data import noaa_wrh_timeseries as wrh

    batch = _batch()
    body = json.dumps(batch).encode()
    route = _route("KDAL")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body)

    original = wrh.fetch_wrh_token
    wrh.fetch_wrh_token = lambda: "private-fixture-token"
    adapters._WRH_BATCH_CACHE.clear()
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC)
            got = adapters.fetch_station_temperature(route, start=start, end=end, client=client)
            again = adapters.fetch_station_temperature(_route("KSEA"), start=start, end=end, client=client)
        _, received = adapters._fetch_wrh_batch(route, client)
    finally:
        wrh.fetch_wrh_token = original
        adapters._WRH_BATCH_CACHE.clear()
    assert len(calls) == 1, "asos5 adds no HTTP request: one batch serves both stations and channels"
    assert type(got) is adapters.WrhPrints and got.asos5 and again.asos5
    assert {s.fetched_at for s in got} == {s.fetched_at for s in got.asos5} == {received}
    one = {"UNITS": batch["UNITS"], "STATION": [s for s in batch["STATION"] if s["STID"] == "KDAL"]}
    parsed = adapters.parse_station_payload(route, json.dumps(one).encode(), received_at=received,
                                            source_response_sha256=hashlib.sha256(body).hexdigest())
    assert _digest(got) == _digest(parsed)
    assert "private-fixture-token" not in "".join(s.raw_report for s in (*got, *got.asos5))


# --- tick: write, G10, savepoint, trace ---------------------------------------------------------

class _Lease:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def record_commit(self, **_):
        pass


def _tick(monkeypatch, path, prints, *, city_name="Dallas", station="KDAL"):
    from src.config import cities_by_name
    from src.data import replacement_forecast_production as production
    from src.state import db, write_coordinator as coordinator
    import src.ingest_main as ingest

    monkeypatch.setattr(adapters, "fetch_station_temperature", lambda *a, **k: prints)
    monkeypatch.setattr(db, "world_write_mutex", lambda: threading.Lock())
    monkeypatch.setattr(db, "get_world_connection", lambda **kw: sqlite3.connect(path))
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator",
                        lambda: SimpleNamespace(lease=lambda *a, **k: _Lease()))
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: {})
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed",
                        lambda cfg, **kw: {"status": "FUSION_UPGRADE_TRIGGER"})
    monkeypatch.setattr("src.data.physical_current_delivery.current_temperature_priority_families", lambda: {})
    monkeypatch.setattr(ingest, "_physical_current_pending_wakes", set())
    return ingest._day0_current_temperature_source_tick(cities_by_name[city_name], _route(station))


def _world(tmp_path) -> Path:
    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        ensure_revocations(conn)
    return path


def _recent(station="KDAL"):
    """The fixture's prints moved to now, so the tick's causal window keeps them."""
    now = datetime.now(UTC).replace(microsecond=0)
    prints = _parse(station)
    shift = now - max(s.observed_at for s in (*prints, *prints.asos5)) - timedelta(minutes=1)

    def move(s):
        return adapters.StationTemperaturePrint(s.observed_at + shift, now, s.value_native, s.unit, s.raw_report)

    return adapters.WrhPrints((move(s) for s in prints), (move(s) for s in prints.asos5))


def _rows(path, channel):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT publish_ts_utc, value_native, unit, fetched_at_utc, raw_report "
                            "FROM observation_prints WHERE source_channel = ? ORDER BY publish_ts_utc",
                            (channel,)).fetchall()


def test_tick_writes_asos5_without_touching_page_counts_or_traces(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="zeus.observation_reaction")
    prints = _recent()
    path = _world(tmp_path)
    result = _tick(monkeypatch, path, prints)
    assert result["status"] == "COMMITTED" and result["inserted"] == len(prints)
    page = _rows(path, "noaa_wrh_kdal")
    asos5 = _rows(path, "asos5_kdal")
    assert [(r[0], r[1], r[4]) for r in page] == [
        (s.observed_at.isoformat(), s.value_native, s.raw_report) for s in prints]
    assert [(r[0], r[1], r[2], r[3]) for r in asos5] == [
        (s.observed_at.isoformat(), s.value_native, "F", s.fetched_at.isoformat()) for s in prints.asos5]
    commits = [json.loads(r.getMessage().split(" ", 1)[1]) for r in caplog.records
               if r.getMessage().startswith("OBSERVATION_REACTION_TRACE ")]
    commits = [c for c in commits if c.get("stage") == "SOURCE_COMMITTED"]
    assert len(commits) == len(prints)
    assert {c["source_channel"] for c in commits} == {"noaa_wrh_kdal"}
    assert result["clock_trace"]["source_channel"] == "noaa_wrh_kdal"
    # A re-poll of the same batch suppresses every repeat in both channels.
    assert _tick(monkeypatch, path, prints)["inserted"] == 0
    assert len(_rows(path, "asos5_kdal")) == len(asos5)


def test_asos_only_round_writes_its_rows_and_skips_page_only_work(monkeypatch, tmp_path, caplog):
    """A page window with no official row (boot, a late hourly) still carries 5-minute
    samples; the next tick's window has moved past the oldest, so they are written now.
    The page-only steps (G10 absence, page trace, page wake) have nothing to act on."""
    caplog.set_level(logging.INFO)
    recent = _recent()
    asos_only = adapters.WrhPrints((), recent.asos5)
    path = _world(tmp_path)
    result = _tick(monkeypatch, path, asos_only)
    assert result == {"status": "COMMITTED", "inserted": 0, "advanced": False, "clock_trace": None}
    assert [(r[0], r[1]) for r in _rows(path, "asos5_kdal")] == [
        (s.observed_at.isoformat(), s.value_native) for s in recent.asos5]
    assert _rows(path, "noaa_wrh_kdal") == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fact_revocations").fetchone()[0] == 0
    assert not [r for r in caplog.records if r.getMessage().startswith(
        ("OBSERVATION_REACTION_TRACE ", "PHYSICAL_CURRENT_CHAIN_TRACE", "PHYSICAL_CURRENT_REDECISION_SEED"))]
    # The later page-bearing round re-carries the overlap; dedup keeps one row per clock.
    assert _tick(monkeypatch, path, recent)["status"] == "COMMITTED"
    assert len(_rows(path, "asos5_kdal")) == len(recent.asos5)


def test_empty_round_with_neither_channel_writes_nothing(monkeypatch, tmp_path):
    path = _world(tmp_path)
    assert _tick(monkeypatch, path, adapters.WrhPrints((), ())) == {"status": "NO_NEW_PRINT"}
    assert _rows(path, "asos5_kdal") == [] and _rows(path, "noaa_wrh_kdal") == []


def test_g10_absence_judges_page_clocks_only(monkeypatch, tmp_path):
    prints = _recent()
    path = _world(tmp_path)
    _tick(monkeypatch, path, prints)
    # The page omits nothing: asos5 clocks lying inside its span are never "absent page prints".
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fact_revocations").fetchone()[0] == 0
    calls = []
    import src.state.fact_revocation as revocation
    real = revocation.record_page_print_absences

    def spy(conn, **kw):
        calls.append(kw)
        return real(conn, **kw)

    monkeypatch.setattr(revocation, "record_page_print_absences", spy)
    _tick(monkeypatch, path, prints)
    assert calls and calls[0]["source_channel"] == "noaa_wrh_kdal"
    assert calls[0]["returned_clocks"] == [s.observed_at.isoformat() for s in prints]


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("disk I/O error"), ValueError("bad clock")])
def test_asos5_failure_never_costs_the_page_prints(monkeypatch, tmp_path, failure):
    from src.state.schema import observation_prints_schema as schema

    prints = _recent()
    path = _world(tmp_path)
    real = schema.append_print
    attempts = []

    def failing(conn, **kw):
        if kw["source_channel"].startswith("asos5_"):
            attempts.append(kw)
            if len(attempts) == 3:  # two asos5 rows already written inside the savepoint
                raise failure
        return real(conn, **kw)

    monkeypatch.setattr(schema, "append_print", failing)
    result = _tick(monkeypatch, path, prints)
    assert len(attempts) == 3, "the asos5 write must have been attempted"
    assert result["status"] == "COMMITTED" and result["inserted"] == len(prints)
    assert len(_rows(path, "noaa_wrh_kdal")) == len(prints)
    assert _rows(path, "asos5_kdal") == [], "the savepoint rollback must undo the partial asos5 write"


# --- reader isolation ----------------------------------------------------------------------------

def _reader_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _read_all(path, *, city, target, decision):
    from src.config import cities_by_name
    from src.data.day0_fast_obs import FAST_OBS_SOURCE_ID, build_fast_station_residual_likelihood
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.day0_oracle_anomaly import _page_running_extremes_from_ledger
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    conn = _reader_conn(path)
    try:
        out = {}
        for metric in ("high", "low"):
            for settle in (True, False):
                out[("fact", metric, settle)] = _latest_authorized_day0_fact(
                    conn, city=city, target_date=target, temperature_metric=metric,
                    decision_time=decision, require_settlement_channel=settle)
            out[("residual", metric)] = build_fast_station_residual_likelihood(
                conn, city=city, target_date=target, metric=metric, observed_source=FAST_OBS_SOURCE_ID,
                observation_time=decision, decision_time=decision)
        state = read_day0_current_temperature_state(conn=conn, city=cities_by_name[city],
                                                    target_date=target, decision_time=decision)
        out["state"] = None if state is None else (state, state.identity())
        out["oracle"] = _page_running_extremes_from_ledger(cities_by_name[city], target, conn=conn)
        return out
    finally:
        conn.close()


def _seed_page_and_awc(path, *, city, station, decision, days):
    """Page prints and AWC METAR at the same 20 hourly :53 clocks per day, so the residual pairs."""
    awc = []
    with sqlite3.connect(path) as conn:
        for day in range(days):
            for hour in range(20):
                at = (decision - timedelta(days=day, hours=hour)).replace(minute=53, second=0, microsecond=0)
                if at > decision - timedelta(minutes=10):
                    continue
                temp_c = 20.0 + (hour % 5)
                append_print(conn, city=city, station_id=station, source_channel=f"noaa_wrh_{station.lower()}",
                             publish_ts_utc=at.isoformat(), value_native=round(temp_c * 1.8 + 32 + 0.18, 2),
                             unit="F", fetched_at_utc=(at + timedelta(minutes=4)).isoformat(),
                             raw_report=f"{station} {at:%d%H%M}Z 00000KT 10SM CLR {int(temp_c):02d}/10 A3000 "
                                        f"RMK AO2 T0{int(temp_c * 10 + 1):03d}0100")
                awc.append((at, temp_c))
                append_print(conn, city=city, station_id=station, source_channel="aviationweather_metar",
                             publish_ts_utc=at.isoformat(), value_native=temp_c, unit="C",
                             fetched_at_utc=(at + timedelta(minutes=2)).isoformat(),
                             raw_report=f"METAR {station} {at:%d%H%M}Z 00000KT 10SM CLR {int(temp_c):02d}/10 "
                                        f"A3000 RMK AO2 T0{int(temp_c * 10 + 1):03d}0100")
    return awc


def test_no_reader_admits_asos5_rows(monkeypatch, tmp_path):
    from zoneinfo import ZoneInfo
    from src.config import cities_by_name

    city, station = "Dallas", "KDAL"
    decision = datetime.now(UTC).replace(second=0, microsecond=0)
    target = decision.astimezone(ZoneInfo(cities_by_name[city].timezone)).date().isoformat()
    path = _world(tmp_path)
    paired = _seed_page_and_awc(path, city=city, station=station, decision=decision, days=3)
    before = _read_all(path, city=city, target=target, decision=decision)
    assert before[("fact", "high", True)] is not None and before["state"] is not None
    assert before[("residual", "high")] is not None and before["oracle"] is not None

    # asos5 rows that would move every extreme, the latest clock and every residual pair if any
    # reader admitted them: the 5-minute grid of the day, plus each paired page/METAR clock.
    grid = []
    for minutes in range(5, 24 * 60, 5):
        at = decision - timedelta(minutes=minutes)
        grid.append(at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0))
    with sqlite3.connect(path) as conn:
        for at in grid + [clock for clock, _ in paired]:
            for value in (140.0, -40.0):
                append_print(conn, city=city, station_id=station, source_channel="asos5_kdal",
                             publish_ts_utc=at.isoformat(), value_native=value, unit="F",
                             fetched_at_utc=(at + timedelta(seconds=30)).isoformat(),
                             raw_report=json.dumps({"source_channel": "asos5_kdal", "station_id": station}))
        assert conn.execute("SELECT COUNT(*) FROM observation_prints WHERE source_channel='asos5_kdal'"
                            ).fetchone()[0] > 500
    after = _read_all(path, city=city, target=target, decision=decision)
    assert after == before
