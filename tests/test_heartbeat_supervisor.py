# Lifecycle: created=2026-04-27; last_reviewed=2026-05-15; last_reused=2026-05-17
# Purpose: Lock R3 Z3 HeartbeatSupervisor fail-closed resting-order gate behavior.
# Reuse: Run when heartbeat supervision, executor submit gating, or R3 live-money readiness changes.
# Created: 2026-04-27
# Last reused/audited: 2026-05-17
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z3.yaml
#                  + docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#                  + 2026-05-17 CLOB venue-heartbeat critical-path split
"""R3 Z3 HeartbeatSupervisor antibodies."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.control.heartbeat_supervisor import (
    HEARTBEAT_CANCEL_SUSPECTED_REASON,
    ExternalHeartbeatSupervisor,
    HeartbeatHealth,
    HeartbeatNotHealthy,
    HeartbeatStatus,
    HeartbeatSupervisor,
    OrderType,
    configure_global_supervisor,
    fresh_heartbeat_id_from_status,
    heartbeat_required_for,
    run_heartbeat_keeper,
    write_heartbeat_keeper_status,
)
from src.state.db import init_schema
from src.venue.polymarket_v2_adapter import HeartbeatAck


class FakeHeartbeatAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.heartbeat_ids: list[str] = []

    def post_heartbeat(self, heartbeat_id: str):
        self.heartbeat_ids.append(heartbeat_id)
        if not self.outcomes:
            return HeartbeatAck(ok=True, raw={"source": "default"})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(coro):
    return asyncio.run(coro)


def _intent() -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=0.50,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="heartbeat-market",
        token_id="heartbeat-token",
        timeout_seconds=3600,
        decision_edge=0.10,
    )


@pytest.fixture(autouse=True)
def _clear_global_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    from src.state.collateral_ledger import configure_global_ledger

    configure_global_supervisor(None)
    configure_global_ledger(None)
    yield
    configure_global_supervisor(None)
    configure_global_ledger(None)


def test_initial_state_starting_then_healthy_after_first_success():
    # Polymarket chain-token protocol: first post sends "", server returns
    # the canonical id which the supervisor must capture for the next tick.
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "session-A"})])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    assert supervisor.status().health is HeartbeatHealth.STARTING

    status = _run(supervisor.run_once())

    assert status.health is HeartbeatHealth.HEALTHY
    assert status.last_success_at is not None
    assert status.consecutive_failures == 0
    assert adapter.heartbeat_ids == [""]  # client started a fresh chain
    assert status.heartbeat_id == "session-A"  # captured server-assigned id


def test_chain_token_protocol_rotation_and_failure_resets_to_empty():
    """Antibody for F5 (smoke 2026-05-01): the supervisor must follow the
    Polymarket chain-token protocol — first post sends "", server returns
    canonical id, supervisor echoes it on next post, and on any failure
    the chain resets to "" so the next tick re-registers cleanly.

    Without this discipline the daemon repeatedly sends a fresh UUID that
    never matches the server's record, producing perpetual 400 Invalid
    Heartbeat ID and blocking GTC/GTD orders.
    """
    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"}),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-2"}),
        RuntimeError("server kicked us"),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-3"}),
    ])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    _run(supervisor.run_once())  # sends "", server returns id-1
    _run(supervisor.run_once())  # sends id-1, server returns id-2
    _run(supervisor.run_once())  # sends id-2, server fails
    _run(supervisor.run_once())  # chain reset → sends "" again

    assert adapter.heartbeat_ids == ["", "id-1", "id-2", ""], (
        "supervisor must (a) start chain with empty string, (b) echo the "
        "server-returned id on each tick, (c) reset to empty string after "
        f"any failure. Got: {adapter.heartbeat_ids!r}"
    )


def test_invalid_heartbeat_id_restarts_chain_in_same_tick():
    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"}),
        RuntimeError("PolyApiException: Invalid Heartbeat ID"),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-2"}),
    ])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    assert _run(supervisor.run_once()).health is HeartbeatHealth.HEALTHY
    recovered = _run(supervisor.run_once())

    assert recovered.health is HeartbeatHealth.HEALTHY
    assert recovered.consecutive_failures == 0
    assert recovered.heartbeat_id == "id-2"
    assert supervisor.gate_for_order_type("GTC") is True
    assert adapter.heartbeat_ids == ["", "id-1", ""]


def test_invalid_heartbeat_id_error_body_is_not_treated_as_canonical_hint():
    """RELATIONSHIP: venue invalid-id body -> heartbeat lease owner.

    A read timeout can leave the client holding the previous heartbeat id even
    though the venue processed and rotated the token. Polymarket's 400 body
    echoes the rejected id, so retrying that value extends the lease gap long
    enough to cancel resting GTC/GTD orders. Recovery must restart the chain
    with the empty bootstrap id in the same tick.
    """
    adapter = FakeHeartbeatAdapter([
        RuntimeError(
            "PolyApiException[status_code=400, "
            "error_message={'heartbeat_id': 'server-current', "
            "'error_msg': 'Invalid Heartbeat ID'}]"
        ),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "server-next"}),
    ])
    supervisor = HeartbeatSupervisor(
        adapter,
        cadence_seconds=5,
        initial_heartbeat_id="persisted-stale",
    )

    recovered = _run(supervisor.run_once())

    assert recovered.health is HeartbeatHealth.HEALTHY
    assert recovered.consecutive_failures == 0
    assert recovered.heartbeat_id == "server-next"
    assert supervisor.gate_for_order_type("GTC") is True
    assert adapter.heartbeat_ids == ["persisted-stale", ""]


def test_invalid_heartbeat_id_empty_chain_recovery_failure_loses_lease():
    adapter = FakeHeartbeatAdapter([
        RuntimeError(
            "PolyApiException[status_code=400, "
            "error_message={'heartbeat_id': 'stale-id', "
            "'error_msg': 'Invalid Heartbeat ID'}]"
        ),
        RuntimeError("empty-chain retry timed out"),
    ])
    supervisor = HeartbeatSupervisor(
        adapter,
        cadence_seconds=5,
        initial_heartbeat_id="stale-id",
    )

    lost = _run(supervisor.run_once())

    assert lost.health is HeartbeatHealth.LOST
    assert lost.consecutive_failures == 1
    assert lost.heartbeat_id == ""
    assert "empty-chain recovery failed" in (lost.last_error or "")
    assert supervisor.gate_for_order_type("GTC") is False
    assert adapter.heartbeat_ids == ["stale-id", ""]


def test_overlapping_heartbeat_ticks_do_not_reuse_same_heartbeat_id():
    class BlockingAdapter:
        def __init__(self):
            self.heartbeat_ids: list[str] = []
            self.entered: asyncio.Event | None = None
            self.release: asyncio.Event | None = None

        async def post_heartbeat(self, heartbeat_id: str):
            assert self.entered is not None
            assert self.release is not None
            self.heartbeat_ids.append(heartbeat_id)
            self.entered.set()
            await self.release.wait()
            return HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"})

    async def scenario():
        adapter = BlockingAdapter()
        adapter.entered = asyncio.Event()
        adapter.release = asyncio.Event()
        supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

        first = asyncio.create_task(supervisor.run_once())
        await adapter.entered.wait()
        skipped = await supervisor.run_once()

        assert skipped.health is HeartbeatHealth.STARTING
        assert adapter.heartbeat_ids == [""]

        adapter.release.set()
        completed = await first

        assert completed.health is HeartbeatHealth.HEALTHY
        assert adapter.heartbeat_ids == [""]

    _run(scenario())


def test_one_miss_degraded_two_misses_lost():
    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "chain-1"}),
        RuntimeError("miss-1"),
        RuntimeError("miss-2"),
    ])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    assert _run(supervisor.run_once()).health is HeartbeatHealth.HEALTHY
    degraded = _run(supervisor.run_once())
    lost = _run(supervisor.run_once())

    assert degraded.health is HeartbeatHealth.DEGRADED
    assert degraded.consecutive_failures == 1
    assert lost.health is HeartbeatHealth.LOST
    assert lost.consecutive_failures == 2
    assert "miss-2" in (lost.last_error or "")


@pytest.mark.skip(reason="auto-pause tombstone retired 2026-05-04 — _write_failclosed_tombstone is now a no-op")
def test_lost_state_writes_tombstone_with_heartbeat_cancel_suspected_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    adapter = FakeHeartbeatAdapter([RuntimeError("miss-1"), RuntimeError("miss-2")])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    _run(supervisor.run_once())
    status = _run(supervisor.run_once())

    assert status.health is HeartbeatHealth.LOST
    tombstone = tmp_path / "auto_pause_failclosed.tombstone"
    assert tombstone.read_text() == HEARTBEAT_CANCEL_SUSPECTED_REASON
    assert sorted(p.name for p in tmp_path.glob("*tombstone*")) == ["auto_pause_failclosed.tombstone"]


def test_lost_state_blocks_GTC_and_GTD_placement():
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")

    assert heartbeat_required_for("GTC") is True
    assert heartbeat_required_for(OrderType.GTD) is True
    assert supervisor.gate_for_order_type("GTC") is False
    assert supervisor.gate_for_order_type("GTD") is False


def test_lost_state_allows_FOK_FAK_immediate_only():
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")

    assert heartbeat_required_for("FOK") is False
    assert heartbeat_required_for(OrderType.FAK) is False
    assert supervisor.gate_for_order_type("FOK") is True
    assert supervisor.gate_for_order_type("FAK") is True


@pytest.mark.skip(reason="auto-pause tombstone retired 2026-05-04 — recovered heartbeat no longer persists tombstone block")
def test_recovered_heartbeat_still_blocks_resting_orders_until_tombstone_cleared():
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")
    supervisor.record_success()

    assert supervisor.status().health is HeartbeatHealth.HEALTHY
    assert supervisor.gate_for_order_type("GTC") is False
    assert supervisor.gate_for_order_type("FOK") is True


def test_executor_blocks_gtc_before_command_persistence_when_heartbeat_lost(monkeypatch):
    from src.execution.executor import _live_order

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")
    configure_global_supervisor(supervisor)

    with patch("src.data.polymarket_client.PolymarketClient") as client_cls:
        with pytest.raises(HeartbeatNotHealthy):
            _live_order("heartbeat-trade", _intent(), shares=10.0, conn=conn, decision_id="decision-heartbeat")

    assert client_cls.call_count == 0
    assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    conn.close()


def test_venue_heartbeat_scheduler_reports_failed_when_post_misses(tmp_path, monkeypatch):
    from src import main
    from src.observability import scheduler_health

    class Client:
        def _ensure_v2_adapter(self):
            return FakeHeartbeatAdapter([RuntimeError("venue-miss")])

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")
    main._venue_heartbeat_supervisor = None

    main._write_venue_heartbeat()

    data = json.loads((tmp_path / "scheduler_health.json").read_text())
    entry = data["venue_heartbeat"]
    assert entry["status"] == "FAILED"
    assert "venue heartbeat unhealthy" in entry["last_failure_reason"]
    assert main._venue_heartbeat_supervisor is not None
    assert main._venue_heartbeat_supervisor.status().health is HeartbeatHealth.DEGRADED


def test_venue_heartbeat_does_not_run_slow_background_inline(monkeypatch, tmp_path):
    from src import main
    from src.observability import scheduler_health

    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "session-A"})])

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    launched = []

    def _background(active_adapter):
        launched.append(active_adapter)
        return "started"

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("slow venue maintenance must not run inline with heartbeat")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")
    monkeypatch.setattr(main, "_run_ws_gap_reconcile_if_required", _forbidden)
    monkeypatch.setattr(main, "_refresh_global_collateral_snapshot_if_due", _forbidden)
    monkeypatch.setattr(main, "_start_venue_background_maintenance_async", _background)
    main._venue_heartbeat_supervisor = None
    main._venue_heartbeat_adapter = None

    main._write_venue_heartbeat()

    assert adapter.heartbeat_ids == [""]
    assert launched == [adapter]
    data = json.loads((tmp_path / "scheduler_health.json").read_text())
    assert data["venue_heartbeat"]["status"] == "OK"


def test_venue_heartbeat_loop_continues_after_failed_tick(monkeypatch):
    from src import main

    class StopLoop(Exception):
        pass

    calls = []
    sleeps = []

    def _heartbeat():
        calls.append("tick")
        if len(calls) == 1:
            raise RuntimeError("transient venue heartbeat failure")

    def _sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise StopLoop()

    monkeypatch.setattr(main, "_write_venue_heartbeat", _heartbeat)
    monkeypatch.setattr("time.sleep", _sleep)

    with pytest.raises(StopLoop):
        main._run_venue_heartbeat_loop(0.01)

    assert calls == ["tick", "tick"]
    assert sleeps == [0.1, 0.1]


def test_external_heartbeat_supervisor_requires_fresh_healthy_status(tmp_path):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    status = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=datetime.now(timezone.utc),
        consecutive_failures=0,
        heartbeat_id="keeper-id",
        cadence_seconds=5,
        last_error=None,
    )
    write_heartbeat_keeper_status(status, path=status_path)

    supervisor = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=8,
        cadence_seconds=5,
    )

    assert supervisor.gate_for_order_type(OrderType.GTC) is True
    assert supervisor.status().health is HeartbeatHealth.HEALTHY

    stale_payload = json.loads(status_path.read_text())
    stale_payload["written_at"] = (datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat()
    status_path.write_text(json.dumps(stale_payload))

    assert supervisor.gate_for_order_type(OrderType.GTC) is False
    assert supervisor.status().health is HeartbeatHealth.LOST
    assert "stale" in (supervisor.status().last_error or "")


def test_heartbeat_keeper_writes_status_without_order_side_effects(tmp_path):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "keeper-A"})])

    run_heartbeat_keeper(
        adapter=adapter,
        status_path=status_path,
        cadence_seconds=5,
        max_ticks=1,
    )

    payload = json.loads(status_path.read_text())
    assert payload["owner"] == "zeus-venue-heartbeat"
    assert payload["health"] == "HEALTHY"
    assert payload["heartbeat_id"] == "keeper-A"
    assert payload["last_error"] is None
    assert adapter.heartbeat_ids == [""]


def test_heartbeat_keeper_reuses_fresh_chain_id_after_restart(tmp_path):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    previous = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=datetime.now(timezone.utc),
        consecutive_failures=0,
        heartbeat_id="keeper-A",
        cadence_seconds=5,
        last_error=None,
    )
    write_heartbeat_keeper_status(previous, path=status_path)
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "keeper-B"})])

    run_heartbeat_keeper(
        adapter=adapter,
        status_path=status_path,
        cadence_seconds=5,
        max_ticks=1,
    )

    payload = json.loads(status_path.read_text())
    assert fresh_heartbeat_id_from_status(path=status_path) == "keeper-B"
    assert payload["heartbeat_id"] == "keeper-B"
    assert adapter.heartbeat_ids == ["keeper-A"]


def test_heartbeat_keeper_ignores_stale_restart_seed(tmp_path):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    previous = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        consecutive_failures=0,
        heartbeat_id="stale-id",
        cadence_seconds=5,
        last_error=None,
    )
    write_heartbeat_keeper_status(previous, path=status_path)
    payload = json.loads(status_path.read_text())
    payload["written_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    status_path.write_text(json.dumps(payload))
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "keeper-A"})])

    run_heartbeat_keeper(
        adapter=adapter,
        status_path=status_path,
        cadence_seconds=5,
        max_ticks=1,
    )

    assert adapter.heartbeat_ids == [""]


def test_main_external_venue_heartbeat_mode_consumes_status_without_posting(
    tmp_path,
    monkeypatch,
):
    from src import main

    status = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=datetime.now(timezone.utc),
        consecutive_failures=0,
        heartbeat_id="keeper-id",
        cadence_seconds=5,
    )
    write_heartbeat_keeper_status(status)
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "should-not-post"})])

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    launched_background = []
    launched_collateral = []

    def _background(active_adapter):
        launched_background.append(active_adapter)
        return "started"

    def _collateral(active_adapter):
        launched_collateral.append(active_adapter)
        return "started"

    monkeypatch.setenv("ZEUS_VENUE_HEARTBEAT_MODE", "external")
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(main, "_start_venue_background_maintenance_async", _background)
    monkeypatch.setattr(main, "_start_collateral_background_refresh_async", _collateral)
    main._venue_heartbeat_thread = None
    main._venue_heartbeat_supervisor = None
    main._venue_heartbeat_adapter = None

    main._start_venue_heartbeat_loop_if_needed()
    main._write_venue_heartbeat()

    assert main._venue_heartbeat_thread is None
    assert main._venue_heartbeat_supervisor is None
    assert launched_collateral == [adapter]
    assert launched_background == [adapter]
    assert adapter.heartbeat_ids == []


def test_main_internal_venue_heartbeat_reuses_fresh_chain_id_on_restart(
    tmp_path,
    monkeypatch,
):
    from src import main

    status_path = tmp_path / "venue-heartbeat-keeper.json"
    previous = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=datetime.now(timezone.utc),
        consecutive_failures=0,
        heartbeat_id="daemon-A",
        cadence_seconds=5,
        last_error=None,
    )
    write_heartbeat_keeper_status(previous, path=status_path, owner="zeus-live-daemon")
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "daemon-B"})])

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    monkeypatch.delenv("ZEUS_VENUE_HEARTBEAT_MODE", raising=False)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    main._venue_heartbeat_thread = None
    main._venue_heartbeat_supervisor = None
    main._venue_heartbeat_adapter = None

    main._write_venue_heartbeat()

    payload = json.loads(status_path.read_text())
    assert adapter.heartbeat_ids == ["daemon-A"]
    assert payload["owner"] == "zeus-live-daemon"
    assert payload["heartbeat_id"] == "daemon-B"


def test_main_starts_venue_heartbeat_before_boot_http():
    from src import main

    source = Path(main.__file__).read_text()
    main_body = source[source.index("def main():"):]
    heartbeat_start = main_body.index("_start_venue_heartbeat_loop_if_needed()")

    assert heartbeat_start < main_body.index("_bankroll_current()")
    assert heartbeat_start < main_body.index("_startup_wallet_check()")


def test_venue_background_maintenance_is_throttled_between_heartbeat_ticks(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    def _maintenance(active_adapter):
        calls.append(active_adapter)

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main, "_run_venue_background_maintenance_once", _maintenance)
    monkeypatch.setattr(main.threading, "Thread", InlineThread)
    main._last_venue_background_maintenance_attempt_at = None

    assert main._start_venue_background_maintenance_async(adapter) == "started"
    assert main._start_venue_background_maintenance_async(adapter) == "throttled"
    assert calls == [adapter]


def test_collateral_background_refresh_is_not_blocked_by_slow_venue_maintenance(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    def _refresh(active_adapter):
        calls.append(active_adapter)
        return True

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main, "_refresh_global_collateral_snapshot_if_due", _refresh)
    monkeypatch.setattr(main.threading, "Thread", InlineThread)
    main._last_venue_background_maintenance_attempt_at = None

    assert main._venue_background_maintenance_lock.acquire(blocking=False)
    try:
        assert main._start_venue_background_maintenance_async(adapter) == "already_running"
        assert main._start_collateral_background_refresh_async(adapter) == "started"
    finally:
        main._venue_background_maintenance_lock.release()

    assert calls == [adapter]


def test_external_heartbeat_defers_background_db_work_while_cycle_runs(monkeypatch):
    from src import main

    calls = []

    class Adapter:
        pass

    def _ensure_adapter():
        calls.append("ensure_adapter")
        return Adapter()

    monkeypatch.setattr(main, "_external_venue_heartbeat_enabled", lambda: True)
    monkeypatch.setattr(
        main,
        "_configure_external_venue_heartbeat_supervisor_if_needed",
        lambda: calls.append("configure_supervisor"),
    )
    monkeypatch.setattr(main, "_ensure_venue_read_side_adapter", _ensure_adapter)
    monkeypatch.setattr(
        main,
        "_start_collateral_background_refresh_async",
        lambda adapter: calls.append("collateral_background"),
    )
    monkeypatch.setattr(
        main,
        "_start_venue_background_maintenance_async",
        lambda adapter: calls.append("venue_background"),
    )

    assert main._cycle_lock.acquire(blocking=False)
    try:
        main._start_venue_heartbeat_loop_if_needed()
    finally:
        main._cycle_lock.release()

    assert calls == ["configure_supervisor"]


def test_venue_background_maintenance_defers_while_cycle_runs(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    monkeypatch.setattr(
        main,
        "_run_ws_gap_reconcile_if_required",
        lambda active_adapter: calls.append("ws_gap"),
    )
    monkeypatch.setattr(
        main,
        "_refresh_global_collateral_snapshot_if_due",
        lambda active_adapter: calls.append("collateral"),
    )

    assert main._cycle_lock.acquire(blocking=False)
    try:
        result = main._run_venue_background_maintenance_once(adapter)
    finally:
        main._cycle_lock.release()

    assert result == {"status": "deferred_cycle_running"}
    assert calls == []


def test_venue_background_maintenance_refreshes_stale_global_collateral(monkeypatch):
    from src import main
    from src.state.collateral_ledger import (
        CollateralLedger,
        CollateralSnapshot,
        configure_global_ledger,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=1_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc) - timedelta(seconds=31),
            authority_tier="CHAIN",
        )
    )
    configure_global_ledger(ledger)

    class Adapter(FakeHeartbeatAdapter):
        def __init__(self):
            super().__init__([HeartbeatAck(ok=True, raw={"heartbeat_id": "session-A"})])
            self.collateral_refreshes = 0

        def get_collateral_payload(self):
            self.collateral_refreshes += 1
            return {
                "pusd_balance_micro": 199_396_602,
                "pusd_allowance_micro": 9_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
            }

    adapter = Adapter()

    main._last_collateral_heartbeat_refresh_attempt_at = None

    result = main._run_venue_background_maintenance_once(adapter)

    assert result["status"] == "ok"
    assert adapter.collateral_refreshes == 1
    snapshot = ledger.snapshot()
    assert snapshot.pusd_balance_micro == 199_396_602
    assert snapshot.pusd_allowance_micro == 9_000_000


def test_venue_background_maintenance_skips_recent_global_collateral(monkeypatch):
    from src import main
    from src.state.collateral_ledger import (
        CollateralLedger,
        CollateralSnapshot,
        configure_global_ledger,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=1_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )
    configure_global_ledger(ledger)

    class Adapter(FakeHeartbeatAdapter):
        def __init__(self):
            super().__init__([HeartbeatAck(ok=True, raw={"heartbeat_id": "session-A"})])
            self.collateral_refreshes = 0

        def get_collateral_payload(self):
            self.collateral_refreshes += 1
            raise AssertionError("recent collateral snapshot should not refresh")

    adapter = Adapter()

    main._last_collateral_heartbeat_refresh_attempt_at = None

    result = main._run_venue_background_maintenance_once(adapter)

    assert result["status"] == "ok"
    assert adapter.collateral_refreshes == 0


def test_venue_background_maintenance_throttles_degraded_collateral_refresh_attempts(monkeypatch):
    from src import main
    from src.state.collateral_ledger import (
        CollateralLedger,
        CollateralSnapshot,
        configure_global_ledger,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=0,
            pusd_allowance_micro=0,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            authority_tier="DEGRADED",
        )
    )
    configure_global_ledger(ledger)

    class Adapter(FakeHeartbeatAdapter):
        def __init__(self):
            super().__init__(
                [
                    HeartbeatAck(ok=True, raw={"heartbeat_id": "session-A"}),
                    HeartbeatAck(ok=True, raw={"heartbeat_id": "session-B"}),
                ]
            )
            self.collateral_refreshes = 0

        def get_collateral_payload(self):
            self.collateral_refreshes += 1
            raise RuntimeError("simulated collateral refresh failure")

    adapter = Adapter()

    main._last_collateral_heartbeat_refresh_attempt_at = None

    main._run_venue_background_maintenance_once(adapter)
    first_snapshot = ledger.snapshot()
    main._run_venue_background_maintenance_once(adapter)

    assert adapter.collateral_refreshes == 1
    assert first_snapshot.authority_tier == "DEGRADED"
    assert ledger.snapshot().authority_tier == "DEGRADED"


def test_venue_background_m5_reconcile_defers_until_user_ws_configured():
    from src import main

    class BootingWSGuard:
        def summary(self, *, now=None):
            return {
                "connected": False,
                "subscription_state": "DISCONNECTED",
                "gap_reason": "not_configured",
                "m5_reconcile_required": True,
                "entry": {"allow_submit": False},
            }

    def fail_if_opened():
        raise AssertionError("boot-time not_configured WS must not open the live DB")

    result = main._run_ws_gap_reconcile_if_required(
        object(),
        conn_factory=fail_if_opened,
        ws_guard=BootingWSGuard(),
        now=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "status": "deferred_ws_not_ready",
        "reason": "ws_not_configured",
        "subscription_state": "DISCONNECTED",
        "gap_reason": "not_configured",
        "m5_reconcile_required": True,
    }


@pytest.mark.skip(reason="M5 exchange reconciliation owns no-resubmit proof after heartbeat loss.")
def test_recovery_does_not_duplicate_orders():
    pass


@pytest.mark.skip(reason="M5 exchange_reconcile_findings owns HEARTBEAT_CANCEL_SUSPECTED classification.")
def test_post_heartbeat_loss_reconcile_marks_local_orders_HEARTBEAT_CANCEL_SUSPECTED():
    pass


@pytest.mark.skip(reason="M5 lifecycle/quarantine truth alignment owns unquarantine-after-open-orders proof.")
def test_recovery_reads_open_orders_and_unquarantines_only_after_truth_aligns():
    pass


@pytest.mark.skip(reason="T1 fake venue integration harness owns forced 16s heartbeat-miss simulation.")
def test_forced_16s_heartbeat_miss_in_fake_venue_auto_cancels_orders_and_reconciles():
    pass
