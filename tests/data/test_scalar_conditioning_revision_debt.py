# Created: 2026-10-09
# Last reused/audited: 2026-10-09
# Authority basis: offline physical-evidence producer debt repair; INV-47 RESET.
"""Real scalar materialization must retire only its satisfiable producer debt."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import hashlib
import sqlite3
from zoneinfo import ZoneInfo
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from src.data import replacement_fusion_upgrade_trigger as trigger
from src.data import replacement_forecast_live_materialization_queue as queue
from src.data import replacement_forecast_materializer as materializer
from src.data import replacement_forecast_seed_discovery as discovery
from src.data.station_ground_evidence import forecast_db_from_connection
from tests import test_replacement_forecast_materializer as inputs
from tests.test_replacement_forecast_materializer import (  # noqa: F401
    _hko_source_surface, _hko_native_surfaces,
)

UTC = timezone.utc
SCALAR = "day0_scalar_conditioning"
CURRENT = "day0_current_temperature_state"


def _payload(request):
    return {name: getattr(request, name) for name in (
        "day0_observed_extreme_c", "day0_observed_extreme_source",
        "day0_observed_extreme_observation_time", "day0_observed_extreme_sample_count",
        "day0_observed_extreme_unit",
    )}


def _publish_wrh(conn, request, *, values, at):
    from src.config import runtime_cities_by_name
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data import noaa_wrh_timeseries as wrh
    observed = datetime.fromisoformat(request.day0_observed_extreme_observation_time)
    start = datetime.combine(request.target_date, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    body = json.dumps({"SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Celsius"},
        "STATION": [{"STID": "ZSPD", "OBSERVATIONS": {
            "date_time": [stamp.isoformat() for stamp in (observed-timedelta(minutes=1), observed)],
            "air_temp_set_1": list(values), "sea_level_pressure_set_1": [1010, 1010],
        }}]}).encode()
    product = wrh.product_from_response(body, "ZSPD", unit="C", fetched_at=at,
        source_response_sha256=hashlib.sha256(body).hexdigest())
    product = replace(product, request_started_at=at-timedelta(seconds=1),
        coverage_start_utc=start.astimezone(UTC)-timedelta(hours=1),
        coverage_end_utc=at-timedelta(seconds=1))
    result = append_current_noaa_wrh_product(conn, city=runtime_cities_by_name()[request.city],
        target_date=str(request.target_date), product=product, as_of=at)
    conn.commit()
    return result


@pytest.fixture
def scalar_case(tmp_path, monkeypatch, _hko_source_surface):
    now = datetime.now(UTC).replace(microsecond=0)
    target = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    start = datetime.combine(target, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    cycle = start.astimezone(UTC) - timedelta(hours=4)
    conn, request = inputs._shanghai_current_owner_request(
        tmp_path, monkeypatch, target_date=target, source_cycle_time=cycle,
        computed_at=now, observed_extreme=30.0, observed_sample_count=2,
    )
    db = forecast_db_from_connection(conn)
    assert db is not None
    from tests.test_noaa_wrh_settlement_product import _live_schema_db_pair
    owner_dir = tmp_path / "observation-owner"
    owner_dir.mkdir()
    _, world = _live_schema_db_pair(owner_dir)
    from src.state.schema.observation_prints_schema import ensure_table
    with sqlite3.connect(world) as ledger:
        ensure_table(ledger)
    conn.execute("ATTACH DATABASE ? AS world", (str(world),))
    monkeypatch.setattr("src.state.db.ZEUS_FORECASTS_DB_PATH", db)
    monkeypatch.setattr("src.state.db.ZEUS_WORLD_DB_PATH", world)
    def read_world(**_kwargs):
        reader = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
        reader.execute("ATTACH DATABASE ? AS forecasts", (f"file:{db}?mode=ro",))
        return reader
    monkeypatch.setattr("src.state.db.get_world_connection_read_only", read_world)
    monkeypatch.setattr(discovery, "get_world_connection_read_only", read_world)
    assert _publish_wrh(conn, request, values=(26.0, 30.0), at=now) == "inserted"
    # Verify canonical selection from the real WRH owner. Keep discovery live
    # so later corrections exercise rediscovery, not a frozen fixture payload.
    payload = _payload(request)
    canonical = discovery._day0_observed_extreme_seed_payload(city=request.city,
        target_date=str(target), metric=request.temperature_metric, computed_at=now)
    assert canonical is not None
    assert {key: canonical[key] for key in payload} == payload
    result = materializer.materialize_replacement_forecast_live(conn, request)
    assert result.ok, result.reason_codes
    conn.commit()
    seed = {"city": request.city, "target_date": str(request.target_date),
        "temperature_metric": request.temperature_metric,
        "source_cycle_time": request.source_cycle_time.isoformat(),
        "computed_at": request.computed_at.isoformat(),
        "baseline_source_run_id": request.baseline_source_run_id,
        "openmeteo_source_run_id": request.openmeteo_source_run_id, **payload}
    assert queue._seed_already_covered(forecast_db=db, seed=seed)
    provenance = json.loads(conn.execute("SELECT provenance_json FROM forecast_posteriors").fetchone()[0])
    assert CURRENT not in provenance
    state = trigger._capturable_current_temperature_state(city=request.city,
        target_date=str(request.target_date), decision_time=now)
    assert state is not None and state["source"].startswith("noaa_wrh_")
    try:
        yield conn, request, payload, seed
    finally:
        conn.close()


def _verdict(conn, request, *, payload=None):
    return trigger.scope_capture_offers_larger_provider_set(conn,
        city=request.city, target_date=str(request.target_date),
        metric=request.temperature_metric, changed_sources=(CURRENT,),
        decision_time=request.computed_at, day0_payload=payload)


def test_identical_wrh_after_real_scalar_commit_resets(scalar_case):
    conn, request, _, _ = scalar_case
    verdict = _verdict(conn, request)
    assert verdict["changed_input_sources"] == []
    assert not verdict["is_upgrade"]
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_same_clock_downward_scalar_correction_retains_fast_debt(scalar_case, monkeypatch):
    conn, request, payload, seed = scalar_case
    request = replace(request, computed_at=request.computed_at+timedelta(seconds=1),
        day0_observed_extreme_c=26.0)
    assert _publish_wrh(conn, request, values=(25.0, 26.0), at=request.computed_at) == "revision"
    payload["day0_observed_extreme_c"] = 26.0
    verdict = _verdict(conn, request)
    assert verdict["changed_input_sources"] == [SCALAR]
    assert CURRENT not in verdict["changed_input_revisions"]
    request = inputs._refresh_shanghai_owner_request(conn, monkeypatch, request, record_observed_prints=False)
    result = materializer.materialize_replacement_forecast_live(conn, request)
    assert result.ok, result.reason_codes
    conn.commit()
    assert queue._seed_already_covered(forecast_db=forecast_db_from_connection(conn),
        seed={**seed, **payload, "computed_at": request.computed_at.isoformat()})
    assert _verdict(conn, request)["changed_input_sources"] == []


def test_revision_only_current_source_changes_without_scalar_debt(scalar_case, tmp_path, monkeypatch):
    conn, request, payload, _ = scalar_case
    from src.config import runtime_cities_by_name
    from src.data import physical_current_delivery as delivery
    from src.runtime import reactor_wake as wakes
    from src.engine import monitor_refresh as monitor
    from src import main

    city = runtime_cities_by_name()[request.city]
    family = (request.city, str(request.target_date), request.temperature_metric)
    from src import config
    state_path = config.state_path
    monkeypatch.setattr(config, "state_path", lambda name: (
        tmp_path/"wake-state"/name
        if name in {delivery._PUBLICATION_FILE, "edli-reactor-wake.json"} else state_path(name)
    ))
    first_delivery = delivery.publish_current_temperature_wakes(
        cities=(city,), scopes=(family,), now=request.computed_at)
    assert first_delivery["published"] == 1
    before = trigger._capturable_current_temperature_state(city=request.city,
        target_date=str(request.target_date), decision_time=request.computed_at)
    later = replace(request, computed_at=request.computed_at+timedelta(seconds=1))
    assert _publish_wrh(conn, later, values=(25.0, 30.0), at=later.computed_at) == "revision"
    after = trigger._capturable_current_temperature_state(city=request.city,
        target_date=str(request.target_date), decision_time=later.computed_at)
    assert before != after
    assert before["value_native"] == after["value_native"]
    assert before["observed_at_utc"] == after["observed_at_utc"]
    assert _verdict(conn, later)["changed_input_sources"] == []
    revised_delivery = delivery.publish_current_temperature_wakes(
        cities=(city,), scopes=(family,), now=later.computed_at)
    assert revised_delivery["published"] == 1
    queued = wakes.reactor_wakes_since(None)
    assert len(queued) == 2
    assert all(wake.forecast_families == (family,) for wake in queued)
    assert delivery.publish_current_temperature_wakes(
        cities=(city,), scopes=(family,), now=later.computed_at)["published"] == 0

    # Actual durable wake owner must dispatch the held monitor and retain its
    # debt. Stop at the real current-source reread, without manufacturing an
    # economic decision or falsely acknowledging monitor completion.
    seen = []
    position = SimpleNamespace(city=request.city, target_date=str(request.target_date),
        temperature_metric=request.temperature_metric)
    def monitor_dispatch(ids, families, **_kwargs):
        assert family in families
        event = monitor._current_wrh_monitor_observation_carrier(conn, position, now=later.computed_at)
        seen.append((ids, json.loads(event.payload_json)))
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda families: frozenset(families))
    monkeypatch.setattr(main, "_forecast_exit_monitor_attempt_state", lambda _: (False, None))
    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", monitor_dispatch)
    monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)
    assert main._edli_reactor_wake_poll_once() is False
    assert seen and seen[0][1]["raw_report_identity"] == after["source_revision_identity"]
    assert seen[0][1]["raw_value"] == payload["day0_observed_extreme_c"]
    assert {wake.wake_id for wake in wakes.reactor_wakes_since(None)} == {wake.wake_id for wake in queued}


def test_scalar_publication_aba_restart_and_duplicates(scalar_case, tmp_path, monkeypatch):
    conn, request, payload, seed = scalar_case
    # Publication/CAS test seam only: scalar q and its RESET still come from
    # the real materializer in the neighbouring relationship tests.
    built = []
    monkeypatch.setattr(discovery, "_day0_observed_extreme_seed_payload", lambda **_: dict(payload))
    def build(_conn, **kw):
        assert kw["current_temperature_state"] is None
        assert kw["input_revision_sources"] == (SCALAR,)
        path = Path(kw["seed_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {**seed, **kw["day0_payload"], "input_revision_sources": [SCALAR],
            "upgrade_trigger": "instrument_set_expansion"}
        path.write_text(json.dumps(body))
        built.append(path)
        return path
    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", build)
    kwargs = dict(forecast_db=forecast_db_from_connection(conn), seed_dir=tmp_path/"queue"/"seeds",
        raw_manifest_dir=tmp_path/"raw", scopes=[(request.city, str(request.target_date), "high")],
        changed_sources=[CURRENT], manifests=(), computed_at=request.computed_at)
    for extreme in (26.0, 27.0, 26.0):
        payload["day0_observed_extreme_c"] = extreme
        first = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
        assert first["seeds_enqueued"] == 1, first
        path = Path(first["enqueued"][0]["seed_file"])
        assert ".station-input-revision." in path.name
        duplicate = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
        assert duplicate["seeds_enqueued"] == 0 and duplicate["already_enqueued"] == 1
        # Consumer completion without convergence leaves no active queue file.
        # Next enqueue uses a fresh owner connection and reclaims the same A key.
        path.unlink()
    assert len(built) == 3
    assert conn.execute("SELECT COUNT(*) FROM fusion_upgrade_enqueues").fetchone()[0] == 2


@pytest.mark.parametrize("fault", ["expired", "unready", "stale_posterior", "future_posterior", "missing", "nan"])
def test_scalar_reset_requires_valid_conditioning_and_live_readiness(scalar_case, fault):
    conn, request, payload, _ = scalar_case
    if fault == "expired":
        conn.execute("UPDATE readiness_state SET expires_at = ?", ((request.computed_at-timedelta(seconds=1)).isoformat(),))
    elif fault == "unready":
        conn.execute("UPDATE readiness_state SET status = 'BLOCKED'")
    elif fault in {"stale_posterior", "future_posterior"}:
        offset = timedelta(hours=-30 if fault == "stale_posterior" else 1)
        conn.execute("UPDATE forecast_posteriors SET computed_at = ?",
            ((request.computed_at+offset).isoformat(),))
    elif fault == "missing":
        payload.pop("day0_observed_extreme_source")
    else:
        payload["day0_observed_extreme_c"] = float("nan")
    conn.commit()
    assert SCALAR in _verdict(conn, request, payload=payload)["changed_input_sources"]


@pytest.mark.parametrize("metric", ["high", "low"])
def test_carrier_current_state_real_commit_and_coverage_reset(tmp_path, monkeypatch, _hko_source_surface, metric):
    conn, request = inputs._shanghai_noaa_future_request(tmp_path, monkeypatch, metric=metric)
    db = forecast_db_from_connection(conn)
    def read_world():
        reader = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        reader.execute("ATTACH DATABASE ? AS forecasts", (f"file:{db}?mode=ro",))
        return reader
    monkeypatch.setattr("src.state.db.get_world_connection_read_only", read_world)
    monkeypatch.setattr(discovery, "_day0_observed_extreme_seed_payload", lambda **_: _payload(request))
    original_reader = queue._queue_read_only_connection
    sql_clock = sqlite3.connect(":memory:")
    def read_queue(path):
        reader = original_reader(path)
        # This public carrier fixture is dated Oct 2. Bind only SQLite's now
        # to its declared event cut, retaining native formatting/comparisons.
        reader.create_function("strftime", 2, lambda fmt, value: sql_clock.execute(
            "SELECT strftime(?, ?)", (fmt, request.computed_at.isoformat() if value == "now" else value),
        ).fetchone()[0])
        return reader
    monkeypatch.setattr(queue, "_queue_read_only_connection", read_queue)
    try:
        state = trigger._capturable_current_temperature_state(city=request.city,
            target_date=str(request.target_date), decision_time=request.computed_at)
        assert state is not None
        before = _verdict(conn, request)
        assert before["changed_input_revisions"][CURRENT] == state
        result = materializer.materialize_replacement_forecast_live(conn, request)
        assert result.ok, result.reason_codes
        conn.commit()
        assert _verdict(conn, request)["changed_input_sources"] == []
        seed = {"city": request.city, "target_date": str(request.target_date),
            "temperature_metric": metric, "computed_at": request.computed_at.isoformat(),
            "baseline_source_run_id": request.baseline_source_run_id,
            "openmeteo_source_run_id": request.openmeteo_source_run_id,
            CURRENT: state, **_payload(request)}
        assert queue._seed_already_covered(forecast_db=db, seed=seed)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return request.computed_at.astimezone(tz or UTC)
        monkeypatch.setattr(queue, "datetime", Clock)
        expansion = {**seed, "upgrade_trigger": "instrument_set_expansion",
            "source_cycle_time": request.source_cycle_time.isoformat()}
        assert queue._instrument_set_expansion_already_applied(forecast_db=db, payload=expansion)
        with monkeypatch.context() as unavailable:
            unavailable.setattr(discovery, "_day0_observed_extreme_seed_payload", lambda **_: None)
            assert _verdict(conn, request)["is_upgrade"], "unavailable conditioning cannot close carrier debt"
            assert not queue._instrument_set_expansion_already_applied(forecast_db=db, payload=expansion)
        assert queue._instrument_set_expansion_already_applied(forecast_db=db, payload=expansion)
    finally:
        conn.close()
        sql_clock.close()
