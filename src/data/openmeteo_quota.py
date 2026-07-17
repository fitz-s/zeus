"""Cross-process Open-Meteo quota authority."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

DAILY_LIMIT = 10_000
HOURLY_LIMIT = 5_000
MINUTE_LIMIT = 600
WARN_THRESHOLD = 0.80
HARD_THRESHOLD = 0.95
RATE_LIMIT_COOLDOWN_SECONDS = 60

DAILY_HARD_CAP = int(DAILY_LIMIT * HARD_THRESHOLD)
HOURLY_HARD_CAP = int(HOURLY_LIMIT * HARD_THRESHOLD)
MINUTE_HARD_CAP = int(MINUTE_LIMIT * HARD_THRESHOLD)

# Maintenance must leave capacity for source-clock captures after a new run lands.
CRITICAL_DAILY_RESERVE = 500
CRITICAL_HOURLY_RESERVE = 250
CRITICAL_MINUTE_RESERVE = 60
MAINTENANCE_DAILY_LIMIT = DAILY_HARD_CAP - CRITICAL_DAILY_RESERVE
MAINTENANCE_HOURLY_LIMIT = HOURLY_HARD_CAP - CRITICAL_HOURLY_RESERVE
MAINTENANCE_MINUTE_LIMIT = MINUTE_HARD_CAP - CRITICAL_MINUTE_RESERVE

_T = TypeVar("_T")


class OpenMeteoQuotaTracker:
    """Quota tracker shared by live processes and persistent across restarts."""

    def __init__(self, *, state_path: Path | None = None) -> None:
        now = datetime.now(timezone.utc)
        self._count = 0
        self._today = now.date()
        self._hour_key = now.strftime("%Y-%m-%dT%H")
        self._hour_count = 0
        self._minute_key = now.strftime("%Y-%m-%dT%H:%M")
        self._minute_count = 0
        self._blocked_until: datetime | None = None
        self._lock = threading.Lock()
        self._priority = threading.local()
        self._state_path = Path(state_path) if state_path is not None else None

    @staticmethod
    def _utc_today() -> date:
        return datetime.now(timezone.utc).date()

    def _shared_enabled(self) -> bool:
        return self._state_path is not None and not (
            os.environ.get("ZEUS_TESTING") == "1"
            or "PYTEST_CURRENT_TEST" in os.environ
        )

    def _is_priority(self) -> bool:
        return bool(getattr(self._priority, "depth", 0))

    @contextlib.contextmanager
    def priority_lane(self):
        """Allow source-clock work to consume the reserved quota tranche."""

        depth = int(getattr(self._priority, "depth", 0))
        self._priority.depth = depth + 1
        try:
            yield
        finally:
            self._priority.depth = depth

    @staticmethod
    def _default_state(now: datetime) -> dict[str, object]:
        return {
            "schema_version": 1,
            "day": now.date().isoformat(),
            "day_count": 0,
            "hour": now.strftime("%Y-%m-%dT%H"),
            "hour_count": 0,
            "minute": now.strftime("%Y-%m-%dT%H:%M"),
            "minute_count": 0,
            "blocked_until": None,
        }

    @classmethod
    def _normalize_state(
        cls,
        state: dict[str, object],
        now: datetime,
    ) -> bool:
        changed = False
        defaults = cls._default_state(now)
        if int(state.get("schema_version") or 0) != 1:
            state.clear()
            state.update(defaults)
            return True
        for key in defaults:
            if key not in state:
                state[key] = defaults[key]
                changed = True
        day = now.date().isoformat()
        if state["day"] != day:
            state["day"] = day
            state["day_count"] = 0
            changed = True
        hour = now.strftime("%Y-%m-%dT%H")
        if state["hour"] != hour:
            state["hour"] = hour
            state["hour_count"] = 0
            changed = True
        minute = now.strftime("%Y-%m-%dT%H:%M")
        if state["minute"] != minute:
            state["minute"] = minute
            state["minute_count"] = 0
            changed = True
        return changed

    def _shared(self, operation: Callable[[dict[str, object], datetime], tuple[_T, bool]]) -> _T:
        path = self._state_path
        if path is None:
            raise RuntimeError("Open-Meteo shared quota path is unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                now = datetime.now(timezone.utc)
                if path.exists():
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise RuntimeError(
                            "Open-Meteo shared quota state is unreadable"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise RuntimeError(
                            "Open-Meteo shared quota state is not an object"
                        )
                    state = payload
                else:
                    state = self._default_state(now)
                normalized = self._normalize_state(state, now)
                result, changed = operation(state, now)
                if normalized or changed or not path.exists():
                    temp = path.with_name(
                        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                    )
                    try:
                        temp.write_text(
                            json.dumps(state, sort_keys=True, separators=(",", ":")),
                            encoding="utf-8",
                        )
                        os.replace(temp, path)
                    finally:
                        with contextlib.suppress(FileNotFoundError):
                            temp.unlink()
                return result
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _check_reset(self) -> None:
        now = datetime.now(timezone.utc)
        if now.date() != self._today:
            self._count = 0
            self._today = now.date()
        hour = now.strftime("%Y-%m-%dT%H")
        if hour != self._hour_key:
            self._hour_key = hour
            self._hour_count = 0
        minute = now.strftime("%Y-%m-%dT%H:%M")
        if minute != self._minute_key:
            self._minute_key = minute
            self._minute_count = 0

    @staticmethod
    def _blocked_until_from_state(state: dict[str, object]) -> datetime | None:
        raw = state.get("blocked_until")
        if raw is None or not str(raw).strip():
            return None
        try:
            return datetime.fromisoformat(str(raw)).astimezone(timezone.utc)
        except ValueError as exc:
            raise RuntimeError("Open-Meteo shared cooldown timestamp is invalid") from exc

    @staticmethod
    def _limits(priority: bool) -> tuple[int, int, int]:
        if priority:
            return DAILY_HARD_CAP, HOURLY_HARD_CAP, MINUTE_HARD_CAP
        return (
            MAINTENANCE_DAILY_LIMIT,
            MAINTENANCE_HOURLY_LIMIT,
            MAINTENANCE_MINUTE_LIMIT,
        )

    @classmethod
    def _state_allows(
        cls,
        state: dict[str, object],
        now: datetime,
        *,
        priority: bool,
    ) -> tuple[bool, str | None]:
        blocked_until = cls._blocked_until_from_state(state)
        if blocked_until is not None and now < blocked_until:
            return False, f"cooldown_until={blocked_until.isoformat()}"
        limits = cls._limits(priority)
        counts = (
            int(state.get("day_count") or 0),
            int(state.get("hour_count") or 0),
            int(state.get("minute_count") or 0),
        )
        labels = ("day", "hour", "minute")
        for label, count, limit in zip(labels, counts, limits, strict=True):
            if count >= limit:
                return False, f"{label}_limit={count}/{limit}"
        return True, None

    def _local_allows(self, now: datetime, *, priority: bool) -> tuple[bool, str | None]:
        if self._blocked_until is not None and now < self._blocked_until:
            return False, f"cooldown_until={self._blocked_until.isoformat()}"
        limits = self._limits(priority)
        counts = (self._count, self._hour_count, self._minute_count)
        labels = ("day", "hour", "minute")
        for label, count, limit in zip(labels, counts, limits, strict=True):
            if count >= limit:
                return False, f"{label}_limit={count}/{limit}"
        return True, None

    def can_call(self) -> bool:
        priority = self._is_priority()
        if self._shared_enabled():
            try:
                allowed, reason = self._shared(
                    lambda state, now: (
                        self._state_allows(state, now, priority=priority),
                        False,
                    )
                )
            except RuntimeError:
                logger.exception("Open-Meteo shared quota authority failed closed")
                return False
        else:
            with self._lock:
                self._check_reset()
                allowed, reason = self._local_allows(
                    datetime.now(timezone.utc),
                    priority=priority,
                )
        if not allowed:
            logger.warning(
                "Open-Meteo quota blocked priority=%s reason=%s",
                priority,
                reason,
            )
        return allowed

    @staticmethod
    def _log_usage(count: int, endpoint: str) -> None:
        usage = count / DAILY_LIMIT
        suffix = f" [{endpoint}]" if endpoint else ""
        if usage >= HARD_THRESHOLD:
            logger.critical(
                "Open-Meteo quota CRITICAL%s: %d/%d (%.1f%%)",
                suffix,
                count,
                DAILY_LIMIT,
                usage * 100.0,
            )
        elif usage >= WARN_THRESHOLD:
            logger.warning(
                "Open-Meteo quota WARNING%s: %d/%d (%.1f%%)",
                suffix,
                count,
                DAILY_LIMIT,
                usage * 100.0,
            )

    def acquire_call(self, endpoint: str = "") -> bool:
        """Atomically reserve one actual HTTP attempt before it is sent."""

        priority = self._is_priority()

        def acquire(
            state: dict[str, object], now: datetime
        ) -> tuple[tuple[bool, str | None, int], bool]:
            allowed, reason = self._state_allows(state, now, priority=priority)
            if not allowed:
                return (False, reason, int(state.get("day_count") or 0)), False
            state["day_count"] = int(state.get("day_count") or 0) + 1
            state["hour_count"] = int(state.get("hour_count") or 0) + 1
            state["minute_count"] = int(state.get("minute_count") or 0) + 1
            return (True, None, int(state["day_count"])), True

        if self._shared_enabled():
            try:
                allowed, reason, count = self._shared(acquire)
            except RuntimeError:
                logger.exception("Open-Meteo shared quota reservation failed closed")
                return False
        else:
            with self._lock:
                self._check_reset()
                now = datetime.now(timezone.utc)
                allowed, reason = self._local_allows(now, priority=priority)
                if allowed:
                    self._count += 1
                    self._hour_count += 1
                    self._minute_count += 1
                count = self._count
        if not allowed:
            logger.warning(
                "Open-Meteo quota reservation blocked%s priority=%s reason=%s",
                f" [{endpoint}]" if endpoint else "",
                priority,
                reason,
            )
        else:
            self._log_usage(count, endpoint)
        return allowed

    def note_rate_limited(self, retry_after_seconds: int | float | None = None) -> None:
        cooldown = max(RATE_LIMIT_COOLDOWN_SECONDS, int(retry_after_seconds or 0))
        blocked_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
            seconds=cooldown
        )
        if self._shared_enabled():
            try:
                def block(state: dict[str, object], _now: datetime) -> tuple[None, bool]:
                    current = self._blocked_until_from_state(state)
                    if current is None or blocked_until > current:
                        state["blocked_until"] = blocked_until.isoformat()
                        return None, True
                    return None, False

                self._shared(block)
            except RuntimeError:
                logger.exception("Open-Meteo shared cooldown write failed")
        else:
            with self._lock:
                if self._blocked_until is None or blocked_until > self._blocked_until:
                    self._blocked_until = blocked_until
        logger.warning(
            "Open-Meteo 429 cooldown engaged for %ds until %s",
            cooldown,
            blocked_until.isoformat(),
        )

    def calls_today(self) -> int:
        if self._shared_enabled():
            try:
                return self._shared(
                    lambda state, _now: (int(state.get("day_count") or 0), False)
                )
            except RuntimeError:
                logger.exception("Open-Meteo shared quota read failed")
                return DAILY_HARD_CAP
        with self._lock:
            self._check_reset()
            return self._count

    def calls_remaining(self) -> int:
        return max(0, DAILY_HARD_CAP - self.calls_today())

    def cooldown_remaining_seconds(self) -> int:
        if self._shared_enabled():
            try:
                def remaining(state: dict[str, object], now: datetime) -> tuple[int, bool]:
                    blocked_until = self._blocked_until_from_state(state)
                    if blocked_until is None:
                        return 0, False
                    return max(0, int((blocked_until - now).total_seconds())), False

                return self._shared(remaining)
            except RuntimeError:
                logger.exception("Open-Meteo shared cooldown read failed")
                return RATE_LIMIT_COOLDOWN_SECONDS
        with self._lock:
            if self._blocked_until is None:
                return 0
            remaining = int(
                (self._blocked_until - datetime.now(timezone.utc)).total_seconds()
            )
            return max(0, remaining)


def runtime_openmeteo_quota_tracker() -> OpenMeteoQuotaTracker:
    from src.config import state_path

    return OpenMeteoQuotaTracker(state_path=state_path("openmeteo_quota.json"))


quota_tracker = runtime_openmeteo_quota_tracker()
