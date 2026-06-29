# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: review finding P2-2 (fix/review-findings-2026-06-03)
#                  Surface market_events persistence failure to trading-daemon scheduler health.
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: Relationship test guarding that a persistence failure in _persist_market_events_to_db is surfaced to the scheduler health signal, not silently swallowed (P2-2 fix).
# Reuse: Confirm MarketEventsPersistenceResult schema + scheduler_health contract match current production code before relying on test as evidence.
"""Relationship test: market_events persistence failure surfaces to scheduler health.

Invariant: when _market_discovery_cycle() sees >=1 parsed weather event but
_persist_market_events_to_db() returns status="failed", the trading-daemon
scheduler health must mark market_discovery FAILED — not healthy.

RED baseline confirmed on pre-fix code (persistence failure was invisible to
scheduler health); GREEN after P2-2 fix.
"""
import contextlib
import threading

import pytest
import src.data.substrate_observer as substrate_observer


def _make_fake_persistence_result(status: str, inserted: int = 0, event_count: int = 1,
                                   error: str | None = None):
    from src.data.market_scanner import MarketEventsPersistenceResult
    return MarketEventsPersistenceResult(
        status=status,
        inserted=inserted,
        event_count=event_count,
        error=error,
    )


class FakePolymarketClient:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass


class FakeConn:
    def commit(self):
        pass

    def close(self):
        pass


def _fake_refresh(*_args, **_kwargs):
    return {"attempted": 1, "inserted": 1, "skipped": 0, "failed": 0, "truncated": 0}


def test_persistence_result_is_thread_isolated():
    """Thread isolation: each thread's find_weather_markets() persist result is private.

    Thread A writes status="failed"; thread B writes status="ok" (concurrently or interleaved).
    Each thread's get_last_market_events_persistence_result() must return ITS OWN result.

    Against the old process-global both threads would see whichever wrote last; against the
    thread-local fix each thread sees only its own write.
    """
    import src.data.market_scanner as ms
    from src.data.market_scanner import (
        MarketEventsPersistenceResult,
        _PERSISTENCE_RESULT_LOCAL,
        get_last_market_events_persistence_result,
    )

    barrier = threading.Barrier(2)

    thread_a_result: list = []
    thread_b_result: list = []

    def thread_a():
        # Simulate: _parse_and_persist_weather_events wrote a "failed" outcome for this thread.
        _PERSISTENCE_RESULT_LOCAL.result = MarketEventsPersistenceResult(
            status="failed",
            inserted=0,
            event_count=1,
            error="db locked",
        )
        barrier.wait()  # ensure thread B also writes before either reads
        thread_a_result.append(get_last_market_events_persistence_result())

    def thread_b():
        # Simulate: a concurrent find_weather_markets() in the market-channel thread succeeded.
        _PERSISTENCE_RESULT_LOCAL.result = MarketEventsPersistenceResult(
            status="ok",
            inserted=2,
            event_count=2,
            error=None,
        )
        barrier.wait()
        thread_b_result.append(get_last_market_events_persistence_result())

    ta = threading.Thread(target=thread_a)
    tb = threading.Thread(target=thread_b)
    ta.start()
    tb.start()
    ta.join(timeout=5)
    tb.join(timeout=5)

    assert thread_a_result, "thread A never completed"
    assert thread_b_result, "thread B never completed"

    a_status = thread_a_result[0].status if thread_a_result[0] is not None else None
    b_status = thread_b_result[0].status if thread_b_result[0] is not None else None

    assert a_status == "failed", (
        f"Thread A expected 'failed' but got {a_status!r}. "
        "If both threads see the same status, the slot is still process-global (not thread-local)."
    )
    assert b_status == "ok", (
        f"Thread B expected 'ok' but got {b_status!r}. "
        "If both threads see the same status, the slot is still process-global (not thread-local)."
    )


def test_trading_daemon_surfaces_persistence_failure(monkeypatch):
    """RED→GREEN: nonempty discovery + failed persistence raises, scheduler marks FAILED.

    Pre-fix: persistence failure was completely invisible; scheduler_health showed OK.
    Post-fix: RuntimeError raised → decorator writes failed=True to scheduler_health.
    """
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.ingest.substrate_observer_daemon as observer_daemon
    import src.observability.scheduler_health as scheduler_health
    import src.state.db as state_db

    health_calls: list[tuple[str, dict]] = []

    def _record_health(job_name, **kwargs):
        health_calls.append((job_name, kwargs))

    # P2 lift: scheduler health for market_discovery is now written by the substrate-observer
    # DAEMON's _scheduler_job wrapper (the bare lifted producer carries no decorator), which
    # imports _write_scheduler_health from src.observability.scheduler_health. Patch it there
    # and exercise the producer THROUGH the daemon wrapper (the as-deployed unit).
    monkeypatch.setattr(scheduler_health, "_write_scheduler_health", _record_health)

    # Discovery returns 2 events (non-empty).
    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_kw: [{"slug": "s1"}, {"slug": "s2"}],
    )

    # Persistence FAILED — this is the condition being tested.
    failed_result = _make_fake_persistence_result(
        status="failed",
        inserted=0,
        event_count=2,
        error="sqlite3.OperationalError: database is locked",
    )
    monkeypatch.setattr(
        market_scanner,
        "get_last_market_events_persistence_result",
        lambda: failed_result,
    )

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class: FakeConn())
    monkeypatch.setattr(market_scanner, "refresh_executable_market_substrate_snapshots", _fake_refresh)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    # Force STALE substrate so the staleness gate falls through to the capture path.
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)

    # Run the producer through the daemon's fail-soft + health wrapper (the as-deployed unit).
    observer_daemon._scheduler_job("market_discovery")(
        substrate_observer._market_discovery_cycle
    )()

    # The scheduler health for market_discovery must be FAILED (failed=True).
    failed_entries = [
        (name, kw) for (name, kw) in health_calls
        if name == "market_discovery" and kw.get("failed") is True
    ]
    assert failed_entries, (
        "market_discovery scheduler health was not marked FAILED when persistence failed. "
        f"All health calls: {health_calls}"
    )


def test_market_discovery_persistence_failure_not_hidden_by_empty_priority_marker(monkeypatch):
    """Priority markers are advisory; they must not mask full-discovery write failure."""

    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.ingest.substrate_observer_daemon as observer_daemon
    import src.observability.scheduler_health as scheduler_health
    import src.state.db as state_db

    health_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: health_calls.append((job_name, kwargs)),
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_families", lambda: [])
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_condition_ids", lambda: [])
    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_kw: [{"slug": "s1"}, {"slug": "s2"}],
    )
    monkeypatch.setattr(
        market_scanner,
        "get_last_market_events_persistence_result",
        lambda: _make_fake_persistence_result(
            status="failed",
            inserted=0,
            event_count=2,
            error="sqlite3.OperationalError: database is locked",
        ),
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class: FakeConn())
    monkeypatch.setattr(market_scanner, "refresh_executable_market_substrate_snapshots", _fake_refresh)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)

    observer_daemon._scheduler_job("market_discovery")(
        substrate_observer._market_discovery_cycle
    )()

    failed_entries = [
        (name, kw) for (name, kw) in health_calls
        if name == "market_discovery" and kw.get("failed") is True
    ]
    assert failed_entries, (
        "empty priority marker hid market_discovery persistence failure. "
        f"All health calls: {health_calls}"
    )


def test_trading_daemon_healthy_when_persistence_ok(monkeypatch):
    """Guard: when persistence succeeds, scheduler health is NOT marked failed."""
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.ingest.substrate_observer_daemon as observer_daemon
    import src.observability.scheduler_health as scheduler_health
    import src.state.db as state_db

    health_calls: list[tuple[str, dict]] = []

    def _record_health(job_name, **kwargs):
        health_calls.append((job_name, kwargs))

    # P2 lift: health is written by the daemon's _scheduler_job wrapper (see the FAILED-case
    # test above). Patch the writer at its source and run the producer through the wrapper.
    monkeypatch.setattr(scheduler_health, "_write_scheduler_health", _record_health)

    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_kw: [{"slug": "s1"}],
    )

    ok_result = _make_fake_persistence_result(
        status="written",
        inserted=3,
        event_count=1,
    )
    monkeypatch.setattr(
        market_scanner,
        "get_last_market_events_persistence_result",
        lambda: ok_result,
    )

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class: FakeConn())
    monkeypatch.setattr(market_scanner, "refresh_executable_market_substrate_snapshots", _fake_refresh)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    # Force STALE substrate so the staleness gate falls through to the capture path.
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)

    observer_daemon._scheduler_job("market_discovery")(
        substrate_observer._market_discovery_cycle
    )()

    # No FAILED entry for market_discovery.
    failed_entries = [
        (name, kw) for (name, kw) in health_calls
        if name == "market_discovery" and kw.get("failed") is True
    ]
    assert not failed_entries, (
        f"market_discovery was incorrectly marked FAILED when persistence succeeded. "
        f"All health calls: {health_calls}"
    )
    # There should be an OK entry.
    ok_entries = [
        (name, kw) for (name, kw) in health_calls
        if name == "market_discovery" and kw.get("failed") is False
    ]
    assert ok_entries, f"Expected an OK health entry for market_discovery. Calls: {health_calls}"
