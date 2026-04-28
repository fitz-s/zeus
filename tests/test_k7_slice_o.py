"""Slice O regression tests — external client reliability (#36, #40, #44, #45)."""
import ast
import importlib
import os
import time
from unittest import mock

import pytest


# ── Bug #36: unified HTTP client in daily_obs_append ───────────────────────

def test_no_requests_import_in_daily_obs_append():
    """daily_obs_append must use httpx exclusively — no 'import requests'."""
    import src.data.daily_obs_append as mod
    src_path = mod.__file__
    tree = ast.parse(open(src_path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "requests", (
                    f"daily_obs_append.py still imports 'requests' at line {node.lineno}"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("requests"), (
                f"daily_obs_append.py still uses 'from requests...' at line {node.lineno}"
            )


# ── Bug #40: cache TTL on _ACTIVE_EVENTS_CACHE ────────────────────────────

def test_active_events_cache_has_ttl():
    """market_scanner cache must respect a TTL — stale data auto-invalidates."""
    import src.data.market_scanner as ms

    assert hasattr(ms, "_ACTIVE_EVENTS_TTL"), "missing _ACTIVE_EVENTS_TTL"
    assert ms._ACTIVE_EVENTS_TTL > 0, "TTL must be positive"
    assert hasattr(ms, "_ACTIVE_EVENTS_CACHE_AT"), "missing _ACTIVE_EVENTS_CACHE_AT"


def test_cache_invalidates_after_ttl(monkeypatch):
    """After TTL expires, _get_active_events() must re-fetch."""
    import src.data.market_scanner as ms

    call_count = 0

    def fake_fetch():
        nonlocal call_count
        call_count += 1
        return [{"id": call_count}]

    monkeypatch.setattr(ms, "_fetch_events_by_tags", fake_fetch)
    ms._clear_active_events_cache()

    # First call populates cache
    result1 = ms._get_active_events()
    assert call_count == 1

    # Second call within TTL uses cache
    result2 = ms._get_active_events()
    assert call_count == 1  # no re-fetch

    # Simulate TTL expiry by backdating the cache timestamp
    ms._ACTIVE_EVENTS_CACHE_AT = time.monotonic() - ms._ACTIVE_EVENTS_TTL - 1

    # Third call after TTL re-fetches
    result3 = ms._get_active_events()
    assert call_count == 2, "cache should have been invalidated after TTL"

    # Cleanup
    ms._clear_active_events_cache()


# ── Bug #44: no hardcoded paths in polymarket_client ───────────────────────

def test_no_hardcoded_home_in_polymarket_client():
    """polymarket_client must not contain hardcoded /Users/ paths."""
    import src.data.polymarket_client as mod
    src_path = mod.__file__
    source = open(src_path).read()
    assert "/Users/leofitz" not in source, (
        "polymarket_client.py still contains hardcoded /Users/leofitz path"
    )


def test_resolve_credentials_uses_env_var():
    """_resolve_credentials should respect OPENCLAW_HOME env var."""
    import src.data.polymarket_client as mod
    import inspect
    source = inspect.getsource(mod._resolve_credentials)
    assert "OPENCLAW_HOME" in source, (
        "_resolve_credentials must look up OPENCLAW_HOME env var"
    )


# ── Bug #45: lazy client init ─────────────────────────────────────────────

def test_polymarket_client_init_is_lazy():
    """PolymarketClient() must NOT call _ensure_client in __init__."""
    import src.data.polymarket_client as mod

    # Instantiation must NOT trigger credential resolution
    with mock.patch.object(mod, "_resolve_credentials", side_effect=RuntimeError("should not be called")):
        client = mod.PolymarketClient()
        # If we get here, __init__ did NOT call _resolve_credentials
        assert client._clob_client is None


def test_ensure_client_called_on_cancel():
    """cancel_order must route through the V2 adapter behind CutoverGuard."""
    import src.data.polymarket_client as mod
    import inspect
    source = inspect.getsource(mod.PolymarketClient.cancel_order)
    assert "gate_for_intent" in source and "IntentKind.CANCEL" in source
    assert "_ensure_v2_adapter" in source, (
        "cancel_order must call the V2 adapter boundary for lazy live I/O"
    )
