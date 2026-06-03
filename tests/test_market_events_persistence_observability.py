# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: fix/review-findings-2026-06-03 P2-2 — surface market_events
# persistence failure to trading-daemon scheduler health.
"""Relationship test: market_events persistence failure surfaces to scheduler health.

Invariant: when _market_discovery_cycle() sees >=1 parsed weather event but
_persist_market_events_to_db() returns status="failed", the trading-daemon
scheduler health must mark market_discovery FAILED — not healthy.

RED baseline confirmed on pre-fix code (persistence failure was invisible to
scheduler health); GREEN after P2-2 fix.
"""
import pytest


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


def test_trading_daemon_surfaces_persistence_failure(monkeypatch):
    """RED→GREEN: nonempty discovery + failed persistence raises, scheduler marks FAILED.

    Pre-fix: persistence failure was completely invisible; scheduler_health showed OK.
    Post-fix: RuntimeError raised → decorator writes failed=True to scheduler_health.
    """
    from src import main
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    health_calls: list[tuple[str, dict]] = []

    def _record_health(job_name, **kwargs):
        health_calls.append((job_name, kwargs))

    monkeypatch.setattr(main, "_write_scheduler_health", _record_health)

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

    main._market_discovery_cycle()

    # The scheduler health for market_discovery must be FAILED (failed=True).
    failed_entries = [
        (name, kw) for (name, kw) in health_calls
        if name == "market_discovery" and kw.get("failed") is True
    ]
    assert failed_entries, (
        "market_discovery scheduler health was not marked FAILED when persistence failed. "
        f"All health calls: {health_calls}"
    )


def test_trading_daemon_healthy_when_persistence_ok(monkeypatch):
    """Guard: when persistence succeeds, scheduler health is NOT marked failed."""
    from src import main
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    health_calls: list[tuple[str, dict]] = []

    def _record_health(job_name, **kwargs):
        health_calls.append((job_name, kwargs))

    monkeypatch.setattr(main, "_write_scheduler_health", _record_health)

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

    main._market_discovery_cycle()

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
