"""Heartbeat supervision for live resting Polymarket orders.

R3 Z3: GTC/GTD placement is allowed only while the venue heartbeat is
healthy. Heartbeat loss reuses the existing fail-closed auto-pause tombstone;
it does not introduce a second control truth surface.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_CANCEL_SUSPECTED_REASON = "heartbeat_cancel_suspected"
DEFAULT_HEARTBEAT_CADENCE_SECONDS = 5
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


class HeartbeatSupervisor:
    def __init__(self, adapter: Any, cadence_seconds: int = DEFAULT_HEARTBEAT_CADENCE_SECONDS) -> None:
        if cadence_seconds <= 0:
            raise ValueError("cadence_seconds must be positive")
        self._adapter = adapter
        self._cadence_seconds = int(cadence_seconds)
        self._heartbeat_id = uuid.uuid4().hex
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
        """Post one heartbeat and update health state."""

        try:
            if self._adapter is None:
                raise RuntimeError("heartbeat adapter unavailable")
            ack = self._adapter.post_heartbeat(self._heartbeat_id)
            if inspect.isawaitable(ack):
                ack = await ack
            if getattr(ack, "ok", True) is False:
                raise RuntimeError("heartbeat ack returned ok=False")
            self.record_success()
        except Exception as exc:  # fail closed, surface through status/tombstone
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
        if self._tombstone_written:
            return
        try:
            from src.config import state_path

            path = state_path("auto_pause_failclosed.tombstone")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(HEARTBEAT_CANCEL_SUSPECTED_REASON)
            tmp.replace(path)
            self._tombstone_written = True
        except Exception as exc:
            logger.error("Failed to write heartbeat fail-closed tombstone: %s", exc)


_GLOBAL_SUPERVISOR: Optional[HeartbeatSupervisor] = None


def configure_global_supervisor(supervisor: Optional[HeartbeatSupervisor]) -> None:
    global _GLOBAL_SUPERVISOR
    _GLOBAL_SUPERVISOR = supervisor


def get_global_supervisor() -> Optional[HeartbeatSupervisor]:
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
    return await supervisor.run_once()
