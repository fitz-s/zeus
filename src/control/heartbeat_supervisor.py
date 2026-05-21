"""Heartbeat supervision for live resting Polymarket orders.

R3 Z3: GTC/GTD placement is allowed only while the venue heartbeat is
healthy. Heartbeat loss reuses the existing fail-closed auto-pause tombstone;
it does not introduce a second control truth surface.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_CANCEL_SUSPECTED_REASON = "heartbeat_cancel_suspected"
DEFAULT_HEARTBEAT_CADENCE_SECONDS = 2
DEFAULT_HEARTBEAT_HTTP_TIMEOUT_SECONDS = 1.0
DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS = 8
DEFAULT_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS = 30
DEFAULT_HEARTBEAT_LEASE_RECOVERY_SUCCESS_TICKS = 3
HEARTBEAT_KEEPER_STATUS_FILENAME = "venue-heartbeat-keeper.json"
_RESTING_ORDER_TYPES = {"GTC", "GTD"}
_IMMEDIATE_ORDER_TYPES = {"FOK", "FAK"}


class HeartbeatHealth(str, Enum):
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    LOST = "LOST"
    DISABLED_FOR_NON_RESTING_ONLY = "DISABLED_FOR_NON_RESTING_ONLY"


class OrderType(str, Enum):
    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


class HeartbeatNotHealthy(RuntimeError):
    """Raised before live resting-order submit when heartbeat is not healthy."""


@dataclass(frozen=True)
class HeartbeatStatus:
    health: HeartbeatHealth
    last_success_at: Optional[datetime]
    consecutive_failures: int
    heartbeat_id: str
    cadence_seconds: int
    last_error: Optional[str] = None
    last_failure_at: Optional[datetime] = None
    last_invalid_id_at: Optional[datetime] = None
    consecutive_successes: int = 0
    lease_continuous_since: Optional[datetime] = None
    lease_gap_suspected_until: Optional[datetime] = None

    def resting_order_safe(self, *, now: Optional[datetime] = None) -> bool:
        if self.health is not HeartbeatHealth.HEALTHY:
            return False
        if self.lease_gap_suspected_until is None:
            return True
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        checked_at = checked_at.astimezone(timezone.utc)
        return checked_at >= self.lease_gap_suspected_until.astimezone(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "heartbeat_id": self.heartbeat_id,
            "cadence_seconds": self.cadence_seconds,
            "last_error": self.last_error,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_invalid_id_at": self.last_invalid_id_at.isoformat() if self.last_invalid_id_at else None,
            "consecutive_successes": self.consecutive_successes,
            "lease_continuous_since": (
                self.lease_continuous_since.isoformat()
                if self.lease_continuous_since
                else None
            ),
            "lease_gap_suspected_until": (
                self.lease_gap_suspected_until.isoformat()
                if self.lease_gap_suspected_until
                else None
            ),
            "resting_order_safe": self.resting_order_safe(),
        }


def _normalize_order_type(order_type: str | OrderType | None) -> str:
    if isinstance(order_type, OrderType):
        return order_type.value
    if order_type is None:
        return "GTC"
    return str(order_type).upper()


def heartbeat_required_for(order_type: str | OrderType | None) -> bool:
    """Return whether an order type requires a healthy venue heartbeat.

    Unknown order types fail closed as heartbeat-required because Zeus must not
    accidentally treat a new resting type as immediate-only.
    """

    normalized = _normalize_order_type(order_type)
    if normalized in _IMMEDIATE_ORDER_TYPES:
        return False
    return True


def heartbeat_cadence_seconds_from_env() -> int:
    raw = os.environ.get("ZEUS_HEARTBEAT_CADENCE_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_HEARTBEAT_CADENCE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ZEUS_HEARTBEAT_CADENCE_SECONDS must be an integer") from exc
    if value <= 0:
        raise ValueError("ZEUS_HEARTBEAT_CADENCE_SECONDS must be positive")
    return value


def heartbeat_http_timeout_seconds_from_env(cadence_seconds: int) -> float:
    raw = os.environ.get("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS")
    if raw is None or raw == "":
        value = min(DEFAULT_HEARTBEAT_HTTP_TIMEOUT_SECONDS, float(cadence_seconds) / 2.0)
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS must be numeric") from exc
    if value <= 0:
        raise ValueError("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS must be positive")
    if value >= float(cadence_seconds):
        raise ValueError(
            "ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS must be shorter than heartbeat cadence"
        )
    return value


def install_dedicated_heartbeat_http_timeout(*, cadence_seconds: int) -> None:
    """Keep heartbeat HTTP blocking time below the lease cadence.

    The CLOB heartbeat rotates a server-owned lease token. A read timeout can
    leave the client unsure whether the server accepted and rotated the token;
    if the request blocks for a full cadence, one network stall can consume the
    whole lease window and Polymarket may cancel resting GTC/GTD orders. The
    keeper runs in its own process, so replacing the SDK module's global client
    here does not affect live evaluator/orderbook traffic.
    """

    timeout_seconds = heartbeat_http_timeout_seconds_from_env(cadence_seconds)
    try:
        import httpx
        from py_clob_client_v2.http_helpers import helpers as heartbeat_http_helpers
    except Exception as exc:  # pragma: no cover - dependency absence is runtime-specific
        logger.warning("heartbeat HTTP timeout install skipped: %s", exc)
        return

    old_client = getattr(heartbeat_http_helpers, "_http_client", None)
    heartbeat_http_helpers._http_client = httpx.Client(
        http2=True,
        timeout=httpx.Timeout(timeout_seconds),
    )
    close = getattr(old_client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.debug("old heartbeat HTTP client close failed", exc_info=True)


def heartbeat_status_max_age_seconds_from_env() -> int:
    raw = os.environ.get("ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS must be an integer") from exc
    if value <= 0:
        raise ValueError("ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS must be positive")
    return value


def heartbeat_keeper_status_path() -> Path:
    from src.config import state_path

    return state_path(HEARTBEAT_KEEPER_STATUS_FILENAME)


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_invalid_heartbeat_id_error(exc: Exception | str) -> bool:
    return "Invalid Heartbeat ID" in str(exc)


def write_heartbeat_keeper_status(
    status: HeartbeatStatus,
    *,
    path: Path | None = None,
    owner: str = "zeus-venue-heartbeat",
) -> Path:
    target = path or heartbeat_keeper_status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 2,
        "owner": owner,
        "written_at": now.isoformat(),
        **status.to_dict(),
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp.replace(target)
    return target


def fresh_heartbeat_id_from_status(
    *,
    path: Path | None = None,
    max_age_seconds: int = DEFAULT_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS,
) -> str:
    """Return the latest fresh venue heartbeat chain token for restart handoff.

    The CLOB heartbeat id is a server-rotated lease token. A daemon restart must
    continue the last fresh token instead of registering a new empty chain while
    the old server-side lease is still active.
    """

    target = path or heartbeat_keeper_status_path()
    try:
        payload = json.loads(target.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    written_at = _parse_utc(payload.get("written_at"))
    if written_at is None:
        return ""
    age_seconds = (datetime.now(timezone.utc) - written_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_age_seconds:
        return ""
    if str(payload.get("health") or "").upper() != HeartbeatHealth.HEALTHY.value:
        return ""
    heartbeat_id = str(payload.get("heartbeat_id") or "").strip()
    return heartbeat_id


class ExternalHeartbeatSupervisor:
    """Read-only gate over a daemon-independent heartbeat keeper status file."""

    def __init__(
        self,
        *,
        status_path: Path | None = None,
        max_age_seconds: int | None = None,
        cadence_seconds: int | None = None,
    ) -> None:
        self._status_path = status_path or heartbeat_keeper_status_path()
        self._max_age_seconds = (
            heartbeat_status_max_age_seconds_from_env()
            if max_age_seconds is None
            else int(max_age_seconds)
        )
        self._cadence_seconds = (
            heartbeat_cadence_seconds_from_env()
            if cadence_seconds is None
            else int(cadence_seconds)
        )

    def status(self) -> HeartbeatStatus:
        try:
            payload = json.loads(self._status_path.read_text())
        except FileNotFoundError:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=None,
                consecutive_failures=0,
                heartbeat_id="external",
                cadence_seconds=self._cadence_seconds,
                last_error=f"external heartbeat status missing: {self._status_path}",
            )
        except (OSError, json.JSONDecodeError) as exc:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=None,
                consecutive_failures=0,
                heartbeat_id="external",
                cadence_seconds=self._cadence_seconds,
                last_error=f"external heartbeat status unreadable: {exc}",
            )

        written_at = _parse_utc(payload.get("written_at"))
        last_success_at = _parse_utc(payload.get("last_success_at"))
        last_failure_at = _parse_utc(payload.get("last_failure_at"))
        last_invalid_id_at = _parse_utc(payload.get("last_invalid_id_at"))
        lease_continuous_since = _parse_utc(payload.get("lease_continuous_since"))
        lease_gap_suspected_until = _parse_utc(payload.get("lease_gap_suspected_until"))
        consecutive_successes = int(payload.get("consecutive_successes") or 0)

        if written_at is None:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=last_success_at,
                consecutive_failures=int(payload.get("consecutive_failures") or 0),
                heartbeat_id="external",
                cadence_seconds=int(payload.get("cadence_seconds") or self._cadence_seconds),
                last_error="external heartbeat status missing written_at",
                last_failure_at=last_failure_at,
                last_invalid_id_at=last_invalid_id_at,
                consecutive_successes=consecutive_successes,
                lease_continuous_since=lease_continuous_since,
                lease_gap_suspected_until=lease_gap_suspected_until,
            )
        age_seconds = (datetime.now(timezone.utc) - written_at).total_seconds()
        cadence_seconds = int(payload.get("cadence_seconds") or self._cadence_seconds)
        if age_seconds > self._max_age_seconds:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=last_success_at,
                consecutive_failures=int(payload.get("consecutive_failures") or 0),
                heartbeat_id="external",
                cadence_seconds=cadence_seconds,
                last_error=(
                    f"external heartbeat status stale: age={age_seconds:.3f}s "
                    f"max_age={self._max_age_seconds}s"
                ),
                last_failure_at=last_failure_at,
                last_invalid_id_at=last_invalid_id_at,
                consecutive_successes=consecutive_successes,
                lease_continuous_since=lease_continuous_since,
                lease_gap_suspected_until=lease_gap_suspected_until,
            )
        raw_health = str(payload.get("health") or "").upper()
        try:
            health = HeartbeatHealth(raw_health)
        except ValueError:
            health = HeartbeatHealth.LOST
        return HeartbeatStatus(
            health=health,
            last_success_at=last_success_at,
            consecutive_failures=int(payload.get("consecutive_failures") or 0),
            heartbeat_id=str(payload.get("heartbeat_id") or "external"),
            cadence_seconds=cadence_seconds,
            last_error=payload.get("last_error"),
            last_failure_at=last_failure_at,
            last_invalid_id_at=last_invalid_id_at,
            consecutive_successes=consecutive_successes,
            lease_continuous_since=lease_continuous_since,
            lease_gap_suspected_until=lease_gap_suspected_until,
        )

    def gate_for_order_type(self, order_type: str | OrderType | None) -> bool:
        if not heartbeat_required_for(order_type):
            return True
        return self.status().resting_order_safe()


class HeartbeatSupervisor:
    def __init__(
        self,
        adapter: Any,
        cadence_seconds: int = DEFAULT_HEARTBEAT_CADENCE_SECONDS,
        *,
        initial_heartbeat_id: str = "",
    ) -> None:
        if cadence_seconds <= 0:
            raise ValueError("cadence_seconds must be positive")
        self._adapter = adapter
        self._cadence_seconds = int(cadence_seconds)
        # Polymarket CLOB v2 heartbeat protocol: client must NOT mint its own ID.
        # First post sends the empty string; the server creates a session and
        # returns its assigned `heartbeat_id`. Each subsequent successful post
        # rotates the canonical ID to a new value, which the client must echo
        # on the next tick. Probed against clob.polymarket.com 2026-05-01:
        #     POST {heartbeat_id:""}              -> {heartbeat_id:"A"}
        #     POST {heartbeat_id:"A"}             -> {heartbeat_id:"B"}    # rotates
        #     POST {heartbeat_id:"<bogus>"}       -> 400 Invalid Heartbeat ID
        # On any 4xx we restart the chain with "" rather than minting a UUID
        # (the previous bug — a fresh UUID never matches the server record).
        self._heartbeat_id: str = str(initial_heartbeat_id or "")
        self._health = HeartbeatHealth.STARTING
        self._last_success_at: Optional[datetime] = None
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_error: Optional[str] = None
        self._last_failure_at: Optional[datetime] = None
        self._last_invalid_id_at: Optional[datetime] = None
        self._lease_continuous_since: Optional[datetime] = None
        self._lease_gap_suspected_until: Optional[datetime] = None
        self._running = False
        self._tombstone_written = False
        self._run_once_lock = threading.Lock()

    async def start(self) -> None:
        """Run heartbeat posts until stop() is called.

        The daemon currently schedules run_once() through APScheduler; start()
        exists for async runtimes and tests that want a continuous coroutine.
        """

        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(self._cadence_seconds)

    async def stop(self) -> None:
        self._running = False

    async def run_once(self) -> HeartbeatStatus:
        """Post one heartbeat and update health state.

        Implements the Polymarket chain-token heartbeat protocol — see the
        comment in __init__. Each successful post returns the next canonical
        `heartbeat_id` which we capture for the following tick. Transient
        failures keep that id so existing resting orders stay tied to the same
        lease chain; explicit Invalid Heartbeat ID restarts from `""`.
        """

        if not self._run_once_lock.acquire(blocking=False):
            logger.warning("Venue heartbeat tick skipped: previous tick still in flight")
            return self.status()
        try:
            try:
                self._heartbeat_id = await self._post_heartbeat_once(self._heartbeat_id)
                self.record_success()
            except Exception as exc:  # fail closed, surface through status/tombstone
                # Polymarket echoes the rejected heartbeat_id in the 400 body.
                # Treating that value as a fresh hint can pin the lease owner to
                # a known-bad token until a later timeout resets the chain, which
                # is long enough for the venue to cancel resting GTC/GTD orders.
                if _is_invalid_heartbeat_id_error(exc):
                    self.record_failure(exc)
                    try:
                        self._heartbeat_id = await self._post_heartbeat_once("")
                        self.record_success()
                        return self.status()
                    except Exception as retry_exc:
                        exc = RuntimeError(
                            f"Invalid Heartbeat ID; empty-chain recovery failed: {retry_exc}"
                        )
                    self._heartbeat_id = ""  # invalid chain cannot protect resting orders
                self.record_failure(exc)
        finally:
            self._run_once_lock.release()
        return self.status()

    async def _post_heartbeat_once(self, heartbeat_id: str) -> str:
        if self._adapter is None:
            raise RuntimeError("heartbeat adapter unavailable")
        ack = self._adapter.post_heartbeat(heartbeat_id)
        if inspect.isawaitable(ack):
            ack = await ack
        if getattr(ack, "ok", True) is False:
            raise RuntimeError("heartbeat ack returned ok=False")
        raw = getattr(ack, "raw", None)
        next_id = ""
        if isinstance(raw, dict):
            next_id = str(raw.get("heartbeat_id") or "")
        if not next_id:
            raise RuntimeError("heartbeat ack missing heartbeat_id")
        return next_id

    def record_success(self) -> None:
        now = datetime.now(timezone.utc)
        if self._consecutive_successes == 0:
            self._lease_continuous_since = now
        self._health = HeartbeatHealth.HEALTHY
        self._last_success_at = now
        self._consecutive_failures = 0
        self._consecutive_successes += 1
        self._last_error = None

    def record_failure(self, exc: Exception | str) -> None:
        now = datetime.now(timezone.utc)
        self._consecutive_failures += 1
        self._consecutive_successes = 0
        self._last_error = str(exc)
        self._last_failure_at = now
        self._lease_continuous_since = None
        self._lease_gap_suspected_until = now + timedelta(
            seconds=self._cadence_seconds * DEFAULT_HEARTBEAT_LEASE_RECOVERY_SUCCESS_TICKS
        )
        if _is_invalid_heartbeat_id_error(exc):
            self._last_invalid_id_at = now
            self._health = HeartbeatHealth.LOST
            self._write_failclosed_tombstone()
        elif self._consecutive_failures == 1:
            self._health = HeartbeatHealth.DEGRADED
        else:
            self._health = HeartbeatHealth.LOST
            self._write_failclosed_tombstone()
        logger.warning(
            "Venue heartbeat failure (%s): health=%s error=%s",
            self._consecutive_failures,
            self._health.value,
            self._last_error,
        )

    def status(self) -> HeartbeatStatus:
        return HeartbeatStatus(
            health=self._health,
            last_success_at=self._last_success_at,
            consecutive_failures=self._consecutive_failures,
            heartbeat_id=self._heartbeat_id,
            cadence_seconds=self._cadence_seconds,
            last_error=self._last_error,
            last_failure_at=self._last_failure_at,
            last_invalid_id_at=self._last_invalid_id_at,
            consecutive_successes=self._consecutive_successes,
            lease_continuous_since=self._lease_continuous_since,
            lease_gap_suspected_until=self._lease_gap_suspected_until,
        )

    def gate_for_order_type(self, order_type: str | OrderType | None) -> bool:
        if not heartbeat_required_for(order_type):
            return True
        if self._tombstone_written or _failclosed_tombstone_exists():
            return False
        return self.status().resting_order_safe()

    def _write_failclosed_tombstone(self) -> None:
        # Tombstone retired 2026-05-04 — runtime safety covered by gate 6/9/10.
        pass


_GLOBAL_SUPERVISOR: Optional[Any] = None


def configure_global_supervisor(supervisor: Optional[Any]) -> None:
    global _GLOBAL_SUPERVISOR
    _GLOBAL_SUPERVISOR = supervisor


def get_global_supervisor() -> Optional[Any]:
    return _GLOBAL_SUPERVISOR


def current_status() -> HeartbeatStatus:
    supervisor = get_global_supervisor()
    if supervisor is None:
        return HeartbeatStatus(
            health=HeartbeatHealth.LOST,
            last_success_at=None,
            consecutive_failures=0,
            heartbeat_id="unconfigured",
            cadence_seconds=heartbeat_cadence_seconds_from_env(),
            last_error="heartbeat supervisor not configured",
        )
    return supervisor.status()


def _failclosed_tombstone_exists() -> bool:
    try:
        from src.config import state_path

        return state_path("auto_pause_failclosed.tombstone").exists()
    except Exception:
        return True


def assert_heartbeat_allows_order_type(order_type: str | OrderType | None = OrderType.GTC) -> None:
    normalized = _normalize_order_type(order_type)
    if not heartbeat_required_for(normalized):
        return
    supervisor = get_global_supervisor()
    status = current_status()
    allowed = supervisor.gate_for_order_type(normalized) if supervisor is not None else False
    if not allowed:
        raise HeartbeatNotHealthy(f"heartbeat={status.health.value}; order_type={normalized}; {status.last_error or ''}")


def summary() -> dict[str, Any]:
    status = current_status()
    supervisor = get_global_supervisor()
    entry_allowed = supervisor.gate_for_order_type(OrderType.GTC) if supervisor is not None else False
    payload = status.to_dict()
    payload["entry"] = {
        "allow_submit": entry_allowed,
        "required_order_types": sorted(_RESTING_ORDER_TYPES),
    }
    if not entry_allowed:
        payload["entry"]["reason"] = status.last_error or (
            "heartbeat_lease_gap_suspected"
            if status.health is HeartbeatHealth.HEALTHY
            else f"heartbeat={status.health.value}"
        )
    return payload


async def run_global_heartbeat_once() -> HeartbeatStatus:
    supervisor = get_global_supervisor()
    if supervisor is None:
        raise HeartbeatNotHealthy("heartbeat supervisor not configured")
    if not hasattr(supervisor, "run_once"):
        raise HeartbeatNotHealthy("heartbeat supervisor is external read-only")
    return await supervisor.run_once()


def run_heartbeat_keeper(
    *,
    adapter: Any | None = None,
    status_path: Path | None = None,
    cadence_seconds: int | None = None,
    max_ticks: int | None = None,
    sleep_fn: Any = time.sleep,
) -> None:
    """Run the minimal CLOB heartbeat lease owner.

    This loop owns only the venue heartbeat and its local status file. It does
    not read orders, reconcile, cancel, refresh collateral, or write DB truth.
    """

    cadence = heartbeat_cadence_seconds_from_env() if cadence_seconds is None else int(cadence_seconds)
    if adapter is None:
        install_dedicated_heartbeat_http_timeout(cadence_seconds=cadence)
        from src.data.polymarket_client import PolymarketClient

        adapter = PolymarketClient()._ensure_v2_adapter()
    initial_heartbeat_id = fresh_heartbeat_id_from_status(path=status_path)
    supervisor = HeartbeatSupervisor(
        adapter,
        cadence_seconds=cadence,
        initial_heartbeat_id=initial_heartbeat_id,
    )
    ticks = 0
    while True:
        started = datetime.now(timezone.utc)
        status = asyncio.run(supervisor.run_once())
        write_heartbeat_keeper_status(status, path=status_path)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        sleep_fn(max(0.1, cadence - elapsed))


def _main(argv: list[str] | None = None) -> int:
    _start = time.monotonic()  # F86: process start time for SIGTERM elapsed log
    # F86: forensic SIGTERM trail — logs elapsed seconds to stderr before exit.
    _log = logging.getLogger(__name__)
    signal.signal(
        signal.SIGTERM,
        lambda s, f: (
            _log.error(
                "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
                os.getpid(), os.getppid(), int(time.monotonic() - _start),
            ),
            sys.exit(0),
        ),
    )
    parser = argparse.ArgumentParser(description="Run the Zeus CLOB venue heartbeat keeper.")
    parser.add_argument("--once", action="store_true", help="post one heartbeat and exit")
    parser.add_argument("--status-path", help="override status JSON path")
    args = parser.parse_args(argv)
    status_path = Path(args.status_path).expanduser() if args.status_path else None
    run_heartbeat_keeper(status_path=status_path, max_ticks=1 if args.once else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
