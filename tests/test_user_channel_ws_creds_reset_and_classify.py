# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR #211 bot review (Codex P1 + Copilot) responses
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — _build_ingestor() resets the adapter's memoized SDK
#          client on each retry, AND classifies failures into AUTH_FAILED
#          vs DISCONNECTED for accurate operator telemetry.
"""Antibody tests for PR #211 bot review fixes.

## Codex P1 (line 1061 of original PR #211)

`PolymarketV2Adapter._sdk_client()` memoizes `self._client` after the first
call. If `create_or_derive_api_key()` failed at boot (e.g., transient
Polymarket /auth/api-key 400), the cached client has `creds=None`. The
WS-retry loop in src/main.py would call `_sdk_client()` again on each
retry, get the SAME cached bad client, and never recover until the next
SIGTERM.

Antibody C1: every call to _build_ingestor() must invalidate the cached
SDK client BEFORE asking for creds, so the next call to _sdk_client()
re-runs the factory + create_or_derive_api_key. Sed-flip: remove
`adapter._client = None` → C1 → RED.

## Copilot (line 1081)

Every build failure was recorded with subscription_state="AUTH_FAILED",
conflating real creds failures with transport/network/unexpected failures.
Operators relying on the WS gap guard's telemetry to diagnose live blocks
would mis-attribute every transient failure to bad credentials.

Antibody T1: creds-shape failure (RuntimeError with "creds" in message) →
AUTH_FAILED. Antibody T2: generic ConnectionError → DISCONNECTED.
Sed-flip: revert to unconditional AUTH_FAILED → T2 → RED.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import src.main as zeus_main
from src.control import ws_gap_guard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for name in (
        "ZEUS_USER_CHANNEL_WS_ENABLED",
        "ZEUS_USER_CHANNEL_WS_AUTO_DERIVE",
        "POLYMARKET_USER_WS_CONDITION_IDS",
    ):
        monkeypatch.delenv(name, raising=False)
    ws_gap_guard.clear_for_test()
    monkeypatch.setattr(zeus_main, "_user_channel_thread", None, raising=False)
    monkeypatch.setattr(zeus_main, "_user_channel_ingestor", None, raising=False)
    yield
    ws_gap_guard.clear_for_test()


class _CountingAdapter:
    """Adapter that records every _sdk_client() call so we can prove the
    memoization is invalidated between _build_ingestor() attempts.

    Behavior:
      - First call: returns SimpleNamespace(creds=None) — boot creds failure.
      - Subsequent calls: returns SimpleNamespace(creds=<valid>) once
        `_client` has been reset to None by the caller — proves that
        _build_ingestor invalidates the cache.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self.factory_calls = 0
        self.access_calls = 0
        self.valid_creds = SimpleNamespace(
            api_key="retry-key",
            api_secret="retry-secret",
            api_passphrase="retry-passphrase",
        )

    def _sdk_client(self) -> Any:
        self.access_calls += 1
        if self._client is None:
            self.factory_calls += 1
            if self.factory_calls == 1:
                self._client = SimpleNamespace(creds=None)  # boot failure
            else:
                self._client = SimpleNamespace(creds=self.valid_creds)
        return self._client


class _FakeIngestor:
    instances: list["_FakeIngestor"] = []

    def __init__(self, adapter: Any, condition_ids: list[str], **kwargs: Any) -> None:
        self.adapter = adapter
        self.condition_ids = list(condition_ids)
        self.auth = kwargs.get("auth")
        type(self).instances.append(self)

    async def start(self) -> None:
        return None


@pytest.fixture
def _stub_environment(monkeypatch):
    _FakeIngestor.instances.clear()
    adapter = _CountingAdapter()

    class _StubClient:
        def _ensure_v2_adapter(self) -> _CountingAdapter:
            return adapter

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _StubClient)
    monkeypatch.setattr(
        "src.ingest.polymarket_user_channel.PolymarketUserChannelIngestor",
        _FakeIngestor,
    )

    started: dict[str, Any] = {"count": 0}

    class _StubThread:
        def __init__(self, *, target: Any, name: str, daemon: bool) -> None:
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            started["count"] += 1

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr("src.main.threading.Thread", _StubThread)
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("POLYMARKET_USER_WS_CONDITION_IDS", "0xaaa,0xbbb")
    return adapter, started


# ---------------------------------------------------------------------------
# Codex P1 — SDK client cache invalidation
# ---------------------------------------------------------------------------


def test_c1_build_ingestor_invalidates_memoized_sdk_client_between_attempts(
    _stub_environment, monkeypatch
):
    """The first _build_ingestor() call (during _start_*) hits a transient
    creds-None failure. A second explicit call must reset adapter._client and
    re-run the factory — otherwise the cached bad client would defeat the
    retry loop forever (Codex P1)."""
    adapter, _ = _stub_environment
    zeus_main._start_user_channel_ingestor_if_enabled()

    # First attempt: factory ran once, creds were None → ingestor NOT
    # constructed (because creds is None).
    assert adapter.factory_calls == 1, (
        f"C1 setup FAIL: expected factory_calls=1 after eager build, got "
        f"{adapter.factory_calls}."
    )
    assert not _FakeIngestor.instances, (
        "C1 setup FAIL: ingestor was unexpectedly constructed on first attempt; "
        "fixture was supposed to return creds=None."
    )

    # Simulate a retry by invoking _build_ingestor again via the module.
    # In production this happens inside _runner; here we expose it via the
    # nested function indirectly by re-calling _start_user_channel_ingestor_if_enabled.
    # The adapter's _client should now be None at the START of the next call
    # because _build_ingestor sets it to None before fetching creds.
    # Trigger a second build by clearing the thread/ingestor and re-calling.
    monkeypatch.setattr(zeus_main, "_user_channel_thread", None, raising=False)
    monkeypatch.setattr(zeus_main, "_user_channel_ingestor", None, raising=False)
    zeus_main._start_user_channel_ingestor_if_enabled()

    # Second attempt: factory must have run AGAIN (cache invalidated) and
    # produced the valid creds → ingestor constructed.
    assert adapter.factory_calls == 2, (
        f"C1 antibody FAIL: expected factory_calls=2 (cache invalidated and "
        f"re-derived), got {adapter.factory_calls}. The adapter._client memoization "
        f"is not being reset between _build_ingestor() attempts; bot's Codex P1 "
        f"concern is still present and the retry loop will never recover from a "
        f"single boot-time creds failure."
    )
    assert _FakeIngestor.instances, (
        "C1 antibody FAIL: second attempt did not produce an ingestor — the "
        "cached bad client was reused instead of re-deriving fresh creds."
    )


# ---------------------------------------------------------------------------
# Copilot — exception classification into AUTH_FAILED vs DISCONNECTED
# ---------------------------------------------------------------------------


def test_t1_creds_failure_classified_as_auth_failed(monkeypatch):
    """A 'creds' shaped failure must map to AUTH_FAILED."""
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("POLYMARKET_USER_WS_CONDITION_IDS", "0xaaa")

    class _CredsFailingAdapter:
        def _sdk_client(self):
            return SimpleNamespace(creds=None)  # build will raise creds-None

    class _StubClient:
        def _ensure_v2_adapter(self):
            return _CredsFailingAdapter()

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _StubClient)
    monkeypatch.setattr(
        "src.main.threading.Thread",
        type("_NoopThread", (), {
            "__init__": lambda self, **kw: None,
            "start": lambda self: None,
            "is_alive": lambda self: False,
        }),
    )

    zeus_main._start_user_channel_ingestor_if_enabled()
    status = ws_gap_guard.status()
    assert status.subscription_state == "AUTH_FAILED", (
        f"T1 antibody FAIL: subscription_state={status.subscription_state!r}; "
        f"creds-None failure must classify as AUTH_FAILED so operator telemetry "
        f"correctly identifies the failure class."
    )


def test_t2_generic_transport_failure_classified_as_disconnected(monkeypatch):
    """A generic non-auth failure (e.g., ConnectionError raised from the
    adapter) must map to DISCONNECTED, not AUTH_FAILED."""
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("POLYMARKET_USER_WS_CONDITION_IDS", "0xaaa")

    class _NetworkFailingAdapter:
        _client = None

        def _sdk_client(self):
            raise ConnectionError("connection reset by peer")

    class _StubClient:
        def _ensure_v2_adapter(self):
            return _NetworkFailingAdapter()

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _StubClient)
    monkeypatch.setattr(
        "src.main.threading.Thread",
        type("_NoopThread", (), {
            "__init__": lambda self, **kw: None,
            "start": lambda self: None,
            "is_alive": lambda self: False,
        }),
    )

    zeus_main._start_user_channel_ingestor_if_enabled()
    status = ws_gap_guard.status()
    assert status.subscription_state == "DISCONNECTED", (
        f"T2 antibody FAIL: subscription_state={status.subscription_state!r}; "
        f"a generic transport failure (ConnectionError) must classify as "
        f"DISCONNECTED, not AUTH_FAILED. AUTH_FAILED implies operator must "
        f"rotate credentials; DISCONNECTED implies wait + retry. Mis-classifying "
        f"poisons the telemetry surface."
    )
