# Lifecycle: created=2026-07-18; last_reviewed=2026-08-31; last_reused=2026-08-31
# Created: 2026-07-18
# Last reused/audited: 2026-08-31
# Authority basis: live Polymarket HTTP attempt governance and first-principles capital-preservation task

from __future__ import annotations

import contextlib
import fcntl
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.data.polymarket_request_governor import (
    EndpointClass,
    PolymarketRequestGovernor,
    RequestAdmissionDenied,
    RequestPriority,
    _endpoint_class,
    request_identity,
)
import src.data.polymarket_request_governor as governor_module


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://clob.polymarket.com/book"))


def test_gamma_transport_reuses_one_tls_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.data import market_scanner as scanner

    clients = []

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            self.urls: list[str] = []

        def get(self, url: str, **_kwargs: Any) -> httpx.Response:
            self.urls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url))

    def _factory(**kwargs: Any) -> _Client:
        client = _Client(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(scanner.httpx, "Client", _factory)
    scanner._GAMMA_HTTP_CLIENT = None
    try:
        scanner._gamma_transport_get(
            f"{scanner.GAMMA_BASE}/events", params={"slug": "one"}, timeout=4.0
        )
        scanner._gamma_transport_get(
            f"{scanner.GAMMA_BASE}/events", params={"slug": "two"}, timeout=4.0
        )
    finally:
        scanner._GAMMA_HTTP_CLIENT = None

    assert len(clients) == 1
    assert clients[0].urls == [
        f"{scanner.GAMMA_BASE}/events",
        f"{scanner.GAMMA_BASE}/events",
    ]


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _start_cross_process_scan_holder(tmp_path: Path) -> subprocess.Popen[str]:
    script = r"""
import os
import sys
import httpx
from pathlib import Path
from src.data.polymarket_request_governor import PolymarketRequestGovernor, RequestPriority

state_file = Path(sys.argv[1])
lock_dir = Path(sys.argv[2])
governor = PolymarketRequestGovernor(state_file=state_file, scan_lock_dir=lock_dir)

def send():
    print("READY", flush=True)
    command = sys.stdin.readline().strip()
    if command == "CRASH":
        os._exit(17)
    return httpx.Response(
        200,
        request=httpx.Request("GET", "https://clob.polymarket.com/book"),
    )

governor.request(
    send,
    "GET",
    "https://clob.polymarket.com/book",
    params={"token_id": "child-scan"},
    priority=RequestPriority.SCAN,
)
print("DONE", flush=True)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "governor.json"),
            str(tmp_path / "locks"),
        ],
        cwd=Path.cwd(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "READY"
    return process


def _finish_scan_holder(process: subprocess.Popen[str], command: str) -> None:
    if process.poll() is not None:
        return
    assert process.stdin is not None
    process.stdin.write(command + "\n")
    process.stdin.flush()


def test_clob_scan_lease_is_cross_process_singleflight_and_bounded(tmp_path: Path) -> None:
    process = _start_cross_process_scan_holder(tmp_path)
    governor = PolymarketRequestGovernor(
        state_file=tmp_path / "governor.json",
        scan_lock_dir=tmp_path / "locks",
    )
    sent = False
    started = time.monotonic()

    def send() -> httpx.Response:
        nonlocal sent
        sent = True
        return _response(200)

    try:
        with pytest.raises(
            RequestAdmissionDenied,
            match=r"POLYMARKET_SCAN_LEASE_BUSY:clob\.polymarket\.com:status=scan_in_flight",
        ):
            governor.request(
                send,
                "GET",
                "https://clob.polymarket.com/book",
                params={"token_id": "other-scan"},
                priority=RequestPriority.SCAN,
            )
        denied_elapsed = time.monotonic() - started
    finally:
        _finish_scan_holder(process, "RELEASE")
        process.wait(timeout=3.0)

    assert sent is False
    assert denied_elapsed < 2.0
    assert process.returncode == 0
    routes = json.loads((tmp_path / "governor.json").read_text())["routes"]
    assert len(routes["clob.polymarket.com:/book"]["attempts"]) == 1
    assert len(routes["clob.polymarket.com:*"]["attempts"]) == 1


def test_clob_scan_lease_does_not_block_priority_or_other_host_routes(tmp_path: Path) -> None:
    process = _start_cross_process_scan_holder(tmp_path)
    governor = PolymarketRequestGovernor(
        state_file=tmp_path / "governor.json",
        scan_lock_dir=tmp_path / "locks",
    )
    try:
        for priority in (
            RequestPriority.HEARTBEAT,
            RequestPriority.HELD_REDUCE_ONLY,
            RequestPriority.SUBMIT_JIT,
        ):
            response = governor.request(
                lambda: _response(200),
                "GET",
                "https://clob.polymarket.com/book",
                params={"token_id": priority.name},
                priority=priority,
            )
            assert response.status_code == 200
        gamma = governor.request(
            lambda: _response(200),
            "GET",
            "https://gamma-api.polymarket.com/markets",
            params={"offset": 0},
            priority=RequestPriority.SCAN,
        )
        assert gamma.status_code == 200
    finally:
        _finish_scan_holder(process, "RELEASE")
        process.wait(timeout=3.0)

    assert process.returncode == 0


def test_clob_scan_lease_recovers_after_holder_crash(tmp_path: Path) -> None:
    process = _start_cross_process_scan_holder(tmp_path)
    _finish_scan_holder(process, "CRASH")
    assert process.wait(timeout=3.0) == 17

    governor = PolymarketRequestGovernor(
        state_file=tmp_path / "governor.json",
        scan_lock_dir=tmp_path / "locks",
    )
    response = governor.request(
        lambda: _response(200),
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "post-crash-scan"},
        priority=RequestPriority.SCAN,
    )
    assert response.status_code == 200


def test_governor_telemetry_rotates_at_configured_size_without_blocking_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "polymarket-request-governor-telemetry.jsonl"
    old = ("old-telemetry\n" * 30).encode()
    path.write_bytes(old)
    monkeypatch.setenv("ZEUS_GOVERNOR_TELEMETRY_IN_TESTS", "1")
    monkeypatch.setenv("ZEUS_POLYMARKET_GOVERNOR_TELEMETRY_MAX_BYTES", "450")
    monkeypatch.setattr(governor_module, "state_path", lambda _name: path)

    governor_module._emit_governor_telemetry({"event": "admitted", "route": "book"})

    assert path.with_suffix(".jsonl.1").read_bytes() == old
    current = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in current] == ["admitted"]
    assert path.stat().st_size <= 450


def test_governor_telemetry_skips_contended_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "polymarket-request-governor-telemetry.jsonl"
    lock_path = path.with_suffix(".jsonl.lock")
    monkeypatch.setenv("ZEUS_GOVERNOR_TELEMETRY_IN_TESTS", "1")
    monkeypatch.setattr(governor_module, "state_path", lambda _name: path)

    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        governor_module._emit_governor_telemetry({"event": "skipped"})

    assert not path.exists()


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
    assert payload["endpoints"]["gamma-api.polymarket.com:discovery"]["generation"] == 0
    assert "failed_at" not in payload["endpoints"]["gamma-api.polymarket.com:discovery"]
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


def test_owned_inflight_success_survives_without_clearing_newer_host_failure(tmp_path: Path) -> None:
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
        except Exception as exc:  # noqa: BLE001 - asserting response ownership
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

    assert isinstance(outcome[0], httpx.Response)
    assert outcome[0].status_code == 200
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
    assert payload["endpoints"]["clob.polymarket.com:clob-market-data"]["failure_count"] == 2


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
    circuit = payload["endpoints"]["clob.polymarket.com:clob-market-data"]
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


def test_position_enumeration_reads_beyond_data_api_default_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.data import polymarket_client as client_module

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    offsets: list[str] = []

    def get(url: str, **kwargs: Any) -> httpx.Response:
        params = kwargs["params"]
        offsets.append(params["offset"])
        offset = int(params["offset"])
        page = [
            {
                "asset": f"token-{index}",
                "size": 5,
                "avgPrice": 0.5,
            }
            for index in range(offset, offset + 500)
        ]
        if offset:
            page = [
                {
                    "asset": "held-token-after-default-page",
                    "size": 5,
                    "avgPrice": 0.79,
                }
            ]
        return httpx.Response(
            200,
            json=page,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(client_module, "polymarket_request_governor", governor)
    monkeypatch.setattr(client_module.httpx, "get", get)
    monkeypatch.setattr(
        client_module,
        "_resolve_credentials",
        lambda: {"funder_address": "wallet"},
    )

    client = object.__new__(client_module.PolymarketClient)
    positions = client.get_positions_from_api()

    assert positions is not None
    assert len(positions) == 501
    assert positions[-1]["token_id"] == "held-token-after-default-page"
    assert offsets == ["0", "500"]


# ---------------------------------------------------------------------------
# Endpoint-class + priority-tier refactor: P0 cancel > P1 FC-03 book/fee/submit
# > P2 held+resting risk refresh > P3 discovery/analytics/reconciliation.
# ---------------------------------------------------------------------------


def test_endpoint_class_classification_is_explicit_not_default_fallthrough() -> None:
    assert _endpoint_class("https://clob.polymarket.com/book") is EndpointClass.MARKET_DATA
    assert _endpoint_class("https://clob.polymarket.com/books") is EndpointClass.MARKET_DATA
    assert _endpoint_class("https://clob.polymarket.com/time") is EndpointClass.MARKET_DATA
    assert _endpoint_class("https://clob.polymarket.com/markets/0xabc") is EndpointClass.DISCOVERY
    assert _endpoint_class("https://clob.polymarket.com/order") is EndpointClass.TRADING
    assert _endpoint_class("https://clob.polymarket.com/fee-rate-scale") is EndpointClass.FEE_SCHEDULE
    assert _endpoint_class("https://gamma-api.polymarket.com/events") is EndpointClass.DISCOVERY
    assert _endpoint_class("https://gamma-api.polymarket.com/markets") is EndpointClass.DISCOVERY
    assert _endpoint_class("https://data-api.polymarket.com/positions") is EndpointClass.ANALYTICS
    # Unknown host/path defaults to UNKNOWN -- the safe (P3-treated) direction,
    # never a silent inherited money-critical class.
    assert _endpoint_class("https://clob.polymarket.com/unmapped-path") is EndpointClass.UNKNOWN
    assert _endpoint_class("https://unknown-host.polymarket.com/x") is EndpointClass.UNKNOWN


def test_gamma_discovery_exhaustion_does_not_deny_clob_market_data_admission(tmp_path: Path) -> None:
    """Antibody: low-value gamma discovery traffic must never blind FC-03 book truth."""

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    # Exhaust and outright fail gamma discovery repeatedly (SCAN tier). Once
    # the circuit trips, further SCAN-tier attempts are correctly denied at
    # admission (mirroring a real scanner retry loop swallowing denials).
    for index in range(20):
        with contextlib.suppress(RequestAdmissionDenied):
            governor.request(
                lambda: _response(503),
                "GET",
                "https://gamma-api.polymarket.com/events",
                params={"slug": f"city-{index}"},
                priority=RequestPriority.SCAN,
            )
    # The independent clob-market-data class (different host entirely) must
    # still admit a fresh FC-03 book fetch at SUBMIT_JIT (P1).
    lease = governor.acquire(
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "entry-candidate"},
        priority=RequestPriority.SUBMIT_JIT,
    )
    assert governor.record_success(lease) is True


def test_clob_discovery_scan_failures_do_not_embargo_clob_market_data_same_host(
    tmp_path: Path,
) -> None:
    """The actual self-inflicted-blindness fix: same HOST, different endpoint class.

    market_scanner's high-frequency CLOB liveness/archived check
    (/markets/{condition_id}, SCAN tier) must not share a failure circuit
    with the FC-03 book/price fetch (/book, SUBMIT_JIT tier) even though
    both hit clob.polymarket.com.
    """

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    for index in range(10):
        with contextlib.suppress(RequestAdmissionDenied):
            governor.request(
                lambda: _response(503),
                "GET",
                f"https://clob.polymarket.com/markets/0xcond{index}",
                priority=RequestPriority.SCAN,
            )
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        governor.acquire(
            "GET",
            "https://clob.polymarket.com/markets/0xcond-next",
            priority=RequestPriority.SCAN,
        )
    # clob-market-data is a distinct circuit on the same host: unaffected.
    lease = governor.acquire(
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "unaffected"},
        priority=RequestPriority.SUBMIT_JIT,
    )
    assert governor.record_success(lease) is True
    payload = json.loads((tmp_path / "governor.json").read_text())
    assert "clob.polymarket.com:discovery" in payload["endpoints"]
    assert "clob.polymarket.com:clob-market-data" in payload["endpoints"]


def test_clob_discovery_embargo_does_not_block_held_risk_market_probe(
    tmp_path: Path,
) -> None:
    """Held SELL tradeability revalidation has an independent circuit."""

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    for index in range(10):
        with contextlib.suppress(RequestAdmissionDenied):
            governor.request(
                lambda: _response(503),
                "GET",
                f"https://clob.polymarket.com/markets/0xscan{index}",
                priority=RequestPriority.SUBMIT_JIT,
            )
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        governor.acquire(
            "GET",
            "https://clob.polymarket.com/markets/0xheld",
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )

    lease = governor.acquire(
        "GET",
        "https://clob.polymarket.com/markets/0xheld",
        priority=RequestPriority.HELD_REDUCE_ONLY,
        endpoint_class_override=EndpointClass.HELD_RISK,
    )
    assert lease.endpoint_class == EndpointClass.HELD_RISK.value
    assert governor.record_success(lease) is True

    failed = governor.acquire(
        "GET",
        "https://clob.polymarket.com/markets/0xfailing-held",
        priority=RequestPriority.HELD_REDUCE_ONLY,
        endpoint_class_override=EndpointClass.HELD_RISK,
    )
    assert governor.record_failure(failed) is True
    unaffected = governor.acquire(
        "GET",
        "https://clob.polymarket.com/markets/0xother-held",
        priority=RequestPriority.HELD_REDUCE_ONLY,
        endpoint_class_override=EndpointClass.HELD_RISK,
    )
    assert governor.record_success(unaffected) is True

    payload = json.loads((tmp_path / "governor.json").read_text())
    assert "clob.polymarket.com:clob-held-risk:0xfailing-held" in payload["endpoints"]
    assert "clob.polymarket.com:clob-held-risk:0xother-held" in payload["endpoints"]
    with pytest.raises(
        ValueError, match="POLYMARKET_ENDPOINT_CLASS_OVERRIDE_INVALID"
    ):
        governor.acquire(
            "GET",
            "https://gamma-api.polymarket.com/markets",
            endpoint_class_override=EndpointClass.HELD_RISK,
        )


def test_held_book_attempt_bypasses_shared_market_data_embargo(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    url = "https://clob.polymarket.com/book"
    params = {"token_id": "held-token"}

    failed = governor.acquire(
        "GET",
        url,
        params=params,
        priority=RequestPriority.SUBMIT_JIT,
    )
    assert governor.record_failure(failed) is True
    with pytest.raises(RequestAdmissionDenied, match="ENDPOINT_EMBARGOED"):
        governor.acquire(
            "GET",
            url,
            params={"token_id": "other-token"},
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )

    held = governor.acquire(
        "GET",
        url,
        params=params,
        priority=RequestPriority.HELD_REDUCE_ONLY,
        endpoint_class_override=EndpointClass.HELD_RISK,
    )
    assert held.endpoint == "clob.polymarket.com:clob-held-risk:held-token"
    assert governor.record_success(held) is True


def test_held_risk_attempt_bypasses_local_route_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(
        governor_module._ROUTE_LIMITS,
        "clob.polymarket.com:/book",
        2,
    )
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    url = "https://clob.polymarket.com/book"
    for index in range(2):
        lease = governor.acquire(
            "GET",
            url,
            params={"token_id": f"ordinary-{index}"},
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )
        assert governor.record_success(lease) is True
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_LIMIT"):
        governor.acquire(
            "GET",
            url,
            params={"token_id": "ordinary-over-limit"},
            priority=RequestPriority.HELD_REDUCE_ONLY,
        )

    held = governor.acquire(
        "GET",
        url,
        params={"token_id": "held-over-limit"},
        priority=RequestPriority.HELD_REDUCE_ONLY,
        endpoint_class_override=EndpointClass.HELD_RISK,
    )
    assert governor.record_success(held) is True


def test_held_client_routes_book_reads_to_exact_held_risk_circuits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.data import polymarket_client as client_module

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    monkeypatch.setattr(client_module, "polymarket_request_governor", governor)
    monkeypatch.setattr(
        client_module.httpx,
        "get",
        lambda url, **_kwargs: httpx.Response(
            200,
            json={"asset_id": "held-one", "bids": [], "asks": []},
            request=httpx.Request("GET", url),
        ),
    )
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda url, **_kwargs: httpx.Response(
            200,
            json=[{"asset_id": "held-one", "bids": [], "asks": []}],
            request=httpx.Request("POST", url),
        ),
    )

    client = object.__new__(client_module.PolymarketClient)
    client._public_request_priority = RequestPriority.HELD_REDUCE_ONLY
    assert client.get_orderbook_snapshot("held-one")["asset_id"] == "held-one"
    assert set(client.get_orderbook_snapshots(["held-one"])) == {"held-one"}

    endpoints = json.loads((tmp_path / "governor.json").read_text())["endpoints"]
    assert "clob.polymarket.com:clob-held-risk:held-one" in endpoints
    assert any(
        key.startswith("clob.polymarket.com:clob-held-risk:books-")
        for key in endpoints
    )


def test_cancel_priority_preempts_submit_jit_under_contention(tmp_path: Path) -> None:
    """P0 (cancel) preempts an in-flight P1 (SUBMIT_JIT) request on the same identity."""

    state = tmp_path / "governor.json"
    submit = PolymarketRequestGovernor(state_file=state)
    cancel = PolymarketRequestGovernor(state_file=state)
    started = threading.Event()
    release = threading.Event()
    outcome: list[object] = []
    url = "https://clob.polymarket.com/book"
    params = {"token_id": "contended"}

    def submit_send() -> httpx.Response:
        started.set()
        assert release.wait(2.0)
        return _response(200)

    def run_submit() -> None:
        try:
            outcome.append(
                submit.request(
                    submit_send,
                    "GET",
                    url,
                    params=params,
                    priority=RequestPriority.SUBMIT_JIT,
                )
            )
        except Exception as exc:  # noqa: BLE001 - asserting the fenced outcome
            outcome.append(exc)

    thread = threading.Thread(target=run_submit)
    thread.start()
    assert started.wait(2.0)
    response = cancel.request(
        lambda: _response(200),
        "GET",
        url,
        params=params,
        priority=RequestPriority.CANCEL,
    )
    release.set()
    thread.join(2.0)

    assert response.status_code == 200
    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], RequestAdmissionDenied)
    assert "LEASE_LOST" in str(outcome[0])


def test_priority_inversion_absent_across_full_tier_chain(tmp_path: Path) -> None:
    """P3 < P2 < P1 < P0: each higher tier always bypasses a lower-tier circuit,
    and the circuit ratchet never lets a lower tier block a higher one."""

    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    url = "https://clob.polymarket.com/book"

    scan = governor.acquire("GET", url, params={"token_id": "p3"}, priority=RequestPriority.SCAN)
    assert governor.record_failure(scan) is True

    held = governor.acquire("GET", url, params={"token_id": "p2"}, priority=RequestPriority.HELD_REDUCE_ONLY)
    assert governor.record_failure(held) is True

    submit = governor.acquire("GET", url, params={"token_id": "p1"}, priority=RequestPriority.SUBMIT_JIT)
    assert governor.record_failure(submit) is True

    cancel = governor.acquire("GET", url, params={"token_id": "p0"}, priority=RequestPriority.CANCEL)
    assert governor.record_success(cancel) is True


def test_multiple_endpoint_429s_are_isolated_across_classes(tmp_path: Path) -> None:
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json")
    cases = [
        ("https://clob.polymarket.com/book", {"token_id": "a"}),
        ("https://clob.polymarket.com/markets/0xcond", None),
        ("https://gamma-api.polymarket.com/events", {"slug": "b"}),
        ("https://data-api.polymarket.com/positions", {"user": "c"}),
    ]
    for url, params in cases:
        response = governor.request(
            lambda: _response(429, {"Retry-After": "30"}),
            "GET",
            url,
            params=params,
        )
        assert response.status_code == 429
    # Each route embargo is independent: a fresh request against every OTHER
    # route (different params/identity) still succeeds despite all four
    # having just been rate-limited.
    other = [
        ("https://clob.polymarket.com/book", {"token_id": "a-2"}),
        ("https://clob.polymarket.com/markets/0xcond2", None),
        ("https://gamma-api.polymarket.com/events", {"slug": "b-2"}),
        ("https://data-api.polymarket.com/positions", {"user": "c-2"}),
    ]
    for index, (url, params) in enumerate(other):
        with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
            governor.acquire("GET", url, params=params)


def test_p3_route_budget_exhaustion_never_denies_p1_admission(tmp_path: Path) -> None:
    clock = _Clock()
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state, clock=clock)
    url = "https://data-api.polymarket.com/positions"
    # Exhaust the 80% low-tier (P3) share of the 150 official limit.
    for index in range(120):
        lease = governor.acquire("GET", url, params={"user": str(index)}, priority=RequestPriority.SCAN)
        assert governor.record_success(lease) is True
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_LIMIT"):
        governor.acquire("GET", url, params={"user": "p3-over-share"}, priority=RequestPriority.SCAN)
    # P1 (SUBMIT_JIT) is unaffected by the P3 exhaustion: still admitted, up
    # to the full official limit.
    lease = governor.acquire("GET", url, params={"user": "p1-unblocked"}, priority=RequestPriority.SUBMIT_JIT)
    assert governor.record_success(lease) is True


def test_work_conservation_p3_gets_full_reserved_share_when_higher_tiers_idle(
    tmp_path: Path,
) -> None:
    """P3 is never throttled BELOW its guaranteed 80% share merely because no
    higher-tier traffic is present -- the reservation is a floor for P0-P2,
    not a punitive cap that shrinks P3 further when capacity is idle."""

    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state)
    url = "https://data-api.polymarket.com/positions"
    for index in range(120):
        lease = governor.acquire("GET", url, params={"user": f"idle-{index}"}, priority=RequestPriority.SCAN)
        assert governor.record_success(lease) is True
    # The 120th (80% of 150) succeeded with zero P0-P2 contention; the 121st
    # still correctly hits its reserved-share ceiling (not starved earlier,
    # not granted more than its guaranteed share).
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_LIMIT"):
        governor.acquire("GET", url, params={"user": "idle-over"}, priority=RequestPriority.SCAN)


def test_missing_retry_after_still_backs_off_the_route(tmp_path: Path) -> None:
    clock = _Clock()
    governor = PolymarketRequestGovernor(state_file=tmp_path / "governor.json", clock=clock)
    response = governor.request(
        lambda: _response(429),  # no Retry-After header at all
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "no-retry-after"},
    )
    assert response.status_code == 429
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire(
            "GET", "https://gamma-api.polymarket.com/events", params={"slug": "next"}
        )


def test_clock_jump_tolerance_expires_embargo_without_error(tmp_path: Path) -> None:
    clock = _Clock()
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state, clock=clock)
    governor.request(
        lambda: _response(429, {"Retry-After": "30"}),
        "GET",
        "https://gamma-api.polymarket.com/events",
        params={"slug": "jump"},
    )
    with pytest.raises(RequestAdmissionDenied, match="ROUTE_EMBARGOED"):
        governor.acquire("GET", "https://gamma-api.polymarket.com/events", params={"slug": "jump-2"})
    # A large forward clock jump (e.g. host clock resync / long GC pause)
    # must not raise and must correctly treat the embargo as expired.
    clock.advance(36_000)
    lease = governor.acquire("GET", "https://gamma-api.polymarket.com/events", params={"slug": "jump-3"})
    assert governor.record_success(lease) is True


def test_legacy_global_governor_rollback_flag_restores_host_only_keying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ZEUS_GOVERNOR_LEGACY_GLOBAL=1 is the one-release rollback to the
    former host-only (not (host, class)) endpoint circuit keying."""

    monkeypatch.setenv("ZEUS_GOVERNOR_LEGACY_GLOBAL", "1")
    state = tmp_path / "governor.json"
    governor = PolymarketRequestGovernor(state_file=state)
    governor.request(
        lambda: _response(503),
        "GET",
        "https://clob.polymarket.com/book",
        params={"token_id": "legacy"},
        priority=RequestPriority.SCAN,
    )
    payload = json.loads(state.read_text())
    assert "clob.polymarket.com" in payload["endpoints"]
    assert "clob.polymarket.com:clob-market-data" not in payload["endpoints"]
