# Created: 2026-09-27
# Last reused or audited: 2026-10-01
# Authority basis: official FMI WFS station/metadata response and EFHK Day0 current-state defect.
"""FMI EFHK current-state causality and authority antibodies."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.data.day0_hourly_vectors import read_day0_current_temperature_state
from src.data.fmi_airport_temperature import (
    SOURCE_CHANNEL, fetch_efhk_temperature, parse_temperature_coverage,
    parse_temperature_metadata,
)
from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
from src.state.schema.observation_prints_schema import append_print, ensure_table


UTC = timezone.utc
STAMPS = [datetime(2026, 9, 27, 11, minute, tzinfo=UTC) for minute in (40, 50)]
STAMPS += [datetime(2026, 9, 27, 12, minute, tzinfo=UTC) for minute in (10, 20)]
CITY = SimpleNamespace(name="Helsinki", timezone="Europe/Helsinki", wu_station="EFHK",
                       settlement_source_type="noaa", settlement_unit="C")
META = '<ObservableProperty xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="temperature"><uom uom="degC"/></ObservableProperty>'


def coverage(*, station="100968", wmo="2974", property_name="temperature",
             values=("15.5", "15.7", "15.8", "15.6"),
             positions=None) -> str:
    triples = positions or " ".join(
        f"60.32937 24.97274 {int(stamp.timestamp())}" for stamp in STAMPS
    )
    return f'''<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
        xmlns:om="http://www.opengis.net/om/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
        xmlns:gmlcov="http://www.opengis.net/gmlcov/1.0" xmlns:swe="http://www.opengis.net/swe/2.0"
        xmlns:target="http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1"
        xmlns:xlink="http://www.w3.org/1999/xlink">
      <om:observedProperty xlink:href="https://opendata.fmi.fi/meta?observableProperty=observation&amp;param=temperature&amp;language=eng"/>
      <target:Location><gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/fmisid">{station}</gml:identifier>
        <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">Vantaa Helsinki-Vantaan lentoasema</gml:name>
        <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/wmo">{wmo}</gml:name></target:Location>
      <om:result><gmlcov:MultiPointCoverage><gmlcov:positions>{triples}</gmlcov:positions>
        <gml:doubleOrNilReasonTupleList>{' '.join(values)}</gml:doubleOrNilReasonTupleList>
        <swe:field name="{property_name}" xlink:href="https://opendata.fmi.fi/meta?observableProperty=observation&amp;param=temperature&amp;language=eng"/>
      </gmlcov:MultiPointCoverage></om:result></wfs:FeatureCollection>'''


def test_official_efhk_grid_and_metadata_preserve_tenths():
    parse_temperature_metadata(META)
    prints = parse_temperature_coverage(
        coverage(), fetched_at=datetime(2026, 9, 27, 12, 25, tzinfo=UTC),
    )
    assert [(p.observed_at, p.temperature_c) for p in prints] == list(zip(
        STAMPS, (15.5, 15.7, 15.8, 15.6), strict=True,
    ))
    assert all(p.fetched_at == datetime(2026, 9, 27, 12, 25, tzinfo=UTC) for p in prints)
    assert all('"availability":"local_fetch_only"' in p.raw_report for p in prints)


@pytest.mark.parametrize("xml", [
    coverage(station="100969"), coverage(wmo="2975"),
    coverage(property_name="dewpoint"),
    coverage(positions="60.333 24.97274 1790510000"),
])
def test_wrong_station_or_parameter_rejected(xml):
    with pytest.raises(ValueError):
        parse_temperature_coverage(xml, fetched_at=datetime(2026, 9, 27, 12, 30, tzinfo=UTC))


def test_unit_nan_and_future_samples():
    with pytest.raises(ValueError, match="UNIT"):
        parse_temperature_metadata(META.replace("degC", "degF"))
    prints = parse_temperature_coverage(
        coverage(values=("15.5", "NaN", "15.8", "15.6")),
        fetched_at=datetime(2026, 9, 27, 12, 15, tzinfo=UTC),
    )
    assert [p.temperature_c for p in prints] == [15.5, 15.8]


def test_fetch_uses_real_response_completion_not_requested_end(monkeypatch):
    import src.data.fmi_airport_temperature as fmi

    class Response:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass

    class Client:
        def get(self, url, **kwargs):
            return Response(META if "meta?" in url else coverage())

    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime(2026, 9, 27, 12, 30, tzinfo=tz)

    monkeypatch.setattr(fmi, "datetime", Clock)
    prints = fetch_efhk_temperature(
        start=datetime(2026, 9, 27, 11, 30, tzinfo=UTC),
        end=datetime(2026, 9, 27, 12, 30, tzinfo=UTC), client=Client(),
    )
    assert prints[-1].fetched_at == datetime(2026, 9, 27, 12, 30, tzinfo=UTC)


def test_current_state_causal_precise_and_not_absorbing(monkeypatch):
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    parsed = parse_temperature_coverage(
        coverage(), fetched_at=datetime(2026, 9, 27, 12, 24, tzinfo=UTC),
    )
    for p in parsed:
        append_print(conn, city="Helsinki", station_id="EFHK",
                     source_channel=SOURCE_CHANNEL, publish_ts_utc=p.observed_at.isoformat(),
                     value_native=p.temperature_c, unit="C",
                     fetched_at_utc=p.fetched_at.isoformat(), raw_report=p.raw_report)
    # The older coarse METAR must not roll back a later precise print.
    append_print(conn, city="Helsinki", station_id="EFHK", source_channel="aviationweather_metar",
                 publish_ts_utc=datetime(2026, 9, 27, 12, 26, tzinfo=UTC).isoformat(),
                 value_native=16, unit="C", fetched_at_utc=datetime(2026, 9, 27, 12, 26, tzinfo=UTC).isoformat(),
                 raw_report="METAR EFHK 271150Z 00000KT CAVOK 16/10 Q1013")
    def read(at):
        return read_day0_current_temperature_state(
            conn=conn, city=CITY, target_date="2026-09-27", decision_time=at,
        )
    assert read(datetime(2026, 9, 27, 11, 47, tzinfo=UTC)) is None
    assert read(datetime(2026, 9, 27, 12, 25, tzinfo=UTC)).value_native == 15.6
    assert read(datetime(2026, 9, 27, 12, 27, tzinfo=UTC)).source == SOURCE_CHANNEL
    assert read(datetime(2026, 9, 27, 12, 55, tzinfo=UTC)).source == "aviationweather_metar"
    fact = _latest_authorized_day0_fact(
        conn, city="Helsinki", target_date="2026-09-27",
        temperature_metric="high", decision_time=datetime(2026, 9, 27, 12, 27, tzinfo=UTC),
    )
    assert fact is not None and fact["observation_source"] == "aviationweather_metar"
    assert _latest_authorized_day0_fact(
        conn, city="Helsinki", target_date="2026-09-27", temperature_metric="high",
        decision_time=datetime(2026, 9, 27, 12, 27, tzinfo=UTC),
        require_settlement_channel=True,
    ) is None
    conn.close()


def test_late_first_fetch_of_old_sample_falls_back_to_legal_source():
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    old = parse_temperature_coverage(
        coverage(), fetched_at=datetime(2026, 9, 27, 13, 2, tzinfo=UTC),
    )[-1]
    append_print(conn, city="Helsinki", station_id="EFHK", source_channel=SOURCE_CHANNEL,
                 publish_ts_utc=old.observed_at.isoformat(), value_native=old.temperature_c,
                 unit="C", fetched_at_utc=old.fetched_at.isoformat(), raw_report=old.raw_report)
    append_print(conn, city="Helsinki", station_id="EFHK", source_channel="aviationweather_metar",
                 publish_ts_utc=datetime(2026, 9, 27, 12, 55, tzinfo=UTC).isoformat(),
                 value_native=16, unit="C",
                 fetched_at_utc=datetime(2026, 9, 27, 12, 56, tzinfo=UTC).isoformat(),
                 raw_report="METAR EFHK 271250Z 00000KT CAVOK 16/10 Q1013")
    state = read_day0_current_temperature_state(
        conn=conn, city=CITY, target_date="2026-09-27",
        decision_time=datetime(2026, 9, 27, 13, 3, tzinfo=UTC),
    )
    assert state is not None and state.source == "aviationweather_metar"
    conn.close()


def _drain_reseed_worker(ingest):
    worker = ingest._physical_current_reseed_thread
    if worker is not None:
        worker.join(10)
        assert not worker.is_alive()


@pytest.mark.parametrize("retry_wake", [False, True])
def test_new_print_uses_world_coordinator_and_wakes_only_helsinki(monkeypatch, tmp_path, retry_wake):
    import src.ingest_main as ingest
    import src.data.fmi_airport_temperature as fmi
    import src.data.replacement_forecast_production as production
    import src.state.db as db
    import src.state.write_coordinator as coordinator

    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
    now = datetime.now(UTC)
    # The fixture is derived from the official structure but uses a current
    # sample time so this wake exercises the active Day0 target date.
    sample = fmi.FmiTemperaturePrint(
        observed_at=now - timedelta(minutes=10), fetched_at=now,
        temperature_c=15.8, raw_report="sample-validated-by-client",
    )
    lease_calls = []
    wake_calls = []

    class Lease:
        def __enter__(self):
            lease_calls.append("entered")
            return self
        def __exit__(self, *_): return False
        def record_commit(self, **kwargs): lease_calls.append(kwargs["rows_changed"])

    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    # Empty HIGH daytime mask must not disable physical-current observations.
    monkeypatch.setattr(ingest, "_active_window_cities", lambda _: [])
    monkeypatch.setattr(ingest, "_physical_current_pending_wakes", {})
    monkeypatch.setattr(fmi, "fetch_temperature", lambda **_: (sample,))
    monkeypatch.setattr(db, "world_write_mutex", lambda: threading.Lock())
    monkeypatch.setattr(db, "get_world_connection", lambda **_: sqlite3.connect(path))
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator",
                        lambda: SimpleNamespace(lease=lambda *_args, **_kwargs: Lease()))
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config",
                        lambda: {"seed_dir": str(tmp_path)})
    def enqueue(_cfg, **kwargs):
        # A separate connection sees the print only after WORLD commit.
        with sqlite3.connect(path) as check:
            assert check.execute("SELECT count(*) FROM observation_prints").fetchone()[0] == 1
        wake_calls.append(kwargs)
        return {"status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED" if retry_wake and len(wake_calls)==1
                else "FUSION_UPGRADE_TRIGGER"}
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", enqueue)

    report = ingest._day0_fmi_temperature_tick()
    _drain_reseed_worker(ingest)
    assert {key: report[key] for key in ("status", "inserted", "advanced")} == {"status": "COMMITTED", "inserted": 1, "advanced": True}
    trace = report["clock_trace"]
    assert trace["provider_observed_at_ms"] == int(sample.observed_at.timestamp()*1000)
    assert trace["response_received_at_ms"] == int(sample.fetched_at.timestamp()*1000)
    assert trace["provider_published_at_ms"] is None
    assert trace["source_http_ms"] >= 0 and trace["receipt_to_world_ms"] >= 0
    assert "world_to_enqueue_return_ms" not in trace
    assert trace["enqueue_status"] == "DEFERRED_TO_RESEED_WORKER"
    assert "q_served_at_ms" not in trace and "venue_ack_at_ms" not in trace
    assert trace["completion_trace"] == "OBSERVATION_REACTION_TRACE"
    assert trace["input_identity"] == {
        "source": SOURCE_CHANNEL,
        "observed_at_utc": sample.observed_at.isoformat(),
        "value_native": sample.temperature_c,
    }
    assert lease_calls == ["entered", 1]
    assert len(wake_calls) == 1
    local_day = now.astimezone(ZoneInfo("Europe/Helsinki")).date().isoformat()
    assert wake_calls[0]["scopes"] == (("Helsinki", local_day, "high"),
                                       ("Helsinki", local_day, "low"))
    assert wake_calls[0]["changed_sources"] == ("day0_current_temperature_state",)
    assert wake_calls[0]["computed_at"] >= sample.fetched_at
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT source_channel, value_native, fetched_at_utc FROM observation_prints").fetchone()
        assert row == (SOURCE_CHANNEL, 15.8, now.isoformat())
    assert ingest._day0_fmi_temperature_tick()["advanced"] is False
    _drain_reseed_worker(ingest)
    assert len(wake_calls) == (2 if retry_wake else 1)
    assert not ingest._physical_current_pending_wakes
    ingest._day0_fmi_temperature_tick()
    _drain_reseed_worker(ingest)
    assert len(wake_calls) == (2 if retry_wake else 1)


def test_fmi_transport_failure_leaves_world_unchanged(monkeypatch):
    import src.ingest_main as ingest
    import src.data.fmi_airport_temperature as fmi

    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    monkeypatch.setattr(ingest, "_active_window_cities", lambda _: ["Helsinki"])
    def fail(**_):
        raise ValueError("bad WFS shape")
    monkeypatch.setattr(fmi, "fetch_temperature", fail)
    assert ingest._day0_fmi_temperature_tick() == {"status": "SOURCE_UNAVAILABLE"}


def test_same_cycle_queue_coverage_waits_for_consumed_current_state(monkeypatch, tmp_path):
    import json
    from src.data import replacement_forecast_live_materialization_queue as queue

    forecast_db = tmp_path / "forecast.sqlite"
    revision = {"source": SOURCE_CHANNEL, "observed_at_utc": "2026-09-27T12:20:00+00:00",
                "value_native": 15.6}
    seed = {
        "city": "Helsinki", "target_date": "2026-09-27", "temperature_metric": "high",
        "computed_at": "2026-09-27T12:25:00+00:00", "baseline_source_run_id": "baseline",
        "openmeteo_source_run_id": "anchor", "day0_current_temperature_state": revision,
    }
    with sqlite3.connect(forecast_db) as conn:
        conn.executescript("""
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY, source_id TEXT, runtime_layer TEXT,
                city TEXT, target_date TEXT, temperature_metric TEXT,
                dependency_source_run_ids_json TEXT, source_cycle_time TEXT,
                computed_at TEXT, provenance_json TEXT, openmeteo_anchor_id INTEGER
            );
            CREATE TABLE readiness_state (
                strategy_key TEXT, status TEXT, provenance_json TEXT,
                dependency_json TEXT
            );
        """)
        conn.execute("INSERT INTO forecast_posteriors "
                     "(posterior_id, source_id, runtime_layer, city, target_date, "
                     "temperature_metric, dependency_source_run_ids_json, source_cycle_time, "
                     "computed_at, provenance_json) VALUES (1, ?, 'live', 'Helsinki', "
                     "'2026-09-27', 'high', ?, '2026-09-27T09:00:00+00:00', "
                     "'2026-09-27T12:26:00+00:00', ?)",
                     (queue.SOURCE_ID,
                      json.dumps({"baseline_b0": "baseline", "openmeteo_ifs9_anchor": "anchor"}),
                      json.dumps({"day0_remaining_carrier_content_identity": "old"})))
        conn.execute("INSERT INTO readiness_state VALUES (?, 'READY', ?, ?)",
                     (queue.STRATEGY_KEY,
                      json.dumps({"city": "Helsinki", "target_date": "2026-09-27",
                                  "temperature_metric": "high"}),
                      json.dumps({"dependencies": [
                          {"role": "baseline_b0", "source_run_id": "baseline"},
                          {"role": "openmeteo_ifs9_anchor", "source_run_id": "anchor"},
                      ]})))
    monkeypatch.setattr(queue, "tradeable_grade_coverage_sql", lambda **_: "AND 1=1")
    monkeypatch.setattr(queue, "replacement_input_refresh_reason", lambda *_args, **_kwargs: None)
    assert queue._seed_already_covered(forecast_db=forecast_db, seed=seed) is False
    assert queue._seed_already_covered(
        forecast_db=forecast_db,
        seed={**seed, "day0_current_temperature_state": {**revision, "source": "wu_icao_history"}},
    ) is False
    with sqlite3.connect(forecast_db) as conn:
        conn.execute("UPDATE forecast_posteriors SET provenance_json = ?",
                     (json.dumps({"day0_current_temperature_state": revision,
                                  "day0_remaining_carrier_content_identity": "new"}),))
    assert queue._seed_already_covered(forecast_db=forecast_db, seed=seed) is True
    newer = {**revision, "observed_at_utc": "2026-09-27T12:30:00+00:00",
             "value_native": 15.5}
    with sqlite3.connect(forecast_db) as conn:
        conn.execute("UPDATE forecast_posteriors SET provenance_json = ?, computed_at = ?",
                     (json.dumps({"day0_current_temperature_state": newer,
                                  "day0_remaining_carrier_content_identity": "newer"}),
                      "2026-09-27T12:36:00+00:00"))
    # A later actual physical state consumes the old seed's obligation; the
    # old identity must not be claimed as consumed, nor kept as immortal debt.
    assert queue._seed_already_covered(forecast_db=forecast_db, seed=seed) is True
    assert queue._seed_already_covered(
        forecast_db=forecast_db,
        seed={**seed, "computed_at": "2026-09-27T12:35:00+00:00",
              "day0_current_temperature_state": newer},
    ) is True
    with sqlite3.connect(forecast_db) as conn:
        cross_source = {**newer, "source": "aviationweather_metar"}
        conn.execute("UPDATE forecast_posteriors SET provenance_json = ?",
                     (json.dumps({"day0_current_temperature_state": cross_source,
                                  "day0_remaining_carrier_content_identity": "newer"}),))
    assert queue._seed_already_covered(forecast_db=forecast_db, seed=seed) is True
    for bad in (
        {**revision, "value_native": 15.5},  # equal-clock correction lacks ordering proof
        {**newer, "observed_at_utc": "2026-09-27T12:40:00+00:00"},  # future of posterior
        {**newer, "value_native": float("nan")},
        {**newer, "source": "wu_icao_history"},  # wrong authority class
    ):
        with sqlite3.connect(forecast_db) as conn:
            conn.execute("UPDATE forecast_posteriors SET provenance_json = ?",
                         (json.dumps({"day0_current_temperature_state": bad,
                                      "day0_remaining_carrier_content_identity": "newer"}),))
        assert queue._seed_already_covered(forecast_db=forecast_db, seed=seed) is False
    with sqlite3.connect(forecast_db) as conn:
        older = {**revision, "observed_at_utc": "2026-09-27T12:10:00+00:00"}
        conn.execute("UPDATE forecast_posteriors SET provenance_json = ?",
                     (json.dumps({"day0_current_temperature_state": older,
                                  "day0_remaining_carrier_content_identity": "newer"}),))
    assert queue._seed_already_covered(forecast_db=forecast_db, seed=seed) is False
    assert queue._request_semantic_key({**seed, "source_cycle_time": "2026-09-27T09:00:00Z"}) != queue._request_semantic_key({
        **seed, "source_cycle_time": "2026-09-27T09:00:00Z",
        "day0_current_temperature_state": {**revision, "value_native": 15.5},
    })
