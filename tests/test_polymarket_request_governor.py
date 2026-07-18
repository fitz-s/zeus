# Created: 2026-07-18
# Last reused/audited: 2026-07-18
# Authority basis: live Polymarket HTTP attempt governance and first-principles capital-preservation task

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.data.polymarket_request_governor import (
    PolymarketRequestGovernor,
    RequestAdmissionDenied,
    RequestPriority,
    request_identity,
)


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://clob.polymarket.com/book"))


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_same_identity_is_singleflight_across_governor_instances(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    first = PolymarketRequestGovernor(state_file=state)
    second = PolymarketRequestGovernor(state_file=state)
    lease = first.acquire("GET", "https://clob.polymarket.com/book", params={"token_id": "x"})

    with pytest.raises(RequestAdmissionDenied, match="IN_FLIGHT"):
        second.acquire("GET", "https://clob.polymarket.com/book", params={"token_id": "x"})

    assert first.record_success(lease) is True
    assert second.acquire("GET", "https://clob.polymarket.com/book", params={"token_id": "x"})


def test_429_embargo_is_route_specific_and_cross_route_success_cannot_clear_it(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    response = governor.request(
        lambda: _response(429, {"Retry-After": "30"}),
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "city"},
        priority=RequestPriority.SCAN,
    )
    assert response.status_code == 429

    with pytest.raises(RequestAdmissionDenied, match="REQUEST_EMBARGOED"):
        governor.acquire("GET", "https://gamma-api.polymarket.com/events", params={"slug": "city"})
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire(
            "GET",
            "https://gamma-api.polymarket.com/events",
            params={"slug": "different-city"},
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )
    other_route = governor.acquire(
        "GET", "https://gamma-api.polymarket.com/markets", priority=RequestPriority.SCAN
    )
    assert governor.record_success(other_route) is True
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire(
            "GET", "https://gamma-api.polymarket.com/events", params={"slug": "third-city"}
        )
    payload = json.loads((tmp_path / "governor.json").read_text())
    assert payload["endpoints"]["gamma-api.polymarket.com"]["generation"] == 0
    assert "failed_at" not in payload["endpoints"]["gamma-api.polymarket.com"]
    assert payload["routes"]["gamma-api.polymarket.com:/events"]["next_retry_at"] is not None


def test_429_does_not_embargo_an_independent_clob_route(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    response = governor.request(
        lambda: _response(429, {"Retry-After": "30"}),
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "city"},
    )
    assert response.status_code == 429
    independent = governor.acquire("POST", "https://clob.polymarket.com/books", json_body=[{"token_id": "city"}])
    assert governor.record_success(independent) is True
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire("GET", "https://clob.polymarket.com/book", params={"token_id": "other"})


def test_same_route_success_after_expiry_clears_its_own_429_embargo(tmp_path: Path) -> None:
    clock = _Clock()
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json", clock=clock)
    governor.request(
        lambda: _response(429, {"Retry-After": "30"}),
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "city"},
    )
    clock.advance(30.001)
    response = governor.request(
        lambda: _response(200),
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "city"},
    )
    assert response.status_code == 200
    lease = governor.acquire("GET", "https://gamma-api.polymarket.com/events", params={"slug": "new-city"})
    assert governor.record_success(lease) is True


def test_old_same_route_success_cannot_clear_newer_429_embargo(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    first = governor.acquire(
        "GET", "https://gamma-api.polymarket.com/events", params={"slug": "first"}
    )
    second = governor.acquire(
        "GET", "https://gamma-api.polymarket.com/events", params={"slug": "second"}
    )
    assert governor.record_rate_limited(first, retry_after=30) is True
    assert governor.record_success(second) is False
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire(
            "GET", "https://gamma-api.polymarket.com/events", params={"slug": "third"}
        )


def test_higher_priority_fresh_probe_can_bypass_lower_priority_outage_circuit(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    governor.request(
        lambda: _response(503),
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "low"},
        priority=RequestPriority.SCAN,
    )

    lease = governor.acquire(
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "held"},
        priority=RequestPriority.HELD_REDUCE_ONLY,
    )
    assert governor.record_success(lease) is True


def test_held_request_preempts_same_identity_and_fences_old_concurrent_response(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    low = PolymarketRequestGovernor(state_file=state)
    held = PolymarketRequestGovernor(state_file=state)
    started = threading.Event()
    release = threading.Event()
    outcome: list[object] = []
    url = "https://clob.polymarket.com/book"
    params = {"token_id": "same"}

    def low_send() -> httpx.Response:
        started.set()
        assert release.wait(2.0)
        return _response(200)

    def run_low() -> None:
        try:
            outcome.append(low.request(low_send, "GET", url, params=params))
        except Exception as exc:  # noqa: BLE001 - asserting the fenced outcome
            outcome.append(exc)

    thread = threading.Thread(target=run_low)
    thread.start()
    assert started.wait(2.0)
    response = held.request(
        lambda: _response(200),
        "GET",
        url,
        params=params,
        priority=RequestPriority.HELD_REDUCE_ONLY,
    )
    release.set()
    thread.join(2.0)

    assert response.status_code == 200
    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], RequestAdmissionDenied)
    assert "LEASE_LOST" in str(outcome[0])


def test_displaced_attempt_429_preserves_route_embargo_without_overwriting_held_owner(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    low = PolymarketRequestGovernor(state_file=state)
    held = PolymarketRequestGovernor(state_file=state)
    started = threading.Event()
    release = threading.Event()
    outcome: list[object] = []
    url = "https://clob.polymarket.com/book"
    params = {"token_id": "same"}

    def low_send() -> httpx.Response:
        started.set()
        assert release.wait(2.0)
        return _response(429, {"Retry-After": "30"})

    def run_low() -> None:
        try:
            outcome.append(low.request(low_send, "GET", url, params=params))
        except Exception as exc:  # noqa: BLE001 - retaining the real response is the assertion
            outcome.append(exc)

    thread = threading.Thread(target=run_low)
    thread.start()
    assert started.wait(2.0)
    assert held.request(
        lambda: _response(200),
        "GET",
        url,
        params=params,
        priority=RequestPriority.HELD_REDUCE_ONLY,
    ).status_code == 200
    release.set()
    thread.join(2.0)

    assert outcome and isinstance(outcome[0], httpx.Response)
    assert outcome[0].status_code == 429
    payload = json.loads(state.read_text())
    entry = next(iter(payload["requests"].values()))
    assert entry["priority"] == int(RequestPriority.HELD_REDUCE_ONLY)
    assert entry["rate_limited"] is False
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        held.acquire("GET", url, params={"token_id": "another"})


def test_displaced_attempt_503_preserves_host_circuit_without_overwriting_held_owner(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    low = PolymarketRequestGovernor(state_file=state)
    held = PolymarketRequestGovernor(state_file=state)
    started = threading.Event()
    release = threading.Event()
    outcome: list[object] = []
    url = "https://clob.polymarket.com/book"
    params = {"token_id": "same"}

    def low_send() -> httpx.Response:
        started.set()
        assert release.wait(2.0)
        return _response(503)

    def run_low() -> None:
        try:
            outcome.append(low.request(low_send, "GET", url, params=params))
        except Exception as exc:  # noqa: BLE001 - retaining the real response is the assertion
            outcome.append(exc)

    thread = threading.Thread(target=run_low)
    thread.start()
    assert started.wait(2.0)
    assert held.request(
        lambda: _response(200),
        "GET",
        url,
        params=params,
        priority=RequestPriority.HELD_REDUCE_ONLY,
    ).status_code == 200
    release.set()
    thread.join(2.0)

    assert outcome and isinstance(outcome[0], httpx.Response)
    assert outcome[0].status_code == 503
    payload = json.loads(state.read_text())
    entry = next(iter(payload["requests"].values()))
    assert entry["priority"] == int(RequestPriority.HELD_REDUCE_ONLY)
    assert entry["next_retry_at"] is None
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        held.acquire("GET", url, params={"token_id": "another"})


def test_displaced_attempt_transport_failure_preserves_host_circuit(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    low = PolymarketRequestGovernor(state_file=state)
    held = PolymarketRequestGovernor(state_file=state)
    started = threading.Event()
    release = threading.Event()
    outcome: list[object] = []
    url = "https://data-api.polymarket.com/positions"
    params = {"user": "same"}

    def low_send() -> httpx.Response:
        started.set()
        assert release.wait(2.0)
        raise httpx.ConnectTimeout("timeout")

    def run_low() -> None:
        try:
            outcome.append(low.request(low_send, "GET", url, params=params))
        except Exception as exc:  # noqa: BLE001 - expected transport failure is the assertion
            outcome.append(exc)

    thread = threading.Thread(target=run_low)
    thread.start()
    assert started.wait(2.0)
    assert held.request(
        lambda: _response(200),
        "GET",
        url,
        params=params,
        priority=RequestPriority.HELD_REDUCE_ONLY,
    ).status_code == 200
    release.set()
    thread.join(2.0)

    assert outcome and isinstance(outcome[0], httpx.ConnectTimeout)
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        held.acquire("GET", url, params={"user": "another"})


def test_expired_attempt_429_preserves_route_embargo(tmp_path: Path) -> None:
    clock = _Clock()
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json", clock=clock)

    def delayed_429() -> httpx.Response:
        clock.advance(2)
        return _response(429, {"Retry-After": "30"})

    assert governor.request(
        delayed_429,
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "expired"},
        lease_seconds=1,
    ).status_code == 429
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire(
            "GET", "https://gamma-api.polymarket.com/events", params={"slug": "next"}
        )


def test_expired_attempt_transport_failure_preserves_host_circuit(tmp_path: Path) -> None:
    clock = _Clock()
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json", clock=clock)

    def delayed_timeout() -> httpx.Response:
        clock.advance(2)
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(httpx.ConnectTimeout):
        governor.request(
            delayed_timeout,
            "GET",
            "https://data-api.polymarket.com/positions",
            params={"user": "expired"},
            lease_seconds=1,
        )
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        governor.acquire(
            "GET", "https://data-api.polymarket.com/positions", params={"user": "next"}
        )


def test_old_inflight_success_cannot_clear_newer_host_failure(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    old = PolymarketRequestGovernor(state_file=state)
    breaker = PolymarketRequestGovernor(state_file=state)
    started = threading.Event()
    release = threading.Event()
    outcome: list[object] = []
    url = "https://clob.polymarket.com/book"

    def old_send() -> httpx.Response:
        started.set()
        assert release.wait(2.0)
        return _response(200)

    def run_old() -> None:
        try:
            outcome.append(old.request(old_send, "GET", url, params={"token_id": "old"}))
        except Exception as exc:  # noqa: BLE001 - asserting generation fencing
            outcome.append(exc)

    thread = threading.Thread(target=run_old)
    thread.start()
    assert started.wait(2.0)
    breaker.request(
        lambda: _response(503),
        "GET",
        url,
        params={"token_id": "breaker"},
        priority=RequestPriority.HEARTBEAT,
    )
    release.set()
    thread.join(2.0)

    assert isinstance(outcome[0], RequestAdmissionDenied)
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        old.acquire("GET", url, params={"token_id": "scan-after-failure"})

    probe = breaker.request(
        lambda: _response(200),
        "GET",
        url,
        params={"token_id": "held-probe"},
        priority=RequestPriority.HELD_REDUCE_ONLY,
    )
    assert probe.status_code == 200
    lease = old.acquire("GET", url, params={"token_id": "scan-after-recovery"})
    assert old.record_success(lease) is True


def test_non_2xx_probe_does_not_clear_host_failure_generation(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    url = "https://clob.polymarket.com/book"
    governor.request(
        lambda: _response(503),
        "GET",
        url,
        params={"token_id": "failure"},
        priority=RequestPriority.SCAN,
    )
    response = governor.request(
        lambda: _response(404),
        "GET",
        url,
        params={"token_id": "held-not-found"},
        priority=RequestPriority.HELD_REDUCE_ONLY,
    )
    assert response.status_code == 404
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        governor.acquire("GET", url, params={"token_id": "still-blocked"})


def test_host_circuit_backoff_accumulates_across_distinct_scan_requests(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state)
    governor.request(
        lambda: _response(503), "GET", "https://clob.polymarket.com/book", params={"token_id": "one"}
    )
    governor.request(
        lambda: _response(503),
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "two"},
        priority=RequestPriority.HEARTBEAT,
    )
    payload = json.loads(state.read_text())
    assert payload["endpoints"]["clob.polymarket.com"]["failure_count"] == 2


def test_late_low_failure_cannot_downgrade_active_held_host_circuit(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state)
    url = "https://clob.polymarket.com/book"
    low = governor.acquire("GET", url, params={"token_id": "low"}, priority=RequestPriority.SCAN)
    held = governor.acquire(
        "GET",
        url,
        params={"token_id": "held"},
        priority=RequestPriority.HELD_REDUCE_ONLY,
    )

    assert governor.record_failure(held) is True
    assert governor.record_failure(low) is True
    payload = json.loads(state.read_text())
    circuit = payload["endpoints"]["clob.polymarket.com"]
    assert circuit["priority"] == int(RequestPriority.HELD_REDUCE_ONLY)
    assert circuit["generation"] == 2
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        governor.acquire(
            "GET",
            url,
            params={"token_id": "heartbeat"},
            priority=RequestPriority.HEARTBEAT,
        )


def test_transport_failure_does_not_reissue_the_same_http_attempt(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    attempts = 0

    def fail() -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(httpx.ConnectTimeout):
        governor.request(fail, "GET", "https://data-api.polymarket.com/positions")
    with pytest.raises(RequestAdmissionDenied, match="REQUEST_EMBARGOED"):
        governor.request(fail, "GET", "https://data-api.polymarket.com/positions")
    assert attempts == 1


def test_retry_after_beyond_one_day_is_never_truncated_or_pruned_early(tmp_path: Path) -> None:
    clock = _Clock()
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state, clock=clock)
    governor.request(
        lambda: _response(429, {"Retry-After": "172800"}),
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "rate-limited"},
    )
    payload = json.loads(state.read_text())
    retry_at = datetime.fromisoformat(
        next(iter(payload["requests"].values()))["next_retry_at"]
    )
    assert retry_at - clock.now == timedelta(seconds=172800)
    clock.advance(172799)
    with pytest.raises(RequestAdmissionDenied, match="REQUEST_EMBARGOED"):
        governor.acquire(
            "GET",
            "https://gamma-api.polymarket.com/events",
            params={"slug": "rate-limited"},
        )


def test_capacity_prune_never_drops_future_lease_or_embargo(tmp_path: Path) -> None:
    clock = _Clock()
    state = tmp_path / "governor.json"
    lease_url = "https://clob.polymarket.com/book"
    embargo_url = "https://gamma-api.polymarket.com/events"
    future_lease_id = request_identity("GET", lease_url, params={"token_id": "future-lease"})
    future_embargo_id = request_identity("GET", embargo_url, params={"slug": "future-embargo"})
    now = clock.now.isoformat()
    future = (clock.now + timedelta(hours=2)).isoformat()
    inactive = {
        f"inactive-{index}": {
            "endpoint": "old.example",
            "priority": int(RequestPriority.SCAN),
            "lease_id": None,
            "in_flight_until": None,
            "next_retry_at": None,
            "failure_count": 0,
            "updated_at": now,
        }
        for index in range(1_024)
    }
    requests = {
        future_lease_id: {
            "endpoint": "clob.polymarket.com",
            "priority": int(RequestPriority.SCAN),
            "lease_id": "future-lease",
            "in_flight_until": future,
            "next_retry_at": None,
            "failure_count": 0,
            "updated_at": now,
        },
        future_embargo_id: {
            "endpoint": "gamma-api.polymarket.com",
            "priority": int(RequestPriority.SCAN),
            "lease_id": None,
            "in_flight_until": None,
            "next_retry_at": future,
            "rate_limited": True,
            "failure_count": 1,
            "updated_at": now,
        },
        **inactive,
    }
    state.write_text(json.dumps({"schema_version": 2, "requests": requests, "endpoints": {}, "routes": {}}))
    governor = PolymarketRequestGovernor(state_file=state, clock=clock)

    admitted = governor.acquire("GET", lease_url, params={"token_id": "new"})
    assert governor.record_success(admitted) is True
    payload = json.loads(state.read_text())
    assert future_lease_id in payload["requests"]
    assert future_embargo_id in payload["requests"]
    with pytest.raises(RequestAdmissionDenied, match="IN_FLIGHT"):
        governor.acquire("GET", lease_url, params={"token_id": "future-lease"})
    with pytest.raises(RequestAdmissionDenied, match="REQUEST_EMBARGOED"):
        governor.acquire("GET", embargo_url, params={"slug": "future-embargo"})


def test_positions_route_reserves_twenty_percent_for_high_priority_across_instances(tmp_path: Path) -> None:
    clock = _Clock()
    state = tmp_path / "governor.json"
    first = PolymarketRequestGovernor(state_file=state, clock=clock)
    second = PolymarketRequestGovernor(state_file=state, clock=clock)
    url = "https://data-api.polymarket.com/positions"

    for index in range(120):
        governor = first if index % 2 == 0 else second
        lease = governor.acquire("GET", url, params={"user": str(index)})
        assert governor.record_success(lease) is True
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_LIMIT"):
        first.acquire("GET", url, params={"user": "low-over-reserve"})

    for index in range(120, 150):
        governor = first if index % 2 == 0 else second
        lease = governor.acquire(
            "GET",
            url,
            params={"user": str(index)},
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )
        assert governor.record_success(lease) is True
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_LIMIT"):
        second.acquire(
            "GET",
            url,
            params={"user": "high-over-official-limit"},
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )

    clock.advance(10.001)
    lease = first.acquire("GET", url, params={"user": "next-window"})
    assert first.record_success(lease) is True


def test_official_routes_are_normalized_and_persist_full_limits(tmp_path: Path) -> None:
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state)
    requests = [
        ("GET", "https://gamma-api.polymarket.com/markets", None),
        ("GET", "https://gamma-api.polymarket.com/events", None),
        ("GET", "https://gamma-api.polymarket.com/tags/slug/weather", None),
        ("GET", "https://clob.polymarket.com/book", {"token_id": "one"}),
        ("POST", "https://clob.polymarket.com/books", None),
        ("GET", "https://data-api.polymarket.com/positions", {"user": "wallet"}),
    ]
    for method, url, params in requests:
        lease = governor.acquire(method, url, params=params)
        assert governor.record_success(lease) is True

    routes = json.loads(state.read_text())["routes"]
    assert {key: value["full_limit"] for key, value in routes.items()} == {
        "gamma-api.polymarket.com:/markets": 300,
        "gamma-api.polymarket.com:/events": 500,
        "gamma-api.polymarket.com:*": 4_000,
        "clob.polymarket.com:/book": 1_500,
        "clob.polymarket.com:/books": 500,
        "clob.polymarket.com:*": 9_000,
        "data-api.polymarket.com:/positions": 150,
        "data-api.polymarket.com:*": 1_000,
    }
    assert len(routes["gamma-api.polymarket.com:*"]["attempts"]) == 3


def test_public_client_and_gamma_scan_use_shared_governor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.data import market_scanner as scanner
    from src.data import polymarket_client as client_module

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    calls: list[str] = []

    def get(url: str, **_kwargs: Any) -> httpx.Response:
        calls.append(url)
        if url.endswith("/positions"):
            return httpx.Response(
                200,
                json=[],
                request=httpx.Request("GET", url),
            )
        return _response(200)

    def post(url: str, **_kwargs: Any) -> httpx.Response:
        calls.append(url)
        return _response(200)

    monkeypatch.setattr(client_module, "polymarket_request_governor", governor)
    monkeypatch.setattr(scanner, "polymarket_request_governor", governor)
    monkeypatch.setattr(client_module.httpx, "get", get)
    monkeypatch.setattr(client_module.httpx, "post", post)
    monkeypatch.setattr(scanner.httpx, "get", get)
    monkeypatch.setattr(
        client_module,
        "_resolve_credentials",
        lambda: {"funder_address": "wallet"},
    )

    client = object.__new__(client_module.PolymarketClient)
    assert client._public_get("/book", params={"token_id": "yes"}).status_code == 200
    assert client._public_post("/books", json_body=[{"token_id": "yes"}]).status_code == 200
    assert client.get_positions_from_api() == []
    assert scanner._gamma_get("/events", params={"slug": "city"}).status_code == 200
    assert calls == [
        "https://clob.polymarket.com/book",
        "https://clob.polymarket.com/books",
        "https://data-api.polymarket.com/positions",
        "https://gamma-api.polymarket.com/events",
    ]
