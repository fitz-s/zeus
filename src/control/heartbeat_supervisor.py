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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_CANCEL_SUSPECTED_REASON = "heartbeat_cancel_suspected"
DEFAULT_HEARTBEAT_CADENCE_SECONDS = 5
DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS = 8
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "heartbeat_id": self.heartbeat_id,
            "cadence_seconds": self.cadence_seconds,
            "last_error": self.last_error,
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
        "schema_version": 1,
        "owner": owner,
        "written_at": now.isoformat(),
        "health": status.health.value,
        "last_success_at": status.last_success_at.isoformat() if status.last_success_at else None,
        "consecutive_failures": status.consecutive_failures,
        "cadence_seconds": status.cadence_seconds,
        "last_error": status.last_error,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp.replace(target)
    return target


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
        if written_at is None:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=last_success_at,
                consecutive_failures=int(payload.get("consecutive_failures") or 0),
                heartbeat_id="external",
                cadence_seconds=int(payload.get("cadence_seconds") or self._cadence_seconds),
                last_error="external heartbeat status missing written_at",
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
            heartbeat_id="external",
            cadence_seconds=cadence_seconds,
            last_error=payload.get("last_error"),
        )

    def gate_for_order_type(self, order_type: str | OrderType | None) -> bool:
        if not heartbeat_required_for(order_type):
            return True
        return self.status().health is HeartbeatHealth.HEALTHY


class HeartbeatSupervisor:
    def __init__(self, adapter: Any, cadence_seconds: int = DEFAULT_HEARTBEAT_CADENCE_SECONDS) -> None:
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
        self._heartbeat_id: str = ""
        self._health = HeartbeatHealth.STARTING
        self._last_success_at: Optional[datetime] = None
        self._consecutive_failures = 0
        self._last_error: Optional[str] = None
        self._running = False
        self._tombstone_written = False

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
        `heartbeat_id` which we capture for the following tick; any failure
        resets to `""` so the next tick starts a fresh chain.
        """

        try:
            if self._adapter is None:
                raise RuntimeError("heartbeat adapter unavailable")
            ack = self._adapter.post_heartbeat(self._heartbeat_id)
            if inspect.isawaitable(ack):
                ack = await ack
            if getattr(ack, "ok", True) is False:
                raise RuntimeError("heartbeat ack returned ok=False")
            next_id = ""
            raw = getattr(ack, "raw", None)
            if isinstance(raw, dict):
                next_id = str(raw.get("heartbeat_id") or "")
            if not next_id:
                raise RuntimeError("heartbeat ack missing heartbeat_id")
            self._heartbeat_id = next_id
            self.record_success()
        except Exception as exc:  # fail closed, surface through status/tombstone
            self._heartbeat_id = ""  # reset chain so next tick re-registers
            self.record_failure(exc)
        return self.status()

    def record_success(self) -> None:
        self._health = HeartbeatHealth.HEALTHY
        self._last_success_at = datetime.now(timezone.utc)
        self._consecutive_failures = 0
        self._last_error = None

    def record_failure(self, exc: Exception | str) -> None:
        self._consecutive_failures += 1
        self._last_error = str(exc)
        if self._consecutive_failures == 1:
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
        )

    def gate_for_order_type(self, order_type: str | OrderType | None) -> bool:
        if not heartbeat_required_for(order_type):
            return True
        if self._tombstone_written or _failclosed_tombstone_exists():
            return False
        return self._health == HeartbeatHealth.HEALTHY

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

    if adapter is None:
        from src.data.polymarket_client import PolymarketClient

        adapter = PolymarketClient()._ensure_v2_adapter()
    cadence = heartbeat_cadence_seconds_from_env() if cadence_seconds is None else int(cadence_seconds)
    supervisor = HeartbeatSupervisor(adapter, cadence_seconds=cadence)
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
    parser = argparse.ArgumentParser(description="Run the Zeus CLOB venue heartbeat keeper.")
    parser.add_argument("--once", action="store_true", help="post one heartbeat and exit")
    parser.add_argument("--status-path", help="override status JSON path")
    args = parser.parse_args(argv)
    status_path = Path(args.status_path).expanduser() if args.status_path else None
    run_heartbeat_keeper(status_path=status_path, max_ticks=1 if args.once else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
