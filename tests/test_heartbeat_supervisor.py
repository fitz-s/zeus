# Lifecycle: created=2026-04-27; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: Lock R3 Z3 HeartbeatSupervisor fail-closed resting-order gate behavior.
# Reuse: Run when heartbeat supervision, executor submit gating, or R3 live-money readiness changes.
# Created: 2026-04-27
# Last reused/audited: 2026-07-19
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z3.yaml
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#                  + 2026-05-17 CLOB venue-heartbeat critical-path split
"""R3 Z3 HeartbeatSupervisor antibodies."""

from __future__ import annotations

import asyncio
import contextlib
import json
import plistlib
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.control.heartbeat_supervisor import (
    DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS,
    ExternalHeartbeatSupervisor,
    HeartbeatHealth,
    HeartbeatNotHealthy,
    HeartbeatStatus,
    HeartbeatSupervisor,
    heartbeat_retry_delay_seconds,
    OrderType,
    configure_global_supervisor,
    fresh_heartbeat_id_from_status,
    heartbeat_cadence_seconds_from_env,
    heartbeat_http_timeout_seconds_from_env,
    heartbeat_required_for,
    install_dedicated_heartbeat_http_timeout,
    recover_missing_live_trading_launchd_if_needed,
    run_heartbeat_keeper,
    write_heartbeat_keeper_status,
    _describe_heartbeat_exception,
)
import src.control.heartbeat_supervisor as heartbeat_supervisor_module
from src.state.db import init_schema
from src.venue.polymarket_v2_adapter import HeartbeatAck
import src.data.substrate_observer as substrate_observer

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENUE_HEARTBEAT_PLIST = _REPO_ROOT / "deploy" / "launchd" / "com.zeus.venue-heartbeat.plist"


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


def test_venue_heartbeat_launchd_artifact_has_explicit_clob_signature_type() -> None:
    assert _VENUE_HEARTBEAT_PLIST.exists(), (
        "deploy/launchd/com.zeus.venue-heartbeat.plist artifact must exist; "
        "the live order gate must not depend on a hand-maintained local plist."
    )
    with _VENUE_HEARTBEAT_PLIST.open("rb") as fh:
        parsed = plistlib.load(fh)

    assert parsed.get("Label") == "com.zeus.venue-heartbeat"
    assert "src.control.heartbeat_supervisor" in parsed.get("ProgramArguments", [])
    env = parsed.get("EnvironmentVariables") or {}
    assert env.get("POLYMARKET_CLOB_V2_SIGNATURE_TYPE") == "2"


def _write_sidecar_heartbeats(state_root: Path, *, sha: str, at: datetime) -> None:
    rows = {
        "forecast-live-heartbeat.json": {"git_head": sha, "written_at": at.isoformat()},
        "daemon-heartbeat-substrate-observer.json": {"git_head": sha, "timestamp": at.isoformat()},
        "daemon-heartbeat-price-channel-ingest.json": {"git_head": sha, "timestamp": at.isoformat()},
        "daemon-heartbeat-post-trade-capital.json": {"git_head": sha, "timestamp": at.isoformat()},
    }
    for name, payload in rows.items():
        (state_root / name).write_text(json.dumps(payload))


def test_live_trading_launchd_watchdog_bootstraps_when_sidecars_are_fresh(
    tmp_path,
    monkeypatch,
) -> None:
    """A missing src.main service is a liveness fault, not a passive alert.

    The venue-heartbeat sidecar may bootstrap live-trading only after it proves
    active launchd config, not-disabled state, and fresh sidecar heartbeats. Code
    identity remains evidence; it is not market or process-liveness authority.
    """

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_GUI_DOMAIN", "gui/501")
    sha = "a" * 40
    now = datetime(2026, 7, 2, 19, 50, tzinfo=timezone.utc)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write_sidecar_heartbeats(state_root, sha=sha, at=now)
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_text("<plist/>")
    status_path = tmp_path / "watchdog.json"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=3, stdout="", stderr="not found")
        if cmd[:2] == ["launchctl", "print-disabled"]:
            return SimpleNamespace(
                returncode=0,
                stdout='"com.zeus.live-trading" => enabled\n',
                stderr="",
            )
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    result = recover_missing_live_trading_launchd_if_needed(
        now=now,
        run_cmd=fake_run,
        repo_root=tmp_path,
        state_root=state_root,
        plist_path=plist,
        status_path=status_path,
    )

    assert result["ok"] is True
    assert result["action"] == "bootstrapped"
    assert calls[-1] == ["launchctl", "bootstrap", "gui/501", str(plist)]
    assert json.loads(status_path.read_text())["action"] == "bootstrapped"


def test_live_trading_watchdog_yields_to_deploy_restart_lock(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZEUS_MODE", "live")
    state_root = tmp_path / "state"
    lock_path = (
        state_root
        / "locks"
        / heartbeat_supervisor_module.LIVE_RESTART_LOCK_FILENAME
    )
    lock_path.parent.mkdir(parents=True)
    fd = heartbeat_supervisor_module.os.open(
        lock_path,
        heartbeat_supervisor_module.os.O_RDWR
        | heartbeat_supervisor_module.os.O_CREAT,
        0o644,
    )
    heartbeat_supervisor_module.fcntl.flock(
        fd,
        heartbeat_supervisor_module.fcntl.LOCK_EX,
    )
    calls = []
    try:
        result = recover_missing_live_trading_launchd_if_needed(
            run_cmd=lambda *args, **kwargs: calls.append((args, kwargs)),
            state_root=state_root,
            status_path=tmp_path / "watchdog.json",
        )
    finally:
        heartbeat_supervisor_module.fcntl.flock(
            fd,
            heartbeat_supervisor_module.fcntl.LOCK_UN,
        )
        heartbeat_supervisor_module.os.close(fd)

    assert result["ok"] is True
    assert result["action"] == "none"
    assert result["reason"] == "deploy_restart_in_progress"
    assert calls == []


def test_live_trading_launchd_watchdog_observes_identity_drift_without_blocking(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_GUI_DOMAIN", "gui/501")
    now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write_sidecar_heartbeats(state_root, sha="b" * 40, at=now)
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_text("<plist/>")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=3, stdout="", stderr="not found")
        if cmd[:2] == ["launchctl", "print-disabled"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=f"{'a' * 40}\n", stderr="")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    result = recover_missing_live_trading_launchd_if_needed(
        now=now,
        run_cmd=fake_run,
        repo_root=tmp_path,
        state_root=state_root,
        plist_path=plist,
        status_path=tmp_path / "watchdog.json",
    )

    assert result["ok"] is True
    assert result["action"] == "bootstrapped"
    assert len(result["identity_observations"]) == 4


def test_live_trading_launchd_watchdog_git_unavailable_does_not_block_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_GUI_DOMAIN", "gui/501")
    now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write_sidecar_heartbeats(state_root, sha="b" * 40, at=now)
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_text("<plist/>")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=3, stdout="", stderr="not found")
        if cmd[:2] == ["launchctl", "print-disabled"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            raise FileNotFoundError("git unavailable")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    result = recover_missing_live_trading_launchd_if_needed(
        now=now,
        run_cmd=fake_run,
        repo_root=tmp_path,
        state_root=state_root,
        plist_path=plist,
        status_path=tmp_path / "watchdog.json",
    )

    assert result["ok"] is True
    assert result["action"] == "bootstrapped"
    assert result["git_identity_error"].startswith("git rev-parse unavailable")


def test_live_trading_launchd_watchdog_reports_loaded_but_failed_service(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_GUI_DOMAIN", "gui/501")
    status_path = tmp_path / "watchdog.json"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "state = spawn scheduled\n"
                    "active count = 0\n"
                    "last exit code = 1\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    result = recover_missing_live_trading_launchd_if_needed(
        now=datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc),
        run_cmd=fake_run,
        status_path=status_path,
    )

    written = json.loads(status_path.read_text())
    assert result["ok"] is False
    assert result["action"] == "blocked"
    assert result["reason"] == "service_loaded_not_running"
    assert result["launchd_state"] == "spawn scheduled"
    assert result["last_exit_status"] == 1
    assert written["reason"] == "service_loaded_not_running"
    assert calls == [["launchctl", "print", "gui/501/com.zeus.live-trading"]]


def test_live_trading_launchd_watchdog_blocks_when_sidecars_are_stale(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_GUI_DOMAIN", "gui/501")
    sha = "b" * 40
    now = datetime(2026, 7, 2, 19, 50, tzinfo=timezone.utc)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write_sidecar_heartbeats(
        state_root,
        sha=sha,
        at=now - timedelta(minutes=10),
    )
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_text("<plist/>")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=3, stdout="", stderr="not found")
        if cmd[:2] == ["launchctl", "print-disabled"]:
            return SimpleNamespace(
                returncode=0,
                stdout='"com.zeus.live-trading" => enabled\n',
                stderr="",
            )
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            raise AssertionError("stale sidecars must block bootstrap")
        raise AssertionError(f"unexpected command: {cmd}")

    result = recover_missing_live_trading_launchd_if_needed(
        now=now,
        run_cmd=fake_run,
        repo_root=tmp_path,
        state_root=state_root,
        plist_path=plist,
        status_path=tmp_path / "watchdog.json",
    )

    assert result["ok"] is False
    assert result["action"] == "blocked"
    assert result["reason"] == "sidecars_not_ready"
    assert not any(call[:2] == ["launchctl", "bootstrap"] for call in calls)


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


def test_chain_token_protocol_rotation_and_transient_failure_preserves_current_id():
    """Antibody for F5 (smoke 2026-05-01): the supervisor must follow the
    Polymarket chain-token protocol — first post sends "", server returns
    canonical id, supervisor echoes it on next post, and on a transient
    transport failure the next tick retries the same id.

    Without this discipline the daemon repeatedly sends a fresh UUID that
    never matches the server's record, producing perpetual 400 Invalid
    Heartbeat ID and blocking GTC/GTD orders. Resetting to "" on a mere
    timeout is also unsafe: it starts a new lease chain and abandons already
    resting GTC/GTD orders tied to the previous heartbeat id.
    """
    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"}),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-2"}),
        RuntimeError("transient timeout"),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-3"}),
    ])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    _run(supervisor.run_once())  # sends "", server returns id-1
    _run(supervisor.run_once())  # sends id-1, server returns id-2
    _run(supervisor.run_once())  # sends id-2, server fails
    _run(supervisor.run_once())  # transient failure preserves id-2

    assert adapter.heartbeat_ids == ["", "id-1", "id-2", "id-2"], (
        "supervisor must (a) start chain with empty string, (b) echo the "
        "server-returned id on each tick, (c) preserve that id across "
        f"transient failures. Got: {adapter.heartbeat_ids!r}"
    )


def test_default_heartbeat_timeout_budget_is_shorter_than_cadence(monkeypatch):
    """RELATIONSHIP: transport timeout budget -> venue lease continuity.

    A heartbeat POST may be processed by the venue but time out before the
    rotated lease token reaches Zeus. If that HTTP call is allowed to block for
    the whole cadence, one stall can consume the lease window and resting
    orders can be venue-cancelled before the next recovery tick.
    """

    monkeypatch.delenv("ZEUS_HEARTBEAT_CADENCE_SECONDS", raising=False)
    monkeypatch.delenv("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS", raising=False)

    cadence = heartbeat_cadence_seconds_from_env()
    timeout = heartbeat_http_timeout_seconds_from_env(cadence)

    assert cadence == 2
    assert 0.0 < timeout < cadence
    assert cadence + timeout < 5.0


def test_default_heartbeat_timeout_derives_below_one_second_cadence(monkeypatch):
    """RELATIONSHIP: tighter cadence still gets a valid default timeout."""
    monkeypatch.setenv("ZEUS_HEARTBEAT_CADENCE_SECONDS", "1")
    monkeypatch.delenv("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS", raising=False)

    cadence = heartbeat_cadence_seconds_from_env()
    timeout = heartbeat_http_timeout_seconds_from_env(cadence)

    assert cadence == 1
    assert 0.0 < timeout < cadence


def test_request_exception_status_keeps_transport_cause():
    """Opaque SDK request exceptions must still leave repairable operator evidence."""

    root = TimeoutError("connect timed out")
    exc = RuntimeError("PolyApiException[status_code=None, error_message=Request exception!]")
    exc.__cause__ = root

    got = _describe_heartbeat_exception(exc)

    assert "RuntimeError: PolyApiException" in got
    assert "cause=TimeoutError: connect timed out" in got


def test_heartbeat_timeout_env_cannot_cover_full_cadence(monkeypatch):
    monkeypatch.setenv("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS", "5")

    with pytest.raises(ValueError, match="no longer than half"):
        heartbeat_http_timeout_seconds_from_env(5)


def test_heartbeat_timeout_env_cannot_consume_most_of_cadence(monkeypatch):
    """A 4s timeout on a 5s cadence leaves too little lease recovery margin."""

    monkeypatch.setenv("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS", "4")

    with pytest.raises(ValueError, match="no longer than half"):
        heartbeat_http_timeout_seconds_from_env(5)


def test_install_dedicated_heartbeat_timeout_replaces_sdk_http_client(monkeypatch):
    """RELATIONSHIP: keeper timeout config -> actual SDK heartbeat transport."""

    monkeypatch.delenv("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS", raising=False)
    from py_clob_client_v2.http_helpers import helpers as heartbeat_http_helpers

    old_client = heartbeat_http_helpers._http_client
    try:
        install_dedicated_heartbeat_http_timeout(cadence_seconds=2)

        installed = heartbeat_http_helpers._http_client
        assert installed is not old_client
        assert installed.timeout.read == pytest.approx(1.0)
        assert installed.timeout.connect == pytest.approx(1.0)
        assert installed._transport._pool._http2 is False
        assert installed._transport._pool._max_connections == 1
        assert installed._transport._pool._max_keepalive_connections == 0
    finally:
        installed = heartbeat_http_helpers._http_client
        if installed is not old_client:
            installed.close()
        heartbeat_http_helpers._http_client = old_client


def test_install_dedicated_heartbeat_timeout_preserves_request_error_cause(monkeypatch):
    """The heartbeat sidecar must not collapse transport failures into an opaque SDK string."""

    import httpx
    from py_clob_client_v2.exceptions import PolyApiException
    from py_clob_client_v2.http_helpers import helpers as heartbeat_http_helpers

    monkeypatch.delenv("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS", raising=False)
    old_client = heartbeat_http_helpers._http_client
    old_request = heartbeat_http_helpers.request
    old_installed = getattr(heartbeat_http_helpers, "_zeus_request_cause_preserved", None)
    old_diagnostic = heartbeat_supervisor_module._HEARTBEAT_REQUEST_CAUSE_PRESERVED
    old_reset_count = heartbeat_supervisor_module._HEARTBEAT_TRANSPORT_RESET_COUNT
    old_reset_reason = heartbeat_supervisor_module._HEARTBEAT_LAST_TRANSPORT_RESET_REASON

    class RaisingClient:
        def request(self, **kwargs):
            request = httpx.Request(kwargs["method"], kwargs["url"])
            raise httpx.ConnectError("dns resolution failed", request=request)

        def close(self):
            pass

    try:
        if hasattr(heartbeat_http_helpers, "_zeus_request_cause_preserved"):
            delattr(heartbeat_http_helpers, "_zeus_request_cause_preserved")
        heartbeat_supervisor_module._HEARTBEAT_REQUEST_CAUSE_PRESERVED = False
        install_dedicated_heartbeat_http_timeout(cadence_seconds=2)
        assert heartbeat_supervisor_module.heartbeat_transport_diagnostics() == {
            "request_cause_preserved": True,
            "transport_reset_count": old_reset_count,
            "last_transport_reset_reason": old_reset_reason,
        }
        installed = heartbeat_http_helpers._http_client
        installed.close()
        heartbeat_http_helpers._http_client = RaisingClient()

        with pytest.raises(PolyApiException) as raised:
            heartbeat_http_helpers.request("https://clob.polymarket.com/v1/heartbeats", "POST")

        assert isinstance(raised.value.__cause__, httpx.ConnectError)
        described = _describe_heartbeat_exception(raised.value)
        assert "Request exception: ConnectError" in described
        assert "cause=ConnectError: dns resolution failed" in described
    finally:
        heartbeat_http_helpers.request = old_request
        heartbeat_http_helpers._http_client = old_client
        if old_installed is None:
            with contextlib.suppress(AttributeError):
                delattr(heartbeat_http_helpers, "_zeus_request_cause_preserved")
        else:
            heartbeat_http_helpers._zeus_request_cause_preserved = old_installed
        heartbeat_supervisor_module._HEARTBEAT_REQUEST_CAUSE_PRESERVED = old_diagnostic
        heartbeat_supervisor_module._HEARTBEAT_TRANSPORT_RESET_COUNT = old_reset_count
        heartbeat_supervisor_module._HEARTBEAT_LAST_TRANSPORT_RESET_REASON = old_reset_reason


def test_transport_failure_resets_dedicated_pool_without_abandoning_chain():
    import httpx
    from py_clob_client_v2.exceptions import PolyApiException

    def pool_timeout() -> PolyApiException:
        request = httpx.Request("POST", "https://clob.polymarket.com/v1/heartbeats")
        try:
            raise httpx.PoolTimeout("pool exhausted", request=request)
        except httpx.PoolTimeout as exc:
            try:
                raise PolyApiException(error_msg="Request exception: PoolTimeout") from exc
            except PolyApiException as wrapped:
                return wrapped

    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"}),
        pool_timeout(),
        pool_timeout(),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-recovered"}),
    ])
    reset_causes: list[BaseException] = []
    supervisor = HeartbeatSupervisor(
        adapter,
        cadence_seconds=5,
        transport_reset=reset_causes.append,
    )

    assert _run(supervisor.run_once()).health is HeartbeatHealth.HEALTHY
    assert _run(supervisor.run_once()).health is HeartbeatHealth.DEGRADED
    lost = _run(supervisor.run_once())
    recovered = _run(supervisor.run_once())

    assert lost.health is HeartbeatHealth.LOST
    assert recovered.health is HeartbeatHealth.HEALTHY
    assert recovered.heartbeat_id == "id-recovered"
    assert adapter.heartbeat_ids == ["", "id-1", "id-1", "id-1"]
    assert [type(cause.__cause__).__name__ for cause in reset_causes] == [
        "PoolTimeout",
        "PoolTimeout",
    ]


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
    assert recovered.consecutive_successes == 1
    assert recovered.heartbeat_id == "id-2"
    assert recovered.last_invalid_id_at is not None
    assert recovered.lease_gap_suspected_until is not None
    assert recovered.resting_order_safe() is False
    assert supervisor.gate_for_order_type("GTC") is False
    assert supervisor.gate_for_order_type("FOK") is True
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
    assert recovered.consecutive_successes == 1
    assert recovered.heartbeat_id == "server-next"
    assert recovered.last_invalid_id_at is not None
    assert recovered.lease_gap_suspected_until is not None
    assert supervisor.gate_for_order_type("GTC") is False
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
    assert lost.consecutive_failures == 2
    assert lost.heartbeat_id == ""
    assert "empty-chain recovery failed" in (lost.last_error or "")
    assert lost.last_invalid_id_at is not None
    assert lost.lease_gap_suspected_until is not None
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


def test_lost_heartbeat_retries_back_off_without_delaying_healthy_cadence():
    cadence = 5
    healthy = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=datetime.now(timezone.utc),
        consecutive_failures=0,
        heartbeat_id="id-1",
        cadence_seconds=cadence,
    )

    assert heartbeat_retry_delay_seconds(healthy, cadence_seconds=cadence) == cadence
    for failures, expected in ((2, 10), (3, 20), (4, 30), (100, 30)):
        lost = replace(
            healthy,
            health=HeartbeatHealth.LOST,
            consecutive_failures=failures,
        )
        assert heartbeat_retry_delay_seconds(lost, cadence_seconds=cadence) == expected


def test_lost_generic_request_failure_retries_preserved_chain_on_next_tick():
    """Generic failures never add an empty-chain POST to their failed tick.

    A timeout cannot say whether the venue rotated the old token. Only the
    explicit Invalid Heartbeat ID protocol response authorizes empty-chain
    recovery; generic failures retain the original id and fail closed for GTC.
    """
    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"}),
        RuntimeError("request miss-1"),
        RuntimeError("request miss-2"),
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-recovered"}),
    ])
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=5)

    assert _run(supervisor.run_once()).health is HeartbeatHealth.HEALTHY
    degraded = _run(supervisor.run_once())
    lost = _run(supervisor.run_once())
    recovered = _run(supervisor.run_once())

    assert degraded.health is HeartbeatHealth.DEGRADED
    assert lost.health is HeartbeatHealth.LOST
    assert recovered.health is HeartbeatHealth.HEALTHY
    assert recovered.consecutive_failures == 0
    assert recovered.heartbeat_id == "id-recovered"
    assert recovered.lease_gap_suspected_until is not None
    assert recovered.resting_order_safe() is False
    assert supervisor.gate_for_order_type("GTC") is False
    assert supervisor.gate_for_order_type("FOK") is True
    assert adapter.heartbeat_ids == ["", "id-1", "id-1", "id-1"]


def test_connect_timeout_posts_once_per_tick_and_preserves_chain():
    """RELATIONSHIP: ConnectTimeout -> one POST -> transport reset -> next-tick retry.

    This is the exact live incident shape. A generic transport failure may not
    start an empty heartbeat chain, even after health reaches LOST.
    """
    import httpx

    request = httpx.Request("POST", "https://clob.polymarket.com/v1/heartbeats")
    timeout = httpx.ConnectTimeout("TLS handshake timed out", request=request)
    adapter = FakeHeartbeatAdapter([
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-1"}),
        timeout,
        timeout,
        HeartbeatAck(ok=True, raw={"heartbeat_id": "id-2"}),
    ])
    reset_causes: list[BaseException] = []
    supervisor = HeartbeatSupervisor(
        adapter,
        cadence_seconds=5,
        transport_reset=reset_causes.append,
    )

    assert _run(supervisor.run_once()).health is HeartbeatHealth.HEALTHY
    assert _run(supervisor.run_once()).health is HeartbeatHealth.DEGRADED
    assert _run(supervisor.run_once()).health is HeartbeatHealth.LOST
    recovered = _run(supervisor.run_once())

    assert recovered.health is HeartbeatHealth.HEALTHY
    assert recovered.heartbeat_id == "id-2"
    assert adapter.heartbeat_ids == ["", "id-1", "id-1", "id-1"]
    assert reset_causes == [timeout, timeout]


def test_lost_state_blocks_GTC_and_GTD_placement():
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")

    assert heartbeat_required_for("GTC") is True
    assert heartbeat_required_for(OrderType.GTD) is True
    assert supervisor.gate_for_order_type("GTC") is False
    assert supervisor.gate_for_order_type("GTD") is False


def test_custom_supervisor_healthy_status_cannot_bypass_provider_gate_veto():
    class CustomSupervisor:
        def __init__(self):
            self.status_calls = 0
            self.gate_calls = 0

        def status(self):
            self.status_calls += 1
            return HeartbeatStatus(
                health=HeartbeatHealth.HEALTHY,
                last_success_at=datetime.now(timezone.utc),
                consecutive_failures=0,
                heartbeat_id="custom",
                cadence_seconds=5,
            )

        def gate_for_order_type(self, order_type):
            self.gate_calls += 1
            return False

    supervisor = CustomSupervisor()
    configure_global_supervisor(supervisor)

    with pytest.raises(HeartbeatNotHealthy):
        heartbeat_supervisor_module.assert_heartbeat_allows_order_type("GTC")

    assert supervisor.status_calls == 1
    assert supervisor.gate_calls == 1


def test_lost_state_allows_FOK_FAK_immediate_only():
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")

    assert heartbeat_required_for("FOK") is False
    assert heartbeat_required_for(OrderType.FAK) is False
    assert supervisor.gate_for_order_type("FOK") is True
    assert supervisor.gate_for_order_type("FAK") is True


def test_lost_summary_allows_immediate_entry_but_blocks_resting_type():
    from src.engine.cycle_runner import _discovery_gates_allow_entries
    from src.events.reactor import _edli_heartbeat_authority_summary
    from src.observability.status_summary import _heartbeat_component
    from src.riskguard.risk_level import RiskLevel

    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")
    configure_global_supervisor(supervisor)

    payload = heartbeat_supervisor_module.summary()

    assert payload["entry"]["allow_submit"] is True
    assert payload["entry"]["resting_allow_submit"] is False
    assert payload["entry"]["immediate_allow_submit"] is True
    assert payload["entry"]["allowed_order_types"] == ["FOK", "FAK"]
    assert _edli_heartbeat_authority_summary("GTC")["allow_submit"] is False
    assert _edli_heartbeat_authority_summary("FOK")["allow_submit"] is True
    assert _heartbeat_component(payload)["allowed"] is True
    assert _heartbeat_component(payload, order_type="FAK")["allowed"] is True
    assert _heartbeat_component(payload, order_type="GTC")["allowed"] is False
    assert _discovery_gates_allow_entries(
        risk_level=RiskLevel.GREEN,
        heartbeat_status=payload,
        ws_gap_status={"entry": {"allow_submit": True}},
        cutover_summary={"entry": {"allow_submit": True}},
        governor_status={"entry": {"allow_submit": True}},
        current_posture="NORMAL",
        chain_ready=True,
        force_exit=False,
        freshness_allows_entries=True,
        entry_bankroll=1.0,
        exposure_gate_hit=False,
        entries_paused=False,
    )


def test_executor_blocks_gtc_before_command_persistence_when_heartbeat_lost(monkeypatch):
    from src.execution.executor import _assert_heartbeat_allows_submit, _live_order

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    supervisor = HeartbeatSupervisor(FakeHeartbeatAdapter([]), cadence_seconds=5)
    supervisor.record_failure("miss-1")
    supervisor.record_failure("miss-2")
    configure_global_supervisor(supervisor)

    with pytest.raises(HeartbeatNotHealthy):
        _assert_heartbeat_allows_submit("GTC")
    with patch("src.data.polymarket_client.PolymarketClient") as client_cls:
        result = _live_order(
            "heartbeat-trade",
            _intent(),
            shares=10.0,
            conn=conn,
            decision_id="decision-heartbeat",
        )

    assert result.status == "rejected"
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


def test_scheduler_health_tracks_mode_skips_as_business_liveness(monkeypatch, tmp_path):
    from src.observability import scheduler_health

    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")

    scheduler_health._write_scheduler_health(
        "run_mode:imminent_open_capture",
        failed=False,
        skipped=True,
        skip_reason="cycle_lock_busy",
    )
    scheduler_health._write_scheduler_health(
        "run_mode:imminent_open_capture",
        failed=False,
        skipped=True,
        skip_reason="cycle_lock_busy",
    )
    scheduler_health._write_scheduler_health("run_mode:opening_hunt", failed=False)

    data = json.loads((tmp_path / "scheduler_health.json").read_text())
    skipped = data["run_mode:imminent_open_capture"]
    assert skipped["status"] == "SKIPPED"
    assert skipped["last_skip_reason"] == "cycle_lock_busy"
    assert skipped["consecutive_skips"] == 2
    assert data["run_mode:opening_hunt"]["consecutive_skips"] == 0


def test_scheduler_health_persists_mode_business_frontier(monkeypatch, tmp_path):
    from src.observability import scheduler_health

    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")

    scheduler_health._write_scheduler_health(
        "run_mode:day0_capture",
        failed=False,
        extra={
            "status": "FAILED",
            "last_started_at": "2026-05-21T20:00:00+00:00",
            "last_completed_at": "2026-05-21T20:01:00+00:00",
            "last_candidates": 0,
            "last_final_intent_built": 0,
            "last_submit_attempts": 0,
            "last_terminal_classification": "no_markets_after_mode_phase_filter",
        },
    )

    data = json.loads((tmp_path / "scheduler_health.json").read_text())
    day0 = data["run_mode:day0_capture"]
    assert day0["status"] == "OK"
    assert day0["business_liveness"]["status"] == "FAILED"
    assert day0["business_liveness"]["last_candidates"] == 0
    assert day0["business_liveness"]["last_final_intent_built"] == 0
    assert day0["business_liveness"]["last_submit_attempts"] == 0
    assert (
        day0["business_liveness"]["last_terminal_classification"]
        == "no_markets_after_mode_phase_filter"
    )


def test_scheduler_health_started_does_not_advance_success(monkeypatch, tmp_path):
    from src.observability import scheduler_health

    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "scheduler_health.json")

    scheduler_health._write_scheduler_health(
        "run_mode:opening_hunt",
        failed=False,
        started=True,
    )

    data = json.loads((tmp_path / "scheduler_health.json").read_text())
    entry = data["run_mode:opening_hunt"]
    assert entry["status"] == "RUNNING"
    assert entry["last_started_at"]
    assert "last_success_at" not in entry


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


def test_external_heartbeat_supervisor_rejects_future_status_for_entry(tmp_path):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    write_heartbeat_keeper_status(
        HeartbeatStatus(
            health=HeartbeatHealth.HEALTHY,
            last_success_at=datetime.now(timezone.utc),
            consecutive_failures=0,
            heartbeat_id="keeper-id",
            cadence_seconds=5,
        ),
        path=status_path,
    )
    payload = json.loads(status_path.read_text())
    payload["written_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    status_path.write_text(json.dumps(payload))

    supervisor = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=8,
        cadence_seconds=5,
    )
    configure_global_supervisor(supervisor)
    try:
        status = supervisor.status()
        assert status.health is HeartbeatHealth.LOST
        assert status.status_reason == "heartbeat_snapshot_from_future"
        assert status.age_seconds is not None and status.age_seconds < 0
        with pytest.raises(HeartbeatNotHealthy):
            heartbeat_supervisor_module.assert_heartbeat_allows_order_type("GTC")
        heartbeat_supervisor_module.assert_heartbeat_allows_order_type("FOK")
        heartbeat_supervisor_module.assert_heartbeat_allows_order_type(
            "FAK",
            reduce_only=True,
        )
    finally:
        configure_global_supervisor(None)


def test_external_mode_cold_singleton_reads_fresh_keeper_without_false_lost(
    tmp_path,
    monkeypatch,
):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    write_heartbeat_keeper_status(
        HeartbeatStatus(
            health=HeartbeatHealth.HEALTHY,
            last_success_at=datetime.now(timezone.utc),
            consecutive_failures=0,
            heartbeat_id="keeper-id",
            cadence_seconds=5,
        ),
        path=status_path,
    )
    monkeypatch.setenv("ZEUS_VENUE_HEARTBEAT_MODE", "external")
    monkeypatch.setattr(
        heartbeat_supervisor_module,
        "heartbeat_keeper_status_path",
        lambda: status_path,
    )
    configure_global_supervisor(None)

    try:
        status = heartbeat_supervisor_module.current_status()
        payload = heartbeat_supervisor_module.summary()
        supervisor = heartbeat_supervisor_module.get_global_supervisor()
    finally:
        configure_global_supervisor(None)

    assert isinstance(supervisor, ExternalHeartbeatSupervisor)
    assert status.health is HeartbeatHealth.HEALTHY
    assert status.status_reason == "ok"
    assert status.source == "zeus-venue-heartbeat"
    assert status.written_at is not None
    assert status.age_seconds is not None and status.age_seconds >= 0
    assert payload["health"] == "HEALTHY"
    assert payload["status_reason"] == "ok"
    assert payload["source"] == "zeus-venue-heartbeat"
    assert payload["written_at"] is not None
    assert payload["age_seconds"] is not None
    assert payload["entry"]["allow_submit"] is True


def test_external_heartbeat_supervisor_blocks_healthy_status_during_lease_gap(tmp_path):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    now = datetime.now(timezone.utc)
    status = HeartbeatStatus(
        health=HeartbeatHealth.HEALTHY,
        last_success_at=now,
        consecutive_failures=0,
        heartbeat_id="keeper-id",
        cadence_seconds=5,
        last_error=None,
        last_failure_at=now,
        last_invalid_id_at=now,
        consecutive_successes=1,
        lease_continuous_since=now,
        lease_gap_suspected_until=now + timedelta(seconds=15),
    )
    write_heartbeat_keeper_status(status, path=status_path)

    supervisor = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=8,
        cadence_seconds=5,
    )

    loaded = supervisor.status()
    assert loaded.health is HeartbeatHealth.HEALTHY
    assert loaded.last_invalid_id_at is not None
    assert loaded.resting_order_safe() is False
    assert supervisor.gate_for_order_type(OrderType.GTC) is False
    assert supervisor.gate_for_order_type(OrderType.FOK) is True

    payload = json.loads(status_path.read_text())
    payload["lease_gap_suspected_until"] = (now - timedelta(seconds=1)).isoformat()
    status_path.write_text(json.dumps(payload))

    assert supervisor.gate_for_order_type(OrderType.GTC) is True


def _write_healthy_status_aged(status_path: Path, *, age_seconds: float) -> None:
    write_heartbeat_keeper_status(
        HeartbeatStatus(
            health=HeartbeatHealth.HEALTHY,
            last_success_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
            consecutive_failures=0,
            heartbeat_id="keeper-id",
            cadence_seconds=5,
        ),
        path=status_path,
    )
    payload = json.loads(status_path.read_text())
    payload["written_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    status_path.write_text(json.dumps(payload))


def test_freshness_window_survives_deployed_worst_case_writer_latency(tmp_path):
    """R3 heartbeat freshness margin (2026-07-19 fix).

    Measured (docs/evidence/capital_efficiency_2026_07_19/
    heartbeat_killswitch_driver.md + this fix's episode-duration histogram over
    logs/zeus-venue-heartbeat.err): the deployed writer
    (com.zeus.venue-heartbeat.plist) runs cadence=5s, http_timeout=2s, and writes
    the status file every tick regardless of outcome. The worst-case gap between
    two consecutive writes for a still-alive writer is bounded by
    cadence + one worst-case single-attempt duration (the HTTP timeout is a
    code-enforced ceiling on that duration) = 5 + 2 = 7s. The prior default
    (8s) left only 1s of margin over that bound -- no room for ordinary
    process/OS scheduling jitter. This asserts a healthy writer is never
    misclassified LOST at that 7s deployed-worst-case gap, nor even with an
    extra ~1.4s of unmodeled jitter on top (8.4s, which would already have
    tripped the prior 8s default), under the new default window.
    """

    status_path = tmp_path / "venue-heartbeat-keeper.json"
    deployed_worst_case_gap = 5 + 2  # cadence_seconds + http_timeout_seconds

    _write_healthy_status_aged(status_path, age_seconds=float(deployed_worst_case_gap))
    supervisor = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS,
        cadence_seconds=5,
    )
    assert supervisor.status().health is HeartbeatHealth.HEALTHY
    assert supervisor.gate_for_order_type(OrderType.GTC) is True

    # Additional unmodeled jitter on top of the pure HTTP-timing bound: this
    # would already have exceeded the prior 8s default (7 + 1.4 = 8.4 > 8).
    jittery_gap = deployed_worst_case_gap + 1.4
    _write_healthy_status_aged(status_path, age_seconds=jittery_gap)
    supervisor_new = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS,
        cadence_seconds=5,
    )
    assert supervisor_new.status().health is HeartbeatHealth.HEALTHY

    supervisor_old = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=8,
        cadence_seconds=5,
    )
    assert supervisor_old.status().health is HeartbeatHealth.LOST
    assert supervisor_old.status().status_reason == "heartbeat_snapshot_expired"


def test_freshness_window_still_detects_dead_writer_within_bound(tmp_path):
    """A writer that has truly stopped ticking is still detected as LOST well

    within the new window, and that detection bound remains negligible next to
    the ~90-120s exit_monitor decision cadence (this fix's own DB measurement
    of decision_log timestamps) that actually consumes this gate on the money
    path.
    """

    status_path = tmp_path / "venue-heartbeat-keeper.json"
    _write_healthy_status_aged(
        status_path,
        age_seconds=float(DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS) + 0.5,
    )
    supervisor = ExternalHeartbeatSupervisor(
        status_path=status_path,
        max_age_seconds=DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS,
        cadence_seconds=5,
    )
    status = supervisor.status()
    assert status.health is HeartbeatHealth.LOST
    assert status.status_reason == "heartbeat_snapshot_expired"
    assert supervisor.gate_for_order_type(OrderType.GTC) is False

    exit_monitor_min_observed_cadence_seconds = 90  # measured, see fix commit body
    assert DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS < exit_monitor_min_observed_cadence_seconds


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
    assert payload["schema_version"] == 2
    assert isinstance(
        payload["transport_diagnostics"]["request_cause_preserved"],
        bool,
    )
    assert isinstance(payload["transport_diagnostics"]["transport_reset_count"], int)
    assert payload["health"] == "HEALTHY"
    assert payload["heartbeat_id"] == "keeper-A"
    assert payload["last_error"] is None
    assert payload["resting_order_safe"] is True
    assert payload["consecutive_successes"] == 1
    assert adapter.heartbeat_ids == [""]


def test_heartbeat_keeper_keeps_status_fresh_while_failed_posts_back_off(
    tmp_path,
    monkeypatch,
):
    status_path = tmp_path / "venue-heartbeat-keeper.json"
    adapter = FakeHeartbeatAdapter(
        [RuntimeError("503") for _ in range(3)]
    )
    monotonic = iter((0.0, 5.0, 10.0, 15.0))
    sleeps: list[float] = []
    writes: list[int] = []
    real_write = heartbeat_supervisor_module.write_heartbeat_keeper_status

    def record_write(status, *, path=None):
        writes.append(status.consecutive_failures)
        return real_write(status, path=path)

    monkeypatch.setattr(
        heartbeat_supervisor_module,
        "maybe_recover_missing_live_trading_launchd",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        heartbeat_supervisor_module,
        "write_heartbeat_keeper_status",
        record_write,
    )

    run_heartbeat_keeper(
        adapter=adapter,
        status_path=status_path,
        cadence_seconds=5,
        max_ticks=4,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: next(monotonic),
    )

    payload = json.loads(status_path.read_text())
    assert adapter.heartbeat_ids == ["", "", ""]
    assert writes == [1, 2, 2, 3]
    assert payload["health"] == "LOST"
    assert payload["consecutive_failures"] == 3
    assert len(sleeps) == 3


def test_heartbeat_keeper_bypasses_dead_proxy_before_client_creation(tmp_path, monkeypatch):
    """The external keeper must match src.main's pre-client proxy health gate."""

    calls: list[str] = []
    adapter = FakeHeartbeatAdapter([HeartbeatAck(ok=True, raw={"heartbeat_id": "keeper-A"})])

    monkeypatch.setattr(
        "src.data.proxy_health.bypass_dead_proxy_env_vars",
        lambda: calls.append("proxy_health"),
    )

    class Client:
        def __init__(self):
            calls.append("client_created")

        def _ensure_v2_adapter(self):
            return adapter

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    monkeypatch.setattr(
        heartbeat_supervisor_module,
        "maybe_recover_missing_live_trading_launchd",
        lambda **_kwargs: None,
    )

    run_heartbeat_keeper(
        status_path=tmp_path / "venue-heartbeat-keeper.json",
        cadence_seconds=5,
        max_ticks=1,
    )

    assert calls == ["proxy_health", "client_created"]
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


def test_main_external_venue_heartbeat_mode_consumes_status_without_boot_adapter(
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
    class Client:
        def _ensure_v2_adapter(self):
            raise AssertionError("external heartbeat startup must not construct CLOB adapter")

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
    assert launched_collateral == []
    assert launched_background == []


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

    # Efficiency #3 (warm-overlap): the boot wallet RPC moved from an inline
    # _bankroll_current() call into the background warm thread spawned by
    # _start_boot_wallet_warm(). That spawn is now the FIRST boot wallet HTTP —
    # it must still come after the venue heartbeat (heartbeat-before-boot-http
    # invariant unchanged; only the call site moved).
    assert heartbeat_start < main_body.index("_start_boot_wallet_warm()")
    assert heartbeat_start < main_body.index("_startup_wallet_check(")


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
    monkeypatch.setattr(main, "_ws_gap_m5_reconcile_required", lambda: False)
    monkeypatch.setattr(main.threading, "Thread", InlineThread)
    main._last_venue_background_maintenance_attempt_at = None
    deadline = time.monotonic() + 1.0
    while main._venue_background_maintenance_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert main._start_venue_background_maintenance_async(adapter) == "started"
    assert main._start_venue_background_maintenance_async(adapter) == "throttled"
    assert calls == [adapter]


def test_venue_background_maintenance_bypasses_throttle_for_m5_latch(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main, "_ws_gap_m5_reconcile_required", lambda: True)
    monkeypatch.setattr(main, "_edli_reactor_pending_backlog_exists", lambda: False)
    monkeypatch.setattr(main.threading, "Thread", InlineThread)
    monkeypatch.setattr(
        main,
        "_run_venue_background_maintenance_once",
        lambda active_adapter: calls.append(active_adapter),
    )
    main._last_venue_background_maintenance_attempt_at = None

    assert main._start_venue_background_maintenance_async(adapter) == "started"
    assert main._start_venue_background_maintenance_async(adapter) == "started"
    assert calls == [adapter, adapter]


def test_venue_background_maintenance_defers_when_edli_pending_backlog_exists(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    monkeypatch.setattr(main, "_edli_reactor_pending_backlog_exists", lambda: True)
    monkeypatch.setattr(main, "_ws_gap_m5_reconcile_required", lambda: False)
    monkeypatch.setattr(
        main,
        "_run_venue_background_maintenance_once",
        lambda active_adapter: calls.append(active_adapter),
    )
    main._last_venue_background_maintenance_attempt_at = None

    assert main._start_venue_background_maintenance_async(adapter) == "deferred_edli_pending_backlog"
    assert main._start_venue_background_maintenance_async(adapter) == "throttled"
    assert calls == []


def test_venue_background_maintenance_runs_m5_reconcile_despite_edli_pending_backlog(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main, "_edli_reactor_pending_backlog_exists", lambda: True)
    monkeypatch.setattr(main, "_ws_gap_m5_reconcile_required", lambda: True)
    monkeypatch.setattr(main.threading, "Thread", InlineThread)
    monkeypatch.setattr(
        main,
        "_run_venue_background_maintenance_once",
        lambda active_adapter: calls.append(active_adapter),
    )
    main._last_venue_background_maintenance_attempt_at = None

    assert main._start_venue_background_maintenance_async(adapter) == "started"
    assert calls == [adapter]


def test_post_reactor_maintenance_starts_only_when_m5_required(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    monkeypatch.setattr(main, "_ensure_venue_read_side_adapter", lambda: adapter)
    monkeypatch.setattr(
        main,
        "_start_venue_background_maintenance_async",
        lambda active_adapter: calls.append(active_adapter) or "started",
    )

    monkeypatch.setattr(main, "_ws_gap_m5_reconcile_required", lambda: False)
    assert main._start_venue_background_maintenance_after_reactor_if_required() == "not_required"
    assert calls == []

    monkeypatch.setattr(main, "_ws_gap_m5_reconcile_required", lambda: True)
    assert main._start_venue_background_maintenance_after_reactor_if_required() == "started"
    assert calls == [adapter]


def test_collateral_background_refresh_defers_when_edli_pending_backlog_exists(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    monkeypatch.setattr(main, "_edli_reactor_pending_backlog_exists", lambda: True)
    monkeypatch.setattr(
        main,
        "_refresh_global_collateral_snapshot_if_due",
        lambda active_adapter: calls.append(active_adapter),
    )

    assert main._start_collateral_background_refresh_async(adapter) == "deferred_edli_pending_backlog"
    assert calls == []


def test_collateral_background_refresh_runs_for_degraded_snapshot_despite_edli_backlog(monkeypatch):
    from src import main
    from src.state.collateral_ledger import (
        CollateralLedger,
        CollateralSnapshot,
        configure_global_ledger,
    )

    adapter = object()
    calls = []
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
            captured_at=datetime.now(timezone.utc),
            authority_tier="DEGRADED",
        )
    )
    configure_global_ledger(ledger)

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main, "_edli_reactor_pending_backlog_exists", lambda: True)
    monkeypatch.setattr(
        main,
        "_refresh_global_collateral_snapshot_if_due",
        lambda active_adapter: calls.append(active_adapter),
    )
    monkeypatch.setattr(main.threading, "Thread", InlineThread)
    main._last_collateral_heartbeat_refresh_attempt_at = None

    assert main._start_collateral_background_refresh_async(adapter) == "started"
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


def test_external_heartbeat_defers_background_db_work_while_edli_reactor_runs(monkeypatch):
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

    assert main._edli_reactor_active_lock.acquire(blocking=False)
    try:
        main._start_venue_heartbeat_loop_if_needed()
    finally:
        main._edli_reactor_active_lock.release()

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


def test_venue_background_maintenance_refreshes_findings_before_ws_latch_clear(monkeypatch):
    from src import main

    adapter = object()
    calls = []

    def _refresh(active_adapter):
        assert active_adapter is adapter
        calls.append("refresh")
        return {"status": "resolved", "remaining": 0}

    def _ws_gap(active_adapter):
        assert active_adapter is adapter
        calls.append("ws_gap")
        return {"status": "cleared", "unresolved_findings": 0}

    monkeypatch.setattr(main, "_refresh_reconcile_findings_if_required", _refresh)
    monkeypatch.setattr(main, "_run_ws_gap_reconcile_if_required", _ws_gap)
    monkeypatch.setattr(
        main,
        "_refresh_global_collateral_snapshot_if_due",
        lambda active_adapter: calls.append("collateral") or False,
    )

    result = main._run_venue_background_maintenance_once(adapter)

    assert result["status"] == "ok"
    assert result["reconcile_findings_refresh"]["status"] == "resolved"
    assert result["ws_gap_reconcile"]["status"] == "cleared"
    assert calls == ["refresh", "ws_gap", "collateral"]


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


def test_market_discovery_scheduler_refreshes_market_substrate_outside_cycle(monkeypatch):
    from src import main
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    calls: list[tuple[str, object]] = []

    def fake_find_weather_markets(*, min_hours_to_resolution, **_kwargs):
        calls.append(("find", min_hours_to_resolution))
        return [{"slug": "weather-event", "outcomes": [{"condition_id": "cond-1", "executable": True}]}]

    class FakePolymarketClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            calls.append(("clob_enter", self))
            return self

        def __exit__(self, *_exc):
            calls.append(("clob_exit", self))

        def get_clob_market_info(self, *_args, **_kwargs):
            raise AssertionError("refresh helper is stubbed in this relationship test")

    class FakeConn:
        def commit(self):
            calls.append(("commit", None))

        def close(self):
            calls.append(("close", None))

    def fake_refresh(conn, *, markets, clob, captured_at, scan_authority, **_kwargs):
        calls.append(("refresh", (conn, markets, isinstance(clob, FakePolymarketClient), scan_authority)))
        return {"attempted": 1, "inserted": 1, "skipped": 0, "failed": 0, "truncated": 0}

    # find_slug_pattern_weather_markets was renamed to find_weather_markets (B3 rename).
    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        fake_find_weather_markets,
    )
    monkeypatch.setattr(market_scanner, "refresh_executable_market_substrate_snapshots", fake_refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class: FakeConn())
    monkeypatch.setattr(
        main,
        "_ensure_venue_read_side_adapter",
        lambda: (_ for _ in ()).throw(AssertionError("market_discovery must use public CLOB read client")),
    )
    # P2: force STALE substrate so the producer-local staleness gate falls through to capture
    # (a prior test's successful cycle may leave a fresh time.monotonic() in this global).
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )

    substrate_observer._market_discovery_cycle()

    assert ("find", 0.0) in calls
    refresh_calls = [call for call in calls if call[0] == "refresh"]
    assert len(refresh_calls) == 1
    assert refresh_calls[0][1][2] is True
    assert ("commit", None) in calls
    assert ("close", None) in calls


def test_market_discovery_scheduler_runs_while_cycle_lock_is_held(monkeypatch):
    from src import main
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    calls: list[str] = []
    # find_slug_pattern_weather_markets was renamed to find_weather_markets (B3 rename).
    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_kwargs: calls.append("find") or [],
    )
    monkeypatch.setattr(
        market_scanner,
        "refresh_executable_market_substrate_snapshots",
        lambda *_args, **_kwargs: calls.append("refresh") or {
            "attempted": 0,
            "inserted": 0,
            "skipped": 0,
            "failed": 0,
            "truncated": 0,
        },
    )

    class FakePolymarketClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    class FakeConn:
        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class: FakeConn())
    # P2: force STALE substrate so the staleness gate falls through to capture (the cycle is
    # decoupled from main._cycle_lock — a P1 cycle-lock held must not block this producer).
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )

    assert main._cycle_lock.acquire(blocking=False)
    try:
        substrate_observer._market_discovery_cycle()
    finally:
        main._cycle_lock.release()

    assert calls == ["find", "refresh", "commit", "close"]


def test_market_discovery_scheduler_defers_only_when_previous_refresh_runs(monkeypatch):
    import src.data.market_scanner as market_scanner

    monkeypatch.setattr(
        market_scanner,
        "find_slug_pattern_weather_markets",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("self-overlap must not scan")),
    )

    assert substrate_observer._market_discovery_lock.acquire(blocking=False)
    try:
        substrate_observer._market_discovery_cycle()
    finally:
        substrate_observer._market_discovery_lock.release()


def test_user_channel_auto_derive_prefers_persisted_ids_and_skips_boot_gamma_scan(monkeypatch):
    # P3 lift (system_decomposition_plan §8 Step 3): _auto_derive_user_channel_condition_ids
    # + _market_events_user_channel_condition_ids moved from src.main to
    # src.ingest.price_channel_ingest. Both the patch target and the call repoint together.
    from src.ingest import price_channel_ingest as main
    import src.data.market_scanner as market_scanner

    monkeypatch.delenv("ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN", raising=False)
    monkeypatch.setattr(main, "_market_events_user_channel_condition_ids", lambda now=None: ["cond-persisted"])
    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fresh persisted ids must not boot-scan Gamma")),
    )

    assert main._auto_derive_user_channel_condition_ids() == ["cond-persisted"]


def test_user_channel_auto_derive_returns_empty_without_boot_gamma_opt_in(monkeypatch):
    # P3 lift (system_decomposition_plan §8 Step 3): auto-derive helpers moved to
    # src.ingest.price_channel_ingest; patch target + call repoint together.
    from src.ingest import price_channel_ingest as main
    import src.data.market_scanner as market_scanner

    monkeypatch.delenv("ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN", raising=False)
    monkeypatch.setattr(main, "_market_events_user_channel_condition_ids", lambda now=None: [])
    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("boot Gamma scan must be opt-in")),
    )

    assert main._auto_derive_user_channel_condition_ids() == []


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
