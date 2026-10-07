# Lifecycle: created=2026-09-29; last_reviewed=2026-10-06; last_reused=2026-10-06
# Purpose: Protect causal physical revision delivery and restart-safe held wakes.
# Reuse: Run when physical current publication, source clocks or held wake dispatch changes.
# Created: 2026-09-29
# Last reused/audited: 2026-10-06 (offline physical revision delivery and causal availability)
# Authority: REQ-20260929-223929-bf51a2; K1/INV-37 and causal source revision delivery.
"""Source revision delivery is not conditional on an incumbent carrier."""
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace
import pytest
from src.data import replacement_fusion_upgrade_trigger as fusion
UTC=timezone.utc

@pytest.fixture(autouse=True)
def _readable_unclaimed_wrh_owner(monkeypatch):
    """Legacy print-only fixtures have a readable empty FORECAST truth owner.

    Unknown/unreadable current-product authority is no longer equivalent to
    proven absence. Supply real empty owner DDL rather than bypass that reader.
    """
    from contextlib import contextmanager
    from src.state import db
    @contextmanager
    def owner(**kwargs):
        conn = sqlite3.connect(":memory:")
        db._create_observations(conn)
        try:
            yield conn
        finally:
            conn.close()
    monkeypatch.setattr(db, "get_forecasts_connection_with_world_read_only", owner)


@pytest.mark.parametrize("incumbent,carrier", [(False,False),(True,False),(True,True)])
@pytest.mark.parametrize("target", ["2026-09-29","2026-09-28"])
def test_current_revision_reaches_every_incumbent_shape(monkeypatch,incumbent,carrier,target):
    now=datetime(2026,9,29,12,tzinfo=UTC)
    cycle="2026-09-29T06:00:00+00:00"
    current={"source":"fmi_airport_temperature","observed_at_utc":target+"T09:20:00+00:00","value_native":15.6}
    consumed=[None]
    monkeypatch.setattr(fusion,"_latest_posterior_inputs",lambda *_a,**_k:(
        cycle if incumbent else None,frozenset(),{},frozenset(),frozenset(),None,(),False,False,consumed[0],carrier,False,{},frozenset()))
    monkeypatch.setattr(fusion,"_capturable_current_temperature_state",lambda **_:current)
    monkeypatch.setattr(fusion,"_capturable_inputs_for_scope",lambda *_a,**_k:{})
    monkeypatch.setattr("src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",lambda *_a,**_k:datetime.fromisoformat(cycle))
    conn=sqlite3.connect(":memory:")
    verdict=fusion.scope_capture_offers_larger_provider_set(conn,city="Helsinki",target_date=target,metric="high",decision_time=now,changed_sources=("day0_current_temperature_state",))
    assert verdict["is_upgrade"]
    assert verdict["source_cycle_time"]==cycle
    assert verdict["changed_input_revisions"]["day0_current_temperature_state"]==current
    if incumbent:
        consumed[0]=current
        assert not fusion.scope_capture_offers_larger_provider_set(conn,city="Helsinki",target_date=target,metric="high",decision_time=now,changed_sources=("day0_current_temperature_state",))["is_upgrade"]
    conn.close()

def test_missing_ensemble_is_not_a_fabricated_first_posterior(monkeypatch):
    monkeypatch.setattr(fusion,"_latest_posterior_inputs",lambda *_a,**_k:(None,frozenset(),{},frozenset(),frozenset(),None,(),False,False,None,False,False,{},frozenset()))
    monkeypatch.setattr(fusion,"_capturable_current_temperature_state",lambda **_:{"source":"fixture","value_native":15.6})
    monkeypatch.setattr("src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",lambda *_a,**_k:None)
    with sqlite3.connect(":memory:") as conn:
        result=fusion.scope_capture_offers_larger_provider_set(conn,city="Helsinki",target_date="2026-09-29",metric="high",decision_time=datetime(2026,9,29,12,tzinfo=UTC))
    assert not result["is_upgrade"]
    assert result["source_cycle_time"] is None


def test_current_and_ended_held_scopes_survive_calendar_rollover():
    from src.data.physical_current_delivery import current_temperature_delivery_scopes
    city=SimpleNamespace(name="fixture",timezone="Asia/Tokyo")
    held={("fixture","2026-09-28","high"):0,("foreign","2026-09-28","high"):0}
    result=current_temperature_delivery_scopes((city,),now=datetime(2026,9,29,23,tzinfo=UTC),held=held)
    assert set(result)=={("fixture","2026-09-28","high"),("fixture","2026-09-30","high"),("fixture","2026-09-30","low")}


def test_replay_does_not_require_new_http_or_inprocess_wake(monkeypatch):
    from src.data import physical_current_delivery as delivery
    from src.data import replacement_forecast_production as production
    monkeypatch.setattr("src.data.replacement_forecast_seed_discovery.held_position_family_priorities",lambda:{})
    calls=[]
    monkeypatch.setattr(production,"_enqueue_fusion_upgrade_reseeds_if_needed",lambda cfg,**kw:calls.append(kw) or {"status":"FUSION_UPGRADE_TRIGGER"})
    city=SimpleNamespace(name="fixture",timezone="UTC")
    for _ in range(2):
        delivery.reconcile_current_temperature_delivery({},cities=(city,),now=datetime(2026,9,29,12,tzinfo=UTC))
    assert len(calls)==2
    assert calls[0]["scopes"]==calls[1]["scopes"]
    assert calls[1]["changed_sources"]==("day0_current_temperature_state",)


def test_unprojected_ended_day_rest_remains_in_delivery_scope(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.state import db
    trade_path, forecast_path = tmp_path/'trade.db', tmp_path/'forecast.db'
    with sqlite3.connect(trade_path) as trade:
        trade.executescript("""
        CREATE TABLE venue_commands(command_id,position_id,venue_order_id,state,token_id,snapshot_id,intent_kind);
        CREATE TABLE venue_order_facts(venue_order_id,state,remaining_size,local_sequence);
        CREATE TABLE position_current(position_id,city,target_date,temperature_metric,condition_id,phase);
        CREATE TABLE executable_market_snapshots(snapshot_id,condition_id,selected_outcome_token_id,captured_at);
        INSERT INTO venue_commands VALUES('command','not-projected','venue-order','ACKED','token','snapshot','ENTRY');
        INSERT INTO venue_order_facts VALUES('venue-order','LIVE',10,1);
        INSERT INTO executable_market_snapshots VALUES('snapshot','condition','token','2026-09-29T12:00:00Z');
        """)
    with sqlite3.connect(forecast_path) as forecasts:
        forecasts.execute('CREATE TABLE market_events(condition_id,city,target_date,temperature_metric)')
        forecasts.execute("INSERT INTO market_events VALUES('condition','fixture','2026-09-28','low')")
    monkeypatch.setattr('src.data.replacement_forecast_seed_discovery.held_position_family_priorities', lambda: {})
    monkeypatch.setattr(db,'get_trade_connection_read_only', lambda: sqlite3.connect(f'file:{trade_path}?mode=ro',uri=True))
    monkeypatch.setattr(db,'get_forecasts_connection_read_only', lambda: sqlite3.connect(f'file:{forecast_path}?mode=ro',uri=True))
    held=delivery.current_temperature_priority_families()
    assert held == {('fixture','2026-09-28','low'): 1}
    scopes=delivery.current_temperature_delivery_scopes(
        (SimpleNamespace(name='fixture',timezone='UTC'),),
        now=datetime(2026,9,30,12,tzinfo=UTC), held=held,
    )
    assert ('fixture','2026-09-28','low') in scopes
    with sqlite3.connect(trade_path) as trade:
        assert trade.execute('SELECT COUNT(*) FROM position_current').fetchone()[0] == 0
        trade.execute("INSERT INTO venue_order_facts VALUES('venue-order','CANCELED',0,2)")
    assert delivery.current_temperature_priority_families() == {}


def test_rest_scope_failure_preserves_known_held_progress(monkeypatch, caplog):
    from src.data import physical_current_delivery as delivery
    monkeypatch.setattr('src.data.replacement_forecast_seed_discovery.held_position_family_priorities',
                        lambda: {('fixture','2026-09-28','high'): 0})
    def unavailable():
        raise sqlite3.OperationalError('test unavailable')
    monkeypatch.setattr('src.state.db.get_trade_connection_read_only', unavailable)
    assert delivery.current_temperature_priority_families() == {('fixture','2026-09-28','high'): 0}
    assert 'CURRENT_TEMPERATURE_REST_SCOPE_UNAVAILABLE' in caplog.text


def _physical_delivery_fixture(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.state.schema.observation_prints_schema import ensure_table, append_print

    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        for clock, value in (("10:00:00", 37.0), ("11:00:00", 35.0)):
            append_print(conn, city="fixture", station_id="TEST", source_channel="noaa_wrh_test",
                         publish_ts_utc=f"2026-09-29T{clock}+00:00", value_native=value, unit="C",
                         fetched_at_utc=f"2026-09-29T{clock}+00:00", raw_report=clock)
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr("src.state.db.get_world_connection_read_only",
                        lambda **kw: sqlite3.connect(f"file:{path}?mode=ro", uri=True))
    monkeypatch.setattr(delivery, "current_temperature_priority_families",
                        lambda: {("fixture", "2026-09-29", "high"): 0})
    return path, SimpleNamespace(name="fixture", timezone="UTC"), datetime(2026, 9, 29, 12, tzinfo=UTC)


def test_committed_revision_wake_survives_restart_and_nonfrontier_correction(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.runtime.reactor_wake import reactor_wakes_since
    from src.state.schema.observation_prints_schema import append_print
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    scopes = delivery.current_temperature_delivery_scopes((city,), now=now)
    wake_path = tmp_path / "edli-reactor-wake.json"
    first = delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)
    assert first == {"status": "WAKE_RECONCILED", "published": 2}
    queued = reactor_wakes_since(None, path=wake_path)
    assert {family for wake in queued for family in wake.forecast_families} == set(scopes)
    assert all(wake.reason == "current_temperature_print_committed" and not wake.event_ids for wake in queued)
    # No process-local pending set is consulted; a restart reads the persisted
    # publication receipt and does not renew a source receipt or duplicate work.
    delivery._CURSORS[:] = [0, 0]
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 0
    with sqlite3.connect(path) as conn:
        assert append_print(conn, city="fixture", station_id="TEST", source_channel="noaa_wrh_test",
                            publish_ts_utc="2026-09-29T10:00:00+00:00", value_native=34.0, unit="C",
                            fetched_at_utc="2026-09-29T11:30:00+00:00", raw_report="correction")
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT fetched_at_utc FROM observation_prints ORDER BY id").fetchall() == [
            ("2026-09-29T10:00:00+00:00",), ("2026-09-29T11:00:00+00:00",), ("2026-09-29T11:30:00+00:00",),
        ]


@pytest.mark.parametrize("failure_step", ["publish", "record"])
def test_physical_wake_failure_is_retryable_after_restart(monkeypatch, tmp_path, failure_step):
    from src.data import physical_current_delivery as delivery
    from src.runtime import reactor_wake
    from src.state import paths
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    scopes = ((city.name, "2026-09-29", "high"),)
    module, name = ((reactor_wake, "publish_reactor_wake") if failure_step == "publish"
                    else (paths, "write_json_atomic"))
    original = getattr(module, name)
    def failed(*args, **kwargs):
        raise OSError("fixture interrupted delivery")
    monkeypatch.setattr(module, name, failed)
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["status"] == "WAKE_DEFERRED"
    assert not (tmp_path / delivery._PUBLICATION_FILE).exists()
    monkeypatch.setattr(module, name, original)
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 1
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 0


def test_periodic_physical_wake_precedes_failed_reseed_without_new_fetch(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.data import replacement_forecast_production as production
    from src.runtime.reactor_wake import reactor_wakes_since
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    def failed_seed(*args, **kwargs):
        assert len(reactor_wakes_since(None, path=tmp_path / "edli-reactor-wake.json")) == 2
        raise RuntimeError("fixture ENS unavailable")
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", failed_seed)
    with pytest.raises(RuntimeError, match="ENS unavailable"):
        delivery.reconcile_current_temperature_delivery({}, cities=(city,), now=now)
    # Retrying failed forecast work must not continuously re-publish the
    # already durable physical hint, which has independent consumer completion.
    with pytest.raises(RuntimeError, match="ENS unavailable"):
        delivery.reconcile_current_temperature_delivery({}, cities=(city,), now=now)


@pytest.mark.parametrize("held", [True, False])
def test_main_physical_wake_runs_held_monitor_before_reactor_and_ack(monkeypatch, held):
    import src.main as main
    from src.runtime import reactor_wake as wake_module
    family = ("Singapore", "2026-10-06", "high")
    wake = wake_module.ReactorWake("physical-held", "2026-10-06T09:00:00+00:00",
                                  "physical_current_delivery", "current_temperature_print_committed",
                                  forecast_families=(family,))
    calls = []
    state = [False, None]
    class IdleLock:
        def locked(self): return False
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda families: frozenset(families) if held else frozenset())
    monkeypatch.setattr(main, "_forecast_exit_monitor_attempt_state", lambda wake_id: tuple(state))
    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", lambda ids, families, **kw: calls.append((ids, families)))
    monkeypatch.setattr(wake_module, "exact_held_sell_completion_wake_ids", lambda **kw: frozenset())
    monkeypatch.setattr(wake_module, "strict_generic_held_family_completion_wakes", lambda **kw: ())
    monkeypatch.setattr(wake_module, "read_reactor_wake", lambda **kw: wake)
    monkeypatch.setattr(wake_module, "coalescible_reactor_wakes", lambda selected: (wake,))
    monkeypatch.setattr(main, "_edli_reactor_active_lock", IdleLock())
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **kw: calls.append("reactor") or True)
    monkeypatch.setattr(main, "_acknowledge_edli_reactor_wake_batch", lambda *a, **kw: calls.append(("ack", kw)) or True)
    if held:
        assert main._edli_reactor_wake_poll_once() is False
        assert calls == [((wake.wake_id,), frozenset((family,)))]
        state[:] = [True, True]
    assert main._edli_reactor_wake_poll_once() is True
    assert calls[-2] == "reactor"
    assert calls[-1][0] == "ack" and calls[-1][1]["forecast_monitor_wake"] is held


def test_ingest_late_correction_wakes_held_before_failed_reseed(monkeypatch, tmp_path):
    import threading
    import src.ingest_main as ingest
    from src.config import cities_by_name
    from src.data import station_temperature_adapters as adapters
    from src.data import replacement_forecast_production as production
    from src.data import physical_current_delivery as delivery
    from src.data.physical_current_sources import load_physical_current_sources
    from src.runtime.reactor_wake import reactor_wakes_since
    from src.state import db, write_coordinator as coordinator
    from src.state.schema.observation_prints_schema import ensure_table, append_print

    city = cities_by_name["Chicago"]
    route = next(r for r in load_physical_current_sources()[0]
                 if r.provider == "noaa_wrh" and r.station_id == city.wu_station)
    now = datetime.now(UTC)
    older, frontier = now - timedelta(minutes=20), now - timedelta(minutes=5)
    sample = adapters._sample(route, older, 70.0, now, "a" * 64)
    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        for observed, value in ((older, 80.0), (frontier, 75.0)):
            append_print(conn, city=city.name, station_id=route.station_id, source_channel=route.source_channel,
                         publish_ts_utc=observed.isoformat(), value_native=value, unit=route.unit,
                         fetched_at_utc=observed.isoformat(), raw_report="previous")
    class Lease:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def record_commit(self, **kwargs): assert kwargs["rows_changed"] == 1
    mutex = threading.Lock()
    monkeypatch.setattr(adapters, "fetch_station_temperature", lambda *a, **kw: (sample,))
    monkeypatch.setattr(db, "world_write_mutex", lambda: mutex)
    monkeypatch.setattr(db, "get_world_connection", lambda **kw: sqlite3.connect(path))
    monkeypatch.setattr(db, "get_world_connection_read_only", lambda **kw: sqlite3.connect(f"file:{path}?mode=ro", uri=True))
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator",
                        lambda: SimpleNamespace(lease=lambda *a, **kw: Lease()))
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: {})
    monkeypatch.setattr(delivery, "current_temperature_priority_families", lambda: {})
    monkeypatch.setattr(ingest, "_physical_current_pending_wakes", set())
    def failed_seed(*args, **kwargs):
        assert not mutex.locked()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()[0] == 3
        assert len(reactor_wakes_since(None, path=tmp_path / "edli-reactor-wake.json")) == 2
        raise RuntimeError("fixture carrier blocked")
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", failed_seed)
    result = ingest._day0_current_temperature_source_tick(city, route)
    assert result["status"] == "COMMITTED"
    assert result["inserted"] == 1 and result["advanced"] is False
    assert (city.name, route.station_id, route.source_channel) in ingest._physical_current_pending_wakes
    assert len(reactor_wakes_since(None, path=tmp_path / "edli-reactor-wake.json")) == 2


def test_physical_wake_causal_cut_excludes_future_receipts_and_wrong_day(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.state.schema.observation_prints_schema import append_print
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    scopes = ((city.name, "2026-09-29", "high"),)
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 1
    with sqlite3.connect(path) as conn:
        # Source clock is in the day, but this correction is not yet possessed.
        append_print(conn, city=city.name, station_id="TEST", source_channel="noaa_wrh_test",
                     publish_ts_utc="2026-09-29T10:00:00+00:00", value_native=33.0, unit="C",
                     fetched_at_utc="2026-09-29T12:01:00+00:00")
        # Later commit id must not mask the earlier-id future receipt once it
        # becomes causal; cardinality belongs in the delivery revision too.
        append_print(conn, city=city.name, station_id="TEST", source_channel="noaa_wrh_test",
                     publish_ts_utc="2026-09-29T11:30:00+00:00", value_native=34.0, unit="C",
                     fetched_at_utc="2026-09-29T11:31:00+00:00")
        append_print(conn, city=city.name, station_id="TEST", source_channel="noaa_wrh_test",
                     publish_ts_utc="2026-09-28T11:00:00+00:00", value_native=99.0, unit="C",
                     fetched_at_utc="2026-09-29T11:59:00+00:00")
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 1
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now)["published"] == 0
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scopes, now=now + timedelta(minutes=1))["published"] == 1
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=((city.name, "2026-09-30", "high"),), now=now)["published"] == 0


def test_physical_publication_uses_existing_city_timestamp_range_index():
    from src.data import physical_current_delivery as delivery
    from src.state.schema.observation_prints_schema import ensure_table
    queries = []
    class RecordingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT json_group_array"):
                queries.append((sql, parameters))
            return super().execute(sql, parameters)
    with sqlite3.connect(":memory:", factory=RecordingConnection) as conn:
        ensure_table(conn)
        conn.executemany(
            "INSERT INTO observation_prints(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc) "
            "VALUES('fixture','TEST','source',?,20,'C',?)",
            ((f"2025-01-01T{hour % 24:02}:00:00+00:00", f"2025-01-01T23:00:00.{hour:06d}+00:00") for hour in range(20000)),
        )
        conn.execute("INSERT INTO observation_prints(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc) "
                     "VALUES('fixture','TEST','source','2026-09-29 11:00:00+00:00',21,'C','2026-09-29T11:01:00+00:00')")
        revision = delivery._current_temperature_ledger_revision(
            conn, city=SimpleNamespace(name="fixture", timezone="UTC"), target="2026-09-29",
            now=datetime(2026, 9, 29, 12, tzinfo=UTC),
        )
        assert revision is not None
        sql, args = queries[-1]
        detail = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, args))
        assert "idx_observation_prints_city_publish" in detail
        assert "publish_ts_utc>? AND publish_ts_utc<?" in detail
        old = "SELECT COUNT(*), MAX(rowid) FROM observation_prints WHERE city=? AND julianday(publish_ts_utc)>=julianday(?)"
        old_detail = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + old, ("fixture", "2026-09-29")))
        assert "publish_ts_utc>?" not in old_detail
        # The selected query is proportional to this day's rows, not history.
        steps = [0]
        def count_steps():
            steps[0] += 1000
            return 0
        conn.set_progress_handler(count_steps, 1000)
        conn.execute(sql, args).fetchone()
        conn.set_progress_handler(None, 0)
        assert steps[0] < 1000


def test_malformed_and_slow_scope_do_not_block_other_physical_wakes(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    original = delivery._current_temperature_ledger_revision
    def one_slow_scope(conn, *, target, **kwargs):
        if target == "2026-09-28":
            conn.execute("WITH RECURSIVE counter(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM counter WHERE n<100000000) SELECT SUM(n) FROM counter").fetchone()
            pytest.fail("scope should have reached its SQLite read deadline")
        assert not delivery._PUBLICATION_LOCK.locked()
        return original(conn, target=target, **kwargs)
    monkeypatch.setattr(delivery, "_current_temperature_ledger_revision", one_slow_scope)
    result = delivery.publish_current_temperature_wakes(
        cities=(city,), scopes=((city.name, "bad-day", "high"), (city.name, "2026-09-28", "high"),
                               (city.name, "2026-09-29", "high")), now=now,
    )
    assert result == {"status": "WAKE_DEFERRED", "published": 1}


def test_busy_publication_lock_defers_without_waiting_or_losing_debt(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    scope = ((city.name, "2026-09-29", "high"),)
    with delivery._PUBLICATION_LOCK:
        assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scope, now=now) == {
            "status": "WAKE_DEFERRED", "published": 0,
        }
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scope, now=now)["published"] == 1


def test_direct_commit_hint_does_not_wait_for_any_ledger_scan(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.runtime.reactor_wake import reactor_wakes_since
    city = SimpleNamespace(name="fixture", timezone="UTC")
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr("src.state.db.get_world_connection_read_only",
                        lambda **kw: pytest.fail("a committed writer must not scan before hinting"))
    result = delivery.publish_current_temperature_wakes(
        cities=(city,), scopes=((city.name, "2026-09-29", "high"),),
        now=datetime(2026, 9, 29, 12, tzinfo=UTC), committed=True,
    )
    assert result["published"] == 1
    assert len(reactor_wakes_since(None, path=tmp_path / "edli-reactor-wake.json")) == 1


def test_partial_causal_page_hints_before_complete_dense_scan(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.state.schema.observation_prints_schema import ensure_table
    path = tmp_path / "dense.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        conn.executemany(
            "INSERT INTO observation_prints(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc) "
            "VALUES('Dense','TEST','source','2026-09-29T11:00:00+00:00',20,'C',?)",
            ((f"2026-09-29T11:01:00.{index:06d}+00:00",) for index in range(300)),
        )
    delivery._LEDGER_SCAN_STATES.clear()
    delivery._LEDGER_SCAN_LOCKS.clear()
    monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda **kw: sqlite3.connect(path))
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    limit = [128]
    def clock():
        count = max((state["count"] for state in delivery._LEDGER_SCAN_STATES.values()), default=0)
        return 0.0 if count < limit[0] else 1.0
    monkeypatch.setattr(delivery.time, "monotonic", clock)
    kwargs = dict(cities=(SimpleNamespace(name="Dense", timezone="UTC"),),
                  scopes=(("Dense", "2026-09-29", "high"),), now=datetime(2026, 9, 29, 12, tzinfo=UTC))
    first = delivery.publish_current_temperature_wakes(**kwargs)
    assert first["published"] == 1 and first["scan_pending"] is True
    state = next(iter(delivery._LEDGER_SCAN_STATES.values()))
    assert state["count"] == 129 and state["complete"] is False
    limit[0] = 256
    second = delivery.publish_current_temperature_wakes(**kwargs)
    assert second["published"] == 0 and second["scan_pending"] is True
    assert state["count"] == 257 and state["complete"] is False
    limit[0] = 384
    final = delivery.publish_current_temperature_wakes(**kwargs)
    assert state["count"] == 300 and state["complete"] is True
    assert not final.get("scan_pending")


def test_failed_new_commit_hint_drains_while_older_cut_is_incomplete(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    scope = ((city.name, "2026-09-29", "high"),)
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scope, now=now)["published"] == 1
    state = next(v for k, v in delivery._LEDGER_SCAN_STATES.items() if str(path) in k)
    # Retain a real admitted first page, with the old continuation still pending.
    state["complete"] = False
    monkeypatch.setattr(delivery.time, "monotonic", lambda: 0.0)
    def row_count_clock():
        return 0.0 if state["tail_cursor"] < state["upper"] + 1 else 1.0
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO observation_prints(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc) VALUES(?,?,?,?,?,?,?)",
                     (city.name, "TEST", "source", "2026-09-29T09:00:00+00:00", 19, "C", now.isoformat()))
    original = __import__("src.runtime.reactor_wake", fromlist=["publish_reactor_wake"]).publish_reactor_wake
    def failed_publish(**kwargs):
        raise OSError("injected durable queue failure")
    monkeypatch.setattr("src.runtime.reactor_wake.publish_reactor_wake", failed_publish)
    assert delivery.publish_current_temperature_wakes(cities=(city,), scopes=scope, now=now, committed=True)["published"] == 0
    monkeypatch.setattr("src.runtime.reactor_wake.publish_reactor_wake", original)
    monkeypatch.setattr(delivery.time, "monotonic", row_count_clock)
    result = delivery.publish_current_temperature_wakes(cities=(city,), scopes=scope, now=now)
    assert result["published"] == 1 and result["scan_pending"] is True
    assert state["complete"] is False
    assert state["tail_revision"] == (1, state["upper"] + 1)


def test_future_lower_rowid_tail_admission_rewakes_before_old_cut_finishes(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    kwargs = dict(cities=(city,), scopes=((city.name, "2026-09-29", "high"),))
    assert delivery.publish_current_temperature_wakes(**kwargs, now=now)["published"] == 1
    state = next(v for k, v in delivery._LEDGER_SCAN_STATES.items() if str(path) in k)
    state["complete"] = False
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO observation_prints(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc) VALUES(?,?,?,?,?,?,?)",
                         ((city.name, "TEST", "source", "2026-09-29T09:00:00+00:00", 19, "C", receipt.isoformat())
                          for receipt in (now + timedelta(hours=1), now)))
    first = delivery.publish_current_temperature_wakes(**kwargs, now=now)
    assert first["published"] == 1 and state["complete"] is False
    assert state["tail_revision"] == (1, state["upper"] + 2)
    second = delivery.publish_current_temperature_wakes(**kwargs, now=now + timedelta(hours=1))
    assert second["published"] == 1 and state["complete"] is False
    assert state["tail_revision"] == (2, state["upper"] + 2)


def test_long_fractional_clock_spelling_retains_owner_causality_without_payload_cache(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.state.schema.observation_prints_schema import append_print
    path, city, now = _physical_delivery_fixture(monkeypatch, tmp_path)
    long_clock = "2026-09-29T11:59:59." + "9" * 100 + "+00:00"
    with sqlite3.connect(path) as conn:
        assert append_print(conn, city=city.name, station_id="TEST", source_channel="long",
                            publish_ts_utc=long_clock, value_native=21, unit="C",
                            fetched_at_utc=long_clock, raw_report="X" * 1_000_000)
    result = delivery.publish_current_temperature_wakes(cities=(city,), scopes=((city.name, "2026-09-29", "high"),), now=now)
    assert result["published"] == 1
    state = next(v for k, v in delivery._LEDGER_SCAN_STATES.items() if str(path) in k)
    assert state["count"] == 3 and state["complete"] is True
    assert len(repr(state)) < 1500
