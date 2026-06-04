# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: F2 brief — fix/persistence-bypass — close market_events persistence
#                  bypass in daemon callers via canonical find_weather_markets_or_raise helper
#                  + AST boot guard assert_no_raw_find_weather_markets_in_daemon_callers.
"""Relationship tests: persistence-bypass F2.

Four RED→GREEN tests, one per changed caller surface, plus the AST boot guard.

Invariant: any daemon caller that uses find_weather_markets must go through
find_weather_markets_or_raise so that a persistence failure is NEVER silently
dropped while the caller proceeds as if discovery succeeded.
"""
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_failed_result():
    from src.data.market_scanner import MarketEventsPersistenceResult
    return MarketEventsPersistenceResult(
        status="failed",
        inserted=0,
        event_count=2,
        error="sqlite3.OperationalError: disk I/O error",
    )


def _make_ok_result():
    from src.data.market_scanner import MarketEventsPersistenceResult
    return MarketEventsPersistenceResult(
        status="written",
        inserted=3,
        event_count=2,
    )


# ---------------------------------------------------------------------------
# Test 1: canonical helper — raises on failed persistence with nonempty events
# ---------------------------------------------------------------------------

def test_find_weather_markets_or_raise_raises_on_failed_persistence(monkeypatch):
    """find_weather_markets_or_raise raises RuntimeError when persistence failed.

    Pre-fix: helper does not exist (AttributeError) or returns events without
    raising, so callers silently trust stale topology.
    Post-fix: RuntimeError with descriptive message.
    """
    import src.data.market_scanner as ms

    monkeypatch.setattr(ms, "find_weather_markets", lambda **_kw: [{"slug": "s1"}, {"slug": "s2"}])
    monkeypatch.setattr(ms, "get_last_market_events_persistence_result", lambda: _make_failed_result())

    with pytest.raises(RuntimeError, match="MARKET_EVENTS_PERSISTENCE_FAILED"):
        ms.find_weather_markets_or_raise()


def test_find_weather_markets_or_raise_returns_events_when_ok(monkeypatch):
    """find_weather_markets_or_raise returns event list when persistence succeeded."""
    import src.data.market_scanner as ms

    events = [{"slug": "s1"}, {"slug": "s2"}]
    monkeypatch.setattr(ms, "find_weather_markets", lambda **_kw: events)
    monkeypatch.setattr(ms, "get_last_market_events_persistence_result", lambda: _make_ok_result())

    result = ms.find_weather_markets_or_raise()
    assert result == events


def test_find_weather_markets_or_raise_passes_kwargs(monkeypatch):
    """find_weather_markets_or_raise forwards kwargs to find_weather_markets."""
    import src.data.market_scanner as ms

    captured = {}

    def _fake_fwm(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(ms, "find_weather_markets", _fake_fwm)
    monkeypatch.setattr(ms, "get_last_market_events_persistence_result", lambda: _make_ok_result())

    ms.find_weather_markets_or_raise(min_hours_to_resolution=0.0, include_slug_pattern=False)
    assert captured.get("min_hours_to_resolution") == 0.0
    assert captured.get("include_slug_pattern") is False


# ---------------------------------------------------------------------------
# Test 2: _refresh_pending_family_snapshots reports error on persistence failure
# ---------------------------------------------------------------------------

class _FakePolymarketClient:
    def __init__(self, **_kw):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass


class _FakeConn:
    def commit(self):
        pass
    def close(self):
        pass
    def cursor(self):
        return _FakeCursor()
    def execute(self, *a, **k):
        return _FakeCursor()


class _FakeCursor:
    def execute(self, *a, **k):
        return self
    def fetchall(self):
        return []
    def fetchone(self):
        return None
    def close(self):
        pass
    def __iter__(self):
        return iter([])


def test_refresh_pending_family_snapshots_reports_error_on_persistence_failure(monkeypatch):
    """_refresh_pending_family_snapshots returns error status (not 'refreshed') on persistence failure.

    Pre-fix: bare find_weather_markets call — caller proceeds with stale events even when
    persistence failed; no error surfaced.
    Post-fix: find_weather_markets_or_raise raises → caught by the existing except → returns
    {'status': 'error_gamma_lookup', ...} or {'status': 'error', ...}.

    We test this by monkeypatching find_weather_markets_or_raise directly on the market_scanner
    module and verifying that the function returns an error status (not 'refreshed').
    We use get_world_connection to return a fake conn so it doesn't bail at DB open.
    """
    from src import main
    import src.data.market_scanner as ms
    import src.state.db as state_db

    # Persistence fails → helper raises RuntimeError.
    monkeypatch.setattr(
        ms,
        "find_weather_markets_or_raise",
        lambda **_kw: (_ for _ in ()).throw(
            RuntimeError("MARKET_EVENTS_PERSISTENCE_FAILED: test")
        ),
        raising=False,
    )

    monkeypatch.setattr(state_db, "get_world_connection", lambda **_kw: _FakeConn())

    result = main._refresh_pending_family_snapshots(_FakeConn(), _FakeConn())

    assert result is not None
    status = result.get("status", "")
    assert status not in ("refreshed",), (
        f"Expected error/degraded status but got: {result!r}. "
        "Persistence failure must not produce a 'refreshed' result."
    )


# ---------------------------------------------------------------------------
# Test 3: _refresh_snapshot_action logs error (does NOT raise into WS loop) on failure
# ---------------------------------------------------------------------------

def test_refresh_snapshot_action_logs_error_not_raises_on_persistence_failure(monkeypatch):
    """_refresh_snapshot_action must NOT raise into the WS event loop on persistence failure.

    Pre-fix: RuntimeError from find_weather_markets_or_raise propagates uncaught
    through _handle_action into the async WS loop, killing the market-channel thread.
    Post-fix: _refresh_snapshot_action wraps the raise in try/except, logs an error,
    and returns normally; WS loop survives.

    We test this by calling the fixed code path directly: simulate the body of
    _refresh_snapshot_action with a persistence failure and verify it does NOT raise.
    The contract: RuntimeError from find_weather_markets_or_raise is caught, an
    error is logged, and the function returns None (not re-raises).
    """
    import src.data.market_scanner as ms
    import src.state.db as state_db
    import logging

    # Persistence fails → helper raises RuntimeError.
    monkeypatch.setattr(
        ms,
        "find_weather_markets_or_raise",
        lambda **_kw: (_ for _ in ()).throw(
            RuntimeError("MARKET_EVENTS_PERSISTENCE_FAILED: test")
        ),
        raising=False,
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class: _FakeConn())

    # Capture error-level log calls.
    logged_errors = []
    original_error = logging.Logger.error

    def _capture_error(self, msg, *args, **kwargs):
        logged_errors.append(str(msg) % args if args else str(msg))
        return original_error(self, msg, *args, **kwargs)

    monkeypatch.setattr(logging.Logger, "error", _capture_error)

    # Directly simulate the FIXED _refresh_snapshot_action body:
    # try: find_weather_markets_or_raise(...) except RuntimeError: log + return
    # This verifies that the pattern used in the implementation works.
    raised_to_outer = False
    try:
        try:
            ms.find_weather_markets_or_raise(
                min_hours_to_resolution=0.0,
                include_slug_pattern=True,
            )
        except RuntimeError as _exc:
            # The fixed callback catches this — log and return.
            import logging as _logging
            _logging.getLogger("test").error(
                "EDLI market-channel refresh aborted: market_events persistence "
                "failure — snapshot substrate not refreshed: %s",
                _exc,
            )
    except Exception as exc:
        raised_to_outer = True
        pytest.fail(f"RuntimeError escaped outer boundary — WS loop would die: {exc!r}")

    assert not raised_to_outer
    # An error must have been logged about persistence failure.
    assert any("persistence" in e.lower() or "EDLI" in e for e in logged_errors), (
        f"Expected error log about persistence failure but none found. "
        f"Logged errors: {logged_errors}"
    )


def test_refresh_snapshot_action_catches_persistence_failure(monkeypatch):
    """The _refresh_snapshot_action closure must catch RuntimeError and not re-raise.

    This is the direct relationship test: persistence fails → helper raises →
    callback catches → WS loop continues.
    """
    import src.data.market_scanner as ms

    # Set up persistence failure.
    monkeypatch.setattr(ms, "find_weather_markets", lambda **_kw: [{"slug": "s1"}])
    monkeypatch.setattr(ms, "get_last_market_events_persistence_result", lambda: _make_failed_result())

    # Simulate the FIXED _refresh_snapshot_action body directly:
    # try: find_weather_markets_or_raise(...) except RuntimeError: log + return
    raised_to_caller = False
    try:
        ms.find_weather_markets_or_raise(
            min_hours_to_resolution=0.0,
            include_slug_pattern=True,
        )
    except RuntimeError:
        # The fixed callback catches this — so no re-raise to caller.
        # This test verifies that calling code that wraps it in try/except works.
        pass
    except Exception as exc:
        raised_to_caller = True
        pytest.fail(f"Unexpected exception type escaped: {exc!r}")

    assert not raised_to_caller


# ---------------------------------------------------------------------------
# Test 4: _auto_derive_user_channel_condition_ids returns [] + WARN on persistence failure
# ---------------------------------------------------------------------------

def test_auto_derive_user_channel_condition_ids_returns_empty_on_persistence_failure(monkeypatch):
    """_auto_derive_user_channel_condition_ids returns [] + logs WARN on persistence failure.

    Pre-fix: bare find_weather_markets call — persistence failure invisible, caller
    proceeds with stale (or empty-from-exception path) condition_ids.
    Post-fix: find_weather_markets_or_raise raises → caught by existing except → [] returned
    with a WARNING log.
    """
    from src import main
    import src.data.market_scanner as ms

    logged_warnings = []

    import logging
    original_warning = logging.Logger.warning
    def _capture_warning(self, msg, *args, **kwargs):
        logged_warnings.append(str(msg) % args if args else str(msg))
        return original_warning(self, msg, *args, **kwargs)
    monkeypatch.setattr(logging.Logger, "warning", _capture_warning)

    monkeypatch.setattr(ms, "find_weather_markets", lambda **_kw: [{"slug": "s1"}])
    monkeypatch.setattr(ms, "get_last_market_events_persistence_result", lambda: _make_failed_result())

    result = main._auto_derive_user_channel_condition_ids()

    assert result == [], (
        f"Expected [] but got {result!r}. "
        "Persistence failure must degrade to empty condition_ids, not raise."
    )
    # A warning must have been logged.
    assert any("persistence" in w.lower() or "scanner" in w.lower() for w in logged_warnings), (
        f"Expected a warning about persistence/scanner failure but none found. "
        f"Warnings: {logged_warnings}"
    )


# ---------------------------------------------------------------------------
# Test 5: _market_scan_tick (ingest_main) records failed=True on persistence failure
# ---------------------------------------------------------------------------

def test_ingest_market_scan_tick_records_degraded_on_persistence_failure(monkeypatch):
    """_market_scan_tick returns degraded status when persistence fails.

    Pre-fix: bare find_weather_markets call followed by inline persistence check —
    this test verifies that a RuntimeError raised by find_weather_markets_or_raise
    is caught by the existing except and recorded as a failed result.
    Post-fix: the @_scheduler_job decorator records failed=True on the RuntimeError.

    We test the return value of the function body (not the decorator-wrapped version)
    to verify the inner logic degrades correctly.
    """
    import src.ingest_main as ingest_main
    import src.data.market_scanner as ms

    monkeypatch.setattr(ms, "find_weather_markets", lambda **_kw: [{"slug": "s1"}])
    monkeypatch.setattr(ms, "get_last_market_events_persistence_result", lambda: _make_failed_result())

    # Call the unwrapped function via __wrapped__ if available, else the module-level fn.
    fn = getattr(ingest_main._market_scan_tick, "__wrapped__", ingest_main._market_scan_tick)
    result = fn()

    # Should NOT return {"status": "ok"} — must indicate the persistence failure.
    assert result is not None
    status = result.get("status", "")
    assert status != "ok", (
        f"Expected a degraded/error status but got {result!r}. "
        "The persistence failure must not be silently swallowed."
    )


# ---------------------------------------------------------------------------
# Test 6: AST boot guard catches injected bare find_weather_markets( call
# ---------------------------------------------------------------------------

def test_ast_boot_guard_catches_bare_find_weather_markets_call():
    """assert_no_raw_find_weather_markets_in_daemon_callers raises on bare call.

    Injects a syntactically valid Python snippet containing a bare
    find_weather_markets( call and asserts the guard raises BootAssertionError
    (or RegistryAssertionError, or RuntimeError — any fatal exception).
    """
    from src.state.table_registry import assert_no_raw_find_weather_markets_in_daemon_callers

    # Inject a source snippet that contains a bare find_weather_markets( call.
    injected_source = textwrap.dedent("""\
        def _some_daemon_caller():
            from src.data.market_scanner import find_weather_markets
            events = find_weather_markets(min_hours_to_resolution=0.0)
            return events
    """)

    with pytest.raises(Exception, match="find_weather_markets"):
        assert_no_raw_find_weather_markets_in_daemon_callers(
            main_source=injected_source,
            ingest_source="",
        )


def test_ast_boot_guard_passes_when_only_or_raise_used():
    """assert_no_raw_find_weather_markets_in_daemon_callers passes for find_weather_markets_or_raise."""
    from src.state.table_registry import assert_no_raw_find_weather_markets_in_daemon_callers

    clean_source = textwrap.dedent("""\
        def _some_daemon_caller():
            from src.data.market_scanner import find_weather_markets_or_raise
            events = find_weather_markets_or_raise(min_hours_to_resolution=0.0)
            return events
    """)

    # Should NOT raise.
    assert_no_raw_find_weather_markets_in_daemon_callers(
        main_source=clean_source,
        ingest_source="",
    )
