# Lifecycle: created=2026-04-27; last_reviewed=2026-05-15; last_reused=2026-05-15
# Purpose: Lock R3 Z3 HeartbeatSupervisor fail-closed resting-order gate behavior.
# Reuse: Run when heartbeat supervision, executor submit gating, or R3 live-money readiness changes.
# Created: 2026-04-27
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z3.yaml
#                  + docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
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
    HeartbeatHealth,
    HeartbeatNotHealthy,
    HeartbeatSupervisor,
    OrderType,
    configure_global_supervisor,
    heartbeat_required_for,
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


def test_venue_heartbeat_refreshes_stale_global_collateral(monkeypatch, tmp_path):
    from src import main
    from src.observability import scheduler_health
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

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")
    main._venue_heartbeat_supervisor = None
    main._venue_heartbeat_adapter = None
    main._last_collateral_heartbeat_refresh_attempt_at = None

    main._write_venue_heartbeat()

    assert adapter.collateral_refreshes == 1
    snapshot = ledger.snapshot()
    assert snapshot.pusd_balance_micro == 199_396_602
    assert snapshot.pusd_allowance_micro == 9_000_000


def test_venue_heartbeat_skips_recent_global_collateral(monkeypatch, tmp_path):
    from src import main
    from src.observability import scheduler_health
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

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")
    main._venue_heartbeat_supervisor = None
    main._venue_heartbeat_adapter = None
    main._last_collateral_heartbeat_refresh_attempt_at = None

    main._write_venue_heartbeat()

    assert adapter.collateral_refreshes == 0


def test_venue_heartbeat_throttles_degraded_collateral_refresh_attempts(monkeypatch, tmp_path):
    from src import main
    from src.observability import scheduler_health
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

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")
    main._venue_heartbeat_supervisor = None
    main._venue_heartbeat_adapter = None
    main._last_collateral_heartbeat_refresh_attempt_at = None

    main._write_venue_heartbeat()
    first_snapshot = ledger.snapshot()
    main._write_venue_heartbeat()

    assert adapter.collateral_refreshes == 1
    assert first_snapshot.authority_tier == "DEGRADED"
    assert ledger.snapshot().authority_tier == "DEGRADED"


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
