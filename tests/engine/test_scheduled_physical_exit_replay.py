# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: offline scheduled physical-exit acceptance; INV-01/06/28/41.
"""Deterministic scheduled replay with canonical source and wake ownership.

The scheduler is APScheduler itself, including due-time discovery, coalescing,
executor submission and job events. Only its clock and executor concurrency are
deterministic. Native HTTP transport is synthetic; production daemon entrypoints
are never started. No posterior, bound probability receipt or fill projection
may be inserted to bridge a missing production step.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
import sqlite3
import time
from types import SimpleNamespace
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from tests.test_replacement_forecast_materializer import (  # noqa: F401: pytest fixtures
    _hko_source_surface, _hko_native_surfaces,
)


UTC = timezone.utc


def _seed_market_and_holding(conn, request, tmp_path, monkeypatch, *, direction, held_bin, expired_book=False):
    """Initial synthetic exposure enters through the canonical entry writer."""
    from src.state import db
    from src.state.portfolio import Position
    from src.state.snapshot_repo import get_snapshot, insert_snapshot
    from src.engine.lifecycle_events import build_entry_canonical_write
    from tests.test_exit_safety import _ensure_snapshot

    path = tmp_path / "zeus_trades.db"
    monkeypatch.setattr(db, "_zeus_trade_db_path", lambda: path)
    trade = sqlite3.connect(path)
    trade.row_factory = sqlite3.Row
    db.init_schema_trade_only(trade)
    _ensure_snapshot(trade, snapshot_id="prototype",
        captured_at=request.computed_at - timedelta(hours=1) if expired_book else request.computed_at,
        freshness_deadline=request.computed_at - timedelta(seconds=1) if expired_book else request.computed_at + timedelta(minutes=10))
    prototype = get_snapshot(trade, "prototype")
    for index, item in enumerate(request.bins):
        condition = "0x" + f"{index+1:064x}"
        yes, no = str(1000 + index * 2), str(1001 + index * 2)
        conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,condition_id,token_id,range_label,range_low,range_high,created_at,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("wrh-synthetic-parent-event", request.city, str(request.target_date), request.temperature_metric,
             condition, yes, item.bin_id, item.lower_c, item.upper_c,
             request.computed_at.isoformat(), request.computed_at.isoformat()))
        insert_snapshot(trade, replace(prototype, snapshot_id=f"wrh-book-{index}",
            condition_id=condition, yes_token_id=yes, no_token_id=no,
            selected_outcome_token_id=yes if direction == "buy_yes" else no,
            outcome_label="YES" if direction == "buy_yes" else "NO", token_map_raw={"YES": yes, "NO": no}))
    position = Position(trade_id="scheduled-held", market_id="0x" + f"{held_bin+1:064x}",
        condition_id="0x" + f"{held_bin+1:064x}", city=request.city, target_date=str(request.target_date),
        cluster="Asia", temperature_metric=request.temperature_metric, bin_label=request.bins[held_bin].bin_id,
        direction=direction, token_id=str(1000 + held_bin * 2), no_token_id=str(1001 + held_bin * 2),
        shares=5, chain_shares=5, chain_state="synced", cost_basis_usd=.6, size_usd=.6,
        entry_price=.12, state="day0_window", unit="C", env="live", strategy_key="forecast_qkernel_entry",
        entered_at=(request.computed_at-timedelta(hours=1)).isoformat(), chain_avg_price=.12,
        chain_cost_basis_usd=.6, fill_authority="venue_confirmed_full")
    events, projection = build_entry_canonical_write(position, phase_after="day0_window",
        decision_id="scheduled-initial-exposure", source_module=__name__)
    projection["phase"] = "day0_window"
    db.append_many_and_project(trade, events, projection)
    conn.commit()
    trade.commit()
    return trade, position


class ScheduledPump:
    """Use registered callbacks and the actual APScheduler dispatch machinery."""

    def __init__(self, monkeypatch, clock):
        from apscheduler.executors.debug import DebugExecutor
        from apscheduler.schedulers.base import BaseScheduler
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED
        from src import ingest_main
        from src.data.scheduler_adapter import (
            build_registry_scheduler, build_job_specs, job_defs_from_specs,
        )

        class ClockType(type):
            def __instancecheck__(cls, value):
                return isinstance(value, datetime)

        class EventClock(datetime, metaclass=ClockType):
            @classmethod
            def now(cls, tz=None):
                return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

        self.clock = clock
        self.clock_type = EventClock
        monkeypatch.setitem(sqlite3.adapters, (EventClock, sqlite3.PrepareProtocol),
            lambda stamp: stamp.isoformat(" "))
        self.events = []
        self.transport_ticks = []
        for module in ("apscheduler.schedulers.base", "apscheduler.executors.base", "apscheduler.triggers.interval"):
            monkeypatch.setattr(f"{module}.datetime", EventClock)
        executors = {spec.executor_class: DebugExecutor() for spec in build_job_specs("ingest_main")}
        self.scheduler = BlockingScheduler(timezone=UTC, executors=executors)
        build_registry_scheduler(
            self.scheduler, "ingest_main", job_defs_from_specs(ingest_main._ingest_main_job_specs()),
            forecast_live_owner_env="",
        )
        # Full registry construction verifies the production job set. This
        # bounded replay pauses unrelated jobs before any executor is started.
        for job in self.scheduler.get_jobs():
            job.modify(next_run_time=None)
        self.scheduler.add_listener(self.events.append, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
        # Base start initializes real executors/stores without entering the
        # BlockingScheduler daemon loop or starting background worker threads.
        BaseScheduler.start(self.scheduler, paused=True)

    def enable(self, job_id, *, at=None):
        self.scheduler.modify_job(job_id, next_run_time=at or self.clock[0])

    def register_main_monitor_jobs(self, *, reconcile=False):
        """Reuse the two literal production registrations without daemon boot.

        main has no standalone registration API. Evaluate only these trusted
        add_job expressions, preserving its actual callbacks, startup delays,
        cadence and coalescing, rather than reproducing a test schedule.
        """
        import ast
        import inspect
        from apscheduler.executors.debug import DebugExecutor
        from src import main

        self.scheduler.remove_executor("default", shutdown=True)
        self.scheduler.add_executor(DebugExecutor(), alias="default")
        self.scheduler.add_executor(DebugExecutor(), alias="monitor_recovery")
        wanted = {"exit_monitor", "exit_monitor_recovery"}
        if reconcile:
            wanted |= {"chain_mirror_reconcile", "edli_command_recovery"}
        found = set()
        for node in ast.walk(ast.parse(inspect.getsource(main.main))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "scheduler" or node.func.attr != "add_job":
                continue
            job_id = next((arg.value.value for arg in node.keywords
                if arg.arg == "id" and isinstance(arg.value, ast.Constant)), None)
            if job_id in wanted:
                registration = ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=node)], type_ignores=[]))
                exec(compile(registration, main.__file__, "exec"), {**vars(main), "scheduler": self.scheduler})
                found.add(job_id)
        assert found == wanted

    def register_fill_job(self):
        import ast
        import inspect
        from apscheduler.executors.debug import DebugExecutor
        from src.ingest import price_channel_daemon, fill_synchronizer, price_channel_ingest

        self.scheduler.add_executor(DebugExecutor(), alias="fill_bridge")
        wanted, found = {"fill_synchronizer", "edli_fill_bridge_repair"}, set()

        for node in ast.walk(ast.parse(inspect.getsource(price_channel_daemon.main))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_job":
                continue
            job_id = next((arg.value.value for arg in node.keywords if arg.arg == "id" and isinstance(arg.value, ast.Constant)), None)
            if job_id not in wanted:
                continue
            registration = ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=node)], type_ignores=[]))
            exec(compile(registration, price_channel_daemon.__file__, "exec"),
                {**vars(price_channel_daemon), "_scheduler": self.scheduler,
                 "_edli_fill_bridge_repair_cycle": price_channel_ingest._edli_fill_bridge_repair_cycle,
                 "fill_synchronizer_cycle": fill_synchronizer.fill_synchronizer_cycle})
            found.add(job_id)
        assert found == wanted

    def advance(self, seconds=0):
        from apscheduler.schedulers.base import STATE_RUNNING, STATE_PAUSED
        self.clock[0] += timedelta(seconds=seconds)
        offset = len(self.events)
        self.scheduler.state = STATE_RUNNING
        try:
            self.scheduler._process_jobs()
        finally:
            self.scheduler.state = STATE_PAUSED
        fired = self.events[offset:]
        assert all(not getattr(event, "exception", None) for event in fired), fired
        return fired

    def run_until(self, end):
        events = []
        while True:
            due = [job.next_run_time for job in self.scheduler.get_jobs() if job.next_run_time is not None and job.next_run_time <= end]
            due.extend(item[0] for item in self.transport_ticks if item[0] <= end)
            if not due:
                break
            at = min(due)
            self.clock[0] = at
            for item in self.transport_ticks:
                if item[0] == at:
                    item[2]()
                    item[0] += item[1]
            events.extend(self.advance())
        self.clock[0] = end
        return events

    def close(self):
        from apscheduler.schedulers.base import BaseScheduler
        BaseScheduler.shutdown(self.scheduler, wait=True)


@pytest.fixture
def scheduled_source(tmp_path, monkeypatch, request):
    from src import config, ingest_main
    from src.data import noaa_wrh_timeseries as wrh
    from src.data import station_temperature_adapters as upstream
    from src.data import physical_current_delivery as delivery
    from src.state import db, write_coordinator
    from src.runtime import reactor_wake
    from tests.test_replacement_forecast_materializer import _TemperatureBin

    params = getattr(request, "param", {})

    clock = [datetime(2026, 10, 6, 2, tzinfo=UTC)]
    state = config.STATE_DIR / tmp_path.name
    state.mkdir()
    monkeypatch.setattr(config, "STATE_DIR", state)
    monkeypatch.setattr(write_coordinator, "_DEFAULT_RUNTIME_COORDINATOR", None)
    monkeypatch.setattr(db, "ZEUS_WORLD_DB_PATH", tmp_path / "zeus-world.db")
    native_request = None
    if params.get("forecast_inputs"):
        # The existing helper writes owned native ENS, source-run coverage,
        # raw provider bodies and ground/anchor possession. Its downstream
        # fusion override is removed BEFORE any posterior is materialized.
        from src.data import replacement_forecast_materializer as materializer
        from src.data import raw_forecast_artifact_manifest as manifests
        from tests import test_replacement_forecast_materializer as inputs
        request.getfixturevalue("_hko_source_surface")
        real_fusion = materializer._replacement_bayes_precision_fusion_override
        write_manifest = manifests.write_manifest_to_db

        def retain_initial_anchor_metadata(conn, manifest):
            if manifest.source_id == "openmeteo_ecmwf_ifs_9km":
                root = tmp_path / "production-queue"
                manifest = replace(manifest, product_metadata={**manifest.product_metadata,
                    "openmeteo_payload_json": manifest.artifact_path,
                    "precision_metadata_json": str(root / "native-precision.json"),
                    "manifest_json": str(root / "raw_manifest_dir" / "anchor.manifest.json")})
            return write_manifest(conn, manifest)

        # Add normal queue transport metadata to the FIRST retained anchor
        # artifact. Never UPDATE an already-issued fact or certificate later.
        with monkeypatch.context() as transport_metadata:
            transport_metadata.setattr(manifests, "write_manifest_to_db", retain_initial_anchor_metadata)
            forecasts, native_request = inputs._shanghai_current_owner_request(
                tmp_path, monkeypatch, record_observed_prints=False)
        monkeypatch.setattr(materializer, "_replacement_bayes_precision_fusion_override", real_fusion)
        clock[0] = native_request.computed_at
        forecast_path = Path(forecasts.execute("PRAGMA database_list").fetchone()[2])
    else:
        forecast_path = tmp_path / "zeus-forecasts.db"
        forecasts = sqlite3.connect(forecast_path)
    monkeypatch.setattr(db, "ZEUS_FORECASTS_DB_PATH", forecast_path)
    with sqlite3.connect(db.ZEUS_WORLD_DB_PATH) as conn:
        db.init_schema_world_only(conn)
    forecasts.row_factory = sqlite3.Row
    db.init_schema_forecasts(forecasts)
    forecasts.commit()
    forecasts.execute("ATTACH DATABASE ? AS world", (str(db.ZEUS_WORLD_DB_PATH),))
    city = config.runtime_cities_by_name()[native_request.city if native_request else "Singapore"]
    request = SimpleNamespace(city=city.name, target_date=clock[0].astimezone(ZoneInfo(city.timezone)).date(), temperature_metric="high",
        computed_at=clock[0], bins=(
            _TemperatureBin("29°C or below", upper_c=29, center_c=28),
            _TemperatureBin("30°C", lower_c=30, upper_c=30, center_c=30),
            _TemperatureBin("31°C or higher", lower_c=31, center_c=32),
        ))
    trade, position = _seed_market_and_holding(forecasts, request, tmp_path, monkeypatch,
        direction=params.get("direction", "buy_no"), held_bin=params.get("held_bin", 1), expired_book=params.get("execute", False))
    # This reader imports the path function once. Earlier fixture namespaces
    # must not remain captured when a later scheduled case changes DB owners.
    from src.data import replacement_forecast_seed_discovery as seed_discovery
    monkeypatch.setattr(seed_discovery, "_zeus_trade_db_path", db._zeus_trade_db_path)
    pump = ScheduledPump(monkeypatch, clock)
    for module in (ingest_main, upstream, delivery, reactor_wake):
        monkeypatch.setattr(module, "datetime", pump.clock_type)
    if native_request is None:
        monkeypatch.setattr(config, "runtime_cities_by_name", lambda: {city.name: city})
        monkeypatch.setattr(config, "runtime_cities", lambda: [city])
    monkeypatch.setattr(upstream, "_WRH_CURRENT_PRODUCT_CACHE", {})
    monkeypatch.setattr(ingest_main, "_WRH_OLD_OWNER_CURSOR", None)
    # The token is an upstream fixture, not a user credential. Keep the actual
    # spacing implementation; its monotonic clock follows simulated time.
    epoch = clock[0]
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "synthetic-public-wrh-token")
    monotonic = lambda: 1000 + (clock[0] - epoch).total_seconds()
    monkeypatch.setattr(wrh, "time", SimpleNamespace(monotonic=monotonic,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + timedelta(seconds=seconds))))
    monkeypatch.setattr(upstream, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(wrh, "_last_request_at", 0.0)
    values, requests, body_available = [(32.0, 28.0)], [], []
    native_instants = [clock[0]-timedelta(minutes=90), clock[0]-timedelta(minutes=60)]

    def native_body():
        times = [stamp.astimezone(ZoneInfo(city.timezone)).strftime("%Y-%m-%dT%H:%M:%S%z")
                 for stamp in native_instants]
        return json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Celsius"},
            "STATION": [{"STID": city.wu_station, "OBSERVATIONS": {
                "date_time": times[:len(values[0])], "air_temp_set_1": list(values[0]),
                "sea_level_pressure_set_1": [1010] * len(values[0]),
            }}]}, sort_keys=True).encode()

    def transport(request):
        # Real dual-DB writer admission succeeds at HTTP time. A future edit
        # moving acquisition under either canonical flock fails this assertion.
        with db.get_forecasts_connection_with_world(write_class="live", blocking=False):
            pass
        requests.append((clock[0], str(request.url.copy_with(query=None))))
        body_available.append({"event_time": clock[0].isoformat(), "wall_monotonic": time.perf_counter()})
        return httpx.Response(200, content=native_body())

    bounded_body = upstream._bounded_body
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(upstream, "_bounded_body",
            lambda ignored, *args, **kwargs: bounded_body(client, *args, **kwargs))
        try:
            yield SimpleNamespace(clock=clock, pump=pump, forecasts=forecasts, trade=trade,
                city=city, position=position, values=values, requests=requests, body_available=body_available,
                request=request, native_request=native_request, params=params, tmp_path=tmp_path)
        finally:
            pump.close()
            trade.close()
            forecasts.close()


def test_registered_wrh_dispatch_commits_native_product_and_durable_held_wake(scheduled_source, record_property):
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.runtime.reactor_wake import coalescible_reactor_wakes, read_reactor_wake

    case = scheduled_source
    job_id = "ingest_day0_noaa_wrh_current"
    case.pump.enable(job_id)
    events = case.pump.advance()
    assert [event.job_id for event in events] == [job_id]
    assert events[0].retval["committed"] == 1, events[0].retval
    owned, snapshot = read_current_noaa_wrh_snapshot(case.forecasts, city=case.city,
        target_date=str(case.request.target_date), as_of=case.clock[0])
    assert owned and snapshot is not None
    assert snapshot.extreme("high").value == 32.0
    wake = read_reactor_wake()
    assert wake is not None and wake.reason == "current_temperature_print_committed"
    wakes = coalescible_reactor_wakes(wake)
    families = {family for item in wakes for family in item.forecast_families}
    assert (case.city.name, str(case.request.target_date), "high") in families
    assert len(case.requests) == 1
    assert snapshot.received_at == events[0].scheduled_run_time == case.clock[0]
    assert case.trade.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    record_property("scheduled_source_milestone", json.dumps({
        "scheduled_at": events[0].scheduled_run_time.isoformat(),
        "received_at": snapshot.received_at.isoformat(), "http_requests": len(case.requests),
        "wake_ids": [item.wake_id for item in wakes], "source_revision": snapshot.response_sha256,
        "execution": "not reached by source-only milestone",
    }))


def test_wrh_writer_lock_is_available_before_and_after_failed_scheduled_callback(scheduled_source):
    """Discriminate self-contention from another process or a leaked fixture txn."""
    from src.state import db

    case = scheduled_source
    with db.get_forecasts_connection_with_world(write_class="live", blocking=False) as conn:
        assert {row[1] for row in conn.execute("PRAGMA database_list")} == {"main", "world"}
    case.pump.enable("ingest_day0_noaa_wrh_current")
    event, = case.pump.advance()
    with db.get_forecasts_connection_with_world(write_class="live", blocking=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == event.retval["committed"]
    assert event.retval["committed"] == 1, event.retval


def test_registered_wrh_respects_real_world_contention_and_recovers(scheduled_source):
    from src.state import db
    from src.runtime.reactor_wake import read_reactor_wake

    case = scheduled_source
    case.pump.enable("ingest_day0_noaa_wrh_current")
    # A real other WORLD writer owns the same protective flock. The source
    # body was obtained before its acquisition, so the next tick uses the cache.
    from src.data.station_temperature_adapters import iter_current_noaa_wrh_products
    product, = iter_current_noaa_wrh_products(((case.city, str(case.request.target_date)),))
    assert product[2].rows
    mutex = db.world_write_mutex()
    assert mutex.acquire(blocking=False)
    try:
        event, = case.pump.advance()
        assert event.retval["committed"] == 0
        assert event.retval["stages"] == [
            {"label": "received_product_commit", "ok": False, "error": "BlockingIOError"}]
        assert read_reactor_wake() is None
        assert case.forecasts.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        mutex.release()
    event, = case.pump.advance(60)
    assert event.retval["committed"] == 1, event.retval
    assert read_reactor_wake() is not None


def test_scheduled_wrh_write_failure_rolls_back_both_owners_before_retry(scheduled_source, monkeypatch):
    from src.data import daily_obs_append as writer
    from src.runtime.reactor_wake import read_reactor_wake
    from src.state import db
    from src.state.db_writer_lock import db_writer_lock, WriteClass

    case = scheduled_source
    original = writer.append_current_noaa_wrh_product

    def write_then_fail(conn, **kwargs):
        for path in (db.ZEUS_FORECASTS_DB_PATH, db.ZEUS_WORLD_DB_PATH):
            with pytest.raises(BlockingIOError):
                with db_writer_lock(path, WriteClass.LIVE, blocking=False):
                    pytest.fail("scheduled mutation lacks canonical owner protection")
        result = original(conn, **kwargs)
        assert result == "inserted"
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM world.data_coverage").fetchone()[0] > 0
        raise RuntimeError("synthetic failure after both canonical writes")

    case.pump.enable("ingest_day0_noaa_wrh_current")
    with monkeypatch.context() as failure:
        failure.setattr(writer, "append_current_noaa_wrh_product", write_then_fail)
        event, = case.pump.advance()
    assert event.retval["committed"] == 0
    assert case.forecasts.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert case.forecasts.execute("SELECT COUNT(*) FROM world.data_coverage").fetchone()[0] == 0
    assert read_reactor_wake() is None
    event, = case.pump.advance(60)
    assert event.retval["committed"] == 1, event.retval
    assert read_reactor_wake() is not None


def test_scheduled_wrh_coalesces_due_ticks_and_keeps_correction_identity(scheduled_source):
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.runtime import reactor_wake

    case = scheduled_source
    started = case.clock[0]
    case.pump.enable("ingest_day0_noaa_wrh_current")
    first, = case.pump.advance()
    assert first.retval["committed"] == 1
    _, before = read_current_noaa_wrh_snapshot(case.forecasts, city=case.city,
        target_date=str(case.request.target_date), as_of=case.clock[0])
    case.values[0] = (29.0, 28.0)
    # Missed intervals result in ONE callback at the newest registered due
    # time. The production polling interval, rather than a fixture constant,
    # defines that time (currently shorter than the HTTP cache lifetime).
    corrected, = case.pump.advance(180)
    interval = case.pump.scheduler.get_job("ingest_day0_noaa_wrh_current").trigger.interval
    assert corrected.scheduled_run_time == started + (timedelta(seconds=180) // interval) * interval
    assert corrected.retval["committed"] == 1
    _, after = read_current_noaa_wrh_snapshot(case.forecasts, city=case.city,
        target_date=str(case.request.target_date), as_of=case.clock[0])
    assert after.extreme("high").value == 29.0
    assert after.response_sha256 != before.response_sha256
    assert len(case.requests) == 2
    duplicate, = case.pump.advance(60)
    assert duplicate.retval["committed"] == 0
    _, retained = read_current_noaa_wrh_snapshot(case.forecasts, city=case.city,
        target_date=str(case.request.target_date), as_of=case.clock[0])
    assert retained.received_at == after.received_at
    wake = reactor_wake.read_reactor_wake()
    assert wake is not None
    assert len(reactor_wake.coalescible_reactor_wakes(wake)) == 4


def test_native_body_preparation_and_wake_publication_are_outside_writer_lease(scheduled_source, monkeypatch):
    from src.data import daily_obs_append as writer, physical_current_delivery as delivery
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.state import db

    case = scheduled_source
    checkpoints = []
    prepare = writer.prepare_current_noaa_wrh_product
    publish = delivery.publish_current_temperature_wakes

    def prepared(reader, **kwargs):
        with db.get_forecasts_connection_with_world(write_class="live", blocking=False):
            checkpoints.append("prepare_without_either_writer_lease")
        return prepare(reader, **kwargs)

    def published(**kwargs):
        # Independent read after acquiring both writer owners proves commit
        # and release precede publication, rather than merely following it.
        with db.get_forecasts_connection_with_world(write_class="live", blocking=False) as reader:
            owned, snapshot = read_current_noaa_wrh_snapshot(reader, city=case.city,
                target_date=str(case.request.target_date), as_of=case.clock[0])
            assert owned and snapshot.extreme("high").value == 32.0
            checkpoints.append("publish_after_committed_row_and_lease_release")
        return publish(**kwargs)

    monkeypatch.setattr(writer, "prepare_current_noaa_wrh_product", prepared)
    monkeypatch.setattr(delivery, "publish_current_temperature_wakes", published)
    case.pump.enable("ingest_day0_noaa_wrh_current")
    event, = case.pump.advance()
    assert event.retval["committed"] == 1
    assert checkpoints == ["prepare_without_either_writer_lease", "publish_after_committed_row_and_lease_release"]


def test_scheduled_prepared_product_cannot_overwrite_concurrent_native_correction(scheduled_source, monkeypatch):
    from src.data import daily_obs_append as writer, station_temperature_adapters as upstream
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.state import db

    case = scheduled_source
    original = writer.prepare_current_noaa_wrh_product
    superseded = []

    def correction_between_prepare_and_commit(reader, **kwargs):
        prepared = original(reader, **kwargs)
        # An independent producer acquires newer native bytes between the
        # scheduled owner's read and write. Both commits retain real owners.
        # The interleaving is deterministic; no row/provenance UPDATE occurs.
        case.clock[0] += timedelta(seconds=60)
        case.values[0] = (29.0, 28.0)
        product, = upstream.iter_current_noaa_wrh_products(((case.city, str(case.request.target_date)),))
        new_product = product[2]
        fresh = original(reader, city=case.city, target_date=str(case.request.target_date),
            product=new_product, as_of=case.clock[0])
        with db.get_forecasts_connection_with_world(write_class="live", blocking=False) as other:
            other.execute("BEGIN IMMEDIATE")
            status = writer.append_current_noaa_wrh_product(other, city=case.city,
                target_date=str(case.request.target_date), product=new_product, as_of=case.clock[0], prepared=fresh)
            assert status == "inserted"
            other.commit()
        superseded.append(new_product.response_sha256)
        return prepared

    monkeypatch.setattr(writer, "prepare_current_noaa_wrh_product", correction_between_prepare_and_commit)
    case.pump.enable("ingest_day0_noaa_wrh_current")
    event, = case.pump.advance()
    assert event.retval["committed"] == 0
    assert event.retval["stages"] == [
        {"label": "received_product_commit", "ok": False, "error": "ValueError"}]
    _, current = read_current_noaa_wrh_snapshot(case.forecasts, city=case.city,
        target_date=str(case.request.target_date), as_of=case.clock[0])
    assert current.response_sha256 == superseded[0]
    assert current.extreme("high").value == 29.0
    assert case.forecasts.execute("SELECT COUNT(*) FROM world.daily_observation_revisions").fetchone()[0] == 0


@pytest.mark.parametrize("scheduled_source", [
    {"direction": "buy_yes", "held_bin": 1},
    {"direction": "buy_no", "held_bin": 2},
    {"direction": "buy_yes", "held_bin": 1, "cold_boot": True},
    {"direction": "buy_no", "held_bin": 2, "cold_boot": True},
    {"direction": "buy_yes", "held_bin": 1, "cold_boot": True, "short_window": True},
    {"direction": "buy_no", "held_bin": 2, "cold_boot": True, "short_window": True},
    {"direction": "buy_yes", "held_bin": 1, "cold_boot": True, "short_window": True, "outside_debt": True},
    {"direction": "buy_yes", "held_bin": 1, "require_semantic_receipt": True},
    {"direction": "buy_no", "held_bin": 2, "require_semantic_receipt": True},
    *({"direction": direction, "held_bin": held_bin, "require_semantic_receipt": True, "source_race": race}
      for direction, held_bin in (("buy_yes", 1), ("buy_no", 2))
      for race in ("correction", "same_extreme_revision", "unreadable")),
    {"direction": "buy_yes", "held_bin": 1, "require_semantic_receipt": True, "execute": True},
    {"direction": "buy_no", "held_bin": 2, "require_semantic_receipt": True, "execute": True, "cold_boot": True, "short_window": True},
    {"direction": "buy_yes", "held_bin": 1, "require_semantic_receipt": True, "execute": True, "control_fault": "cutover_unavailable"},
    {"direction": "buy_yes", "held_bin": 1, "require_semantic_receipt": True, "execute": True, "poll_only": True},
    {"direction": "buy_yes", "held_bin": 1, "require_semantic_receipt": True, "execute": True, "full_close": True, "cold_boot": True, "short_window": True},
    {"direction": "buy_no", "held_bin": 2, "require_semantic_receipt": True, "execute": True, "full_close": True, "cold_boot": True, "short_window": True},
], indirect=True, ids=("dead_yes", "dead_no", "cold_dead_yes", "cold_dead_no", "cold_short_yes", "cold_short_no", "outside_scope_debt", "receipt_yes", "receipt_no",
    "yes_correction", "yes_same_extreme_revision", "yes_unreadable", "no_correction", "no_same_extreme_revision", "no_unreadable",
    "signed_yes", "signed_cold_no", "signed_cutover_unavailable", "signed_poll_only", "signed_full_yes", "signed_full_no"))
def test_scheduled_native_wake_reaches_real_held_monitor(scheduled_source, monkeypatch, record_property):
    import threading
    from src import main
    from src.engine import monitor_refresh, cycle_runner, cycle_runtime
    from src.execution import exit_lifecycle
    from src.riskguard import riskguard
    from src.state import db, portfolio
    import datetime as runtime_datetime
    from tests import test_day0_hard_fact_exit as books

    case = scheduled_source
    record_property("replay_db_directory", str(case.tmp_path))
    book_window_end = case.clock[0] + timedelta(seconds=4 if case.params.get("short_window") else 120)
    # transition_phase deliberately imports datetime inside the normal writer.
    # Bind that import too, so lifecycle events never jump to wall time during
    # a historical replay. Already-imported producer modules are bound below.
    monkeypatch.setattr(runtime_datetime, "datetime", case.pump.clock_type)
    for module in (main, monitor_refresh, cycle_runner, cycle_runtime, exit_lifecycle, portfolio, riskguard, books, db):
        monkeypatch.setattr(module, "datetime", case.pump.clock_type)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: case.clock[0])
    monkeypatch.setattr(cycle_runner, "ZEUS_WORLD_DB_PATH", db.ZEUS_WORLD_DB_PATH)
    monkeypatch.setattr(cycle_runner, "_zeus_trade_db_path", db._zeus_trade_db_path)
    monkeypatch.setattr(riskguard, "RISK_DB_PATH", case.tmp_path / "risk_state.db")
    # A normal conservative attestation exercises real risk readers. It permits
    # held redecision while retaining fail-closed entry/control behavior.
    riskguard._persist_dependency_db_locked_attestation(sqlite3.OperationalError("synthetic dependency contention"))
    assert riskguard.get_current_level().value == "DATA_DEGRADED"
    class NativeBookVenue(books._DeepBookClob):
        def _book(self, token_id):
            self._bid = .10 if case.clock[0] < book_window_end else .04
            self._ask = self._bid + .02
            return {**super()._book(token_id),
                "market": "0x"+f"{(int(token_id)-1000)//2+1:064x}",
                "timestamp": str(int(case.clock[0].timestamp()*1000)),
                "tick_size": ".01", "min_order_size": "1", "neg_risk": False}

        def get_orderbook_snapshot(self, token_id):
            return self._book(token_id)

        def get_clob_market_info(self, condition_id, **kwargs):
            index = int(condition_id, 16)-1
            return {"condition_id": condition_id, "active": True, "closed": False,
                "archived": False, "accepting_orders": True, "enable_order_book": True,
                "minimum_tick_size": ".01", "minimum_order_size": "1", "neg_risk": False,
                "tokens": [{"token_id": str(1000+index*2), "outcome": "Yes"},
                           {"token_id": str(1001+index*2), "outcome": "No"}]}

        def get_fee_rate_details(self, token_id):
            return {"base_fee": 0}

        def get_open_orders(self, **kwargs):
            return []

        def __getattribute__(self, name):
            if name in {"get_open_orders", "get_orderbook_snapshot", "get_clob_market_info", "get_fee_rate_details"}:
                return object.__getattribute__(self, name)
            return super().__getattribute__(name)

    clob = NativeBookVenue(.10, .12)
    venue = None
    if case.params.get("execute"):
        from tests.fakes.scheduled_exit_venue import make_scheduled_exit_venue
        held_token = case.position.token_id if case.position.direction == "buy_yes" else case.position.no_token_id
        venue = make_scheduled_exit_venue(trade_db_path=db._zeus_trade_db_path(), event_clock=lambda: case.clock[0],
            condition_id=case.position.condition_id, yes_token_id=case.position.token_id,
            no_token_id=case.position.no_token_id, held_token=held_token, held_shares=5, bid=".10",
            fill_size=5 if case.params.get("full_close") else 2, liquidity=100, window_end=book_window_end,
            restored_window=(case.clock[0]+timedelta(seconds=600), case.clock[0]+timedelta(seconds=780)),
            market_end=datetime.combine(case.request.target_date+timedelta(days=1), datetime.min.time(),
                tzinfo=ZoneInfo(case.city.timezone)).astimezone(UTC))
        venue.install_httpx_transport(monkeypatch)
        venue.install_chain_transports(monkeypatch)
        controls = _install_normal_execution_controls(case, monkeypatch, venue)
        clob = venue.client
        submit = exit_lifecycle.place_sell_order

        def advance_execution_clock(*args, **kwargs):
            # Preserve the real causal intent-before-command contract even
            # though the deterministic scheduler does not spend event time
            # while Python runs. This changes only the shared fixture clock.
            case.clock[0] += timedelta(microseconds=1)
            return submit(*args, **kwargs)

        monkeypatch.setattr(exit_lifecycle, "place_sell_order", advance_execution_clock)
    monkeypatch.setattr(exit_lifecycle, "_held_monitor_clob_client", lambda: clob)
    real_semantic = exit_lifecycle._protective_sell_semantic_receipt
    changed_source = []

    def reprove_after_native_source_change(*args, **kwargs):
        race = case.params.get("source_race")
        if race and not changed_source:
            changed_source.append(race)
            # A deterministic external-source interleaving after the normal
            # EXIT_INTENT commit, immediately before unchanged source reproof.
            if race == "unreadable":
                from src.config import state_path
                body = state_path("noaa_wrh_response_bodies") / (case.initial_source.response_sha256 + ".zlib")
                body.write_bytes(b"synthetic corrupted retained native body")
            else:
                case.values[0] = (29.0, 28.0) if race == "correction" else (32.0, 27.0)
                events = case.pump.advance(60)
                assert any(event.job_id == "ingest_day0_noaa_wrh_current" and event.retval["committed"] == 1 for event in events)
        return real_semantic(*args, **kwargs)

    monkeypatch.setattr(exit_lifecycle, "_protective_sell_semantic_receipt", reprove_after_native_source_change)
    threads = []

    class RuntimeThreads:
        def __getattr__(self, name):
            return getattr(threading, name)

        def Thread(self, *args, **kwargs):
            thread = threading.Thread(*args, **kwargs)
            threads.append(thread)
            return thread

    monkeypatch.setattr(main, "threading", RuntimeThreads())
    monkeypatch.setattr(exit_lifecycle, "threading", RuntimeThreads())
    completed = threading.Event()
    results = []
    run = exit_lifecycle.run_exit_monitor_cycle

    def observe_monitor(**kwargs):
        try:
            if case.params.get("outside_debt") and not results:
                from src.engine.lifecycle_events import build_entry_canonical_write
                other = replace(case.position, trade_id="new-outside-low", temperature_metric="low",
                    token_id="1000", no_token_id="1001", direction="buy_no",
                    condition_id="0x" + f"{1:064x}", market_id="0x" + f"{1:064x}", bin_label="29°C or below")
                events, projection = build_entry_canonical_write(other, phase_after="day0_window",
                    decision_id="new-outside-low-initial", source_module=__name__)
                projection["phase"] = "day0_window"
                with db.get_trade_connection() as writer:
                    db.append_many_and_project(writer, events, projection)
                    writer.commit()
            result = run(**kwargs)
            results.append(result)
            return result
        finally:
            completed.set()

    monkeypatch.setattr(exit_lifecycle, "run_exit_monitor_cycle", observe_monitor)
    main._edli_initialize_reactor_wake_cursor()
    # Establish the initial overdue scope from canonical exposure, as the
    # independent monitor cadence does. Otherwise its first lazy debt read
    # can preempt the initial targeted tranche before that scope is admitted.
    monkeypatch.setattr(main, "_held_position_monitor_canonical_debt", threading.Event())
    monkeypatch.setattr(main, "_held_position_monitor_canonical_last_check", 0.0)
    if case.params.get("cold_boot"):
        case.pump.register_main_monitor_jobs(reconcile=venue is not None)
    else:
        assert main._held_position_monitor_debt_pending()
    if venue is not None:
        from src.ingest import fill_synchronizer, price_channel_daemon, fill_cash_observer, price_channel_ingest
        from src.execution import command_recovery
        from src.state import chain_mirror_reconciler
        for module in (fill_synchronizer, price_channel_daemon, fill_cash_observer, command_recovery, chain_mirror_reconciler, price_channel_ingest):
            monkeypatch.setattr(module, "datetime", case.pump.clock_type)
        monkeypatch.setattr(main, "_EDLI_COMMAND_RECOVERY_LAST_FULL_BUCKET", None)
        monkeypatch.setattr(main, "_EDLI_COMMAND_RECOVERY_FULL_SKIPPED_SINCE_BUCKET", None)
        recovery_summaries = []
        recover = command_recovery.reconcile_unresolved_commands

        def observe_recovery(**kwargs):
            result = recover(**kwargs)
            recovery_summaries.append({"at": case.clock[0].isoformat(), "scope": kwargs.get("scope"), "result": result})
            return result

        monkeypatch.setattr(command_recovery, "reconcile_unresolved_commands", observe_recovery)
        if not case.params.get("cold_boot"):
            case.pump.register_main_monitor_jobs(reconcile=True)
        case.pump.register_fill_job()
    case.pump.enable("ingest_day0_noaa_wrh_current")
    event, = case.pump.advance()
    assert event.retval["committed"] == 1
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    _, case.initial_source = read_current_noaa_wrh_snapshot(case.forecasts, city=case.city,
        target_date=str(case.request.target_date), as_of=case.clock[0])
    main._edli_reactor_wake_poll_once()
    assert completed.wait(15), "committed native wake did not finish a real held-monitor dispatch"
    for thread in threads:
        thread.join(15)
        assert not thread.is_alive()
    assert len(results) == 1
    rows = case.trade.execute("SELECT occurred_at,payload_json FROM position_events WHERE event_type='MONITOR_REFRESHED'").fetchall()
    initial_serviced = bool(rows)
    from src.runtime import reactor_wake
    original_wakes = reactor_wake.reactor_wakes_for_reason("current_temperature_print_committed")
    initial_attempts = {wake.wake_id: main._forecast_exit_monitor_attempt_state(wake.wake_id) for wake in original_wakes}
    if not initial_serviced:
        # One-second listener cadence: a failed attempt gets one fair exclusion
        # turn, then the unchanged durable wake retries. A falsely successful
        # attempt does not re-enter the held monitor on these real polls.
        for _ in range(2):
            case.clock[0] += timedelta(seconds=1)
            main._edli_reactor_wake_poll_once()
            for thread in threads:
                thread.join(15)
                assert not thread.is_alive()
            rows = case.trade.execute("SELECT occurred_at,payload_json FROM position_events WHERE event_type='MONITOR_REFRESHED'").fetchall()
            if rows:
                break
    if case.params.get("cold_boot") and not rows:
        first_due = event.scheduled_run_time + timedelta(seconds=main.HELD_POSITION_MONITOR_FIRST_DELAY_SECONDS)
        recovery = case.pump.advance((first_due-case.clock[0]).total_seconds())
        assert any(item.job_id == "exit_monitor" for item in recovery)
        for thread in threads:
            thread.join(15)
            assert not thread.is_alive()
        rows = case.trade.execute("SELECT occurred_at,payload_json FROM position_events WHERE event_type='MONITOR_REFRESHED'").fetchall()
    assert rows, "real held monitor failed to persist redecision"
    held = next(position for position in portfolio.load_runtime_open_portfolio(case.trade).positions if position.trade_id == case.position.trade_id)
    assert held.last_monitor_prob_is_fresh is True
    assert held.last_monitor_prob == 0.0
    if not case.params.get("execute") or case.params.get("control_fault"):
        assert case.trade.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    monitor_payload = json.loads(rows[0]["payload_json"])
    record_property("scheduled_held_monitor", json.dumps({
        "direction": held.direction, "held_q": held.last_monitor_prob,
        "source_due_at": event.scheduled_run_time.isoformat(), "monitor_at": rows[0]["occurred_at"],
        "initial_target_serviced": initial_serviced, "initial_callback_result": results[0],
        "initial_attempts": initial_attempts, "callback_results": results,
        "book_window_end": book_window_end.isoformat(), "monitor_bid": monitor_payload["last_monitor_best_bid"],
        "control": "real DATA_DEGRADED attestation; synthetic control writers" if venue else "real DATA_DEGRADED attestation; readiness absent",
    }))
    assert datetime.fromisoformat(rows[0]["occurred_at"]) < book_window_end
    assert monitor_payload["last_monitor_best_bid"] >= .05
    if case.params.get("outside_debt"):
        assert initial_serviced is False and results == [False, True]
        assert all(state == (True, False) for state in initial_attempts.values())
    if case.params.get("require_semantic_receipt"):
        intent = json.loads(case.trade.execute("SELECT payload_json FROM position_events WHERE event_type='EXIT_INTENT' AND position_id=? ORDER BY sequence_no DESC LIMIT 1",
            (case.position.trade_id,)).fetchone()[0])
        receipt = intent["exit_intent_probability_receipt"]
        assert receipt is not None, "qualified native evidence was dropped before protective SELL reproof"
        assert receipt["probability_authority"] == "day0_absorbing_hard_fact"
        assert receipt["held_side_probability"] == 0.0
        assert receipt["hard_fact_evidence"]["payload_identity"] == case.initial_source.response_sha256
        if case.params.get("source_race"):
            rejected = json.loads(case.trade.execute("SELECT payload_json FROM position_events WHERE event_type='EXIT_ORDER_REJECTED' AND position_id=? ORDER BY sequence_no DESC LIMIT 1",
                (case.position.trade_id,)).fetchone()[0])
            assert rejected["error"] == "protective_source_redecision_required", rejected
        else:
            held_token = held.token_id if held.direction == "buy_yes" else held.no_token_id
            semantic = exit_lifecycle._protective_sell_semantic_receipt(case.trade,
                position_id=held.trade_id, token_id=held_token, shares=5,
                kind="DAY0_HARD_FACT_BIN_DEAD")
            assert semantic is not None, "bound receipt did not pass real current-source protective reproof"
            errors = [json.loads(row[0]).get("error", "") for row in case.trade.execute(
                "SELECT payload_json FROM position_events WHERE event_type='EXIT_ORDER_REJECTED' AND position_id=?", (held.trade_id,))]
            assert all("protective_sell_execution_authority_unavailable" not in error for error in errors)
    if venue is not None:
        if case.params.get("control_fault"):
            assert venue.posts == []
        else:
            rejection = [json.loads(row[0]) for row in case.trade.execute(
                "SELECT payload_json FROM position_events WHERE event_type='EXIT_ORDER_REJECTED'")]
            assert len(venue.posts) == 1, {"rejection": rejection, "calls": venue.calls}
            assert venue.posts[0]["durable_before_post"] is True
            assert datetime.fromisoformat(venue.posts[0]["at"]) < book_window_end
            assert case.trade.execute("SELECT shares FROM position_current WHERE position_id=?", (held.trade_id,)).fetchone()[0] == 5
            record_property("signed_submission", json.dumps(venue.posts))
            record_property("predeclared_book_and_match", json.dumps({"bid": str(venue.bid),
                "quoted_depth_shares": str(venue.liquidity), "simulated_match_cap_shares": str(venue.fill_size),
                "holding_shares": 5, "assumption": "FAK match quantity is a predeclared transport input"}))
            record_property("source_to_submit_wall_seconds", venue.posts[0]["wall_monotonic"]-case.body_available[0]["wall_monotonic"])
            command = case.trade.execute("SELECT * FROM venue_commands").fetchone()
            from src.state.snapshot_repo import get_snapshot
            executable = get_snapshot(case.trade, command["snapshot_id"])
            assert executable.snapshot_id not in {"prototype", "wrh-book-1", "wrh-book-2"}
            assert float(executable.orderbook_top_bid) == .10
            assert float(command["price"]) == .10
            # The native venue confirmation changes only external facts. The
            # registered poller and pending-exit owner must project the fill.
            if not case.params.get("poll_only"):
                case.pump.run_until(case.clock[0]+timedelta(seconds=1))
            confirmed = venue.confirm_trade()
            assert case.trade.execute("SELECT shares FROM position_current WHERE position_id=?", (held.trade_id,)).fetchone()[0] == 5
            if not case.params.get("poll_only"):
                import asyncio
                from src.ingest import polymarket_user_channel as user_channel
                monkeypatch.setattr(user_channel, "datetime", case.pump.clock_type)
                stream = user_channel.PolymarketUserChannelIngestor(venue.adapter,
                    [held.condition_id], auth=user_channel.WSAuth("synthetic-key", "synthetic-secret", "synthetic-pass"),
                    conn_factory=db.get_trade_connection_with_world)
                # Feed raw native transport messages to the same handler used
                # by the authenticated receive loop. No fact writer is called
                # directly, and socket/auth startup is outside this replay.
                asyncio.run(stream.handle_raw_message(json.dumps(confirmed)))
                if not case.params.get("full_close"):
                    terminal = {**venue.orders[venue.posts[0]["order_id"]],
                        "event_type": "order", "type": "CANCELLATION",
                        "timestamp": str(int(case.clock[0].timestamp()*1000))}
                    asyncio.run(stream.handle_raw_message(json.dumps(terminal)))
                # The synthetic authenticated transport remains connected.
                # Native PONG messages arrive every10 event seconds through
                # the actual receive handler, retaining the real stale guard.
                case.pump.transport_ticks.append([case.clock[0]+timedelta(seconds=10), timedelta(seconds=10),
                    lambda: asyncio.run(stream.handle_raw_message("PONG"))])
            fill_events = case.pump.run_until(event.scheduled_run_time+timedelta(seconds=90))
            record_property("registered_fill_results", json.dumps({item.job_id: item.retval for item in fill_events}, default=str))
            if case.params.get("full_close"):
                for thread in threads:
                    thread.join(15)
                    assert not thread.is_alive()
                closed = case.trade.execute("SELECT * FROM position_current WHERE position_id=?", (held.trade_id,)).fetchone()
                fills = case.trade.execute("SELECT * FROM venue_trade_facts WHERE state='CONFIRMED'").fetchall()
                command_state = case.trade.execute("SELECT state FROM venue_commands WHERE command_id=?", (command["command_id"],)).fetchone()[0]
                close_events = case.trade.execute("SELECT occurred_at,payload_json FROM position_events WHERE event_type='EXIT_ORDER_FILLED' AND position_id=?", (held.trade_id,)).fetchall()
                assert len(fills) == 1 and float(fills[0]["filled_size"]) == 5
                assert command_state == "FILLED"
                assert closed["phase"] == "economically_closed", dict(closed)
                assert len(close_events) == 1
                assert not portfolio.load_runtime_open_portfolio(case.trade).positions
                assert float(closed["realized_pnl_usd"]) == pytest.approx(-.10)
                assert {row[0] for row in case.trade.execute("SELECT status FROM venue_fill_cash_facts")} == {"UNKNOWN"}
                assert asyncio.run(stream.handle_raw_message(json.dumps(confirmed)))["reason"] == "duplicate_trade_fact"
                assert case.trade.execute("SELECT COUNT(*) FROM position_events WHERE event_type='EXIT_ORDER_FILLED'").fetchone()[0] == 1
                assert not portfolio.load_runtime_open_portfolio(case.trade).positions
                record_property("full_confirmed_closure", json.dumps({"phase": closed["phase"],
                    "command_state": command_state, "filled_shares": 5, "realized_pnl_usd": closed["realized_pnl_usd"],
                    "match_at": confirmed["match_time"], "confirmed_observed_at": fills[0]["observed_at"],
                    "canonical_close_at": close_events[0]["occurred_at"], "book_window_end": book_window_end.isoformat(),
                    "chain_cash_status": "UNKNOWN", "duplicate_confirmed_reopened_position": False}))
                return
            case.pump.run_until(case.clock[0]+timedelta(seconds=355))
            for thread in threads:
                thread.join(15)
                assert not thread.is_alive()
            current = case.trade.execute("SELECT shares,phase,order_status FROM position_current WHERE position_id=?", (held.trade_id,)).fetchone()
            assert current["shares"] == 3, dict(current)
            record_property("registered_command_recovery", json.dumps(recovery_summaries, default=str))
            command = case.trade.execute("SELECT state FROM venue_commands WHERE command_id=?", (command["command_id"],)).fetchone()
            journal = [json.loads(row[0]) for row in case.trade.execute(
                "SELECT payload_json FROM position_events WHERE position_id=? AND json_extract(payload_json, '$.economic_fill_identity') IS NOT NULL",
                (held.trade_id,))]
            record_property("canonical_partial_economic_journal", json.dumps(journal))
            assert sum(float(item.get("filled_shares") or 0) for item in journal) == 2, {
                "command_state": command["state"], "projection": dict(current), "journal": journal}
            assert sum(float(item.get("allocated_cost_basis_usd") or 0) for item in journal) == pytest.approx(.24)
            assert sum(float(item.get("realized_pnl_delta_usd") or 0) for item in journal) == pytest.approx(-.04)
            if case.params.get("poll_only"):
                assert command["state"] in {"EXPIRED", "CANCELED"}, {"state": command["state"], "recoveries": recovery_summaries}
                return
            assert command["state"] in {"EXPIRED", "CANCELED"}, {"state": command["state"], "recoveries": recovery_summaries}
            from src.state.fill_dedup import economic_exit_fills_for_position
            economic = economic_exit_fills_for_position(case.trade, held.trade_id)
            assert sum(float(fill.quantity) for fill in economic) == 2
            assert current["shares"] == 5-sum(float(fill.quantity) for fill in economic)
            assert len(venue.posts) == 1
            # This return of executable depth was declared before the first
            # native evidence. Refresh external account/process facts through
            # their normal writers; the next real cadence decides the residual.
            case.pump.run_until(event.scheduled_run_time+timedelta(seconds=600))
            controls.ledger.refresh(venue.adapter)
            from src.runtime import bankroll_provider
            assert bankroll_provider.warm_from_collateral_snapshot() is not None
            from src.control import ws_gap_guard
            ws_gap_guard.record_message(observed_at=case.clock[0], subscription_state="SUBSCRIBED")
            asyncio.run(controls.supervisor.run_once())
            case.pump.run_until(event.scheduled_run_time+timedelta(seconds=755))
            assert len(venue.posts) == 2, {"calls": venue.calls, "recoveries": recovery_summaries}
            assert venue.posts[1]["size"] == "3"
            assert event.scheduled_run_time+timedelta(seconds=600) <= datetime.fromisoformat(venue.posts[1]["at"]) < venue.restored_window[1]
            record_property("confirmed_partial_and_residual", json.dumps({"posts": venue.posts, "canonical_confirmed_quantity": 2,
                "residual_shares": 3, "restored_window": [value.isoformat() for value in venue.restored_window]}))
            # A late duplicate after a second command and a receive-handler
            # restart must retain the same economic fill identity exactly once.
            partial_count = case.trade.execute("SELECT COUNT(*) FROM venue_command_events WHERE event_type='PARTIAL_FILL_OBSERVED'").fetchone()[0]
            late = {**confirmed, "timestamp": str(int(case.clock[0].timestamp()*1000))}
            assert asyncio.run(stream.handle_raw_message(json.dumps(late)))["reason"] == "duplicate_trade_fact"
            restarted_stream = user_channel.PolymarketUserChannelIngestor(venue.adapter,
                [held.condition_id], auth=stream.auth, conn_factory=db.get_trade_connection_with_world)
            assert asyncio.run(restarted_stream.handle_raw_message(json.dumps(late)))["reason"] == "duplicate_trade_fact"
            assert case.trade.execute("SELECT COUNT(*) FROM venue_trade_facts WHERE state='CONFIRMED'").fetchone()[0] == 1
            assert case.trade.execute("SELECT COUNT(*) FROM venue_command_events WHERE event_type='PARTIAL_FILL_OBSERVED'").fetchone()[0] == partial_count
            # Recreate listener memory while preserving DB and durable wakes.
            # This is a consumer-memory restart, not daemon process startup.
            main._edli_initialize_reactor_wake_cursor()
            monkeypatch.setattr(main, "_forecast_exit_monitor_attempts", {})
            monkeypatch.setattr(main, "_held_position_monitor_canonical_debt", threading.Event())
            monkeypatch.setattr(main, "_held_position_monitor_canonical_last_check", 0.0)
            before_monitor = len(results)
            main._edli_reactor_wake_poll_once()
            for thread in threads:
                thread.join(15)
                assert not thread.is_alive()
            assert len(results) > before_monitor, "durable wake did not re-enter real monitor after memory reset"
            assert len(venue.posts) == 2
            resumed = case.trade.execute("SELECT shares,realized_pnl_usd FROM position_current WHERE position_id=?", (held.trade_id,)).fetchone()
            assert resumed["shares"] == 3 and float(resumed["realized_pnl_usd"]) == pytest.approx(-.04)
            record_property("restart_and_late_duplicate", json.dumps({"post_count": len(venue.posts),
                "confirmed_facts": 1, "residual_shares": resumed["shares"], "monitor_calls_after_reset": len(results)-before_monitor}))


def _register_forecast_input_queues(case, monkeypatch):
    """Retain upstream bodies/metadata, then let normal discovery build seeds."""
    from dataclasses import asdict
    from apscheduler.executors.debug import DebugExecutor
    from src.ingest import forecast_live_daemon as daemon
    from src.data import replacement_forecast_production as production
    from src.data import replacement_forecast_seed_discovery as discovery
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_forecast_current_target_plan as target_plan
    from src.data import replacement_fusion_upgrade_trigger as upgrades
    from src.data import replacement_forecast_bundle_reader as reader
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        OpenMeteoEcmwfIfs9AnchorRequest, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    )
    from src.data.raw_forecast_artifact_manifest import write_manifest, write_manifest_to_db

    request = case.native_request
    root = case.tmp_path / "production-queue"
    cfg = production._replacement_forecast_live_materialization_queue_config()
    cfg = {**cfg, "forecast_db": Path(case.forecasts.execute("PRAGMA database_list").fetchone()[2])}
    for key in ("seed_dir", "seed_processed_dir", "seed_failed_dir", "raw_manifest_dir",
                "request_dir", "inflight_dir", "processed_dir", "failed_dir"):
        cfg[key] = root / key
        cfg[key].mkdir(parents=True)
    raw = root / "native-anchor.json"
    raw.write_bytes(request.openmeteo_raw_payload_bytes)
    precision = root / "native-precision.json"
    precision.write_text(json.dumps(asdict(request.openmeteo_precision_guard.metadata), default=str))
    manifest_path = cfg["raw_manifest_dir"] / "anchor.manifest.json"
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(raw,
        request=OpenMeteoEcmwfIfs9AnchorRequest(case.city.lat, case.city.lon,
            request.source_cycle_time, case.city.timezone), metric=request.temperature_metric,
        source_available_at=request.openmeteo_source_available_at,
        captured_at=request.openmeteo_source_available_at,
        product_metadata={"city": case.city.name, "target_date": str(request.target_date),
            "openmeteo_payload_json": str(raw), "precision_metadata_json": str(precision),
            "manifest_json": str(manifest_path)})
    artifact_id = write_manifest_to_db(case.forecasts, manifest)
    case.forecasts.commit()
    write_manifest(replace(manifest, product_metadata={**manifest.product_metadata, "artifact_id": artifact_id}), manifest_path)
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: cfg)
    monkeypatch.setattr(daemon, "_replacement_forecast_last_discovery_revision", None)
    for module in (daemon, production, discovery, queue, target_plan, upgrades, reader):
        monkeypatch.setattr(module, "datetime", case.pump.clock_type)
    scheduler = case.pump.scheduler
    before = {job.id for job in scheduler.get_jobs()}
    for lane in (daemon.REPLACEMENT_FORECAST_EXECUTOR_LANE, daemon.REPLACEMENT_FORECAST_PRIORITY_EXECUTOR_LANE,
                 daemon.REPLACEMENT_FORECAST_DOWNLOAD_EXECUTOR_LANE, daemon.FORECAST_RETENTION_EXECUTOR_LANE):
        scheduler.add_executor(DebugExecutor(), alias=lane)
    daemon._register_replacement_forecast_production_jobs(scheduler)
    for job in scheduler.get_jobs():
        if job.id not in before:
            job.modify(next_run_time=None)
    return cfg


def _run_materializer_cli_on_replay_clock(case, monkeypatch):
    """Compose the resident-process boundary with the unchanged real CLI.

    Queue ownership/claims and CLI parsing, source validation, computation and
    canonical commits remain real. The OS worker transport is synchronous here
    so fixture clock/path bindings reach the child code; process crash recovery
    and daemon resident-worker lifecycle are outside this replay's coverage.
    """
    import io
    import subprocess
    from contextlib import redirect_stdout, redirect_stderr
    from scripts import materialize_replacement_forecast_live as cli
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_forecast_materialization_seed_builder as seeds
    from src.data import replacement_forecast_materialization_request_builder as requests

    for module in (cli, seeds, requests):
        monkeypatch.setattr(module, "datetime", case.pump.clock_type)
    runs = []

    def run(argv):
        assert Path(argv[1]).resolve() == Path(cli.__file__).resolve()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv[2:]))
        result = subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())
        runs.append(result)
        return result

    monkeypatch.setattr(queue, "_run_command", run)
    return runs


def _stage_coherent_hourly_inputs(case, monkeypatch):
    """Hourly trajectories reuse the daily producers' exact native bodies."""
    from src.data import day0_hourly_vectors as hourly, openmeteo_model_updates as updates
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    request = case.native_request
    captured = request.computed_at - timedelta(minutes=2)
    original_sql = sqlite3.connect(":memory:")
    case.forecasts.create_function("strftime", 2, lambda fmt, value:
        case.clock[0].isoformat(timespec="milliseconds") if (fmt, value) == ("%Y-%m-%dT%H:%M:%f+00:00", "now")
        else original_sql.execute("SELECT strftime(?,?)", (fmt, value)).fetchone()[0])
    for model in hourly.day0_hourly_models_for_city(case.city):
        if model == "ecmwf_ifs":
            body = request.openmeteo_raw_payload_bytes
        else:
            row = case.forecasts.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE source_id=? AND artifact_metadata_json LIKE '%physical_response%' ORDER BY artifact_id LIMIT 1",
                (model+"_single_runs",)).fetchone()
            assert row is not None, model
            body = Path(row[0]).read_bytes()
        payload = json.loads(body)
        api = OPENMETEO_MODEL_IDS.get(model, model)
        endpoint = "https://single-runs-api.open-meteo.com/v1/forecast"
        params = {"latitude": case.city.lat, "longitude": case.city.lon, "timezone": case.city.timezone,
            "models": api, "hourly": "temperature_2m", "run": request.source_cycle_time.isoformat()}
        identity = hourly.build_request_hash(endpoint=endpoint, params=params, models=[model],
            captured_at=captured.isoformat(), payload=payload)
        meta = hourly._day0_provider_run_meta(model=model, model_api_id=api, run=request.source_cycle_time,
            available_at=request.openmeteo_source_available_at, modified_at=request.openmeteo_source_available_at,
            authority="run_pinned_single_runs", endpoint_mode="single_runs", request_params={**params, "endpoint": endpoint},
            request_hash=identity, fetch_started_at=captured, fetch_finished_at=captured)
        vectors = hourly.parse_openmeteo_hourly_payload(payload, city=case.city, models=[model],
            captured_at=captured.isoformat(), source_run_meta_json=json.dumps(meta))
        assert hourly.persist_day0_hourly_vectors(vectors, target_date=str(request.target_date), conn=case.forecasts,
            request_hash=identity, endpoint=endpoint, now=case.clock[0]) == len(vectors)
    baseline = case.forecasts.execute("SELECT members_json,source_available_at FROM ensemble_snapshots WHERE city=? AND target_date=? AND temperature_metric='high' ORDER BY snapshot_id DESC LIMIT 1",
        (case.city.name, str(request.target_date))).fetchone()
    members = json.loads(baseline[0])
    times = [f"{request.target_date}T{hour:02d}:00" for hour in range(24)]
    payload = {"latitude": case.city.lat, "longitude": case.city.lon, "timezone": case.city.timezone,
        "utc_offset_seconds": 28800, "hourly_units": {"temperature_2m": "°C"}, "hourly": {"time": times}}
    for index, member in enumerate(members):
        key = "temperature_2m" if index == 0 else f"temperature_2m_member{index:02d}"
        payload["hourly"][key] = [member] * 24
    body = json.dumps(payload, sort_keys=True).encode()
    metadata = {"last_run_initialisation_time": request.source_cycle_time.isoformat(),
        "last_run_availability_time": baseline[1], "last_run_modification_time": baseline[1],
        "update_interval_seconds": 21600, "temporal_resolution_seconds": 3600}

    def fetch_body(url, params, **kwargs):
        if "capture_entity_body" in kwargs:
            kwargs["capture_entity_body"](body, captured.timestamp())
        if "capture_network_response" in kwargs:
            kwargs["capture_network_response"](body, captured.timestamp(), {"content-type": "application/json"})
        return json.loads(body)

    with monkeypatch.context() as native:
        native.setattr(updates, "_fetch_openmeteo", lambda *args, **kwargs: metadata)
        native.setattr("src.data.openmeteo_client.fetch", fetch_body)
        native.setattr(hourly, "_day0_utc_now", lambda: captured)
        vectors, identity = hourly.fetch_day0_source_clock_ensemble_vectors(case.city, now=captured)
    assert len(vectors) == 51
    assert hourly.persist_day0_hourly_vectors(vectors, target_date=str(request.target_date), conn=case.forecasts,
        request_hash=identity, endpoint=hourly.OPENMETEO_ENSEMBLE_URL, now=case.clock[0]) == 51
    case.forecasts.commit()


@pytest.mark.parametrize("scheduled_source", [{"forecast_inputs": True}], indirect=True)
def test_registered_source_delivery_builds_real_forecast_seed(scheduled_source, monkeypatch, record_property):
    from src.ingest import forecast_live_daemon as daemon

    case = scheduled_source
    cfg = _register_forecast_input_queues(case, monkeypatch)
    assert case.forecasts.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    case.values[0] = (26.0, 25.0)
    case.pump.enable("ingest_day0_noaa_wrh_current")
    source, = case.pump.advance()
    assert source.retval["committed"] == 1
    case.pump.enable("ingest_current_temperature_delivery", at=case.clock[0] + timedelta(seconds=1))
    case.pump.enable(daemon.REPLACEMENT_FORECAST_DISCOVERY_JOB_ID, at=case.clock[0] + timedelta(seconds=1))
    events = case.pump.advance(1)
    seeds = list(cfg["seed_dir"].glob("*.json"))
    record_property("scheduled_seed_reports", json.dumps({event.job_id: event.retval for event in events}, default=str))
    from src.data.replacement_forecast_current_target_plan import build_replacement_forecast_current_target_plan
    from dataclasses import asdict
    plan = build_replacement_forecast_current_target_plan(cfg["forecast_db"], require_raw_artifacts=False, now_utc=case.clock[0])
    record_property("scheduled_current_target_plan", json.dumps(asdict(plan), default=str))
    assert seeds, {event.job_id: event.retval for event in events}
    payloads = [json.loads(path.read_text()) for path in seeds]
    assert any(payload["city"] == case.city.name and payload["day0_observed_extreme_c"] == 26.0 for payload in payloads)


@pytest.mark.parametrize("scheduled_source", [{"forecast_inputs": True}], indirect=True)
def test_registered_priority_queue_materializes_uninjected_current_probability(scheduled_source, monkeypatch, record_property):
    from src.ingest import forecast_live_daemon as daemon

    case = scheduled_source
    cfg = _register_forecast_input_queues(case, monkeypatch)
    cli_runs = _run_materializer_cli_on_replay_clock(case, monkeypatch)
    case.values[0] = (26.0, 25.0)
    case.pump.enable("ingest_day0_noaa_wrh_current")
    case.pump.advance()
    case.pump.enable(daemon.REPLACEMENT_FORECAST_DISCOVERY_JOB_ID, at=case.clock[0] + timedelta(seconds=1))
    case.pump.advance(1)
    case.pump.enable(daemon.REPLACEMENT_FORECAST_PRIORITY_MATERIALIZE_JOB_ID, at=case.clock[0] + timedelta(seconds=1))
    reports = []
    for _ in range(3):
        reports.extend(event.retval for event in case.pump.advance(1)
            if event.job_id == daemon.REPLACEMENT_FORECAST_PRIORITY_MATERIALIZE_JOB_ID)
        if case.forecasts.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0]:
            break
    record_property("priority_queue_reports", json.dumps(reports, default=str))
    record_property("materializer_cli_results", json.dumps([
        {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr} for run in cli_runs]))
    row = case.forecasts.execute("SELECT posterior_id,q_json,computed_at,provenance_json FROM forecast_posteriors ORDER BY computed_at DESC LIMIT 1").fetchone()
    assert row is not None, {"reports": reports, "cli": [(run.returncode, run.stdout, run.stderr) for run in cli_runs]}
    q = json.loads(row["q_json"])
    assert sum(q.values()) == pytest.approx(1)
    assert all(0 <= value <= 1 for value in q.values())
    assert datetime.fromisoformat(row["computed_at"]) >= case.native_request.computed_at


@pytest.mark.parametrize("scheduled_source", [{"forecast_inputs": True}], indirect=True)
def test_scheduled_physical_revision_changes_positive_q_under_one_fixed_book(scheduled_source, monkeypatch, record_property):
    """The source/producer schedule is real; selector observation is composed."""
    from src.ingest import forecast_live_daemon as daemon
    from src.engine import monitor_refresh
    from tests.engine.test_physical_wrh_q_delivery import _select_current_wrh

    case = scheduled_source
    cfg = _register_forecast_input_queues(case, monkeypatch)
    _run_materializer_cli_on_replay_clock(case, monkeypatch)
    _stage_coherent_hourly_inputs(case, monkeypatch)
    # These market inputs are fixed before either posterior exists.
    book = {"bid": ".07", "fee": ".05", "cash": "10"}
    case.values[0] = (26.0, 25.0)
    case.pump.enable("ingest_day0_noaa_wrh_current")
    case.pump.advance()
    for job in ("ingest_current_temperature_delivery", daemon.REPLACEMENT_FORECAST_DISCOVERY_JOB_ID,
                daemon.REPLACEMENT_FORECAST_PRIORITY_MATERIALIZE_JOB_ID):
        case.pump.enable(job, at=case.clock[0]+timedelta(seconds=1))

    def current_after_queue(previous_count):
        reports = []
        for _ in range(4):
            reports.extend((event.job_id, event.retval) for event in case.pump.advance(1))
            if case.forecasts.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] > previous_count:
                break
        rows = case.forecasts.execute("SELECT posterior_id,q_json,computed_at FROM forecast_posteriors ORDER BY computed_at DESC").fetchall()
        assert len(rows) > previous_count, reports
        snapshot = monitor_refresh._build_current_global_day0_family_snapshot(case.position,
            trade_conn=case.trade, decision_time=case.clock[0], cached_snapshots=())
        held_q, _, fresh = monitor_refresh._materialize_current_global_day0_probability(case.position, snapshot)
        assert fresh
        selection = _select_current_wrh(snapshot=snapshot, position=case.position, at=case.clock[0],
            monkeypatch=monkeypatch, **book)
        return rows, held_q, selection

    before_rows, before_q, before = current_after_queue(0)
    case.values[0] = (30.0, 25.0)
    case.pump.advance(60)
    after_rows, after_q, after = current_after_queue(len(before_rows))
    record_property("fixed_book_positive_q", json.dumps({"book": book, "before_q": before_q, "after_q": after_q,
        "before_at": before_rows[0]["computed_at"], "after_at": after_rows[0]["computed_at"]}))
    assert before.candidate is None
    assert 0 < after_q < before_q < 1
    assert after.candidate is not None and after.candidate.action == "SELL"
    assert after.expected_terminal_wealth.expected_ev_usd > 0
    assert after.expected_terminal_wealth.expected_delta_log_wealth > 0


@pytest.mark.parametrize("case_name,expected", [
    ("preempted", False), ("partial", False), ("missing_write", False),
    ("covered", True), ("degraded_no_action", True), ("discharged", True), ("empty", True),
])
def test_targeted_completion_requires_the_admitted_phase_coverage(scheduled_source, monkeypatch, case_name, expected):
    """Controlled phase outcomes test completion, never action authority.

    Real bootstrap, family filtering, canonical artifact and return/claim
    handling consume a phase result. The scheduled tests above independently
    establish genuine source-derived canonical monitoring.
    """
    import threading
    from src.engine import cycle_runner
    from src.execution import exit_lifecycle
    from src.riskguard import riskguard
    from src.state import db
    from src.engine.lifecycle_events import build_entry_canonical_write

    case = scheduled_source
    if case_name == "partial":
        other = replace(case.position, trade_id="scheduled-second", token_id="1000", no_token_id="1001",
            condition_id="0x" + f"{1:064x}", market_id="0x" + f"{1:064x}", bin_label="29°C or below")
        events, projection = build_entry_canonical_write(other, phase_after="day0_window",
            decision_id="scheduled-second-initial", source_module=__name__)
        projection["phase"] = "day0_window"
        db.append_many_and_project(case.trade, events, projection)
        case.trade.commit()
    for module in (cycle_runner, exit_lifecycle, riskguard):
        monkeypatch.setattr(module, "datetime", case.pump.clock_type)
    monkeypatch.setattr(cycle_runner, "ZEUS_WORLD_DB_PATH", db.ZEUS_WORLD_DB_PATH)
    monkeypatch.setattr(cycle_runner, "_zeus_trade_db_path", db._zeus_trade_db_path)
    monkeypatch.setattr(riskguard, "RISK_DB_PATH", case.tmp_path / "risk-completion.db")
    riskguard._persist_dependency_db_locked_attestation(sqlite3.OperationalError("synthetic dependency contention"))
    monkeypatch.setattr(exit_lifecycle, "_held_monitor_clob_client", lambda: object())
    phase_summaries = []

    def phase(conn, clob, admitted, artifact, tracker, summary, **kwargs):
        ids = [position.trade_id for position in admitted.positions]
        assert sorted(ids) == ([] if case_name == "empty" else ["scheduled-held", "scheduled-second"] if case_name == "partial" else ["scheduled-held"])
        summary.update(held_monitor_candidates=len(ids), held_monitor_candidate_position_ids=ids,
            held_monitor_canonical_position_ids=ids[:1] if case_name == "partial" else [] if case_name in {"discharged", "missing_write"} else ids,
            held_monitor_discharged_position_ids=ids if case_name == "discharged" else [],
            held_monitor_no_action_authority_position_ids=ids if case_name == "degraded_no_action" else [],
            monitor_canonical_write_failed=int(case_name == "missing_write"),
            held_monitor_preempted=case_name == "preempted")
        phase_summaries.append(summary)
        return False, False

    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", phase)
    active = threading.Event()
    outcomes = []
    result = exit_lifecycle.run_exit_monitor_cycle(held_position_monitor_active=active,
        mark_held_position_monitor_complete=active.clear,
        target_families={(case.city.name, str(case.request.target_date), "low" if case_name == "empty" else "high")},
        failure_outcome_sink=outcomes.append)
    assert result is expected
    assert not active.is_set()
    assert outcomes == ([] if expected else ["COVERAGE_INCOMPLETE"])
    assert phase_summaries
    if not expected:
        assert phase_summaries[0]["monitoring_error"] == "TARGETED_MONITOR_CANONICAL_COVERAGE_INCOMPLETE"


@pytest.mark.parametrize("direction,refresh_name", [("buy_yes", "refresh_exact_zero_position"), ("buy_no", "refresh_exact_one_position")])
@pytest.mark.parametrize("fault", ["missing", "incomplete", "station", "metric", "bin", "unknown_source", "future", "tiny_probability"])
def test_exact_fast_refresh_clears_stale_receipts_without_qualified_typed_evidence(scheduled_source, monkeypatch, direction, refresh_name, fault):
    from src.engine import monitor_refresh
    from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit

    case = scheduled_source
    monkeypatch.setattr(monitor_refresh, "datetime", case.pump.clock_type)
    case.pump.enable("ingest_day0_noaa_wrh_current")
    case.pump.advance()
    position = replace(case.position, direction=direction)
    verdict = evaluate_hard_fact_exit(position=position, city=case.city, now=case.clock[0], world_conn=case.forecasts)
    assert verdict is not None and verdict.evidence is not None
    if fault == "missing":
        verdict = None
    elif fault == "incomplete":
        verdict = replace(verdict, evidence=None)
    elif fault == "station":
        verdict = replace(verdict, evidence=replace(verdict.evidence, station_id="WRONG"))
    elif fault == "metric":
        verdict = replace(verdict, metric="low")
    elif fault == "bin":
        position.bin_label = "40°C or higher"
    elif fault == "unknown_source":
        verdict = replace(verdict, source="unknown_source", evidence=replace(verdict.evidence, source="unknown_source"))
    elif fault == "future":
        verdict = replace(verdict, evidence=replace(verdict.evidence, issued_at=(case.clock[0]+timedelta(seconds=1)).isoformat()))
    elif fault == "tiny_probability":
        verdict = 0.00001
    position._monitor_probability_receipt = {"stale_sentinel": True}
    position._day0_monitor_probability_receipt = {"stale_full_sentinel": True}
    position.last_monitor_prob_is_fresh = True

    def refresh():
        if refresh_name == "refresh_exact_zero_position":
            return monitor_refresh.refresh_exact_zero_position(case.trade, None, position,
                refresh_quote=False, hard_fact_verdict=verdict)
        return monitor_refresh.refresh_exact_one_position(position, hard_fact_verdict=verdict)

    if fault == "missing":
        refresh()
    else:
        with pytest.raises(ValueError, match="DAY0_HARD_FACT_RECEIPT"):
            refresh()
        assert position.last_monitor_prob_is_fresh is False
    assert not hasattr(position, "_monitor_probability_receipt")
    assert not hasattr(position, "_day0_monitor_probability_receipt")


def _install_normal_execution_controls(case, monkeypatch, venue):
    """Synthetic process/account inputs go through real control/ledger writers."""
    import asyncio
    import hashlib
    import hmac
    from src.control import cutover_guard, heartbeat_supervisor, ws_gap_guard
    from src.execution import executor
    from src.riskguard import riskguard
    from src import risk_allocator
    from src import main
    from src.risk_allocator import governor
    from src.state import collateral_ledger, db
    from src.venue import polymarket_v2_adapter
    from src.runtime import bankroll_provider

    # Normal configure/publish owners run below. Restore process singletons on
    # fixture teardown so later absent-readiness controls stay independent.
    for module, names in ((heartbeat_supervisor, ("_GLOBAL_SUPERVISOR",)),
                          (ws_gap_guard, ("_status",)),
                          (collateral_ledger, ("_GLOBAL_LEDGER",)),
                          (governor, ("_GLOBAL_GOVERNOR", "_GLOBAL_ALLOCATOR", "_GLOBAL_GOVERNOR_STATE",
                                      "_GLOBAL_ALLOCATOR_PUBLISHED_AT_MONOTONIC", "_GLOBAL_ALLOCATOR_EVER_PUBLISHED"))):
        for name in names:
            monkeypatch.setattr(module, name, getattr(module, name))

    for module in (cutover_guard, heartbeat_supervisor, ws_gap_guard, collateral_ledger, executor, polymarket_v2_adapter, bankroll_provider):
        monkeypatch.setattr(module, "datetime", case.pump.clock_type)
    for name in vars(bankroll_provider):
        if name.startswith("_last_"):
            monkeypatch.setattr(bankroll_provider, name, getattr(bankroll_provider, name))
    monkeypatch.setenv("POLYMARKET_CLOB_V2_HOST", venue.adapter.host)
    monkeypatch.setenv("POLYMARKET_CLOB_V2_SIGNATURE_TYPE", "0")
    path = case.tmp_path / "synthetic-cutover.json"
    monkeypatch.setattr(cutover_guard, "CUTOVER_STATE_PATH", path)
    secret = "public-offline-replay-control-secret"
    monkeypatch.setenv(cutover_guard.OPERATOR_TOKEN_SECRET_ENV, secret)
    message = b"v1.synthetic-operator.scheduled-replay"
    token = message.decode() + "." + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    evidence = case.tmp_path / "synthetic-process-readiness.json"
    evidence.write_text(json.dumps({"status": "PASS", "gate_count": 17, "passed_gates": 17,
        "staged_smoke_status": "PASS", "live_deploy_authorized": False,
        "fixture_input": True, "observed_at": case.clock[0].isoformat()}))
    if case.params.get("control_fault") != "cutover_unavailable":
        for state in (cutover_guard.CutoverState.PRE_CUTOVER_FREEZE, cutover_guard.CutoverState.CUTOVER_DOWNTIME,
                      cutover_guard.CutoverState.POST_CUTOVER_RECONCILE, cutover_guard.CutoverState.LIVE_ENABLED):
            cutover_guard.transition(state, operator_token=token,
                operator_evidence_path=evidence if state is cutover_guard.CutoverState.LIVE_ENABLED else None)

    class HeartbeatTransport:
        def post_heartbeat(self, heartbeat_id):
            return SimpleNamespace(ok=True, raw={"heartbeat_id": "synthetic-public-heartbeat"})

    supervisor = heartbeat_supervisor.HeartbeatSupervisor(HeartbeatTransport())
    asyncio.run(supervisor.run_once())
    heartbeat_supervisor.configure_global_supervisor(supervisor)
    ws_gap_guard.configure_status(ws_gap_guard.WSGapStatus(gap_reason="not_configured", m5_reconcile_required=True))
    ws_gap_guard.record_message(observed_at=case.clock[0], subscription_state="SUBSCRIBED")
    ledger = collateral_ledger.CollateralLedger(db_path=db._zeus_trade_db_path())
    snapshot = ledger.refresh(venue.adapter)
    assert snapshot.authority_tier == "VENUE"
    collateral_ledger.configure_global_ledger(ledger)
    assert bankroll_provider.warm_from_collateral_snapshot() is not None
    published = risk_allocator.refresh_global_allocator(case.trade,
        ledger={"current_drawdown_pct": 0.0, "risk_level": riskguard.get_current_level()},
        heartbeat=heartbeat_supervisor.current_status(), ws_status=ws_gap_guard.summary())
    assert published["configured"] is True
    monkeypatch.setattr("src.data.polymarket_client.resolve_funder_address", lambda: venue.funder_address)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", venue.client_class)
    monkeypatch.setattr(main, "_venue_heartbeat_adapter", venue.adapter)
    return SimpleNamespace(ledger=ledger, supervisor=supervisor, allocator=published)
