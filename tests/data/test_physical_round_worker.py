# Created: 2026-10-10
# Last audited: 2026-10-10
# Authority basis: docs/operations/current/plans/task_2026-10-09_physical_round.md
# Purpose: The physical-current round never runs the fusion reseed inline; one daemon worker drains the
#   pending wakes in batches and loses no key; the WORLD write is bounded by one time budget.
# Reuse: Run when _day0_current_temperature_source_tick, _physical_current_reseed_worker or
#   _physical_current_pending_wakes changes.
"""Round latency is bounded by the write, not by the reseed it triggers."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.ingest_main as ingest
from src.data import replacement_forecast_production as production
from src.data import station_temperature_adapters as adapters
from src.data.physical_current_sources import load_physical_current_sources
from src.state import db, write_coordinator as coordinator
from src.state.schema.observation_prints_schema import ensure_table

UTC = timezone.utc
KDAL = ("Dallas", "KDAL", "noaa_wrh_kdal")
KORD = ("Chicago", "KORD", "noaa_wrh_kord")
OK = {"status": "FUSION_UPGRADE_TRIGGER"}
FAILSOFT = {"status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"}


class _Lease:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def record_commit(self, **_):
        pass


@pytest.fixture
def world(monkeypatch, tmp_path):
    path = tmp_path / "world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
    seen = SimpleNamespace(conn_kwargs=[], lease_kwargs=[], mutex_timeouts=[])

    class Mutex:
        def acquire(self, timeout=-1):
            seen.mutex_timeouts.append(timeout)
            return True

        def release(self):
            pass

    def connect(**kw):
        seen.conn_kwargs.append(kw)
        return sqlite3.connect(path)

    def lease(*_a, **kw):
        seen.lease_kwargs.append(kw)
        return _Lease()

    monkeypatch.setattr(db, "world_write_mutex", lambda: Mutex())
    monkeypatch.setattr(db, "get_world_connection", connect)
    monkeypatch.setattr(coordinator, "default_runtime_write_coordinator",
                        lambda: SimpleNamespace(lease=lease))
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: {})
    monkeypatch.setattr("src.data.physical_current_delivery.current_temperature_priority_families", lambda: {})
    monkeypatch.setattr(ingest, "_physical_current_pending_wakes", set())
    monkeypatch.setattr(ingest, "_physical_current_reseed_thread", None)
    seen.path = path
    yield seen
    worker = ingest._physical_current_reseed_thread
    if worker is not None:
        worker.join(10)


def _join_worker():
    worker = ingest._physical_current_reseed_thread
    if worker is not None:
        worker.join(10)
        assert not worker.is_alive()


def _route(station="KDAL"):
    return next(r for r in load_physical_current_sources()[0]
                if r.provider == "noaa_wrh" and r.station_id == station)


def _tick(monkeypatch, prints=None):
    from src.config import cities_by_name
    now = datetime.now(UTC)
    route = _route()
    if prints is None:
        prints = (adapters._sample(route, now - timedelta(minutes=2), 71.0, now, "a" * 64),)
    monkeypatch.setattr(adapters, "fetch_station_temperature", lambda *a, **k: prints)
    return ingest._day0_current_temperature_source_tick(cities_by_name["Dallas"], route)


def _rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT count(*) FROM observation_prints").fetchone()[0]


def test_round_makes_no_inline_enqueue(world, monkeypatch):
    callers = []
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed",
                        lambda cfg, **kw: callers.append(threading.current_thread()) or OK)
    result = _tick(monkeypatch)
    _join_worker()
    assert result["status"] == "COMMITTED" and result["advanced"]
    assert result["clock_trace"]["enqueue_status"] == "DEFERRED_TO_RESEED_WORKER"
    assert "world_to_enqueue_return_ms" not in result["clock_trace"]
    assert [c.name for c in callers] == ["physical-current-reseed"]
    assert callers[0].daemon and callers[0] is not threading.main_thread()
    assert not ingest._physical_current_pending_wakes


def test_round_returns_while_the_reseed_is_still_running(world, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked(cfg, **kw):
        entered.set()
        release.wait(10)
        return OK

    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", blocked)
    result = _tick(monkeypatch)
    assert result["status"] == "COMMITTED"
    assert entered.wait(10) and ingest._physical_current_reseed_thread.is_alive()
    assert ingest._physical_current_pending_wakes == {KDAL}
    release.set()
    _join_worker()
    assert not ingest._physical_current_pending_wakes


def test_key_added_during_a_batch_survives_into_the_next_batch(world, monkeypatch):
    entered, release, calls = threading.Event(), threading.Event(), []

    def enqueue(cfg, **kw):
        calls.append({scope[0] for scope in kw["scopes"]})
        if len(calls) == 1:
            entered.set()
            release.wait(10)
        return OK

    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", enqueue)
    _tick(monkeypatch)
    assert entered.wait(10)
    with ingest._physical_current_reseed_lock:
        ingest._physical_current_pending_wakes.add(KORD)
    ingest._defer_physical_current_reseed()
    release.set()
    _join_worker()
    assert calls == [{"Dallas"}, {"Chicago"}]
    assert not ingest._physical_current_pending_wakes


def test_batch_exception_keeps_every_key(world, monkeypatch):
    def boom(cfg, **kw):
        raise RuntimeError("enqueue down")

    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", boom)
    ingest._physical_current_pending_wakes.update({KDAL, KORD})
    ingest._defer_physical_current_reseed()
    _join_worker()
    assert ingest._physical_current_pending_wakes == {KDAL, KORD}
    assert ingest._physical_current_reseed_thread is None


def test_failsoft_report_keeps_every_key_then_next_signal_retries(world, monkeypatch):
    reports = [FAILSOFT, OK]
    calls = []
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed",
                        lambda cfg, **kw: calls.append(kw["scopes"]) or reports.pop(0))
    ingest._physical_current_pending_wakes.update({KDAL, KORD})
    ingest._defer_physical_current_reseed()
    _join_worker()
    assert ingest._physical_current_pending_wakes == {KDAL, KORD} and len(calls) == 1
    ingest._defer_physical_current_reseed()
    _join_worker()
    assert not ingest._physical_current_pending_wakes and len(calls) == 2
    assert {s[0] for s in calls[1]} == {"Dallas", "Chicago"}


def test_one_batch_makes_one_enqueue_over_the_union_of_scopes(world, monkeypatch):
    calls = []
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed",
                        lambda cfg, **kw: calls.append(kw) or OK)
    ingest._physical_current_pending_wakes.update({KDAL, KORD})
    ingest._defer_physical_current_reseed()
    _join_worker()
    assert len(calls) == 1
    assert {s[0] for s in calls[0]["scopes"]} == {"Dallas", "Chicago"}
    assert calls[0]["changed_sources"] == ("day0_current_temperature_state",)


def test_write_budget_expiry_defers_and_the_retry_commits_once(world, monkeypatch):
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg, **kw: OK)
    real_connect = db.get_world_connection

    def expired(**kw):
        world.conn_kwargs.append(kw)
        raise TimeoutError("DB_CONNECTION_DEADLINE_EXPIRED")

    monkeypatch.setattr(db, "get_world_connection", expired)
    now = datetime.now(UTC)
    prints = (adapters._sample(_route(), now - timedelta(minutes=2), 71.0, now, "a" * 64),)
    assert _tick(monkeypatch, prints) == {"status": "WRITE_DEFERRED"}
    assert _rows(world.path) == 0 and not ingest._physical_current_pending_wakes
    monkeypatch.setattr(db, "get_world_connection", real_connect)
    first = _tick(monkeypatch, prints)
    _join_worker()
    again = _tick(monkeypatch, prints)
    assert (first["inserted"], again["inserted"]) == (1, 0)
    assert _rows(world.path) == 1


def test_one_budget_bounds_mutex_wait_lease_and_connect(world, monkeypatch):
    monkeypatch.setattr(production, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg, **kw: OK)
    _tick(monkeypatch)
    _join_worker()
    (mutex_s,), (lease,), (conn,) = world.mutex_timeouts, world.lease_kwargs, world.conn_kwargs
    budget_ms = 1000 * ingest._PHYSICAL_CURRENT_WRITE_BUDGET_S
    assert mutex_s * 1000 <= budget_ms
    assert lease["deadline_ms"] <= budget_ms
    assert conn["busy_timeout_ms"] <= budget_ms
    assert conn["deadline_monotonic"] is not None
