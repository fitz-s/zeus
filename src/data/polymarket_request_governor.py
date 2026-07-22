"""Cross-process admission control for public Polymarket HTTP reads.

This module deliberately governs attempts, never responses: a rejected or
embargoed request has no usable quote/data payload.  It therefore cannot turn
old venue facts into fresh ones.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import random
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

import httpx

from src.config import state_path


class RequestPriority(IntEnum):
    """Economic priority; higher lanes may probe after a lower-lane outage."""

    SCAN = 10
    HEARTBEAT = 20
    ACCOUNT_RECOVERY = 30
    SUBMIT_JIT = 40
    HELD_REDUCE_ONLY = 50


class RequestAdmissionDenied(RuntimeError):
    """No HTTP attempt was made because its persistent lease/circuit denied it."""


@dataclass(frozen=True)
class RequestLease:
    request_id: str
    lease_id: str
    endpoint: str
    priority: RequestPriority
    endpoint_generation: int
    rate_limit_route: str | None
    rate_limit_generation: int


_T = TypeVar("_T")
_SCHEMA_VERSION = 2
_LEASE_SECONDS = 60.0
_BASE_DELAY_SECONDS = 2.0
_MAX_BACKOFF_SECONDS = 300.0
_STATE_TTL = timedelta(hours=24)
_MAX_REQUESTS = 1_024
_ROUTE_WINDOW_SECONDS = 10.0
_LOW_PRIORITY_FRACTION = 0.80
_ROUTE_LIMITS = {
    "gamma-api.polymarket.com:/markets": 300,
    "gamma-api.polymarket.com:/events": 500,
    "gamma-api.polymarket.com:*": 4_000,
    "clob.polymarket.com:/books": 500,
    "clob.polymarket.com:/book": 1_500,
    "clob.polymarket.com:*": 9_000,
    "data-api.polymarket.com:/positions": 150,
    "data-api.polymarket.com:*": 1_000,
}


def _int(value: object, default: int = 0) -> int:
    """Read an integer from untrusted persistent JSON without widening it."""

    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def request_identity(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> str:
    """Stable identity of the exact executable request, not a response cache key."""

    payload = json.dumps(
        {"method": method.upper(), "url": url, "params": params or {}, "json": json_body},
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _endpoint(url: str) -> str:
    parsed = httpx.URL(url)
    # A proxy/TLS outage affects a venue host, not merely one token's path.
    return str(parsed.host)[:180]


def _routes(url: str) -> tuple[tuple[str, int], ...]:
    parsed = httpx.URL(url)
    host = str(parsed.host)
    path = parsed.path.rstrip("/") or "/"
    exact = f"{host}:{path}"
    result: list[tuple[str, int]] = []
    if exact in _ROUTE_LIMITS:
        result.append((exact, _ROUTE_LIMITS[exact]))
    general = f"{host}:*"
    if general in _ROUTE_LIMITS:
        result.append((general, _ROUTE_LIMITS[general]))
    return tuple(result)


def _rate_limit_route(url: str) -> str | None:
    """Return the narrowest published route that can have emitted a 429.

    An HTTP 429 says nothing about a different endpoint on the same host. A
    response for ``/book`` must therefore not embargo ``/books`` merely
    because both consume a host-wide accounting bucket. If Polymarket only
    publishes a host-wide bucket for the path, that bucket is the narrowest
    available scope.
    """

    routes = _routes(url)
    for route, _limit in routes:
        if not route.endswith(":*"):
            return route
    return routes[0][0] if routes else None


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
    except ValueError:
        return None


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 0.0
        if retry_at.tzinfo is None:
            return 0.0
        return max(0.0, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds())


class PolymarketRequestGovernor:
    """Persistent request identity leases plus endpoint outage circuits.

    A transport/5xx lower-priority failure may not consume repeated
    scanner/heartbeat retries; a higher-priority live re-decision is allowed
    to make its own fresh probe. A 429 is different: it creates a published
    route-specific embargo which no priority may bypass. HELD_REDUCE_ONLY may
    replace a lower-priority lease for the same identity; the new lease id
    fences the displaced caller's eventual response.
    """

    def __init__(
        self,
        *,
        state_file: Path | None = None,
        active: bool | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        explicit = state_file is not None
        self._path = Path(state_file) if state_file is not None else state_path("polymarket-request-governor.json")
        self._active = (explicit or "PYTEST_CURRENT_TEST" not in os.environ) if active is None else bool(active)
        self._lock = threading.Lock()
        self._local: dict[str, object] | None = None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _default_state() -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "requests": {},
            "endpoints": {},
            "routes": {},
        }

    @staticmethod
    def _entries(state: dict[str, object], name: str) -> dict[str, dict[str, object]]:
        existing = state.get(name)
        if isinstance(existing, dict):
            return cast(dict[str, dict[str, object]], existing)
        entries: dict[str, dict[str, object]] = {}
        state[name] = entries
        return entries

    @classmethod
    def _prune(cls, state: dict[str, object], now: datetime) -> None:
        for name in ("requests", "endpoints"):
            entries = cls._entries(state, name)
            for key, entry in list(entries.items()):
                if not isinstance(entry, dict):
                    del entries[key]
                    continue
                updated = _parse_time(entry.get("updated_at"))
                embargo = _parse_time(entry.get("next_retry_at"))
                lease = _parse_time(entry.get("in_flight_until"))
                if updated is None or (
                    updated + _STATE_TTL <= now
                    and (embargo is None or embargo <= now)
                    and (lease is None or lease <= now)
                ):
                    del entries[key]
            if len(entries) > _MAX_REQUESTS:
                # Capacity is a retention target, not authority to erase a
                # live lease or an active embargo. Keeping a temporarily
                # oversized state is the only safe choice when all records
                # remain economically active.
                inactive = sorted(
                    (
                        key
                        for key, entry in entries.items()
                        if (_parse_time(entry.get("next_retry_at")) or now) <= now
                        and (_parse_time(entry.get("in_flight_until")) or now) <= now
                    ),
                    key=lambda key: _parse_time(entries[key].get("updated_at"))
                    or datetime.min.replace(tzinfo=timezone.utc),
                )
                for key in inactive[: max(0, len(entries) - _MAX_REQUESTS)]:
                    del entries[key]

        routes = cls._entries(state, "routes")
        cutoff = now.timestamp() - _ROUTE_WINDOW_SECONDS
        for key, entry in list(routes.items()):
            if not isinstance(entry, dict):
                del routes[key]
                continue
            attempts = entry.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
            entry["attempts"] = [
                float(value)
                for value in attempts
                if isinstance(value, (int, float)) and float(value) > cutoff
            ]
            embargo = _parse_time(entry.get("next_retry_at"))
            if not entry["attempts"] and (embargo is None or embargo <= now):
                del routes[key]

    def _mutate(self, operation: Callable[[dict[str, object], datetime], tuple[_T, bool]]) -> _T:
        if not self._active:
            result, _ = operation(self._default_state(), self._clock())
            return result
        if "PYTEST_CURRENT_TEST" in os.environ and self._path == state_path("polymarket-request-governor.json"):
            with self._lock:
                state = self._local or self._default_state()
                self._local = state
                now = self._clock()
                self._prune(state, now)
                return operation(state, now)[0]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                now = self._clock()
                if self._path.exists():
                    try:
                        state = json.loads(self._path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise RuntimeError("Polymarket request governor state is unreadable") from exc
                    if not isinstance(state, dict) or state.get("schema_version") != _SCHEMA_VERSION:
                        state = self._default_state()
                else:
                    state = self._default_state()
                self._prune(state, now)
                result, changed = operation(state, now)
                if changed or not self._path.exists():
                    tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                    try:
                        tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                        os.replace(tmp, self._path)
                    finally:
                        with contextlib.suppress(FileNotFoundError):
                            tmp.unlink()
                return result
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def acquire(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        priority: RequestPriority = RequestPriority.SCAN,
        lease_seconds: float = _LEASE_SECONDS,
    ) -> RequestLease:
        request_id = request_identity(method, url, params=params, json_body=json_body)
        endpoint = _endpoint(url)
        route_limits = _routes(url)
        rate_limit_route = _rate_limit_route(url)
        lease_id = secrets.token_hex(16)
        lease_seconds = max(1.0, min(float(lease_seconds), _MAX_BACKOFF_SECONDS))

        def operation(state: dict[str, object], now: datetime) -> tuple[RequestLease, bool]:
            requests = self._entries(state, "requests")
            entry = requests.get(request_id, {})
            embargo = _parse_time(entry.get("next_retry_at"))
            inflight = _parse_time(entry.get("in_flight_until"))
            old_priority = _int(entry.get("priority"), int(RequestPriority.SCAN))
            if embargo and embargo > now and (
                bool(entry.get("rate_limited")) or int(priority) <= old_priority
            ):
                raise RequestAdmissionDenied(
                    f"POLYMARKET_REQUEST_EMBARGOED:{embargo.isoformat()}"
                )
            if inflight and inflight > now and int(priority) <= old_priority:
                raise RequestAdmissionDenied(
                    f"POLYMARKET_REQUEST_IN_FLIGHT:{inflight.isoformat()}"
                )
            circuit = self._entries(state, "endpoints").get(endpoint, {})
            circuit_until = _parse_time(circuit.get("next_retry_at"))
            circuit_priority = _int(circuit.get("priority"), int(RequestPriority.SCAN))
            if circuit_until and circuit_until > now and int(priority) <= circuit_priority:
                raise RequestAdmissionDenied(f"POLYMARKET_ENDPOINT_EMBARGOED:{endpoint}:{circuit_until.isoformat()}")
            routes = self._entries(state, "routes")
            route_generation = 0
            if rate_limit_route:
                rate_state = routes.get(rate_limit_route, {})
                rate_until = _parse_time(rate_state.get("next_retry_at"))
                if rate_until and rate_until > now:
                    raise RequestAdmissionDenied(
                        f"POLYMARKET_ROUTE_EMBARGOED:{rate_limit_route}:{rate_until.isoformat()}"
                    )
                route_generation = _int(rate_state.get("rate_limit_generation"))
            if route_limits:
                cutoff = now.timestamp() - _ROUTE_WINDOW_SECONDS
                admitted: list[tuple[dict[str, object], list[float], int]] = []
                for route, route_limit in route_limits:
                    route_state = routes.setdefault(route, {"attempts": []})
                    attempts = route_state.get("attempts")
                    if not isinstance(attempts, list):
                        attempts = []
                    live_attempts = [
                        float(value)
                        for value in attempts
                        if isinstance(value, (int, float)) and float(value) > cutoff
                    ]
                    allowed = (
                        route_limit
                        if priority >= RequestPriority.SUBMIT_JIT
                        else int(route_limit * _LOW_PRIORITY_FRACTION)
                    )
                    if len(live_attempts) >= allowed:
                        raise RequestAdmissionDenied(
                            f"POLYMARKET_ROUTE_LIMIT:{route}:{len(live_attempts)}/{allowed}"
                        )
                    admitted.append((route_state, live_attempts, route_limit))
                for route_state, attempts, route_limit in admitted:
                    attempts.append(now.timestamp())
                    route_state["attempts"] = attempts
                    route_state["updated_at"] = now.isoformat()
                    route_state["full_limit"] = route_limit
                    route_state["window_seconds"] = _ROUTE_WINDOW_SECONDS
            endpoint_generation = _int(circuit.get("generation"))
            requests[request_id] = {
                "endpoint": endpoint,
                "priority": int(priority),
                "lease_id": lease_id,
                "in_flight_until": (now + timedelta(seconds=lease_seconds)).isoformat(),
                "next_retry_at": None,
                "failure_count": _int(entry.get("failure_count")),
                "endpoint_generation": endpoint_generation,
                "rate_limited": False,
                "rate_limit_route": rate_limit_route,
                "rate_limit_generation": route_generation,
                "updated_at": now.isoformat(),
            }
            return RequestLease(
                request_id,
                lease_id,
                endpoint,
                priority,
                endpoint_generation,
                rate_limit_route,
                route_generation,
            ), True

        return self._mutate(operation)

    @staticmethod
    def _owns(entry: dict[str, object] | None, lease: RequestLease, now: datetime) -> bool:
        if not entry or entry.get("lease_id") != lease.lease_id:
            return False
        expiry = _parse_time(entry.get("in_flight_until"))
        return expiry is not None and expiry > now

    def record_success(self, lease: RequestLease) -> bool:
        def operation(state: dict[str, object], now: datetime) -> tuple[bool, bool]:
            requests = self._entries(state, "requests")
            entry = requests.get(lease.request_id)
            if not self._owns(entry, lease, now):
                return False, False
            assert entry is not None
            endpoints = self._entries(state, "endpoints")
            endpoint_state = endpoints.get(lease.endpoint, {})
            current_generation = _int(endpoint_state.get("generation"))
            if current_generation != lease.endpoint_generation:
                return False, False
            routes = self._entries(state, "routes")
            if lease.rate_limit_route:
                route_state = routes.get(lease.rate_limit_route, {})
                current_route_generation = _int(route_state.get("rate_limit_generation"))
                if current_route_generation != lease.rate_limit_generation:
                    return False, False
            requests[lease.request_id] = {
                "endpoint": lease.endpoint, "priority": int(lease.priority), "lease_id": None,
                "in_flight_until": None, "next_retry_at": None, "failure_count": 0,
                "endpoint_generation": lease.endpoint_generation,
                "rate_limited": False,
                "rate_limit_route": lease.rate_limit_route,
                "rate_limit_generation": lease.rate_limit_generation,
                "updated_at": now.isoformat(),
            }
            if lease.rate_limit_route:
                route_state = dict(routes.get(lease.rate_limit_route, {}))
                route_state["next_retry_at"] = None
                route_state["updated_at"] = now.isoformat()
                route_state["last_success_at"] = now.isoformat()
                route_state["rate_limit_generation"] = lease.rate_limit_generation
                routes[lease.rate_limit_route] = route_state
            # Keep generation after recovery so an earlier probe cannot clear
            # a later failure circuit. Its owned response remains independently
            # subject to the caller's payload and freshness validation.
            endpoints[lease.endpoint] = {
                "generation": current_generation,
                "priority": int(lease.priority),
                "next_retry_at": None,
                "failure_count": 0,
                "updated_at": now.isoformat(),
                "last_success_at": now.isoformat(),
            }
            return True, True
        return self._mutate(operation)

    def record_neutral(self, lease: RequestLease) -> bool:
        """Release one lease without treating a non-2xx response as recovery."""

        def operation(state: dict[str, object], now: datetime) -> tuple[bool, bool]:
            requests = self._entries(state, "requests")
            entry = requests.get(lease.request_id)
            if not self._owns(entry, lease, now):
                return False, False
            assert entry is not None
            requests[lease.request_id] = {
                "endpoint": lease.endpoint,
                "priority": int(lease.priority),
                "lease_id": None,
                "in_flight_until": None,
                "next_retry_at": None,
                "failure_count": _int(entry.get("failure_count")),
                "endpoint_generation": lease.endpoint_generation,
                "rate_limited": bool(entry.get("rate_limited")),
                "rate_limit_route": lease.rate_limit_route,
                "rate_limit_generation": lease.rate_limit_generation,
                "updated_at": now.isoformat(),
            }
            return True, True

        return self._mutate(operation)

    def record_failure(self, lease: RequestLease) -> bool:
        """Persist a transport/5xx host fact without stealing request ownership."""

        def operation(state: dict[str, object], now: datetime) -> tuple[bool, bool]:
            requests = self._entries(state, "requests")
            entry = requests.get(lease.request_id)
            owns_request = self._owns(entry, lease, now)
            failures = min(
                16,
                (_int(entry.get("failure_count")) if owns_request and entry is not None else 0) + 1,
            )
            endpoints = self._entries(state, "endpoints")
            prior = endpoints.get(lease.endpoint, {})
            endpoint_failures = min(16, _int(prior.get("failure_count")) + 1)
            endpoint_generation = _int(prior.get("generation")) + 1
            # Different scan parameters are still one transport path.  Carry
            # the exponential circuit across identities so a rotating scanner
            # cannot convert an outage into a fixed 2-second hammer loop.
            cap = min(
                _MAX_BACKOFF_SECONDS,
                _BASE_DELAY_SECONDS * (2 ** (max(failures, endpoint_failures) - 1)),
            )
            delay = max(random.uniform(0.0, cap), 0.001)
            until = now + timedelta(seconds=delay)
            payload = {
                "endpoint": lease.endpoint, "priority": int(lease.priority), "lease_id": None,
                "in_flight_until": None, "next_retry_at": until.isoformat(),
                "failure_count": failures,
                "endpoint_generation": endpoint_generation,
                "updated_at": now.isoformat(),
            }
            if owns_request:
                requests[lease.request_id] = payload
            prior_until = _parse_time(prior.get("next_retry_at"))
            endpoint_payload = dict(payload)
            # A late low-priority failure is still a new host fact, but it
            # must not downgrade a still-active HELD circuit and let a middle
            # priority lane bypass the existing outage embargo.
            endpoint_payload["priority"] = (
                max(_int(prior.get("priority"), int(RequestPriority.SCAN)), int(lease.priority))
                if prior_until and prior_until > now
                else int(lease.priority)
            )
            endpoint_payload["failure_count"] = endpoint_failures
            endpoint_payload["generation"] = endpoint_generation
            endpoint_payload["failed_at"] = now.isoformat()
            endpoint_payload["next_retry_at"] = max(
                value for value in (prior_until, until) if value is not None
            ).isoformat()
            endpoints[lease.endpoint] = endpoint_payload
            return True, True
        return self._mutate(operation)

    def record_rate_limited(self, lease: RequestLease, *, retry_after: float | None = None) -> bool:
        """Persist a 429 as an exact-route embargo, never a host outage.

        The server's quota signal is authoritative for its own published
        route. Unlike transport/5xx faults it may not be bypassed by a
        higher-priority request, and a 2xx from another route cannot recover
        it. A generation fence prevents an older in-flight same-route 2xx
        from clearing a newer embargo. The 429 itself remains authoritative
        after its local lease was displaced or expired: preserve the route
        fact, but never overwrite a newer request owner.
        """

        def operation(state: dict[str, object], now: datetime) -> tuple[bool, bool]:
            requests = self._entries(state, "requests")
            entry = requests.get(lease.request_id)
            owns_request = self._owns(entry, lease, now)
            failures = min(
                16,
                (_int(entry.get("failure_count")) if owns_request and entry is not None else 0) + 1,
            )
            cap = min(_MAX_BACKOFF_SECONDS, _BASE_DELAY_SECONDS * (2 ** (failures - 1)))
            provider_wait = max(0.0, float(retry_after or 0.0))
            until = now + timedelta(seconds=max(random.uniform(0.0, cap), provider_wait, 0.001))
            route_generation = lease.rate_limit_generation
            routes = self._entries(state, "routes")
            changed = False
            if lease.rate_limit_route:
                prior = dict(routes.get(lease.rate_limit_route, {}))
                route_generation = _int(prior.get("rate_limit_generation")) + 1
                prior_until = _parse_time(prior.get("next_retry_at"))
                prior.update(
                    {
                        "next_retry_at": max(value for value in (prior_until, until) if value is not None).isoformat(),
                        "rate_limit_generation": route_generation,
                        "rate_limited_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                )
                routes[lease.rate_limit_route] = prior
                changed = True
            if owns_request:
                requests[lease.request_id] = {
                    "endpoint": lease.endpoint,
                    "priority": int(lease.priority),
                    "lease_id": None,
                    "in_flight_until": None,
                    "next_retry_at": until.isoformat(),
                    "failure_count": failures,
                    "endpoint_generation": lease.endpoint_generation,
                    "rate_limited": True,
                    "rate_limit_route": lease.rate_limit_route,
                    "rate_limit_generation": route_generation,
                    "updated_at": now.isoformat(),
                }
                changed = True
            return changed, changed

        return self._mutate(operation)

    def request(
        self,
        send: Callable[[], httpx.Response],
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        priority: RequestPriority = RequestPriority.SCAN,
        lease_seconds: float = _LEASE_SECONDS,
    ) -> httpx.Response:
        """Perform one newly-admitted request and persist only outcome metadata."""

        if not self._active:
            return send()
        lease = self.acquire(method, url, params=params, json_body=json_body, priority=priority, lease_seconds=lease_seconds)
        transport_error: httpx.HTTPError | None = None
        try:
            response = send()
        except httpx.HTTPError as exc:
            transport_error = exc
        if transport_error is not None:
            self.record_failure(lease)
            raise transport_error
        # Some compatibility tests inject minimal response stand-ins.  A real
        # httpx.Response always has status_code; absent means no failure signal.
        status_code = int(getattr(response, "status_code", 200))
        if status_code == 429:
            headers = getattr(response, "headers", {})
            self.record_rate_limited(lease, retry_after=retry_after_seconds(headers.get("Retry-After")))
        elif status_code >= 500:
            self.record_failure(lease)
        elif 200 <= status_code < 300:
            if not self.record_success(lease) and not self.record_neutral(lease):
                raise RequestAdmissionDenied("POLYMARKET_REQUEST_LEASE_LOST")
        elif not self.record_neutral(lease):
            raise RequestAdmissionDenied("POLYMARKET_REQUEST_LEASE_LOST")
        return response


polymarket_request_governor = PolymarketRequestGovernor()
