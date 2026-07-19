"""Cross-process Open-Meteo quota authority."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import random
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, TypeVar

logger = logging.getLogger(__name__)

DAILY_LIMIT = 10_000
HOURLY_LIMIT = 5_000
MINUTE_LIMIT = 600
WARN_THRESHOLD = 0.80
HARD_THRESHOLD = 0.95
RATE_LIMIT_COOLDOWN_SECONDS = 60
REQUEST_STATE_SCHEMA_VERSION = 2
MAX_REQUEST_STATES = 512
REQUEST_STATE_TTL = timedelta(hours=24)
REQUEST_RETRY_BASE_SECONDS = 2.0
REQUEST_RETRY_MAX_SECONDS = 300.0
REQUEST_LEASE_SECONDS = 45.0

DAILY_HARD_CAP = int(DAILY_LIMIT * HARD_THRESHOLD)
HOURLY_HARD_CAP = int(HOURLY_LIMIT * HARD_THRESHOLD)
MINUTE_HARD_CAP = int(MINUTE_LIMIT * HARD_THRESHOLD)

# Quota has three economic priorities.  Maintenance must leave one tranche for
# newly published source runs, and source-clock capture must leave another for
# held Day0 positions whose probability needs a fresh remaining-day path.
SOURCE_CLOCK_DAILY_RESERVE = 500
SOURCE_CLOCK_HOURLY_RESERVE = 250
SOURCE_CLOCK_MINUTE_RESERVE = 60
CRITICAL_DAILY_RESERVE = 500
CRITICAL_HOURLY_RESERVE = 250
CRITICAL_MINUTE_RESERVE = 60
PRIORITY_DAILY_LIMIT = DAILY_HARD_CAP - CRITICAL_DAILY_RESERVE
PRIORITY_HOURLY_LIMIT = HOURLY_HARD_CAP - CRITICAL_HOURLY_RESERVE
PRIORITY_MINUTE_LIMIT = MINUTE_HARD_CAP - CRITICAL_MINUTE_RESERVE
MAINTENANCE_DAILY_LIMIT = PRIORITY_DAILY_LIMIT - SOURCE_CLOCK_DAILY_RESERVE
MAINTENANCE_HOURLY_LIMIT = PRIORITY_HOURLY_LIMIT - SOURCE_CLOCK_HOURLY_RESERVE
MAINTENANCE_MINUTE_LIMIT = PRIORITY_MINUTE_LIMIT - SOURCE_CLOCK_MINUTE_RESERVE

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
        self._request_states: dict[str, object] = {}
        self._lock = threading.Lock()
        self._priority = threading.local()
        self._critical = threading.local()
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

    def _is_critical(self) -> bool:
        return bool(getattr(self._critical, "depth", 0))

    @contextlib.contextmanager
    def priority_lane(self):
        """Allow source-clock work to consume the reserved quota tranche."""

        depth = int(getattr(self._priority, "depth", 0))
        self._priority.depth = depth + 1
        try:
            yield
        finally:
            self._priority.depth = depth

    @contextlib.contextmanager
    def critical_lane(self):
        """Allow held Day0 probability refresh to consume the final reserve."""

        depth = int(getattr(self._critical, "depth", 0))
        self._critical.depth = depth + 1
        try:
            yield
        finally:
            self._critical.depth = depth

    @staticmethod
    def _default_state(now: datetime) -> dict[str, object]:
        return {
            "schema_version": REQUEST_STATE_SCHEMA_VERSION,
            "day": now.date().isoformat(),
            "day_count": 0,
            "hour": now.strftime("%Y-%m-%dT%H"),
            "hour_count": 0,
            "minute": now.strftime("%Y-%m-%dT%H:%M"),
            "minute_count": 0,
            "blocked_until": None,
            "requests": {},
        }

    @classmethod
    def _normalize_state(
        cls,
        state: dict[str, object],
        now: datetime,
    ) -> bool:
        changed = False
        defaults = cls._default_state(now)
        schema_version = int(state.get("schema_version") or 0)
        if schema_version == 1:
            state["schema_version"] = REQUEST_STATE_SCHEMA_VERSION
            state["requests"] = {}
            changed = True
        elif schema_version != REQUEST_STATE_SCHEMA_VERSION:
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
        if cls._prune_request_states(state, now):
            changed = True
        return changed

    @staticmethod
    def _request_priority(priority: bool, critical: bool) -> str:
        if critical:
            return "critical"
        if priority:
            return "priority"
        return "maintenance"

    @staticmethod
    def _bounded_text(value: str, *, limit: int = 160) -> str:
        return str(value).strip()[:limit]

    @staticmethod
    def _parse_timestamp(raw: object) -> datetime | None:
        if raw is None or not str(raw).strip():
            return None
        try:
            return datetime.fromisoformat(str(raw)).astimezone(timezone.utc)
        except ValueError:
            return None

    @classmethod
    def _request_entries(cls, state: dict[str, object]) -> dict[str, object]:
        entries = state.get("requests")
        if not isinstance(entries, dict):
            entries = {}
            state["requests"] = entries
        return entries

    @classmethod
    def _prune_request_states(cls, state: dict[str, object], now: datetime) -> bool:
        entries = cls._request_entries(state)
        changed = False
        for request_id, entry in list(entries.items()):
            if not isinstance(request_id, str) or not isinstance(entry, dict):
                del entries[request_id]
                changed = True
                continue
            updated_at = cls._parse_timestamp(entry.get("updated_at"))
            next_retry_at = cls._parse_timestamp(entry.get("next_retry_at"))
            in_flight_until = cls._parse_timestamp(entry.get("in_flight_until"))
            if updated_at is None or (
                updated_at + REQUEST_STATE_TTL <= now
                and (next_retry_at is None or next_retry_at <= now)
                and (in_flight_until is None or in_flight_until <= now)
            ):
                del entries[request_id]
                changed = True
        if len(entries) > MAX_REQUEST_STATES:
            oldest = sorted(
                (
                    request_id
                    for request_id, entry in entries.items()
                    if (
                        cls._parse_timestamp(entry.get("next_retry_at")) is None
                        or cls._parse_timestamp(entry.get("next_retry_at")) <= now
                    )
                    and (
                        cls._parse_timestamp(entry.get("in_flight_until")) is None
                        or cls._parse_timestamp(entry.get("in_flight_until")) <= now
                    )
                ),
                key=lambda request_id: (
                    cls._parse_timestamp(
                        entries[request_id].get("updated_at")
                    )
                    or datetime.min.replace(tzinfo=timezone.utc),
                    request_id,
                ),
            )
            for request_id in oldest[: max(0, len(entries) - MAX_REQUEST_STATES)]:
                del entries[request_id]
                changed = True
        return changed

    @classmethod
    def _request_retry_after(
        cls,
        state: dict[str, object],
        request_id: str,
        now: datetime,
    ) -> tuple[int, str | None]:
        entry = cls._request_entries(state).get(request_id)
        if not isinstance(entry, dict):
            return 0, None
        retry_at = cls._parse_timestamp(entry.get("next_retry_at"))
        if retry_at is None or retry_at <= now:
            return 0, None
        return max(1, int((retry_at - now).total_seconds()) + 1), retry_at.isoformat()

    @classmethod
    def _request_in_flight_until(
        cls,
        state: dict[str, object],
        request_id: str,
        now: datetime,
    ) -> str | None:
        entry = cls._request_entries(state).get(request_id)
        if not isinstance(entry, dict):
            return None
        in_flight_until = cls._parse_timestamp(entry.get("in_flight_until"))
        if in_flight_until is None or in_flight_until <= now:
            return None
        return in_flight_until.isoformat()

    @classmethod
    def _request_state_has_capacity(
        cls,
        state: dict[str, object],
        request_id: str,
        now: datetime,
    ) -> bool:
        entries = cls._request_entries(state)
        if request_id in entries:
            return True
        cls._prune_request_states(state, now)
        if len(entries) < MAX_REQUEST_STATES:
            return True
        inactive = sorted(
            (
                candidate
                for candidate, entry in entries.items()
                if (
                    cls._parse_timestamp(entry.get("next_retry_at")) is None
                    or cls._parse_timestamp(entry.get("next_retry_at")) <= now
                )
                and (
                    cls._parse_timestamp(entry.get("in_flight_until")) is None
                    or cls._parse_timestamp(entry.get("in_flight_until")) <= now
                )
            ),
            key=lambda candidate: (
                cls._parse_timestamp(entries[candidate].get("updated_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                candidate,
            ),
        )
        if not inactive:
            return False
        del entries[inactive[0]]
        return True

    @classmethod
    def _record_request(
        cls,
        state: dict[str, object],
        now: datetime,
        *,
        request_id: str,
        endpoint: str,
        job: str,
        priority: str,
        outcome: str,
        failure_count: int | None = None,
        next_retry_at: datetime | None = None,
        lease_id: str | None = None,
        in_flight_until: datetime | None = None,
        http_outcome: Mapping[str, object] | None = None,
    ) -> None:
        entries = cls._request_entries(state)
        old = entries.get(request_id)
        prior_attempts = int(old.get("attempts") or 0) if isinstance(old, dict) else 0
        prior_failures = int(old.get("failure_count") or 0) if isinstance(old, dict) else 0
        entry: dict[str, object] = {
            "endpoint": cls._bounded_text(endpoint),
            "job": cls._bounded_text(job),
            "priority": priority,
            "outcome": outcome,
            "attempts": prior_attempts + (1 if outcome == "attempt" else 0),
            "failure_count": max(
                0,
                prior_failures if failure_count is None else int(failure_count),
            ),
            "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
            "lease_id": lease_id,
            "in_flight_until": (
                in_flight_until.isoformat() if in_flight_until else None
            ),
            "owner_pid": os.getpid(),
            "quota_cost": 1,
            "updated_at": now.isoformat(),
        }
        if http_outcome is not None:
            entry["http_outcome"] = {
                key: http_outcome[key]
                for key in (
                    "status_code",
                    "retry_class",
                    "retry_after_seconds",
                    "reason",
                    "body_sha256",
                )
                if key in http_outcome
            }
        entries[request_id] = entry
        cls._prune_request_states(state, now)

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
    def _limits(priority: bool, critical: bool = False) -> tuple[int, int, int]:
        if critical:
            return DAILY_HARD_CAP, HOURLY_HARD_CAP, MINUTE_HARD_CAP
        if priority:
            return PRIORITY_DAILY_LIMIT, PRIORITY_HOURLY_LIMIT, PRIORITY_MINUTE_LIMIT
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
        critical: bool = False,
    ) -> tuple[bool, str | None]:
        blocked_until = cls._blocked_until_from_state(state)
        if blocked_until is not None and now < blocked_until:
            return False, f"cooldown_until={blocked_until.isoformat()}"
        limits = cls._limits(priority, critical)
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

    def _local_allows(
        self,
        now: datetime,
        *,
        priority: bool,
        critical: bool = False,
    ) -> tuple[bool, str | None]:
        if self._blocked_until is not None and now < self._blocked_until:
            return False, f"cooldown_until={self._blocked_until.isoformat()}"
        limits = self._limits(priority, critical)
        counts = (self._count, self._hour_count, self._minute_count)
        labels = ("day", "hour", "minute")
        for label, count, limit in zip(labels, counts, limits, strict=True):
            if count >= limit:
                return False, f"{label}_limit={count}/{limit}"
        return True, None

    def can_call(self) -> bool:
        priority = self._is_priority()
        critical = self._is_critical()
        if self._shared_enabled():
            try:
                allowed, reason = self._shared(
                    lambda state, now: (
                        self._state_allows(
                            state,
                            now,
                            priority=priority,
                            critical=critical,
                        ),
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
                    critical=critical,
                )
        if not allowed:
            logger.warning(
                "Open-Meteo quota blocked priority=%s critical=%s reason=%s",
                priority,
                critical,
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
        critical = self._is_critical()

        def acquire(
            state: dict[str, object], now: datetime
        ) -> tuple[tuple[bool, str | None, int], bool]:
            allowed, reason = self._state_allows(
                state,
                now,
                priority=priority,
                critical=critical,
            )
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
                allowed, reason = self._local_allows(
                    now,
                    priority=priority,
                    critical=critical,
                )
                if allowed:
                    self._count += 1
                    self._hour_count += 1
                    self._minute_count += 1
                count = self._count
        if not allowed:
            logger.warning(
                "Open-Meteo quota reservation blocked%s priority=%s critical=%s reason=%s",
                f" [{endpoint}]" if endpoint else "",
                priority,
                critical,
                reason,
            )
        else:
            self._log_usage(count, endpoint)
        return allowed

    def acquire_request(
        self,
        request_id: str,
        *,
        endpoint: str = "",
        job: str = "",
        lease_seconds: float = REQUEST_LEASE_SECONDS,
    ) -> tuple[bool, str | None, str | None]:
        """Atomically reserve one quota unit and one request-scoped attempt lease."""

        priority = self._is_priority()
        critical = self._is_critical()
        priority_name = self._request_priority(priority, critical)
        lease_id = secrets.token_hex(16)
        lease_seconds = max(1.0, min(float(lease_seconds), REQUEST_RETRY_MAX_SECONDS))

        def acquire(
            state: dict[str, object], now: datetime
        ) -> tuple[tuple[bool, str | None, str | None, int], bool]:
            existing = self._request_entries(state).get(request_id)
            if isinstance(existing, dict) and existing.get("outcome") == "terminal_http":
                outcome = existing.get("http_outcome")
                status = (
                    int(outcome.get("status_code") or 0)
                    if isinstance(outcome, dict)
                    else 0
                )
                return (
                    False,
                    f"request_terminal=status={status or 'unknown'}",
                    None,
                    int(state.get("day_count") or 0),
                ), False
            retry_after, retry_at = self._request_retry_after(state, request_id, now)
            if retry_after:
                return (
                    False,
                    f"request_retry_until={retry_at}",
                    None,
                    int(state.get("day_count") or 0),
                ), False
            in_flight_until = self._request_in_flight_until(state, request_id, now)
            if in_flight_until is not None:
                return (
                    False,
                    f"request_in_flight_until={in_flight_until}",
                    None,
                    int(state.get("day_count") or 0),
                ), False
            if not self._request_state_has_capacity(state, request_id, now):
                return (
                    False,
                    f"request_state_capacity={MAX_REQUEST_STATES}",
                    None,
                    int(state.get("day_count") or 0),
                ), False
            allowed, reason = self._state_allows(
                state,
                now,
                priority=priority,
                critical=critical,
            )
            if not allowed:
                return (
                    False,
                    reason,
                    None,
                    int(state.get("day_count") or 0),
                ), False
            state["day_count"] = int(state.get("day_count") or 0) + 1
            state["hour_count"] = int(state.get("hour_count") or 0) + 1
            state["minute_count"] = int(state.get("minute_count") or 0) + 1
            self._record_request(
                state,
                now,
                request_id=request_id,
                endpoint=endpoint,
                job=job,
                priority=priority_name,
                outcome="attempt",
                lease_id=lease_id,
                in_flight_until=now + timedelta(seconds=lease_seconds),
            )
            return (True, None, lease_id, int(state["day_count"])), True

        if self._shared_enabled():
            try:
                allowed, reason, acquired_lease_id, count = self._shared(acquire)
            except RuntimeError:
                logger.exception("Open-Meteo shared request reservation failed closed")
                return False, "shared_quota_unavailable", None
        else:
            with self._lock:
                self._check_reset()
                now = datetime.now(timezone.utc)
                local_state: dict[str, object] = {"requests": self._request_states}
                self._prune_request_states(local_state, now)
                existing = self._request_entries(local_state).get(request_id)
                if isinstance(existing, dict) and existing.get("outcome") == "terminal_http":
                    outcome = existing.get("http_outcome")
                    status = (
                        int(outcome.get("status_code") or 0)
                        if isinstance(outcome, dict)
                        else 0
                    )
                    allowed = False
                    reason = f"request_terminal=status={status or 'unknown'}"
                elif (retry_after_and_at := self._request_retry_after(
                    local_state, request_id, now
                ))[0]:
                    allowed = False
                    reason = f"request_retry_until={retry_after_and_at[1]}"
                elif (
                    in_flight_until := self._request_in_flight_until(
                        local_state, request_id, now
                    )
                ) is not None:
                    allowed = False
                    reason = f"request_in_flight_until={in_flight_until}"
                elif not self._request_state_has_capacity(
                    local_state, request_id, now
                ):
                    allowed = False
                    reason = f"request_state_capacity={MAX_REQUEST_STATES}"
                else:
                    allowed, reason = self._local_allows(
                        now,
                        priority=priority,
                        critical=critical,
                    )
                if allowed:
                    self._count += 1
                    self._hour_count += 1
                    self._minute_count += 1
                    self._record_request(
                        local_state,
                        now,
                        request_id=request_id,
                        endpoint=endpoint,
                        job=job,
                        priority=priority_name,
                        outcome="attempt",
                        lease_id=lease_id,
                        in_flight_until=now + timedelta(seconds=lease_seconds),
                    )
                    acquired_lease_id = lease_id
                else:
                    acquired_lease_id = None
                count = self._count
        if allowed:
            self._log_usage(count, endpoint)
        else:
            logger.warning(
                "Open-Meteo request reservation blocked [%s] priority=%s reason=%s",
                endpoint or request_id,
                priority_name,
                reason,
            )
        return allowed, reason, acquired_lease_id

    @classmethod
    def _lease_matches(
        cls,
        state: dict[str, object],
        request_id: str,
        lease_id: str | None,
        now: datetime,
    ) -> bool:
        entry = cls._request_entries(state).get(request_id)
        if not isinstance(entry, dict):
            return lease_id is None
        current = entry.get("lease_id")
        in_flight_until = cls._parse_timestamp(entry.get("in_flight_until"))
        if lease_id is not None:
            return (
                current == lease_id
                and in_flight_until is not None
                and in_flight_until > now
            )
        return current is None or in_flight_until is None or in_flight_until <= now

    def record_request_success(
        self,
        request_id: str,
        *,
        endpoint: str = "",
        job: str = "",
        lease_id: str | None = None,
    ) -> bool:
        """Record a fresh response and clear only this request's retry embargo."""

        priority = self._request_priority(self._is_priority(), self._is_critical())

        def record(state: dict[str, object], now: datetime) -> tuple[bool, bool]:
            if not self._lease_matches(state, request_id, lease_id, now):
                return False, False
            if not self._request_state_has_capacity(state, request_id, now):
                return False, False
            self._record_request(
                state,
                now,
                request_id=request_id,
                endpoint=endpoint,
                job=job,
                priority=priority,
                outcome="success",
                failure_count=0,
            )
            return True, True

        if self._shared_enabled():
            return self._shared(record)
        with self._lock:
            now = datetime.now(timezone.utc)
            state = {"requests": self._request_states}
            if not self._lease_matches(state, request_id, lease_id, now):
                return False
            if not self._request_state_has_capacity(state, request_id, now):
                return False
            self._record_request(
                state,
                now,
                request_id=request_id,
                endpoint=endpoint,
                job=job,
                priority=priority,
                outcome="success",
                failure_count=0,
            )
            return True

    def record_request_retry(
        self,
        request_id: str,
        *,
        endpoint: str = "",
        job: str = "",
        retry_after_seconds: float | None = None,
        lease_id: str | None = None,
        http_outcome: Mapping[str, object] | None = None,
    ) -> int:
        """Persist a bounded full-jitter embargo after a failed request."""

        priority = self._request_priority(self._is_priority(), self._is_critical())

        def record(state: dict[str, object], now: datetime) -> tuple[int, bool]:
            if not self._lease_matches(state, request_id, lease_id, now):
                return 0, False
            if not self._request_state_has_capacity(state, request_id, now):
                return 0, False
            old = self._request_entries(state).get(request_id)
            prior_failures = int(old.get("failure_count") or 0) if isinstance(old, dict) else 0
            failures = min(prior_failures + 1, 16)
            cap = min(
                REQUEST_RETRY_MAX_SECONDS,
                REQUEST_RETRY_BASE_SECONDS * (2 ** (failures - 1)),
            )
            delay = min(cap, max(0.001, random.uniform(0.0, cap)))
            if retry_after_seconds is not None:
                delay = max(delay, float(retry_after_seconds))
            retry_at = now + timedelta(seconds=delay)
            self._record_request(
                state,
                now,
                request_id=request_id,
                endpoint=endpoint,
                job=job,
                priority=priority,
                outcome="rate_limited" if retry_after_seconds is not None else "transport_error",
                failure_count=failures,
                next_retry_at=retry_at,
                http_outcome=http_outcome,
            )
            return max(1, int(delay) + 1), True

        if self._shared_enabled():
            return self._shared(record)
        with self._lock:
            return record({"requests": self._request_states}, datetime.now(timezone.utc))[0]

    def record_request_terminal(
        self,
        request_id: str,
        *,
        endpoint: str = "",
        job: str = "",
        lease_id: str | None = None,
        http_outcome: Mapping[str, object],
    ) -> bool:
        """Persist a deterministic HTTP failure for this exact request identity.

        Only classification metadata is stored; URLs, query values, and response bodies
        never enter the shared state file.
        """

        priority = self._request_priority(self._is_priority(), self._is_critical())

        def record(state: dict[str, object], now: datetime) -> tuple[bool, bool]:
            if not self._lease_matches(state, request_id, lease_id, now):
                return False, False
            if not self._request_state_has_capacity(state, request_id, now):
                return False, False
            self._record_request(
                state,
                now,
                request_id=request_id,
                endpoint=endpoint,
                job=job,
                priority=priority,
                outcome="terminal_http",
                failure_count=0,
                http_outcome=http_outcome,
            )
            return True, True

        if self._shared_enabled():
            return self._shared(record)
        with self._lock:
            return record({"requests": self._request_states}, datetime.now(timezone.utc))[0]

    def request_terminal_outcome(self, request_id: str) -> dict[str, object] | None:
        """Return the redacted terminal outcome persisted for one exact request."""

        def read(state: dict[str, object], _now: datetime) -> tuple[dict[str, object] | None, bool]:
            entry = self._request_entries(state).get(request_id)
            outcome = entry.get("http_outcome") if isinstance(entry, dict) else None
            return (dict(outcome) if isinstance(outcome, dict) else None), False

        if self._shared_enabled():
            return self._shared(read)
        with self._lock:
            return read({"requests": self._request_states}, datetime.now(timezone.utc))[0]

    @classmethod
    def _retry_after_for_counts(
        cls,
        *,
        now: datetime,
        blocked_until: datetime | None,
        counts: tuple[int, int, int],
        priority: bool,
        critical: bool,
    ) -> int:
        waits: list[float] = []
        if blocked_until is not None and blocked_until > now:
            waits.append((blocked_until - now).total_seconds())
        limits = cls._limits(priority, critical)
        if counts[0] >= limits[0]:
            next_day = datetime.combine(
                now.date() + timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            waits.append((next_day - now).total_seconds())
        if counts[1] >= limits[1]:
            next_hour = now.replace(
                minute=0,
                second=0,
                microsecond=0,
            ) + timedelta(hours=1)
            waits.append((next_hour - now).total_seconds())
        if counts[2] >= limits[2]:
            next_minute = now.replace(
                second=0,
                microsecond=0,
            ) + timedelta(minutes=1)
            waits.append((next_minute - now).total_seconds())
        return max(0, int(max(waits, default=0.0)) + (1 if waits else 0))

    def retry_after_seconds(self) -> int:
        """Seconds until the active quota lane can make another reservation."""

        priority = self._is_priority()
        critical = self._is_critical()
        if self._shared_enabled():
            try:
                return self._shared(
                    lambda state, now: (
                        self._retry_after_for_counts(
                            now=now,
                            blocked_until=self._blocked_until_from_state(state),
                            counts=(
                                int(state.get("day_count") or 0),
                                int(state.get("hour_count") or 0),
                                int(state.get("minute_count") or 0),
                            ),
                            priority=priority,
                            critical=critical,
                        ),
                        False,
                    )
                )
            except RuntimeError:
                logger.exception("Open-Meteo shared quota retry window read failed")
                return RATE_LIMIT_COOLDOWN_SECONDS
        with self._lock:
            self._check_reset()
            return self._retry_after_for_counts(
                now=datetime.now(timezone.utc),
                blocked_until=self._blocked_until,
                counts=(self._count, self._hour_count, self._minute_count),
                priority=priority,
                critical=critical,
            )

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
