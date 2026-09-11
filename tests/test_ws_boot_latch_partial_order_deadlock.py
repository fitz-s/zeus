# Created: 2026-06-09
# Last reused/audited: 2026-09-11
# Authority basis: 2026-06-09 ws-boot-latch deadlock incident. Three requirements formed
#   a cycle that latched submits FOREVER after any daemon restart with a resting order:
#   (1) the pong clean-boot transition (not_configured -> AUTHED) demanded an EMPTY local
#   side-effect surface (a PARTIAL GTC venue_command from 2026-06-08 blocked it);
#   (2) main's M5 clear pass defers on DISCONNECTED:not_configured (the boot state);
#   (3) ws_gap_guard.clear_after_m5_reconcile demands a healthy (pong-fed) subscription.
#   Plus the refresh leg: pongs refused to refresh while the M5 latch was armed, so the
#   guard went stale 30s after AUTHED and clear_after_m5_reconcile failed closed forever
#   ("cannot clear ws gap without healthy subscription", the 12:26Z loop).
"""RELATIONSHIP tests: pong keepalive -> M5 sweep -> ws_gap submit latch.

Cross-module invariant (polymarket_user_channel -> ws_gap_guard -> exchange_reconcile):
  TWO proofs, TWO owners. A pong proves transport+auth: it must transition the clean-boot
  latch to AUTHED and KEEP liveness fresh, but never clear submit authority while the
  local side-effect surface is non-empty. The full M5 sweep proves the surface: with a
  healthy AUTHED subscription and a zero-finding sweep it must clear submit authority —
  even with a resting PARTIAL order present. No reachable state may be un-clearable by
  the (pong stream + clean sweep) pair.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.control import ws_gap_guard
from src.execution.exchange_reconcile import (
    init_exchange_reconcile_schema,
    run_ws_gap_reconcile_and_clear,
)
from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth
from src.state.db import init_schema

NOW = datetime(2026, 6, 9, 23, 40, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_exchange_reconcile_schema(c)
    ws_gap_guard.clear_for_test(observed_at=NOW)
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="DISCONNECTED",
            gap_reason="not_configured",
            m5_reconcile_required=True,
            updated_at=NOW,
            stale_after_seconds=30,
        )
    )
    yield c
    c.close()
    ws_gap_guard.clear_for_test(observed_at=NOW)


def _ingestor(c) -> PolymarketUserChannelIngestor:
    return PolymarketUserChannelIngestor(
        adapter=object(),
        condition_ids=["condition-ws"],
        auth=WSAuth("key", "secret", "pass"),
        conn_factory=lambda: c,
        own_connection=False,
    )


def _write_current_sidecar_authority(
    tmp_path,
    *,
    now: datetime,
    m5_status: str = "OK",
    m5_success_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    heartbeat_generation: str = "price-channel-generation",
    m5_generation: str | None = None,
    canonical_held_identity_debt: str | None = None,
) -> None:
    """Write one coherent P3 heartbeat/M5 receipt pair for clean-boot tests."""

    pid = 123
    (tmp_path / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps(
            {
                "daemon": "price-channel-ingest",
                "status": "READY",
                "ready": True,
                "alive_at": (heartbeat_at or now).isoformat(),
                "pid": pid,
                "generation": heartbeat_generation,
            }
        )
    )
    business_liveness = {
        "daemon_pid": pid,
        "heartbeat_generation": m5_generation or heartbeat_generation,
        "heartbeat_receipt": "m5-receipt",
    }
    if canonical_held_identity_debt:
        business_liveness["canonical_held_identity_debt"] = (
            canonical_held_identity_debt
        )
    reconcile = {
        "status": m5_status,
        "business_liveness": business_liveness,
    }
    if m5_success_at is not None:
        reconcile["last_success_at"] = m5_success_at.isoformat()
    (tmp_path / "scheduler_jobs_health.json").write_text(
        json.dumps({"edli_user_channel_reconcile": reconcile})
    )


def _seed_partial_command(c) -> None:
    # The 2026-06-08 survivor: a PARTIAL GTC order resting across the restart.
    c.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at
        ) VALUES ('cmdpartial', 'snap', 'env', 'pos', 'dec', 'idem', 'EXIT',
                  '0xmarket', 'tok', 'SELL', '5', '0.32', '0xrest', 'PARTIAL', ?, ?)
        """,
        (NOW.isoformat(), NOW.isoformat()),
    )


class _CleanSweepAdapter:
    """Venue truth: the resting order is the ONLY open order; trades enumerable."""

    def __init__(self):
        self.read_freshness = {"open_orders": True, "trades": True, "positions": True}

    def get_open_orders(self):
        return [
            {
                "id": "0xrest",
                "market": "0xmarket",
                "asset_id": "tok",
                "side": "SELL",
                "original_size": "5",
                "size_matched": "1.65",
                "status": "LIVE",
                "order_type": "GTC",
            }
        ]

    def get_trades(self):
        return []


# ---- the deadlock, leg by leg ----------------------------------------------------------
def test_pong_with_resting_order_marks_authed_but_keeps_latch(conn) -> None:
    _seed_partial_command(conn)
    status = _ingestor(conn)._record_transport_keepalive(observed_at=NOW)
    assert status.subscription_state == "AUTHED", (
        "the pong proves transport+auth; refusing the AUTHED transition because a "
        "resting order exists is leg 1 of the boot-latch deadlock"
    )
    assert status.m5_reconcile_required is True, (
        "the pong must NOT clear submit authority — the surface is non-empty; only "
        "the M5 sweep owns that proof"
    )
    assert not status.to_summary(now=NOW)["entry"]["allow_submit"]


def test_pong_keeps_liveness_fresh_while_latched(conn) -> None:
    _seed_partial_command(conn)
    ing = _ingestor(conn)
    ing._record_transport_keepalive(observed_at=NOW)
    later = NOW + timedelta(seconds=29)
    status = ing._record_transport_keepalive(observed_at=later)
    assert status.last_message_at == later, (
        "pongs must keep refreshing liveness while the M5 latch is armed; a stale "
        "guard makes clear_after_m5_reconcile fail closed forever (12:26Z loop)"
    )
    assert status.m5_reconcile_required is True  # refresh never clears the latch
    assert not status.is_stale(now=later + timedelta(seconds=10))


def test_clean_m5_sweep_clears_latch_despite_partial_order(conn) -> None:
    _seed_partial_command(conn)
    ing = _ingestor(conn)
    ing._record_transport_keepalive(observed_at=NOW)
    result = run_ws_gap_reconcile_and_clear(
        _CleanSweepAdapter(), conn, observed_at=NOW + timedelta(seconds=5)
    )
    assert result["status"] == "cleared", result
    summary = ws_gap_guard.summary(now=NOW + timedelta(seconds=6))
    assert summary["entry"]["allow_submit"] is True, (
        "pong (channel proof) + zero-finding sweep (surface proof) must reopen "
        "submit authority even with a resting PARTIAL order — no reachable state "
        "may be un-clearable by that pair"
    )


def test_midrun_gap_reconnect_pong_marks_authed_never_fast_clears(conn) -> None:
    # Leg 5 (2026-06-09 19:20Z incident): after a REAL disconnect
    # (gap_reason=websocket_disconnect:...), the reconnected channel emits only
    # protocol pongs (quiet wallet, no data messages). The pong must transition
    # DISCONNECTED -> AUTHED so the M5 sweep can observe a healthy subscription —
    # but must NEVER fast-clear (a real gap can hide fills even with an empty
    # local surface).
    _seed_partial_command(conn)  # venue order 0xrest is OURS (known command)
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="DISCONNECTED",
            gap_reason="websocket_disconnect:ConnectionResetError",
            m5_reconcile_required=True,
            updated_at=NOW,
            stale_after_seconds=30,
        )
    )
    status = _ingestor(conn)._record_transport_keepalive(observed_at=NOW)
    assert status.subscription_state == "AUTHED"
    assert status.m5_reconcile_required is True, (
        "a real mid-run gap must never clear on a pong — even with an empty "
        "local surface; only the M5 sweep proves no fills were missed"
    )
    assert not status.to_summary(now=NOW)["entry"]["allow_submit"]
    # ...and the M5 sweep now CAN clear it (subscription healthy + clean sweep).
    result = run_ws_gap_reconcile_and_clear(
        _CleanSweepAdapter(), conn, observed_at=NOW + timedelta(seconds=5)
    )
    assert result["status"] == "cleared", result
    assert ws_gap_guard.summary(now=NOW + timedelta(seconds=6))["entry"]["allow_submit"] is True


def test_auth_failed_state_not_revived_by_pong(conn) -> None:
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="AUTH_FAILED",
            gap_reason="auth_failure_frame",
            m5_reconcile_required=True,
            updated_at=NOW,
            stale_after_seconds=30,
        )
    )
    status = _ingestor(conn)._record_transport_keepalive(observed_at=NOW)
    assert status.subscription_state == "AUTH_FAILED"
    assert status.m5_reconcile_required is True


def test_empty_surface_pong_still_full_clears(conn) -> None:
    # Regression: the original clean-boot fast path is unchanged.
    status = _ingestor(conn)._record_transport_keepalive(observed_at=NOW)
    assert status.subscription_state == "AUTHED"
    assert status.m5_reconcile_required is False
    assert status.to_summary(now=NOW)["entry"]["allow_submit"] is True


def test_order_daemon_accepts_m5_success_despite_canonical_held_identity_debt(
    conn, tmp_path, monkeypatch
) -> None:
    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    _write_current_sidecar_authority(
        tmp_path,
        now=live_now,
        m5_success_at=live_now,
        canonical_held_identity_debt="canonical_held_identity_coverage_missing",
    )

    summary = ws_gap_guard.summary(now=live_now + timedelta(seconds=5))
    assert summary["entry"]["allow_submit"] is True
    assert summary["gap_reason"] == "sidecar_durable_evidence"
    ws_gap_guard.assert_ws_allows_submit("condition-ws")


def test_user_ws_latch_ignores_market_quote_refresh_failure_when_reconcile_fresh(
    conn, tmp_path, monkeypatch
) -> None:
    """Market quote refresh failure is not a user-channel submit gap."""

    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    _write_current_sidecar_authority(
        tmp_path, now=live_now, m5_success_at=live_now
    )

    summary = ws_gap_guard.summary(now=live_now + timedelta(seconds=5))
    assert summary["entry"]["allow_submit"] is True
    assert summary["gap_reason"] == "sidecar_durable_evidence"
    ws_gap_guard.assert_ws_allows_submit("condition-ws")


@pytest.mark.parametrize("current_status", ["RUNNING", "SKIPPED"])
def test_order_daemon_keeps_fresh_reconcile_success_during_next_attempt(
    conn, tmp_path, monkeypatch, current_status
) -> None:
    """A new attempt cannot erase an unexpired successful M5 proof."""

    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    _write_current_sidecar_authority(
        tmp_path,
        now=live_now,
        m5_status=current_status,
        m5_success_at=live_now - timedelta(seconds=30),
    )

    summary = ws_gap_guard.summary(now=live_now)
    assert summary["entry"]["allow_submit"] is True
    assert summary["gap_reason"] == "sidecar_durable_evidence"


def test_order_daemon_rejects_failed_m5_even_with_recent_prior_success(
    conn, tmp_path, monkeypatch
) -> None:
    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    _write_current_sidecar_authority(
        tmp_path,
        now=live_now,
        m5_status="FAILED",
        m5_success_at=live_now - timedelta(seconds=30),
    )

    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False


def test_order_daemon_rejects_generation_mismatched_m5_receipt(
    conn, tmp_path, monkeypatch
) -> None:
    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    _write_current_sidecar_authority(
        tmp_path,
        now=live_now,
        m5_success_at=live_now,
        m5_generation="prior-price-channel-generation",
    )

    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False


def test_order_daemon_does_not_trust_running_reconcile_without_success(
    conn, tmp_path, monkeypatch
) -> None:
    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    (tmp_path / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps(
            {
                "daemon": "price-channel-ingest",
                "alive_at": live_now.isoformat(),
                "pid": 123,
            }
        )
    )
    (tmp_path / "scheduler_jobs_health.json").write_text(
        json.dumps(
            {
                "edli_user_channel_reconcile": {
                    "status": "RUNNING",
                    "last_started_at": live_now.isoformat(),
                }
            }
        )
    )

    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False


def test_order_daemon_clean_boot_latch_stays_closed_when_sidecar_evidence_stale(
    conn, tmp_path, monkeypatch
) -> None:
    import src.config as config

    live_now = datetime.now(timezone.utc)
    old = live_now - timedelta(seconds=ws_gap_guard.DURABLE_SIDECAR_STALE_AFTER_SECONDS + 1)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    (tmp_path / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps({"daemon": "price-channel-ingest", "alive_at": old.isoformat(), "pid": 123})
    )
    (tmp_path / "scheduler_jobs_health.json").write_text(
        json.dumps(
            {
                "edli_market_channel_ingestor": {"status": "OK", "last_success_at": old.isoformat()},
                "edli_user_channel_reconcile": {"status": "OK", "last_success_at": old.isoformat()},
            }
        )
    )

    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")


def test_real_midrun_ws_gap_is_not_cleared_by_sidecar_evidence(conn, tmp_path, monkeypatch) -> None:
    import src.config as config

    live_now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    (tmp_path / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps({"daemon": "price-channel-ingest", "alive_at": live_now.isoformat(), "pid": 123})
    )
    (tmp_path / "scheduler_jobs_health.json").write_text(
        json.dumps(
            {
                "edli_market_channel_ingestor": {"status": "OK", "last_success_at": live_now.isoformat()},
                "edli_user_channel_reconcile": {"status": "OK", "last_success_at": live_now.isoformat()},
            }
        )
    )
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=live_now - timedelta(seconds=10),
            subscription_state="DISCONNECTED",
            gap_reason="websocket_disconnect:ConnectionResetError",
            m5_reconcile_required=True,
            updated_at=live_now,
            stale_after_seconds=30,
        )
    )

    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")


def test_unresolved_finding_keeps_sweep_from_clearing(conn) -> None:
    # Fail-closed regression: the sweep proof requires ZERO unresolved findings.
    from src.execution.exchange_reconcile import record_finding

    _seed_partial_command(conn)
    record_finding(
        conn,
        kind="position_drift",
        subject_id="tokX",
        context="ws_gap",
        evidence={"reason": "drift"},
        recorded_at=NOW,
    )
    ing = _ingestor(conn)
    ing._record_transport_keepalive(observed_at=NOW)
    result = run_ws_gap_reconcile_and_clear(
        _CleanSweepAdapter(), conn, observed_at=NOW + timedelta(seconds=5)
    )
    assert result["status"] == "blocked"
    assert ws_gap_guard.summary(now=NOW + timedelta(seconds=6))["entry"]["allow_submit"] is False


def test_price_channel_write_deferred_is_only_emitted_before_world_mutex_acquire(
    monkeypatch,
):
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import write_coordinator

    class _BusyMutex:
        def acquire(self, *, timeout):
            assert timeout >= 0
            return False

        def release(self):
            raise AssertionError("busy mutex must not be released")

    monkeypatch.setattr(
        market_channel_ingestor, "_world_write_mutex", lambda: _BusyMutex()
    )
    with pytest.raises(lane.PriceChannelWriteDeferred) as caught:
        lane._PriceChannelWriteGate(
            owner="price_channel_user_inbox",
            scope="world",
            deadline_ms=1,
        ).__enter__()
    assert caught.value.owner == "price_channel_user_inbox"
    assert caught.value.stage == "world_mutex_pre_acquire"

    class _LeaseTimeoutCoordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **_kwargs):
            raise TimeoutError("lease timeout")
            yield

    class _FreeMutex:
        def acquire(self, *, timeout):
            assert timeout >= 0
            return True

        def release(self):
            return None

    monkeypatch.setattr(market_channel_ingestor, "_world_write_mutex", lambda: _FreeMutex())
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _LeaseTimeoutCoordinator(),
    )
    with pytest.raises(TimeoutError, match="lease timeout"):
        lane._PriceChannelWriteGate(
            owner="price_channel_user_inbox",
            scope="world",
            deadline_ms=1,
        ).__enter__()


def test_user_reconcile_pre_acquire_deferral_preserves_prior_scheduler_success(
    monkeypatch, tmp_path
):
    import src.config as config
    from src.ingest import price_channel_daemon as daemon
    from src.ingest import price_channel_ingest as lane
    import src.observability.scheduler_health as scheduler_health

    health_path = tmp_path / "scheduler_jobs_health.json"
    prior = {
        "edli_user_channel_reconcile": {
            "status": "OK",
            "last_success_at": "2026-09-11T22:00:00+00:00",
            "business_liveness": {
                "daemon_pid": 123,
                "heartbeat_generation": "generation-old",
                "heartbeat_receipt": "receipt-old",
            },
        }
    }
    health_path.write_text(json.dumps(prior))
    monkeypatch.setattr(config, "state_path", lambda _filename: health_path)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", health_path)
    deferred = lane.PriceChannelWriteDeferred(
        owner="price_channel_user_inbox",
        message="user inbox deferred",
    )
    promoted = []
    monkeypatch.setattr(
        daemon, "_promote_price_channel_heartbeat_ready", lambda: promoted.append(True)
    )
    result = daemon._scheduler_job("edli_user_channel_reconcile")(
        lambda: (_ for _ in ()).throw(deferred)
    )()

    assert result is None
    assert json.loads(health_path.read_text()) == prior
    assert promoted == []


def test_real_failure_revokes_prior_m5_and_deferral_cannot_restore_it(
    conn, monkeypatch, tmp_path
):
    """A typed pre-acquire deferral preserves an already revoked M5 proof."""

    import src.config as config
    from src.control import ws_gap_guard
    from src.ingest import price_channel_daemon as daemon
    from src.ingest import price_channel_ingest as lane
    import src.observability.scheduler_health as scheduler_health

    live_now = datetime.now(timezone.utc)
    health_path = tmp_path / "scheduler_jobs_health.json"
    monkeypatch.setattr(config, "state_path", lambda _filename: tmp_path / _filename)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", health_path)
    _write_current_sidecar_authority(
        tmp_path, now=live_now, m5_success_at=live_now
    )
    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is True

    failed = daemon._scheduler_job("edli_user_channel_reconcile")(
        lambda: (_ for _ in ()).throw(RuntimeError("real reconcile failure"))
    )()
    assert failed is None
    failed_health = json.loads(health_path.read_text())[
        "edli_user_channel_reconcile"
    ]
    assert failed_health["status"] == "FAILED"
    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False

    deferred = lane.PriceChannelWriteDeferred(
        owner="price_channel_user_inbox", message="pre-acquire"
    )
    assert daemon._scheduler_job("edli_user_channel_reconcile")(
        lambda: (_ for _ in ()).throw(deferred)
    )() is None
    assert json.loads(health_path.read_text())["edli_user_channel_reconcile"] == (
        failed_health
    )
    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False


@pytest.mark.parametrize("evidence", ("absent", "stale", "wrong_generation"))
def test_pre_acquire_deferral_does_not_create_sidecar_authority(
    conn, monkeypatch, tmp_path, evidence
):
    import src.config as config
    from src.ingest import price_channel_daemon as daemon
    from src.ingest import price_channel_ingest as lane
    import src.observability.scheduler_health as scheduler_health

    live_now = datetime.now(timezone.utc)
    health_path = tmp_path / "scheduler_jobs_health.json"
    monkeypatch.setattr(config, "state_path", lambda _filename: tmp_path / _filename)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", health_path)
    if evidence == "absent":
        (tmp_path / "daemon-heartbeat-price-channel-ingest.json").write_text(
            json.dumps(
                {
                    "daemon": "price-channel-ingest",
                    "status": "READY",
                    "ready": True,
                    "alive_at": live_now.isoformat(),
                    "pid": 123,
                    "generation": "generation-current",
                }
            )
        )
    elif evidence == "stale":
        old = live_now - timedelta(
            seconds=ws_gap_guard.DURABLE_SIDECAR_STALE_AFTER_SECONDS + 1
        )
        _write_current_sidecar_authority(tmp_path, now=live_now, m5_success_at=old)
    else:
        _write_current_sidecar_authority(
            tmp_path,
            now=live_now,
            m5_success_at=live_now,
            m5_generation="generation-prior",
        )

    deferred = lane.PriceChannelWriteDeferred(
        owner="price_channel_user_inbox", message="pre-acquire"
    )
    assert daemon._scheduler_job("edli_user_channel_reconcile")(
        lambda: (_ for _ in ()).throw(deferred)
    )() is None
    assert ws_gap_guard.summary(now=live_now)["entry"]["allow_submit"] is False


@pytest.mark.parametrize(
    ("owner", "job_name"),
    (
        ("price_channel_venue_reconcile", "edli_user_channel_reconcile"),
        ("price_channel_user_inbox", "edli_market_channel_ingestor"),
    ),
)
def test_only_user_reconcile_pre_acquire_deferral_is_silent(
    monkeypatch, owner, job_name
):
    from src.ingest import price_channel_daemon as daemon
    from src.ingest import price_channel_ingest as lane
    import src.observability.scheduler_health as scheduler_health

    writes = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job, **kwargs: writes.append({"job_name": job, **kwargs}),
    )
    deferred = lane.PriceChannelWriteDeferred(owner=owner, message="deferred")
    result = daemon._scheduler_job(job_name)(
        lambda: (_ for _ in ()).throw(deferred)
    )()

    assert result is None
    assert writes == [{"job_name": job_name, "failed": True, "reason": "deferred"}]


@pytest.mark.parametrize(
    "error",
    (
        TimeoutError("plain timeout"),
        sqlite3.OperationalError("database is locked"),
        OSError("open failed"),
        RuntimeError("commit failed"),
        ValueError("reconcile failed"),
    ),
)
def test_user_reconcile_real_failures_remain_scheduler_failed(monkeypatch, error):
    from src.ingest import price_channel_daemon as daemon
    import src.observability.scheduler_health as scheduler_health

    writes = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job, **kwargs: writes.append({"job_name": job, **kwargs}),
    )
    result = daemon._scheduler_job("edli_user_channel_reconcile")(
        lambda: (_ for _ in ()).throw(error)
    )()

    assert result is None
    assert writes == [
        {
            "job_name": "edli_user_channel_reconcile",
            "failed": True,
            "reason": str(error),
        }
    ]


def test_scheduler_failed_result_remains_failed(monkeypatch):
    from src.ingest import price_channel_daemon as daemon
    import src.observability.scheduler_health as scheduler_health

    writes = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job, **kwargs: writes.append({"job_name": job, **kwargs}),
    )
    result = daemon._scheduler_job("edli_user_channel_reconcile")(
        lambda: {
            "scheduler_failed": True,
            "scheduler_failure_reason": "business failure",
        }
    )()

    assert result["scheduler_failed"] is True
    assert writes == [
        {
            "job_name": "edli_user_channel_reconcile",
            "failed": True,
            "reason": "business failure",
            "extra": {
                "scheduler_failed": True,
                "scheduler_failure_reason": "business failure",
            },
        }
    ]


def test_price_channel_deferred_marker_is_constructed_only_at_mutex_gate():
    import ast
    from pathlib import Path

    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "ingest"
        / "price_channel_ingest.py"
    )
    tree = ast.parse(source_path.read_text())
    raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "PriceChannelWriteDeferred"
    ]
    assert len(raises) == 1
    gate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "_PriceChannelWriteGate"
    )
    assert raises[0] in ast.walk(gate)
